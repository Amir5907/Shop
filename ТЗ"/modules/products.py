import logging

from utils.validators import DuplicateError, NotFoundError, ValidationError, required_str, to_int, to_money

log=logging.getLogger("orderflow.products")

EDITABLE=["name","category","price","stock","min_stock","active"]


def available(product:dict)->int:
    return product["stock"]-product["reserved"]


class Products:
    def __init__(self,db):
        self.db=db

    def get(self,product_id)->dict:
        product=self.db.state["products"].get(str(product_id).strip())
        if product is None:
            raise NotFoundError(f"Товар с id {product_id} не найден")
        return product

    def add(self,name:str,category:str,price,stock,min_stock=0,product_id=None)->dict:
        name=required_str(name,"название")
        category=required_str(category,"категория")
        price=str(to_money(price,"цена",allow_zero=False))
        stock=to_int(stock,"количество на складе",0)
        min_stock=to_int(min_stock,"минимальный остаток",0)
        with self.db.transaction() as state:
            if product_id in (None,""):
                pid=state["counters"]["product"]+1
            else:
                pid=to_int(product_id,"id товара")
            if str(pid) in state["products"]:
                raise DuplicateError(f"Товар с id {pid} уже существует")
            product={
                "id":pid,
                "name":name,
                "category":category,
                "price":price,
                "stock":stock,
                "reserved":0,
                "min_stock":min_stock,
                "active":True,
                "deleted":False,
            }
            state["products"][str(pid)]=product
            state["counters"]["product"]=max(state["counters"]["product"],pid)
        log.info(f"Добавлен товар {pid} «{name}»")
        return product

    def update(self,product_id,**changes)->dict:
        novoe={}
        for field,value in changes.items():
            if field not in EDITABLE:
                raise ValidationError(f"Нельзя изменить поле {field}")
            if field=="name" or field=="category":
                novoe[field]=required_str(value,field)
            elif field=="price":
                novoe[field]=str(to_money(value,"цена",allow_zero=False))
            elif field=="stock" or field=="min_stock":
                novoe[field]=to_int(value,field,0)
            else:
                novoe[field]=str(value).strip().lower() in ("y","yes","true","1","да")
        with self.db.transaction():
            product=self.get(product_id)
            if product["deleted"]:
                raise ValidationError(f"Товар {product_id} удалён")
            product.update(novoe)
        if available(product)<0:
            log.warning(f"Товар {product_id}: остаток {product['stock']} меньше резерва {product['reserved']}")
        log.info(f"Изменён товар {product_id}: {novoe}")
        return product

    def delete(self,product_id)->None:
        with self.db.transaction():
            product=self.get(product_id)
            product["deleted"]=True
            product["active"]=False
        log.info(f"Товар {product_id} помечен как удалённый")

    def search(self,text:str="",category=None,include_deleted:bool=False)->list:
        text=text.strip().lower()
        rez=[]
        for p in self.db.state["products"].values():
            if p["deleted"] and not include_deleted:
                continue
            if text not in p["name"].lower():
                continue
            if category and p["category"].lower()!=category.strip().lower():
                continue
            rez.append(p)
        return rez
