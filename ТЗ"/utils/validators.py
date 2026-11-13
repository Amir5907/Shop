from datetime import datetime
from decimal import Decimal, InvalidOperation

from utils.helpers import round_money


class OrderFlowError(Exception):
    pass


class ValidationError(OrderFlowError):
    pass


class DuplicateError(ValidationError):
    pass


class NotFoundError(OrderFlowError):
    pass


class StockError(OrderFlowError):
    pass


class StatusError(OrderFlowError):
    pass


class StorageError(OrderFlowError):
    pass


def to_int(value,field:str,minimum:int=1)->int:
    if isinstance(value,bool):
        raise ValidationError(f"{field}: ожидается целое число")
    try:
        chislo=int(str(value).strip())
    except (TypeError,ValueError):
        raise ValidationError(f"{field}: ожидается целое число, получено {value!r}") from None
    if chislo<minimum:
        raise ValidationError(f"{field}: значение должно быть не меньше {minimum}")
    return chislo


def to_money(value,field:str="цена",allow_zero:bool=True)->Decimal:
    try:
        summa=Decimal(str(value).strip())
    except InvalidOperation:
        raise ValidationError(f"{field}: ожидается число, получено {value!r}") from None
    if not summa.is_finite() or summa<0:
        raise ValidationError(f"{field}: недопустимое значение {value!r}")
    if summa==0 and not allow_zero:
        raise ValidationError(f"{field}: не может быть нулём")
    return round_money(summa)


def to_percent(value,field:str="скидка")->Decimal:
    procent=to_money(value,field)
    if procent>100:
        raise ValidationError(f"{field}: не может быть больше 100%")
    return procent


def required_str(value,field:str)->str:
    if value is None:
        text=""
    else:
        text=str(value).strip()
    if text=="":
        raise ValidationError(f"{field}: поле не может быть пустым")
    return text


def to_datetime(value,field:str="дата")->datetime:
    try:
        return datetime.fromisoformat(str(value).strip())
    except ValueError:
        raise ValidationError(f"{field}: ожидается ГГГГ-ММ-ДД [ЧЧ:ММ:СС], получено {value!r}") from None


def require_fields(record:dict,fields:list)->None:
    net=[]
    for f in fields:
        if record.get(f) in (None,""):
            net.append(f)
    if net:
        raise ValidationError("нет обязательных полей: "+", ".join(net))
