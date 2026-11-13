import csv
import json
import logging
from pathlib import Path

import pandas as pd

from utils.validators import StorageError, ValidationError

log=logging.getLogger("orderflow.exporter")


def items_to_text(items:list)->str:
    parts=[]
    for line in items:
        parts.append(f"{line['product_id']}:{line['quantity']}")
    return ";".join(parts)


def export_records(records:list,path)->Path:
    path=Path(path)
    if path.suffix.lower() not in (".json",".csv"):
        raise ValidationError("Экспорт возможен только в .json или .csv")
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        with open(path,"w",encoding="utf-8",newline="") as f:
            if path.suffix.lower()==".json":
                json.dump(records,f,ensure_ascii=False,indent=1,default=str)
            else:
                fields=[]
                for r in records:
                    for key in r:
                        if key not in fields:
                            fields.append(key)
                writer=csv.DictWriter(f,fieldnames=fields)
                writer.writeheader()
                for r in records:
                    row={}
                    for k,v in r.items():
                        if isinstance(v,(list,dict)):
                            row[k]=json.dumps(v,ensure_ascii=False)
                        else:
                            row[k]=v
                    writer.writerow(row)
    except OSError as e:
        log.exception(f"Ошибка экспорта в {path}")
        raise StorageError(f"Не удалось записать {path}: {e.strerror}") from e
    log.info(f"Экспортировано {len(records)} записей в {path}")
    return path


def export_df(df:pd.DataFrame,path)->Path:
    path=Path(path)
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.suffix.lower()==".json":
            df.reset_index().astype(str).to_json(path,orient="records",force_ascii=False,indent=1)
        else:
            df.to_csv(path,encoding="utf-8")
    except OSError as e:
        log.exception(f"Ошибка экспорта в {path}")
        raise StorageError(f"Не удалось записать {path}: {e.strerror}") from e
    return path


class Exporter:
    def __init__(self,db,analytics):
        self.db=db
        self.analytics=analytics

    def products(self,path)->Path:
        return export_records(list(self.db.state["products"].values()),path)

    def orders(self,path)->Path:
        orders=list(self.db.state["orders"].values())
        if Path(path).suffix.lower()==".csv":
            flat=[]
            for o in orders:
                row=dict(o)
                row.pop("status_history")
                row["items"]=items_to_text(o["items"])
                flat.append(row)
            orders=flat
        return export_records(orders,path)

    def report(self,folder,date_from=None,date_to=None)->list:
        folder=Path(folder)
        a=self.analytics
        summary={}
        for k,v in a.summary(date_from,date_to).items():
            summary[k]=str(v)
        paths=[export_records([summary],folder/"summary.json")]
        tables={
            "top_products":a.top_products(20,date_from,date_to),
            "top_categories":a.top_categories(20,date_from,date_to),
            "top_customers":a.top_customers(20,date_from,date_to),
            "sales_by_day":a.dynamics("day",date_from,date_to),
            "sales_by_month":a.dynamics("month",date_from,date_to),
            "status_pivot":a.status_pivot(),
            "category_pivot":a.category_pivot(),
            "restock":a.stock_analysis(date_from,date_to).get("Нужно пополнить",pd.DataFrame()),
        }
        for name,df in tables.items():
            paths.append(export_df(df,folder/f"{name}.csv"))
        log.info(f"Отчёт сохранён в {folder}")
        return paths
