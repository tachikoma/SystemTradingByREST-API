"""
월별 유니버스 스냅샷 재생성 스크립트

DB에 이미 저장된 가격 데이터와 universe 테이블을 읽어서
universe_availability / universe_snapshots 테이블을 새로운 기준으로 재생성합니다.

API 재호출 없이 로컬 DB만으로 실행 가능합니다.

사용법:
    poetry run python backtest/rebuild_snapshots.py

환경변수:
    MONTHLY_UNIVERSE_SIZE: 월별 유니버스 크기 (기본 250)
    DB_DIR: DB 파일 디렉토리 (기본 ./data)
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
import sqlite3

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

from util.logging_config import configure_logging, get_logger
from util.db_helper import check_table_exist, insert_df_to_db, execute_sql

configure_logging(file_name='rebuild_snapshots.log')
logger = get_logger('rebuild_snapshots')

# DB 설정
DB_DIR = os.getenv('DB_DIR', './data')
DB_NAME = 'backtest_data'
DB_PATH = os.path.join(DB_DIR, f'{DB_NAME}.db')

# 실전 전략(_filter_and_create_universe)과 동일한 종목명 제외 키워드
NAME_EXCLUDE_KEYWORDS = [
    "지주", "홀딩스", "스팩", "리츠", "캐피탈",
    "CD금리", "KOFR금리", "머니마켓",
]

# 최소 일평균 거래대금 기준 (원 단위): 실전 전략의 30억 기준과 동일
MIN_DAILY_TRADING_VALUE = 3_000_000_000  # 30억 원


def passes_strategy_filter(code: str, code_name: str) -> bool:
    """실전 전략과 동일한 종목 필터 통과 여부 확인

    1. 종목코드 끝자리 '0' (우선주 제외)
    2. 종목명 제외 키워드 (지주/홀딩스/스팩/리츠/캐피탈 등)
    """
    if not code.endswith('0'):
        return False
    for keyword in NAME_EXCLUDE_KEYWORDS:
        if keyword in code_name:
            return False
    return True


def load_universe(conn: sqlite3.Connection) -> dict:
    """universe 테이블에서 {code: code_name} 딕셔너리 로드"""
    rows = conn.execute("SELECT code, code_name FROM universe").fetchall()
    # 컬럼 이름이 없는 경우 인덱스 기반 접근
    universe = {}
    for row in rows:
        if len(row) >= 3:
            # (idx, code, code_name, created_at) 구조
            universe[str(row[1])] = str(row[2])
        elif len(row) == 2:
            universe[str(row[0])] = str(row[1])
    return universe


def load_price_tables(conn: sqlite3.Connection, universe: dict) -> dict:
    """각 종목의 가격 테이블에서 월별 거래대금 계산

    Returns:
        {code: {'earliest': 'YYYYMM', 'latest': 'YYYYMM', 'monthly_liquidity': {YYYYMM: float}}}
    """
    price_data = {}
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]

    total = len(universe)
    for idx, (code, code_name) in enumerate(universe.items(), 1):
        if code not in tables:
            logger.debug(f"[{idx}/{total}] {code_name}({code}): 가격 테이블 없음, 스킵")
            continue

        try:
            # 'index' 또는 'date' 컬럼 처리
            pragma = conn.execute(f"PRAGMA table_info(`{code}`)").fetchall()
            col_names = [r[1] for r in pragma]
            date_col = 'date' if 'date' in col_names else 'index'
            df = pd.read_sql(f'SELECT `{date_col}`, close, volume FROM `{code}`', conn)
            if 'date' in df.columns and 'index' not in df.columns:
                df = df.rename(columns={'date': 'index'})
        except Exception as e:
            logger.warning(f"[{idx}/{total}] {code_name}({code}): 테이블 읽기 실패 — {e}")
            continue

        if df.empty or 'index' not in df.columns:
            logger.debug(f"[{idx}/{total}] {code_name}({code}): 데이터 없음, 스킵")
            continue

        df['yyyymm'] = df['index'].astype(str).str[:6]
        df['trading_value'] = df['close'].astype(float) * df['volume'].astype(float)
        monthly_liquidity = df.groupby('yyyymm')['trading_value'].mean().to_dict()

        date_sorted = sorted(df['index'].astype(str).tolist())
        earliest = date_sorted[0][:6]
        latest = date_sorted[-1][:6]

        price_data[code] = {
            'earliest': earliest,
            'latest': latest,
            'monthly_liquidity': monthly_liquidity,
        }

        if idx % 20 == 0:
            logger.info(f"가격 데이터 로드 진행: {idx}/{total}")

    logger.info(f"가격 데이터 로드 완료: {len(price_data)}/{total}개 종목")
    return price_data


def rebuild_availability(conn: sqlite3.Connection, universe: dict, price_data: dict):
    """universe_availability 테이블 재생성"""
    records = []
    for code, code_name in universe.items():
        if code not in price_data:
            continue
        records.append({
            'code': code,
            'code_name': code_name,
            'earliest_yyyymm': price_data[code]['earliest'],
            'latest_yyyymm': price_data[code]['latest'],
        })

    if not records:
        logger.warning("universe_availability: 저장할 데이터 없음")
        return

    df = pd.DataFrame(records)
    # 기존 테이블 삭제 후 재생성
    conn.execute("DROP TABLE IF EXISTS universe_availability")
    conn.commit()
    insert_df_to_db(DB_NAME, 'universe_availability', df)
    logger.info(f"universe_availability 재생성 완료: {len(records)}개 종목")


def rebuild_snapshots(conn: sqlite3.Connection, universe: dict, price_data: dict):
    """universe_snapshots 테이블 재생성 (실전 전략 동일 기준)"""
    snapshot_size = int(os.getenv('MONTHLY_UNIVERSE_SIZE', '250'))
    logger.info(f"월별 유니버스 크기: {snapshot_size}개 (MONTHLY_UNIVERSE_SIZE)")

    # 1차 필터: 우선주 + 이름 키워드 제외 (시간 불변)
    filtered_universe = {
        code: name
        for code, name in universe.items()
        if code in price_data and passes_strategy_filter(code, name)
    }
    excluded = len(universe) - len(filtered_universe)
    logger.info(
        f"1차 필터(우선주+이름키워드) 통과: {len(filtered_universe)}개 / {len(universe)}개 "
        f"({excluded}개 제외)"
    )

    # 월별 거래대금 데이터 수집
    monthly_rows = []
    for code, code_name in filtered_universe.items():
        monthly_liquidity = price_data[code].get('monthly_liquidity', {})
        for yyyymm, liquidity in monthly_liquidity.items():
            monthly_rows.append({
                'yyyymm': yyyymm,
                'code': code,
                'code_name': code_name,
                'monthly_trading_value': float(liquidity),
            })

    if not monthly_rows:
        logger.warning("universe_snapshots: 저장할 데이터 없음")
        return

    monthly_df = pd.DataFrame(monthly_rows)

    # 2차 필터: 최소 거래대금 30억 미만 제외
    before = len(monthly_df)
    monthly_df = monthly_df[monthly_df['monthly_trading_value'] >= MIN_DAILY_TRADING_VALUE]
    logger.info(
        f"2차 필터(최소 거래대금 30억): {len(monthly_df)}개 / {before}개 행 통과 "
        f"({before - len(monthly_df)}개 제외)"
    )

    # 월별 거래대금 기준 상위 N개 선택
    monthly_df = monthly_df.sort_values(['yyyymm', 'monthly_trading_value'], ascending=[True, False])
    monthly_df['liquidity_rank'] = monthly_df.groupby('yyyymm')['monthly_trading_value'].rank(
        method='first', ascending=False
    )
    monthly_df = monthly_df[monthly_df['liquidity_rank'] <= snapshot_size].copy()
    monthly_df['liquidity_rank'] = monthly_df['liquidity_rank'].astype(int)
    monthly_df = monthly_df.sort_values(['yyyymm', 'liquidity_rank'])

    # 기존 테이블 삭제 후 재생성
    conn.execute("DROP TABLE IF EXISTS universe_snapshots")
    conn.commit()
    insert_df_to_db(DB_NAME, 'universe_snapshots', monthly_df)

    # 결과 통계
    codes_per_month = monthly_df.groupby('yyyymm')['code'].count()
    months = monthly_df['yyyymm'].nunique()
    logger.info(
        f"universe_snapshots 재생성 완료: {months}개월, "
        f"월당 종목 수 min={codes_per_month.min()} / avg={codes_per_month.mean():.1f} / max={codes_per_month.max()} "
        f"(목표 상위 {snapshot_size}개)"
    )

    # 샘플 출력 — 첫 월과 마지막 월
    sample_months = sorted(monthly_df['yyyymm'].unique())
    for sample_yyyymm in [sample_months[0], sample_months[-1]]:
        sample = monthly_df[monthly_df['yyyymm'] == sample_yyyymm][['code', 'code_name', 'liquidity_rank']].head(10)
        logger.info(f"[{sample_yyyymm}] TOP10:\n{sample.to_string(index=False)}")

    return monthly_df


def main():
    logger.info("=" * 60)
    logger.info("월별 유니버스 스냅샷 재생성 시작")
    logger.info(f"DB: {DB_PATH}")
    logger.info("=" * 60)

    if not os.path.exists(DB_PATH):
        logger.error(f"DB 파일을 찾을 수 없습니다: {DB_PATH}")
        logger.error("먼저 fetch_historical_data.py를 실행하여 가격 데이터를 수집하세요.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    try:
        logger.info("1/4: universe 테이블 로드...")
        universe = load_universe(conn)
        logger.info(f"유니버스 종목 수: {len(universe)}")

        logger.info("2/4: 종목별 가격 데이터에서 월별 거래대금 계산...")
        price_data = load_price_tables(conn, universe)

        logger.info("3/4: universe_availability 재생성...")
        rebuild_availability(conn, universe, price_data)

        logger.info("4/4: universe_snapshots 재생성 (실전 전략 동일 기준)...")
        rebuild_snapshots(conn, universe, price_data)

        logger.info("=" * 60)
        logger.info("재생성 완료!")
        logger.info("이제 --walk-forward 옵션으로 백테스트를 재실행하세요:")
        logger.info("  poetry run python backtest/run_backtest.py --walk-forward")
        logger.info("=" * 60)

    finally:
        conn.close()


if __name__ == '__main__':
    main()
