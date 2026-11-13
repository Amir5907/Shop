import asyncio
import csv
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from utils.helpers import timed
from utils.validators import OrderFlowError, ValidationError, require_fields

log=logging.getLogger("orderflow.importer")

REQUIRED={
    "products":["name","category","price","stock"],
    "customers":["name","contact"],
    "orders":["customer_id","items"],
}


def parse_items(text:str)->list:
    items=[]
    for part in str(text).split(";"):
        part=part.strip()
        if part=="":
            continue
        if ":" not in part:
            raise ValidationError(f"позиция {part!r}: ожидается формат id:количество")
        pid,qty=part.split(":",1)
        items.append({"product_id":pid,"quantity":qty})
    return items


def read_records(path,kind:str)->list:
    path=Path(path)
    if path.suffix.lower()==".json":
        with open(path,encoding="utf-8") as f:
            data=json.load(f)
        if isinstance(data,dict):
            data=data.get(kind)
        if not isinstance(data,list):
            raise ValidationError(f"{path.name}: ожидается список записей или объект с ключом «{kind}»")
        return data
    if path.suffix.lower()==".csv":
        with open(path,encoding="utf-8",newline="") as f:
            return list(csv.DictReader(f))
    raise ValidationError(f"{path.name}: поддерживаются только .json и .csv")


def safe_read(path,kind:str):
    try:
        return read_records(path,kind)
    except (OSError,ValueError,OrderFlowError) as e:
        log.error(f"Не удалось прочитать {path}: {e}")
        return e


def read_many(paths:list,kind:str,mode:str="threads",workers:int=4)->list:
    if mode=="seq":
        rez=[]
        for p in paths:
            rez.append(safe_read(p,kind))
        return rez
    if mode=="threads":
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures=[]
            for p in paths:
                futures.append(pool.submit(safe_read,p,kind))
            rez=[]
            for f in futures:
                rez.append(f.result())
            return rez
    return asyncio.run(read_many_async(paths,kind))


async def read_many_async(paths:list,kind:str)->list:
    tasks=[]
    for p in paths:
        tasks.append(asyncio.create_task(asyncio.to_thread(safe_read,p,kind)))
    return await asyncio.gather(*tasks)


class Importer:
    def __init__(self,db,products,customers,orders):
        self.db=db
        self.products=products
        self.customers=customers
        self.orders=orders

    def add_one(self,r:dict,kind:str)->None:
        if kind=="products":
            self.products.add(r["name"],r["category"],r["price"],r["stock"],r.get("min_stock",0),product_id=r.get("id"))
        elif kind=="customers":
            self.customers.add(r["name"],r["contact"],r.get("personal_discount") or 0,
                               customer_id=r.get("id"),registered_at=r.get("registered_at"))
        else:
            items=r["items"]
            if isinstance(items,str):
                items=parse_items(items)
            self.orders.create(r["customer_id"],items,promo_code=r.get("promo_code") or None,
                               order_id=r.get("order_id"),created_at=r.get("created_at") or None,
                               status=r.get("status") or "new")

    def import_records(self,records:list,kind:str)->dict:
        ok=0
        rejected=[]
        with timed(log,f"Импорт {kind} ({len(records)} записей)"),self.db.transaction():
            for nomer,record in enumerate(records,1):
                try:
                    if not isinstance(record,dict):
                        raise ValidationError("запись не является объектом")
                    require_fields(record,REQUIRED[kind])
                    self.add_one(record,kind)
                    ok+=1
                except (OrderFlowError,TypeError,ValueError,AttributeError) as e:
                    rid=None
                    if isinstance(record,dict):
                        rid=record.get("order_id") or record.get("id")
                    rejected.append({"record":nomer,"id":rid,"reason":str(e)})
        log.info(f"Импорт {kind}: принято {ok}, отклонено {len(rejected)}")
        for r in rejected:
            log.warning(f"Импорт {kind}, запись {r['record']} отклонена: {r['reason']}")
        return {"kind":kind,"ok":ok,"rejected":rejected}

    def import_file(self,path,kind:str)->dict:
        if kind not in REQUIRED:
            raise ValidationError(f"Можно импортировать: {', '.join(REQUIRED)}")
        try:
            records=read_records(path,kind)
        except (OSError,ValueError) as e:
            log.exception(f"Ошибка чтения {path}")
            raise ValidationError(f"Не удалось прочитать файл {path}: {e}") from e
        rez=self.import_records(records,kind)
        rez["file"]=str(path)
        return rez

    def import_files(self,paths:list,kind:str,mode:str="async",workers:int=4)->list:
        spisok=[]
        for p in paths:
            spisok.append(Path(p))
        data=read_many(spisok,kind,mode,workers)
        results=[]
        for path,records in zip(paths,data):
            if isinstance(records,Exception):
                results.append({"file":str(path),"kind":kind,"ok":0,"error":str(records),"rejected":[]})
            else:
                rez=self.import_records(records,kind)
                rez["file"]=str(path)
                results.append(rez)
        return results
