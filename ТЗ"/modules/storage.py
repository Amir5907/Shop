import json
import logging
import os
import shutil
import threading
from contextlib import contextmanager
from pathlib import Path

from utils.validators import StorageError

log=logging.getLogger("orderflow.storage")


def empty_state()->dict:
    return {
        "products":{},
        "customers":{},
        "orders":{},
        "discounts":[],
        "history":[],
        "counters":{"product":0,"customer":0,"order":10000},
    }


class Database:
    def __init__(self,path):
        if path:
            self.path=Path(path)
        else:
            self.path=None
        self.lock=threading.RLock()
        self.depth=0
        self.state=self.load()
        self.last_saved=json.dumps(self.state,ensure_ascii=False)

    @property
    def backup(self)->Path:
        return self.path.with_suffix(".json.bak")

    def read_file(self,path:Path)->dict:
        with open(path,encoding="utf-8") as f:
            data=json.load(f)
        if not isinstance(data,dict):
            raise ValueError("файл не является объектом")
        for key in empty_state():
            if key not in data:
                raise ValueError(f"нет раздела {key}")
        return data

    def load(self)->dict:
        if self.path is None:
            return empty_state()
        if not self.path.exists() and not self.backup.exists():
            return empty_state()
        for fail in (self.path,self.backup):
            if not fail.exists():
                continue
            try:
                data=self.read_file(fail)
                if fail==self.backup:
                    log.warning(f"Основной файл повреждён, данные восстановлены из {fail}")
                return data
            except (OSError,ValueError) as e:
                log.error(f"Файл {fail} не читается: {e}")
        raise StorageError("Файл данных повреждён, резервная копия тоже. Подробности в журнале.")

    def save(self)->None:
        text=json.dumps(self.state,ensure_ascii=False,indent=1)
        if self.path is not None:
            tmp=self.path.with_suffix(".tmp")
            try:
                self.path.parent.mkdir(parents=True,exist_ok=True)
                with open(tmp,"w",encoding="utf-8") as f:
                    f.write(text)
                    f.flush()
                    os.fsync(f.fileno())
                if self.path.exists():
                    shutil.copy2(self.path,self.backup)
                os.replace(tmp,self.path)
            except OSError as e:
                log.exception(f"Ошибка сохранения в {self.path}")
                raise StorageError("Не удалось сохранить данные, изменения отменены") from e
        self.last_saved=text

    def rollback(self)->None:
        self.state.clear()
        self.state.update(json.loads(self.last_saved))

    @contextmanager
    def transaction(self):
        with self.lock:
            self.depth+=1
            try:
                yield self.state
                if self.depth==1:
                    self.save()
            except Exception:
                if self.depth==1:
                    self.rollback()
                raise
            finally:
                self.depth-=1
