import time
import os
import datetime
from zoneinfo import ZoneInfo
from util.make_up_universe import *
from util.db_helper import *
from util.time_helper import *
from util.notifier import *
import math
import traceback
import threading
import gc
from util.logging_config import get_logger
from util.rsi_calc import compute_rsi
from util import trade_logger

logger = get_logger(__name__)

import html

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
    PROFIT_TARGET_PERCENT = 10.0  # 매도 최소 수익률 기준 (%)
    RSI_BUY_THRESHOLD = 3  # RSI 매수 기준 (최적화: 5→3)
    PRICE_DROP_THRESHOLD = -5.0  # 가격 하락 기준 (%) (최적화: -2→-5)
    TIME_STOP_LOSS_DAYS = 90  # 시간 손절 기준 (일): 매수 후 N일 초과 시 강제 매도 (최적화: 90일)
    CASH_RESERVE_RATIO = 0.2  # 현금 보유 비율 (최적화: 20% 현금 유지)
    MORNING_FALLBACK_GAP_UP_THRESHOLD = 3.0  # 오전 fallback 갭업 차단 기준 (%) — 이 이상 갭업이면 매수 보류
    REALTIME_MAX_CODES = int(os.getenv('REALTIME_MAX_CODES', '100'))  # 실시간 등록 최대 종목 수 (WebSocket API 제한)
    POLLING_MAX_CODES  = int(os.getenv('POLLING_MAX_CODES', '150'))  # 폴링(REST) 추가 감시 최대 종목 수
    POLLING_RATE_LIMIT = 0.2  # 종목당 REST 호출 간격 (초) — rate limit 방지
    POLLING_LOG_INTERVAL = 50  # N종목마다 폴링 진행 로그 출력
    # RSI 계산 방식: 'cutler' (SMA) 또는 'wilder' (Wilder/EWMA)
    RSI_METHOD = 'cutler'
    
    def __init__(self, kiwoom, universe_cache_mode='on_demand'):
        threading.Thread.__init__(self)
        self.strategy_name = "RSIStrategy"
        self.kiwoom = kiwoom
        # universe cache mode: 'startup' | 'eod' | 'on_demand'
        self.universe_cache_mode = universe_cache_mode

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
        # 신규 매수 중단 스위치(운영 안전장치)
        self.pause_new_buys = str(os.getenv('RSI_PAUSE_NEW_BUYS', '0')).lower() in ('1', 'true', 'yes')
        self._buy_pause_log_date = None
        if self.pause_new_buys:
            logger.warning("신규 매수 중단 모드 활성화: RSI_PAUSE_NEW_BUYS=1")
        # 오후 매수 윈도우 실행 여부 추적 (fallback 처리용)
        self.buy_window_done_today = False
        # 최근 매도 사유 (check_sell_signal → order_sell 간 전달용)
        self._last_sell_reason = ""
        # 가격 데이터 준비 상태 추적
        self.price_data_ready = False
        self.last_price_data_date = None
        self._price_data_retry_count = 0
        try:
            self.PRICE_DATA_MAX_RETRIES = int(os.getenv('PRICE_DATA_MAX_RETRIES', '3'))
        except Exception:
            self.PRICE_DATA_MAX_RETRIES = 3
        # 날짜 롤오버 추적
        try:
            self._last_rollover_date = get_korea_time().date()
        except Exception:
            self._last_rollover_date = None

        # MA200 미형성 스킵 집계(일일)
        try:
            self.ma200_skip_date = get_korea_time().strftime('%Y%m%d')
        except Exception:
            self.ma200_skip_date = None
        self.ma200_skip_counts = {}
        self.ma200_skip_reported_date = None
        # 이 실행 인스턴스에서 장중을 관찰했는지 플래그
        self._saw_market_open = False
        try:
            self.MA200_SKIP_REPORT_TOP_N = int(os.getenv('MA200_SKIP_REPORT_TOP_N', '10'))
            if self.MA200_SKIP_REPORT_TOP_N < 1:
                self.MA200_SKIP_REPORT_TOP_N = 10
        except Exception:
            self.MA200_SKIP_REPORT_TOP_N = 10
        
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

        # 환경변수로 RSI 및 전략 파라미터 덮어쓰기 (있을 경우)
        try:
            v = os.getenv('RSI_SELL_THRESHOLD')
            if v is not None:
                self.RSI_SELL_THRESHOLD = float(v)
        except Exception:
            pass
        try:
            v = os.getenv('PROFIT_TARGET_PERCENT')
            if v is not None:
                self.PROFIT_TARGET_PERCENT = float(v)
        except Exception:
            pass
        try:
            v = os.getenv('RSI_BUY_THRESHOLD')
            if v is not None:
                self.RSI_BUY_THRESHOLD = float(v)
        except Exception:
            pass
        try:
            v = os.getenv('CASH_RESERVE_RATIO')
            if v is not None:
                tmp = float(v)
                if tmp > 1:
                    tmp = tmp / 100.0
                self.CASH_RESERVE_RATIO = tmp
        except Exception:
            pass

        try:
            v = os.getenv('MORNING_FALLBACK_GAP_UP_THRESHOLD')
            if v is not None:
                self.MORNING_FALLBACK_GAP_UP_THRESHOLD = float(v)
        except Exception:
            pass

        try:
            v = os.getenv('TIME_STOP_LOSS_DAYS')
            if v is not None:
                self.TIME_STOP_LOSS_DAYS = int(v)
        except Exception:
            pass

        # 단일 종목 최대 비중 비율 (예: 0.05 또는 5)
        try:
            v = os.getenv('MAX_POSITION_RATIO')
            if v is not None:
                tmp = float(v)
                if tmp > 1:
                    tmp = tmp / 100.0
                self.MAX_POSITION_RATIO = float(tmp)
            else:
                self.MAX_POSITION_RATIO = 0.05
        except Exception:
            self.MAX_POSITION_RATIO = 0.05

        logger.info("환경변수 파라미터: RSI_SELL_THRESHOLD=%s PROFIT_TARGET_PERCENT=%s RSI_BUY_THRESHOLD=%s CASH_RESERVE_RATIO=%s MORNING_FALLBACK_GAP_UP_THRESHOLD=%s TIME_STOP_LOSS_DAYS=%s",
                    self.RSI_SELL_THRESHOLD, self.PROFIT_TARGET_PERCENT, self.RSI_BUY_THRESHOLD, self.CASH_RESERVE_RATIO, self.MORNING_FALLBACK_GAP_UP_THRESHOLD, self.TIME_STOP_LOSS_DAYS)

        # 스레드 중지/웨이크용 이벤트
        self._stop_event = threading.Event()

        # 폴링(REST) 관련 상태 변수
        # WebSocket에 등록된 코드 집합 — 폴링 대상 결정에 사용
        self._realtime_registered_codes: set = set()
        # 루프 직전 실시간 데이터 스냅샷 — 루프 중 변경 방지
        self._rt_snapshot: dict = {}

        # 새벽(0~7시)에 발생하는 알림을 모아 아침 08시에 한 번에 전송하기 위한 큐
        self._delayed_messages = []
        self._delayed_messages_lock = threading.Lock()
        self._last_delayed_flush_date = None

        self.init_strategy()

    def init_strategy(self):
        """전략 초기화 기능을 수행하는 함수"""
        try:
            # Universe 캐시 모드 처리
            # - 'startup': 시작 시 전체 캐시 및 유니버스 생성
            # - 'eod' 또는 'on_demand': 시작 시 전체 캐시/유니버스 생성하지 않음
            if self.universe_cache_mode == 'startup':
                self.check_and_cache_if_needed()
            
            self.check_and_get_universe(force_update=False if self.universe_cache_mode != 'startup' else True)

            # 가격 정보를 조회: 프로그램 시작 시 하루 1회 보장
            success = self.check_and_get_price_data()
            if success:
                self.price_data_ready = True
                try:
                    self.last_price_data_date = get_korea_time().strftime('%Y%m%d')
                except Exception:
                    self.last_price_data_date = None
            else:
                self.price_data_ready = False
                self._price_data_retry_count = 0
            self._stop_event.wait(0.3)  # API 호출 간격 확보 (interruptible)

            # Kiwoom > 주문정보 확인
            self.kiwoom.get_order()
            self._stop_event.wait(0.3)  # API 호출 간격 확보 (interruptible)

            # Kiwoom > 잔고 확인
            self.kiwoom.get_balance()
            self._stop_event.wait(0.3)  # API 호출 간격 확보 (interruptible)

            # DB에서 매수일 복원: 프로그램 재시작 후에도 시간 손절이 올바르게 동작하도록 함
            try:
                saved_dates = load_all_purchase_dates()
                # DB에 저장된 키는 API/주문 소스에 따라 'A' 접두사가 있을 수 있으므로 정규화하여 비교
                normalized_saved = {k.lstrip('A').strip(): v for k, v in saved_dates.items()}
                for code, purchase_date in normalized_saved.items():
                    if code in self.kiwoom.balance:
                        if not self.kiwoom.balance[code].get('매수일'):
                            self.kiwoom.balance[code]['매수일'] = purchase_date
                            logger.info("DB에서 매수일 복원: %s -> %s", code, purchase_date)
                # DB에 있지만 이미 청산된 종목은 DB에서 삭제
                current_holdings = set(self.kiwoom.balance.keys())
                for code in list(normalized_saved.keys()):
                    if code not in current_holdings:
                        delete_purchase_date(code)
                        logger.info("청산 확인으로 매수일 DB 정리: %s", code)
            except Exception as e:
                logger.warning("매수일 DB 복원 중 오류 (무시): %s", e)

            # Kiwoom > 예수금 확인
            self.deposit = self.kiwoom.get_deposit()

            # 유니버스 실시간 체결정보 등록
            self.set_universe_real_time()

            # WebSocket 미등록 종목 REST 폴링 워커 시작
            self._start_polling_worker()

            self.is_init_success = True

        except Exception as e:
            logger.exception("Strategy init failed: %s", traceback.format_exc())
            # 텔레그램으로 마스킹된 트레이스백 전송 (길면 파일 첨부)
            try:
                send_telegram_traceback(traceback.format_exc())
            except Exception:
                # 실패 시 최소한의 알림 전송
                try:
                    self._queue_or_send("⚠️ 전략 초기화 실패 (트레이스백 전송 실패, 상세 로그는 서버에서 확인하세요.)")
                except Exception:
                    pass

    def _queue_or_send(self, text, parse_mode=None):
        """새벽 시간(0~7시)에는 메시지를 큐에 모으고, 그 외 시간에는 즉시 전송합니다."""
        try:
            now = get_korea_time()
        except Exception:
            now = None

        if now and 0 <= now.hour < 8:
            try:
                with self._delayed_messages_lock:
                    self._delayed_messages.append((text, parse_mode))
            except Exception:
                # 큐에 적재 실패 시 즉시 전송 시도
                try:
                    send_message(text, parse_mode=parse_mode)
                except Exception:
                    pass
        else:
            try:
                send_message(text, parse_mode=parse_mode)
            except Exception:
                pass

    def _flush_delayed_messages(self, now=None):
        """모아둔 새벽 메시지를 아침에 모아 전송합니다."""
        try:
            if now is None:
                now = get_korea_time()
        except Exception:
            now = None

        if now is None:
            return

        today = now.date()
        # 하루에 한 번만 플러시
        if getattr(self, '_last_delayed_flush_date', None) == today:
            return

        try:
            with self._delayed_messages_lock:
                if not self._delayed_messages:
                    self._last_delayed_flush_date = today
                    return

                header = f"📝 새벽 알림 모음 ({now.strftime('%Y-%m-%d')})\n\n"
                body = "\n\n".join([m[0] for m in self._delayed_messages if m and m[0]])
                try:
                    send_message(header + body)
                except Exception:
                    pass
                self._delayed_messages = []
                self._last_delayed_flush_date = today
        except Exception:
            pass

    def apply_env_updates(self, changed: dict):
        """런타임에서 안전하게 환경변수 변경을 적용합니다.

        - 민감하거나 리포지터리 불변 규칙에 해당하는 키는 무시합니다.
        - 적용 가능한 값은 해당 인스턴스 속성으로 즉시 반영하고 필요시 백그라운드 작업을 트리거합니다.
        """
        # 보호된(자동 적용 금지) 환경변수: 엔트리/포지션/리스크 관련 핵심 파라미터
        # 두 가지 표기 변형(밑줄 포함/미포함)을 허용하되 중복 항목은 제거합니다.
        protected = {"RSI_BUY_THRESHOLD", "PRICE_DROP_THRESHOLD", "CASH_RESERVE_RATIO", "ENABLE_STOP_LOSS"}
        # 참고: `RSI_SELL_THRESHOLD`는 현재 런타임에서 조정 가능하도록 허용되어 있습니다.
        # 이유: 매도(종결) 기준을 조정하면 기존 포지션의 진입 조건이 바뀌지 않아
        # 포지션 규모(진입 빈도)에 미치는 영향이 상대적으로 작다고 판단했기 때문입니다.
        # 다만 매도 기준 변경도 손익/리스크에 영향을 줄 수 있으므로 알림/감사 로깅은 권장합니다.
        applied = []
        ignored = []
        try:
            for k, (old, new) in changed.items():
                ku = k.upper()
                if ku in protected:
                    ignored.append(ku)
                    continue
                try:
                    if ku == 'ALLOW_KIWOOM_CALLS':
                        self.allow_kiwoom_calls = str(new).lower() in ('1', 'true', 'yes')
                        applied.append(ku)
                    elif ku == 'RSI_METHOD':
                        rm = str(new).strip().lower()
                        if rm in ('cutler', 'wilder'):
                            self.RSI_METHOD = rm
                            applied.append(ku)
                    elif ku == 'RSI_SELL_THRESHOLD':
                        self.RSI_SELL_THRESHOLD = float(new)
                        applied.append(ku)
                    elif ku == 'PROFIT_TARGET_PERCENT':
                        self.PROFIT_TARGET_PERCENT = float(new)
                        applied.append(ku)
                    elif ku == 'TIME_STOP_LOSS_DAYS':
                        self.TIME_STOP_LOSS_DAYS = int(new)
                        applied.append(ku)
                    elif ku == 'MORNING_FALLBACK_GAP_UP_THRESHOLD':
                        self.MORNING_FALLBACK_GAP_UP_THRESHOLD = float(new)
                        applied.append(ku)
                    elif ku == 'PRICE_DATA_MAX_RETRIES':
                        self.PRICE_DATA_MAX_RETRIES = int(new)
                        applied.append(ku)
                    elif ku == 'MA200_SKIP_REPORT_TOP_N':
                        self.MA200_SKIP_REPORT_TOP_N = int(new)
                        applied.append(ku)
                    elif ku in ('TRADING_FEE_PERCENT_MOCK', 'TRADING_TAX_PERCENT_MOCK', 'TRADING_FEE_PERCENT_REAL', 'TRADING_TAX_PERCENT_REAL'):
                        try:
                            if self.kiwoom.mock:
                                fee_percent = float(os.getenv('TRADING_FEE_PERCENT_MOCK', '0.35'))
                                tax_percent = float(os.getenv('TRADING_TAX_PERCENT_MOCK', '0.0'))
                            else:
                                fee_percent = float(os.getenv('TRADING_FEE_PERCENT_REAL', '0.015'))
                                tax_percent = float(os.getenv('TRADING_TAX_PERCENT_REAL', '0.20'))
                            self.BUY_FEE_RATE = 1 + (fee_percent / 100)
                            self.SELL_FEE_RATE = 1 + ((fee_percent + tax_percent) / 100)
                            applied.append(ku)
                        except Exception:
                            logger.exception("Failed to update fee rates for %s", ku)
                    elif ku == 'MAX_POSITION_RATIO':
                        tmp = float(new)
                        if tmp > 1:
                            tmp = tmp / 100.0
                        self.MAX_POSITION_RATIO = tmp
                        applied.append(ku)
                    elif ku == 'UNIVERSE_CACHE_MODE':
                        self.universe_cache_mode = str(new).strip().lower()
                        applied.append(ku)
                        if self.universe_cache_mode == 'startup':
                            try:
                                threading.Thread(target=self.check_and_cache_if_needed, daemon=True).start()
                            except Exception:
                                pass
                    else:
                        # 알 수 없는 키는 전략 내부에 적용할 수 없으므로 무시
                        pass
                except Exception:
                    logger.exception("Failed to apply env key %s", k)

            if applied:
                logger.info("Applied env updates: %s", applied)
                try:
                    self._queue_or_send("환경변수 자동 적용: " + ", ".join(applied))
                except Exception:
                    pass
            if ignored:
                logger.warning("Ignored protected env changes: %s", ignored)
                try:
                    self._queue_or_send("보호된 환경변수 변경 무시됨(수동 승인 필요): " + ", ".join(ignored))
                except Exception:
                    pass
        except Exception:
            logger.exception("apply_env_updates failed")

    def apply_sensitive_updates(self, changed: dict):
        """민감 환경변수 변경을 승인받아 적용합니다.

        보호된 키들(RSI 매수 기준, 손절/리스크 관련)은 이 함수로만 적용됩니다.
        이 함수는 승인이 난 후 `EnvApprover`가 호출합니다.
        """
        applied = []
        try:
            for k, (_old, new) in changed.items():
                ku = k.upper()
                try:
                    if ku == 'RSI_BUY_THRESHOLD':
                        self.RSI_BUY_THRESHOLD = float(new)
                        applied.append(ku)
                    elif ku == 'PRICE_DROP_THRESHOLD':
                        self.PRICE_DROP_THRESHOLD = float(new)
                        applied.append(ku)
                    elif ku == 'CASH_RESERVE_RATIO':
                        tmp = float(new)
                        if tmp > 1:
                            tmp = tmp / 100.0
                        self.CASH_RESERVE_RATIO = tmp
                        applied.append(ku)
                    elif ku == 'ENABLE_STOP_LOSS':
                        val = str(new).lower() in ('1', 'true', 'yes', 'on')
                        try:
                            setattr(self, 'enable_stop_loss', val)
                        except Exception:
                            pass
                        applied.append(ku)
                    else:
                        # 해당 키는 전략에서 직접 적용할 수 없음
                        logger.debug("apply_sensitive_updates: unknown sensitive key %s", ku)
                except Exception:
                    logger.exception("Failed to apply sensitive key %s", k)

            if applied:
                logger.info("Applied sensitive env updates: %s", applied)
                try:
                    self._queue_or_send("민감 설정 승인 적용: " + ", ".join(applied))
                except Exception:
                    pass
        except Exception:
            logger.exception("apply_sensitive_updates failed")

    @notify_on_exception(fallback_return=None)
    def check_and_cache_if_needed(self):
        """캐시 상태 체크 및 필요 시 전체 캐싱"""
        import os
        from datetime import datetime
        from zoneinfo import ZoneInfo
        
        # DB_DIR 적용: .env의 DB_DIR을 우선 사용
        db_dir = os.getenv('DB_DIR', './data')
        try:
            os.makedirs(db_dir, exist_ok=True)
        except Exception:
            pass
        # 우선순위: canonical 파일 (정규화된 백만원 단위) -> 키움 원본 캐시
        canonical_file = os.path.join(db_dir, 'all_stocks_canonical.parquet')
        cache_file = os.path.join(db_dir, 'all_stocks_kiwoom.parquet')
        now = get_korea_time()

        # 환경변수로 초기 전체 캐싱을 건너뛸 수 있음
        # 예: DISABLE_INITIAL_CACHE=1 또는 DISABLE_INITIAL_CACHE=true
        disable_initial = os.getenv('DISABLE_INITIAL_CACHE', '0')
        if str(disable_initial).lower() in ('1', 'true', 'yes'):
            logger.warning("전체 종목 캐싱이 환경변수로 비활성화되었습니다 (DISABLE_INITIAL_CACHE=%s)", disable_initial)
            try:
                self._queue_or_send("⚠️ 전체 종목 캐싱 비활성화: DISABLE_INITIAL_CACHE set")
            except Exception:
                pass
            return
        
        # canonical 파일이 우선 존재하는지 확인
        if os.path.exists(canonical_file):
            file_mod_time = datetime.fromtimestamp(os.path.getmtime(canonical_file), tz=ZoneInfo("Asia/Seoul"))
            days_old = (now.date() - file_mod_time.date()).days
            logger.info(f"Canonical 캐시 발견: {canonical_file}, {days_old}일 전 데이터")
            # UNIVERSE_UPDATE_DAYS 이내면 canonical 사용
            if days_old < self.UNIVERSE_UPDATE_DAYS:
                logger.info(f"✅ Canonical 파일 사용 가능 ({days_old}일 전, {self.UNIVERSE_UPDATE_DAYS}일 이내)")
                self.last_full_cache_time = file_mod_time
                return
            else:
                logger.warning(f"⚠️ Canonical 파일이 너무 오래됨 ({days_old}일 전, {self.UNIVERSE_UPDATE_DAYS}일 초과)")

        # canonical이 없거나 오래되었으면 기존 키움 캐시를 확인
        if os.path.exists(cache_file):
            file_mod_time = datetime.fromtimestamp(os.path.getmtime(cache_file), tz=ZoneInfo("Asia/Seoul"))
            days_old = (now.date() - file_mod_time.date()).days
            logger.info(f"캐시 파일 발견: {cache_file}, {days_old}일 전 데이터")
            # UNIVERSE_UPDATE_DAYS 이내 캐시는 사용 가능
            if days_old < self.UNIVERSE_UPDATE_DAYS:
                logger.info(f"✅ 캐시 파일 사용 가능 ({days_old}일 전, {self.UNIVERSE_UPDATE_DAYS}일 이내)")
                self.last_full_cache_time = file_mod_time
                return
            else:
                logger.warning(f"⚠️ 캐시 파일이 너무 오래됨 ({days_old}일 전, {self.UNIVERSE_UPDATE_DAYS}일 초과)")
        else:
            logger.warning(f"⚠️ 캐시 파일: {cache_file} 없음")
        
        # 캐시가 없거나 오래된 경우 → 즉시 전체 캐싱
        logger.info("💾 전체 종목 캐싱 시작...")
        self._queue_or_send(f"💾 전체 종목 캐싱 시작\n소요 예상: {'약 66분 (모의투자)' if self.kiwoom.mock else '약 10분 (실전투자)'}")
        
        try:
            from util.make_up_universe import cache_daily_data
            cache_daily_data(self.kiwoom)
            
            self.last_full_cache_time = now
            logger.info("✅ 전체 종목 캐싱 완료")
            self._queue_or_send("✅ 전체 종목 캐싱 완료")
        except Exception as cache_error:
            logger.error("❌ 전체 종목 캐싱 실패: %s", cache_error)
            self._queue_or_send(f"❌ 전체 종목 캐싱 실패\n{cache_error}\n캐시 없이 진행합니다.")
    
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
            self._queue_or_send(f"🚫 <b>모의투자 블랙리스트 추가</b>\n종목: {display}\n사유: {reason}")
            
            # universe에서 제거
            if code in self.universe:
                del self.universe[code]
                logger.info("Universe에서 제거: %s", code)
        
        except Exception as e:
            logger.error("블랙리스트 추가 실패: %s", e)

    def _compact_price_info(self, price_df):
        """가격 DataFrame -> compact dict 변환

        반환값: dict with keys `last_20_closes` (np.float32 array),
        `ma_latest` (dict of ma20/ma60/ma200 as float32), `last_date` (index last),
        `close_count` (유효 종가 개수)
        """
        try:
            if price_df is None or len(price_df) == 0:
                return {
                    'last_20_closes': np.array([], dtype='float32'),
                    'ma_latest': {'ma20': np.float32(np.nan), 'ma60': np.float32(np.nan), 'ma200': np.float32(np.nan)},
                    'last_date': None,
                    'close_count': 0,
                }

            # Close 컬럼은 반드시 'close'로 고정
            if 'close' not in price_df.columns:
                logger.warning("price_df에 'close' 컬럼이 없습니다. compact 생성을 건너뜁니다.")
                return {
                    'last_20_closes': np.array([], dtype='float32'),
                    'ma_latest': {'ma20': np.float32(np.nan), 'ma60': np.float32(np.nan), 'ma200': np.float32(np.nan)},
                    'last_date': None,
                    'close_count': 0,
                }

            closes = price_df['close'].astype('float32').dropna()
            last_20 = closes.values[-20:].astype('float32') if len(closes) > 0 else np.array([], dtype='float32')

            def _safe_ma(series, window):
                try:
                    if len(series) == 0:
                        return np.float32(np.nan)
                    return np.float32(series.rolling(window).mean().iloc[-1])
                except Exception:
                    tail = series.values[-window:]
                    if len(tail) == 0:
                        return np.float32(np.nan)
                    return np.float32(np.mean(tail.astype('float32')))

            ma20 = _safe_ma(closes, self.MA_SHORT)
            ma60 = _safe_ma(closes, self.MA_LONG)
            ma200 = _safe_ma(closes, self.MA_TREND)

            try:
                last_date = price_df.index[-1]
            except Exception:
                last_date = None

            return {
                'last_20_closes': last_20,
                'ma_latest': {'ma20': ma20, 'ma60': ma60, 'ma200': ma200},
                'last_date': last_date,
                'close_count': int(len(closes)),
            }
        except Exception as e:
            logger.warning('Compact price info 생성 실패: %s', e)
            return {
                'last_20_closes': np.array([], dtype='float32'),
                'ma_latest': {'ma20': np.float32(np.nan), 'ma60': np.float32(np.nan), 'ma200': np.float32(np.nan)},
                'last_date': None,
                'close_count': 0,
            }

    def _record_ma200_skip(self, code):
        """MA200 미형성으로 인한 매수 스킵을 일일 카운팅합니다."""
        try:
            today = get_korea_time().strftime('%Y%m%d')
        except Exception:
            return

        if self.ma200_skip_date != today:
            self.ma200_skip_date = today
            self.ma200_skip_counts = {}
            self.ma200_skip_reported_date = None

        self.ma200_skip_counts[code] = int(self.ma200_skip_counts.get(code, 0)) + 1

    def _write_ma200_skip_report_csv(self, report_date, counts, reported_at, total_count):
        """MA200 스킵 집계를 CSV로 누적 저장합니다."""
        if report_date is None:
            return
        if counts is None:
            counts = {}

        try:
            db_dir = os.getenv('DB_DIR', './data')
            os.makedirs(db_dir, exist_ok=True)
            csv_path = os.path.join(db_dir, 'ma200_skip_daily_report.csv')

            import csv
            file_exists = os.path.exists(csv_path)
            with open(csv_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow([
                        'date',
                        'code',
                        'code_name',
                        'skip_count',
                        'total_skip_count',
                        'unique_codes',
                        'reported_at',
                    ])

                if len(counts) == 0:
                    writer.writerow([
                        report_date,
                        '',
                        '',
                        0,
                        int(total_count),
                        0,
                        reported_at,
                    ])
                else:
                    unique_codes = len(counts)
                    for code, cnt in sorted(counts.items(), key=lambda x: x[1], reverse=True):
                        name = self.resolve_stock_name(code) or ''
                        writer.writerow([
                            report_date,
                            code,
                            name,
                            int(cnt),
                            int(total_count),
                            unique_codes,
                            reported_at,
                        ])
        except Exception as e:
            logger.warning("MA200 스킵 리포트 CSV 저장 실패: %s", e)

    def _send_ma200_skip_daily_report(self, now=None):
        """장 마감 이후 MA200 미형성 스킵 요약을 1회 전송합니다."""
        try:
            if now is None:
                now = get_korea_time()
            report_date = now.strftime('%Y%m%d')

            # 이미 메모리에서 오늘 전송된 상태면 중단
            if self.ma200_skip_reported_date == report_date:
                return

            # 주말(토/일) 전송 금지
            try:
                if now.weekday() >= 5:
                    return
            except Exception:
                pass

            # 이전 실행에서 파일로 기록된 리포트가 있으면 중단(프로그램 재시작 후 중복 방지)
            try:
                db_dir = os.getenv('DB_DIR', './data')
                csv_path = os.path.join(db_dir, 'ma200_skip_daily_report.csv')
                if os.path.exists(csv_path):
                    import csv as _csv
                    with open(csv_path, 'r', encoding='utf-8') as _f:
                        reader = _csv.reader(_f)
                        for row in reader:
                            if len(row) > 0 and row[0] == report_date:
                                self.ma200_skip_reported_date = report_date
                                logger.info("MA200 리포트 이미 기록됨(파일 발견): %s", csv_path)
                                return
            except Exception:
                pass

            # 장 종료 후(매수 윈도우 종료 이후)만 전송
            if now.hour < 15 or (now.hour == 15 and now.minute < 21):
                return

            # 이 인스턴스가 오늘 장중을 관찰하지 않았다면(프로그램이 장 종료 후에 시작된 경우) 전송 금지
            if not getattr(self, '_saw_market_open', False):
                logger.info("이 인스턴스는 오늘 장중 관찰이 없어 MA200 리포트 전송을 생략합니다: %s", report_date)
                return

            counts = dict(self.ma200_skip_counts) if self.ma200_skip_date == report_date else {}
            total_count = int(sum(counts.values()))
            unique_count = len(counts)

            # 건수 0인 경우 전송하지 않음
            if total_count == 0:
                logger.info("MA200 스킵 항목 없음으로 리포트 전송 생략: date=%s", report_date)
                return

            top_n = int(self.MA200_SKIP_REPORT_TOP_N)
            ranked = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_n]

            lines = [
                f"📊 MA200 미형성 스킵 일일 리포트 ({report_date})",
                f"총 스킵 건수: {total_count}",
                f"스킵 종목 수: {unique_count}",
            ]

            if len(ranked) == 0:
                lines.append("상위 종목: 없음")
            else:
                lines.append(f"상위 {len(ranked)}종목:")
                for code, cnt in ranked:
                    name = self.resolve_stock_name(code)
                    display = f"{name}({code})" if name else code
                    lines.append(f"- {display}: {int(cnt)}회")

            self._queue_or_send("\n".join(lines))
            logger.info("MA200 스킵 일일 리포트 전송 완료: date=%s total=%d unique=%d", report_date, total_count, unique_count)

            reported_at = now.strftime('%Y-%m-%d %H:%M:%S')
            # 전송 후에만 기록
            self._write_ma200_skip_report_csv(report_date, counts, reported_at, total_count)
            self.ma200_skip_reported_date = report_date
        except Exception as e:
            logger.warning("MA200 스킵 일일 리포트 전송 실패: %s", e)

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
                universe_list = get_universe(
                    kiwoom_client=self.kiwoom,
                    max_codes=self.REALTIME_MAX_CODES + self.POLLING_MAX_CODES,
                )
                logger.info("Universe list: %s", universe_list)
            except Exception as e:
                error_msg = f"Universe 생성 실패: {e}"
                logger.error(error_msg)
                self._queue_or_send(f"❌ Universe 생성 실패\n{html.escape(str(e))}")
                
                # 기존 universe 테이블이 있으면 로드하여 계속 사용
                if table_exists:
                    logger.warning("기존 Universe를 계속 사용합니다.")
                    self._queue_or_send("⚠️ 기존 Universe를 계속 사용합니다.")
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
                            self.last_universe_update = datetime.strptime(
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
                    self._queue_or_send(f"🚨 치명적 오류: Universe 없음\n{e}", parse_mode=None)
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
                    self.last_universe_update = datetime.strptime(
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
        """일봉 데이터가 존재하는지 확인하고 없다면 생성하는 함수

        Returns:
            bool: True if operation completed (no unhandled errors), False on failure
        """
        for idx, code in enumerate(self.universe.keys()):
            name = self.resolve_stock_name(code)
            display = f"{name}({code})" if name else code
            logger.info("(%d/%d) %s", idx + 1, len(self.universe), display)
            
            # 테이블 존재 여부 확인
            table_exists = check_table_exist(self.strategy_name, code)
            
            # 케이스 1: 테이블이 없으면 API로 조회 후 생성
            if not table_exists:
                # allow configurable deep fetch for initial creation
                try:
                    max_loops = int(os.getenv('PRICE_FETCH_MAX_LOOPS', '1'))
                except Exception:
                    max_loops = 1

                from util.price_fetcher import fetch_price_data
                logger.info("Price table missing for %s: fetching shallow (1 page)", display)
                price_df = fetch_price_data(self.kiwoom, code)
                self._stop_event.wait(0.3)  # API 호출 후 대기 (interruptible)
                if price_df is None or len(price_df) == 0:
                    logger.warning("Price data fetch returned empty for %s", display)
                else:
                    # log fetched range
                    try:
                        logger.info("Fetched price rows for %s: count=%d, first=%s, last=%s", display, len(price_df), price_df.index[0], price_df.index[-1])
                    except Exception:
                        pass

                insert_df_to_db(self.strategy_name, code, price_df)
                compact = self._compact_price_info(price_df)
                # 메모리 최적화: compact 구조로 저장
                self.universe[code].update(compact)
                # 필요한 경우 전체 DataFrame도 유지 (환경변수로 제어)
                if str(os.getenv('KEEP_FULL_PRICE_DF', '0')).lower() in ('1', 'true', 'yes'):
                    self.universe[code]['price_df'] = price_df
                logger.debug("Created price table for %s (compact stored)", display)
                continue
            
            # 케이스 2: 장 종료 후 데이터 업데이트 필요한지 확인
            if check_transaction_closed():
                date_col = get_date_col_name(self.strategy_name, code)
                sql = "select max(`{}`) from `{}`".format(date_col, code)
                cur = execute_sql(self.strategy_name, sql)
                last_date = cur.fetchone()
                now = get_korea_time().strftime("%Y%m%d")

                # 최근 저장 일자가 오늘(또는 최신 거래일)이 아니면 업데이트
                if not last_date or last_date[0] != now:
                    # 얕은(빠른) 조회는 항상 1페이지만 요청합니다 (최신 데이터 확보 목적)
                    from util.price_fetcher import fetch_price_data
                    logger.info("Updating price data for %s: last_date=%s, now=%s - shallow fetch=1", display, last_date[0] if last_date else None, now)
                    price_df = fetch_price_data(self.kiwoom, code)
                    self._stop_event.wait(0.3)  # API 호출 후 대기 (interruptible)

                    if price_df is None:
                        logger.warning("Price update failed for %s: no data returned", display)
                    else:
                        try:
                            logger.info("Price update fetched for %s: rows=%d, first=%s, last=%s", display, len(price_df), price_df.index[0], price_df.index[-1])
                        except Exception:
                            pass

                        insert_df_to_db(self.strategy_name, code, price_df)
                        compact = self._compact_price_info(price_df)
                        self.universe[code].update(compact)
                        if str(os.getenv('KEEP_FULL_PRICE_DF', '0')).lower() in ('1', 'true', 'yes'):
                            self.universe[code]['price_df'] = price_df
                        logger.debug("Updated price data for %s (compact stored)", display)
                    continue
            
            # 케이스 3: DB에서 기존 데이터 로드 (API 호출 없음, 대기 불필요)
            sql = "select * from `{}`".format(code)
            cur = execute_sql(self.strategy_name, sql)
            cols = [column[0] for column in cur.description]

            price_df = pd.DataFrame.from_records(data=cur.fetchall(), columns=cols)
            date_col = resolve_date_column(price_df)
            price_df = price_df.set_index(date_col)

            # Detect missing recent trading date even when table exists
            try:
                from datetime import timedelta, date
                from util.time_helper import MARKET_HOLIDAYS_2026
                kst_now = get_korea_time()
                today_dt = kst_now.date()
                prev_dt = today_dt - timedelta(days=1)
                attempts = 0
                while (prev_dt.weekday() >= 5 or prev_dt in MARKET_HOLIDAYS_2026) and attempts < 10:
                    prev_dt = prev_dt - timedelta(days=1)
                    attempts += 1
                prev_trading_str = prev_dt.strftime('%Y%m%d')
            except Exception:
                prev_trading_str = None

            refreshed_from_db_check = False
            try:
                if prev_trading_str and prev_trading_str not in price_df.index:
                    logger.warning("DB has table for %s but missing prev trading date %s; refreshing from API", display, prev_trading_str)
                    from util.price_fetcher import fetch_price_data
                    new_df = fetch_price_data(self.kiwoom, code)
                    self._stop_event.wait(0.2)
                    if new_df is not None and len(new_df) > 0:
                        price_df = new_df
                        try:
                            insert_df_to_db(self.strategy_name, code, price_df)
                        except Exception:
                            pass
                        refreshed_from_db_check = True
                        logger.info("Refreshed DB price data for %s: rows=%d (replaced)", display, len(price_df))
                    else:
                        logger.warning("Attempted refresh for %s returned no data", display)
            except Exception as e:
                logger.warning("Error while checking DB freshness for %s: %s", display, e)

            compact = self._compact_price_info(price_df)
            self.universe[code].update(compact)
            if str(os.getenv('KEEP_FULL_PRICE_DF', '0')).lower() in ('1', 'true', 'yes'):
                self.universe[code]['price_df'] = price_df
            logger.debug("Loaded price data from DB for %s (refreshed=%s, compact stored)", display, refreshed_from_db_check)

        # loop 끝: 다음은 성공 플래그 설정
        try:
            self.last_price_data_date = get_korea_time().strftime('%Y%m%d')
        except Exception:
            pass
        return True

    def run(self):
        """실질적 수행 역할을 하는 함수"""
        while self.is_init_success:
            try:
                # 현재 한국 시간 확인
                now = get_korea_time()
                logger.info("Korea time: %s", now)

                # 프로그램이 장중을 한 번이라도 관찰했는지 기록
                try:
                    if check_transaction_open():
                        self._saw_market_open = True
                except Exception:
                    pass

                # 날짜 롤오버 감지: 날짜가 바뀌면 하루치 가격 데이터 갱신을 시도
                try:
                    current_date = now.date()
                    if getattr(self, '_last_rollover_date', None) is None:
                        self._last_rollover_date = current_date
                    if current_date != self._last_rollover_date:
                        self._last_rollover_date = current_date
                        # 새로운 날짜로 넘어가면 장중 관찰 플래그 및 일일 플래그 리셋
                        try:
                            self._saw_market_open = False
                        except Exception:
                            pass
                        try:
                            # 일일 리셋 플래그: universe/캐시/매수 윈도우
                            self.universe_updated_today = False
                            self.full_cache_done_today = False
                            self.buy_window_done_today = False
                            # MA200 집계 날짜 초기화
                            today = get_korea_time().strftime('%Y%m%d')
                            if self.ma200_skip_date != today:
                                self.ma200_skip_date = today
                                self.ma200_skip_counts = {}
                                self.ma200_skip_reported_date = None
                        except Exception:
                            pass

                        logger.info("날짜 변경 감지: 일일 가격 데이터 갱신 시도")
                        ok = self.check_and_get_price_data()
                        if ok:
                            self.price_data_ready = True
                            try:
                                self.last_price_data_date = get_korea_time().strftime('%Y%m%d')
                            except Exception:
                                self.last_price_data_date = None
                            self._price_data_retry_count = 0
                            logger.info("일일 가격 데이터 갱신 성공")
                        else:
                            self.price_data_ready = False
                            self._price_data_retry_count = 0
                            logger.warning("일일 가격 데이터 갱신 실패: 장 시작 전 재시도 예정")
                            try:
                                self._queue_or_send("⚠️ 일일 가격 데이터 갱신 실패: 장 시작 전 재시도 예정")
                            except Exception:
                                pass
                except Exception:
                    pass

                # 메모리 정리: 큰 반복/캐시 작업 직후 호출 (순환참조 회수 목적)
                gc.collect()
                # 새벽에 모아둔 알림을 아침 08시(08:00~08:05)에 플러시
                try:
                    if now.hour == 8 and now.minute < 5 and getattr(self, '_last_delayed_flush_date', None) != now.date():
                        self._flush_delayed_messages(now)
                except Exception:
                    pass
                
                # 전체 종목 데이터 캐싱 (새벽 01:00, 30일 주기)
                # EOD 자동 캐시는 모드가 'eod'인 경우에만 수행
                # 실행 창: 01:00 ~ 01:10 (중복 호출 방지용 짧은 윈도우)
                start = now.replace(hour=1, minute=0, second=0, microsecond=0)
                end = now.replace(hour=1, minute=10, second=0, microsecond=0)
                # 평일(월~금) 다음날 새벽(화~토)에만 실행하도록 이전 영업일 체크
                from datetime import timedelta
                from util.time_helper import MARKET_HOLIDAYS_2026
                prev_day = now.date() - timedelta(days=1)
                if (self.universe_cache_mode == 'eod' and prev_day.weekday() < 5 and prev_day not in MARKET_HOLIDAYS_2026
                    and start <= now < end and not self.full_cache_done_today):
                    days_since_cache = self.UNIVERSE_UPDATE_DAYS + 1 # 최초 실행 시 무조건 캐싱
                    if self.last_full_cache_time:
                        days_since_cache = (now.date() - self.last_full_cache_time.date()).days
                    
                    # 30일 주기 또는 최초 실행 시 전체 캐싱
                    if days_since_cache >= self.UNIVERSE_UPDATE_DAYS:
                        logger.info("💾 장 종료 전체 종목 캐싱 시작 (마지막 캐싱: %s)", 
                                   self.last_full_cache_time.date() if self.last_full_cache_time else '최초')
                        self._queue_or_send(f"💾 장 종료 전체 종목 캐싱 시작\n마지막 캐싱: {days_since_cache}일 전\n소요 예상: {'약 66분 (모의투자)' if self.kiwoom.mock else '약 10분 (실전투자)'}")
                        
                        self.full_cache_in_progress = True
                        try:
                            # 전체 4,233개 종목 캐싱
                            from util.make_up_universe import cache_daily_data
                            cache_daily_data(self.kiwoom)
                            
                            self.full_cache_done_today = True
                            self.last_full_cache_time = now
                            self.full_cache_in_progress = False
                            
                            logger.info("✅ 장 종료 전체 종목 캐싱 완료")
                            self._queue_or_send("✅ 장 종료 전체 종목 캐싱 완료")
                        except Exception as cache_error:
                            self.full_cache_in_progress = False
                            logger.error("❌ 장 종료 전체 종목 캐싱 실패: %s", cache_error)
                            self._queue_or_send(f"❌ 장 종료 전체 종목 캐싱 실패\n{cache_error}", parse_mode=None)
                
                # Universe 재구성 체크 (매일 03:00 ~ 03:10 사이)
                # Universe 재구성: 이전 영업일이 존재하는 경우(화~토 새벽)에만 실행
                from datetime import timedelta as _td
                from util.time_helper import MARKET_HOLIDAYS_2026 as _MH
                prev_day = now.date() - _td(days=1)
                if now.hour == 3 and now.minute < 10 and not self.universe_updated_today and prev_day.weekday() < 5 and prev_day not in _MH:
                    days_since_update = (now.date() - self.last_universe_update.date()).days
                    
                    if days_since_update >= self.UNIVERSE_UPDATE_DAYS:
                        logger.info("🔄 Universe 재구성 시작 (마지막 업데이트: %d일 전)", days_since_update)
                        self._queue_or_send(f"🔄 Universe 재구성 시작\n마지막 업데이트: {days_since_update}일 전")
                        
                        try:
                            self.update_universe_with_holdings()
                            self.last_universe_update = now
                            self.universe_updated_today = True
                            
                            logger.info("✅ Universe 재구성 완료 (종목 수: %d)", len(self.universe))
                            self._queue_or_send(f"✅ Universe 재구성 완료\n종목 수: {len(self.universe)}")
                        except Exception as update_error:
                            logger.error("Universe 재구성 실패: %s", update_error)
                            self._queue_or_send(f"❌ Universe 재구성 실패\n{update_error}", parse_mode=None)
                
                # 날짜 롤오버(현재 날짜 변화)로 일일 플래그를 초기화합니다.
                # (날짜 변경 감지 블록(current_date != self._last_rollover_date)에서 이미 처리되므로
                #  시간 기반 리셋(now.hour == 1)은 제거하여 중복/경합을 방지합니다.)
                
                # (0)장중인지 확인
                if not check_transaction_open():
                    # 장 마감 이후 MA200 미형성 스킵 일일 리포트를 1회 전송
                    self._send_ma200_skip_daily_report(now)

                    # 장 시작 전: 날짜 롤오버 시 가격 데이터 갱신이 실패했다면 재시도
                    if not self.price_data_ready and self._price_data_retry_count < self.PRICE_DATA_MAX_RETRIES:
                        logger.info("가격 데이터 미준비: 장 시작 전 재시도 %d/%d", self._price_data_retry_count + 1, self.PRICE_DATA_MAX_RETRIES)
                        try:
                            ok = self.check_and_get_price_data()
                            self._price_data_retry_count += 1
                            if ok:
                                self.price_data_ready = True
                                try:
                                    self.last_price_data_date = get_korea_time().strftime('%Y%m%d')
                                except Exception:
                                    self.last_price_data_date = None
                                logger.info("가격 데이터 재시도 성공")
                            else:
                                logger.warning("가격 데이터 재시도 실패 (%d/%d)", self._price_data_retry_count, self.PRICE_DATA_MAX_RETRIES)
                        except Exception as e:
                            logger.error("가격 데이터 재시도 중 예외: %s", e)

                    logger.info("장시간이 아니므로 5분간 대기합니다.")
                    self._stop_event.wait(5 * 60)
                    continue

                # 주기적 동기화 체크 (웹소켓 실시간 데이터 보완용)
                current_time = time.time()
                if current_time - self.last_sync_time >= self.SYNC_INTERVAL:
                    logger.info("=== 주기적 동기화 시작 ===")
                    try:
                        # API 호출 사이에 대기시간을 두어 rate limit 방지
                        self._stop_event.wait(0.4)
                        self.kiwoom.get_order()
                        self._stop_event.wait(0.4)  # API 호출 간격 확보 (interruptible)
                        
                        self.kiwoom.get_balance()
                        self._stop_event.wait(0.4)  # API 호출 간격 확보 (interruptible)

                        # DB에서 매수일 복원 및 미청산 종목 정리
                        try:
                            saved_dates = load_all_purchase_dates()
                            normalized_saved = {k.lstrip('A').strip(): v for k, v in saved_dates.items()}
                            for code, purchase_date in normalized_saved.items():
                                if code in self.kiwoom.balance:
                                    if not self.kiwoom.balance[code].get('매수일'):
                                        self.kiwoom.balance[code]['매수일'] = purchase_date
                                        logger.info("DB에서 매수일 복원: %s -> %s", code, purchase_date)
                            current_holdings = set(self.kiwoom.balance.keys())
                            for code in list(normalized_saved.keys()):
                                if code not in current_holdings:
                                    delete_purchase_date(code)
                                    logger.info("청산 확인으로 매수일 DB 정리: %s", code)
                        except Exception as e:
                            logger.warning("매수일 DB 복원 중 오류 (무시): %s", e)

                        self.update_deposit()
                        
                        self.last_sync_time = current_time
                        logger.info("=== 주기적 동기화 완료 ===")
                    except Exception as sync_error:
                        logger.error("주기적 동기화 실패: %s", sync_error)

                # 보유/주문 종목이 유니버스에 없더라도 모니터링하도록 보장
                try:
                    self.ensure_holdings_in_universe()
                except Exception as e:
                    logger.error("ensure_holdings_in_universe 호출 중 오류: %s", e)

                # 루프 시작 전 실시간 데이터 스냅샷 확정
                # — 루프 실행 중 WebSocket/폴링 워커에 의한 값 변경이 개별 종목 판단에 영향을 주지 않도록 고정
                self._rt_snapshot = dict(self.kiwoom.universe_realtime_transaction_info)

                for idx, code in enumerate(self.universe.keys()):
                    logger.debug('[{}/{} {}_{}]'.format(idx + 1, len(self.universe), code, self.universe[code]['code_name'].strip()))
                    self._stop_event.wait(0.3)  # 종목별 처리 간격 (interruptible)

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
                        self._queue_or_send("⚠️ 전략 실행 중 오류 (트레이스백 전송 실패, 상세 로그는 서버에서 확인하세요.)")
                    except Exception:
                        pass

    def stop(self):
        """외부에서 호출하여 스레드의 루프를 중지시키고 잠자고 있는 wait를 해제합니다."""
        try:
            self.is_init_success = False
        except Exception:
            pass
        try:
            self._stop_event.set()
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
            self._queue_or_send(f"🔴 Universe 재구성\nUniverse에서 제외된 보유 종목 {len(codes_to_liquidate)}개 청산 시작")
            
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

                        # 시장가 주문이므로 실시간 호가/현재가를 이용해 예상값 계산
                        rt_info = self.kiwoom.universe_realtime_transaction_info.get(code, {})
                        estimated_price = rt_info.get('(최우선)매도호가') or rt_info.get('현재가') or 0
                        sell_amount = quantity * estimated_price
                        estimated_proceeds = math.floor(sell_amount / self.SELL_FEE_RATE) if estimated_price > 0 else 0
                        purchase_price = self.kiwoom.balance[code].get('매입가', 0)
                        purchase_amount = purchase_price * quantity
                        estimated_profit = estimated_proceeds - purchase_amount
                        estimated_profit_rate = (estimated_profit / purchase_amount * 100) if purchase_amount > 0 else 0.0

                        name = self.resolve_stock_name(code)
                        display = f"{name}({code})" if name else code
                        self._queue_or_send(
                            "✅ 청산 주문 접수\n"
                            f"종목: {display}\n"
                            f"수량: {quantity}주\n"
                            f"가격(추정): {estimated_price:,}원\n"
                            f"예상수령(추정): {estimated_proceeds:,}원\n"
                            f"예상수익률(추정): {estimated_profit_rate:.2f}%\n"
                            f"주문번호: {order_result.get('order_no', 'N/A')}"
                        )
                    else:
                        error_msg = order_result.get('error_message', 'Unknown error')
                        logger.error("❌ 청산 주문 실패: %s(%s) - %s", code_name, code, error_msg)
                        name = self.resolve_stock_name(code)
                        display = f"{name}({code})" if name else code
                        self._queue_or_send(f"❌ 청산 주문 실패\n종목: {display}\n오류: {error_msg}", parse_mode=None)
                        # 실패해도 universe에 추가하여 다음에 다시 시도
                        self.universe[code] = holding_info[code]
                    
                    self._stop_event.wait(0.2)  # API 호출 간격 (interruptible)
                    
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
            # WebSocket 등록 코드 집합 업데이트 — 폴링 대상 결정에 사용
            self._realtime_registered_codes = set(selected[:self.REALTIME_MAX_CODES])
            logger.info("WebSocket 등록 코드 갱신: %d종목", len(self._realtime_registered_codes))
        except Exception as e:
            logger.error("실시간 등록 요청 실패: %s", e)

    def _start_polling_worker(self):
        """WebSocket 미등록 종목을 주기적으로 REST API로 조회하는 백그라운드 워커 시작.

        WebSocket(100개 제한) 외의 유니버스 종목의 현재가를 POLLING_RATE_LIMIT 간격으로
        get_stock_info(ka10001)로 조회하여 universe_realtime_transaction_info에 기록합니다.
        rate limit: 종목당 0.2초 → 초당 5건 (키움 제한 10건/초의 절반)
        """
        def worker():
            logger.info("폴링 워커 시작: REST API로 WebSocket 미등록 종목 현재가 조회")
            while not self._stop_event.is_set():
                try:
                    # WebSocket 미등록 종목 = 폴링 대상
                    polling_targets = [
                        code for code in list(self.universe.keys())
                        if code not in self._realtime_registered_codes
                    ][:self.POLLING_MAX_CODES]

                    if not polling_targets:
                        self._stop_event.wait(10)
                        continue

                    logger.debug("폴링 워커: %d종목 조회 시작", len(polling_targets))
                    for idx, code in enumerate(polling_targets):
                        if self._stop_event.is_set():
                            break
                        try:
                            info = self.kiwoom.get_stock_info(code)
                            if info:
                                cur_prc = abs(int(float(info.get('cur_prc', 0) or 0)))
                                trde_qty = abs(int(float(info.get('trde_qty', 0) or 0)))
                                # WebSocket 포맷과 동일한 키를 유지해 주문/계산 로직이 동일하게 동작하도록 맞춥니다.
                                rt_info = self.kiwoom.universe_realtime_transaction_info.setdefault(code, {})
                                rt_info.update({
                                    '현재가': cur_prc,
                                    '(최우선)매도호가': cur_prc,
                                    '(최우선)매수호가': cur_prc,
                                    '누적거래량': trde_qty,
                                    '_from_polling': True,  # 폴링 출처 식별용
                                })
                        except Exception as e:
                            logger.debug("폴링 실패 (%s): %s", code, e)

                        if idx % self.POLLING_LOG_INTERVAL == 0 and idx > 0:
                            logger.debug("폴링 워커 진행: %d/%d", idx, len(polling_targets))

                        # 종목별 호출 간격 (rate limit 방지)
                        self._stop_event.wait(self.POLLING_RATE_LIMIT)

                except Exception as e:
                    logger.warning("폴링 워커 예외: %s", e)

                # 배치 완료 후 짧은 대기 (다음 순회 시작)
                self._stop_event.wait(5)

            logger.info("폴링 워커 종료")

        t = threading.Thread(target=worker, name='polling-worker', daemon=True)
        t.start()
        logger.info("폴링 워커 스레드 시작 완료 (최대 %d종목 REST 폴링)", self.POLLING_MAX_CODES)

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

            # --- 실시간 등록 갯수 제한 처리 (루프 밖에서 일괄 처리) ---
            try:
                existing_universe = set(self.universe.keys())
                desired_set = existing_universe.union(codes_to_ensure)
                if len(desired_set) > self.REALTIME_MAX_CODES:
                    removable = [c for c in existing_universe if c not in codes_to_ensure]
                    excess = len(desired_set) - self.REALTIME_MAX_CODES

                    if removable:
                        # 1) price_df 또는 realtime에서 가능한 한 거래량을 수집 (비용 없음)
                        volumes_map = {}
                        missing = []
                        for c in removable:
                            uni = self.universe.get(c, {})
                            df_tmp = uni.get('price_df')
                            v = 0
                            if df_tmp is not None and len(df_tmp) > 0:
                                try:
                                    v = int(df_tmp.iloc[-1]['volume'])
                                except Exception:
                                    try:
                                        v = int(float(df_tmp.iloc[-1]['volume']))
                                    except Exception:
                                        v = 0
                            else:
                                try:
                                    rt = self.kiwoom.universe_realtime_transaction_info.get(c)
                                    if isinstance(rt, dict) and '누적거래량' in rt:
                                        v = int(rt.get('누적거래량') or 0)
                                except Exception:
                                    v = 0

                            volumes_map[c] = v
                            if v == 0:
                                missing.append(c)

                        # 2) 필요한 만큼만 API 호출로 보충 (부하 최소화)
                        need = excess - sum(1 for c in removable if volumes_map.get(c, 0) > 0)
                        if need > 0 and hasattr(self.kiwoom, 'get_stock_info'):
                            for c in missing[:need]:
                                try:
                                    info = self.kiwoom.get_stock_info(c)
                                    if info and info.get('trde_qty') is not None:
                                        try:
                                            volumes_map[c] = int(str(info.get('trde_qty')).replace(',', ''))
                                        except Exception:
                                            try:
                                                volumes_map[c] = int(float(str(info.get('trde_qty')).replace(',', '')))
                                            except Exception:
                                                volumes_map[c] = 0
                                except Exception:
                                    volumes_map[c] = 0

                        # 3) 거래량 기준으로 정렬 후 excess 만큼 제거
                        removable_sorted = sorted(removable, key=lambda x: volumes_map.get(x, 0))
                        to_remove = removable_sorted[:excess]
                        for r in to_remove:
                            try:
                                del self.universe[r]
                            except Exception:
                                pass
                        logger.info("실시간 등록 제한으로 제거된 기존 universe 종목: %s", to_remove)
                    else:
                        logger.warning("실시간 등록 제한 충돌: 제거할 기존 universe 후보 없음")
            except Exception as e:
                logger.error("실시간 등록 제한 일괄 처리 실패: %s", e)

            # 이제 codes_to_ensure 루프 시작
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

                    # --- DB 또는 API에서 가격 데이터 로드 ---
                    # 동작 플래그: 전체 DataFrame 유지 여부
                    keep_full = str(os.getenv('KEEP_FULL_PRICE_DF', '0')).lower() in ('1', 'true', 'yes')

                    if check_table_exist(self.strategy_name, code):
                        # DB에 있는 최근일자를 확인하여 최신성이 없으면 API로 보강
                        try:
                            # 최근 저장 일자 확인
                            date_col = get_date_col_name(self.strategy_name, code)
                            cur = execute_sql(self.strategy_name, f"select max(`{date_col}`) from `{code}`")
                            last_date_row = cur.fetchone()
                            last_date = last_date_row[0] if last_date_row else None
                        except Exception:
                            last_date = None

                        # 계산: 이전 거래일(영업일)을 구함
                        try:
                            from datetime import timedelta
                            from util.time_helper import MARKET_HOLIDAYS_2026
                            kst_now = get_korea_time()
                            today_dt = kst_now.date()
                            prev_dt = today_dt - timedelta(days=1)
                            attempts = 0
                            while (prev_dt.weekday() >= 5 or prev_dt in MARKET_HOLIDAYS_2026) and attempts < 10:
                                prev_dt = prev_dt - timedelta(days=1)
                                attempts += 1
                            prev_trading_str = prev_dt.strftime('%Y%m%d')
                        except Exception:
                            prev_trading_str = None

                        needs_api_refresh = False
                        if prev_trading_str is None:
                            needs_api_refresh = False
                        else:
                            if last_date is None or last_date != prev_trading_str:
                                needs_api_refresh = True

                        if needs_api_refresh:
                                # API로 DB 보강 (얕은 조회)
                                from util.price_fetcher import fetch_price_data
                                price_df = fetch_price_data(self.kiwoom, code)
                                self._stop_event.wait(0.3)
                                if price_df is not None and len(price_df) > 0:
                                    insert_df_to_db(self.strategy_name, code, price_df)
                                else:
                                    # 못가져오면 기존 DB를 얕게 읽음
                                    price_df = None

                        if not needs_api_refresh:
                            # DB에서 전체 price_df를 항상 불러옵니다. 
                            try:
                                sql = "select * from `{}`".format(code)
                                cur = execute_sql(self.strategy_name, sql)
                                cols = [column[0] for column in cur.description]
                                price_df = pd.DataFrame.from_records(data=cur.fetchall(), columns=cols)
                                date_col = resolve_date_column(price_df)
                                price_df = price_df.set_index(date_col)
                            except Exception:
                                price_df = None
                    else:
                        # API로 가격 데이터 조회 후 DB에 저장
                        from util.price_fetcher import fetch_price_data
                        price_df = fetch_price_data(self.kiwoom, code)
                        self._stop_event.wait(0.3)
                        insert_df_to_db(self.strategy_name, code, price_df)

                    # 종목명 획득 시도
                    try:
                        code_name = self.resolve_stock_name(code)
                    except Exception:
                        code_name = self.kiwoom.balance.get(code, {}).get('종목명', 'N/A')

                    # 임시로 universe에 추가 (다만 MAX 제한 이후엔 추가 실패 가능)
                    if len(self.universe.keys()) < self.REALTIME_MAX_CODES:
                        # keep_full 플래그에 따라 전체 price_df를 보관하거나 compact 정보만 저장
                        try:
                            if price_df is None:
                                # DB/API에서 데이터를 얻지 못한 경우라도 compact 생성 함수는 안전하게 처리
                                compact = self._compact_price_info(price_df)
                                self.universe[code] = {'code_name': code_name}
                                self.universe[code].update(compact)
                            else:
                                if keep_full:
                                    self.universe[code] = {
                                        'code_name': code_name,
                                        'price_df': price_df
                                    }
                                else:
                                    compact = self._compact_price_info(price_df)
                                    self.universe[code] = {'code_name': code_name}
                                    self.universe[code].update(compact)

                            added.append(code)
                        except Exception as e:
                            logger.error("임시 universe 추가 중 오류 %s(%s): %s", code_name, code, e)
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
        if not universe_item:
            logger.warning("Universe item not found for code: %s", code)
            return None, None

        # 실시간 체결 정보 확인 (루프 전 캡처된 스냅샷 사용 — 루프 중 변경 영향 차단)
        # _rt_snapshot이 없는 경우(초기화 전 직접 호출 등) 라이브 데이터로 폴백
        rt_source = self._rt_snapshot if self._rt_snapshot else self.kiwoom.universe_realtime_transaction_info
        if code not in rt_source:
            logger.info("실시간 체결정보가 아직 없습니다: %s", code)
            return None, None

        try:
            realtime_info = rt_source[code]
            close = realtime_info.get('현재가')

            # 우선 compact 데이터 사용
            if 'last_20_closes' in universe_item:
                stored = universe_item.get('last_20_closes', np.array([], dtype='float32'))
                # make numpy array
                try:
                    stored = np.asarray(stored, dtype='float32')
                except Exception:
                    stored = np.array([], dtype='float32')

                use_closed = str(os.getenv('RSI_USE_CLOSED_BAR', '0')).lower() in ('1', 'true', 'yes')

                if not use_closed and close is not None:
                    closes = np.concatenate([stored, np.array([np.float32(close)], dtype='float32')])
                else:
                    closes = stored.copy()

                # Need at least 3 points to compute 2-days-ago and RSI reliably
                if len(closes) < 3:
                    logger.warning("충분한 가격 히스토리 없음 (compact) for %s: len=%d", code, len(closes))
                    return None, None

                s = pd.Series(closes.astype('float64'))

                period = int(self.RSI_PERIOD)
                try:
                    min_periods = int(os.getenv('RSI_MIN_PERIODS', str(period)))
                except Exception:
                    min_periods = period

                method = getattr(self, 'RSI_METHOD', 'cutler')
                method = method.lower() if isinstance(method, str) else 'cutler'

                rsi = compute_rsi(s, period=period, min_periods=min_periods, method=method)

                df = pd.DataFrame({'close': s.values})
                df[f'RSI({self.RSI_PERIOD})'] = rsi.values

                return df, float(close)

            # fallback: legacy behavior when full price_df exists
            if 'price_df' in universe_item:
                price_df = universe_item.get('price_df')
                # reuse original behavior by temporarily setting and calling existing logic
                # (simpler to reconstruct minimal DataFrame)
                # Use previous implementation: create df and include realtime bar if needed
                # For brevity, build df from price_df and proceed as before
                df = price_df.copy()
                from datetime import timedelta
                try:
                    today_date = get_korea_time().strftime('%Y%m%d')
                    use_closed = str(os.getenv('RSI_USE_CLOSED_BAR', '0')).lower() in ('1', 'true', 'yes')
                    if not use_closed:
                        realtime_price_data = {
                            'open': realtime_info.get('시가'),
                            'high': realtime_info.get('고가'),
                            'low': realtime_info.get('저가'),
                            'close': realtime_info.get('현재가'),
                            'volume': realtime_info.get('누적거래량'),
                        }
                        df.loc[today_date] = pd.Series(realtime_price_data)
                except Exception:
                    pass

                # reuse original RSI computation on df
                s = df['close'].astype('float64')
                period = int(self.RSI_PERIOD)
                try:
                    min_periods = int(os.getenv('RSI_MIN_PERIODS', str(period)))
                except Exception:
                    min_periods = period
                method = getattr(self, 'RSI_METHOD', 'cutler')
                method = method.lower() if isinstance(method, str) else 'cutler'

                rsi = compute_rsi(s, period=period, min_periods=min_periods, method=method)

                df[f'RSI({self.RSI_PERIOD})'] = rsi
                return df, float(close)

            logger.warning("No price data available for RSI calculation: %s", code)
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

            # 시간 손절: 매수일 기준 N일 초과 시 강제 매도
            purchase_date_str = self.kiwoom.balance[code].get('매수일')
            if not purchase_date_str:
                # 매수일 누락 시 DB에서 즉시 복원하여 시간 손절이 무력화되지 않도록 보정
                try:
                    restored_purchase_date = get_purchase_date(code)
                    if restored_purchase_date:
                        self.kiwoom.balance[code]['매수일'] = restored_purchase_date
                        purchase_date_str = restored_purchase_date
                        logger.info("매수일 실시간 복원: %s -> %s", code, restored_purchase_date)
                except Exception as e:
                    logger.warning("매수일 DB 복원 실패 (%s): %s", code, e)

            if purchase_date_str:
                try:
                    purchase_date = datetime.strptime(purchase_date_str, '%Y%m%d').date()
                    holding_days = (get_korea_time().date() - purchase_date).days
                    if holding_days > self.TIME_STOP_LOSS_DAYS:
                        name = self.resolve_stock_name(code)
                        display = f"{name}({code})" if name else code
                        logger.info(
                            "시간 손절 발생: %s (보유일=%d일, 기준=%d일, 매입가=%d원)",
                            display, holding_days, self.TIME_STOP_LOSS_DAYS, purchase_price
                        )
                        self._last_sell_reason = "TIME_STOP_LOSS"
                        return True
                except Exception as e:
                    logger.warning("매수일 파싱 오류 (%s): %s", code, e)

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

            # 목표 가격 계산: 손익분기점 대비 목표 수익률 충족
            target_price = math.ceil(breakeven_price * (1 + (self.PROFIT_TARGET_PERCENT / 100)))

            # 로그: RSI 및 조건 여부
            condition_met = (
                rsi > self.RSI_SELL_THRESHOLD
                and close > breakeven_price
                and close >= target_price
            )
            logger.info(
                "check_sell_signal %s RSI=%.2f close=%d breakeven=%d target_price=%d target_pct=%.2f condition=%s",
                display,
                rsi,
                close,
                breakeven_price,
                target_price,
                self.PROFIT_TARGET_PERCENT,
                condition_met,
            )
            
            # 매도 조건: RSI 과열+손익분기점 돌파 AND 목표가(손익분기점 기준) 도달
            if condition_met:
                estimated_profit_rate = ((close - breakeven_price) / purchase_price) * 100
                logger.info("매도 신호 발생: %s (RSI=%.2f, close=%d, purchase=%d, breakeven=%d, 예상수익률=%.2f%%)", 
                           display, rsi, close, purchase_price, breakeven_price, estimated_profit_rate)
                self._last_sell_reason = "RSI_SIGNAL"
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

            name = self.resolve_stock_name(code)
            display = f"{name}({code})" if name else code

            ask = self._get_order_price(
                code,
                display,
                primary_key='(최우선)매도호가',
                price_label='매도호가',
                fallback_keys=('현재가', '(최우선)매수호가'),
            )
            if ask is None:
                return

            order_result = self.kiwoom.send_order('send_sell_order', '1001', 1, code, quantity, ask, '00')

            # 주문 결과 확인 (딕셔너리 형식)
            if order_result.get('success'):
                # 매도 체결 시 예상 수령액 계산 (수수료 + 증권거래세 차감)
                sell_amount = quantity * ask
                estimated_proceeds = math.floor(sell_amount / self.SELL_FEE_RATE)  # 실제 수령액
                total_fee = sell_amount - estimated_proceeds  # 총 비용
                purchase_price = self.kiwoom.balance[code].get('매입가', 0)
                purchase_amount = purchase_price * quantity
                estimated_profit = estimated_proceeds - purchase_amount
                estimated_profit_rate = (estimated_profit / purchase_amount * 100) if purchase_amount > 0 else 0.0
                
                # send_order()에서 이미 self.kiwoom.order[code]를 설정함
                # 웹소켓 응답이 오면 자동으로 업데이트되고, 체결 완료 시 자동 삭제됨

                message = "📉 <b>매도 주문 접수</b>\n종목: {}\n주문번호: {}\n수량: {}주\n가격: {:,}원\n예상수령: {:,}원 (수수료+세금: {:,}원)\n예상수익률: {:.2f}%".format(
                    display, order_result.get('order_no', 'N/A'), quantity, ask, estimated_proceeds, total_fee, estimated_profit_rate)
                logger.info(message)
                self._queue_or_send(message)

                # 매매 이력 CSV 기록
                trade_logger.log_trade(
                    mode='mock' if self.kiwoom.mock else 'real',
                    action='SELL',
                    code=code,
                    name=name or code,
                    price=ask,
                    quantity=quantity,
                    fee=total_fee,
                    net_amount=estimated_proceeds,
                    purchase_price=purchase_price,
                    profit=estimated_profit,
                    profit_rate=estimated_profit_rate,
                    sell_reason=self._last_sell_reason,
                    order_no=str(order_result.get('order_no', '')),
                )
            else:
                error_code = order_result.get('error_code', 'UNKNOWN')
                error_message = order_result.get('error_message', '알 수 없는 오류')
                name = self.resolve_stock_name(code)
                display = f"{name}({code})" if name else code
                error_msg = "❌ <b>매도 주문 실패</b>\n종목: {}\n수량: {}주\n가격: {:,}원\n오류코드: {}\n오류메시지: {}".format(
                    display, quantity, ask, error_code, html.escape(error_message))
                logger.error("매도 주문 실패: 종목=%s, error_code=%s, error_msg=%s", display, error_code, error_message)
                self._queue_or_send(error_msg)
            
        except KeyError as e:
            code_name = self.resolve_stock_name(code)
            logger.error("매도 주문 처리 중 키 오류 %s(%s): %s", code_name, code, e)
        except Exception as e:
            code_name = self.resolve_stock_name(code)
            logger.error("매도 주문 처리 중 예상치 못한 오류 %s(%s): %s", code_name, code, e)

    def _get_order_price(self, code, display, primary_key, price_label, fallback_keys=()):
        """주문 가격 조회 시 실시간 호가가 없으면 현재가로 제한적으로 폴백합니다."""
        rt_info = self.kiwoom.universe_realtime_transaction_info.get(code)
        if not isinstance(rt_info, dict):
            logger.error("실시간 체결정보가 없습니다: %s", display)
            return None

        for key in (primary_key, *fallback_keys):
            value = rt_info.get(key)
            try:
                price = int(value)
            except (TypeError, ValueError):
                continue

            if price <= 0:
                continue

            if key != primary_key:
                source_label = '폴링 현재가' if key == '현재가' and rt_info.get('_from_polling') else key
                logger.warning("%s가 없어 %s로 대체합니다: %s (%d원)", price_label, source_label, display, price)
            return price

        logger.error("%s 정보를 가져올 수 없습니다: %s", price_label, display)
        return None

    @notify_on_exception(fallback_return=None)
    def check_buy_signal_and_order(self, code):
        """매수 대상인지 확인하고 주문을 접수하는 함수"""
        # 운영 안전장치: 신규 매수 중단 모드
        if self.pause_new_buys:
            try:
                today = get_korea_time().strftime('%Y%m%d')
            except Exception:
                today = None
            if today and self._buy_pause_log_date != today:
                self._buy_pause_log_date = today
                logger.warning("신규 매수가 중단되어 매수 신호 검사를 건너뜁니다 (RSI_PAUSE_NEW_BUYS=1)")
            return False

        # 매수 가능 시간 확인
        # 기본: 오후 14:50~15:20 허용
        # fallback: 오전 09:00~09:20 (오후 윈도우를 놓쳤을 때만)
        try:
            from util.time_helper import is_buy_window_open, is_morning_buy_fallback_window
        except Exception:
            # 호환성: 모듈 임포트 실패 시 기존 함수를 시도
            try:
                from util.time_helper import check_adjacent_transaction_closed as is_buy_window_open
            except Exception:
                is_buy_window_open = lambda: False
            is_morning_buy_fallback_window = lambda: False

        in_afternoon_window = is_buy_window_open()
        in_morning_fallback = is_morning_buy_fallback_window() and not self.buy_window_done_today

        if not in_afternoon_window and not in_morning_fallback:
            return False

        if in_afternoon_window:
            self.buy_window_done_today = True

        # RSI 계산 (공통 함수 사용)
        df, close = self.calculate_rsi(code)
        
        if df is None or close is None:
            return False

        # 오전 fallback 전용: 갭 확인
        # 전일 종가 대비 금일 시가(또는 현재가)가 +3% 이상 갭업이면 신호 소멸로 매수 신호 검사 보류
        # 갭다운이면 신호 강화로 진행 (로그만 기록)
        if in_morning_fallback:
            try:
                # 스냅샷 우선 사용 (없으면 라이브 데이터 폴백)
                rt_source = self._rt_snapshot if self._rt_snapshot else self.kiwoom.universe_realtime_transaction_info
                rt_info = rt_source.get(code, {})
                # 시가 우선, 없으면 현재가로 대체
                today_open = rt_info.get('시가') or rt_info.get('현재가')
                # df[-2]는 전일 종가 (df[-1]은 calculate_rsi에서 appended된 현재가)
                prev_close = float(df['close'].iloc[-2]) if len(df) >= 2 else None

                if today_open and prev_close and prev_close != 0:
                    gap_pct = (float(today_open) - prev_close) / prev_close * 100
                    _name = self.resolve_stock_name(code)
                    _display = f"{_name}({code})" if _name else code
                    if gap_pct >= self.MORNING_FALLBACK_GAP_UP_THRESHOLD:
                        # 갭업: 당일 이미 반등 -> 매수 시그널(검사) 소멸
                        logger.info("오전 fallback 갭업으로 매수 신호 검사 보류 %s: gap=%.2f%% (기준=%.1f%%)", _display, gap_pct, self.MORNING_FALLBACK_GAP_UP_THRESHOLD)
                        return False
                    logger.info("오전 fallback 갭 확인 통과 %s: gap=%.2f%% -> 매수 신호 검사 진행", _display, gap_pct)
            except Exception as _gap_err:
                logger.warning("오전 fallback 갭 확인 중 오류 (%s): %s — 갭 확인 건너뜀", code, _gap_err)
        
        try:
            # DataFrame 길이 체크
            if len(df) < 3:
                logger.warning("데이터가 부족합니다 (%s): len=%d", code, len(df))
                return False
            
            # 이동평균은 compact ma_latest 우선 사용, 없으면 rolling으로 계산
            uni_item = self.universe.get(code, {})
            ma_latest = uni_item.get('ma_latest', {})
            ma20 = ma_latest.get('ma20')
            ma60 = ma_latest.get('ma60')
            ma200 = ma_latest.get('ma200')

            if ma20 is None or ma60 is None or ma200 is None:
                df['ma20'] = df['close'].rolling(window=self.MA_SHORT, min_periods=self.MA_SHORT).mean()
                df['ma60'] = df['close'].rolling(window=self.MA_LONG, min_periods=self.MA_LONG).mean()
                df['ma200'] = df['close'].rolling(window=self.MA_TREND, min_periods=self.MA_TREND).mean()
                ma20 = df['ma20'].iloc[-1]
                ma60 = df['ma60'].iloc[-1]
                ma200 = df['ma200'].iloc[-1]
            else:
                # ensure numeric type
                try:
                    ma20 = float(ma20)
                except Exception:
                    ma20 = float('nan')
                try:
                    ma60 = float(ma60)
                except Exception:
                    ma60 = float('nan')
                try:
                    ma200 = float(ma200)
                except Exception:
                    ma200 = float('nan')

            rsi = df.iloc[-1:][f'RSI({self.RSI_PERIOD})'].values[0]

            # display name for logging
            name = self.resolve_stock_name(code)
            display = f"{name}({code})" if name else code
            
            # 로그: RSI 및 지표값 
            logger.info("check_buy_signal %s RSI=%.2f ma20=%.2f ma60=%.2f ma200=%.2f", display, rsi, ma20, ma60, ma200)

            # 신규 상장 등으로 MA200이 형성되지 않은 경우는 정상 스킵으로 처리
            try:
                close_count = int(uni_item.get('close_count', 0) or 0)
            except Exception:
                close_count = 0
            if close_count <= 0:
                try:
                    close_count = int(df['close'].astype(float).notna().sum())
                except Exception:
                    close_count = 0

            if not np.isfinite(ma200) and close_count < self.MA_TREND:
                logger.info(
                    "MA200 미형성으로 매수 신호 스킵 (%s): close_count=%d, required=%d",
                    display,
                    close_count,
                    self.MA_TREND,
                )
                self._record_ma200_skip(code)
                return False
            
            # 값들이 유효한지 체크
            if np.isnan(rsi) or np.isinf(rsi) or np.isnan(ma20) or np.isinf(ma20) or np.isnan(ma60) or np.isinf(ma60) or np.isnan(ma200) or np.isinf(ma200):
                logger.warning("계산된 값이 유효하지 않습니다 (%s): rsi=%s, ma20=%s, ma60=%s, ma200=%s", display, rsi, ma20, ma60, ma200)
                return False
            
            # compact 구조를 사용하므로 위치 기반으로 2거래일 전 종가를 취득
            if len(df) < 3:
                logger.warning("2 거래일 전 데이터 접근 불가 (%s): len=%d", display, len(df))
                return False
            close_2days_ago = df['close'].iloc[-3]
            
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

            bid = self._get_order_price(
                code,
                display,
                primary_key='(최우선)매수호가',
                price_label='매수호가',
                fallback_keys=('현재가', '(최우선)매도호가'),
            )
            if bid is None:
                return

            # (6)주문 수량 계산(소수점은 제거하기 위해 버림)
            # 단일 종목 비중 캡(MAX_POSITION_RATIO)을 적용하여 주문 수량을 제한
            try:
                base_qty = math.floor(budget / bid)
                cap_qty = math.floor((self.deposit * self.MAX_POSITION_RATIO) / bid)
                # cap_qty가 0이면 캡으로 인해 매수 불가
                if cap_qty < 1:
                    logger.info("단일 종목 비중 캡으로 매수 보류: cap_qty=0, cap_ratio=%.4f", self.MAX_POSITION_RATIO)
                    return
                quantity = min(base_qty, cap_qty)
            except Exception as _cap_err:
                logger.warning("단일 종목 비중 캡 적용 중 오류: %s — 캡 미적용으로 기본 수량 사용", _cap_err)
                quantity = math.floor(budget / bid)

            # (7)주문 주식 수량이 1 미만이라면 매수 불가하므로 체크
            if quantity < 1:
                logger.info("주문 수량 부족 (quantity < 1): budget=%d, bid=%d", budget, bid)
                return

            # (8)예수금 충분한지 미리 체크 (매수 수수료 포함)
            amount = quantity * bid
            estimated_cost = math.floor(amount * self.BUY_FEE_RATE)
            total_cost = estimated_cost - amount
            
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
                message = "📈 <b>매수 주문 접수</b>\n종목: {}\n주문번호: {}\n수량: {}주\n가격: {:,}원\n예상비용(수수료+세금): {:,}원\n예수금: {:,}원".format(
                    display, order_result.get('order_no', 'N/A'), quantity, bid, total_cost, self.deposit)
                logger.info(message)
                self._queue_or_send(message)

                # 매매 이력 CSV 기록
                trade_logger.log_trade(
                    mode='mock' if self.kiwoom.mock else 'real',
                    action='BUY',
                    code=code,
                    name=name or code,
                    price=bid,
                    quantity=quantity,
                    fee=total_cost,
                    net_amount=estimated_cost,
                    order_no=str(order_result.get('order_no', '')),
                )
            else:
                error_code = order_result.get('error_code', 'UNKNOWN')
                error_message = order_result.get('error_message', '알 수 없는 오류')
                name = self.resolve_stock_name(code)
                display = f"{name}({code})" if name else code
                error_msg = "❌ <b>매수 주문 실패</b>\n종목: {}\n수량: {}주\n가격: {:,}원\n오류코드: {}\n오류메시지: {}".format(
                    display, quantity, bid, error_code, html.escape(error_message))
                logger.error("매수 주문 실패: 종목=%s, error_code=%s, error_msg=%s", display, error_code, error_message)
                self._queue_or_send(error_msg)
                
                # 모의투자 매매제한 종목(RC4007) 감지 및 블랙리스트 추가
                if self.kiwoom.mock and 'RC4007' in error_message:
                    code_name = self.universe.get(code, {}).get('code_name', code)
                    self.add_to_mock_blacklist(code, code_name, html.escape(error_message))

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
