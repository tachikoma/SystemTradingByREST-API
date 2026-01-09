import time
import os
import datetime
from zoneinfo import ZoneInfo
from api.Kiwoom import Kiwoom
from util.make_up_universe import *
from util.db_helper import *
from util.time_helper import *
from util.notifier import *
import math
import traceback
import threading
from util.logging_config import get_logger

logger = get_logger(__name__)


class RSIStrategy(threading.Thread):
    # 전략 상수 정의 (백테스트 최적화 반영: 2026-01-01)
    MAX_HOLDINGS = 10  # 최대 보유 종목 수
    RSI_PERIOD = 2  # RSI 계산 기간
    MA_SHORT = 20  # 단기 이동평균
    MA_LONG = 60  # 장기 이동평균
    MA_TREND = 200  # 장기 추세 이동평균 (필터용)
    RSI_SELL_THRESHOLD = 80  # RSI 매도 기준
    RSI_BUY_THRESHOLD = 3  # RSI 매수 기준 (최적화: 5→3)
    PRICE_DROP_THRESHOLD = -5.0  # 가격 하락 기준 (%) (최적화: -2→-5)
    CASH_RESERVE_RATIO = 0.2  # 현금 보유 비율 (최적화: 20% 현금 유지)
    REALTIME_MAX_CODES = 100  # 실시간 등록 최대 종목 수 (API 제한)
    
    def __init__(self, kiwoom):
        threading.Thread.__init__(self)
        self.strategy_name = "RSIStrategy"
        self.kiwoom = kiwoom

        # 유니버스 정보를 담을 딕셔너리
        self.universe = {}

        # 계좌 예수금
        self.deposit = 0

        # 초기화 함수 성공 여부 확인 변수
        self.is_init_success = False

        # 주기적 동기화 관련 변수
        self.last_sync_time = 0
        self.SYNC_INTERVAL = 300  # 5분마다 동기화 (300초)
        
        # Universe 재구성 관련 변수
        self.last_universe_update = get_korea_time()
        self.UNIVERSE_UPDATE_DAYS = 30  # 30일마다 재구성
        self.universe_updated_today = False
        
        # 전체 데이터 캐싱 관련 변수 (30일 주기)
        self.last_full_cache_time = None
        self.full_cache_in_progress = False
        self.full_cache_done_today = False
        
        # 모의투자 매매제한 종목 블랙리스트 (모의투자에서만 사용)
        self.mock_trade_blacklist = set()
        self.load_mock_blacklist()
        
        # 거래 비용 설정 (.env 파일에서 읽어오기)
        # 모의투자와 실전투자에 따라 자동으로 적용
        if kiwoom.mock:
            # 모의투자: 수수료 0.35%, 증권거래세 없음
            fee_percent = float(os.getenv('TRADING_FEE_PERCENT_MOCK', '0.35'))
            tax_percent = float(os.getenv('TRADING_TAX_PERCENT_MOCK', '0.0'))
            logger.info("💼 모의투자 거래 비용 적용")
        else:
            # 실전투자: 수수료 0.015%, 증권거래세 0.20% (매도시)
            fee_percent = float(os.getenv('TRADING_FEE_PERCENT_REAL', '0.015'))
            tax_percent = float(os.getenv('TRADING_TAX_PERCENT_REAL', '0.20'))
            logger.info("💰 실전투자 거래 비용 적용")
        
        self.BUY_FEE_RATE = 1 + (fee_percent / 100)
        self.SELL_FEE_RATE = 1 + ((fee_percent + tax_percent) / 100)
        
        logger.info("거래 비용 설정: 수수료=%.4f%%, 증권거래세=%.4f%% (매도시)", 
                   fee_percent, tax_percent)
        logger.info("계산된 비율: BUY_FEE_RATE=%.6f (%.2f%%), SELL_FEE_RATE=%.6f (%.2f%%)", 
                   self.BUY_FEE_RATE, fee_percent, self.SELL_FEE_RATE, fee_percent + tax_percent)

        self.init_strategy()

    def init_strategy(self):
        """전략 초기화 기능을 수행하는 함수"""
        try:
            # 전체 캐시 상태 체크 및 필요 시 즉시 캐싱
            self.check_and_cache_if_needed()
            
            # 유니버스 조회, 없으면 생성
            self.check_and_get_universe(True)

            # 가격 정보를 조회, 필요하면 생성
            self.check_and_get_price_data()
            time.sleep(0.3)  # API 호출 간격 확보

            # Kiwoom > 주문정보 확인
            self.kiwoom.get_order()
            time.sleep(0.3)  # API 호출 간격 확보

            # Kiwoom > 잔고 확인
            self.kiwoom.get_balance()
            time.sleep(0.3)  # API 호출 간격 확보

            # Kiwoom > 예수금 확인
            self.deposit = self.kiwoom.get_deposit()

            # 유니버스 실시간 체결정보 등록
            self.set_universe_real_time()

            self.is_init_success = True

        except Exception as e:
            logger.exception("Strategy init failed: %s", traceback.format_exc())
            # 텔레그램 메시지 전송
            send_message(f"⚠️ 전략 초기화 실패\n{traceback.format_exc()}")

    def check_and_cache_if_needed(self):
        """프로그램 시작 시 캐시 상태 체크 및 필요 시 전체 캐싱"""
        import os
        from datetime import datetime
        from zoneinfo import ZoneInfo
        
        cache_file = 'all_stocks_kiwoom.xlsx'
        now = get_korea_time()
        
        # 캐시 파일이 있는지 확인
        if os.path.exists(cache_file):
            file_mod_time = datetime.fromtimestamp(os.path.getmtime(cache_file), tz=ZoneInfo("Asia/Seoul"))
            days_old = (now.date() - file_mod_time.date()).days
            
            logger.info(f"캐시 파일 발견: {days_old}일 전 데이터")
            
            # 30일 이내 캐시는 사용 가능
            if days_old < self.UNIVERSE_UPDATE_DAYS:
                logger.info(f"✅ 캐시 파일 사용 가능 ({days_old}일 전, {self.UNIVERSE_UPDATE_DAYS}일 이내)")
                self.last_full_cache_time = file_mod_time
                return
            else:
                logger.warning(f"⚠️ 캐시 파일이 너무 오래됨 ({days_old}일 전, {self.UNIVERSE_UPDATE_DAYS}일 초과)")
        else:
            logger.warning("⚠️ 캐시 파일 없음")
        
        # 캐시가 없거나 오래된 경우 → 즉시 전체 캐싱
        logger.info("💾 프로그램 시작: 전체 종목 캐싱 시작...")
        send_message(f"💾 프로그램 시작: 전체 종목 캐싱 시작\n소요 예상: 약 66분 (모의투자) / 약 22분 (실전투자)")
        
        try:
            from util.make_up_universe import cache_daily_data
            cache_daily_data(self.kiwoom)
            
            self.last_full_cache_time = now
            logger.info("✅ 전체 종목 캐싱 완료")
            send_message("✅ 전체 종목 캐싱 완료")
        except Exception as cache_error:
            logger.error("전체 종목 캐싱 실패: %s", cache_error)
            send_message(f"❌ 전체 종목 캐싱 실패\n{cache_error}\n캐시 없이 진행합니다.")
    
    def load_mock_blacklist(self):
        """DB에서 모의투자 블랙리스트를 로드하는 함수"""
        if not self.kiwoom.mock:
            return  # 실전 투자에서는 블랙리스트 사용하지 않음
        
        try:
            if check_table_exist(self.strategy_name, 'mock_blacklist'):
                sql = "select code from mock_blacklist"
                cur = execute_sql(self.strategy_name, sql)
                blacklist_items = cur.fetchall()
                for item in blacklist_items:
                    self.mock_trade_blacklist.add(item[0])
                logger.info("모의투자 블랙리스트 로드: %d개 종목", len(self.mock_trade_blacklist))
        except Exception as e:
            logger.error("블랙리스트 로드 실패: %s", e)

    def add_to_mock_blacklist(self, code, code_name, reason):
        """모의투자 블랙리스트에 종목을 추가하는 함수"""
        if not self.kiwoom.mock:
            return  # 실전 투자에서는 블랙리스트 사용하지 않음
        
        if code in self.mock_trade_blacklist:
            return  # 이미 블랙리스트에 있음
        
        try:
            self.mock_trade_blacklist.add(code)
            
            # DB에 저장
            if not check_table_exist(self.strategy_name, 'mock_blacklist'):
                # 테이블 생성
                create_sql = """CREATE TABLE mock_blacklist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    code_name TEXT,
                    reason TEXT,
                    created_at TEXT
                )"""
                execute_sql(self.strategy_name, create_sql)
            
            now = get_korea_time().strftime("%Y%m%d %H:%M:%S")
            insert_sql = f"INSERT INTO mock_blacklist (code, code_name, reason, created_at) VALUES ('{code}', '{code_name}', '{reason}', '{now}')"
            execute_sql(self.strategy_name, insert_sql)
            
            logger.warning("모의투자 블랙리스트 추가: %s (%s) - %s", code, code_name, reason)
            send_message(f"🚫 <b>모의투자 블랙리스트 추가</b>\n종목: {code_name} ({code})\n사유: {reason}")
            
            # universe에서 제거
            if code in self.universe:
                del self.universe[code]
                logger.info("Universe에서 제거: %s", code)
        
        except Exception as e:
            logger.error("블랙리스트 추가 실패: %s", e)

    def check_and_get_universe(self, force_update=False):
        """유니버스가 존재하는지 확인하고 없으면 생성하는 함수
        
        Args:
            force_update: True이면 기존 universe를 무시하고 새로 생성
        """
        if force_update or not check_table_exist(self.strategy_name, 'universe'):
            logger.info("Universe table does not exist. Creating new universe.")
            
            try:
                # 스마트 유니버스 생성: 장 종료 후면 API로 당일 데이터 갱신, 장 중이면 크롤링
                universe_list = get_universe(kiwoom_client=self.kiwoom)
                logger.info("Universe list: %s", universe_list)
            except Exception as e:
                error_msg = f"Universe 생성 실패: {e}"
                logger.error(error_msg)
                send_message(f"❌ Universe 생성 실패\n{e}")
                
                # 기존 universe 테이블이 있으면 로드하여 계속 사용
                if check_table_exist(self.strategy_name, 'universe'):
                    logger.warning("기존 Universe를 계속 사용합니다.")
                    send_message("⚠️ 기존 Universe를 계속 사용합니다.")
                    # 기존 universe 로드 (아래 else 블록 로직 사용)
                    sql = "select * from universe"
                    cur = execute_sql(self.strategy_name, sql)
                    universe_list_db = cur.fetchall()
                    
                    universe_created_at = None
                    for item in universe_list_db:
                        idx, code, code_name, created_at = item
                        
                        if universe_created_at is None:
                            universe_created_at = created_at
                        
                        if self.kiwoom.mock and code in self.mock_trade_blacklist:
                            logger.info("블랙리스트 종목 제외: %s (%s)", code_name, code)
                            continue
                        self.universe[code] = {
                            'code_name': code_name
                        }
                    
                    if universe_created_at:
                        try:
                            self.last_universe_update = datetime.datetime.strptime(
                                universe_created_at, "%Y%m%d"
                            ).replace(tzinfo=ZoneInfo("Asia/Seoul"))
                            days_ago = (get_korea_time().date() - self.last_universe_update.date()).days
                            logger.info("기존 universe 로드 완료: %d개 (생성 %d일 전)", 
                                       len(self.universe), days_ago)
                        except Exception:
                            self.last_universe_update = get_korea_time()
                    
                    return  # 기존 universe 사용하고 종료
                else:
                    # 기존 universe도 없으면 치명적 오류
                    logger.critical("Universe 생성 실패이고 기존 universe도 없습니다.")
                    send_message(f"🚨 치명적 오류: Universe 없음\n{e}")
                    raise Exception(f"Universe 생성 실패이고 기존 데이터도 없습니다: {e}")
            
            temp_universe = {}
            # 오늘 날짜를 20210101 형태로 지정
            now = get_korea_time().strftime("%Y%m%d")

            # KOSPI(0)에 상장된 모든 종목 코드를 가져와 kospi_code_list에 저장
            kospi_code_list = self.kiwoom.get_code_list_by_market("0")

            # KOSDAQ(10)에 상장된 모든 종목 코드를 가져와 kosdaq_code_list에 저장
            kosdaq_code_list = self.kiwoom.get_code_list_by_market("10")

            for code_dict in kospi_code_list + kosdaq_code_list:
                # 모든 종목 코드를 바탕으로 반복문 수행
                # time.sleep(0.5) # To avoid rate limiting
                # TODO code_name = self.kiwoom.get_master_code_name(code_dict["code"])
                code_name = code_dict["name"]

                # 얻어온 종목명이 유니버스에 포함되어 있다면 딕셔너리에 추가
                if code_name in universe_list:
                    # 모의투자일 때 블랙리스트 체크
                    if self.kiwoom.mock and code_dict["code"] in self.mock_trade_blacklist:
                        logger.info("블랙리스트 종목 제외: %s (%s)", code_name, code_dict["code"])
                        continue
                    temp_universe[code_dict["code"]] = code_name
            # 코드, 종목명, 생성일자자를 열로 가지는 DaaFrame 생성
            universe_df = pd.DataFrame({
                'code': temp_universe.keys(),
                'code_name': temp_universe.values(),
                'created_at': [now] * len(temp_universe.keys())
            })

            # universe라는 테이블명으로 Dataframe을 DB에 저장함
            insert_df_to_db(self.strategy_name, 'universe', universe_df)
            
            # 생성한 데이터를 바로 self.universe에 저장 (불필요한 DB 읽기 방지)
            for code, code_name in temp_universe.items():
                self.universe[code] = {
                    'code_name': code_name
                }
            
            # Universe 생성 시간을 메모리에 저장 (DB의 created_at과 동기화)
            self.last_universe_update = get_korea_time()
            logger.info("Created and loaded universe with %d items (created_at: %s)", 
                       len(self.universe), now)
        else:
            # 기존 universe 테이블이 있으면 DB에서 로드
            sql = "select * from universe"
            cur = execute_sql(self.strategy_name, sql)
            universe_list = cur.fetchall()
            
            # DB에서 Universe 생성 날짜를 읽어와 last_universe_update 초기화
            universe_created_at = None
            for item in universe_list:
                idx, code, code_name, created_at = item
                
                # 첫 번째 레코드의 created_at을 Universe 생성 날짜로 사용
                if universe_created_at is None:
                    universe_created_at = created_at
                
                # 모의투자일 때 블랙리스트 체크
                if self.kiwoom.mock and code in self.mock_trade_blacklist:
                    logger.info("블랙리스트 종목 제외: %s (%s)", code_name, code)
                    continue
                self.universe[code] = {
                    'code_name': code_name
                }
            
            # DB의 created_at을 last_universe_update로 설정 (YYYYMMDD 형식 파싱)
            if universe_created_at:
                try:
                    self.last_universe_update = datetime.datetime.strptime(
                        universe_created_at, "%Y%m%d"
                    ).replace(tzinfo=ZoneInfo("Asia/Seoul"))
                    days_ago = (get_korea_time().date() - self.last_universe_update.date()).days
                    logger.info("Loaded universe from DB with %d items (created %d days ago: %s)", 
                               len(self.universe), days_ago, universe_created_at)
                except Exception as e:
                    logger.warning("Failed to parse universe created_at: %s, using current time", e)
                    self.last_universe_update = get_korea_time()
            else:
                logger.warning("No universe created_at found, using current time")
                self.last_universe_update = get_korea_time()

    def check_and_get_price_data(self):
        """일봉 데이터가 존재하는지 확인하고 없다면 생성하는 함수"""
        for idx, code in enumerate(self.universe.keys()):
            logger.info("(%d/%d) %s", idx + 1, len(self.universe), code)
            
            # 테이블 존재 여부 확인
            table_exists = check_table_exist(self.strategy_name, code)
            
            # 케이스 1: 테이블이 없으면 API로 조회 후 생성
            if not table_exists:
                price_df = self.kiwoom.get_price_data(code)
                time.sleep(0.3)  # API 호출 후 대기
                insert_df_to_db(self.strategy_name, code, price_df)
                self.universe[code]['price_df'] = price_df
                logger.debug("Created price table for %s", code)
                continue
            
            # 케이스 2: 장 종료 후 데이터 업데이트 필요한지 확인
            if check_transaction_closed():
                sql = "select max(`{}`) from `{}`".format('index', code)
                cur = execute_sql(self.strategy_name, sql)
                last_date = cur.fetchone()
                now = get_korea_time().strftime("%Y%m%d")
                
                # 최근 저장 일자가 오늘이 아니면 업데이트
                if last_date[0] != now:
                    price_df = self.kiwoom.get_price_data(code)
                    time.sleep(0.3)  # API 호출 후 대기
                    insert_df_to_db(self.strategy_name, code, price_df)
                    self.universe[code]['price_df'] = price_df
                    logger.debug("Updated price data for %s", code)
                    continue
            
            # 케이스 3: DB에서 기존 데이터 로드 (API 호출 없음, 대기 불필요)
            sql = "select * from `{}`".format(code)
            cur = execute_sql(self.strategy_name, sql)
            cols = [column[0] for column in cur.description]
            
            price_df = pd.DataFrame.from_records(data=cur.fetchall(), columns=cols)
            price_df = price_df.set_index('index')
            self.universe[code]['price_df'] = price_df
            logger.debug("Loaded price data from DB for %s", code)

    def run(self):
        """실질적 수행 역할을 하는 함수"""
        while self.is_init_success:
            try:
                # 현재 한국 시간 확인
                now = get_korea_time()
                logger.info("Korea time: %s", now)
                
                # 전체 종목 캐싱 (23:50 ~ 23:59 사이, 30일 주기)
                # Universe 재구성 전날 밤에 미리 전체 종목 데이터 캐싱 (66분 소요)
                if now.hour == 23 and 50 <= now.minute < 60 and not self.full_cache_done_today:
                    days_since_cache = 999  # 최초 실행 시 무조건 캐싱
                    if self.last_full_cache_time:
                        days_since_cache = (now.date() - self.last_full_cache_time.date()).days
                    
                    # 30일 주기 또는 최초 실행 시 전체 캐싱
                    if days_since_cache >= self.UNIVERSE_UPDATE_DAYS:
                        logger.info("💾 전체 종목 캐싱 시작 (마지막 캐싱: %s)", 
                                   self.last_full_cache_time.date() if self.last_full_cache_time else '최초')
                        send_message(f"💾 전체 종목 캐싱 시작\n마지막 캐싱: {days_since_cache}일 전\n소요 예상: 약 66분 (모의투자) / 약 10분 (실전투자)")
                        
                        self.full_cache_in_progress = True
                        try:
                            # 전체 4,233개 종목 캐싱
                            from util.make_up_universe import cache_daily_data
                            cache_daily_data(self.kiwoom)
                            
                            self.full_cache_done_today = True
                            self.last_full_cache_time = now
                            self.full_cache_in_progress = False
                            
                            logger.info("✅ 전체 종목 캐싱 완료")
                            send_message("✅ 전체 종목 캐싱 완료")
                        except Exception as cache_error:
                            self.full_cache_in_progress = False
                            logger.error("전체 종목 캐싱 실패: %s", cache_error)
                            send_message(f"❌ 전체 종목 캐싱 실패\n{cache_error}")
                
                # Universe 재구성 체크 (매일 00:00 ~ 00:05 사이)
                if now.hour == 0 and now.minute < 5 and not self.universe_updated_today:
                    days_since_update = (now.date() - self.last_universe_update.date()).days
                    
                    if days_since_update >= self.UNIVERSE_UPDATE_DAYS:
                        logger.info("🔄 Universe 재구성 시작 (마지막 업데이트: %d일 전)", days_since_update)
                        send_message(f"🔄 Universe 재구성 시작\n마지막 업데이트: {days_since_update}일 전")
                        
                        try:
                            self.update_universe_with_holdings()
                            self.last_universe_update = now
                            self.universe_updated_today = True
                            
                            logger.info("✅ Universe 재구성 완료 (종목 수: %d)", len(self.universe))
                            send_message(f"✅ Universe 재구성 완료\n종목 수: {len(self.universe)}")
                        except Exception as update_error:
                            logger.error("Universe 재구성 실패: %s", update_error)
                            send_message(f"❌ Universe 재구성 실패\n{update_error}")
                
                # 다음 날로 넘어가면 플래그 리셋
                if now.hour == 1:
                    self.universe_updated_today = False
                    self.full_cache_done_today = False
                
                # (0)장중인지 확인
                if not check_transaction_open():
                    logger.info("장시간이 아니므로 5분간 대기합니다.")
                    time.sleep(5 * 60)
                    continue
                # 보유/주문 종목이 유니버스에 없더라도 모니터링하도록 보장
                try:
                    self.ensure_holdings_in_universe()
                except Exception as e:
                    logger.error("ensure_holdings_in_universe 호출 중 오류: %s", e)

                # 주기적 동기화 체크 (웹소켓 실시간 데이터 보완용)
                current_time = time.time()
                if current_time - self.last_sync_time >= self.SYNC_INTERVAL:
                    logger.info("=== 주기적 동기화 시작 ===")
                    try:
                        # API 호출 사이에 대기시간을 두어 rate limit 방지
                        time.sleep(0.3)
                        self.kiwoom.get_order()
                        time.sleep(0.3)  # API 호출 간격 확보
                        
                        self.kiwoom.get_balance()
                        time.sleep(0.3)  # API 호출 간격 확보
                        
                        self.update_deposit()
                        
                        self.last_sync_time = current_time
                        logger.info("=== 주기적 동기화 완료 ===")
                    except Exception as sync_error:
                        logger.error("주기적 동기화 실패: %s", sync_error)

                for idx, code in enumerate(self.universe.keys()):
                    logger.debug('[{}/{} {}_{}]'.format(idx + 1, len(self.universe), code, self.universe[code]['code_name'].strip()))
                    time.sleep(0.3)  # 종목별 처리 간격

                    # (1)접수한 주문이 있는지 확인
                    if code in self.kiwoom.order.keys():
                        # (2)주문이 있음
                        logger.info('접수 주문 (%s)%s', code, self.kiwoom.order[code])

                        # (2.1) '미체결수량' 확인하여 미체결 종목인지 확인
                        if self.kiwoom.order[code]['미체결수량'] > 0:
                            # 미체결 주문이 있으면 다음 종목으로 (현재는 자동 체결 대기)
                            logger.info('미체결 수량 존재: %d', self.kiwoom.order[code]['미체결수량'])
                            continue

                    # (3)보유 종목인지 확인
                    elif code in self.kiwoom.balance.keys():
                        logger.info('보유 종목 (%s)%s', code, self.kiwoom.balance[code])
                        # (6)매도 대상 확인
                        if self.check_sell_signal(code):
                            # (7)매도 대상이면 매도 주문 접수
                            self.order_sell(code)

                    else:
                        # (4)접수 주문 및 보유 종목이 아니라면 매수대상인지 확인 후 주문접수
                        self.check_buy_signal_and_order(code)

            except Exception as e:
                logger.exception("Run loop exception: %s", traceback.format_exc())
                # 텔레그램 메시지 전송
                send_message(f"⚠️ 전략 실행 중 오류\n{traceback.format_exc()}")

    def update_universe_with_holdings(self):
        """Universe 업데이트 및 제외된 보유 종목 청산"""
        # 현재 보유 종목 백업
        holding_codes = set(self.kiwoom.balance.keys())
        holding_info = {code: self.universe.get(code, {'code_name': 'N/A'}) for code in holding_codes}
        
        logger.info("현재 보유 종목 수: %d", len(holding_codes))
        
        # 새로운 universe 생성 (force_update=True)
        self.check_and_get_universe(force_update=True)
        
        # 새 universe에 없는 보유 종목 찾기
        codes_to_liquidate = holding_codes - set(self.universe.keys())
        
        if codes_to_liquidate:
            logger.info("🔴 Universe에서 제외된 보유 종목 %d개 청산 시작", len(codes_to_liquidate))
            send_message(f"🔴 Universe 재구성\nUniverse에서 제외된 보유 종목 {len(codes_to_liquidate)}개 청산 시작")
            
            # 제외된 종목들을 시장가 매도
            for code in codes_to_liquidate:
                try:
                    code_name = holding_info[code].get('code_name', 'N/A')
                    quantity = self.kiwoom.balance[code]['보유수량']
                    
                    logger.info("청산 주문: %s (%s) %d주", code, code_name, quantity)
                    
                    # 시장가 매도 (order_classification='03')
                    order_result = self.kiwoom.send_order(
                        'universe_liquidation', '1001', 1, code, quantity, 0, '03'
                    )
                    
                    if order_result.get('success'):
                        # 매도 주문 성공 시 일단 universe에 임시로 추가 (체결 완료될 때까지 유지)
                        self.universe[code] = holding_info[code]
                        logger.info("✅ 청산 주문 접수 완료: %s (%s)", code, code_name)
                        send_message(f"✅ 청산 주문 접수\n종목: {code} ({code_name})\n수량: {quantity}주\n주문번호: {order_result.get('order_no', 'N/A')}")
                    else:
                        error_msg = order_result.get('error_message', 'Unknown error')
                        logger.error("❌ 청산 주문 실패: %s (%s) - %s", code, code_name, error_msg)
                        send_message(f"❌ 청산 주문 실패\n종목: {code} ({code_name})\n오류: {error_msg}")
                        # 실패해도 universe에 추가하여 다음에 다시 시도
                        self.universe[code] = holding_info[code]
                    
                    time.sleep(0.2)  # API 호출 간격
                    
                except Exception as e:
                    logger.exception("청산 주문 중 오류 (%s): %s", code, e)
                    # 오류 발생 시에도 universe에 추가하여 다음에 다시 시도
                    self.universe[code] = holding_info[code]
        else:
            logger.info("✅ Universe 재구성 완료 (청산할 종목 없음)")
        
        # 가격 데이터 업데이트
        self.check_and_get_price_data()
        
        # 실시간 체결정보 재등록
        self.set_universe_real_time()
    
    def set_universe_real_time(self):
        """유니버스 실시간 체결정보 수신 등록하는 함수"""
        
        # universe 딕셔너리의 key값들은 종목코드들을 의미
        codes = self.universe.keys()
        
        # 모의투자일 때 블랙리스트 제외
        if self.kiwoom.mock:
            codes = [code for code in codes if code not in self.mock_trade_blacklist]
        else:
            codes = list(codes)

        # 종목코드들을 ';'을 기준으로 묶어주는 작업
        codes = ";".join(map(str, codes))

        # 종목코드들의 실시간 체결정보 수신을 요청
        self.kiwoom.set_real_reg(codes, "0")

    def ensure_holdings_in_universe(self):
        """보유 또는 주문중인 종목이 universe에 없을 경우 임시로 로드하여 모니터링 대상에 포함시킵니다.

        - DB에 이미 일봉 데이터가 있으면 로드하고, 없으면 Kiwoom API로 가져와 DB에 저장합니다.
        - 임시로 `self.universe`에 추가하며, 바로 실시간 등록을 갱신합니다.
        """
        try:
            # balance와 order에 있는 코드를 합쳐서 확인
            codes_to_ensure = set(list(self.kiwoom.balance.keys()) + list(self.kiwoom.order.keys()))
            added = []
            for code in codes_to_ensure:
                if not code:
                    continue
                if code in self.universe:
                    continue

                # 모의투자 블랙리스트면 모니터링 제외
                if self.kiwoom.mock and code in self.mock_trade_blacklist:
                    logger.info("블랙리스트로 인해 모니터링 제외: %s", code)
                    continue

                try:
                    # --- 실시간 등록 갯수 제한 처리 ---
                    # 현재 universe와 보유/주문 코드를 합친 목표 집합 계산
                    existing_universe = set(self.universe.keys())
                    desired_set = existing_universe.union(codes_to_ensure)
                    if len(desired_set) > self.REALTIME_MAX_CODES:
                        # 제거 가능한 후보: 보유/주문이 아닌 기존 universe 종목
                        removable = [c for c in existing_universe if c not in codes_to_ensure]

                        excess = len(desired_set) - self.REALTIME_MAX_CODES
                        if len(removable) >= excess:
                            # 제거 우선순위: 거래량(마지막 행의 'volume') 적은 순으로 제거
                            def last_volume(code_key):
                                try:
                                    df_tmp = self.universe.get(code_key, {}).get('price_df')
                                    if df_tmp is None or len(df_tmp) == 0:
                                        return 0
                                    # 컬럼 이름 다양성 대비
                                    for col in ['volume', '누적거래량']:
                                        if col in df_tmp.columns:
                                            return int(df_tmp.iloc[-1][col])
                                    return 0
                                except Exception:
                                    return 0

                            removable_sorted = sorted(removable, key=lambda x: last_volume(x))
                            to_remove = removable_sorted[:excess]
                            for r in to_remove:
                                try:
                                    del self.universe[r]
                                except Exception:
                                    pass
                            logger.info("실시간 등록 제한으로 제거된 기존 universe 종목: %s", to_remove)
                        else:
                            # removable이 부족하면 모두 제거하고, 남는 슬롯만큼만 신규 추가 허용
                            for r in removable:
                                try:
                                    del self.universe[r]
                                except Exception:
                                    pass
                            logger.warning("충분한 제거 후보 없음: %d개 제거, 그러나 여전히 슬롯 부족 가능", len(removable))

                    # --- DB 또는 API에서 가격 데이터 로드 ---
                    if check_table_exist(self.strategy_name, code):
                        sql = "select * from `{}`".format(code)
                        cur = execute_sql(self.strategy_name, sql)
                        cols = [column[0] for column in cur.description]
                        price_df = pd.DataFrame.from_records(data=cur.fetchall(), columns=cols)
                        price_df = price_df.set_index('index')
                    else:
                        # API로 가격 데이터 조회 후 DB에 저장
                        price_df = self.kiwoom.get_price_data(code)
                        time.sleep(0.3)
                        insert_df_to_db(self.strategy_name, code, price_df)

                    # 종목명 획득 시도
                    try:
                        code_name = self.kiwoom.get_master_code_name(code)
                    except Exception:
                        code_name = self.kiwoom.balance.get(code, {}).get('종목명', 'N/A')

                    # 임시로 universe에 추가 (다만 MAX 제한 이후엔 추가 실패 가능)
                    if len(self.universe.keys()) < self.REALTIME_MAX_CODES:
                        self.universe[code] = {
                            'code_name': code_name,
                            'price_df': price_df
                        }
                        added.append(code)
                    else:
                        logger.warning("실시간 등록 최대치(%d) 초과로 보유/주문 종목 추가 건너뜀: %s", self.REALTIME_MAX_CODES, code)
                except Exception as e:
                    logger.error("보유/주문 종목을 universe에 추가 실패 (%s): %s", code, e)

            if added:
                logger.info("임시로 universe에 추가된 보유/주문 종목: %s", added)
                try:
                    self.set_universe_real_time()
                except Exception as e:
                    logger.error("실시간 재등록 실패: %s", e)
        except Exception as e:
            logger.error("ensure_holdings_in_universe 실패: %s", e)

    def calculate_rsi(self, code):
        """RSI를 계산하는 공통 함수
        
        Args:
            code: 종목 코드
            
        Returns:
            tuple: (DataFrame with RSI, 현재가) 또는 (None, None) if error
        """
        universe_item = self.universe.get(code)
        if not universe_item or 'price_df' not in universe_item:
            logger.warning("Universe item or price_df not found for code: %s", code)
            return None, None
        
        # 실시간 체결 정보 확인
        if code not in self.kiwoom.universe_realtime_transaction_info.keys():
            logger.info("실시간 체결정보가 아직 없습니다: %s", code)
            return None, None
        
        try:
            # 실시간 체결 정보 가져오기
            realtime_info = self.kiwoom.universe_realtime_transaction_info[code]
            open_price = realtime_info['시가']
            high = realtime_info['고가']
            low = realtime_info['저가']
            close = realtime_info['현재가']
            volume = realtime_info['누적거래량']
            
            # 오늘 가격 데이터를 과거 가격 데이터(DataFrame)의 행으로 추가하기 위해 리스트로 만듦
            today_price_data = [open_price, high, low, close, volume]
            
            df = universe_item['price_df'].copy()
            
            # 과거 가격 데이터에 금일 날짜로 데이터 추가
            df.loc[get_korea_time().strftime('%Y%m%d')] = today_price_data
            
            # RSI(N) 계산 - 표준 RSI 공식 사용 (BacktestEngine과 동일)
            date_index = df.index.astype('str')
            
            # 가격 변화 계산
            delta = df['close'].diff(1)
            
            # 상승분 (gain)과 하락분 (loss) 분리
            gain = np.where(delta > 0, delta, 0)
            loss = np.where(delta < 0, -delta, 0)
            
            # 평균 계산
            avg_gain = pd.DataFrame(gain, index=date_index).rolling(window=self.RSI_PERIOD).mean()
            avg_loss = pd.DataFrame(loss, index=date_index).rolling(window=self.RSI_PERIOD).mean()
            
            # RS (Relative Strength) 계산
            # ZeroDivisionError 방지
            with np.errstate(divide='ignore', invalid='ignore'):
                rs = avg_gain / avg_loss.replace(0, np.nan)
                # 표준 RSI 공식: 100 - (100 / (1 + RS))
                RSI = 100 - (100 / (1 + rs))
                RSI = RSI.fillna(0)  # NaN을 0으로 대체
            
            df['RSI(2)'] = RSI
            
            return df, close
            
        except (KeyError, IndexError, ZeroDivisionError) as e:
            logger.error("RSI 계산 중 오류 발생 (%s): %s", code, e)
            return None, None
        except Exception as e:
            logger.error("RSI 계산 중 예상치 못한 오류 (%s): %s", code, e)
            return None, None

    def check_sell_signal(self, code):
        """매도대상인지 확인하는 함수"""
        # RSI 계산 (공통 함수 사용)
        df, close = self.calculate_rsi(code)
        
        if df is None or close is None:
            return False
        
        try:
            # 보유 종목의 매입가격 조회
            if code not in self.kiwoom.balance:
                logger.warning("보유 종목이 아닙니다: %s", code)
                return False
            
            purchase_price = self.kiwoom.balance[code]['매입가']
            
            # 금일의 RSI(2) 구하기
            if len(df) == 0:
                logger.warning("DataFrame이 비어있습니다: %s", code)
                return False
            
            rsi = df[-1:]['RSI(2)'].values[0]
            
            # RSI가 NaN이거나 inf인지 체크
            if np.isnan(rsi) or np.isinf(rsi):
                logger.warning("RSI 값이 유효하지 않습니다 (%s): %s", code, rsi)
                return False
            
            # 매도 시 수수료+세금을 고려한 손익분기점 계산
            # 실제 수령액 = 매도금액 / SELL_FEE_RATE
            # 손익분기점 = 매입가 * SELL_FEE_RATE (이 가격 이상에서 팔아야 손실 없음)
            breakeven_price = math.ceil(purchase_price * self.SELL_FEE_RATE)
            
            # 매도 조건: RSI 과열 + 수수료/세금 고려해도 수익
            if rsi > self.RSI_SELL_THRESHOLD and close > breakeven_price:
                estimated_profit_rate = ((close - breakeven_price) / purchase_price) * 100
                logger.info("매도 신호 발생: %s (RSI=%.2f, close=%d, purchase=%d, breakeven=%d, 예상수익률=%.2f%%)", 
                           code, rsi, close, purchase_price, breakeven_price, estimated_profit_rate)
                return True
            else:
                return False
                
        except (KeyError, IndexError) as e:
            logger.error("매도 신호 확인 중 오류 (%s): %s", code, e)
            return False
        except Exception as e:
            logger.error("매도 신호 확인 중 예상치 못한 오류 (%s): %s", code, e)
            return False

    def order_sell(self, code):
        """매도 주문 접수 함수"""
        try:
            # 보유 수량 확인(전량 매도 방식으로 보유한 수량을 모두 매도함)
            if code not in self.kiwoom.balance:
                logger.error("보유하지 않은 종목입니다: %s", code)
                return
            
            quantity = self.kiwoom.balance[code]['보유수량']
            
            if quantity <= 0:
                logger.warning("보유 수량이 0 이하입니다 (%s): %d", code, quantity)
                return

            # 최우선 매도 호가 확인 (에러 핸들링 추가)
            if code not in self.kiwoom.universe_realtime_transaction_info:
                logger.error("실시간 체결정보가 없습니다: %s", code)
                return
            
            ask = self.kiwoom.universe_realtime_transaction_info[code]['(최우선)매도호가']

            order_result = self.kiwoom.send_order('send_sell_order', '1001', 1, code, quantity, ask, '00')

            # 주문 결과 확인 (딕셔너리 형식)
            if order_result.get('success'):
                # 매도 체결 시 예상 수령액 계산 (수수료 + 증권거래세 차감)
                sell_amount = quantity * ask
                estimated_proceeds = math.floor(sell_amount / self.SELL_FEE_RATE)  # 실제 수령액
                total_fee = sell_amount - estimated_proceeds  # 총 비용
                
                # send_order()에서 이미 self.kiwoom.order[code]를 설정함
                # 웹소켓 응답이 오면 자동으로 업데이트되고, 체결 완료 시 자동 삭제됨
                
                message = "📉 <b>매도 주문 접수</b>\n종목: {}\n주문번호: {}\n수량: {}주\n가격: {:,}원\n예상수령: {:,}원 (수수료+세금: {:,}원)".format(
                    code, order_result.get('order_no', 'N/A'), quantity, ask, estimated_proceeds, total_fee)
                logger.info(message)
                send_message(message)
            else:
                error_code = order_result.get('error_code', 'UNKNOWN')
                error_message = order_result.get('error_message', '알 수 없는 오류')
                error_msg = "❌ <b>매도 주문 실패</b>\n종목: {}\n수량: {}주\n가격: {:,}원\n오류코드: {}\n오류메시지: {}".format(
                    code, quantity, ask, error_code, error_message)
                logger.error("매도 주문 실패: code=%s, error_code=%s, error_msg=%s", code, error_code, error_message)
                send_message(error_msg)
            
        except KeyError as e:
            logger.error("매도 주문 처리 중 키 오류 (%s): %s", code, e)
        except Exception as e:
            logger.error("매도 주문 처리 중 예상치 못한 오류 (%s): %s", code, e)

    def check_buy_signal_and_order(self, code):
        """매수 대상인지 확인하고 주문을 접수하는 함수"""
        # 매수 가능 시간 확인
        if not check_adjacent_transaction_closed():
            return False

        # RSI 계산 (공통 함수 사용)
        df, close = self.calculate_rsi(code)
        
        if df is None or close is None:
            return False
        
        try:
            # DataFrame 길이 체크
            if len(df) < 3:
                logger.warning("데이터가 부족합니다 (%s): len=%d", code, len(df))
                return False
            
            # 종가(close)를 기준으로 이동 평균 구하기
            df['ma20'] = df['close'].rolling(window=self.MA_SHORT, min_periods=1).mean()
            df['ma60'] = df['close'].rolling(window=self.MA_LONG, min_periods=1).mean()
            df['ma200'] = df['close'].rolling(window=self.MA_TREND, min_periods=1).mean()
            
            rsi = df[-1:]['RSI(2)'].values[0]
            ma20 = df[-1:]['ma20'].values[0]
            ma60 = df[-1:]['ma60'].values[0]
            ma200 = df[-1:]['ma200'].values[0]
            
            # 값들이 유효한지 체크
            if np.isnan(rsi) or np.isinf(rsi) or np.isnan(ma20) or np.isinf(ma20) or np.isnan(ma60) or np.isinf(ma60) or np.isnan(ma200) or np.isinf(ma200):
                logger.warning("계산된 값이 유효하지 않습니다 (%s): rsi=%s, ma20=%s, ma60=%s, ma200=%s", code, rsi, ma20, ma60, ma200)
                return False
            
            # 2 거래일 전 날짜(index)를 구함
            today_str = get_korea_time().strftime('%Y%m%d')
            if today_str not in df.index:
                logger.warning("오늘 날짜가 DataFrame에 없습니다 (%s): %s", code, today_str)
                return False
            
            idx = df.index.get_loc(today_str) - 2
            
            # 인덱스가 유효한지 체크
            if idx < 0 or idx >= len(df):
                logger.warning("2 거래일 전 데이터 접근 불가 (%s): idx=%d, len=%d", code, idx, len(df))
                return False
            
            # 위 index로부터 2 거래일 전 종가를 얻어옴
            close_2days_ago = df.iloc[idx]['close']
            
            # 2 거래일 전 종가와 현재가를 비교함
            if close_2days_ago == 0:
                logger.warning("2 거래일 전 종가가 0입니다 (%s)", code)
                return False
            
            price_diff = (close - close_2days_ago) / close_2days_ago * 100
            
        except (KeyError, IndexError) as e:
            logger.error("매수 신호 확인 중 오류 (%s): %s", code, e)
            return False
        except Exception as e:
            logger.error("매수 신호 확인 중 예상치 못한 오류 (%s): %s", code, e)
            return False

        # (2-1) 모의투자일 때 블랙리스트 체크
        if self.kiwoom.mock and code in self.mock_trade_blacklist:
            logger.debug("블랙리스트 종목 매수 시도 차단: %s", code)
            return

        # (3)매수 신호 확인(조건에 부합하면 주문 접수)
        # 조건: 단기 상승 추세 + 장기 상승 추세 + RSI 과매도 + 단기 하락
        if ma20 > ma60 and close > ma200 and rsi < self.RSI_BUY_THRESHOLD and price_diff < self.PRICE_DROP_THRESHOLD:
            # (4)이미 보유한 종목, 매수 주문 접수한 종목의 합이 보유 가능 최대치라면 더 이상 매수 불가하므로 종료
            if (self.get_balance_count() + self.get_buy_order_count()) >= self.MAX_HOLDINGS:
                return

            # (5)주문에 사용할 금액 계산 (현금 20% 보유 전략 적용)
            # 전체 예수금의 80%만 투자에 사용 (백테스트 최적화 결과 반영)
            investable_deposit = self.deposit * (1 - self.CASH_RESERVE_RATIO)
            budget = investable_deposit / (self.MAX_HOLDINGS - (self.get_balance_count() + self.get_buy_order_count()))

            # 최우선 매수호가 확인 (에러 핸들링 추가)
            try:
                bid = self.kiwoom.universe_realtime_transaction_info[code]['(최우선)매수호가']
            except KeyError:
                logger.error("매수호가 정보를 가져올 수 없습니다: %s", code)
                return

            # (6)주문 수량 계산(소수점은 제거하기 위해 버림)
            quantity = math.floor(budget / bid)

            # (7)주문 주식 수량이 1 미만이라면 매수 불가하므로 체크
            if quantity < 1:
                logger.info("주문 수량 부족 (quantity < 1): budget=%d, bid=%d", budget, bid)
                return

            # (8)예수금 충분한지 미리 체크 (매수 수수료 포함)
            amount = quantity * bid
            estimated_cost = math.floor(amount * self.BUY_FEE_RATE)
            
            if self.deposit < estimated_cost:
                logger.warning("예수금 부족: deposit=%d, estimated_cost=%d", self.deposit, estimated_cost)
                return

            # (9)계산을 바탕으로 지정가 매수 주문 접수
            order_result = self.kiwoom.send_order('send_buy_order', '1001', 0, code, quantity, bid, '00')

            # 주문 성공 시에만 예수금 차감 (딕셔너리 형식)
            if order_result.get('success'):  # 주문 성공
                self.deposit = self.deposit - estimated_cost
                
                # send_order()에서 이미 self.kiwoom.order[code]를 설정했으므로
                # 여기서는 중복 설정하지 않음 (웹소켓 응답이 오면 자동 업데이트됨)
                
                # 텔레그램 메시지 전송
                message = "📈 <b>매수 주문 접수</b>\n종목: {}\n주문번호: {}\n수량: {}주\n가격: {:,}원\n예수금: {:,}원".format(
                    code, order_result.get('order_no', 'N/A'), quantity, bid, self.deposit)
                logger.info(message)
                send_message(message)
            else:
                error_code = order_result.get('error_code', 'UNKNOWN')
                error_message = order_result.get('error_message', '알 수 없는 오류')
                error_msg = "❌ <b>매수 주문 실패</b>\n종목: {}\n수량: {}주\n가격: {:,}원\n오류코드: {}\n오류메시지: {}".format(
                    code, quantity, bid, error_code, error_message)
                logger.error("매수 주문 실패: code=%s, error_code=%s, error_msg=%s", code, error_code, error_message)
                send_message(error_msg)
                
                # 모의투자 매매제한 종목(RC4007) 감지 및 블랙리스트 추가
                if self.kiwoom.mock and 'RC4007' in error_message:
                    code_name = self.universe.get(code, {}).get('code_name', code)
                    self.add_to_mock_blacklist(code, code_name, error_message)

        # 매수신호가 없다면 종료
        else:
            return

    def update_deposit(self, max_retries=3, retry_delay=1):
        """실시간으로 예수금을 동기화하는 함수
        
        Args:
            max_retries: 최대 재시도 횟수 (기본값: 3)
            retry_delay: 재시도 대기 시간(초) (기본값: 1)
        """
        try:
            self.deposit = self.kiwoom.get_deposit(max_retries=max_retries, retry_delay=retry_delay)
            logger.info("예수금 업데이트: %d", self.deposit)
        except Exception as e:
            logger.error("예수금 업데이트 실패: %s", e)

    def get_balance_count(self):
        """매도 주문이 접수되지 않은 보유 종목 수를 계산하는 함수"""
        balance_count = len(self.kiwoom.balance)
        # kiwoom balance에 존재하는 종목이 매도 주문 접수되었고 미체결수량이 있다면 아직 보유 중
        # 미체결수량이 0이면 매도가 완료되었지만 아직 balance에서 제거 안된 상태
        for code in self.kiwoom.order.keys():
            if code in self.kiwoom.balance and self.kiwoom.order[code]['주문구분'] == "매도":
                # 미체결수량이 0이면 매도 완료로 간주하고 제외
                if self.kiwoom.order[code]['미체결수량'] == 0:
                    balance_count = balance_count - 1
        return balance_count

    def get_buy_order_count(self):
        """매수 주문 종목 수를 계산하는 함수"""
        buy_order_count = 0
        # 아직 체결이 완료되지 않은 매수 주문
        for code in self.kiwoom.order.keys():
            if code not in self.kiwoom.balance and self.kiwoom.order[code]['주문구분'] == "매수":
                buy_order_count = buy_order_count + 1
        return buy_order_count
