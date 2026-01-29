import requests
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


def universe_cache_exists(db_dir=None, max_age_days=None, strategy_name=None):
    """
    all_stocks_kiwoom.parquet 캐시 또는 전략 DB의 `universe` 테이블 존재/신선도 확인.

    Returns: (exists_bool, days_old_or_None, modified_datetime_or_None)
    - parquet 파일이 있으면 수정시간으로 days_old 계산.
    - parquet가 없고 strategy_name이 주어지면 DB의 `universe` 테이블 존재 여부를 확인.
    - max_age_days가 주어지면 exists_bool은 days_old < max_age_days 조건을 사용해 판단.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from util.time_helper import get_korea_time
    from util.db_helper import check_table_exist

    db_dir = db_dir or DB_DIR
    cache_file = os.path.join(db_dir, 'all_stocks_kiwoom.parquet')

    if os.path.exists(cache_file):
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(cache_file), tz=ZoneInfo("Asia/Seoul"))
            try:
                days_old = (get_korea_time().date() - mtime.date()).days
            except Exception:
                days_old = None
            if max_age_days is None:
                return True, days_old, mtime
            return (days_old is not None and days_old < int(max_age_days)), days_old, mtime
        except Exception:
            return True, None, None

    # parquet 파일이 없으면 DB 테이블 존재 여부로 판단 (전략 DB가 주어진 경우)
    if strategy_name:
        try:
            table_exists = check_table_exist(strategy_name, 'universe')
            if table_exists:
                return True, None, None
        except Exception:
            pass

    return False, None, None

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


def is_market_hours():
    """
    장시간인지 확인하는 함수 (크롤링 가능 시간 체크용)
    평일 09:00 ~ 15:30 사이를 장시간으로 판단 (휴장일 제외)
    
    주의: 15:30까지 포함하는 이유는 동시호가 시간대에도 크롤링 가능하기 때문
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


def execute_crawler(output_file='all_stocks_naver.parquet'):
    # KOSPI, KOSDAQ 종목을 하나로 합치는데 사용할 변수
    df_total = []

    # CODES에 담긴 KOSPI, KOSDAQ 종목 모두를 크롤링하기 위해 for문을 사용
    for code in CODES:

        # 전체 페이지 개수를 가져오기 위한 코드 (마켓별로 `code` 사용)
        # lazy import BeautifulSoup to avoid requiring bs4 unless crawling
        from bs4 import BeautifulSoup
        res = requests.get(BASE_URL + str(code))
        page_soup = BeautifulSoup(res.text, 'lxml')

        # '맨뒤'에 해당하는 태그를 기준으로 전체 페이지 개수 추출하기
        total_page_elem = page_soup.select_one('td.pgRR > a')
        if total_page_elem is None:
            logger.warning(f"전체 페이지 정보를 찾을 수 없어 market={code}을(를) 1페이지만 처리합니다.")
            total_page_num = 1
        else:
            try:
                total_page_num = int(total_page_elem.get('href').split('=')[-1])
            except Exception as e:
                logger.warning(f"전체 페이지 수 파싱 실패 (href={total_page_elem.get('href')}): {e}. 1로 처리합니다.")
                total_page_num = 1

        # 조회할 수 있는 항목정보들 추출
        ipt_html = page_soup.select_one('div.subcnt_sise_item_top')

        # 페이지에서 조회할 항목정보들 추출 (로컬 변수로 관리)
        if ipt_html is None:
            logger.warning(f"항목 정보(div.subcnt_sise_item_top)를 찾을 수 없습니다. 기본 필드로 폴백합니다. (market={code})")
            fields = DEFAULT_FIELD_IDS
        else:
            fields = [item.get('value') for item in ipt_html.select('input')]

        # page마다 존재하는 모든 종목들의 항목정보를 크롤링해서 result에 저장
        result = []
        for page in range(1, total_page_num + 1):
            try:
                page_df = crawler(code, str(page), fields)
                if page_df is not None and not page_df.empty:
                    result.append(page_df)
            except Exception as e:
                logger.warning(f"페이지 크롤링 실패 (market={code}, page={page}): {e}")

        # 전체 페이지를 저장한 result를 하나의 데이터프레임으로 만듬
        if result:
            df = pd.concat(result, axis=0, ignore_index=True)
        else:
            df = pd.DataFrame()
        
        # 시장구분 컬럼 추가 (0=코스피, 1=코스닥)
        df['시장구분'] = '코스피' if code == 0 else '코스닥'

        # 변수 df는 KOSPI, KOSDAQ별로 크롤링한 종목 정보이고 이를 하나로 합치기 위해 df_total에 추가
        df_total.append(df)

    # df_total를 하나의 데이터프레임으로 만듬
    df_total = pd.concat(df_total)

    # 합친 데이터프레임의 index 번호를 새로 매김
    df_total.reset_index(inplace=True, drop=True)

    # 전체 크롤링 결과를 Parquet로 저장
    out_path = output_file if os.path.isabs(output_file) else os.path.join(DB_DIR, output_file)
    try:
        df_total.to_parquet(out_path, index=True)
        try:
            logger.info(f"크롤링 결과 저장: {Path(out_path).resolve()}")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"크롤링 결과 Parquet 저장 실패: {e}")

    # 크롤링 결과를 반환
    return df_total


def crawler(code, page, fields):
    """Parse a single page using explicit `fields` (stateless).

    `fields` is now required to make the function pure and deterministic.
    """

    # Naver finance에 전달할 값들 세팅(요청을 보낼 때는 menu, fieldIds, returnUrl을 지정해서 보내야 함)
    data = {'menu': 'market_sum',
            'fieldIds': fields,
            'returnUrl': BASE_URL + str(code) + "&page=" + str(page)}

    # lazy import BeautifulSoup only when crawler runs
    from bs4 import BeautifulSoup
    # 네이버로 요청을 전달(post방식)
    res = requests.post('https://finance.naver.com/sise/field_submit.nhn', data=data)

    page_soup = BeautifulSoup(res.text, 'lxml')

    # 크롤링할 table의 html 가져오는 코드(크롤링 대상 요소의 클래스는 브라우저에서 확인)
    table_html = page_soup.select_one('div.box_type_l')

    # column명을 가공
    header_data = [item.get_text().strip() for item in table_html.select('thead th')][1:-1]

    # 종목코드 추출 (a.title 태그의 href에서 추출)
    code_data = []
    for item in table_html.select('a.tltle'):
        href = item.get('href', '')
        if 'code=' in href:
            code = href.split('code=')[1].split('&')[0]
            code_data.append(code)
        else:
            code_data.append('')

    # 종목명 + 수치 추출 (a.title = 종목명, td.number = 기타 수치)
    inner_data = [item.get_text().strip() for item in table_html.find_all(lambda x:
                                                                          (x.name == 'a' and
                                                                           'tltle' in x.get('class', [])) or
                                                                          (x.name == 'td' and
                                                                           'number' in x.get('class', []))
                                                                          )]

    # page마다 있는 종목의 순번 가져오기
    no_data = [item.get_text().strip() for item in table_html.select('td.no')]
    number_data = np.array(inner_data)

    # 가로 x 세로 크기에 맞게 행렬화
    number_data.resize(len(no_data), len(header_data))

    # 한 페이지에서 얻은 정보를 모아 DataFrame로 만들어 반환
    df = pd.DataFrame(data=number_data, columns=header_data)
    
    # 종목코드 컬럼 추가
    if len(code_data) == len(df):
        df.insert(0, '종목코드', code_data)
    
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
    2. 장 중 → 네이버 크롤링 시도 (빠름) → 실패 시 캐시 사용
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
                logger.info("네이버 크롤링으로 fallback합니다...")
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
            # 아래 네이버 크롤링 로직으로 계속 진행
    all_stock_cache_file = 'all_stocks_naver.parquet'
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
            print(f"오늘 생성된 {all_stock_cache_path} 파일을 사용합니다.")
            try:
                df = pd.read_parquet(all_stock_cache_path)
                # 읽어온 Parquet에 필수 컬럼이 없는 경우 NaN 컬럼으로 보완
                required_cols = ['거래량', '거래대금', '시가총액', '등락률', '외국인비율', '종목명', '종목코드', '시장구분']
                for rc in required_cols:
                    if rc not in df.columns:
                        df[rc] = np.nan
            except Exception as e:
                logger.error(f"Parquet 파일 읽기 실패: {e}. 크롤링을 시도합니다.")
                file_is_today = False  # 파일 읽기 실패하면 크롤링 시도
    
    # 크롤링 스킵: 평일 08:00-09:00에는 네이버 크롤링 데이터가 신뢰 불가
    from util.time_helper import is_market_closed_day
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    if not use_kiwoom_api and (not is_market_closed_day()):
        if datetime_time(8, 0) <= now_kst.time() < datetime_time(9, 0):
            logger.info("평일 08:00-09:00: 네이버 크롤링을 스킵합니다. 캐시 사용을 시도합니다.")
            cached_df = _try_load_cache()
            if cached_df is not None:
                logger.info(f"✅ 캐시 파일 사용: {len(cached_df)}개 종목 (크롤링 스킵)")
                df = cached_df
                file_is_today = True
            else:
                raise Exception("평일 08:00-09:00 이므로 크롤링을 스킵합니다. 사용 가능한 캐시가 없습니다.")

    # 오늘 파일이 없거나 읽기 실패 시 크롤링 시도
    if not file_is_today:
        logger.info(f"크롤링을 실행합니다. (파일 존재: {os.path.exists(all_stock_cache_path)}, 오늘 파일: {file_is_today}, path={Path(all_stock_cache_path).resolve()})")
        print(f"크롤링을 실행합니다...")
        
        try:
            df = execute_crawler(all_stock_cache_file)
        except Exception as e:
            # 캐시 파일 사용 (네이버 + 키움 API 캐시 모두 시도)
            logger.warning(f"크롤링 실패: {e}. 캐시 파일을 확인합니다...")
            cached_df = _try_load_cache()
            if cached_df is not None:
                logger.info(f"✅ 캐시 파일 사용: {len(cached_df)}개 종목")
                df = cached_df
            else:
                logger.error(f"크롤링 실패이고 사용 가능한 캐시도 없습니다.")
                raise Exception(f"크롤링 실패이고 사용 가능한 캐시도 없습니다: {e}")

    universe = _filter_and_create_universe(df)
    try:
        del df
        gc.collect()
    except Exception:
        pass
    return universe


def _try_load_cache():
    """
    캐시 파일을 로드하는 내부 함수 (우선순위: 키움 API 캐시 → 네이버 크롤링 캐시)
    
    Returns:
        DataFrame or None: 캐시 데이터 또는 None (실패 시)
    """
    cache_files = [
        'all_stocks_kiwoom.parquet',  # 키움 API 전체 종목 (우선)
        'all_stocks_naver.parquet'     # 네이버 크롤링 전체 종목
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
    네이버 크롤링과 키움 API 모두에서 공통으로 사용
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
    
    # 크롤링 시 저장된 시장구분 정보 활용 (종목코드 기반 추측보다 정확)
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
        (~df.종목명.str.contains("캐피탈", na=False)) &    # 캐피탈 제외 (모의투자 제한 많음)
        (~df.종목명.str.contains("CD금리", na=False)) &    # CD금리 제외
        (~df.종목명.str.contains("KOFR금리", na=False)) &    # KOFR금리 제외
        (~df.종목명.str.contains("머니마켓", na=False))    # 머니마켓 제외
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
    print('Start!')
    universe = get_universe()
    print(universe)
    print('End')