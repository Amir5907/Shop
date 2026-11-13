import logging
from decimal import Decimal

from modules.orders import COMPLETED, order_net
from utils.helpers import DATE_FMT, now, round_money
from utils.validators import DuplicateError, NotFoundError, required_str, to_datetime, to_int, to_percent

log=logging.getLogger("orderflow.customers")


class Customers:
    def __init__(self,db):
        self.db=db

    def get(self,customer_id)->dict:
        customer=self.db.state["customers"].get(str(customer_id).strip())
        if customer is None:
            raise NotFoundError(f"Покупатель с id {customer_id} не найден")
        return customer

    def add(self,name:str,contact:str,personal_discount=0,customer_id=None,registered_at=None)->dict:
        name=required_str(name,"имя")
        contact=required_str(contact,"контакты")
        skidka=str(to_percent(personal_discount,"персональная скидка"))
        if registered_at:
            registered_at=to_datetime(registered_at).strftime(DATE_FMT)
        else:
            registered_at=now()
        with self.db.transaction() as state:
            if customer_id in (None,""):
                cid=state["counters"]["customer"]+1
            else:
                cid=to_int(customer_id,"id покупателя")
            if str(cid) in state["customers"]:
                raise DuplicateError(f"Покупатель с id {cid} уже существует")
            customer={
                "id":cid,
                "name":name,
                "contact":contact,
                "registered_at":registered_at,
                "personal_discount":skidka,
            }
            state["customers"][str(cid)]=customer
            state["counters"]["customer"]=max(state["counters"]["customer"],cid)
        log.info(f"Добавлен покупатель {cid} «{name}»")
        return customer

    def search(self,text:str="")->list:
        text=text.strip().lower()
        rez=[]
        for c in self.db.state["customers"].values():
            if text in c["name"].lower() or text in c["contact"].lower():
                rez.append(c)
        return rez

    def orders_of(self,customer_id)->list:
        cid=self.get(customer_id)["id"]
        rez=[]
        for o in self.db.state["orders"].values():
            if o["customer_id"]==cid:
                rez.append(o)
        rez.sort(key=lambda o:o["created_at"])
        return rez

    def stats(self,customer_id)->dict:
        orders=self.orders_of(customer_id)
        col_completed=0
        col_cancelled=0
        spent=Decimal("0")
        for o in orders:
            if o["status"] in COMPLETED:
                col_completed+=1
                spent+=order_net(o)
            if o["status"]=="cancelled":
                col_cancelled+=1
        if col_completed>0:
            avg=round_money(spent/col_completed)
        else:
            avg=Decimal("0.00")
        return {
            "Всего заказов":len(orders),
            "Завершённых":col_completed,
            "Отменённых":col_cancelled,
            "Сумма покупок":round_money(spent),
            "Средний чек":avg,
        }
