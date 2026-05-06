"""
백테스트용 국내 주식 12년치 과거 데이터 수집 프로그램

키움 REST API를 이용하여 주식 12년치 일봉 데이터를 수집하고
backtest_data.db 파일로 저장합니다.

API 한번에 최대 600개(약 3년치) 데이터를 받아올 수 있으므로
12년치를 위해 최대 5번 연속 조회를 수행합니다.
"""

import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
from datetime import datetime, timedelta

from api.Kiwoom import Kiwoom
from util.db_helper import check_table_exist, insert_df_to_db, execute_sql
from util.make_up_universe import get_universe
from util.logging_config import configure_logging, get_logger


# 로거 설정
logger = get_logger('fetch_historical_data')

# 상수 정의
DB_NAME = "backtest_data"  # DB 파일명 (backtest_data.db로 저장됨)
MAX_DATA_PER_CALL = 600  # API 한번에 가져올 수 있는 최대 데이터 개수 (약 3년치)
TARGET_YEARS = 12  # 목표 수집 기간 (년)
TARGET_DAYS = TARGET_YEARS * 365  # 목표 수집 기간 (일) - 약 4380일
MAX_LOOPS = 5  # 최대 API 호출 횟수 (5번 * 600개 = 3000개, 약 12년치)


class HistoricalDataFetcher:
    """과거 데이터 수집 클래스"""
    
    def __init__(self, kiwoom: Kiwoom, db_name: str = DB_NAME):
        """
        Args:
            kiwoom: Kiwoom API 인스턴스
            db_name: DB 파일명 (확장자 제외)
        """
        self.kiwoom = kiwoom
        self.db_name = db_name
        self.universe = {}
        
    def setup_universe(self):
        """유니버스 설정 - 기존 자동매매 프로그램의 종목 리스트 사용"""
        logger.info("유니버스 설정 시작...")
        
        # 유니버스 리스트 가져오기
        universe_list = get_universe()
        logger.info(f"유니버스 종목 수: {len(universe_list)}")
        logger.debug(f"유니버스 종목명 샘플 (처음 10개): {universe_list[:10]}")
        
        # KOSPI와 KOSDAQ 종목 코드 가져오기
        kospi_code_list = self.kiwoom.get_code_list_by_market("0")
        kosdaq_code_list = self.kiwoom.get_code_list_by_market("10")
        all_code_list = kospi_code_list + kosdaq_code_list
        
        logger.info(f"전체 시장 종목 수: {len(all_code_list)}")
        logger.debug(f"시장 종목명 샘플 (처음 10개): {[c['name'] for c in all_code_list[:10]]}")
        
        # 유니버스에 해당하는 종목만 필터링
        unmatched = []
        for code_dict in all_code_list:
            code_name = code_dict["name"]
            if code_name in universe_list:
                self.universe[code_dict["code"]] = code_name
            
        # 매칭되지 않은 유니버스 종목 찾기
        matched_names = set(self.universe.values())
        unmatched = [name for name in universe_list if name not in matched_names]
        
        logger.info(f"매칭된 유니버스 종목 수: {len(self.universe)}")
        if unmatched:
            logger.warning(f"매칭되지 않은 종목 수: {len(unmatched)}")
            logger.warning(f"매칭되지 않은 종목 샘플 (처음 10개): {unmatched[:10]}")
        
        # 유니버스를 DB에 저장 (기존 테이블이 있어도 덮어쓰기)
        now = datetime.now().strftime("%Y%m%d")
        universe_df = pd.DataFrame({
            'code': self.universe.keys(),
            'code_name': self.universe.values(),
            'created_at': [now] * len(self.universe.keys())
        })
        insert_df_to_db(self.db_name, 'universe', universe_df)
        logger.info("유니버스를 DB에 저장했습니다.")
            
        return self.universe

    # 실전 전략(_filter_and_create_universe)과 동일한 종목명 제외 키워드
    _NAME_EXCLUDE_KEYWORDS = [
        "지주", "홀딩스", "스팩", "리츠", "캐피탈",
        "CD금리", "KOFR금리", "머니마켓",
    ]

    # 최소 일평균 거래대금 기준 (원 단위): 실전 전략의 30억(=3,000백만원) 기준과 동일
    _MIN_DAILY_TRADING_VALUE = 3_000_000_000  # 30억 원

    def _passes_strategy_filter(self, code: str, code_name: str) -> bool:
        """실전 전략과 동일한 종목 필터 통과 여부 확인

        1. 종목코드 끝자리 '0' (우선주 제외)
        2. 종목명 제외 키워드 (지주/홀딩스/스팩/리츠/캐피탈 등)
        """
        # 우선주 제외: 코드 끝자리 '0'인 종목만 허용
        if not code.endswith('0'):
            return False
        # 종목명 제외 키워드
        for keyword in self._NAME_EXCLUDE_KEYWORDS:
            if keyword in code_name:
                return False
        return True

    def save_universe_snapshots(self, price_data_by_code: dict):
        """워크포워드용 유니버스 스냅샷을 DB에 저장 (실전 전략 동일 기준 적용)

        1) universe_availability: 종목별 데이터 가용 기간
        2) universe_snapshots: YYYYMM별 실제 유니버스 구성 종목

        실전 전략(_filter_and_create_universe)과 동일한 필터링 기준을 각 월에 소급 적용합니다:
        - 해당 월에 가격 데이터가 존재하는 종목만 후보
        - 우선주 제외 (종목코드 끝자리 '0')
        - 종목명 제외 키워드 필터 (지주/홀딩스/스팩/리츠/캐피탈/CD금리/KOFR금리/머니마켓)
        - 최소 일평균 거래대금 기준 (30억 = close * volume 월평균 > 3,000,000,000원)
        - 거래대금 기준 상위 N개 선택 (N = MONTHLY_UNIVERSE_SIZE 환경변수, 기본 100)

        Args:
            price_data_by_code: {
                code: {
                    'earliest': 'YYYYMM',
                    'latest': 'YYYYMM',
                    'monthly_liquidity': {'YYYYMM': float, ...}
                },
                ...
            }
        """
        logger.info("월별 유니버스 스냅샷 저장 시작 (실전 전략 동일 기준 적용)...")

        # 1) 종목별 데이터 가용 기간 저장
        availability_records = []
        for code, code_name in self.universe.items():
            if code not in price_data_by_code:
                continue
            availability_records.append({
                'code': code,
                'code_name': code_name,
                'earliest_yyyymm': price_data_by_code[code]['earliest'],
                'latest_yyyymm': price_data_by_code[code]['latest'],
            })

        if availability_records:
            availability_df = pd.DataFrame(availability_records)
            insert_df_to_db(self.db_name, 'universe_availability', availability_df)
            logger.info(
                f"종목별 데이터 가용 기간 저장 완료: {len(availability_records)}개 종목 → 'universe_availability' 테이블"
            )
        else:
            logger.warning("가용 기간으로 저장할 종목이 없습니다.")

        # 2) YYYYMM별 유니버스 구성 저장 — 실전 전략과 동일한 필터 소급 적용
        snapshot_size = int(os.getenv('MONTHLY_UNIVERSE_SIZE', '100'))

        # 2-a) 1차 필터: 우선주 제외 + 종목명 키워드 제외 (시간 불변 필터)
        strategy_filtered_universe = {
            code: code_name
            for code, code_name in self.universe.items()
            if code in price_data_by_code and self._passes_strategy_filter(code, code_name)
        }
        excluded_count = len(self.universe) - len(strategy_filtered_universe)
        logger.info(
            f"1차 필터(우선주+이름키워드) 통과: {len(strategy_filtered_universe)}개 / {len(self.universe)}개 "
            f"({excluded_count}개 제외)"
        )

        # 2-b) 월별 거래대금 데이터 수집 (1차 필터 통과 종목만)
        monthly_rows = []
        for code, code_name in strategy_filtered_universe.items():
            monthly_liquidity = price_data_by_code[code].get('monthly_liquidity', {})
            for yyyymm, liquidity in monthly_liquidity.items():
                monthly_rows.append({
                    'yyyymm': yyyymm,
                    'code': code,
                    'code_name': code_name,
                    'monthly_trading_value': float(liquidity),
                })

        if not monthly_rows:
            logger.warning("월별 유니버스 스냅샷으로 저장할 데이터가 없습니다.")
            return

        monthly_df = pd.DataFrame(monthly_rows)

        # 2-c) 2차 필터: 해당 월 최소 일평균 거래대금 기준 (30억 미만 제외)
        before_liquidity_filter = len(monthly_df)
        monthly_df = monthly_df[monthly_df['monthly_trading_value'] >= self._MIN_DAILY_TRADING_VALUE]
        logger.info(
            f"2차 필터(최소 거래대금 30억): {len(monthly_df)}개 / {before_liquidity_filter}개 행 통과"
        )

        # 2-d) 월별 거래대금 기준 상위 N개 선택
        monthly_df = monthly_df.sort_values(['yyyymm', 'monthly_trading_value'], ascending=[True, False])
        monthly_df['liquidity_rank'] = monthly_df.groupby('yyyymm')['monthly_trading_value'].rank(
            method='first', ascending=False
        )
        monthly_df = monthly_df[monthly_df['liquidity_rank'] <= snapshot_size].copy()
        monthly_df['liquidity_rank'] = monthly_df['liquidity_rank'].astype(int)
        monthly_df = monthly_df.sort_values(['yyyymm', 'liquidity_rank'])

        insert_df_to_db(self.db_name, 'universe_snapshots', monthly_df)

        # 월별 후보 종목 수 분포 로그
        codes_per_month = monthly_df.groupby('yyyymm')['code'].count()
        logger.info(
            f"월별 유니버스 스냅샷 저장 완료: {monthly_df['yyyymm'].nunique()}개월, "
            f"월당 종목 수 min={codes_per_month.min()} / avg={codes_per_month.mean():.1f} / max={codes_per_month.max()} "
            f"(목표 상위 {snapshot_size}개) → 'universe_snapshots' 테이블"
        )
    
    def fetch_stock_data(self, code: str, max_loops: int = MAX_LOOPS) -> pd.DataFrame:
        """
        특정 종목의 과거 데이터 수집
        
        Args:
            code: 종목 코드
            max_loops: 최대 API 호출 횟수 (기본값: 4번 = 약 10년치)
            
        Returns:
            DataFrame: 수집된 OHLCV 데이터
        """
        logger.info(f"종목 {code} 데이터 수집 시작 (최대 {max_loops}번 호출)...")
        
        try:
            # max_loops를 설정하여 여러 번 연속 조회
            # cont_yn은 내부적으로 처리되므로 기본값 사용
            df = self.kiwoom.get_price_data(
                code=code,
                cont_yn='N',
                max_loops=max_loops,
                max_retries=5,  # 재시도 횟수 증가
                retry_delay=2    # 재시도 대기 시간 증가
            )
            
            if df is not None and len(df) > 0:
                logger.info(f"종목 {code}: {len(df)}개 데이터 수집 완료 (최신: {df.index[-1]}, 최초: {df.index[0]})")
                return df
            else:
                logger.warning(f"종목 {code}: 데이터가 없습니다.")
                return pd.DataFrame()
                
        except Exception as e:
            logger.error(f"종목 {code} 데이터 수집 중 오류 발생: {e}")
            return pd.DataFrame()
    
    def save_to_db(self, code: str, df: pd.DataFrame):
        """
        수집한 데이터를 DB에 저장
        
        Args:
            code: 종목 코드
            df: 저장할 DataFrame
        """
        if df is None or len(df) == 0:
            logger.warning(f"종목 {code}: 저장할 데이터가 없습니다.")
            return
            
        try:
            # 기존 데이터와 비교하여 업데이트 여부 결정
            if check_table_exist(self.db_name, code):
                # 기존 데이터의 최신 날짜 확인
                sql = f"SELECT max(`index`) FROM `{code}`"
                cur = execute_sql(self.db_name, sql)
                last_date = cur.fetchone()[0]
                
                if last_date:
                    logger.info(f"종목 {code}: 기존 데이터 최신 날짜 = {last_date}")
                    
            # 데이터 저장 (replace: 기존 테이블 삭제 후 새로 생성)
            insert_df_to_db(self.db_name, code, df, option="replace")
            logger.info(f"종목 {code}: DB 저장 완료 ({len(df)}개 레코드)")
            
        except Exception as e:
            logger.error(f"종목 {code} DB 저장 중 오류 발생: {e}")
    
    def fetch_all_data(self):
        """
        전체 유니버스 종목의 과거 데이터 수집
        """
        if not self.universe:
            logger.error("유니버스가 설정되지 않았습니다. setup_universe()를 먼저 호출하세요.")
            return
        
        total_stocks = len(self.universe)
        logger.info(f"총 {total_stocks}개 종목의 데이터 수집을 시작합니다...")
        logger.info(f"예상 소요 시간: 약 {total_stocks * MAX_LOOPS * 0.5 / 60:.1f}분")
        
        success_count = 0
        fail_count = 0
        price_data_range = {}  # 종목별 데이터 가용 기간 및 월별 거래대금 메타
        
        for idx, (code, name) in enumerate(self.universe.items(), 1):
            logger.info(f"\n[{idx}/{total_stocks}] {name} ({code})")
            
            # 이미 데이터가 있는지 확인
            if check_table_exist(self.db_name, code):
                logger.info(f"종목 {code}: 이미 데이터가 존재합니다. 업데이트를 수행합니다.")
            
            # 데이터 수집
            df = self.fetch_stock_data(code, max_loops=MAX_LOOPS)
            
            if df is not None and len(df) > 0:
                # DB에 저장
                self.save_to_db(code, df)
                success_count += 1
                # 데이터 가용 기간 + 월별 거래대금 기록
                earliest = str(df.index.min())[:6]
                latest = str(df.index.max())[:6]
                tmp = df[['close', 'volume']].copy()
                tmp['yyyymm'] = tmp.index.astype(str).str[:6]
                tmp['trading_value'] = tmp['close'].astype(float) * tmp['volume'].astype(float)
                monthly_liquidity = tmp.groupby('yyyymm')['trading_value'].mean().to_dict()
                price_data_range[code] = {
                    'earliest': earliest,
                    'latest': latest,
                    'monthly_liquidity': monthly_liquidity,
                }
            else:
                fail_count += 1
            
            # API 레이트 리밋 방지를 위한 대기
            # max_loops 만큼 호출했으므로 좀 더 긴 대기 시간 설정
            time.sleep(1.0)
            
            # 진행 상황 출력
            if idx % 10 == 0:
                logger.info(f"\n=== 진행 상황: {idx}/{total_stocks} ({idx/total_stocks*100:.1f}%) ===")
                logger.info(f"성공: {success_count}, 실패: {fail_count}")
        
        logger.info(f"\n=== 데이터 수집 완료 ===")
        logger.info(f"총 종목 수: {total_stocks}")
        logger.info(f"성공: {success_count}")
        logger.info(f"실패: {fail_count}")
        logger.info(f"DB 파일: {self.db_name}.db")

        # 종목별 데이터 가용 기간을 DB에 저장 (워크포워드 백테스트에서 활용)
        if price_data_range:
            self.save_universe_snapshots(price_data_range)


def main():
    """메인 실행 함수"""
    # 환경 변수 로드
    env_path = Path(__file__).parent.parent / '.env'
    load_dotenv(dotenv_path=env_path)
    
    # 로깅 설정 - fetch_historical_data.log 파일에 기록
    configure_logging(file_name='fetch_historical_data.log')
    
    logger.info("=" * 60)
    logger.info("백테스트용 과거 데이터 수집 프로그램 시작")
    logger.info("=" * 60)
    
    # 실전투자 API 키만 사용 (백테스트용 데이터 수집은 실전 데이터가 필요)
    appkey = os.environ.get('KIWOOM_REAL_APPKEY')
    secretkey = os.environ.get('KIWOOM_REAL_SECRETKEY')
    
    if not appkey or not secretkey:
        logger.error("Error: KIWOOM_REAL_APPKEY and KIWOOM_REAL_SECRETKEY are not set.")
        logger.error("백테스트 데이터 수집은 실전투자 API 키가 필요합니다.")
        logger.error("Please set in .env file:")
        logger.error("  KIWOOM_REAL_APPKEY=your_real_app_key")
        logger.error("  KIWOOM_REAL_SECRETKEY=your_real_secret_key")
        sys.exit(1)
    
    # Kiwoom API 초기화 (실전투자 키로 mock=False 사용)
    logger.info("Kiwoom API 초기화 중 (실전투자 모드)...")
    kiwoom = Kiwoom(appkey=appkey, secretkey=secretkey, mock=False)
    logger.info("Kiwoom API 초기화 완료")
    
    # 데이터 수집기 생성
    fetcher = HistoricalDataFetcher(kiwoom, db_name=DB_NAME)
    
    # 유니버스 설정
    fetcher.setup_universe()
    
    # 사용자 확인
    print(f"\n총 {len(fetcher.universe)}개 종목의 약 {TARGET_YEARS}년치 데이터를 수집합니다.")
    print(f"DB 파일: {DB_NAME}.db")
    print(f"예상 소요 시간: 약 {len(fetcher.universe) * MAX_LOOPS * 0.5 / 60:.1f}분")
    
    response = input("\n계속하시겠습니까? (y/n): ").strip().lower()
    if response != 'y':
        logger.info("사용자에 의해 취소되었습니다.")
        sys.exit(0)
    
    # 전체 데이터 수집 시작
    start_time = time.time()
    fetcher.fetch_all_data()
    elapsed_time = time.time() - start_time
    
    logger.info(f"\n총 소요 시간: {elapsed_time/60:.1f}분")
    logger.info("프로그램을 종료합니다.")


if __name__ == '__main__':
    main()
