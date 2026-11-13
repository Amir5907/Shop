import logging
from decimal import Decimal
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR=Path(__file__).resolve().parent.parent
DATA_DIR=BASE_DIR/"data"
DB_FILE=DATA_DIR/"data"/"store.json"
GENERATED_DIR=DATA_DIR/"data"/"generated"
REPORTS_DIR=DATA_DIR/"reports"
LOG_FILE=DATA_DIR/"logs"/"orderflow.log"

CURRENCY="EUR"
DELIVERY_COST=Decimal("5.00")
FREE_DELIVERY_FROM=Decimal("100.00")

THREADS=4
PROCESSES=4
SEED=42

DEFAULT_DISCOUNTS=[
    {"name":"−5% на электронику","type":"category","percent":5,"target":"Электроника"},
    {"name":"−3% от 300","type":"threshold","percent":3,"min_total":300},
    {"name":"Промокод WELCOME10","type":"promo","percent":10,"code":"WELCOME10",
     "valid_from":"2026-01-01","valid_to":"2026-12-31","stackable":False},
]


def setup_logging()->None:
    LOG_FILE.parent.mkdir(parents=True,exist_ok=True)
    logger=logging.getLogger("orderflow")
    logger.setLevel(logging.INFO)
    file_handler=RotatingFileHandler(LOG_FILE,maxBytes=1024*1024,backupCount=3,encoding="utf-8")
    formatter=logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    file_handler.setFormatter(formatter)
    logger.handlers=[file_handler]
