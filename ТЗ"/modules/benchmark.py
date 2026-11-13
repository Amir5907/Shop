import logging
import tempfile
import time

import pandas as pd

from modules.analytics import cpu_stats
from modules.generator import generate, write_files
from modules.importer import read_many

log=logging.getLogger("orderflow.benchmark")

FILES=8


def measure(fn)->tuple:
    start=time.perf_counter()
    rez=fn()
    return time.perf_counter()-start,rez


def run(make_store,sizes=(1000,5000,20000),seed:int=42,threads:int=4,processes:int=4)->pd.DataFrame:
    rows=[]
    params=f"seed={seed}, threads={threads}, processes={processes}"

    def add(n,operation,seconds,items):
        rows.append({"orders":n,"operation":operation,"items":items,"seconds":round(seconds,4),"params":params})
        log.info(f"benchmark {operation}, {n} заказов: {seconds:.4f} c")

    for n in sizes:
        data=generate(customers=max(10,n//10),products=max(50,n//50),orders=n,seed=seed)
        store=make_store(None)
        store.importer.import_records(data["customers"],"customers")
        store.importer.import_records(data["products"],"products")

        t,rez=measure(lambda:store.importer.import_records(data["orders"],"orders"))
        add(n,"импорт заказов",t,rez["ok"])

        queries=[
            {"status":"delivered","min_total":"50"},
            {"date_from":"2026-03-01","date_to":"2026-06-30"},
            {"product_id":1,"sort_by":"total","descending":True},
            {"customer_id":1},
            {"status":"cancelled","date_from":"2026-05-01","sort_by":"date"},
        ]

        def search_all():
            col=0
            for q in queries:
                col+=len(store.orders.search(**q))
            return col

        t,col=measure(search_all)
        add(n,f"поиск/фильтрация ({len(queries)} запросов)",t,col)

        def analytics_all():
            a=store.analytics
            a.summary()
            a.top_products()
            a.top_categories()
            a.top_customers()
            a.dynamics("week")
            a.status_pivot()
            a.daily_stats()
            a.stock_analysis()

        t,_=measure(analytics_all)
        add(n,"аналитика pandas/numpy",t,n)

        orders=list(store.db.state["orders"].values())
        t_seq,seq=measure(lambda:cpu_stats(orders,1))
        add(n,"CPU-статистика: последовательно",t_seq,n)
        t_mp,mp=measure(lambda:cpu_stats(orders,processes))
        add(n,f"CPU-статистика: {processes} процесса(ов)",t_mp,n)
        if seq!=mp:
            raise RuntimeError("результаты последовательного и многопроцессного расчёта различаются")

        with tempfile.TemporaryDirectory() as tmp:
            paths=[]
            for i in range(FILES):
                part=data["orders"][i::FILES]
                paths.append(write_files({f"orders_{i}":part},tmp)[f"orders_{i}"])
            for mode in ("seq","threads","async"):
                t,_=measure(lambda:read_many(paths,"orders",mode,threads))
                add(n,f"чтение {FILES} файлов: {mode}",t,n)
    return pd.DataFrame(rows)
