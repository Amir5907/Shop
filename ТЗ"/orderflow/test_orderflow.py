import json
import logging
import sys
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from main import Store

config.setup_logging()
test_log = logging.getLogger("orderflow.tests")
from modules.analytics import cpu_stats
from modules.generator import generate
from modules.products import available
from utils.validators import (DuplicateError, NotFoundError, StatusError, StockError, StorageError,
                              ValidationError)


class Base(unittest.TestCase):
    def setUp(self):
        self.s = Store(None, default_discounts=())
        self.customer = self.s.customers.add("Анна", "anna@mail.com")
        self.pen = self.s.products.add("Ручка", "Канцелярия", "0.10", 100)
        self.phone = self.s.products.add("Телефон", "Электроника", "200.00", 3, min_stock=1)

    def order(self, *items, **kw):
        return self.s.orders.create(self.customer["id"], list(items), **kw)


class TestOrders(Base):
    def test_create_sums_same_product_and_uses_decimal(self):
        o = self.order((self.pen["id"], 1), (self.pen["id"], 2))
        self.assertEqual(len(o["items"]), 1)
        self.assertEqual(o["items"][0]["quantity"], 3)
        self.assertEqual(o["subtotal"], "0.30")
        self.assertEqual(o["total"], "5.30")
        self.assertEqual(self.pen["reserved"], 3)
        self.assertTrue(o["order_id"].startswith("ORD-"))

    def test_unknown_customer_product_and_bad_quantity(self):
        with self.assertRaises(NotFoundError):
            self.s.orders.create(999, [(self.pen["id"], 1)])
        with self.assertRaises(NotFoundError):
            self.order((999, 1))
        for bad in (0, -1, "abc", 1.5):
            with self.assertRaises(ValidationError):
                self.order((self.pen["id"], bad))

    def test_not_enough_stock_changes_nothing(self):
        with self.assertRaises(StockError):
            self.order((self.pen["id"], 1), (self.phone["id"], 4))
        self.assertEqual(self.pen["reserved"], 0)
        self.assertEqual(self.s.db.state["orders"], {})

    def test_price_change_does_not_change_old_order(self):
        o = self.order((self.phone["id"], 1))
        self.s.products.update(self.phone["id"], price="999")
        self.assertEqual(self.s.orders.get(o["order_id"])["items"][0]["price"], "200.00")

    def test_change_items(self):
        o = self.order((self.pen["id"], 2))
        self.s.orders.change_item(o["order_id"], self.phone["id"], 1)
        self.s.orders.change_item(o["order_id"], self.pen["id"], 0)
        self.assertEqual([l["product_id"] for l in o["items"]], [self.phone["id"]])
        self.assertEqual((self.pen["reserved"], self.phone["reserved"]), (0, 1))
        self.assertEqual(o["total"], "200.00")
        with self.assertRaises(ValidationError):
            self.s.orders.change_item(o["order_id"], self.phone["id"], 0)


class TestStatusesAndStock(Base):
    def test_full_lifecycle_and_return(self):
        o = self.order((self.phone["id"], 2))
        for st in ("confirmed", "assembling", "ready", "shipped", "delivered"):
            self.s.orders.set_status(o["order_id"], st)
        self.assertEqual((self.phone["stock"], self.phone["reserved"]), (1, 0))
        self.s.orders.set_status(o["order_id"], "returned", {self.phone["id"]: 1})
        self.assertEqual(self.phone["stock"], 2)
        self.assertEqual(o["refund"], "200.00")
        self.assertEqual(o["status_history"][-1]["from"], "delivered")
        actions = [h["action"] for h in self.s.orders.history(o["order_id"])]
        self.assertEqual(actions[:2], ["stock_reserved", "order_created"])
        self.assertIn("return_registered", actions)

    def test_invalid_transitions(self):
        o = self.order((self.pen["id"], 1))
        with self.assertRaises(StatusError):
            self.s.orders.set_status(o["order_id"], "delivered")
        for st in ("confirmed", "assembling", "ready", "shipped", "delivered"):
            self.s.orders.set_status(o["order_id"], st)
        with self.assertRaises(StatusError):
            self.s.orders.set_status(o["order_id"], "assembling")
        with self.assertRaises(ValidationError):
            self.s.orders.set_status(o["order_id"], "lost")

    def test_cancel_releases_reserve(self):
        o = self.order((self.phone["id"], 3))
        self.assertEqual(available(self.phone), 0)
        self.s.orders.set_status(o["order_id"], "cancelled")
        self.assertEqual(available(self.phone), 3)

    def test_concurrent_orders_for_last_units(self):
        results = []

        def buy():
            try:
                self.order((self.phone["id"], 1))
                results.append("ok")
            except StockError:
                results.append("fail")

        threads = [threading.Thread(target=buy) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("ok"), 3)
        self.assertEqual(self.phone["reserved"], 3)
        self.assertEqual(available(self.phone), 0)
        self.assertEqual(len(self.s.db.state["orders"]), 3)


class TestDiscounts(Base):
    def test_item_and_order_discounts(self):
        self.s.discounts.add("Электроника", "category", 10, target="Электроника")
        self.s.discounts.add("Порог", "threshold", 5, min_total=100)
        self.s.discounts.add("Промо", "promo", 2, code="SALE", valid_to="2099-12-31")
        o = self.order((self.phone["id"], 1), promo_code="sale")
        self.assertEqual(o["items"][0]["total"], "180.00")
        self.assertEqual(o["discount"], "12.60")
        self.assertEqual(o["total"], "167.40")

    def test_not_stackable_takes_best(self):
        self.s.discounts.add("Порог", "threshold", 5, min_total=100)
        self.s.discounts.add("Промо", "promo", 15, code="BIG", stackable=False)
        o = self.order((self.phone["id"], 1), promo_code="BIG")
        self.assertEqual(o["discount"], "30.00")

    def test_expired_or_unknown_promo(self):
        self.s.discounts.add("Старый", "promo", 10, code="OLD", valid_to="2020-01-01")
        for code in ("OLD", "NOPE"):
            with self.assertRaises(ValidationError):
                self.order((self.pen["id"], 1), promo_code=code)
        self.assertEqual(self.pen["reserved"], 0)


class TestImportExportSearch(Base):
    def test_import_with_bad_records(self):
        records = [
            {"name": "Чайник", "category": "Дом", "price": "30", "stock": "5"},
            {"name": "Без цены", "category": "Дом", "stock": "5"},
            {"id": self.pen["id"], "name": "Дубль", "category": "Дом", "price": "1", "stock": "1"},
            {"name": "Минус", "category": "Дом", "price": "-3", "stock": "1"},
            "мусор",
        ]
        result = self.s.importer.import_records(records, "products")
        self.assertEqual(result["ok"], 1)
        self.assertEqual([r["record"] for r in result["rejected"]], [2, 3, 4, 5])

    def test_export_import_roundtrip_csv(self):
        self.order((self.pen["id"], 2), (self.phone["id"], 1))
        with tempfile.TemporaryDirectory() as tmp:
            path = self.s.exporter.orders(Path(tmp) / "orders.csv")
            other = Store(None, default_discounts=())
            other.customers.add("Анна", "anna@mail.com")
            other.importer.import_records(list(self.s.db.state["products"].values()), "products")
            result = other.importer.import_file(path, "orders")
        self.assertEqual(result["ok"], 1)
        self.assertEqual(list(other.db.state["orders"].values())[0]["total"],
                         list(self.s.db.state["orders"].values())[0]["total"])

    def test_combined_search(self):
        a = self.order((self.pen["id"], 1), created_at="2026-01-10")
        b = self.order((self.phone["id"], 1), created_at="2026-02-10")
        self.s.orders.set_status(b["order_id"], "confirmed")
        self.assertEqual(self.s.orders.search(status="confirmed"), [b])
        self.assertEqual(self.s.orders.search(date_from="2026-01-01", date_to="2026-01-31"), [a])
        self.assertEqual(self.s.orders.search(min_total="100", product_id=self.phone["id"]), [b])
        self.assertEqual(self.s.orders.search(sort_by="total", descending=True), [b, a])


class TestAnalytics(Base):
    def test_summary_excludes_cancelled(self):
        self.order((self.pen["id"], 10))
        c = self.order((self.phone["id"], 1))
        self.s.orders.set_status(c["order_id"], "cancelled")
        summary = self.s.analytics.summary()
        self.assertEqual(summary["Отменено"], 1)
        self.assertEqual(summary["Сумма оформленных заказов"], Decimal("6.00"))
        self.assertEqual(summary["Средний чек"], Decimal("6.00"))
        self.assertEqual(int(self.s.analytics.top_products().loc[self.pen["id"], "sold"]), 10)

    def test_cpu_stats_same_in_processes(self):
        data = generate(20, 30, 300, seed=1)
        self.s = Store(None, default_discounts=())
        for kind in ("customers", "products", "orders"):
            self.assertEqual(self.s.importer.import_records(data[kind], kind)["rejected"], [])
        orders = list(self.s.db.state["orders"].values())
        self.assertEqual(cpu_stats(orders, 1), cpu_stats(orders, 2))
        self.assertFalse(self.s.analytics.stock_analysis()["Остатки и резерв"].empty)

    def test_generator_seed_repeatable(self):
        self.assertEqual(generate(5, 5, 20, seed=7), generate(5, 5, 20, seed=7))
        self.assertNotEqual(generate(5, 5, 20, seed=7), generate(5, 5, 20, seed=8))


class TestStorage(unittest.TestCase):
    def test_persist_backup_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.json"
            s = Store(path, default_discounts=())
            s.customers.add("Анна", "a@a")
            s.customers.add("Борис", "b@b")
            self.assertEqual(len(Store(path, default_discounts=()).db.state["customers"]), 2)
            path.write_text("{битый json", encoding="utf-8")
            self.assertEqual(len(Store(path, default_discounts=()).db.state["customers"]), 1)
            path.with_suffix(".json.bak").write_text("тоже битый", encoding="utf-8")
            with self.assertRaises(StorageError):
                Store(path, default_discounts=())

    def test_failed_save_rolls_back(self):
        s = Store(None, default_discounts=())
        s.customers.add("Анна", "a@a")
        s.db.save = lambda: (_ for _ in ()).throw(StorageError("диск полон"))
        with self.assertRaises(StorageError):
            s.customers.add("Борис", "b@b")
        self.assertEqual(len(s.db.state["customers"]), 1)


class LoggedResult(unittest.TextTestResult):
    def startTest(self, test):
        super().startTest(test)
        test_log.info(f"START {test.id()}")

    def addSuccess(self, test):
        super().addSuccess(test)
        test_log.info(f"OK    {test.id()}")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        test_log.error(f"FAIL  {test.id()}\n{self.failures[-1][1]}")

    def addError(self, test, err):
        super().addError(test, err)
        test_log.error(f"ERROR {test.id()}\n{self.errors[-1][1]}")


if __name__ == "__main__":
    unittest.main(testRunner=unittest.TextTestRunner(resultclass=LoggedResult, verbosity=2))
