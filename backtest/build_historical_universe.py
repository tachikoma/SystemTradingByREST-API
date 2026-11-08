"""
과거 시점별 전체 상장종목 리스트를 구축하는 모듈 (생존자 편향 제거)

FinanceDataReader의 KRX-DESC(현재상장) + KRX-DELISTING(상장폐지) 데이터를 병합하여
백테스트 기간(기본 2016~2026)에 실제로 존재했던 모든 종목을 수집합니다.
"""

import sys
from pathlib import Path
from datetime import datetime

import pandas as pd
import FinanceDataReader as fdr

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from util.logging_config import get_logger
from util.time_helper import get_korea_time

logger = get_logger('build_historical_universe')

# FDR KRX 캐시(GitHub)는 KRX 영업일 기준 1~2일 시차로 게시되어 당일 파일이 404일 수 있다.
# 예: 2026-09-09 장중에는 09-07 파일까지만 존재. 당일 404 시 최근 파일로 폴백한다.
FDR_KRX_CACHE_BASE = (
    'https://raw.githubusercontent.com/FinanceData'
    '/fdr_krx_data_cache/refs/heads/master/data'
)
CACHE_FALLBACK_DAYS = 10


def _read_fdr_cache_csv(subdir: str, dtype: dict, label: str) -> pd.DataFrame:
    """FDR KRX 캐시 CSV를 당일부터 과거로 최대 N일간 거슬러 읽는다."""
    from datetime import timedelta

    last_err = None
    for back in range(CACHE_FALLBACK_DAYS):
        day = (get_korea_time().date() - timedelta(days=back)).strftime('%Y-%m-%d')
        url = f'{FDR_KRX_CACHE_BASE}/{subdir}/{day}.csv'
        try:
            df = pd.read_csv(url, dtype=dtype)
            if back:
                logger.warning(
                    "%s 캐시 당일분 없음 → %d일 전(%s) 파일로 폴백", label, back, day
                )
            else:
                logger.info("%s 캐시 당일분(%s) 사용", label, day)
            return df
        except Exception as e:  # 404 등 → 이전 날짜 시도
            last_err = e
    raise RuntimeError(f"{label} 캐시 로드 실패 ({CACHE_FALLBACK_DAYS}일간): {last_err}")


def _listing_krx_desc() -> pd.DataFrame:
    """현재상장 목록. fdr 우선, 404 등 실패 시 캐시 직접 폴백 (FDR 파싱과 동일 규격)."""
    try:
        return fdr.StockListing('KRX-DESC')
    except Exception as e:
        logger.warning("fdr.StockListing('KRX-DESC') 실패(%s) → 캐시 직접 폴백", e)
    df = _read_fdr_cache_csv('listing/desc', {'Code': str}, 'KRX-DESC')
    df['ListingDate'] = pd.to_datetime(df['ListingDate'], errors='coerce')
    return df


def _listing_krx_marcap() -> pd.DataFrame:
    """시총/상장주식수 목록. fdr 우선, 실패 시 캐시 직접 폴백."""
    try:
        return fdr.StockListing('KRX')
    except Exception as e:
        logger.warning("fdr.StockListing('KRX') 실패(%s) → 캐시 직접 폴백", e)
    df = _read_fdr_cache_csv(
        'listing/krx',
        {'Code': str, 'Dept': str, 'ChangeCode': str, 'MarketId': str},
        'KRX',
    )
    df['Stocks'] = pd.to_numeric(
        df['Stocks'].astype(str).str.replace(',', ''), errors='coerce'
    )
    return df


def _listing_krx_delisting() -> pd.DataFrame:
    """상장폐지 목록. fdr 우선(단, FDR은 실패 시 빈 DF를 반환하므로 empty도 폴백),
    실패 시 캐시 직접 폴백 (FDR의 날짜 파싱/기간 필터와 동일 규격)."""
    try:
        df = fdr.StockListing('KRX-DELISTING')
        if len(df) > 0:
            return df
        logger.warning("fdr.StockListing('KRX-DELISTING') 빈 결과 → 캐시 직접 폴백")
    except Exception as e:
        logger.warning("fdr.StockListing('KRX-DELISTING') 실패(%s) → 캐시 직접 폴백", e)
    df = _read_fdr_cache_csv(
        'listing/delisting', {'Symbol': str, 'ToSymbol': str}, 'KRX-DELISTING'
    )
    df['ListingDate'] = pd.to_datetime(df['ListingDate'], format='%Y-%m-%d', errors='coerce')
    df['DelistingDate'] = pd.to_datetime(df['DelistingDate'], format='%Y-%m-%d', errors='coerce')
    df['ListingShares'] = pd.to_numeric(
        df['ListingShares'].astype(str).str.replace(',', ''), errors='coerce'
    )
    start = datetime(1960, 1, 1)
    end = get_korea_time().replace(tzinfo=None)
    df = df[(start <= df['DelistingDate']) & (df['DelistingDate'] <= end)]
    return df.reset_index(drop=True)

# 실전 전략과 동일한 종목명 제외 키워드
NAME_EXCLUDE_KEYWORDS = [
    "지주", "홀딩스", "스팩", "리츠", "캐피탈",
    "CD금리", "KOFR금리", "머니마켓",
]


def passes_basic_filter(code: str, name: str) -> bool:
    """기본 필터: 우선주 제외 + 이름 키워드 제외"""
    if not code.endswith('0'):
        return False
    for keyword in NAME_EXCLUDE_KEYWORDS:
        if keyword in name:
            return False
    return True


def build_complete_universe(
    start_date: str = '2016-01-01',
    end_date: str = '2026-06-03',
) -> pd.DataFrame:
    """전체 상장종목 리스트 구축 (현재상장 + 상장폐지)

    Returns:
        DataFrame with columns: code, name, market, listing_date, delisting_date (nullable), stocks
    """
    logger.info("=== 전체 상장종목 리스트 구축 시작 ===")
    logger.info(f"기간: {start_date} ~ {end_date}")

    # 1) 현재 상장 종목
    logger.info("KRX-DESC 로드 중...")
    current = _listing_krx_desc()
    current_df = pd.DataFrame({
        'code': current['Code'],
        'name': current['Name'],
        'market': current['Market'],
        'listing_date': pd.to_datetime(current['ListingDate'], errors='coerce'),
        'delisting_date': pd.NaT,
        'source': 'current',
    })
    logger.info(f"현재 상장 종목: {len(current_df)}개")

    # 2) 상장폐지 종목 중 6-digit 보통주만
    logger.info("KRX-DELISTING 로드 중...")
    delisted = _listing_krx_delisting()
    delisted_common = delisted[
        (delisted['Kind'] == '보통주') &
        (delisted['Symbol'].str.len() == 6)
    ].copy()
    delisted_df = pd.DataFrame({
        'code': delisted_common['Symbol'],
        'name': delisted_common['Name'],
        'market': delisted_common['Market'],
        'listing_date': pd.to_datetime(delisted_common['ListingDate'], errors='coerce'),
        'delisting_date': pd.to_datetime(delisted_common['DelistingDate'], errors='coerce'),
        'source': 'delisted',
    })
    logger.info(f"상장폐지 종목(6자리 보통주): {len(delisted_df)}개")

    # 3) 상장주식수(Stocks) 정보 수집
    #    - 현재상장: fdr.StockListing('KRX')의 Stocks 컬럼
    #    - 상장폐지: KRX-DELISTING의 ListingShares 컬럼
    logger.info("상장주식수 정보 수집 중...")
    krx_listing = _listing_krx_marcap()
    stocks_map = dict(zip(krx_listing['Code'].astype(str), krx_listing['Stocks']))

    delisted_raw = delisted  # 2)에서 로드한 원본 재사용 (재요청 방지)
    listing_shares_map = dict(
        zip(delisted_raw['Symbol'].astype(str), delisted_raw['ListingShares'])
    )

    # 4) 병합
    all_stocks = pd.concat([current_df, delisted_df], ignore_index=True)
    all_stocks = all_stocks.drop_duplicates(subset='code', keep='first')
    all_stocks['stocks'] = all_stocks['code'].map(
        lambda c: stocks_map.get(c) or listing_shares_map.get(c) or 0
    ).fillna(0).astype(int)
    logger.info(f"병합 후 전체 고유 종목: {len(all_stocks)}개 (Stocks 정보 보유: {(all_stocks['stocks'] > 0).sum()})")

    # 5) 백테스트 기간에 살아있었던 종목만 필터
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)

    mask = (
        (all_stocks['listing_date'] <= end_dt) &
        (
            all_stocks['delisting_date'].isna() |
            (all_stocks['delisting_date'] >= start_dt)
        )
    )
    all_stocks = all_stocks[mask].copy()
    logger.info(f"기간 내 존재했던 종목: {len(all_stocks)}개")

    # 6) 기본 필터 (우선주 제외 + 이름 키워드 제외)
    mask_basic = all_stocks.apply(
        lambda r: passes_basic_filter(r['code'], r['name']), axis=1
    )
    all_stocks = all_stocks[mask_basic].copy()
    logger.info(f"기본 필터 통과: {len(all_stocks)}개")

    return all_stocks


if __name__ == '__main__':
    df = build_complete_universe()
    print(f"\n=== 요약 ===")
    print(f"총 종목: {len(df)}")
    print(f"KOSPI: {len(df[df['market'] == 'KOSPI'])}")
    print(f"KOSDAQ: {len(df[df['market'] == 'KOSDAQ'])}")
    print(f"KONEX: {len(df[df['market'] == 'KONEX'])}")
    print(f"상장폐지 출신: {len(df[df['source'] == 'delisted'])}")
    print(f"현재상장 출신: {len(df[df['source'] == 'current'])}")
