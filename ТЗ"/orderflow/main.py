import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

import pandas as pd

import config
from modules import benchmark, generator
from modules.analytics import Analytics
from modules.customers import Customers
from modules.discounts import Discounts
from modules.exporter import Exporter
from modules.importer import Importer, parse_items
from modules.inventory import Inventory
from modules.orders import STATUSES, Orders
from modules.products import Products, available
from modules.storage import Database
from utils.helpers import timed
from utils.validators import OrderFlowError

log=logging.getLogger("orderflow")


class Store:
    def __init__(self,data_file,default_discounts=config.DEFAULT_DISCOUNTS):
        self.db=Database(data_file)
        self.products=Products(self.db)
        self.customers=Customers(self.db)
        self.inventory=Inventory(self.db,self.products)
        self.discounts=Discounts(self.db)
        self.orders=Orders(self.db,self.products,self.customers,self.inventory,self.discounts,
                           config.DELIVERY_COST,config.FREE_DELIVERY_FROM)
        self.analytics=Analytics(self.db)
        self.importer=Importer(self.db,self.products,self.customers,self.orders)
        self.exporter=Exporter(self.db,self.analytics)
        if not self.db.state["discounts"] and not self.db.state["orders"]:
            for rule in default_discounts:
                self.discounts.add(**rule)


def ask(text:str,default:str="")->str:
    if default:
        otvet=input(f"{text} [{default}]: ").strip()
    else:
        otvet=input(f"{text}: ").strip()
    if otvet=="":
        return default
    return otvet


def ask_num(text:str,kind=int):
    while True:
        otvet=input(f"{text} [{kind.__name__}]: ").strip()
        try:
            return kind(otvet)
        except ValueError:
            print(f"Нужно ввести {kind.__name__}")


def yes(text:str)->bool:
    return input(f"{text} (y/n): ").strip().lower()=="y"


def show_table(data)->None:
    if isinstance(data,pd.DataFrame):
        if data.empty:
            print("нет")
        else:
            print(data.to_string())
    elif isinstance(data,dict):
        for key,value in data.items():
            print(f"  {key}: {value}")


def show_product(p:dict)->None:
    if p["deleted"]:
        sost="удалён"
    elif p["active"]:
        sost="активен"
    else:
        sost="недоступен"
    print(f"[{p['id']}] {p['name']} | {p['category']} | {p['price']} | склад {p['stock']}, "
          f"резерв {p['reserved']}, доступно {available(p)}, мин. {p['min_stock']} | {sost}")


def show_order(o:dict,full:bool=False)->None:
    text=f"{o['order_id']} | {o['created_at']} | покупатель {o['customer_id']} | {o['status']} | итого {o['total']}"
    if o["refund"]!="0.00":
        text+=f" (возврат {o['refund']})"
    print(text)
    if not full:
        return
    for line in o["items"]:
        stroka=(f"   - [{line['product_id']}] {line['name']}: {line['quantity']} × {line['price']} = "
                f"{line['gross']} − {line['discount']} = {line['total']}")
        if line["returned"]:
            stroka+=f", возвращено {line['returned']}"
        print(stroka)
    print(f"   Подытог {o['subtotal']}, скидка на заказ −{o['discount']}, доставка {o['delivery']}, итого {o['total']}")
    if o["applied_discounts"]:
        print("   Скидки:",", ".join(o["applied_discounts"]))
    for h in o["status_history"]:
        print(f"   {h['at']}: {h['from'] or '—'} -> {h['to']}")


def show_import(rez:dict)->None:
    text=f"{rez.get('file','')} [{rez['kind']}]: принято {rez['ok']}, отклонено {len(rez['rejected'])}"
    if "error" in rez:
        text+=f", ошибка файла: {rez['error']}"
    print(text)
    for r in rez["rejected"][:20]:
        print(f"   запись {r['record']} (id {r['id']}): {r['reason']}")


def ask_period()->tuple:
    date_from=ask("Дата с (Enter — всё время)") or None
    date_to=ask("Дата по (Enter — сегодня)") or None
    return date_from,date_to


class App:
    def __init__(self,store):
        self.s=store

    def safe(self,action):
        try:
            action()
        except OrderFlowError as e:
            log.warning(f"{type(e).__name__}: {e}")
            print(f"Ошибка: {e}")
        except (ValueError,KeyError) as e:
            print(f"Ошибка: неверный ввод ({e})")
        except Exception:
            log.exception("Непредвиденная ошибка")
            print(f"Внутренняя ошибка, подробности в журнале {config.LOG_FILE}")

    def menu(self,title,punkty,exit_text="Назад"):
        while True:
            print(f"\n===== {title} =====")
            for nomer,(label,action) in enumerate(punkty,1):
                print(f"{nomer}. {label}")
            print(f"0. {exit_text}")
            vybor=ask("Выбор")
            if vybor=="0":
                break
            if not vybor.isdigit() or not 1<=int(vybor)<=len(punkty):
                print("Нет такого пункта")
                continue
            self.safe(punkty[int(vybor)-1][1])

    def main_menu(self):
        self.menu("OrderFlow",[
            ("Товары",self.products_menu),
            ("Покупатели",self.customers_menu),
            ("Заказы",self.orders_menu),
            ("Склад",self.stock_menu),
            ("Поиск и фильтрация заказов",self.search_orders),
            ("Аналитика и отчёты",self.analytics_menu),
            ("Импорт / экспорт",self.io_menu),
            ("Скидки",self.discounts_menu),
            ("Генерация тестовых данных",self.generate),
            ("Benchmark",self.benchmark),
        ],"Выход")

    def products_menu(self):
        self.menu("Товары",[
            ("Список / поиск",self.list_products),
            ("Добавить",self.add_product),
            ("Изменить",self.edit_product),
            ("Удалить",self.delete_product),
        ])

    def customers_menu(self):
        self.menu("Покупатели",[
            ("Список / поиск",self.list_customers),
            ("Добавить",self.add_customer),
            ("Заказы и статистика покупателя",self.customer_info),
        ])

    def orders_menu(self):
        self.menu("Заказы",[
            ("Создать заказ",self.create_order),
            ("Просмотреть заказ",self.view_order),
            ("Изменить состав (только new)",self.change_items),
            ("Изменить статус",self.change_status),
            ("Оформить возврат",self.register_return),
            ("История операций заказа",self.order_history),
        ])

    def stock_menu(self):
        self.menu("Склад",[
            ("Остатки и резерв",self.list_stock),
            ("Приход товара",self.restock),
            ("Анализ склада",self.stock_analysis),
        ])

    def analytics_menu(self):
        self.menu("Аналитика",[
            ("Сводка продаж",self.sales_summary),
            ("Самые продаваемые товары",self.top_products),
            ("Самые востребованные категории",self.top_categories),
            ("Лучшие покупатели",self.top_customers),
            ("Динамика продаж",self.dynamics),
            ("Сводные таблицы (статусы, категории по месяцам)",self.pivots),
            ("Статистика дневной выручки (NumPy)",self.daily_stats),
            ("Сохранить отчёт в data/reports",self.save_report),
        ])

    def io_menu(self):
        self.menu("Импорт / экспорт",[
            ("Импорт файла (products/customers/orders)",self.import_file),
            ("Импорт нескольких файлов (asyncio)",self.import_many),
            ("Экспорт товаров",self.export_products),
            ("Экспорт заказов",self.export_orders),
        ])

    def discounts_menu(self):
        self.menu("Скидки",[
            ("Список",self.list_discounts),
            ("Добавить",self.add_discount),
        ])

    def list_products(self):
        text=ask("Часть названия (Enter — все)")
        category=ask("Категория (Enter — все)") or None
        spisok=self.s.products.search(text,category)
        for p in spisok:
            show_product(p)
        print(f"Найдено: {len(spisok)}")

    def add_product(self):
        name=ask("Название")
        category=ask("Категория")
        price=ask_num("Цена",float)
        stock=ask_num("Количество на складе")
        min_stock=ask_num("Минимальный остаток")
        show_product(self.s.products.add(name,category,price,stock,min_stock))

    def edit_product(self):
        pid=ask("id товара")
        show_product(self.s.products.get(pid))
        print("Enter — оставить как есть")
        changes={}
        polya=[("name","Название"),("category","Категория"),("price","Цена"),
               ("stock","Остаток"),("min_stock","Мин. остаток"),("active","Доступен (y/n)")]
        for field,label in polya:
            value=ask(label)
            if value:
                changes[field]=value
        show_product(self.s.products.update(pid,**changes))

    def delete_product(self):
        self.s.products.delete(ask("id товара"))
        print("Товар удалён")

    def list_customers(self):
        for c in self.s.customers.search(ask("Имя или контакт (Enter — все)")):
            print(f"[{c['id']}] {c['name']} | {c['contact']} | с {c['registered_at']} | перс. скидка {c['personal_discount']}%")

    def add_customer(self):
        name=ask("Имя или название")
        contact=ask("Контакты")
        skidka=ask_num("Персональная скидка, %",float)
        customer=self.s.customers.add(name,contact,skidka)
        print("Добавлен покупатель с id",customer["id"])

    def customer_info(self):
        cid=ask("id покупателя")
        for order in self.s.customers.orders_of(cid):
            show_order(order)
        show_table(self.s.customers.stats(cid))

    def create_order(self):
        customer_id=ask("id покупателя")
        self.s.customers.get(customer_id)
        items=[]
        while True:
            pid=ask("id товара (Enter — закончить)")
            if pid=="":
                break
            items.append((pid,ask_num("Количество")))
        promo=ask("Промокод (Enter — нет)") or None
        show_order(self.s.orders.create(customer_id,items,promo),full=True)

    def view_order(self):
        show_order(self.s.orders.get(ask("Номер заказа")),full=True)

    def change_items(self):
        oid=ask("Номер заказа")
        pid=ask("id товара")
        qty=ask_num("Новое количество (0 — убрать)")
        show_order(self.s.orders.change_item(oid,pid,qty),full=True)

    def change_status(self):
        print("Статусы:",", ".join(STATUSES))
        oid=ask("Номер заказа")
        status=ask("Новый статус")
        show_order(self.s.orders.set_status(oid,status),full=True)

    def register_return(self):
        oid=ask("Номер заказа")
        text=ask("Что возвращаем, формат id:кол-во;id:кол-во (Enter — весь заказ)")
        returns=None
        if text:
            returns={}
            for item in parse_items(text):
                returns[item["product_id"]]=item["quantity"]
        show_order(self.s.orders.set_status(oid,"returned",returns),full=True)

    def order_history(self):
        for h in self.s.orders.history(ask("Номер заказа")):
            print(f"{h['at']} | {h['action']} | {h['details']}")

    def list_stock(self):
        for p in self.s.products.search():
            show_product(p)

    def restock(self):
        pid=ask("id товара")
        qty=ask_num("Количество")
        show_product(self.s.inventory.restock(pid,qty))

    def stock_analysis(self):
        date_from,date_to=ask_period()
        for title,df in self.s.analytics.stock_analysis(date_from,date_to).items():
            print(f"\n--- {title} ---")
            show_table(df)

    def search_orders(self):
        print("Заполните нужные условия, остальные пропустите Enter")
        found=self.s.orders.search(
            order_id=ask("Номер заказа (часть)"),
            customer_id=ask("id покупателя"),
            status=ask("Статус"),
            date_from=ask("Дата с (ГГГГ-ММ-ДД)"),
            date_to=ask("Дата по"),
            min_total=ask("Сумма от"),
            max_total=ask("Сумма до"),
            product_id=ask("Содержит товар (id)"),
            sort_by=ask("Сортировка date/total/status","date"),
            descending=yes("По убыванию?"),
        )
        for order in found[:50]:
            show_order(order)
        if len(found)>50:
            print(f"Найдено: {len(found)} (показаны первые 50)")
        else:
            print(f"Найдено: {len(found)}")

    def sales_summary(self):
        show_table(self.s.analytics.summary(*ask_period()))

    def top_products(self):
        show_table(self.s.analytics.top_products(10,*ask_period()))

    def top_categories(self):
        show_table(self.s.analytics.top_categories(10,*ask_period()))

    def top_customers(self):
        show_table(self.s.analytics.top_customers(10,*ask_period()))

    def dynamics(self):
        freq=ask("day/week/month","month")
        show_table(self.s.analytics.dynamics(freq,*ask_period()))

    def pivots(self):
        show_table(self.s.analytics.status_pivot())
        print()
        show_table(self.s.analytics.category_pivot())

    def daily_stats(self):
        show_table(self.s.analytics.daily_stats(*ask_period()))

    def save_report(self):
        folder=config.REPORTS_DIR/datetime.now().strftime("report_%Y%m%d_%H%M%S")
        with timed(log,"Формирование отчёта"):
            paths=self.s.exporter.report(folder)
        print(f"Сохранено файлов: {len(paths)} в {folder}")

    def import_file(self):
        path=ask("Путь к файлу")
        kind=ask("Что импортируем","orders")
        show_import(self.s.importer.import_file(path,kind))

    def import_many(self):
        paths=[]
        for p in ask("Пути к файлам через запятую").split(","):
            if p.strip():
                paths.append(p.strip())
        kind=ask("Что импортируем","orders")
        for rez in self.s.importer.import_files(paths,kind,"async",config.THREADS):
            show_import(rez)

    def export_products(self):
        path=config.REPORTS_DIR/ask("Имя файла","products.csv")
        print("Сохранено:",self.s.exporter.products(path))

    def export_orders(self):
        path=config.REPORTS_DIR/ask("Имя файла","orders.json")
        print("Сохранено:",self.s.exporter.orders(path))

    def list_discounts(self):
        for r in self.s.discounts.rules:
            print(r)

    def add_discount(self):
        name=ask("Название")
        tip=ask("Тип product/category/threshold/promo")
        percent=ask_num("Процент",float)
        target=ask("id товара или категория (для product/category)") or None
        min_total=ask("Порог суммы (для threshold)") or None
        code=ask("Промокод (для promo)") or None
        valid_from=ask("Действует с (ГГГГ-ММ-ДД)") or None
        valid_to=ask("Действует по") or None
        stackable=yes("Суммируется с другими?")
        print(self.s.discounts.add(name,tip,percent,target,min_total,code,valid_from,valid_to,stackable))

    def generate(self):
        counters=self.s.db.state["counters"]
        data=generator.generate(
            customers=ask_num("Покупателей"),
            products=ask_num("Товаров"),
            orders=ask_num("Заказов"),
            avg_items=ask_num("Среднее число позиций",float),
            date_from=ask("Период с","2026-01-01"),
            date_to=ask("Период по","2026-10-31"),
            seed=ask_num("seed"),
            first_customer=counters["customer"]+1,
            first_product=counters["product"]+1,
            first_order=counters["order"]+1,
        )
        paths=generator.write_files(data,config.GENERATED_DIR)
        print("Файлы:",", ".join(str(p) for p in paths.values()))
        if yes("Загрузить в магазин?"):
            with self.s.db.transaction():
                for kind in ("customers","products","orders"):
                    show_import(self.s.importer.import_records(data[kind],kind))

    def benchmark(self):
        while True:
            try:
                sizes=[]
                for x in input("Объёмы заказов через запятую [int,int,...]: ").split(","):
                    sizes.append(int(x))
                break
            except ValueError:
                print("Нужно ввести целые числа через запятую")
        seed=ask_num("seed")
        threads=ask_num("Потоков")
        processes=ask_num("Процессов")
        rez=benchmark.run(Store,sizes,seed,threads,processes)
        show_table(rez)
        path=config.REPORTS_DIR/datetime.now().strftime("benchmark_%Y%m%d_%H%M%S.csv")
        path.parent.mkdir(parents=True,exist_ok=True)
        rez.to_csv(path,index=False)
        print("Результаты сохранены:",path)


def main()->None:
    config.setup_logging()
    log.info("Запуск программы")
    try:
        store=Store(config.DB_FILE)
        App(store).main_menu()
    except OrderFlowError as e:
        print(f"Ошибка: {e}")
        log.error(str(e))
    except (KeyboardInterrupt,EOFError):
        print()
    finally:
        log.info("Завершение программы")
    print("До свидания!")


if __name__=="__main__":
    main()
