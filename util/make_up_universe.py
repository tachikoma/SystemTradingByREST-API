import numpy as np
import pandas as pd
from datetime import datetime, time as datetime_time
from zoneinfo import ZoneInfo
import os
import gc
from util.time_helper import check_transaction_closed
from util.logging_config import get_logger
from pathlib import Path


# Directory for cache/data files (Excel, universe outputs)
DB_DIR = os.getenv("DB_DIR", "./data")
Path(DB_DIR).mkdir(parents=True, exist_ok=True)

BASE_URL = 'https://finance.naver.com/sise/sise_market_sum.nhn?sosok='
CODES = [0, 1]  # KOSPI:0, KOSDAQ:1
START_PAGE = 1
now = datetime.now(ZoneInfo("Asia/Seoul"))
formattedDate = now.strftime("%Y%m%d")

# 모의투자 매매제한 종목 코드 리스트
MOCK_TRADE_BLACKLIST_CODES = [
    '023760',  # 한국캐피탈
    # 추가 제한 종목은 여기에 추가
]

logger = get_logger(__name__)

# 기본 필드 아이디(네이버 필드 id 목록이 변경되었을 때의 폴백)
# 실제 네이버 필드 id는 사이트 변경에 따라 달라질 수 있으므로 최소한의 주요 항목을 포함
DEFAULT_FIELD_IDS = ['open', 'high', 'low', 'market_sum', 'trd_amt', 'cur_prc']


def cache_daily_data(kiwoom_client):
    """
    매일 장 종료 후 키움 API로 당일 데이터를 수집하여 캐싱하는 함수
    (Universe 재구성과 별개로 데이터만 갱신)
    
    Args:
        kiwoom_client: Kiwoom API 클라이언트 인스턴스
    
    Note:
        - Universe 재구성 (30일 주기): 종목 리스트 변경
        - 데이터 캐싱 (매일): 기존 종목들의 최신 데이터만 갱신
    """
    logger.info("📊 키움 API로 당일 데이터 캐싱 시작...")
    
    try:
        # 캐시를 읽지 않고 새로 생성하되, 수집 후에는 저장 (use_cache=False, save_cache=True)
        df = fetch_all_stocks_from_kiwoom(kiwoom_client, use_cache=False, save_cache=True)
        logger.info(f"✅ 당일 데이터 캐싱 완료: {len(df)}개 종목")
        return df
    except Exception as e:
        logger.error(f"❌ 데이터 캐싱 실패: {e}")
        raise


def fetch_all_stocks_from_kiwoom(kiwoom_client, use_cache=True, save_cache=True, cache_file='all_stocks_kiwoom.parquet'):
    """
    키움 API를 활용하여 전체 종목 리스트를 수집하는 함수
    (유니버스 생성용 기초 데이터)
    
    Args:
        kiwoom_client: Kiwoom API 클라이언트 인스턴스
        use_cache: 캐시 읽기 여부 (기본값: True)
        save_cache: 캐시 저장 여부 (기본값: True)
        cache_file: 캐시 파일 경로 (기본값: 'all_stocks_kiwoom.parquet')
    
    Returns:
        DataFrame: 전체 종목 정보가 담긴 데이터프레임
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import time
    
    today_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    
    # 캐시 파일 확인 (30일 이내 파일 사용 가능)
    cache_path = cache_file if os.path.isabs(cache_file) else os.path.join(DB_DIR, cache_file)
    if use_cache and os.path.exists(cache_path):
        file_mod_time = datetime.fromtimestamp(os.path.getmtime(cache_path), tz=ZoneInfo("Asia/Seoul"))
        file_date_str = file_mod_time.strftime("%Y%m%d")
        days_old = (datetime.now(ZoneInfo("Asia/Seoul")).date() - file_mod_time.date()).days
        
        # 30일 이내 캐시 파일은 사용 가능
        if days_old < 30:
            logger.info(f"캐시 파일 사용: {Path(cache_path).resolve()} ({days_old}일 전 데이터, 30일 이내)")
            try:
                return pd.read_parquet(cache_path)
            except Exception as e:
                logger.warning(f"캐시 Parquet 읽기 실패: {e}. API로 새로 조회합니다.")
        else:
            logger.warning(f"캐시 파일이 너무 오래됨: {days_old}일 전. API로 새로 조회합니다.")
    
    logger.info("키움 API로 종목 정보를 수집합니다...")
    
    # 1단계: 전체 종목 리스트 가져오기 (ka10099)
    logger.info("1/2: 종목 리스트 조회 중 (ka10099)...")
    kospi_list = kiwoom_client.get_code_list_by_market("0")  # 코스피
    kosdaq_list = kiwoom_client.get_code_list_by_market("10")  # 코스닥
    
    all_stocks = []
    for stock in kospi_list:
        all_stocks.append({**stock, 'market': '코스피'})
    for stock in kosdaq_list:
        all_stocks.append({**stock, 'market': '코스닥'})
    
    logger.info(f"총 {len(all_stocks)}개 종목 발견 (코스피: {len(kospi_list)}, 코스닥: {len(kosdaq_list)})")
    
    # 2단계: 각 종목의 상세 정보 가져오기 (ka10001)
    logger.info("2/2: 종목별 상세 정보 조회 중 (ka10001)... (시간이 소요될 수 있습니다)")
    
    # Rate limit 설정 (환경변수에서 읽기, 없으면 기본값 사용)
    # 모의투자는 rate limit이 더 엄격 (0.2초), 실전투자는 0.1초
    sleep_interval = float(
        os.getenv(
            'KIWOOM_API_SLEEP_MOCK' if kiwoom_client.mock else 'KIWOOM_API_SLEEP_REAL',
            '0.2' if kiwoom_client.mock else '0.1'
        )
    )
    logger.info(f"API 호출 간격: {sleep_interval}초 ({'모의투자' if kiwoom_client.mock else '실전투자'} 모드)")
    
    stock_data = []
    failed_count = 0
    
    for idx, stock in enumerate(all_stocks, 1):
        if idx % 100 == 0:
            logger.info(f"진행 상황: {idx}/{len(all_stocks)}...")
        
        info = kiwoom_client.get_stock_info(stock['code'])
        
        if info:
            try:
                # 키움 API: 시가총액은 억원 단위, 거래대금은 백만원 단위
                stock_data.append({
                    '종목코드': stock['code'],
                    '종목명': info.get('name', stock['name']),
                    '시장구분': stock['market'],
                    '현재가': int(info.get('cur_prc', 0)),
                    '거래량': int(info.get('trde_qty', 0)),
                    '거래대금': int(info.get('trde_amt', 0)),  # 백만원 단위 (그대로 사용)
                    '시가총액': int(info.get('mrkt_cap', 0)) * 100,  # 억원 → 백만원 (×100)
                    '등락률': float(info.get('flu_rt', 0)),
                    '외국인비율': float(info.get('for_exh_rt', 0)),
                    '상장주식수': int(info.get('list_cnt', 0)),
                })
            except (ValueError, TypeError) as e:
                logger.warning(f"종목 {stock['code']} 데이터 파싱 실패: {e}")
                failed_count += 1
        else:
            failed_count += 1
        
        # Rate limit 방지
        time.sleep(sleep_interval)
    
    logger.info(f"데이터 수집 완료: {len(stock_data)}개 성공, {failed_count}개 실패")
    
    # DataFrame 생성
    df = pd.DataFrame(stock_data)
    # --- master_list DB에 코드/종목명 저장(캐시 초기화용) ---
    try:
        from util.db_helper import upsert_stock_name
        for row in stock_data:
            try:
                code = str(row.get('종목코드') or row.get('종목_code') or row.get('code'))
                name = str(row.get('종목명') or row.get('종목명', None) or row.get('종목_name') or row.get('name') or '')
                if code and name:
                    upsert_stock_name('master_list', code, name)
            except Exception:
                continue
    except Exception:
        # DB 연동 실패 시에도 전체 기능은 유지되도록 무시
        logger.debug("master_list 업서트 스텝 건너뜀 (DB 오류)")
    
    # 캐시 저장
    if save_cache:
        try:
            # Prefer Parquet for compactness and speed
            df.to_parquet(cache_path, index=True)
            logger.info(f"캐시 파일 저장: {Path(cache_path).resolve()}")
        except Exception as e:
            logger.error(f"캐시 Parquet 저장 실패: {e}")
    
    return df


def get_stock_data_fdr_pykrx(output_file='all_stocks_pykrx.parquet'):
    """
    pykrx 전용으로 전 종목(ALL) 가격/종목명/시장구분/외국인비율을 수집하고
    KOSPI/KOSDAQ만 필터하여 Parquet로 저장합니다.
    """
    try:
        from pykrx import stock as krx_stock
    except Exception as e:
        # pykrx may depend on setuptools/pkg_resources; give actionable guidance
        msg = str(e)
        if isinstance(e, ModuleNotFoundError) and 'pkg_resources' in msg:
            raise ImportError(
                "pykrx requires setuptools (pkg_resources).\n"
                "Install with: python3 -m pip install --user setuptools pykrx"
            ) from e
        raise ImportError("pykrx is required: python3 -m pip install pykrx") from e

    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    logger.info(f"1. {today} 기준 전 종목 시세 수집 중 (ALL)...")

    # 1) 시세 가져오기 (pykrx API 변동/휴장일에 대비한 예외 처리)
    try:
        df_price = krx_stock.get_market_ohlcv_by_ticker(today, market="ALL")
    except Exception as e:
        logger.warning(f"get_market_ohlcv_by_ticker 실패: {e}; 대체 호출 시도(get_market_ohlcv_by_date 또는 범위 지정).")
        try:
            # 일부 pykrx 버전 또는 환경에서는 날짜 범위 호출이 필요할 수 있음
            # 함수 시그니처 차이 때문에 위치 인수로 ticker/market을 전달하여 재시도
            try:
                df_price = krx_stock.get_market_ohlcv_by_date(today, today, "ALL")
            except TypeError:
                # 어떤 버전은 ticker 키워드를 요구할 수 있으므로 이름 없이 두 번째 위치 인수로 재시도
                df_price = krx_stock.get_market_ohlcv_by_date(today, today)
        except Exception as e2:
            logger.error(f"대체 시세 호출도 실패: {e2}")
            raise

    # DataFrame 구조가 다양할 수 있으므로 안전하게 리셋 및 컬럼명 정리
    try:
        df_price = df_price.reset_index()
    except Exception:
        # 이미 리셋된 경우 그대로 사용
        pass

    # 컬럼명 통일: '티커' -> '종목코드', '종가' -> '현재가'
    rename_map = {}
    if '티커' in df_price.columns:
        rename_map['티커'] = '종목코드'
    if '종가' in df_price.columns:
        rename_map['종가'] = '현재가'
    if rename_map:
        df_price = df_price.rename(columns=rename_map)

    # 인덱스에 티커가 들어있는 경우 대비: reset_index 후 첫 컬럼을 종목코드로 사용
    if '종목코드' not in df_price.columns:
        try:
            df_price = df_price.reset_index()
        except Exception:
            pass
        # 첫 컬럼명이 이미 가격 컬럼들과 겹치지 않으면 이를 종목코드로 간주
        first_col = df_price.columns[0]
        if first_col not in ['현재가', '거래량', '거래대금', '등락률'] and '종목코드' not in df_price.columns:
            df_price = df_price.rename(columns={first_col: '종목코드'})

    price_cols = ['종목코드', '현재가', '거래량', '거래대금', '등락률']
    df_price = df_price[[c for c in price_cols if c in df_price.columns]]

    # 일부 환경에서는 ALL 호출이 비어있을 수 있으므로, 시장별로 분리 호출하여 병합 시도
    if df_price is None or (hasattr(df_price, 'empty') and df_price.empty):
        logger.warning("df_price가 비어있음 — KOSPI/KOSDAQ 개별 호출로 재시도합니다.")
        parts = []
        for mkt in ("KOSPI", "KOSDAQ"):
            try:
                part = krx_stock.get_market_ohlcv_by_ticker(today, market=mkt)
                try:
                    part = part.reset_index()
                except Exception:
                    pass
                if '티커' in part.columns:
                    part = part.rename(columns={'티커': '종목코드', '종가': '현재가'})
                # ensure first-col -> 종목코드 if needed
                if '종목코드' not in part.columns:
                    first_col = part.columns[0]
                    if first_col not in ['현재가', '거래량', '거래대금', '등락률']:
                        part = part.rename(columns={first_col: '종목코드'})
                parts.append(part[[c for c in price_cols if c in part.columns]])
            except Exception as e:
                logger.warning(f"{mkt} 개별 시세 호출 실패: {e}")

        if parts:
            try:
                df_price = pd.concat(parts, axis=0, ignore_index=True)
            except Exception as e:
                logger.error(f"시장별 concat 실패: {e}")


    # 2) 시장구분 매핑
    logger.info("2. 종목명 및 시장구분 매핑 중...")
    kospi_tickers = set(krx_stock.get_market_ticker_list(today, market="KOSPI"))
    kosdaq_tickers = set(krx_stock.get_market_ticker_list(today, market="KOSDAQ"))

    def identify_market(tkr):
        if tkr in kospi_tickers:
            return '코스피'
        if tkr in kosdaq_tickers:
            return '코스닥'
        return 'ETF/ETN'

    df_price['시장구분'] = df_price['종목코드'].apply(identify_market)

    # 3) 외국인 지분율
    logger.info("3. 외국인 지분율 수집 중...")
    try:
        df_for = krx_stock.get_exhaustion_rates_of_foreign_investment_by_ticker(today, "ALL").reset_index()
        if '지분율' in df_for.columns:
            df_for = df_for[['티커', '지분율']].rename(columns={'티커': '종목코드', '지분율': '외국인비율'})
        else:
            df_for = df_for.iloc[:, :2]
            df_for.columns = ['종목코드', '외국인비율']
    except Exception as e:
        logger.warning(f"외국인 지분율 수집 실패: {e}")
        df_for = pd.DataFrame(columns=['종목코드', '외국인비율'])

    # 4) 종목명 및 시가총액
    logger.info("4. 종목명 및 시가총액 수집 중...")
    try:
        df_cap = krx_stock.get_market_cap_by_ticker(today, market="ALL").reset_index()
        if '티커' in df_cap.columns:
            df_cap = df_cap.rename(columns={'티커': '종목코드'})
        if '종목명' not in df_cap.columns:
            for cand in ['종목명', 'Name']:
                if cand in df_cap.columns:
                    df_cap = df_cap.rename(columns={cand: '종목명'})
                    break
    except Exception as e:
        logger.warning(f"시가총액/종목명 수집 실패: {e}")
        df_cap = pd.DataFrame(columns=['종목코드', '종목명'])

    # df_cap이 비어있으면 마켓별로 재시도
    if df_cap is None or (hasattr(df_cap, 'empty') and df_cap.empty):
        logger.warning("df_cap이 비어있음 — KOSPI/KOSDAQ 개별 호출로 재시도합니다.")
        cap_parts = []
        for mkt in ("KOSPI", "KOSDAQ"):
            try:
                try:
                    cap_part = krx_stock.get_market_cap_by_ticker(today, mkt).reset_index()
                except TypeError:
                    # 일부 버전 시그니처 차이 대비: positional arg
                    cap_part = krx_stock.get_market_cap_by_ticker(today, mkt).reset_index()
                if '티커' in cap_part.columns:
                    cap_part = cap_part.rename(columns={'티커': '종목코드'})
                for cand in ['종목명', 'Name']:
                    if cand in cap_part.columns:
                        cap_part = cap_part.rename(columns={cand: '종목명'})
                        break
                cap_parts.append(cap_part[['종목코드', '종목명', '시가총액'] if '시가총액' in cap_part.columns else ['종목코드', '종목명']])
            except Exception as e:
                logger.warning(f"{mkt} 시가총액 호출 실패: {e}")
        if cap_parts:
            try:
                df_cap = pd.concat(cap_parts, axis=0, ignore_index=True)
            except Exception as e:
                logger.error(f"df_cap concat 실패: {e}")

    # 5) 병합
    logger.info("5. 데이터 최종 병합 및 타입 최적화...")
    df_final = pd.merge(df_cap, df_price, on='종목코드', how='inner')
    df_final = pd.merge(df_final, df_for, on='종목코드', how='left')

    df_final['외국인비율'] = pd.to_numeric(df_final.get('외국인비율'), errors='coerce').fillna(0.0).astype('float32')

    # 시가총액 억원 단위 정규화
    if '시가총액' in df_final.columns:
        mc_raw = pd.to_numeric(df_final.get('시가총액'), errors='coerce').fillna(0)
        median_mc = mc_raw.abs().median() if not mc_raw.empty else 0
        if median_mc > 100_000_000:
            logger.info(f"시가총액 단위 감지: median={median_mc:.0f} -> 원 단위로 판단, 억원으로 변환합니다.")
            df_final['시가총액'] = (mc_raw / 100_000_000).round().astype('int64')
        else:
            logger.info(f"시가총액 단위 감지: median={median_mc:.0f} -> 이미 억원 단위로 가정합니다.")
            df_final['시가총액'] = mc_raw.round().astype('int64')

    # KOSPI/KOSDAQ만 유지
    if '시장구분' in df_final.columns:
        before = len(df_final)
        df_final = df_final[df_final['시장구분'].isin(['코스피', '코스닥'])].reset_index(drop=True)
        logger.info(f"시장 필터(KOSPI/KOSDAQ) 적용: {before} -> {len(df_final)}")

    # 타입 최적화
    if '현재가' in df_final.columns:
        df_final['현재가'] = pd.to_numeric(df_final['현재가'], errors='coerce').fillna(0).astype('int32')
    if '등락률' in df_final.columns:
        df_final['등락률'] = pd.to_numeric(df_final['등락률'], errors='coerce').fillna(0).astype('float32')

    try:
        del df_price, df_for, df_cap
    except Exception:
        pass
    gc.collect()

    out_path = output_file if os.path.isabs(output_file) else os.path.join(DB_DIR, output_file)
    try:
        df_final.to_parquet(out_path, engine='fastparquet', compression='snappy')
    except Exception:
        df_final.to_parquet(out_path)

    logger.info(f"최종 수집 완료: {len(df_final)}개 종목 (KONEX 제외)")
    return df_final


def is_market_hours():
    """
    장시간인지 확인하는 함수 (종목 정보 가져오기 가능 시간 체크용)
    평일 09:00 ~ 15:30 사이를 장시간으로 판단 (휴장일 제외)
    
    주의: 15:30까지 포함하는 이유는 동시호가 시간대에도 종목 정보 가져오기 가능하기 때문
          실제 매매는 15:20까지만 가능 (check_transaction_open 참고)
    """
    from util.time_helper import is_market_closed_day
    
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    
    # 휴장일 체크 (주말 + 공휴일)
    if is_market_closed_day():
        return False
    
    # 장시작: 09:00, 장마감: 15:30 (공식 마감 시간)
    market_open = datetime_time(9, 0)
    market_close = datetime_time(15, 30)
    
    current_time = now.time()
    
    return market_open <= current_time <= market_close


def execute_crawler(output_file='all_stocks_pykrx.parquet'):
    # 기존 네이버 크롤러를 FDR+pykrx 통합 로직으로 대체합니다.
    # output_file 경로를 DB_DIR 기준으로 해석하고 get_stock_data_fdr_pykrx를 호출합니다.
    out_path = output_file if os.path.isabs(output_file) else os.path.join(DB_DIR, output_file)
    # get_stock_data_fdr_pykrx는 내부에서 parquet로도 저장합니다. 호출 후 반환된 df를 리턴합니다.
    df = get_stock_data_fdr_pykrx(output_file=os.path.basename(out_path))

    # pykrx가 빈 결과를 반환하는 경우 캐시로 안전하게 폴백합니다.
    if df is None or (hasattr(df, 'empty') and df.empty):
        logger.warning("get_stock_data_fdr_pykrx가 빈 결과를 반환했습니다. 캐시 파일로 폴백을 시도합니다.")
        cached = _try_load_cache()
        if cached is not None and not (hasattr(cached, 'empty') and cached.empty):
            logger.info(f"캐시 폴백 성공: {len(cached)}개 종목 반환")
            return cached
        else:
            raise Exception("FDR+pykrx가 빈 결과를 반환했고 사용 가능한 캐시도 없습니다.")

    return df


def get_universe(kiwoom_client=None, use_kiwoom_api=False):
    """
    유니버스를 생성하는 함수 (스마트 캐싱 전략)
    
    Args:
        kiwoom_client: Kiwoom API 클라이언트 (장 종료 후 자동으로 사용)
        use_kiwoom_api: 키움 API 강제 사용 (기본값: False, 자동 판단)
    
    Returns:
        list: 종목명 리스트
    
    동작 방식 (스마트 전략):
    1. 장 종료 후 (15:30 이후) → 키움 API로 당일 데이터 수집하여 캐싱
    2. 장 중 → FDR+pykrx 시도 (빠름) → 실패 시 캐시 사용
    3. 수동으로 use_kiwoom_api=True 지정 시 → 항상 API 사용
    """
    # 장 종료 후면 키움 API로 당일 데이터 갱신 (kiwoom_client가 있는 경우)
    if kiwoom_client and (use_kiwoom_api or check_transaction_closed()):
        mode = "강제 모드" if use_kiwoom_api else "장 종료 후 자동 갱신"
        logger.info(f"키움 API로 유니버스 생성을 시도합니다... ({mode})")
        try:
            df = fetch_all_stocks_from_kiwoom(kiwoom_client)
            logger.info(f"✅ 키움 API로 {len(df)}개 종목 정보 획득 및 캐싱 완료")
            universe = _filter_and_create_universe(df)
            try:
                del df
                gc.collect()
            except Exception:
                pass
            return universe
        except Exception as e:
            logger.error(f"키움 API 유니버스 생성 실패: {e}")
            if use_kiwoom_api:  # 강제 모드였다면 fallback
                logger.info("FDR+pykrx로 fallback합니다...")
            else:  # 장 종료 후 자동 모드였다면 캐시 우선 시도
                logger.info("캐시 파일 확인합니다...")
                cached_df = _try_load_cache()
                if cached_df is not None:
                    logger.info(f"✅ 캐시 파일 사용: {len(cached_df)}개 종목")
                    universe = _filter_and_create_universe(cached_df)
                    try:
                        del cached_df
                        gc.collect()
                    except Exception:
                        pass
                    return universe
            # 아래 FDR+pykrx 로직으로 계속 진행
    all_stock_cache_file = 'all_stocks_pykrx.parquet'
    today_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    all_stock_cache_path = all_stock_cache_file if os.path.isabs(all_stock_cache_file) else os.path.join(DB_DIR, all_stock_cache_file)
    
    # 오늘 날짜 파일이 있는지 확인
    file_is_today = False
    if os.path.exists(all_stock_cache_path):
        file_mod_time = datetime.fromtimestamp(os.path.getmtime(all_stock_cache_path), tz=ZoneInfo("Asia/Seoul"))
        file_date_str = file_mod_time.strftime("%Y%m%d")
        file_is_today = (file_date_str == today_str)
        if file_is_today:
            logger.info(f"오늘 생성된 {all_stock_cache_path} 파일을 사용합니다. (생성 시간: {file_mod_time.strftime('%H:%M:%S')})")
            try:
                df = pd.read_parquet(all_stock_cache_path)
                # 읽어온 Parquet에 필수 컬럼이 없는 경우 NaN 컬럼으로 보완
                required_cols = ['거래량', '거래대금', '시가총액', '등락률', '외국인비율', '종목명', '종목코드', '시장구분']
                for rc in required_cols:
                    if rc not in df.columns:
                        df[rc] = np.nan
            except Exception as e:
                logger.error(f"Parquet 파일 읽기 실패: {e}. FDR+pykrx를 시도합니다.")
                file_is_today = False  # 파일 읽기 실패하면 FDR+pykrx 시도
    
    # FDR+pykrx 스킵: 평일 08:00-09:00에는 FDR+pykrx 데이터가 신뢰 불가
    from util.time_helper import is_market_closed_day
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    if not use_kiwoom_api and (not is_market_closed_day()):
        if datetime_time(8, 0) <= now_kst.time() < datetime_time(9, 0):
            logger.info("평일 08:00-09:00: FDR+pykrx를 스킵합니다. 캐시 사용을 시도합니다.")
            cached_df = _try_load_cache()
            if cached_df is not None:
                logger.info(f"✅ 캐시 파일 사용: {len(cached_df)}개 종목 (FDR+pykrx 스킵)")
                df = cached_df
                file_is_today = True
            else:
                raise Exception("평일 08:00-09:00 이므로 FDR+pykrx를 스킵합니다. 사용 가능한 캐시가 없습니다.")

    # 오늘 파일이 없거나 읽기 실패 시 FDR+pykrx 시도
    if not file_is_today:
        logger.info(f"FDR+pykrx를 실행합니다. (파일 존재: {os.path.exists(all_stock_cache_path)}, 오늘 파일: {file_is_today}, path={Path(all_stock_cache_path).resolve()})")
        
        try:
            df = execute_crawler(all_stock_cache_file)
        except Exception as e:
            # 캐시 파일 사용 (FDR + 키움 API 캐시 모두 시도)
            logger.warning(f"FDR+pykrx 실패: {e}. 캐시 파일을 확인합니다...")
            cached_df = _try_load_cache()
            if cached_df is not None:
                logger.info(f"✅ 캐시 파일 사용: {len(cached_df)}개 종목")
                df = cached_df
            else:
                logger.error(f"FDR+pykrx 실패이고 사용 가능한 캐시도 없습니다.")
                raise Exception(f"FDR+pykrx 실패이고 사용 가능한 캐시도 없습니다: {e}")

    universe = _filter_and_create_universe(df)
    try:
        del df
        gc.collect()
    except Exception:
        pass
    return universe


def _try_load_cache():
    """
    캐시 파일을 로드하는 내부 함수 (우선순위: 키움 API 캐시 → FDR+pykrx 캐시)
    
    Returns:
        DataFrame or None: 캐시 데이터 또는 None (실패 시)
    """
    cache_files = [
        'all_stocks_kiwoom.parquet',  # 키움 API 전체 종목 (우선)
        'all_stocks_pykrx.parquet'     # pykrx 전체 종목
    ]

    for cache_file in cache_files:
        cache_path = cache_file if os.path.isabs(cache_file) else os.path.join(DB_DIR, cache_file)
        if os.path.exists(cache_path):
            try:
                try:
                    df = pd.read_parquet(cache_path)
                except Exception as e:
                    logger.warning(f"Parquet 읽기 실패: {e}")
                    raise
                # 파일 수정시간 조회는 실패할 수 있으므로 개별로 처리
                try:
                    file_mod_time = datetime.fromtimestamp(
                        os.path.getmtime(cache_path), 
                        tz=ZoneInfo("Asia/Seoul")
                    )
                    logger.info(f"캐시 파일 발견: {cache_file} (생성: {file_mod_time.strftime('%Y-%m-%d %H:%M:%S')})")
                except Exception:
                    logger.info(f"캐시 파일 읽음: {cache_file} (수정시간 없음)")
                    logger.info(f"⚠️  캐시 파일 사용: {cache_file}")
                    # 보장: 필터링에서 기대하는 컬럼들이 없으면 NaN 컬럼을 추가하여 KeyError 방지
                    required_cols = ['거래량', '거래대금', '시가총액', '등락률', '외국인비율', '종목명', '종목코드', '시장구분']
                    for rc in required_cols:
                        if rc not in df.columns:
                            df[rc] = np.nan
                    return df
            except Exception as e:
                logger.warning(f"{cache_path} 읽기 실패: {e}")
                continue
    
    return None


def _filter_and_create_universe(df, kiwoom_client=None, max_codes=100):
    """
    DataFrame을 받아서 필터링하고 유니버스를 생성하는 내부 함수
    FDR+pykrx과 키움 API 모두에서 공통으로 사용
    """
    # 데이터 정제
    mapping = {',': '', 'N/A': '0', '%': ''}
    df.replace(mapping, regex=True, inplace=True)

    # 사용할 column들 설정 (RSI 전략에 최적화)
    cols = ['거래량', '거래대금', '시가총액', '등락률', '외국인비율']

    # column들을 숫자타입으로 변환(Naver Finance를 크롤링해온 데이터는 str 형태)
    for col in cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # NaN이 생긴 행 제거
    df = df.dropna(subset=cols)
    
    # 음수 등락률 절대값 처리 필요 (등락률은 이미 숫자)
    if len(df) == 0:
        logger.warning("필터링 후 데이터가 없습니다.")
        return []
    
    # 종목코드가 있는 경우 모의투자 제한 종목 제외
    if '종목코드' in df.columns:
        before_count = len(df)
        df = df[~df['종목코드'].isin(MOCK_TRADE_BLACKLIST_CODES)]
        removed = before_count - len(df)
        if removed > 0:
            logger.info(f"모의투자 제한 종목 {removed}개 제외")

    # ===== RSI(2) 전략에 최적화된 Universe 구성 =====
    # 1. 기본 필터링: 유동성 + 적절한 시가총액 범위
    # 거래대금/시가총액 단위: 백만원
    
    # 시장구분 정보 활용
    kosdaq_mask = df['시장구분'] == '코스닥'
    
    # 시장별 차등 시가총액 필터 (코스피: 500억, 코스닥: 200억)
    market_cap_filter = (
        (~kosdaq_mask & (df['시가총액'] > 50000)) |  # 코스피: 500억 이상
        (kosdaq_mask & (df['시가총액'] > 20000))      # 코스닥: 200억 이상
    )
    
    df = df[
        market_cap_filter &                        # 시장별 차등 시가총액 조건
        (df['거래대금'] > 3000) &                  # 30억 이상 (유동성 확보)
        (df['시가총액'] < 5000000) &               # 5조 미만 (대형 우량주 제외)
        (df['거래량'] > 0) &                       # 거래량 있는 종목
        (~df.종목명.str.contains("지주", na=False)) &    # 지주회사 제외
        (~df.종목명.str.contains("홀딩스", na=False)) &  # 홀딩스 제외
        (~df.종목명.str.contains("스팩", na=False)) &    # 스팩 제외
        (~df.종목명.str.contains("리츠", na=False)) &    # 리츠 제외
        (~df.종목명.str.contains("캐피탈", na=False))    # 캐피탈 제외 (모의투자 제한 많음)
    ]

    # 우선주 필터링: 종목명에 단순히 '우'가 포함된다고 제거하면
    # '우진', '우리금융지주' 등 일반 종목이 잘못 제외될 수 있음.
    # 보통주는 종목코드가 '0'으로 끝나는 경우가 대부분이므로
    # 종목코드가 있으면 코드 끝자리가 '0'인 종목만 남기고, 없으면 그대로 유지
    try:
        if '종목코드' in df.columns:
            df = df[df['종목코드'].astype(str).str.endswith('0')]
        else:
            pass  # 종목코드가 없으면 그대로 유지
    except Exception:
        # 필터 적용 중 예외가 발생하면 원래 데이터프레임을 유지
        logger.warning("우선주 필터 적용 중 오류 발생: 종목코드 기반 필터링을 건너뜁니다.")

    # 2. 변동성 지표 계산
    # - 등락률 절대값: 당일 변동성
    df['변동성_지표'] = abs(df['등락률'])
    # - 외국인비율: 유동성 대리변수 (사용 비율이 높을수록 안정적) <- 현재는 사용하지 않음
    
    # 3. 거래 활발도 계산 (거래대금 대비 시가총액 비율)
    df['거래회전율'] = df['거래대금'] / df['시가총액'] * 100
    
    # 4. 변동성 + 거래활발도 기준 종합 점수
    # 변동성 상위 50% + 거래회전율 상위 50% 종목 선호
    df['변동성_순위'] = df['변동성_지표'].rank(method='max', ascending=False)
    df['거래회전율_순위'] = df['거래회전율'].rank(method='max', ascending=False)
    df['종합_순위'] = (df['변동성_순위'] + df['거래회전율_순위']) / 2

    # 5. 종합 순위로 정렬
    df = df.sort_values(by=['종합_순위'])

    # 필터링한 데이터프레임의 index 번호를 새로 매김
    df = df.reset_index(drop=True)

    # 안전한 DataFrame 조작을 위해 복사본 사용
    df = df.copy()

    # 캐시에서 읽을 때 Parquet를 index_col=0으로 읽는 케이스를 지원
    # index에 종목코드가 들어있다면 이를 명시적 컬럼으로 복원
    if '종목코드' not in df.columns:
        df = df.reset_index()
        # reset_index로 생성된 첫 컬럼을 `종목코드`로 표준화
        first_col = df.columns[0]
        if first_col != '종목코드':
            df = df.rename(columns={first_col: '종목코드'})

    # 상위 100개만 추출
    df = df.loc[:99]
    
    # Universe 최소 개수 검증 (비정상 데이터 방지)
    MIN_UNIVERSE_SIZE = 10
    if len(df) < MIN_UNIVERSE_SIZE:
        error_msg = f"Universe 크기가 너무 작습니다 ({len(df)}개). 최소 {MIN_UNIVERSE_SIZE}개 필요."
        logger.error(error_msg)
        raise Exception(error_msg)

    # 유니버스 생성 결과를 Parquet 출력
    # 우선 df는 필터링 및 정렬을 마친 상위 100개(또는 지정된 수) 후보입니다.
    # 추가 조치: 현재 보유/주문 종목(kiwoom_client)을 병합하여 보유종목이 누락되지 않도록 함
    try:
        if kiwoom_client is not None:
            held_codes = set()
            order_codes = set()
            try:
                held_codes = set(getattr(kiwoom_client, 'balance', {}).keys())
            except Exception:
                held_codes = set()
            try:
                order_codes = set(getattr(kiwoom_client, 'order', {}).keys())
            except Exception:
                order_codes = set()

            # 코드 -> 종목명 맵을 빠르게 조회
            existing_codes = set(df['종목코드'].astype(str).tolist()) if '종목코드' in df.columns else set()

            # 보유/주문 중 df에 없는 종목을 df에 추가(간단한 레코드로 추가)
            missing_codes = (held_codes | order_codes) - existing_codes
            added_rows = []
            for code in missing_codes:
                # 모의투자 블랙리스트 처리는 호출측에서 하도록 함
                code_name = None
                # 우선 Kiwoom client의 balance에서 종목명 사용
                try:
                    code_name = kiwoom_client.balance.get(code, {}).get('종목명')
                except Exception:
                    code_name = None
                # 필요 시 API로 종목명 조회(안정성: 예외 처리)
                if not code_name:
                    try:
                        code_name = kiwoom_client.get_master_code_name(code) or f"{code}"
                    except Exception:
                        code_name = f"{code}"

                # 최소한의 행을 추가 (필요 컬럼에 NAs)
                new_row = {col: None for col in df.columns}
                if '종목코드' in df.columns:
                    new_row['종목코드'] = code
                # 종목명 컬럼이 존재하면 채움
                if '종목명' in df.columns:
                    new_row['종목명'] = code_name
                added_rows.append(new_row)

            if added_rows:
                # concat으로 인한 FutureWarning 회피: 행 단위로 안전하게 추가
                # 컬럼 타입에 맞는 기본값으로 채워 삽입 (all-NA 컬럼 생성 방지)
                col_kinds = {col: df[col].dtype.kind for col in df.columns}
                for new_row in added_rows:
                    row_values = []
                    for col in df.columns:
                        if col in new_row and new_row[col] is not None:
                            row_values.append(new_row[col])
                        else:
                            kind = col_kinds.get(col, 'O')
                            if kind in ('i', 'u', 'f', 'c'):  # numeric types
                                row_values.append(0)
                            elif kind == 'b':
                                row_values.append(False)
                            else:
                                row_values.append('')
                    df.loc[len(df)] = row_values

            # 이제 전체 후보에서 보유/주문을 우선 보존하되, max_codes를 초과하면
            # 보유/주문이 아닌 기존 후보 중 거래량이 작은 순으로 제거
            # 거래량 컬럼이름 다양성 고려
            vol_col = None
            for c in ['거래량', 'volume', '누적거래량']:
                if c in df.columns:
                    vol_col = c
                    break

            # mark held/order rows
            df['_is_held_or_order'] = df['종목코드'].astype(str).isin(held_codes | order_codes) if '종목코드' in df.columns else False

            # 만약 후보수가 초과하면 제거 수행
            if len(df) > max_codes:
                excess = len(df) - max_codes
                # 제거 후보: 보유/주문이 아닌 행
                # 제거 후보: 보유/주문이 아닌 행 (명시적 복사)
                removable_df = df.loc[~df['_is_held_or_order']].copy()
                if vol_col:
                    # NaN을 0으로 대체한 별도 열을 생성하여 원본을 건드리지 않음
                    vol_series = pd.to_numeric(removable_df[vol_col], errors='coerce').fillna(0)
                    removable_sorted = removable_df.assign(_vol_numeric=vol_series).sort_values(by='_vol_numeric', ascending=True)
                else:
                    removable_sorted = removable_df

                # 실제로 제거할 인덱스
                to_remove_idx = removable_sorted.index.tolist()[:excess]
                if len(to_remove_idx) < excess:
                    logger.warning("병합 후 슬롯 부족: 제거 후보 부족 (필요:%d, 가능:%d)", excess, len(to_remove_idx))

                # 제거
                if to_remove_idx:
                    df = df.drop(index=to_remove_idx).reset_index(drop=True)

            # 최종적으로 max_codes까지 자름(안전망)
            df = df.head(max_codes)

            # cleanup
            if '_is_held_or_order' in df.columns:
                df = df.drop(columns=['_is_held_or_order'])

    except Exception as e:
        logger.warning(f"보유/주문 병합 중 경고 발생: {e}")

    try:
        out_universe = os.path.join(DB_DIR, 'universe.parquet')
        try:
            df.to_parquet(out_universe, index=True)
            try:
                logger.info(f"Universe 저장: {Path(out_universe).resolve()}")
            except Exception:
                logger.info(f"Universe 저장: {out_universe}")
        except Exception as e:
            logger.error(f"Universe Parquet 저장 실패: {e}")
    except Exception as e:
        logger.warning(f"universe 저장 실패: {e}")

    # 임시로 생성된 대용량 컬럼들을 삭제하여 메모리 사용을 줄입니다.
    try:
        tmp_cols = ['변동성_지표', '거래회전율', '변동성_순위', '거래회전율_순위', '종합_순위', '_vol_numeric']
        for c in tmp_cols:
            if c in df.columns:
                try:
                    df.drop(columns=[c], inplace=True)
                except Exception:
                    pass
    except Exception:
        pass

    # (주의) 원본 DataFrame 레퍼런스 제거는 호출자에서 처리합니다.
    # 내부에서는 임시 컬럼만 제거하여 피크 메모리를 낮춥니다.

    universe_list = df['종목명'].tolist() if '종목명' in df.columns else df.iloc[:, 0].astype(str).tolist()
    logger.info(f"Universe 생성 완료: {len(universe_list)}개 종목 (병합 후)")
    return universe_list


if __name__ == "__main__":
    import sys
    import os

    # 권장 실행 방식 안내: 패키지 모드로 실행하는 것이 import 경로 문제를 방지합니다.
    if not (__package__):
        sys.stderr.write(
            "권장: 패키지 모드로 실행하세요 — `python -m util.make_up_universe`\n"
            "직접 실행 중입니다. 일부 상대/절대 import가 실패할 수 있습니다.\n"
        )
        # 자동 재실행 시도 (옵션: --no-reexec 또는 환경변수 SKIP_REEXEC로 건너뜀)
        if "--no-reexec" not in sys.argv and not os.getenv("SKIP_REEXEC"):
            sys.stderr.write("모듈 모드로 재실행합니다...\n")
            args = [sys.executable, "-m", "util.make_up_universe"] + sys.argv[1:]
            os.execv(sys.executable, args)
        else:
            sys.stderr.write("재실행 건너뜀 (--no-reexec 또는 SKIP_REEXEC 감지). 계속 진행합니다.\n")

    # 실제 동작 시작
    logger.info('Start!')
    universe = get_universe()
    print(universe)
    print('End')