import os
import sqlite3
from pathlib import Path
from typing import Dict, Optional
from util.logging_config import get_logger

logger = get_logger(__name__)


DB_DIR = os.getenv("DB_DIR", "./data")
Path(DB_DIR).mkdir(parents=True, exist_ok=True)


def _db_path(db_name: str) -> str:
    return str(Path(DB_DIR) / f"{db_name}.db")


def check_table_exist(db_name, table_name):
    with sqlite3.connect(_db_path(db_name)) as con:
        cur = con.cursor()
        sql = "SELECT name FROM sqlite_master WHERE type='table' and name=:table_name"
        cur.execute(sql, {"table_name": table_name})

        if len(cur.fetchall()) > 0:
            return True
        else:
            return False


def insert_df_to_db(db_name, table_name, df, option="replace"):
    with sqlite3.connect(_db_path(db_name)) as con:
        df.to_sql(table_name, con, if_exists=option)


def execute_sql(db_name, sql, param={}):
    with sqlite3.connect(_db_path(db_name)) as con:
        cur = con.cursor()
        cur.execute(sql, param)
        return cur


def _ensure_master_list_table(db_name: str):
    """`master_list` 테이블을 보장한다: code TEXT PRIMARY KEY, name TEXT"""
    try:
        with sqlite3.connect(_db_path(db_name)) as con:
            cur = con.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS master_list (
                       code TEXT PRIMARY KEY,
                       name TEXT
                   )"""
            )
            con.commit()
    except Exception as e:
        logger.exception("_ensure_master_list_table 실패: %s", e)


def upsert_stock_name(db_name: str, code: str, name: str) -> None:
    """종목 코드-명 정보를 INSERT OR REPLACE 한다."""
    try:
        _ensure_master_list_table(db_name)
        with sqlite3.connect(_db_path(db_name)) as con:
            cur = con.cursor()
            cur.execute("INSERT OR REPLACE INTO master_list (code, name) VALUES (?, ?)", (code, name))
            con.commit()
    except Exception as e:
        logger.exception("upsert_stock_name 실패: %s", e)


def get_stock_name(db_name: str, code: str) -> Optional[str]:
    """DB에서 종목명을 조회한다. 없으면 None 반환."""
    try:
        _ensure_master_list_table(db_name)
        with sqlite3.connect(_db_path(db_name)) as con:
            cur = con.cursor()
            cur.execute("SELECT name FROM master_list WHERE code = ?", (code,))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.exception("get_stock_name 실패: %s", e)
        return None


def load_all_stock_names(db_name: str) -> Dict[str, str]:
    """DB에 저장된 모든 code->name 맵을 반환한다."""
    result: Dict[str, str] = {}
    try:
        _ensure_master_list_table(db_name)
        with sqlite3.connect(_db_path(db_name)) as con:
            cur = con.cursor()
            cur.execute("SELECT code, name FROM master_list")
            for code, name in cur.fetchall():
                result[code] = name
    except Exception as e:
        logger.exception("load_all_stock_names 실패: %s", e)
    return result


if __name__ == "__main__":
    result = check_table_exist("RSIStrategy", "universe")
    print(f"Table 'universe' exists: {result}")