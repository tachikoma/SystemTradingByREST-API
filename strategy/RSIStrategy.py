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

import numpy as np
import pandas as pd
import json


def _get_debug_config():
    enabled = str(os.getenv('KIW_DEBUG_RSI', '0')).lower() in ('1', 'true', 'yes')
    try:
        sample = int(os.getenv('KIW_DEBUG_RSI_SAMPLE_RATE', '1'))
        if sample < 1:
            sample = 1
    except Exception:
        sample = 1
    return enabled, sample


def log_rsi_debug(symbol, stage, payload):
    """Structured one-line JSON debug log for RSI internals.

    Args:
        symbol: 종목 코드
        stage: 'pre_calc' | 'init_seed' | 'post_calc'
        payload: dict of additional fields
    """
    enabled, sample = _get_debug_config()
    if not enabled:
        return

    # sampling: deterministic by hashing symbol+stage+ts
    if sample > 1:
        try:
            import hashlib
            key = f"{symbol}-{stage}-{payload.get('ts','') or ''}"
            h = int(hashlib.md5(key.encode()).hexdigest(), 16)
            if h % sample != 0:
                return
        except Exception:
            pass

    out = {
        'symbol': symbol,
        'stage': stage,
        'ts': payload.get('ts'),
        'payload': payload,
    }
    try:
        logger.info("RSI_DEBUG: %s", json.dumps(out, default=str, ensure_ascii=False))
    except Exception:
        logger.info("RSI_DEBUG (fallback): %s %s", symbol, stage)

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
    # RSI 계산 방식: 'cutler' (SMA) 또는 'wilder' (Wilder/EWMA)
    RSI_METHOD = 'cutler'
    
    def __init__(self, kiwoom):
        threading.Thread.__init__(self)
        self.strategy_name = "RSIStrategy"
        self.kiwoom = kiwoom

        # Kiwoom API 호출 허용 플래그 (환경변수로 제어 가능)
        # ALLOW_KIWOOM_CALLS=1 또는 true/yes 로 설정하면 외부 API 호출을 허용합니다.
        allow_flag = os.getenv('ALLOW_KIWOOM_CALLS', '0')
        self.allow_kiwoom_calls = str(allow_flag).lower() in ('1', 'true', 'yes')
        if self.allow_kiwoom_calls:
            logger.info("Kiwoom API calls enabled via ALLOW_KIWOOM_CALLS")
        else:
            logger.info("Kiwoom API calls disabled (set ALLOW_KIWOOM_CALLS=1 to enable)")

        # DB 기반 코드->종목명 캐시 로드 (전략 DB 사용)
        try:
            # master_list DB에서 전체 코드->종목명 맵을 로드
            self.universe_map = load_all_stock_names('master_list')
        except Exception:
            self.universe_map = {}

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

        # RSI_METHOD can be overridden via environment variable (.env)
        try:
            rsi_method_env = os.getenv('RSI_METHOD', None)
            if rsi_method_env:
                rm = str(rsi_method_env).strip().lower()
                if rm in ('cutler', 'wilder'):
                    self.RSI_METHOD = rm
                else:
                    logger.warning("Invalid RSI_METHOD '%s' in environment; using %s", rsi_method_env, self.RSI_METHOD)
        except Exception:
            pass

        logger.info("RSI 계산 방식: %s", self.RSI_METHOD)

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
            # 텔레그램으로 마스킹된 트레이스백 전송 (길면 파일 첨부)
            try:
                send_telegram_traceback(traceback.format_exc())
            except Exception:
                # 실패 시 최소한의 알림 전송
                try:
                    send_message("⚠️ 전략 초기화 실패 (트레이스백 전송 실패, 상세 로그는 서버에서 확인하세요.)")
                except Exception:
                    pass

    @notify_on_exception(fallback_return=None)
    def check_and_cache_if_needed(self):
        """프로그램 시작 시 캐시 상태 체크 및 필요 시 전체 캐싱"""
        import os
        from datetime import datetime
        from zoneinfo import ZoneInfo
        
        # DB_DIR 적용: .env의 DB_DIR을 우선 사용
        db_dir = os.getenv('DB_DIR', '/app/data')
        try:
            os.makedirs(db_dir, exist_ok=True)
        except Exception:
            pass
        cache_file = os.path.join(db_dir, 'all_stocks_kiwoom.xlsx')
        now = get_korea_time()

        # 환경변수로 초기 전체 캐싱을 건너뛸 수 있음
        # 예: DISABLE_INITIAL_CACHE=1 또는 DISABLE_INITIAL_CACHE=true
        disable_initial = os.getenv('DISABLE_INITIAL_CACHE', '0')
        if str(disable_initial).lower() in ('1', 'true', 'yes'):
            logger.warning("초기 전체 종목 캐싱이 환경변수로 비활성화되었습니다 (DISABLE_INITIAL_CACHE=%s)", disable_initial)
            try:
                send_message("⚠️ 초기 전체 종목 캐싱 비활성화: DISABLE_INITIAL_CACHE set")
            except Exception:
                pass
            return
        
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
        send_message(f"💾 프로그램 시작: 전체 종목 캐싱 시작\n소요 예상: {'약 66분 (모의투자)' if self.kiwoom.mock else '약 10분 (실전투자)'}")
        
        try:
            from util.make_up_universe import cache_daily_data
            cache_daily_data(self.kiwoom)
            
            self.last_full_cache_time = now
            logger.info("✅ 시작 전체 종목 캐싱 완료")
            send_message("✅ 시작 전체 종목 캐싱 완료")
        except Exception as cache_error:
            logger.error("❌ 시작 전체 종목 캐싱 실패: %s", cache_error)
            send_message(f"❌ 시작 전체 종목 캐싱 실패\n{cache_error}\n캐시 없이 진행합니다.")
    
    @notify_on_exception(fallback_return=None)
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

    def resolve_stock_name(self, code):
        """종목명 해석 우선순위: 메모리 캐시(self.universe_map) -> DB -> (옵션) Kiwoom API

        반환값: 종목명(str) 또는 None
        """
        if not code:
            return None

        # 1) 메모리 캐시
        try:
            name = self.universe_map.get(code)
            if name:
                logger.debug("resolve_stock_name: memory cache hit %s -> %s", code, name)
                return name
        except Exception:
            name = None

        # 2) 현재 로드된 universe (메모리) 우선 조회
        try:
            uni_item = self.universe.get(code)
            if uni_item:
                code_name = uni_item.get('code_name') if isinstance(uni_item, dict) else None
                if code_name:
                    logger.debug("resolve_stock_name: universe in-memory hit %s -> %s", code, code_name)
                    try:
                        self.universe_map[code] = code_name
                    except Exception:
                        pass
                    return code_name
        except Exception:
            pass

        # 3) 전략 DB의 universe 테이블에서 조회 (있다면 우선 사용)
        try:
            if check_table_exist(self.strategy_name, 'universe'):
                sql = "select code_name from universe where code = '{}' LIMIT 1".format(code)
                cur = execute_sql(self.strategy_name, sql)
                row = cur.fetchone()
                if row and row[0]:
                    name = row[0]
                    logger.debug("resolve_stock_name: strategy DB universe hit %s -> %s", code, name)
                    try:
                        self.universe_map[code] = name
                    except Exception:
                        pass
                    return name
        except Exception:
            pass

        # 4) DB 조회: master_list 우선 조회
        try:
            name = get_stock_name('master_list', code)
            if name:
                logger.debug("resolve_stock_name: master_list DB hit %s -> %s", code, name)
                # 메모리 캐시에 보관
                try:
                    self.universe_map[code] = name
                except Exception:
                    pass
                return name
        except Exception:
            pass

        # 3) Kiwoom API 호출 (허용된 경우)
        logger.debug("resolve_stock_name: allow_kiwoom_calls=%s for code=%s", self.allow_kiwoom_calls, code)
        if self.allow_kiwoom_calls:
            try:
                # 안전 래퍼가 있으면 사용
                if hasattr(self.kiwoom, 'get_master_code_name_safe'):
                    name = self.kiwoom.get_master_code_name_safe(code)
                else:
                    name = self.kiwoom.get_master_code_name(code)
                logger.debug("resolve_stock_name: Kiwoom API returned %s for %s", name, code)
                if name:
                    try:
                        # master_list DB에 저장
                        upsert_stock_name('master_list', code, name)
                    except Exception:
                        pass
                    try:
                        self.universe_map[code] = name
                    except Exception:
                        pass
                    return name
            except Exception:
                pass

        return None

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
            name = self.resolve_stock_name(code)
            display = f"{name}({code})" if name else code
            send_message(f"🚫 <b>모의투자 블랙리스트 추가</b>\n종목: {display}\n사유: {reason}")
            
            # universe에서 제거
            if code in self.universe:
                del self.universe[code]
                logger.info("Universe에서 제거: %s", code)
        
        except Exception as e:
            logger.error("블랙리스트 추가 실패: %s", e)

    @notify_on_exception(fallback_return=None)
    def check_and_get_universe(self, force_update=False):
        """유니버스가 존재하는지 확인하고 없으면 생성하는 함수
        
        Args:
            force_update: True이면 기존 universe를 무시하고 새로 생성
        """

        table_exists = check_table_exist(self.strategy_name, 'universe')
        
        if force_update or not table_exists:
            # 명확한 로그: 강제 갱신 요청인지, 테이블이 존재하지 않아 생성하는지 구분 출력
            if force_update and table_exists:
                logger.info("Force update requested: existing universe table will be replaced (force_update=True).")
            elif force_update and not table_exists:
                logger.info("Force update requested and no existing universe table: creating new universe (force_update=True).")
            else:
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
                if table_exists:
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
                            logger.info("블랙리스트 종목 제외: %s(%s)", code_name, code)
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
                        logger.info("블랙리스트 종목 제외: %s(%s)", code_name, code_dict["code"])
                        continue
                    temp_universe[code_dict["code"]] = code_name

            # 보유 종목 병합은 주기적 검토 함수(`ensure_holdings_in_universe`)에서 처리합니다.
            # 여기서는 유니버스 생성 결과만을 DB에 저장하고 메모리에 로드합니다.
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
                    logger.info("블랙리스트 종목 제외: %s(%s)", code_name, code)
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

    @notify_on_exception(fallback_return=None)
    def check_and_get_price_data(self):
        """일봉 데이터가 존재하는지 확인하고 없다면 생성하는 함수"""
        for idx, code in enumerate(self.universe.keys()):
            name = self.resolve_stock_name(code)
            display = f"{name}({code})" if name else code
            logger.info("(%d/%d) %s", idx + 1, len(self.universe), display)
            
            # 테이블 존재 여부 확인
            table_exists = check_table_exist(self.strategy_name, code)
            
            # 케이스 1: 테이블이 없으면 API로 조회 후 생성
            if not table_exists:
                price_df = self.kiwoom.get_price_data(code)
                time.sleep(0.3)  # API 호출 후 대기
                insert_df_to_db(self.strategy_name, code, price_df)
                self.universe[code]['price_df'] = price_df
                logger.debug("Created price table for %s", display)
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
                    logger.debug("Updated price data for %s", display)
                    continue
            
            # 케이스 3: DB에서 기존 데이터 로드 (API 호출 없음, 대기 불필요)
            sql = "select * from `{}`".format(code)
            cur = execute_sql(self.strategy_name, sql)
            cols = [column[0] for column in cur.description]
            
            price_df = pd.DataFrame.from_records(data=cur.fetchall(), columns=cols)
            price_df = price_df.set_index('index')
            self.universe[code]['price_df'] = price_df
            logger.debug("Loaded price data from DB for %s", display)

    def run(self):
        """실질적 수행 역할을 하는 함수"""
        while self.is_init_success:
            try:
                # 현재 한국 시간 확인
                now = get_korea_time()
                logger.info("Korea time: %s", now)
                
                # 전체 종목 데이터 캐싱 (15:40 ~ 17:00 사이, 30일 주기, 66분(모의투자)/10분(실전투자) 소요)
                if not is_market_closed_day() and now.hour == 15 and 40 <= now.minute < 17 and not self.full_cache_done_today:                
                    days_since_cache = self.UNIVERSE_UPDATE_DAYS + 1 # 최초 실행 시 무조건 캐싱
                    if self.last_full_cache_time:
                        days_since_cache = (now.date() - self.last_full_cache_time.date()).days
                    
                    # 30일 주기 또는 최초 실행 시 전체 캐싱
                    if days_since_cache >= self.UNIVERSE_UPDATE_DAYS:
                        logger.info("💾 장 종료 전체 종목 캐싱 시작 (마지막 캐싱: %s)", 
                                   self.last_full_cache_time.date() if self.last_full_cache_time else '최초')
                        send_message(f"💾 장 종료 전체 종목 캐싱 시작\n마지막 캐싱: {days_since_cache}일 전\n소요 예상: {'약 66분 (모의투자)' if self.kiwoom.mock else '약 10분 (실전투자)'}")
                        
                        self.full_cache_in_progress = True
                        try:
                            # 전체 4,233개 종목 캐싱
                            from util.make_up_universe import cache_daily_data
                            cache_daily_data(self.kiwoom)
                            
                            self.full_cache_done_today = True
                            self.last_full_cache_time = now
                            self.full_cache_in_progress = False
                            
                            logger.info("✅ 장 종료 전체 종목 캐싱 완료")
                            send_message("✅ 장 종료 전체 종목 캐싱 완료")
                        except Exception as cache_error:
                            self.full_cache_in_progress = False
                            logger.error("❌ 장 종료 전체 종목 캐싱 실패: %s", cache_error)
                            send_message(f"❌ 장 종료 전체 종목 캐싱 실패\n{cache_error}")
                
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
                        time.sleep(0.4)
                        self.kiwoom.get_order()
                        time.sleep(0.4)  # API 호출 간격 확보
                        
                        self.kiwoom.get_balance()
                        time.sleep(0.4)  # API 호출 간격 확보
                        
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
                # 텔레그램으로 마스킹된 트레이스백 전송 (길면 파일 첨부)
                try:
                    send_telegram_traceback(traceback.format_exc())
                except Exception:
                    try:
                        send_message("⚠️ 전략 실행 중 오류 (트레이스백 전송 실패, 상세 로그는 서버에서 확인하세요.)")
                    except Exception:
                        pass

    @notify_on_exception(fallback_return=None)
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
                    
                    logger.info("청산 주문: %s(%s) %d주", code_name, code, quantity)
                    
                    # 시장가 매도 (order_classification='03')
                    order_result = self.kiwoom.send_order(
                        'universe_liquidation', '1001', 1, code, quantity, 0, '03'
                    )
                    
                    if order_result.get('success'):
                        # 매도 주문 성공 시 일단 universe에 임시로 추가 (체결 완료될 때까지 유지)
                        self.universe[code] = holding_info[code]
                        logger.info("✅ 청산 주문 접수 완료: %s(%s)", code_name, code)
                        name = self.resolve_stock_name(code)
                        display = f"{name}({code})" if name else code
                        send_message(f"✅ 청산 주문 접수\n종목: {display}\n수량: {quantity}주\n주문번호: {order_result.get('order_no', 'N/A')}")
                    else:
                        error_msg = order_result.get('error_message', 'Unknown error')
                        logger.error("❌ 청산 주문 실패: %s(%s) - %s", code_name, code, error_msg)
                        name = self.resolve_stock_name(code)
                        display = f"{name}({code})" if name else code
                        send_message(f"❌ 청산 주문 실패\n종목: {display}\n오류: {error_msg}")
                        # 실패해도 universe에 추가하여 다음에 다시 시도
                        self.universe[code] = holding_info[code]
                    
                    time.sleep(0.2)  # API 호출 간격
                    
                except Exception as e:
                    logger.exception("청산 주문 중 오류 %s(%s): %s", code_name, code, e)
                    # 오류 발생 시에도 universe에 추가하여 다음에 다시 시도
                    self.universe[code] = holding_info[code]
        else:
            logger.info("✅ Universe 재구성 완료 (청산할 종목 없음)")
        
        # 가격 데이터 업데이트
        self.check_and_get_price_data()
        
        # 실시간 체결정보 재등록
        self.set_universe_real_time()
    
    @notify_on_exception(fallback_return=None)
    def set_universe_real_time(self):
        """유니버스 실시간 체결정보 수신 등록하는 함수"""
        # 우선순위: 1) 보유종목, 2) 주문중인 종목, 3) 기존 universe 종목
        try:
            held = list(self.kiwoom.balance.keys())
        except Exception:
            held = []
        try:
            orders = list(self.kiwoom.order.keys())
        except Exception:
            orders = []

        # 기존 universe 목록
        existing = [c for c in list(self.universe.keys())]

        # 모의투자일 때 블랙리스트 제외 처리
        def filter_blacklist(seq):
            if self.kiwoom.mock:
                return [c for c in seq if c not in self.mock_trade_blacklist]
            return list(seq)

        held = filter_blacklist(held)
        orders = filter_blacklist(orders)
        existing = filter_blacklist(existing)

        # 병합하되 우선순위를 지키며 REALTIME_MAX_CODES 까지 선택
        selected = []
        for seq in (held, orders, existing):
            for c in seq:
                if c not in selected:
                    selected.append(c)
                if len(selected) >= self.REALTIME_MAX_CODES:
                    break
            if len(selected) >= self.REALTIME_MAX_CODES:
                break

        if len(selected) == 0:
            logger.info("등록할 실시간 종목 없음")
            return

        if len(selected) > self.REALTIME_MAX_CODES:
            logger.warning("실시간 등록 종목 수가 제한(%d)을 초과하여 잘라냄: 선택=%d", self.REALTIME_MAX_CODES, len(selected))

        codes = ";".join(map(str, selected[:self.REALTIME_MAX_CODES]))
        logger.info("실시간 등록 종목(%d): %s", len(selected[:self.REALTIME_MAX_CODES]), selected[:self.REALTIME_MAX_CODES])
        try:
            self.kiwoom.set_real_reg(codes, "0")
        except Exception as e:
            logger.error("실시간 등록 요청 실패: %s", e)

    @notify_on_exception(fallback_return=None)
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
                        logger.warning("실시간 등록 최대치(%d) 초과로 보유/주문 종목 추가 건너뜀: %s(%s)", self.REALTIME_MAX_CODES, code_name, code)
                except Exception as e:
                    logger.error("보유/주문 종목을 universe에 추가 실패 %s(%s): %s", code_name, code, e)

            if added:
                logger.info("임시로 universe에 추가된 보유/주문 종목: %s", added)
                try:
                    self.set_universe_real_time()
                except Exception as e:
                    logger.error("실시간 재등록 실패: %s", e)
        except Exception as e:
            logger.error("ensure_holdings_in_universe 실패: %s", e)

    @notify_on_exception(fallback_return=(None, None))
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
            
            # 오늘 가격 데이터를 과거 가격 데이터(DataFrame)의 행으로 추가
            df = universe_item['price_df'].copy()
            today_date = get_korea_time().strftime('%Y%m%d')
            # Toggle: use closed bar only (do not include realtime/current partial bar)
            use_closed = str(os.getenv('RSI_USE_CLOSED_BAR', '0')).lower() in ('1', 'true', 'yes')
            today_price_data = {
                'open': open_price,
                'high': high,
                'low': low,
                'close': close,
                'volume': volume,
            }
            if not use_closed:
                # include realtime as today's (partial) bar
                df.loc[today_date] = pd.Series(today_price_data)
            
            # RSI(N) 계산 - 표준 RSI 공식 사용 (BacktestEngine과 동일)
            date_index = df.index.astype('str')

            # --- Pre-calc debug log: input series and recent prices ---
            try:
                last_prices = df['close'].astype(float).tail(20).tolist()
            except Exception:
                last_prices = []
            history_close = None
            try:
                # if we're including realtime row, the previous close is at -2, otherwise at -1
                if not use_closed and len(df) >= 2:
                    history_close = float(df['close'].iloc[-2])
                elif use_closed and len(df) >= 1:
                    history_close = float(df['close'].iloc[-1])
            except Exception:
                history_close = None

            realtime_price = None
            try:
                realtime_price = float(close) if close is not None else None
            except Exception:
                realtime_price = None

            pre_payload = {
                'ts': get_korea_time().isoformat(),
                'price_source': 'mixed',
                'history_close': history_close,
                'realtime_price': realtime_price,
                'dtype': str(df['close'].dtype) if 'close' in df.columns else None,
                'last_prices': last_prices,
                'include_current_bar': (not use_closed),
            }
            try:
                log_rsi_debug(code, 'pre_calc', pre_payload)
            except Exception:
                pass

            # 가격 변화 계산
            delta = df['close'].diff(1)
            
            # 상승분 (gain)과 하락분 (loss) 분리
            # pandas Series.where를 사용하여 delta[0]=NaN이 보존되도록 함
            gain = delta.where(delta > 0, 0.0)
            loss = (-delta).where(delta < 0, 0.0)
            # 첫 인덱스의 delta는 비교상 NaN이 되므로 ewm의 min_periods 기준을
            # 기대 동작(초기화 시점이 period 위치가 되도록)으로 맞추기 위해
            # 첫 원소는 명시적으로 NaN으로 설정
            if len(gain) > 0:
                gain.iloc[0] = np.nan
                loss.iloc[0] = np.nan

            period = int(self.RSI_PERIOD)

            # Allow overriding min_periods via environment variable for experimentation
            try:
                min_periods = int(os.getenv('RSI_MIN_PERIODS', str(period)))
            except Exception:
                min_periods = period
            if min_periods < 1:
                min_periods = 1
            # Ensure min_periods does not exceed window size (period)
            if min_periods > period:
                try:
                    logger.warning("RSI_MIN_PERIODS (%d) > RSI_PERIOD (%d); capping to RSI_PERIOD", min_periods, period)
                except Exception:
                    pass
                min_periods = period

            method = getattr(self, 'RSI_METHOD', 'cutler')
            method = method.lower() if isinstance(method, str) else 'cutler'

            # Compute initial SMA seed for logging (if available)
            avg_gain_init = None
            avg_loss_init = None
            try:
                if len(gain) >= period:
                    try:
                        avg_gain_init = float(gain.rolling(window=period, min_periods=min_periods).mean().iloc[period-1])
                    except Exception:
                        avg_gain_init = None
                    try:
                        avg_loss_init = float(loss.rolling(window=period, min_periods=min_periods).mean().iloc[period-1])
                    except Exception:
                        avg_loss_init = None
            except Exception:
                avg_gain_init = None
                avg_loss_init = None

            try:
                init_payload = {'ts': get_korea_time().isoformat(), 'period': period, 'method': method, 'min_periods': min_periods, 'avg_gain_init': avg_gain_init, 'avg_loss_init': avg_loss_init, 'include_current_bar': (not use_closed)}
                log_rsi_debug(code, 'init_seed', init_payload)
            except Exception:
                pass

            if method == 'cutler':
                # Cutler 방식: 단순 이동평균(SMA)을 사용한 평균 상승/하락
                avg_gain = gain.rolling(window=period, min_periods=min_periods).mean()
                avg_loss = loss.rolling(window=period, min_periods=min_periods).mean()

                # RS 및 RSI 계산 (division 에러 무시하여 NaN 유지)
                with np.errstate(divide='ignore', invalid='ignore'):
                    rs = avg_gain / avg_loss
                    rsi = 100.0 - (100.0 / (1.0 + rs))

                # 핵심: 분모가 0이면(오직 상승만 있을 때) RSI=100으로 처리
                # 상승/하락 모두 0이면 50으로 처리
                rsi = rsi.astype(float)
                rsi.loc[avg_loss == 0.0] = 100.0
                both_zero_mask = (avg_gain == 0.0) & (avg_loss == 0.0)
                rsi.loc[both_zero_mask] = 50.0

            else:
                # Wilder's smoothing: ewm with alpha=1/period and adjust=False
                # min_periods=period 으로 초기부적합값을 NaN으로 유지
                avg_gain = gain.ewm(alpha=1.0/period, min_periods=min_periods, adjust=False).mean()
                avg_loss = loss.ewm(alpha=1.0/period, min_periods=min_periods, adjust=False).mean()

                # RS 및 RSI 계산
                rs = avg_gain / avg_loss
                rsi = 100.0 - (100.0 / (1.0 + rs))

                # 특수 케이스 처리: 부동소수점 안전하게
                both_zero = np.isclose(avg_gain, 0.0) & np.isclose(avg_loss, 0.0)
                loss_zero = np.isclose(avg_loss, 0.0) & (~both_zero)
                gain_zero = np.isclose(avg_gain, 0.0) & (~both_zero)

                rsi = rsi.astype(float)
                rsi.loc[both_zero] = 50.0
                rsi.loc[loss_zero] = 100.0
                rsi.loc[gain_zero] = 0.0

            df['RSI({})'.format(self.RSI_PERIOD)] = rsi
            # --- Post-calc debug log: final averages, RS, RSI ---
            try:
                recent_rsi = []
                try:
                    recent_rsi = rsi.dropna().tail(5).astype(float).tolist()
                except Exception:
                    recent_rsi = []

                avg_gain_curr = None
                avg_loss_curr = None
                rs_curr = None
                try:
                    if len(avg_gain) > 0:
                        avg_gain_curr = float(avg_gain.iloc[-1]) if not pd.isna(avg_gain.iloc[-1]) else None
                except Exception:
                    avg_gain_curr = None
                try:
                    if len(avg_loss) > 0:
                        avg_loss_curr = float(avg_loss.iloc[-1]) if not pd.isna(avg_loss.iloc[-1]) else None
                except Exception:
                    avg_loss_curr = None
                try:
                    if 'rs' in locals() and len(rs) > 0:
                        rs_curr = float(rs.iloc[-1]) if not pd.isna(rs.iloc[-1]) else None
                except Exception:
                    rs_curr = None

                post_payload = {
                    'ts': get_korea_time().isoformat(),
                    'period': period,
                    'method': method,
                    'min_periods': min_periods,
                    'avg_gain_curr': avg_gain_curr,
                    'avg_loss_curr': avg_loss_curr,
                    'RS': rs_curr,
                    'RSI': float(rsi.iloc[-1]) if (len(rsi) > 0 and not pd.isna(rsi.iloc[-1])) else None,
                    'recent_rsi': recent_rsi,
                    'include_current_bar': (not use_closed),
                }
                log_rsi_debug(code, 'post_calc', post_payload)
            except Exception:
                pass

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
            
            # 금일의 RSI(N) 구하기
            if len(df) == 0:
                logger.warning("DataFrame이 비어있습니다: %s", code)
                return False
            
            rsi = df[-1:][f'RSI({self.RSI_PERIOD})'].values[0]
            
            # RSI가 NaN이거나 inf인지 체크
            if np.isnan(rsi) or np.isinf(rsi):
                logger.warning("RSI 값이 유효하지 않습니다 (%s): %s", code, rsi)
                return False

            # 종목명 표시용 문자열
            name = self.resolve_stock_name(code)
            display = f"{name}({code})" if name else code
            
            # 매도 시 수수료+세금을 고려한 손익분기점 계산
            breakeven_price = math.ceil(purchase_price * self.SELL_FEE_RATE)

            # 로그: RSI 및 조건 여부 
            condition_met = (rsi > self.RSI_SELL_THRESHOLD and close > breakeven_price)
            logger.info("check_sell_signal %s RSI=%.2f close=%d breakeven=%d condition=%s", display, rsi, close, breakeven_price, condition_met)
            
            # 매도 조건: RSI 과열 + 수수료/세금 고려해도 수익
            if condition_met:
                estimated_profit_rate = ((close - breakeven_price) / purchase_price) * 100
                logger.info("매도 신호 발생: %s (RSI=%.2f, close=%d, purchase=%d, breakeven=%d, 예상수익률=%.2f%%)", 
                           display, rsi, close, purchase_price, breakeven_price, estimated_profit_rate)
                return True
            else:
                return False
                
        except (KeyError, IndexError) as e:
            logger.error("매도 신호 확인 중 오류 (%s): %s", code, e)
            return False
        except Exception as e:
            logger.error("매도 신호 확인 중 예상치 못한 오류 (%s): %s", code, e)
            return False

    @notify_on_exception(fallback_return=None)
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
                
                name = self.resolve_stock_name(code)
                display = f"{name}({code})" if name else code
                message = "📉 <b>매도 주문 접수</b>\n종목: {}\n주문번호: {}\n수량: {}주\n가격: {:,}원\n예상수령: {:,}원 (수수료+세금: {:,}원)".format(
                    display, order_result.get('order_no', 'N/A'), quantity, ask, estimated_proceeds, total_fee)
                logger.info(message)
                send_message(message)
            else:
                error_code = order_result.get('error_code', 'UNKNOWN')
                error_message = order_result.get('error_message', '알 수 없는 오류')
                name = self.resolve_stock_name(code)
                display = f"{name}({code})" if name else code
                error_msg = "❌ <b>매도 주문 실패</b>\n종목: {}\n수량: {}주\n가격: {:,}원\n오류코드: {}\n오류메시지: {}".format(
                    display, quantity, ask, error_code, error_message)
                logger.error("매도 주문 실패: 종목=%s, error_code=%s, error_msg=%s", display, error_code, error_message)
                send_message(error_msg)
            
        except KeyError as e:
            code_name = self.resolve_stock_name(code)
            logger.error("매도 주문 처리 중 키 오류 %s(%s): %s", code_name, code, e)
        except Exception as e:
            code_name = self.resolve_stock_name(code)
            logger.error("매도 주문 처리 중 예상치 못한 오류 %s(%s): %s", code_name, code, e)

    @notify_on_exception(fallback_return=None)
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
            
            rsi = df[-1:][f'RSI({self.RSI_PERIOD})'].values[0]
            ma20 = df[-1:]['ma20'].values[0]
            ma60 = df[-1:]['ma60'].values[0]
            ma200 = df[-1:]['ma200'].values[0]

            # display name for logging
            name = self.resolve_stock_name(code)
            display = f"{name}({code})" if name else code
            
            # 로그: RSI 및 지표값 
            logger.info("check_buy_signal %s RSI=%.2f ma20=%.2f ma60=%.2f ma200=%.2f", display, rsi, ma20, ma60, ma200)
            
            # 값들이 유효한지 체크
            if np.isnan(rsi) or np.isinf(rsi) or np.isnan(ma20) or np.isinf(ma20) or np.isnan(ma60) or np.isinf(ma60) or np.isnan(ma200) or np.isinf(ma200):
                logger.warning("계산된 값이 유효하지 않습니다 (%s): rsi=%s, ma20=%s, ma60=%s, ma200=%s", display, rsi, ma20, ma60, ma200)
                return False
            
            # 2 거래일 전 날짜(index)를 구함
            today_str = get_korea_time().strftime('%Y%m%d')
            if today_str not in df.index:
                logger.warning("오늘 날짜가 DataFrame에 없습니다 (%s): %s", display, today_str)
                return False
            
            idx = df.index.get_loc(today_str) - 2
            
            # 인덱스가 유효한지 체크
            if idx < 0 or idx >= len(df):
                logger.warning("2 거래일 전 데이터 접근 불가 (%s): idx=%d, len=%d", display, idx, len(df))
                return False
            
            # 위 index로부터 2 거래일 전 종가를 얻어옴
            close_2days_ago = df.iloc[idx]['close']
            
            # 2 거래일 전 종가와 현재가를 비교함
            if close_2days_ago == 0:
                logger.warning("2 거래일 전 종가가 0입니다 (%s)", display)
                return False
            
            price_diff = (close - close_2days_ago) / close_2days_ago * 100
            
        except (KeyError, IndexError) as e:
            name = self.resolve_stock_name(code)
            display = f"{name}({code})" if name else code
            logger.error("매수 신호 확인 중 오류 (%s): %s", display, e)
            return False
        except Exception as e:
            name = self.resolve_stock_name(code)
            display = f"{name}({code})" if name else code
            logger.error("매수 신호 확인 중 예상치 못한 오류 (%s): %s", display, e)
            return False

        # (2-1) 모의투자일 때 블랙리스트 체크
        name = self.resolve_stock_name(code)
        display = f"{name}({code})" if name else code
        if self.kiwoom.mock and code in self.mock_trade_blacklist:
            logger.debug("블랙리스트 종목 매수 시도 차단: %s", display)
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
                logger.error("매수호가 정보를 가져올 수 없습니다: %s", display)
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
                
                # 텔레그램 메시지 전송 (종목명 우선 표시)
                name = self.resolve_stock_name(code)
                display = f"{name}({code})" if name else code
                message = "📈 <b>매수 주문 접수</b>\n종목: {}\n주문번호: {}\n수량: {}주\n가격: {:,}원\n예수금: {:,}원".format(
                    display, order_result.get('order_no', 'N/A'), quantity, bid, self.deposit)
                logger.info(message)
                send_message(message)
            else:
                error_code = order_result.get('error_code', 'UNKNOWN')
                error_message = order_result.get('error_message', '알 수 없는 오류')
                name = self.resolve_stock_name(code)
                display = f"{name}({code})" if name else code
                error_msg = "❌ <b>매수 주문 실패</b>\n종목: {}\n수량: {}주\n가격: {:,}원\n오류코드: {}\n오류메시지: {}".format(
                    display, quantity, bid, error_code, error_message)
                logger.error("매수 주문 실패: 종목=%s, error_code=%s, error_msg=%s", display, error_code, error_message)
                send_message(error_msg)
                
                # 모의투자 매매제한 종목(RC4007) 감지 및 블랙리스트 추가
                if self.kiwoom.mock and 'RC4007' in error_message:
                    code_name = self.universe.get(code, {}).get('code_name', code)
                    self.add_to_mock_blacklist(code, code_name, error_message)

        # 매수신호가 없다면 종료
        else:
            return

    @notify_on_exception(fallback_return=None)
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
