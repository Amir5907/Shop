from decimal import Decimal
from multiprocessing import Pool

import numpy as np
import pandas as pd

from modules.orders import COMPLETED
from utils.helpers import round_money

FREQ={"day":"D","week":"W","month":"M"}


def cents(value:str)->int:
    return int(Decimal(value)*100)


def money(value_cents)->Decimal:
    return round_money(Decimal(str(value_cents))/100)


def to_euro(df:pd.DataFrame)->pd.DataFrame:
    for col in list(df.columns):
        if str(col).endswith("_cents"):
            df[col.replace("_cents","")]=df.pop(col)/100
    return df


def period(df:pd.DataFrame,date_from=None,date_to=None)->pd.DataFrame:
    if date_from:
        df=df[df["created_at"]>=pd.Timestamp(date_from)]
    if date_to:
        df=df[df["created_at"]<pd.Timestamp(date_to)+pd.Timedelta(days=1)]
    return df


class Analytics:
    def __init__(self,db):
        self.db=db

    def orders_df(self)->pd.DataFrame:
        rows=[]
        for o in self.db.state["orders"].values():
            rows.append({
                "order_id":o["order_id"],
                "customer_id":o["customer_id"],
                "created_at":o["created_at"],
                "status":o["status"],
                "net_cents":cents(o["total"])-cents(o["refund"]),
                "positions":len(o["items"]),
            })
        df=pd.DataFrame(rows,columns=["order_id","customer_id","created_at","status","net_cents","positions"])
        df["created_at"]=pd.to_datetime(df["created_at"])
        return df

    def items_df(self)->pd.DataFrame:
        rows=[]
        for o in self.db.state["orders"].values():
            for line in o["items"]:
                sold=line["quantity"]-line["returned"]
                rows.append({
                    "order_id":o["order_id"],
                    "created_at":o["created_at"],
                    "status":o["status"],
                    "product_id":line["product_id"],
                    "name":line["name"],
                    "category":line["category"],
                    "sold":sold,
                    "revenue_cents":cents(line["total"])*sold//line["quantity"],
                })
        df=pd.DataFrame(rows,columns=["order_id","created_at","status","product_id","name","category","sold","revenue_cents"])
        df["created_at"]=pd.to_datetime(df["created_at"])
        return df

    def sales(self,date_from=None,date_to=None)->pd.DataFrame:
        df=period(self.orders_df(),date_from,date_to)
        return df[df["status"]!="cancelled"]

    def sales_items(self,date_from=None,date_to=None)->pd.DataFrame:
        df=period(self.items_df(),date_from,date_to)
        return df[df["status"]!="cancelled"]

    def summary(self,date_from=None,date_to=None)->dict:
        df=period(self.orders_df(),date_from,date_to)
        sales=df[df["status"]!="cancelled"]
        completed=df[df["status"].isin(COMPLETED)]
        if len(sales)>0:
            avg=money(sales["net_cents"].mean())
        else:
            avg=Decimal("0.00")
        return {
            "Заказов за период":len(df),
            "Оформлено (без отменённых)":len(sales),
            "Отменено":int((df["status"]=="cancelled").sum()),
            "Возвращено":int((df["status"]=="returned").sum()),
            "Сумма оформленных заказов":money(sales["net_cents"].sum()),
            "Выручка завершённых (доставлено/возврат)":money(completed["net_cents"].sum()),
            "Средний чек":avg,
        }

    def top_products(self,n:int=5,date_from=None,date_to=None)->pd.DataFrame:
        items=self.sales_items(date_from,date_to)
        top=items.groupby("product_id").agg(name=("name","last"),sold=("sold","sum"),revenue_cents=("revenue_cents","sum"))
        top=top.sort_values("sold",ascending=False).head(n)
        return to_euro(top)

    def top_categories(self,n:int=5,date_from=None,date_to=None)->pd.DataFrame:
        items=self.sales_items(date_from,date_to)
        top=items.groupby("category").agg(sold=("sold","sum"),orders=("order_id","nunique"),revenue_cents=("revenue_cents","sum"))
        top=top.sort_values("sold",ascending=False).head(n)
        return to_euro(top)

    def top_customers(self,n:int=5,date_from=None,date_to=None)->pd.DataFrame:
        sales=self.sales(date_from,date_to)
        top=sales.groupby("customer_id").agg(orders=("order_id","count"),spent_cents=("net_cents","sum"))
        top=top.sort_values("spent_cents",ascending=False).head(n)
        names=[]
        for cid in top.index:
            names.append(self.db.state["customers"].get(str(cid),{}).get("name","?"))
        top.insert(0,"name",names)
        return to_euro(top)

    def dynamics(self,freq:str="day",date_from=None,date_to=None)->pd.DataFrame:
        sales=self.sales(date_from,date_to).copy()
        sales["period"]=sales["created_at"].dt.to_period(FREQ[freq])
        rez=sales.groupby("period").agg(orders=("order_id","count"),revenue_cents=("net_cents","sum"))
        return to_euro(rez)

    def status_pivot(self,freq:str="month")->pd.DataFrame:
        df=self.orders_df()
        df["period"]=df["created_at"].dt.to_period(FREQ[freq])
        return df.pivot_table(index="period",columns="status",values="order_id",aggfunc="count",fill_value=0)

    def category_pivot(self,freq:str="month")->pd.DataFrame:
        items=self.sales_items()
        items["period"]=items["created_at"].dt.to_period(FREQ[freq])
        pivot=items.pivot_table(index="period",columns="category",values="revenue_cents",aggfunc="sum",fill_value=0)
        return pivot/100

    def daily_stats(self,date_from=None,date_to=None)->dict:
        daily=self.dynamics("day",date_from,date_to)["revenue"].to_numpy(dtype=float)
        if daily.size==0:
            return {}
        mean=np.mean(daily)
        if daily.size>1:
            std=np.std(daily,ddof=1)
        else:
            std=0.0
        stats={
            "Дней с продажами":int(daily.size),
            "Среднее за день":round(float(mean),2),
            "Медиана за день":round(float(np.median(daily)),2),
            "Стандартное отклонение":round(float(std),2),
            "Минимум":round(float(np.min(daily)),2),
            "Максимум":round(float(np.max(daily)),2),
            "90-й перцентиль":round(float(np.percentile(daily,90)),2),
        }
        if mean:
            stats["Коэффициент вариации, %"]=round(float(std/mean*100),2)
        if daily.size>1:
            naklon=np.polyfit(np.arange(daily.size),daily,1)[0]
            stats["Тренд в день"]=round(float(naklon),2)
        return stats

    def stock_analysis(self,date_from=None,date_to=None,top:int=5)->dict:
        products=[]
        for p in self.db.state["products"].values():
            if not p["deleted"]:
                products.append(p)
        if len(products)==0:
            return {}
        df=pd.DataFrame(products).set_index("id")
        df["available"]=df["stock"]-df["reserved"]
        sold=self.sales_items(date_from,date_to).groupby("product_id").agg(sold=("sold","sum"),revenue_cents=("revenue_cents","sum"))
        df=df.join(sold).fillna({"sold":0,"revenue_cents":0}).astype({"sold":int,"revenue_cents":int})
        cols=["name","category","stock","reserved","available","min_stock","sold"]

        categories=df.groupby("category")[["sold","stock"]].sum()
        vsego=categories["sold"]+categories["stock"]
        categories["consumed_%"]=(categories["sold"]/vsego.where(vsego>0)*100).fillna(0).round(1)

        low=df[df["available"]<=df["min_stock"]]
        restock=low[cols].copy()
        restock["to_order"]=(low["min_stock"]*2-low["available"]).clip(lower=1)

        shortage=df[df["stock"]<df["reserved"]][cols].copy()
        shortage["shortage"]=shortage["reserved"]-shortage["stock"]

        oborot=df.sort_values("revenue_cents",ascending=False).head(top)[cols+["revenue_cents"]].copy()

        return {
            "Остатки и резерв":df[cols],
            "Минимальный остаток":low[cols],
            "Не хватает для текущих заказов":shortage,
            "Без продаж за период":df[df["sold"]==0][cols],
            "Наибольший оборот":to_euro(oborot),
            "Быстрее всего расходуются категории":categories.sort_values("consumed_%",ascending=False),
            "Нужно пополнить":restock,
        }


def category_month_stats(orders:list)->dict:
    rez={}
    for order in orders:
        if order["status"]=="cancelled":
            continue
        month=order["created_at"][:7]
        for line in order["items"]:
            sold=line["quantity"]-line["returned"]
            key=(line["category"],month)
            if key not in rez:
                rez[key]=[Decimal("0"),0]
            rez[key][0]+=Decimal(line["total"])*sold/line["quantity"]
            rez[key][1]+=sold
    return rez


def merge_stats(parts:list)->dict:
    itog={}
    for part in parts:
        for key,(revenue,qty) in part.items():
            if key not in itog:
                itog[key]=[Decimal("0"),0]
            itog[key][0]+=revenue
            itog[key][1]+=qty
    rez={}
    for key,(revenue,qty) in itog.items():
        rez[key]=(round_money(revenue),qty)
    return rez


def cpu_stats(orders:list,processes:int=1)->dict:
    if processes<=1:
        return merge_stats([category_month_stats(orders)])
    chunks=[]
    for i in range(processes):
        chunks.append(orders[i::processes])
    with Pool(processes) as pool:
        return merge_stats(pool.map(category_month_stats,chunks))
