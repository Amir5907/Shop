import time
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

CENT=Decimal("0.01")
DATE_FMT="%Y-%m-%d %H:%M:%S"


def now()->str:
    return datetime.now().strftime(DATE_FMT)


def round_money(summa:Decimal)->Decimal:
    return summa.quantize(CENT,rounding=ROUND_HALF_UP)


def add_history(state:dict,action:str,order_id:str,**details)->None:
    state["history"].append({
        "at":now(),
        "action":action,
        "order_id":order_id,
        "details":details,
    })


@contextmanager
def timed(log,what:str):
    start=time.perf_counter()
    yield
    log.info(f"{what}: {time.perf_counter()-start:.3f} c")
