import os
import sqlite3
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

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


_PURCHASE_DATE_DB = "RSIStrategy"
_PURCHASE_DATE_TABLE = "purchase_dates"


def _ensure_purchase_dates_table() -> None:
    """purchase_dates 테이블 생성 보장: code TEXT PRIMARY KEY, purchase_date TEXT (YYYYMMDD)"""
    try:
        with sqlite3.connect(_db_path(_PURCHASE_DATE_DB)) as con:
            cur = con.cursor()
            cur.execute(
                f"""CREATE TABLE IF NOT EXISTS {_PURCHASE_DATE_TABLE} (
                       code TEXT PRIMARY KEY,
                       purchase_date TEXT NOT NULL
                   )"""
            )
            con.commit()
    except Exception as e:
        logger.exception("_ensure_purchase_dates_table 실패: %s", e)


def upsert_purchase_date(code: str, purchase_date: str) -> None:
    """종목 매수일을 저장/갱신한다. purchase_date 형식: YYYYMMDD"""
    try:
        _ensure_purchase_dates_table()
        with sqlite3.connect(_db_path(_PURCHASE_DATE_DB)) as con:
            cur = con.cursor()
            cur.execute(
                f"INSERT OR REPLACE INTO {_PURCHASE_DATE_TABLE} (code, purchase_date) VALUES (?, ?)",
                (code, purchase_date),
            )
            con.commit()
    except Exception as e:
        logger.exception("upsert_purchase_date 실패 (%s): %s", code, e)


def get_purchase_date(code: str) -> Optional[str]:
    """DB에서 종목 매수일을 조회한다. 없으면 None 반환."""
    try:
        _ensure_purchase_dates_table()
        with sqlite3.connect(_db_path(_PURCHASE_DATE_DB)) as con:
            cur = con.cursor()
            cur.execute(f"SELECT purchase_date FROM {_PURCHASE_DATE_TABLE} WHERE code = ?", (code,))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.exception("get_purchase_date 실패 (%s): %s", code, e)
        return None


def load_all_purchase_dates() -> Dict[str, str]:
    """DB에 저장된 모든 code->purchase_date 맵을 반환한다."""
    result: Dict[str, str] = {}
    try:
        _ensure_purchase_dates_table()
        with sqlite3.connect(_db_path(_PURCHASE_DATE_DB)) as con:
            cur = con.cursor()
            cur.execute(f"SELECT code, purchase_date FROM {_PURCHASE_DATE_TABLE}")
            for code, purchase_date in cur.fetchall():
                result[code] = purchase_date
    except Exception as e:
        logger.exception("load_all_purchase_dates 실패: %s", e)
    return result


def delete_purchase_date(code: str) -> None:
    """DB에서 종목 매수일 기록을 삭제한다."""
    try:
        _ensure_purchase_dates_table()
        with sqlite3.connect(_db_path(_PURCHASE_DATE_DB)) as con:
            cur = con.cursor()
            cur.execute(f"DELETE FROM {_PURCHASE_DATE_TABLE} WHERE code = ?", (code,))
            con.commit()
    except Exception as e:
        logger.exception("delete_purchase_date 실패 (%s): %s", code, e)


_OHLCV_COLS = {'open', 'high', 'low', 'close', 'volume', 'adj_close', 'change'}


def resolve_date_column(df: pd.DataFrame) -> str:
    """DataFrame에서 OHLCV가 아닌 첫 번째 컬럼을 날짜 컬럼명으로 반환

    Returns:
        컬럼명 (없으면 첫 번째 컬럼, DataFrame이 비어있으면 'index')
    """
    for col in df.columns:
        if col.lower() not in _OHLCV_COLS:
            return col
    return df.columns[0] if len(df.columns) > 0 else 'index'


def get_date_col_name(db_name: str, table_name: str) -> str:
    """SQLite 테이블의 컬럼 중 OHLCV가 아닌 첫 번째 컬럼명을 반환

    Args:
        db_name: DB 파일명 (확장자 제외)
        table_name: 테이블명

    Returns:
        날짜 컬럼명 (없으면 'index')
    """
    with sqlite3.connect(_db_path(db_name)) as con:
        cur = con.cursor()
        cur.execute(f"PRAGMA table_info(`{table_name}`)")
        cols = [r[1] for r in cur.fetchall()]
    for col in cols:
        if col.lower() not in _OHLCV_COLS:
            return col
    return cols[0] if cols else 'index'


if __name__ == "__main__":
    result = check_table_exist("RSIStrategy", "universe")
    print(f"Table 'universe' exists: {result}")