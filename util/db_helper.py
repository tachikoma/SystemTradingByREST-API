import os
import sqlite3
from pathlib import Path


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


if __name__ == "__main__":
    result = check_table_exist("RSIStrategy", "universe")
    print(f"Table 'universe' exists: {result}")