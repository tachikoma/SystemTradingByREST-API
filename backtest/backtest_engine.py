"""
RSI 전략 백테스트 엔진

RSIStrategy의 매매 로직을 재현하여 과거 데이터로 백테스트를 수행합니다.

주의: 
- RSI 계산 방식은 RSIStrategy와 동일하게 'cutler' (SMA) 또는 'wilder' (EWMA) 선택 가능
- 거래 비용 계산은 RSIStrategy와 동일: BUY_FEE_RATE, SELL_FEE_RATE 분리 적용
- 현금 보유 비율(CASH_RESERVE_RATIO) 적용: 투자 가능 금액의 20%를 현금으로 유지
"""

import pandas as pd
import numpy as np
import math
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
from util.logging_config import get_logger
from util.rsi_calc import compute_rsi
import os

logger = get_logger(__name__)


def _prev_yyyymm(yyyymm: str) -> str:
    """YYYYMM 문자열의 이전 월을 반환합니다."""
    year = int(yyyymm[:4])
    month = int(yyyymm[4:6])
    if month == 1:
        return f"{year - 1}12"
    return f"{year}{month - 1:02d}"


class BacktestEngine:
    """백테스트 실행 엔진"""

    DEFAULT_INITIAL_CAPITAL = 10_000_000
    DEFAULT_RSI_SELL_THRESHOLD = 70.0
    DEFAULT_PROFIT_TARGET_PERCENT = 10.0
    DEFAULT_RSI_BUY_THRESHOLD = 3.0
    DEFAULT_CASH_RESERVE_RATIO = 0.2
    DEFAULT_COMMISSION_RATE_MOCK = 0.0035
    DEFAULT_COMMISSION_RATE_REAL = 0.00015
    DEFAULT_TAX_RATE_MOCK = 0.0000
    DEFAULT_TAX_RATE_REAL = 0.0020
    DEFAULT_RSI_METHOD = 'wilder'
    DEFAULT_TIME_STOP_LOSS_DAYS = 90
    DEFAULT_SLIPPAGE_BUY = 0.002   # 0.2% 매수 슬리피지 (체결 불리)
    DEFAULT_SLIPPAGE_SELL = 0.002  # 0.2% 매도 슬리피지 (체결 불리)
    
    def __init__(
        self,
        initial_capital: Optional[float] = None,  # env: INITIAL_CAPITAL, 기본값: DEFAULT_INITIAL_CAPITAL
        max_holdings: int = 10,  # 최대 보유 종목 수
        rsi_period: int = 2,  # RSI 계산 기간
        ma_short: int = 20,  # 단기 이동평균
        ma_long: int = 60,  # 장기 이동평균
        ma_trend: int = 200,  # 장기 추세 이동평균 (필터용)
        # 전략 파라미터: None → .env → 엔진 내부 기본값 순으로 적용
        # 명시적으로 값을 전달하면 .env를 무시하고 해당 값이 최우선 적용됨
        rsi_sell_threshold: Optional[float] = None,    # env: RSI_SELL_THRESHOLD, 기본값: DEFAULT_RSI_SELL_THRESHOLD
        profit_target_percent: float = None, # env: PROFIT_TARGET_PERCENT, 기본값: DEFAULT_PROFIT_TARGET_PERCENT
        rsi_buy_threshold: float = None,     # env: RSI_BUY_THRESHOLD, 기본값: DEFAULT_RSI_BUY_THRESHOLD
        price_drop_threshold: float = -5.0,  # 가격 하락 기준 (%) (최적화된 값)
        cash_reserve_ratio: float = None,    # env: CASH_RESERVE_RATIO, 기본값: DEFAULT_CASH_RESERVE_RATIO
        commission_rate: float = None,       # env: TRADING_FEE_PERCENT_REAL, 기본값: 실전 기본 수수료
        tax_rate: float = None,              # env: TRADING_TAX_PERCENT_REAL, 기본값: 실전 기본 거래세
        rsi_method: str = None,              # env: RSI_METHOD, 기본값: DEFAULT_RSI_METHOD
        rsi_min_periods: int = None,         # None이면 rsi_period 사용
        # 손절 파라미터 (백테스트 결과: 손절 없음이 최고 성능)
        enable_stop_loss: bool = False,     # 가격 손절 비활성화 (최적화 기본값)
        price_stop_loss_pct: float = -20.0,  # 가격 손절 기준 (%)
        enable_time_stop_loss: bool = False, # 시간 손절 비활성화 (RSI>70+any-profit sell이 더 효과적)
        time_stop_loss_days: Optional[int] = None,  # env: TIME_STOP_LOSS_DAYS, 기본값: DEFAULT_TIME_STOP_LOSS_DAYS
        slippage_buy: float = None,              # env: SLIPPAGE_BUY, 기본값: DEFAULT_SLIPPAGE_BUY
        slippage_sell: float = None,             # env: SLIPPAGE_SELL, 기본값: DEFAULT_SLIPPAGE_SELL
        symbol_names: Dict[str, str] = None,
        # RSI 매도 모드 (approach #3): 'above'=RSI>threshold 즉시 매도, 'cross'=RSI 하향 돌파 시 매도
        rsi_sell_mode: str = 'above',
        rsi_cross_exit_threshold: float = 50.0,
        # 마켓 타이밍 필터 (approach #4): {인덱스코드: DataFrame(close 포함)}
        index_data: Dict[str, pd.DataFrame] = None,
        market_filter_enabled: bool = False,
        market_filter_kospi_code: str = '229200',
        market_filter_kosdaq_code: str = '381180',
        market_filter_ma_period: int = 200,
        # 레짐 필터 (B+C): KOSPI 지수 MA 기반 시장 국면 — 하락장에서 진입 회피
        regime_filter_enabled: bool = False,
        regime_ma_period: int = 120,
        # 진입 가격 필터: 최근 N일 저점 대비 X% 이내에서만 매수 (mean reversion 확인)
        entry_price_filter_enabled: bool = False,
        entry_price_filter_pct: float = 3.0,
        entry_price_filter_lookback: int = 5,
        # MA 필터 활성화/비활성화 (approach A): 추세 필터 제거 실험용
        use_ma20_filter: bool = True,   # MA20 > MA60 조건 사용
        use_ma200_filter: bool = True,  # Close > MA200 조건 사용
        # 신호 강도 기반 포지셔닝: RSI가 낮을수록 더 많은 자본 배분
        use_signal_strength_positioning: bool = False,
        # 신호 강도 지수 승수: 1.0=선형, 2.0=제곱, 3.0=세제곱 (강한 과매도에 더 집중)
        signal_strength_exponent: float = 1.0,
    ):
        self.max_holdings = max_holdings
        self.rsi_period = rsi_period
        self.ma_short = ma_short
        self.ma_long = ma_long
        self.ma_trend = ma_trend
        self.price_drop_threshold = price_drop_threshold
        self.rsi_min_periods = rsi_min_periods if rsi_min_periods is not None else rsi_period

        # --- 우선순위 적용 헬퍼: 명시적 파라미터 > .env > 엔진 내부 기본값 ---
        def _resolve_float(explicit, env_name, fallback):
            """명시적 값이 있으면 그것을 쓰고, 없으면 env, 그것도 없으면 fallback 사용"""
            if explicit is not None:
                return float(explicit)
            v = os.getenv(env_name)
            return float(v) if v is not None else fallback

        def _resolve_int(explicit, env_name, fallback):
            """명시적 값이 있으면 그것을 쓰고, 없으면 env, 그것도 없으면 fallback 사용"""
            if explicit is not None:
                return int(explicit)
            v = os.getenv(env_name)
            return int(v) if v is not None else fallback

        self.initial_capital = _resolve_float(initial_capital, 'INITIAL_CAPITAL', self.DEFAULT_INITIAL_CAPITAL)
        self.rsi_sell_threshold   = _resolve_float(rsi_sell_threshold, 'RSI_SELL_THRESHOLD', self.DEFAULT_RSI_SELL_THRESHOLD)
        self.profit_target_percent = _resolve_float(profit_target_percent, 'PROFIT_TARGET_PERCENT', self.DEFAULT_PROFIT_TARGET_PERCENT)
        self.rsi_buy_threshold    = _resolve_float(rsi_buy_threshold, 'RSI_BUY_THRESHOLD', self.DEFAULT_RSI_BUY_THRESHOLD)

        # CASH_RESERVE_RATIO: 퍼센트(20) 또는 소수(0.2) 두 형식 모두 허용
        if cash_reserve_ratio is not None:
            self.cash_reserve_ratio = float(cash_reserve_ratio)
        else:
            v = os.getenv('CASH_RESERVE_RATIO')
            if v is not None:
                tmp = float(v)
                self.cash_reserve_ratio = tmp / 100.0 if tmp > 1 else tmp
            else:
                self.cash_reserve_ratio = self.DEFAULT_CASH_RESERVE_RATIO

        # 단일 종목 최대 비중 비율 (예: 0.05 또는 5 -> 0.05)
        v = os.getenv('MAX_POSITION_RATIO')
        try:
            if v is not None:
                tmp = float(v)
                self.max_position_ratio = tmp / 100.0 if tmp > 1 else tmp
            else:
                self.max_position_ratio = 0.05
        except Exception:
            self.max_position_ratio = 0.05

        # RSI 계산 방식
        _rsi_method = rsi_method if rsi_method is not None else os.getenv('RSI_METHOD', self.DEFAULT_RSI_METHOD)
        _rsi_method = _rsi_method.strip().lower() if isinstance(_rsi_method, str) else self.DEFAULT_RSI_METHOD
        if _rsi_method not in ('cutler', 'wilder'):
            logger.warning(f"Invalid RSI method '{_rsi_method}', using '{self.DEFAULT_RSI_METHOD}'")
            _rsi_method = self.DEFAULT_RSI_METHOD
        self.rsi_method = _rsi_method

        # 거래 비용: 백테스트는 항상 실전(real) 비용 체계를 사용한다.
        # 명시적 파라미터 > TRADING_*_REAL env > 실전 기본값
        default_commission_rate = self.DEFAULT_COMMISSION_RATE_REAL
        default_tax_rate = self.DEFAULT_TAX_RATE_REAL

        def _parse_percent_env(val):
            """퍼센트 표기(0.35 → 0.0035) 변환"""
            if val is None:
                return None
            try:
                return float(val) / 100.0
            except Exception:
                return None

        if commission_rate is not None:
            self.commission_rate = float(commission_rate)
        else:
            parsed = _parse_percent_env(os.getenv('TRADING_FEE_PERCENT_REAL'))
            self.commission_rate = parsed if parsed is not None else default_commission_rate

        if tax_rate is not None:
            self.tax_rate = float(tax_rate)
        else:
            parsed = _parse_percent_env(os.getenv('TRADING_TAX_PERCENT_REAL'))
            self.tax_rate = parsed if parsed is not None else default_tax_rate

        self.buy_fee_rate  = 1 + self.commission_rate
        self.sell_fee_rate = 1 + self.commission_rate + self.tax_rate

        # 손절 설정
        self.enable_stop_loss      = enable_stop_loss
        self.price_stop_loss_pct   = price_stop_loss_pct
        self.enable_time_stop_loss = enable_time_stop_loss
        self.time_stop_loss_days   = _resolve_int(time_stop_loss_days, 'TIME_STOP_LOSS_DAYS', self.DEFAULT_TIME_STOP_LOSS_DAYS)

        # 슬리피지 설정 (매수 시 불리하게 적용: 가격 상승, 매도 시 불리하게 적용: 가격 하락)
        self.slippage_buy  = _resolve_float(slippage_buy,  'SLIPPAGE_BUY',  self.DEFAULT_SLIPPAGE_BUY)
        self.slippage_sell = _resolve_float(slippage_sell, 'SLIPPAGE_SELL', self.DEFAULT_SLIPPAGE_SELL)

        # RSI 매도 모드 (approach #3): env RSI_SELL_MODE 로 오버라이드
        env_sell_mode = os.getenv('RSI_SELL_MODE', '').strip().lower()
        self.rsi_sell_mode = env_sell_mode if env_sell_mode in ('above', 'cross') else rsi_sell_mode
        self.rsi_cross_exit_threshold = rsi_cross_exit_threshold

        # 마켓 타이밍 필터 (approach #4)
        self.index_data = index_data or {}
        self.market_filter_enabled = market_filter_enabled
        self.market_filter_kospi_code = market_filter_kospi_code
        self.market_filter_kosdaq_code = market_filter_kosdaq_code
        self.market_filter_ma_period = market_filter_ma_period

        # 레짐 필터 (B+C): KOSPI MA 기반 시장 국면 — 하락장에서 진입 회피
        self.regime_filter_enabled = regime_filter_enabled
        self.regime_ma_period = regime_ma_period

        # 진입 가격 필터
        self.entry_price_filter_enabled = entry_price_filter_enabled
        self.entry_price_filter_pct = entry_price_filter_pct
        self.entry_price_filter_lookback = entry_price_filter_lookback

        # MA 필터 토글 (approach A)
        self.use_ma20_filter = use_ma20_filter
        self.use_ma200_filter = use_ma200_filter

        # 신호 강도 기반 포지셔닝
        self.use_signal_strength_positioning = use_signal_strength_positioning
        self.signal_strength_exponent = signal_strength_exponent

        # 포트폴리오 상태
        self.cash = self.initial_capital
        self.symbol_names = symbol_names or {}
        self.holdings: Dict[str, Dict] = {}  # {code: {'quantity': int, 'avg_price': float, 'buy_date': str}}

        # 거래 기록
        self.trades: List[Dict] = []
        self.daily_portfolio_value: List[Dict] = []
        self.stop_loss_count = 0  # 손절 횟수
        
    def calculate_rsi(self, prices: pd.Series, period: int = None) -> pd.Series:
        """RSI 계산 — util.rsi_calc.compute_rsi 위임
        
        Args:
            prices: 종가 시계열
            period: RSI 기간 (기본값은 self.rsi_period)
            
        Returns:
            RSI 시계열
        """
        if period is None:
            period = self.rsi_period
        return compute_rsi(
            prices=prices.astype('float64'),
            period=period,
            min_periods=self.rsi_min_periods,
            method=self.rsi_method,
        )
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """기술적 지표 계산
        
        Args:
            df: OHLCV 데이터프레임
            
        Returns:
            지표가 추가된 데이터프레임
        """
        df = df.copy()
        
        # RSI 계산 (Wilder smoothing). 두 컬럼을 모두 만들어 RSIStrategy와 호환 유지
        rsi_series = self.calculate_rsi(df['close'], self.rsi_period)
        df[f'RSI({self.rsi_period})'] = rsi_series
        df['rsi'] = rsi_series
        
        # 이동평균 계산
        df['ma20'] = df['close'].rolling(window=self.ma_short, min_periods=self.ma_short).mean()
        df['ma60'] = df['close'].rolling(window=self.ma_long, min_periods=self.ma_long).mean()
        df['ma200'] = df['close'].rolling(window=self.ma_trend, min_periods=self.ma_trend).mean()
        
        return df
    
    def check_market_filter(self, code: str, date: str) -> bool:
        """마켓 타이밍 필터: 인덱스(ETF)가 200MA 이상일 때만 매수 허용

        index_data에 229200(KOSPI200)과 381180(KOSDAQ150)이 전달되면,
        각각의 200MA를 계산하여 해당 날짜에 close > 200MA 조건을 충족하는지 확인합니다.
        코드 분류 없이 모든 종목에 대해 KOSPI200 필터를 적용하고,
        KOSDAQ150은 추가 필터로 함께 적용합니다.
        """
        if not self.market_filter_enabled or not self.index_data:
            return True

        for idx_code, idx_df in self.index_data.items():
            if date not in idx_df.index:
                return False
            idx = idx_df.index.get_loc(date)
            if idx < self.market_filter_ma_period:
                return False
            close = idx_df.iloc[idx]['close']
            ma200 = idx_df['close'].iloc[idx - self.market_filter_ma_period:idx].mean()
            if close <= ma200:
                logger.debug(
                    "마켓필터 차단 %s code=%s date=%s idx_close=%d ma200=%.0f",
                    idx_code, code, date, close, ma200
                )
                return False
        return True

    def check_regime_filter(self, code: str, date: str) -> bool:
        """레짐 필터 (B+C): KOSPI 지수가 이동평균 이상일 때만 매수 허용

        기존 market_filter (ETF MA200)와 별개로, configurable MA period로
        시장 국면(상승장/하락장)을 판단합니다. 하락장(close < MA)에서는 매수 차단.
        """
        if not self.regime_filter_enabled or not self.index_data:
            return True

        kospi_code = self.market_filter_kospi_code
        idx_df = self.index_data.get(kospi_code)
        if idx_df is None:
            return True
        if date not in idx_df.index:
            return False
        idx = idx_df.index.get_loc(date)
        if idx < self.regime_ma_period:
            return False
        close = idx_df.iloc[idx]['close']
        ma = idx_df['close'].iloc[idx - self.regime_ma_period:idx].mean()
        if close <= ma:
            logger.debug(
                "레짐필터 차단 %s code=%s date=%s idx_close=%d ma%d=%.0f",
                kospi_code, code, date, close, self.regime_ma_period, ma
            )
            return False
        return True

    def check_buy_signal(
        self, 
        code: str, 
        date: str, 
        df: pd.DataFrame,
        current_holdings_count: int
    ) -> Tuple[bool, Optional[float]]:
        """매수 신호 확인
        
        Args:
            code: 종목 코드
            date: 현재 날짜
            df: 해당 종목의 OHLCV + 지표 데이터
            current_holdings_count: 현재 보유 종목 수
            
        Returns:
            (매수 신호 여부, 매수 가격)
        """
        try:
            # 최대 보유 종목 수 체크
            if current_holdings_count >= self.max_holdings:
                return False, None
            
            # 날짜 인덱스 확인
            if date not in df.index:
                return False, None
            
            idx = df.index.get_loc(date)
            
            # 최소 데이터 요구사항 체크 (2일 전 데이터 필요)
            if idx < 2:
                return False, None
            
            # 현재 데이터
            current = df.iloc[idx]
            close = current['close']
            rsi = current['rsi']
            ma20 = current['ma20']
            ma60 = current['ma60']
            ma200 = current['ma200']

            # display (name(code)) for logging
            display = f"{self.symbol_names.get(code)}({code})" if self.symbol_names.get(code) else code
            logger.debug("check_buy_signal %s date=%s rsi=%.2f ma20=%.2f ma60=%.2f ma200=%.2f", display, date, rsi, ma20, ma60, ma200)
            
            # 2거래일 전 종가
            close_2days_ago = df.iloc[idx - 2]['close']
            
            # 값 유효성 체크
            if np.isnan(ma200) and (idx + 1) < self.ma_trend:
                logger.debug(
                    "MA200 미형성으로 매수 신호 스킵 %s date=%s close_count=%d required=%d",
                    display,
                    date,
                    idx + 1,
                    self.ma_trend,
                )
                return False, None

            if np.isnan(rsi) or np.isnan(ma20) or np.isnan(ma60) or np.isnan(ma200) or close_2days_ago == 0:
                return False, None
            
            # 가격 변동률 계산
            price_diff = (close - close_2days_ago) / close_2days_ago * 100
            
            # 진입 가격 필터: 최근 N일 저점 대비 X% 이내인지 확인
            if self.entry_price_filter_enabled:
                lookback_start = max(0, idx - self.entry_price_filter_lookback)
                recent_low = df.iloc[lookback_start:idx + 1]['close'].min()
                if close > recent_low * (1 + self.entry_price_filter_pct / 100):
                    logger.debug(
                        "진입가격필터 차단 %s date=%s close=%d recent_low=%.0f filter_pct=%.1f%%",
                        display, date, close, recent_low, self.entry_price_filter_pct
                    )
                    return False, None

            # 매수 조건 확인
            # 1) [선택] ma20 > ma60 (단기 이평 > 장기 이평, use_ma20_filter=True 시)
            # 2) [선택] close > ma200 (장기 추세 상승, use_ma200_filter=True 시)
            # 3) RSI < rsi_buy_threshold (과매도)
            # 4) 2일 전 대비 price_drop_threshold 이상 하락
            ma20_ok = (not self.use_ma20_filter) or (ma20 > ma60)
            ma200_ok = (not self.use_ma200_filter) or (close > ma200)
            if ma20_ok and ma200_ok and rsi < self.rsi_buy_threshold and price_diff < self.price_drop_threshold:
                return True, close
            
            return False, None
            
        except (KeyError, IndexError) as e:
            logger.warning(f"매수 신호 확인 중 오류 ({code}, {date}): {e}")
            return False, None
    
    def check_sell_signal(
        self, 
        code: str, 
        date: str, 
        df: pd.DataFrame,
        avg_purchase_price: float
    ) -> Tuple[bool, Optional[float]]:
        """매도 신호 확인
        
        Args:
            code: 종목 코드
            date: 현재 날짜
            df: 해당 종목의 OHLCV + 지표 데이터
            avg_purchase_price: 평균 매입가
            
        Returns:
            (매도 신호 여부, 매도 가격)
        """
        try:
            if date not in df.index:
                return False, None
            
            idx = df.index.get_loc(date)
            current = df.iloc[idx]
            previous = df.iloc[idx - 1] if idx > 0 else None

            open_price = current['open']
            close = current['close']
            current_rsi = current['rsi']
            prev_rsi = previous['rsi'] if previous is not None else np.nan

            # display (name(code)) for logging
            display = f"{self.symbol_names.get(code)}({code})" if self.symbol_names.get(code) else code
            logger.debug(
                "check_sell_signal %s date=%s prev_rsi=%.2f current_rsi=%.2f open=%d close=%d",
                display,
                date,
                prev_rsi,
                current_rsi,
                open_price,
                close,
            )
            
            # 값 유효성 체크
            if np.isnan(prev_rsi) and np.isnan(current_rsi):
                return False, None
            
            # 매도 시 수수료+세금을 고려한 손익분기점 계산
            # RSIStrategy와 동일: math.ceil() 적용 (가격은 정수)
            breakeven_price = math.ceil(avg_purchase_price * self.sell_fee_rate)

            # 매도 조건 확인
            if self.rsi_sell_mode == 'cross':
                # RSI 하향 돌파 매도: RSI가 threshold 위로 갔다가 exit 이하로 내려올 때
                prev_rsi_valid = not np.isnan(prev_rsi)
                rsi_was_high = prev_rsi_valid and prev_rsi > self.rsi_sell_threshold
                rsi_dropped = current_rsi < self.rsi_cross_exit_threshold
                if rsi_was_high and rsi_dropped and close >= breakeven_price:
                    logger.info(
                        "RSI 하향돌파 매도 %s date=%s prev_rsi=%.2f curr_rsi=%.2f close=%d breakeven=%d",
                        display, date, prev_rsi, current_rsi, close, breakeven_price
                    )
                    self._last_sell_reason = "RSI_CROSS"
                    return True, close
            else:
                # RSI 과매수 즉시 매도: RSI가 threshold 초과 시 breakeven 이상이면 매도
                if current_rsi > self.rsi_sell_threshold and close >= breakeven_price:
                    # profit_target_percent > 0 이면 최소 수익 조건으로 사용
                    if self.profit_target_percent is not None and self.profit_target_percent > 0:
                        profit_pct = (close - avg_purchase_price) / avg_purchase_price * 100
                        if profit_pct < self.profit_target_percent:
                            return False, None
                    return True, close
            
            return False, None
            
        except (KeyError, IndexError) as e:
            logger.warning(f"매도 신호 확인 중 오류 ({code}, {date}): {e}")
            return False, None
    
    def check_stop_loss(
        self,
        code: str,
        date: str,
        df: pd.DataFrame,
        avg_purchase_price: float,
        buy_date: str
    ) -> Tuple[bool, Optional[float], str]:
        """손절 조건 확인
        
        Args:
            code: 종목 코드
            date: 현재 날짜
            df: 해당 종목의 OHLCV + 지표 데이터
            avg_purchase_price: 평균 매입가
            buy_date: 매수 날짜
            
        Returns:
            (손절 신호 여부, 매도 가격, 손절 사유)
        """
        if not self.enable_stop_loss and not self.enable_time_stop_loss:
            return False, None, ""
        
        try:
            if date not in df.index:
                return False, None, ""
            
            idx = df.index.get_loc(date)
            current = df.iloc[idx]
            close = current['close']
            
            # 1. 가격 손절 체크 (enable_stop_loss가 True일 때만)
            if self.enable_stop_loss:
                price_change_pct = ((close - avg_purchase_price) / avg_purchase_price) * 100
                if price_change_pct <= self.price_stop_loss_pct:
                    return True, close, f"가격손절({price_change_pct:.2f}%)"
            
            # 2. 시간 손절 체크 (enable_time_stop_loss가 True일 때만)
            if self.enable_time_stop_loss:
                buy_date_dt = pd.to_datetime(buy_date, format='%Y%m%d')
                current_date_dt = pd.to_datetime(date, format='%Y%m%d')
                holding_days = (current_date_dt - buy_date_dt).days
                
                if holding_days > self.time_stop_loss_days:
                    return True, close, f"시간손절({holding_days}일)"
            
            return False, None, ""
            
        except (KeyError, IndexError) as e:
            logger.warning(f"손절 확인 중 오류 ({code}, {date}): {e}")
            return False, None, ""
    
    def execute_buy(self, code: str, price: float, date: str, budget: float):
        """매수 주문 실행 (RSIStrategy와 동일한 로직)
        
        Args:
            code: 종목 코드
            price: 매수 가격 (신호 기준 가격; 슬리피지는 내부 적용)
            date: 거래 날짜
            budget: 매수에 사용할 예산
        """
        # 슬리피지 적용: 매수 시 실제 체결 가격은 신호 가격보다 slippage_buy만큼 높음
        execution_price = math.ceil(price * (1 + self.slippage_buy))
        if execution_price <= 0:
            logger.warning(f"{code}: 매수 가격이 0 이하입니다 (price={price}), 매수 스킵")
            return
        # 매수 가능 수량 계산 (RSIStrategy와 동일하게 math.floor 사용)
        quantity = math.floor(budget / execution_price)
        
        if quantity < 1:
            return
        
        # 수수료 포함 실제 매수 금액 (슬리피지 적용 가격 기준, RSIStrategy와 동일하게 math.floor 사용)
        buy_amount = quantity * execution_price
        total_cost = math.floor(buy_amount * self.buy_fee_rate)
        
        # 예산 체크
        if total_cost > self.cash:
            # 예산에 맞게 수량 재조정
            quantity = int((self.cash / self.buy_fee_rate) / execution_price)
            if quantity < 1:
                return
            buy_amount = quantity * execution_price
            total_cost = math.floor(buy_amount * self.buy_fee_rate)
        
        # 현금 차감
        self.cash -= total_cost
        
        # 포지션 추가 또는 업데이트 (avg_price는 슬리피지 적용 가격으로 기록)
        if code in self.holdings:
            # 기존 보유 종목 추가 매수
            old_quantity = self.holdings[code]['quantity']
            old_avg_price = self.holdings[code]['avg_price']
            buy_date = self.holdings[code]['buy_date']  # 최초 매수일 유지
            new_quantity = old_quantity + quantity
            new_avg_price = (old_quantity * old_avg_price + buy_amount) / new_quantity
            
            self.holdings[code] = {
                'quantity': new_quantity,
                'avg_price': new_avg_price,
                'buy_date': buy_date
            }
        else:
            # 신규 매수
            self.holdings[code] = {
                'quantity': quantity,
                'avg_price': execution_price,
                'buy_date': date
            }
        
        # 거래 기록
        commission = total_cost - buy_amount
        self.trades.append({
            'date': date,
            'code': code,
            'type': 'buy',
            'price': price,             # 신호 기준 가격
            'execution_price': execution_price,  # 슬리피지 적용 체결 가격
            'quantity': quantity,
            'amount': buy_amount,
            'commission': commission,
            'total_cost': total_cost
        })
        
        display = f"{self.symbol_names.get(code)}({code})" if self.symbol_names.get(code) else code
        logger.info(f"[{date}] 매수: {display}, 신호가: {price:,.0f}, 체결가: {execution_price:,.0f}(+슬리피지), 수량: {quantity}, 총액: {total_cost:,.0f}")
    
    def execute_sell(self, code: str, price: float, date: str):
        """매도 주문 실행 (RSIStrategy와 동일한 로직)
        
        Args:
            code: 종목 코드
            price: 매도 가격 (신호 기준 가격; 슬리피지는 내부 적용)
            date: 거래 날짜
        """
        if code not in self.holdings:
            return
        
        quantity = self.holdings[code]['quantity']
        avg_price = self.holdings[code]['avg_price']
        
        # 슬리피지 적용: 매도 시 실제 체결 가격은 신호 가격보다 slippage_sell만큼 낮음
        execution_price = math.floor(price * (1 - self.slippage_sell))

        # 수수료 + 거래세 포함 실제 매도 금액 (슬리피지 적용 가격 기준, RSIStrategy와 동일)
        sell_amount = quantity * execution_price
        net_proceeds = math.floor(sell_amount / self.sell_fee_rate)
        
        # 현금 증가
        self.cash += net_proceeds
        
        # 수익률 계산 (매수/매도 수수료 모두 반영)
        # buy_cost: 매수 시 실제 지출 금액 (수수료 포함)
        buy_cost = quantity * avg_price * self.buy_fee_rate
        profit = net_proceeds - buy_cost
        profit_rate = (profit / buy_cost) * 100
        
        # 포지션 제거
        del self.holdings[code]
        
        # 거래 기록
        total_fee = sell_amount - net_proceeds
        fee_denom = self.commission_rate + self.tax_rate
        self.trades.append({
            'date': date,
            'code': code,
            'type': 'sell',
            'price': price,             # 신호 기준 가격
            'execution_price': execution_price,  # 슬리피지 적용 체결 가격
            'quantity': quantity,
            'amount': sell_amount,
            'commission': total_fee * (self.commission_rate / fee_denom) if fee_denom else 0,
            'tax': total_fee * (self.tax_rate / fee_denom) if fee_denom else 0,
            'net_proceeds': net_proceeds,
            'avg_buy_price': avg_price,
            'profit': profit,
            'profit_rate': profit_rate
        })
        
        display = f"{self.symbol_names.get(code)}({code})" if self.symbol_names.get(code) else code
        logger.info(f"[{date}] 매도: {display}, 신호가: {price:,.0f}, 체결가: {execution_price:,.0f}(-슬리피지), "
                   f"수량: {quantity}, 수익: {profit:,.0f} ({profit_rate:.2f}%)")
    
    def calculate_portfolio_value(self, date: str, price_data: Dict[str, pd.DataFrame]) -> float:
        """현재 포트폴리오 가치 계산
        
        Args:
            date: 평가 날짜
            price_data: {종목코드: OHLCV DataFrame} 딕셔너리
            
        Returns:
            총 포트폴리오 가치
        """
        total_value = self.cash
        
        for code, holding in self.holdings.items():
            if code not in price_data:
                continue
            
            df = price_data[code]
            if date not in df.index:
                # 해당 날짜 데이터가 없으면 마지막 가격 사용
                price = df.iloc[-1]['close']
            else:
                price = df.loc[date, 'close']
            
            total_value += holding['quantity'] * price
        
        return total_value
    
    def run_backtest(
        self, 
        price_data: Dict[str, pd.DataFrame],
        start_date: str = None,
        end_date: str = None,
        availability_map: Dict[str, tuple] = None,
        monthly_universe_map: Dict[str, list] = None,
        index_data: Dict[str, pd.DataFrame] = None,
    ) -> Dict:
        """백테스트 실행
        
        Args:
            price_data: {종목코드: OHLCV DataFrame} 딕셔너리
            start_date: 시작 날짜 (YYYYMMDD)
            end_date: 종료 날짜 (YYYYMMDD)
            availability_map: {종목코드: (earliest_yyyymm, latest_yyyymm)} 딕셔너리.
                지정하면 각 거래일마다 해당 날짜에 데이터가 존재하는 종목만 매수 신호 검토.
                생존편향 제거를 위한 워크포워드 백테스트에서 활용합니다.
            monthly_universe_map: {YYYYMM: [code1, code2, ...]} 딕셔너리.
                지정하면 해당 월 스냅샷에 포함된 종목만 매수 신호 검토 (liquidity_rank 순서 유지).
                진짜 월별 워크포워드 유니버스 적용 시 사용합니다.
            index_data: {인덱스코드: DataFrame(close포함)} 딕셔너리.
                지정하면 market_filter_enabled=True일 때 인덱스 200MA 이상에서만 매수 허용.
            
        Returns:
            백테스트 결과 딕셔너리
        """
        logger.info("백테스트 시작...")
        if availability_map:
            logger.info("워크포워드 모드: 종목별 데이터 가용 기간 필터 적용")
        if monthly_universe_map:
            logger.info("워크포워드 모드: 월별 유니버스 스냅샷 필터 적용")
        if index_data is not None:
            # run_backtest() 호출 시 전달된 index_data를 self.index_data에 저장
            self.index_data = index_data
        if self.market_filter_enabled and self.index_data:
            logger.info(
                "마켓 타이밍 필터 적용: %d개 인덱스 200MA 필터",
                len(self.index_data),
            )
        
        # 초기화
        self.cash = self.initial_capital
        self.holdings = {}
        self.trades = []
        self.daily_portfolio_value = []
        self.stop_loss_count = 0
        
        # 지표 계산
        processed_data = {}
        for code, df in price_data.items():
            processed_data[code] = self.calculate_indicators(df)
        
        # 모든 거래일 추출 (모든 종목의 날짜를 합침)
        all_dates = set()
        for df in processed_data.values():
            all_dates.update(df.index)
        
        trading_dates = sorted(list(all_dates))
        
        # 예약 주문 관리: next_date -> [{'type': 'buy'|'sell', ...}]
        pending_orders = defaultdict(list)
        
        # 날짜 필터링
        if start_date:
            trading_dates = [d for d in trading_dates if d >= start_date]
        if end_date:
            trading_dates = [d for d in trading_dates if d <= end_date]
        
        logger.info(f"백테스트 기간: {trading_dates[0]} ~ {trading_dates[-1]}")
        logger.info(f"총 거래일: {len(trading_dates)}일")
        logger.info(f"종목 수: {len(processed_data)}")

        # 체결 시점 정렬: next_open(기본, T+1) | same_day_close(기존, T)
        buy_execution_mode = os.getenv('BACKTEST_BUY_EXECUTION_MODE', 'next_open').strip().lower()
        if buy_execution_mode not in ('next_open', 'same_day_close'):
            logger.warning(
                "알 수 없는 BACKTEST_BUY_EXECUTION_MODE=%s, next_open으로 대체",
                buy_execution_mode,
            )
            buy_execution_mode = 'next_open'

        sell_execution_mode = os.getenv('BACKTEST_SELL_EXECUTION_MODE', 'next_open').strip().lower()
        if sell_execution_mode not in ('next_open', 'same_day_close'):
            logger.warning(
                "알 수 없는 BACKTEST_SELL_EXECUTION_MODE=%s, next_open으로 대체",
                sell_execution_mode,
            )
            sell_execution_mode = 'next_open'

        logger.info(
            "백테스트 체결 모드: buy=%s, sell=%s",
            buy_execution_mode,
            sell_execution_mode,
        )

        # 월별 스냅샷 정렬: same_month(기존) 또는 prev_month(룩어헤드 방지)
        snapshot_alignment = os.getenv('UNIVERSE_SNAPSHOT_ALIGNMENT', 'prev_month').strip().lower()
        if snapshot_alignment not in ('same_month', 'prev_month'):
            logger.warning(
                "알 수 없는 UNIVERSE_SNAPSHOT_ALIGNMENT=%s, prev_month로 대체",
                snapshot_alignment,
            )
            snapshot_alignment = 'prev_month'
        
        # 각 거래일마다 시뮬레이션
        for idx, date in enumerate(trading_dates):
            # 0) 전일 예약 주문 실행 (T+1 체결, 익일 시가)
            orders = pending_orders.pop(date, [])
            if orders:
                # 매도 주문을 먼저 실행해 현금을 확보
                sell_orders = [o for o in orders if o['type'] == 'sell']
                for o in sell_orders:
                    code = o['code']
                    if code not in self.holdings:
                        continue
                    df = processed_data.get(code)
                    if df is None or date not in df.index:
                        continue
                    exec_price = df.loc[date, 'open']
                    if np.isnan(exec_price) or exec_price <= 0:
                        exec_price = df.loc[date, 'close']
                    if exec_price <= 0:
                        continue
                    self.execute_sell(code, exec_price, date)

                    reason = o.get('reason', '')
                    if '손절' in reason:
                        signal_date = o.get('signal_date', 'unknown')
                        logger.info(
                            "[%s] %s(신호일:%s): %s, 체결가: %s",
                            date,
                            reason,
                            signal_date,
                            code,
                            f"{exec_price:,.0f}",
                        )

                buy_orders = [o for o in orders if o['type'] == 'buy']
                if buy_orders:
                    available_slots = self.max_holdings - len(self.holdings)
                    if available_slots > 0:
                        buy_orders = buy_orders[:available_slots]
                        for o in buy_orders:
                            code = o['code']
                            df = processed_data.get(code)
                            if df is None or date not in df.index or code in self.holdings:
                                continue
                            exec_price = df.loc[date, 'open']
                            if np.isnan(exec_price) or exec_price <= 0:
                                exec_price = df.loc[date, 'close']
                            if exec_price <= 0:
                                continue
                            self.execute_buy(code, exec_price, date, o['budget'])

            # 1) 매도 신호 확인 (보유 종목)
            codes_to_sell = []
            for code in list(self.holdings.keys()):
                if code not in processed_data:
                    continue
                
                df = processed_data[code]
                avg_price = self.holdings[code]['avg_price']
                buy_date = self.holdings[code]['buy_date']
                
                # RSI 매도 신호 체크
                sell_signal, sell_price = self.check_sell_signal(code, date, df, avg_price)
                if sell_signal:
                    codes_to_sell.append((code, sell_price, "RSI매도", date))
                    continue
                
                # 손절 체크
                stop_loss_signal, stop_price, stop_reason = self.check_stop_loss(
                    code, date, df, avg_price, buy_date
                )
                if stop_loss_signal:
                    codes_to_sell.append((code, stop_price, stop_reason, date))
                    self.stop_loss_count += 1
            
            # 매도 체결: 설정에 따라 T+1(next_open) 또는 당일 종가(same_day_close)
            next_date = trading_dates[idx + 1] if idx + 1 < len(trading_dates) else None
            for code, price, reason, signal_date in codes_to_sell:
                if next_date and sell_execution_mode == 'next_open':
                    pending_orders[next_date].append({
                        'type': 'sell',
                        'code': code,
                        'reason': reason,
                        'signal_date': signal_date,
                    })
                else:
                    self.execute_sell(code, price, date)
                    if "손절" in reason:
                        logger.info(f"[{date}] {reason}: {code}, 가격: {price:,.0f}")
            
            # 2) 매수 신호 확인 (미보유 종목)
            current_holdings = len(self.holdings)
            available_slots = self.max_holdings - current_holdings
            
            if available_slots > 0:
                # 매수 신호 검토 순서: 실전과 동일하게 스냅샷 liquidity_rank 순
                if monthly_universe_map:
                    yyyymm = date[:6]
                    if snapshot_alignment == 'prev_month':
                        snapshot_key = _prev_yyyymm(yyyymm)
                        date_codes = monthly_universe_map.get(snapshot_key)
                        if date_codes is None:
                            # 데이터 범위 첫 월 등에서는 현재 월로 폴백
                            date_codes = monthly_universe_map.get(yyyymm)
                    else:
                        date_codes = monthly_universe_map.get(yyyymm)
                    if date_codes:
                        codes_to_check = [c for c in date_codes if c in processed_data and c not in self.holdings]
                    else:
                        codes_to_check = [c for c in processed_data if c not in self.holdings]
                else:
                    codes_to_check = [c for c in processed_data if c not in self.holdings]

                buy_candidates = []
                for code in codes_to_check:
                    df = processed_data[code]

                    # 워크포워드 모드: 해당 날짜에 데이터가 존재하는 종목만 매수 신호 검토
                    if availability_map and code in availability_map:
                        date_yyyymm = date[:6]
                        earliest, latest = availability_map[code][:2]
                        if not (earliest <= date_yyyymm <= latest):
                            continue
                    
                    # 마켓 타이밍 필터 (approach #4): 인덱스 200MA 이상일 때만 매수
                    if not self.check_market_filter(code, date):
                        continue

                    # 레짐 필터 (B+C): KOSPI 지수 MA 이상일 때만 매수
                    if not self.check_regime_filter(code, date):
                        continue
                    
                    buy_signal, buy_price = self.check_buy_signal(
                        code, date, df, current_holdings
                    )
                    
                    if buy_signal:
                        buy_candidates.append((code, buy_price))

                # 매수 가능한 종목 수만큼만 매수 (universe 처리 순서 유지 — 실전과 동일)
                buy_candidates = buy_candidates[:available_slots]
                
                if buy_candidates:
                    # 매수 예산 배분 (RSIStrategy와 동일: 현금 보유 비율 적용)
                    investable_cash = self.cash * (1 - self.cash_reserve_ratio)
                    try:
                        portfolio_value_current = self.calculate_portfolio_value(date, processed_data)
                        cap_amount = portfolio_value_current * self.max_position_ratio
                    except Exception:
                        cap_amount = self.initial_capital * self.max_position_ratio

                    if self.use_signal_strength_positioning and len(buy_candidates) > 1:
                        strengths = []
                        for code, _ in buy_candidates:
                            current_rsi = processed_data[code].loc[date, 'rsi']
                            raw_strength = (self.rsi_buy_threshold - current_rsi)
                            strength = max(1.0, raw_strength) ** self.signal_strength_exponent
                            strengths.append(strength)
                        total_strength = sum(strengths)
                        budgets = [investable_cash * s / total_strength for s in strengths]
                        if cap_amount is not None:
                            max_total = cap_amount * len(buy_candidates)
                            if max_total < investable_cash:
                                budgets = [b * max_total / investable_cash for b in budgets]
                    else:
                        b = investable_cash / len(buy_candidates)
                        if cap_amount is not None:
                            b = min(b, cap_amount)
                        budgets = [b] * len(buy_candidates)

                    # 매수 체결: 설정에 따라 T+1(next_open) 또는 당일 종가(same_day_close)
                    if next_date and buy_execution_mode == 'next_open':
                        for (code, price), budget in zip(buy_candidates, budgets):
                            pending_orders[next_date].append({
                                'type': 'buy',
                                'code': code,
                                'budget': budget,
                            })
                    else:
                        for (code, price), budget in zip(buy_candidates, budgets):
                            self.execute_buy(code, price, date, budget)
            
            # 3) 일일 포트폴리오 가치 기록
            portfolio_value = self.calculate_portfolio_value(date, processed_data)
            self.daily_portfolio_value.append({
                'date': date,
                'portfolio_value': portfolio_value,
                'cash': self.cash,
                'holdings_count': len(self.holdings)
            })
        
        # 백테스트 종료 - 결과 계산
        logger.info("백테스트 완료!")
        return self.calculate_results()
    
    def calculate_results(self) -> Dict:
        """백테스트 결과 분석
        
        Returns:
            결과 딕셔너리 (수익률, 샤프비율, MDD 등)
        """
        if not self.daily_portfolio_value:
            return {}
        
        df = pd.DataFrame(self.daily_portfolio_value)
        df['returns'] = df['portfolio_value'].pct_change()
        
        # 최종 포트폴리오 가치
        final_value = df.iloc[-1]['portfolio_value']
        
        # 총 수익률
        total_return = (final_value - self.initial_capital) / self.initial_capital * 100
        
        # 연환산 수익률
        days = len(df)
        annual_return = ((final_value / self.initial_capital) ** (252 / days) - 1) * 100
        
        # 샤프 비율 (무위험 수익률 0% 가정)
        sharpe_ratio = (df['returns'].mean() / df['returns'].std()) * np.sqrt(252) if df['returns'].std() != 0 else 0
        
        # MDD (Maximum Drawdown) — 포트폴리오 가치 직접 기반으로 계산 (NaN 전파 없음)
        value_series = df['portfolio_value']
        peak = value_series.expanding().max()
        drawdown = (value_series - peak) / peak
        mdd = drawdown.min() * 100
        
        # 승률 계산 (수익 거래 비율)
        sell_trades = [t for t in self.trades if t['type'] == 'sell']
        winning_trades = [t for t in sell_trades if t['profit'] > 0]
        win_rate = len(winning_trades) / len(sell_trades) * 100 if sell_trades else 0
        
        # 평균 수익률
        avg_profit_rate = np.mean([t['profit_rate'] for t in sell_trades]) if sell_trades else 0
        
        results = {
            'initial_capital': self.initial_capital,
            'final_value': final_value,
            'total_return': total_return,
            'annual_return': annual_return,
            'sharpe_ratio': sharpe_ratio,
            'mdd': mdd,
            'total_trades': len(self.trades),
            'buy_trades': len([t for t in self.trades if t['type'] == 'buy']),
            'sell_trades': len(sell_trades),
            'win_rate': win_rate,
            'avg_profit_rate': avg_profit_rate,
            'total_profit': sum([t.get('profit', 0) for t in sell_trades]),
            'stop_loss_count': self.stop_loss_count,  # 손절 횟수
            'stop_loss_enabled': self.enable_stop_loss,  # 가격 손절 활성화 여부
            'time_stop_loss_enabled': self.enable_time_stop_loss,  # 시간 손절 활성화 여부
            'price_stop_loss_pct': self.price_stop_loss_pct,  # 가격 손절 기준
            'time_stop_loss_days': self.time_stop_loss_days,  # 시간 손절 기준
            'slippage_buy': self.slippage_buy,    # 매수 슬리피지 비율
            'slippage_sell': self.slippage_sell,  # 매도 슬리피지 비율
            'open_positions': dict(self.holdings),  # 미청산 포지션
            'open_positions_value': final_value - self.cash,  # 미청산 포지션 평가금액
            'daily_values': df
        }
        
        return results
