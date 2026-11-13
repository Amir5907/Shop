import logging
import re
from decimal import Decimal

from utils.helpers import DATE_FMT, add_history, now, round_money
from utils.validators import (DuplicateError, NotFoundError, StatusError, ValidationError,
                              required_str, to_datetime, to_int, to_money)

log=logging.getLogger("orderflow.orders")

STATUSES=["new","confirmed","assembling","ready","shipped","delivered","cancelled","returned"]

PEREHODY={
    "new":["confirmed","cancelled"],
    "confirmed":["assembling","cancelled"],
    "assembling":["ready","cancelled"],
    "ready":["shipped","cancelled"],
    "shipped":["delivered"],
    "delivered":["returned"],
    "cancelled":[],
    "returned":[],
}

RESERVED=["new","confirmed","assembling","ready"]
COMPLETED=["delivered","returned"]


def check_status(value)->str:
    status=str(value).strip().lower()
    if status not in STATUSES:
        raise ValidationError(f"Неизвестный статус {value!r}. Доступны: {', '.join(STATUSES)}")
    return status


def order_net(order:dict)->Decimal:
    return Decimal(order["total"])-Decimal(order["refund"])


class Orders:
    def __init__(self,db,products,customers,inventory,discounts,delivery_cost,free_delivery_from):
        self.db=db
        self.products=products
        self.customers=customers
        self.inventory=inventory
        self.discounts=discounts
        self.delivery_cost=delivery_cost
        self.free_delivery_from=free_delivery_from

    def get(self,order_id:str)->dict:
        order=self.db.state["orders"].get(str(order_id).strip().upper())
        if order is None:
            raise NotFoundError(f"Заказ {order_id} не найден")
        return order

    def make_line(self,product:dict,quantity:int)->dict:
        return {
            "product_id":product["id"],
            "name":product["name"],
            "category":product["category"],
            "price":product["price"],
            "quantity":quantity,
            "returned":0,
        }

    def calculate(self,lines:list,customer:dict,promo_code,created_at:str)->dict:
        return self.discounts.calculate(lines,customer,promo_code,created_at,self.delivery_cost,self.free_delivery_from)

    def set_totals(self,order:dict,calc:dict)->None:
        order["subtotal"]=calc["subtotal"]
        order["discount"]=calc["discount"]
        order["delivery"]=calc["delivery"]
        order["total"]=calc["total"]
        order["applied_discounts"]=calc["applied"]

    def create(self,customer_id,items:list,promo_code=None,order_id=None,created_at=None,status:str="new")->dict:
        customer=self.customers.get(to_int(customer_id,"id покупателя"))
        if not isinstance(items,(list,tuple)) or len(items)==0:
            raise ValidationError("Заказ должен содержать хотя бы один товар")
        tovary={}
        for item in items:
            if isinstance(item,dict):
                pid=item.get("product_id")
                qty=item.get("quantity")
            else:
                pid,qty=item
            pid=str(to_int(pid,"id товара"))
            qty=to_int(qty,"количество")
            tovary[pid]=tovary.get(pid,0)+qty
        status=check_status(status)
        if created_at:
            created_at=to_datetime(created_at,"дата заказа").strftime(DATE_FMT)
        else:
            created_at=now()
        if promo_code:
            promo_code=promo_code.strip().upper()
        else:
            promo_code=None

        with self.db.transaction() as state:
            if order_id:
                oid=required_str(order_id,"номер заказа").upper()
            else:
                oid=f"ORD-{state['counters']['order']+1}"
            if oid in state["orders"]:
                raise DuplicateError(f"Заказ {oid} уже существует")
            lines=[]
            for pid,qty in tovary.items():
                lines.append(self.make_line(self.products.get(pid),qty))
            calc=self.calculate(lines,customer,promo_code,created_at)
            if status in RESERVED:
                self.inventory.reserve(oid,tovary)

            order={
                "order_id":oid,
                "customer_id":customer["id"],
                "created_at":created_at,
                "status":status,
                "items":lines,
                "promo_code":promo_code,
                "refund":"0.00",
                "status_history":[{"at":created_at,"from":None,"to":status}],
            }
            self.set_totals(order,calc)
            if status=="returned":
                vse={}
                for line in lines:
                    vse[line["product_id"]]=line["quantity"]
                self.apply_refund(order,vse)
            state["orders"][oid]=order
            nomer=re.fullmatch(r"ORD-(\d+)",oid)
            if nomer:
                state["counters"]["order"]=max(state["counters"]["order"],int(nomer.group(1)))
            add_history(state,"order_created",oid,customer_id=customer["id"],status=status,items=tovary,total=order["total"])
            if calc["applied"]:
                add_history(state,"discount_applied",oid,rules=calc["applied"],discount=order["discount"],promo_code=promo_code)
        log.info(f"Создан заказ {oid} на сумму {order['total']}")
        return order

    def change_item(self,order_id:str,product_id,quantity)->dict:
        pid=to_int(product_id,"id товара")
        qty=to_int(quantity,"количество",0)
        with self.db.transaction() as state:
            order=self.get(order_id)
            if order["status"]!="new":
                raise StatusError("Состав можно менять только у заказа в статусе new")
            old_line=None
            new_items=[]
            for line in order["items"]:
                if line["product_id"]==pid:
                    old_line=line
                else:
                    new_items.append(dict(line))
            if old_line:
                old=old_line["quantity"]
            else:
                old=0
            if qty==old:
                return order
            if qty>0:
                if old_line:
                    new_line=dict(old_line)
                    new_line["quantity"]=qty
                else:
                    new_line=self.make_line(self.products.get(pid),qty)
                new_items.append(new_line)
            if len(new_items)==0:
                raise ValidationError("В заказе должен остаться хотя бы один товар — для этого есть отмена")
            customer=self.customers.get(order["customer_id"])
            calc=self.calculate(new_items,customer,order["promo_code"],order["created_at"])
            if qty>old:
                self.inventory.reserve(order["order_id"],{str(pid):qty-old})
            else:
                self.inventory.release(order["order_id"],{str(pid):old-qty})
            order["items"]=new_items
            self.set_totals(order,calc)
            add_history(state,"items_changed",order["order_id"],product_id=pid,**{"from":old,"to":qty},total=order["total"])
        log.info(f"Заказ {order['order_id']}: товар {pid} {old} -> {qty} шт.")
        return order

    def set_status(self,order_id:str,new_status:str,returns=None)->dict:
        new=check_status(new_status)
        with self.db.transaction() as state:
            order=self.get(order_id)
            old=order["status"]
            if new not in PEREHODY[old]:
                raise StatusError(f"Нельзя перевести заказ {order['order_id']} из «{old}» в «{new}»")
            lines={}
            for line in order["items"]:
                lines[str(line["product_id"])]=line["quantity"]
            if new=="cancelled":
                self.inventory.release(order["order_id"],lines)
            elif new=="shipped":
                self.inventory.ship(order["order_id"],lines)
            elif new=="returned":
                self.register_return(state,order,returns)
            order["status"]=new
            order["status_history"].append({"at":now(),"from":old,"to":new})
            add_history(state,"status_changed",order["order_id"],**{"from":old,"to":new})
        log.info(f"Заказ {order['order_id']}: статус {old} -> {new}")
        return order

    def register_return(self,state:dict,order:dict,returns)->None:
        by_id={}
        for line in order["items"]:
            by_id[line["product_id"]]=line
        if returns is None:
            returns={}
            for pid,line in by_id.items():
                returns[pid]=line["quantity"]-line["returned"]
        vozvrat={}
        for pid,qty in returns.items():
            pid=to_int(pid,"id товара")
            qty=to_int(qty,"количество")
            line=by_id.get(pid)
            if line is None:
                raise ValidationError(f"Товара {pid} нет в заказе")
            mozhno=line["quantity"]-line["returned"]
            if qty>mozhno:
                raise ValidationError(f"Вернуть можно не больше {mozhno} шт. товара {pid}")
            vozvrat[pid]=qty
        if len(vozvrat)==0:
            raise ValidationError("Нечего возвращать")
        na_sklad={}
        for pid,qty in vozvrat.items():
            na_sklad[str(pid)]=qty
        self.inventory.put_back(order["order_id"],na_sklad)
        refund=self.apply_refund(order,vozvrat)
        add_history(state,"return_registered",order["order_id"],items=vozvrat,refund=str(refund))

    def apply_refund(self,order:dict,returns:dict)->Decimal:
        subtotal=Decimal(order["subtotal"])
        if subtotal>0:
            dolya=(subtotal-Decimal(order["discount"]))/subtotal
        else:
            dolya=Decimal("0")
        refund=Decimal("0")
        for line in order["items"]:
            qty=returns.get(line["product_id"],0)
            if qty:
                line["returned"]+=qty
                refund+=Decimal(line["total"])*qty/line["quantity"]*dolya
        refund=round_money(refund)
        order["refund"]=str(Decimal(order["refund"])+refund)
        return refund

    def search(self,order_id=None,customer_id=None,status=None,date_from=None,date_to=None,
               min_total=None,max_total=None,product_id=None,sort_by:str="date",descending:bool=False)->list:
        if order_id:
            order_id=str(order_id).strip().upper()
        if customer_id:
            customer_id=to_int(customer_id,"id покупателя")
        if status:
            status=check_status(status)
        if date_from:
            date_from=to_datetime(date_from).strftime(DATE_FMT)
        if date_to:
            date_to=to_datetime(date_to).strftime("%Y-%m-%d")
        if min_total:
            min_total=to_money(min_total,"мин. сумма")
        if max_total:
            max_total=to_money(max_total,"макс. сумма")
        if product_id:
            product_id=to_int(product_id,"id товара")
        if sort_by not in ("date","total","status"):
            raise ValidationError("Сортировка возможна по date, total или status")

        rez=[]
        for o in self.db.state["orders"].values():
            if order_id and order_id not in o["order_id"]:
                continue
            if customer_id and o["customer_id"]!=customer_id:
                continue
            if status and o["status"]!=status:
                continue
            if date_from and o["created_at"]<date_from:
                continue
            if date_to and o["created_at"][:10]>date_to:
                continue
            if min_total and Decimal(o["total"])<min_total:
                continue
            if max_total and Decimal(o["total"])>max_total:
                continue
            if product_id:
                est=False
                for line in o["items"]:
                    if line["product_id"]==product_id:
                        est=True
                if not est:
                    continue
            rez.append(o)

        if sort_by=="date":
            rez.sort(key=lambda o:o["created_at"],reverse=descending)
        elif sort_by=="total":
            rez.sort(key=lambda o:Decimal(o["total"]),reverse=descending)
        else:
            rez.sort(key=lambda o:STATUSES.index(o["status"]),reverse=descending)
        return rez

    def history(self,order_id:str)->list:
        oid=self.get(order_id)["order_id"]
        rez=[]
        for h in self.db.state["history"]:
            if h["order_id"]==oid:
                rez.append(h)
        return rez
