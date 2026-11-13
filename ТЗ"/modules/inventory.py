import logging

from modules.products import available
from utils.helpers import add_history
from utils.validators import StockError, to_int

log=logging.getLogger("orderflow.inventory")


class Inventory:
    def __init__(self,db,products):
        self.db=db
        self.products=products

    def get_pairs(self,lines:dict)->list:
        pairs=[]
        for pid,qty in lines.items():
            pairs.append((self.products.get(pid),qty))
        return pairs

    def reserve(self,order_id:str,lines:dict)->None:
        with self.db.transaction() as state:
            pairs=self.get_pairs(lines)
            for product,qty in pairs:
                if not product["active"]:
                    raise StockError(f"Товар «{product['name']}» недоступен для заказа")
                if available(product)<qty:
                    est=max(available(product),0)
                    raise StockError(f"Недостаточно «{product['name']}»: доступно {est}, нужно {qty}")
            for product,qty in pairs:
                product["reserved"]+=qty
            add_history(state,"stock_reserved",order_id,items=dict(lines))
        log.info(f"Заказ {order_id}: зарезервировано {lines}")

    def release(self,order_id:str,lines:dict)->None:
        with self.db.transaction() as state:
            for product,qty in self.get_pairs(lines):
                product["reserved"]-=qty
            add_history(state,"stock_released",order_id,items=dict(lines))
        log.info(f"Заказ {order_id}: снят резерв {lines}")

    def ship(self,order_id:str,lines:dict)->None:
        with self.db.transaction() as state:
            pairs=self.get_pairs(lines)
            for product,qty in pairs:
                if product["stock"]<qty:
                    raise StockError(f"На складе только {product['stock']} шт. «{product['name']}», отгрузить {qty} нельзя")
            for product,qty in pairs:
                product["stock"]-=qty
                product["reserved"]-=qty
            add_history(state,"stock_shipped",order_id,items=dict(lines))
        log.info(f"Заказ {order_id}: отгружено со склада {lines}")

    def put_back(self,order_id:str,lines:dict)->None:
        with self.db.transaction() as state:
            for product,qty in self.get_pairs(lines):
                product["stock"]+=qty
            add_history(state,"stock_returned",order_id,items=dict(lines))
        log.info(f"Заказ {order_id}: возвращено на склад {lines}")

    def restock(self,product_id,quantity)->dict:
        qty=to_int(quantity,"количество")
        with self.db.transaction():
            product=self.products.get(product_id)
            product["stock"]+=qty
        log.info(f"Приход товара {product_id}: +{qty} шт.")
        return product
