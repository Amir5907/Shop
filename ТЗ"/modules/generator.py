import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from modules.orders import RESERVED

CATEGORIES={
    "Электроника":["Наушники","Смартфон","Планшет","Колонка","Зарядка","Мышь"],
    "Одежда":["Футболка","Куртка","Кроссовки","Шапка","Джинсы"],
    "Книги":["Роман","Учебник Python","Комикс","Справочник"],
    "Дом":["Лампа","Чайник","Плед","Кружка","Сковорода"],
    "Спорт":["Мяч","Гантели","Коврик","Велошлем"],
}
NAMES=["Анна","Борис","Виктор","Глеб","Дарья","Егор","Ирина","Марат","Ольга","Игорь"]
STATUS_WEIGHTS={"delivered":50,"new":8,"confirmed":6,"assembling":5,"ready":4,"shipped":7,"cancelled":12,"returned":8}


def generate(customers:int,products:int,orders:int,avg_items:float=3,date_from="2026-01-01",date_to="2026-10-31",seed:int=42,
             first_customer:int=1,first_product:int=1,first_order:int=10001)->dict:
    rnd=random.Random(seed)
    start=datetime.fromisoformat(date_from)
    end=datetime.fromisoformat(date_to)+timedelta(days=1)
    seconds=int((end-start).total_seconds())-1

    product_list=[]
    for pid in range(first_product,first_product+products):
        category=rnd.choice(list(CATEGORIES))
        product_list.append({
            "id":pid,
            "name":f"{rnd.choice(CATEGORIES[category])} #{pid}",
            "category":category,
            "price":f"{rnd.uniform(2,300):.2f}",
            "stock":0,
            "min_stock":rnd.randint(2,15),
        })

    customer_list=[]
    for cid in range(first_customer,first_customer+customers):
        customer_list.append({
            "id":cid,
            "name":f"{rnd.choice(NAMES)} {cid}",
            "contact":f"user{cid}@example.com",
            "personal_discount":rnd.choice([0,0,0,3,5]),
        })

    reserved={}
    for p in product_list:
        reserved[p["id"]]=0
    order_list=[]
    for nomer in range(first_order,first_order+orders):
        col=min(products,max(1,round(rnd.gauss(avg_items,1))))
        status=rnd.choices(list(STATUS_WEIGHTS),weights=list(STATUS_WEIGHTS.values()))[0]
        items=[]
        for p in rnd.sample(product_list,col):
            items.append({"product_id":p["id"],"quantity":rnd.randint(1,4)})
        if status in RESERVED:
            for item in items:
                reserved[item["product_id"]]+=item["quantity"]
        created=start+timedelta(seconds=rnd.randint(0,seconds))
        order_list.append({
            "order_id":f"ORD-{nomer}",
            "customer_id":rnd.randint(first_customer,first_customer+customers-1),
            "created_at":created.strftime("%Y-%m-%d %H:%M:%S"),
            "status":status,
            "items":items,
        })
    for p in product_list:
        p["stock"]=reserved[p["id"]]+rnd.randint(0,40)
    order_list.sort(key=lambda o:o["created_at"])
    return {"customers":customer_list,"products":product_list,"orders":order_list}


def write_files(data:dict,folder)->dict:
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    paths={}
    for kind,records in data.items():
        paths[kind]=folder/f"{kind}.json"
        paths[kind].write_text(json.dumps(records,ensure_ascii=False,indent=1),encoding="utf-8")
    return paths
