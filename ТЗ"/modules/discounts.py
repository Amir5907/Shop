import logging
from decimal import Decimal

from utils.helpers import DATE_FMT, round_money
from utils.validators import DuplicateError, ValidationError, required_str, to_datetime, to_money, to_percent

log=logging.getLogger("orderflow.discounts")

RULE_TYPES=["product","category","threshold","promo"]


def is_active(rule:dict,at:str)->bool:
    if rule.get("valid_from","")>at:
        return False
    if rule.get("valid_to") and at>rule["valid_to"]:
        return False
    return True


class Discounts:
    def __init__(self,db):
        self.db=db

    @property
    def rules(self)->list:
        return self.db.state["discounts"]

    def add(self,name:str,type:str,percent,target=None,min_total=None,code=None,valid_from=None,valid_to=None,stackable:bool=True)->dict:
        if type not in RULE_TYPES:
            raise ValidationError(f"Тип скидки должен быть одним из: {', '.join(RULE_TYPES)}")
        rule={
            "name":required_str(name,"название скидки"),
            "type":type,
            "percent":str(to_percent(percent)),
            "stackable":bool(stackable),
        }
        if type=="product" or type=="category":
            rule["target"]=required_str(target,"товар или категория скидки")
        if type=="threshold":
            rule["min_total"]=str(to_money(min_total,"порог суммы"))
        if type=="promo":
            rule["code"]=required_str(code,"промокод").upper()
        if valid_from:
            rule["valid_from"]=to_datetime(valid_from,"начало действия").strftime(DATE_FMT)
        if valid_to:
            end=to_datetime(valid_to,"конец действия")
            if len(str(valid_to).strip())<=10:
                end=end.replace(hour=23,minute=59,second=59)
            rule["valid_to"]=end.strftime(DATE_FMT)
        with self.db.transaction():
            if type=="promo":
                for r in self.rules:
                    if r.get("code")==rule["code"]:
                        raise DuplicateError(f"Промокод {rule['code']} уже существует")
            self.rules.append(rule)
        log.info(f"Добавлена скидка {rule}")
        return rule

    def find_promo(self,code:str,at:str)->dict:
        code=code.strip().upper()
        for r in self.rules:
            if r["type"]=="promo" and r["code"]==code:
                if not is_active(r,at):
                    raise ValidationError(f"Промокод {code} не действует на дату {at[:10]}")
                return r
        raise ValidationError(f"Промокод {code} не найден")

    def item_rule(self,line:dict,active:list):
        best=None
        for r in active:
            podhodit=False
            if r["type"]=="product" and r["target"]==str(line["product_id"]):
                podhodit=True
            if r["type"]=="category" and r["target"].lower()==line["category"].lower():
                podhodit=True
            if podhodit and (best is None or Decimal(r["percent"])>Decimal(best["percent"])):
                best=r
        return best

    def calculate(self,lines:list,customer:dict,promo_code,at:str,delivery_cost:Decimal,free_delivery_from:Decimal)->dict:
        active=[]
        for r in self.rules:
            if is_active(r,at):
                active.append(r)

        applied=[]
        subtotal=Decimal("0")
        for line in lines:
            gross=round_money(Decimal(line["price"])*line["quantity"])
            best=self.item_rule(line,active)
            if best:
                discount=round_money(gross*Decimal(best["percent"])/100)
                if best["name"] not in applied:
                    applied.append(best["name"])
            else:
                discount=Decimal("0.00")
            line["gross"]=str(gross)
            line["discount"]=str(discount)
            line["total"]=str(gross-discount)
            subtotal+=gross-discount

        candidates=[]
        for r in active:
            if r["type"]=="threshold" and subtotal>=Decimal(r["min_total"]):
                candidates.append(r)
        if promo_code:
            candidates.append(self.find_promo(promo_code,at))
        personal=Decimal(customer.get("personal_discount","0"))
        if personal>0:
            candidates.append({"name":"Персональная скидка","percent":str(personal),"stackable":True})

        stack_sum=Decimal("0")
        stackable=[]
        best_single=None
        for r in candidates:
            if r["stackable"]:
                stack_sum+=Decimal(r["percent"])
                stackable.append(r)
            if best_single is None or Decimal(r["percent"])>Decimal(best_single["percent"]):
                best_single=r
        stack_sum=min(stack_sum,Decimal("100"))

        if best_single and Decimal(best_single["percent"])>stack_sum:
            percent=Decimal(best_single["percent"])
            applied.append(best_single["name"])
        else:
            percent=stack_sum
            for r in stackable:
                applied.append(r["name"])

        order_discount=round_money(subtotal*percent/100)
        posle=subtotal-order_discount
        if posle>=free_delivery_from:
            delivery=Decimal("0.00")
        else:
            delivery=delivery_cost
        return {
            "subtotal":str(round_money(subtotal)),
            "discount":str(order_discount),
            "delivery":str(delivery),
            "total":str(round_money(posle+delivery)),
            "applied":applied,
        }
