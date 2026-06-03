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
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from collections import defaultdict
import logging
from util.logging_config import get_logger
from util.rsi_calc import compute_rsi
import os

logger = get_logger(__name__)


class BacktestEngine:
    """백테스트 실행 엔진 (Pullback in Uptrend + ATR Trailing Stop + Market Timing Filter)"""

    DEFAULT_INITIAL_CAPITAL = 10_000_000
    DEFAULT_RSI_SELL_THRESHOLD = 85.0
    DEFAULT_PROFIT_TARGET_PERCENT = 10.0
    DEFAULT_RSI_BUY_THRESHOLD = 30.0
    DEFAULT_CASH_RESERVE_RATIO = 0.2
    DEFAULT_COMMISSION_RATE_MOCK = 0.0035
    DEFAULT_COMMISSION_RATE_REAL = 0.00015
    DEFAULT_TAX_RATE_MOCK = 0.0000
    DEFAULT_TAX_RATE_REAL = 0.0020
    DEFAULT_RSI_METHOD = 'wilder'
    DEFAULT_TIME_STOP_LOSS_DAYS = 60
    DEFAULT_SLIPPAGE_BUY = 0.002   # 0.2% 매수 슬리피지 (체결 불리)
    DEFAULT_SLIPPAGE_SELL = 0.002  # 0.2% 매도 슬리피지 (체결 불리)
    DEFAULT_ATR_PERIOD = 14
    DEFAULT_ATR_TRAILING_MULTIPLE = 3.0
    DEFAULT_MARKET_FILTER_CODE = '229200'   # KODEX 200 (KOSPI 200 ETF)
    DEFAULT_MARKET_MA_PERIOD = 200
    
    def __init__(
        self,
        initial_capital: Optional[float] = None,  # env: INITIAL_CAPITAL, 기본값: DEFAULT_INITIAL_CAPITAL
        max_holdings: int = 10,  # 최대 보유 종목 수
        rsi_period: int = 14,  # RSI 계산 기간
        ma_short: int = 20,  # 단기 이동평균
        ma_long: int = 60,  # 장기 이동평균
        ma_trend: int = 200,  # 장기 추세 이동평균 (필터용)
        # 전략 파라미터: None → .env → 엔진 내부 기본값 순으로 적용
        # 명시적으로 값을 전달하면 .env를 무시하고 해당 값이 최우선 적용됨
        rsi_sell_threshold: Optional[float] = None,    # env: RSI_SELL_THRESHOLD, 기본값: DEFAULT_RSI_SELL_THRESHOLD
        profit_target_percent: float = None, # env: PROFIT_TARGET_PERCENT, 기본값: DEFAULT_PROFIT_TARGET_PERCENT
        rsi_buy_threshold: float = None,     # env: RSI_BUY_THRESHOLD, 기본값: DEFAULT_RSI_BUY_THRESHOLD
        price_drop_threshold: float = -3.0,  # 2일간 가격 하락 기준 (%) (Pullback 확인)
        cash_reserve_ratio: float = None,    # env: CASH_RESERVE_RATIO, 기본값: DEFAULT_CASH_RESERVE_RATIO
        commission_rate: float = None,       # env: TRADING_FEE_PERCENT_REAL, 기본값: 실전 기본 수수료
        tax_rate: float = None,              # env: TRADING_TAX_PERCENT_REAL, 기본값: 실전 기본 거래세
        rsi_method: str = None,              # env: RSI_METHOD, 기본값: DEFAULT_RSI_METHOD
        rsi_min_periods: int = None,         # None이면 rsi_period 사용
        # ATR 트레일링 스탑 파라미터 (RSI 매도/고정 수익률 목표 대체)
        atr_period: int = None,              # ATR 계산 기간, 기본값: DEFAULT_ATR_PERIOD
        atr_trailing_multiple: float = None, # ATR 배수, 기본값: DEFAULT_ATR_TRAILING_MULTIPLE
        # 손절 파라미터 (fixed % stop은 ATR이 대체, 비활성화 기본)
        enable_stop_loss: bool = False,      # 가격 손절 (ATR이 대체하므로 기본 False)
        price_stop_loss_pct: float = -20.0,  # 가격 손절 기준 (%)
        enable_time_stop_loss: bool = True,  # 시간 손절
        time_stop_loss_days: Optional[int] = None,  # env: TIME_STOP_LOSS_DAYS, 기본값: DEFAULT_TIME_STOP_LOSS_DAYS
        slippage_buy: float = None,              # env: SLIPPAGE_BUY, 기본값: DEFAULT_SLIPPAGE_BUY
        slippage_sell: float = None,             # env: SLIPPAGE_SELL, 기본값: DEFAULT_SLIPPAGE_SELL
        market_filter_code: str = None,          # env: MARKET_FILTER_CODE, 기본값: None(미사용)
        market_ma_period: int = None,            # env: MARKET_MA_PERIOD, 기본값: DEFAULT_MARKET_MA_PERIOD
        symbol_names: Dict[str, str] = None,
    ):
        # --- 우선순위 적용 헬퍼: 명시적 파라미터 > .env > 엔진 내부 기본값 ---
        def _resolve_float(explicit, env_name, fallback):
            if explicit is not None:
                return float(explicit)
            v = os.getenv(env_name)
            return float(v) if v is not None else fallback

        def _resolve_int(explicit, env_name, fallback):
            if explicit is not None:
                return int(explicit)
            v = os.getenv(env_name)
            return int(v) if v is not None else fallback

        self.max_holdings = max_holdings
        self.rsi_period = _resolve_int(rsi_period, 'RSI_PERIOD', rsi_period)
        self.ma_short = ma_short
        self.ma_long = ma_long
        self.ma_trend = ma_trend
        self.price_drop_threshold = _resolve_float(price_drop_threshold, 'PRICE_DROP_THRESHOLD', price_drop_threshold)
        self.rsi_min_periods = rsi_min_periods if rsi_min_periods is not None else self.rsi_period
        self.atr_period = _resolve_int(atr_period, 'ATR_PERIOD', self.DEFAULT_ATR_PERIOD)
        self.atr_trailing_multiple = _resolve_float(atr_trailing_multiple, 'ATR_TRAILING_MULTIPLE', self.DEFAULT_ATR_TRAILING_MULTIPLE)

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

        # 손절 설정 (env 우선, 명시적 파라미터는 fallback)
        _env_enable_stop = os.getenv('ENABLE_STOP_LOSS')
        self.enable_stop_loss = str(_env_enable_stop).lower() in ('1', 'true') if _env_enable_stop is not None else enable_stop_loss
        _env_psp = os.getenv('PRICE_STOP_LOSS_PCT')
        self.price_stop_loss_pct = float(_env_psp) if _env_psp is not None else price_stop_loss_pct
        self.enable_time_stop_loss = enable_time_stop_loss
        self.time_stop_loss_days   = _resolve_int(time_stop_loss_days, 'TIME_STOP_LOSS_DAYS', self.DEFAULT_TIME_STOP_LOSS_DAYS)
        self.time_stop_loss_days   = _resolve_int(time_stop_loss_days, 'TIME_STOP_LOSS_DAYS', self.DEFAULT_TIME_STOP_LOSS_DAYS)

        # 슬리피지 설정 (매수 시 불리하게 적용: 가격 상승, 매도 시 불리하게 적용: 가격 하락)
        self.slippage_buy  = _resolve_float(slippage_buy,  'SLIPPAGE_BUY',  self.DEFAULT_SLIPPAGE_BUY)
        self.slippage_sell = _resolve_float(slippage_sell, 'SLIPPAGE_SELL', self.DEFAULT_SLIPPAGE_SELL)

        # 마켓 타이밍 필터
        _raw = market_filter_code if market_filter_code is not None else os.getenv('MARKET_FILTER_CODE')
        self.market_filter_code = _raw if _raw else None
        self.market_ma_period = _resolve_int(market_ma_period, 'MARKET_MA_PERIOD', self.DEFAULT_MARKET_MA_PERIOD)

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

        - RSI (지정 기간, Cutler 또는 Wilder)
        - 이동평균 (20/60/200)
        - ATR (평균 진폭 범위, 트레일링 스탑용)

        Args:
            df: OHLCV 데이터프레임

        Returns:
            지표가 추가된 데이터프레임
        """
        df = df.copy()

        # RSI 계산
        rsi_series = self.calculate_rsi(df['close'], self.rsi_period)
        df[f'RSI({self.rsi_period})'] = rsi_series
        df['rsi'] = rsi_series

        # 이동평균 계산
        df['ma20'] = df['close'].rolling(window=self.ma_short, min_periods=self.ma_short).mean()
        df['ma60'] = df['close'].rolling(window=self.ma_long, min_periods=self.ma_long).mean()
        df['ma200'] = df['close'].rolling(window=self.ma_trend, min_periods=self.ma_trend).mean()

        # ATR (Average True Range) 계산
        prev_close = df['close'].shift(1)
        tr = pd.concat([
            (df['high'] - df['low']),
            (df['high'] - prev_close).abs(),
            (df['low'] - prev_close).abs()
        ], axis=1).max(axis=1)
        df['tr'] = tr
        df['atr'] = tr.rolling(window=self.atr_period, min_periods=self.atr_period).mean()
        df[f'ATR({self.atr_period})'] = df['atr']

        return df
    
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
            
            # 매수 조건 확인
            # 1) ma20 > ma60 (단기 이평 > 장기 이평)
            # 2) close > ma200 (장기 추세 상승)
            # 3) RSI < rsi_buy_threshold (과매도)
            # 4) 2일 전 대비 price_drop_threshold 이상 하락
            if ma20 > ma60 and close > ma200 and rsi < self.rsi_buy_threshold and price_diff < self.price_drop_threshold:
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
        """매도 신호 확인 (미사용 — ATR 트레일링 스탑이 check_stop_loss에서 처리)
        
        더 이상 메인 루프에서 호출되지 않습니다.
        ATR 트레일링 + 시간 손절은 check_stop_loss에서 처리합니다.
        
        Returns:
            항상 (False, None)
        """
        return False, None
    
    def check_stop_loss(
        self,
        code: str,
        date: str,
        df: pd.DataFrame,
        avg_purchase_price: float,
        buy_date: str
    ) -> Tuple[bool, Optional[float], str]:
        """청산 조건 확인 (ATR 트레일링 스탑 + 시간 손절)
        
        Args:
            code: 종목 코드
            date: 현재 날짜
            df: 해당 종목의 OHLCV + 지표 데이터
            avg_purchase_price: 평균 매입가
            buy_date: 매수 날짜
            
        Returns:
            (청산 신호 여부, 매도 가격, 청산 사유)
        """
        if not self.enable_stop_loss and not self.enable_time_stop_loss:
            return False, None, ""
        
        try:
            if date not in df.index:
                return False, None, ""
            
            idx = df.index.get_loc(date)
            current = df.iloc[idx]
            close = current['close']
            
            # 1. ATR 트레일링 스탑 (기본 청산 방식 — 수익/손실 모두 청산)
            highest_price = self.holdings.get(code, {}).get('highest_price', avg_purchase_price)
            atr = current.get('atr', np.nan)
            if not np.isnan(atr) and atr > 0:
                trailing_stop_price = highest_price - self.atr_trailing_multiple * atr
                if close < trailing_stop_price:
                    return True, close, f"ATR트레일링({close:.0f}<{trailing_stop_price:.0f})"
            
            # 2. 가격 손절 체크 (enable_stop_loss가 True일 때만, legacy fallback)
            if self.enable_stop_loss:
                price_change_pct = ((close - avg_purchase_price) / avg_purchase_price) * 100
                if price_change_pct <= self.price_stop_loss_pct:
                    return True, close, f"가격손절({price_change_pct:.2f}%)"
            
            # 3. 시간 손절 체크 (enable_time_stop_loss가 True일 때만)
            if self.enable_time_stop_loss:
                buy_date_dt = pd.to_datetime(buy_date, format='%Y%m%d')
                current_date_dt = pd.to_datetime(date, format='%Y%m%d')
                holding_days = (current_date_dt - buy_date_dt).days
                
                if holding_days > self.time_stop_loss_days:
                    return True, close, f"시간손절({holding_days}일)"
            
            return False, None, ""
            
        except (KeyError, IndexError) as e:
            logger.warning(f"청산 조건 확인 중 오류 ({code}, {date}): {e}")
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
            # 신규 매수 (highest_price: ATR 트레일링 스탑 기준)
            self.holdings[code] = {
                'quantity': quantity,
                'avg_price': execution_price,
                'buy_date': date,
                'highest_price': execution_price
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
        monthly_universe_map: Dict[str, set] = None,
    ) -> Dict:
        """백테스트 실행
        
        Args:
            price_data: {종목코드: OHLCV DataFrame} 딕셔너리
            start_date: 시작 날짜 (YYYYMMDD)
            end_date: 종료 날짜 (YYYYMMDD)
            availability_map: {종목코드: (earliest_yyyymm, latest_yyyymm)} 딕셔너리.
                지정하면 각 거래일마다 해당 날짜에 데이터가 존재하는 종목만 매수 신호 검토.
                생존편향 제거를 위한 워크포워드 백테스트에서 활용합니다.
            monthly_universe_map: {YYYYMM: {code1, code2, ...}} 딕셔너리.
                지정하면 해당 월 스냅샷에 포함된 종목만 매수 신호 검토.
                진짜 월별 워크포워드 유니버스 적용 시 사용합니다.
            
        Returns:
            백테스트 결과 딕셔너리
        """
        logger.info("백테스트 시작...")
        if availability_map:
            logger.info("워크포워드 모드: 종목별 데이터 가용 기간 필터 적용")
        if monthly_universe_map:
            logger.info("워크포워드 모드: 월별 유니버스 스냅샷 필터 적용")
        
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
        
        # 마켓 타이밍 필터: market_filter_code의 200MA 계산
        market_ma = None
        if self.market_filter_code and self.market_filter_code in processed_data:
            market_df = processed_data[self.market_filter_code]
            ma_period = self.market_ma_period
            col_name = f'ma{ma_period}'
            if col_name not in market_df.columns:
                market_df[col_name] = market_df['close'].rolling(window=ma_period, min_periods=ma_period).mean()
            market_ma = market_df[col_name]
            logger.info(f"마켓 타이밍 필터: {self.market_filter_code}({col_name}) 적용 — 매수는 시장 지수가 {col_name} 위에 있을 때만 허용")
        elif self.market_filter_code:
            logger.warning(f"마켓 필터 코드({self.market_filter_code})를 processed_data에서 찾을 수 없습니다. 필터 미적용.")
        
        # 모든 거래일 추출 (모든 종목의 날짜를 합침)
        all_dates = set()
        for df in processed_data.values():
            all_dates.update(df.index)
        
        trading_dates = sorted(list(all_dates))
        
        # 매수 예약 주문 관리: next_date -> [{'type': 'buy', 'code': str, 'budget': float}]
        pending_orders = defaultdict(list)
        
        # 날짜 필터링
        if start_date:
            trading_dates = [d for d in trading_dates if d >= start_date]
        if end_date:
            trading_dates = [d for d in trading_dates if d <= end_date]
        
        logger.info(f"백테스트 기간: {trading_dates[0]} ~ {trading_dates[-1]}")
        logger.info(f"총 거래일: {len(trading_dates)}일")
        logger.info(f"종목 수: {len(processed_data)}")
        
        # 각 거래일마다 시뮬레이션
        for idx, date in enumerate(trading_dates):
            # 0) 전일 예약된 매수 주문 실행 (T+1 체결, 익일 시가)
            orders = pending_orders.pop(date, [])
            if orders:
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
                            if np.isnan(exec_price):
                                exec_price = df.loc[date, 'close']
                            self.execute_buy(code, exec_price, date, o['budget'])

            # 1) 보유 종목 최고가 갱신 (ATR 트레일링 기준)
            for code in list(self.holdings.keys()):
                if code not in processed_data:
                    continue
                hold_df = processed_data[code]
                if date in hold_df.index:
                    today_close = hold_df.loc[date, 'close']
                    current_highest = self.holdings[code].get('highest_price', self.holdings[code]['avg_price'])
                    if today_close > current_highest:
                        self.holdings[code]['highest_price'] = today_close
            
            # 2) 청산 신호 확인 (ATR 트레일링 + 시간 손절)
            codes_to_sell = []
            for code in list(self.holdings.keys()):
                if code not in processed_data:
                    continue
                
                df = processed_data[code]
                avg_price = self.holdings[code]['avg_price']
                buy_date = self.holdings[code]['buy_date']
                
                # ATR 트레일링 + 시간 손절 (check_stop_loss가 모든 청산 처리)
                stop_loss_signal, stop_price, stop_reason = self.check_stop_loss(
                    code, date, df, avg_price, buy_date
                )
                if stop_loss_signal:
                    codes_to_sell.append((code, stop_price, stop_reason))
                    self.stop_loss_count += 1
            
            # 매도 실행
            for code, price, reason in codes_to_sell:
                self.execute_sell(code, price, date)
                logger.info(f"[{date}] {reason}: {code}, 가격: {price:,.0f}")
            
            # 2) 매수 신호 확인 (미보유 종목)
            current_holdings = len(self.holdings)
            available_slots = self.max_holdings - current_holdings
            
            # 마켓 타이밍 필터: 시장이 200MA 위에 있을 때만 매수
            market_ok = True
            if market_ma is not None and date in market_ma.index:
                m_idx = market_ma.index.get_loc(date)
                m_current = market_ma.iloc[m_idx]
                m_close = market_df.loc[date, 'close'] if date in market_df.index else None
                if m_close is not None and not np.isnan(m_current):
                    market_ok = m_close > m_current
            
            if available_slots > 0 and market_ok:
                buy_candidates = []
                
                for code, df in processed_data.items():
                    # 이미 보유 중이면 스킵
                    if code in self.holdings:
                        continue

                    # 월별 유니버스 스냅샷 필터
                    if monthly_universe_map:
                        month_codes = monthly_universe_map.get(date[:6], set())
                        if month_codes and code not in month_codes:
                            continue

                    # 워크포워드 모드: 해당 날짜에 데이터가 존재하는 종목만 매수 신호 검토
                    if availability_map and code in availability_map:
                        date_yyyymm = date[:6]
                        earliest, latest = availability_map[code][:2]
                        if not (earliest <= date_yyyymm <= latest):
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

                    budget_per_stock = investable_cash / available_slots
                    if cap_amount is not None:
                        budget_per_stock = min(budget_per_stock, cap_amount)

                    # 매수 체결: 익일 시가로 예약 (T+1), 마지막 거래일은 즉시 종가 실행 (폴백)
                    next_date = trading_dates[idx + 1] if idx + 1 < len(trading_dates) else None
                    if next_date:
                        for code, price in buy_candidates:
                            pending_orders[next_date].append({
                                'type': 'buy',
                                'code': code,
                                'budget': budget_per_stock,
                            })
                    else:
                        for code, price in buy_candidates:
                            self.execute_buy(code, price, date, budget_per_stock)
            
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
            'stop_loss_count': self.stop_loss_count,
            'stop_loss_enabled': self.enable_stop_loss,
            'time_stop_loss_enabled': self.enable_time_stop_loss,
            'price_stop_loss_pct': self.price_stop_loss_pct,
            'time_stop_loss_days': self.time_stop_loss_days,
            'slippage_buy': self.slippage_buy,
            'slippage_sell': self.slippage_sell,
            'atr_period': self.atr_period,
            'atr_trailing_multiple': self.atr_trailing_multiple,
            'market_filter_code': self.market_filter_code,
            'market_ma_period': self.market_ma_period,
            'open_positions': dict(self.holdings),
            'open_positions_value': final_value - self.cash,
            'daily_values': df
        }
        
        return results
