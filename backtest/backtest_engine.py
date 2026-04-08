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
import logging
from util.logging_config import get_logger
from util.rsi_calc import compute_rsi
import os

logger = get_logger(__name__)


class BacktestEngine:
    """백테스트 실행 엔진"""
    
    def __init__(
        self,
        initial_capital: float = 10_000_000,  # 초기 자본금 (1천만원)
        max_holdings: int = 10,  # 최대 보유 종목 수
        rsi_period: int = 2,  # RSI 계산 기간
        ma_short: int = 20,  # 단기 이동평균
        ma_long: int = 60,  # 장기 이동평균
        ma_trend: int = 200,  # 장기 추세 이동평균 (필터용)
        # 전략 파라미터: None → .env → 하드코딩 기본값 순으로 적용
        # 명시적으로 값을 전달하면 .env를 무시하고 해당 값이 최우선 적용됨
        rsi_sell_threshold: float = None,    # env: RSI_SELL_THRESHOLD, 기본값: 80
        profit_target_percent: float = None, # env: PROFIT_TARGET_PERCENT, 기본값: 10.0
        rsi_buy_threshold: float = None,     # env: RSI_BUY_THRESHOLD, 기본값: 3
        price_drop_threshold: float = -5.0,  # 가격 하락 기준 (%)
        cash_reserve_ratio: float = None,    # env: CASH_RESERVE_RATIO, 기본값: 0.2
        commission_rate: float = None,       # env: TRADING_FEE_PERCENT_{MODE}, 기본값: 0.0035
        tax_rate: float = None,              # env: TRADING_TAX_PERCENT_{MODE}, 기본값: 0.0015
        rsi_method: str = None,              # env: RSI_METHOD, 기본값: 'cutler'
        rsi_min_periods: int = None,         # None이면 rsi_period 사용
        # 손절 파라미터 (백테스트 결과: 손절 없음이 최고 성능)
        enable_stop_loss: bool = False,      # 가격 손절 비활성화 (최적화 기본값)
        price_stop_loss_pct: float = -20.0,  # 가격 손절 기준 (%)
        enable_time_stop_loss: bool = True, # 시간 손절 독립 플래그 (최적화 기본값)
        time_stop_loss_days: int = 365,      # 시간 손절 기준 (일) (최적화 기본값)
        symbol_names: Dict[str, str] = None,
    ):
        self.initial_capital = initial_capital
        self.max_holdings = max_holdings
        self.rsi_period = rsi_period
        self.ma_short = ma_short
        self.ma_long = ma_long
        self.ma_trend = ma_trend
        self.price_drop_threshold = price_drop_threshold
        self.rsi_min_periods = rsi_min_periods if rsi_min_periods is not None else rsi_period

        # --- 우선순위 적용 헬퍼: 명시적 파라미터 > .env > 하드코딩 기본값 ---
        def _resolve_float(explicit, env_name, fallback):
            """명시적 값이 있으면 그것을 쓰고, 없으면 env, 그것도 없으면 fallback 사용"""
            if explicit is not None:
                return float(explicit)
            v = os.getenv(env_name)
            return float(v) if v is not None else fallback

        self.rsi_sell_threshold   = _resolve_float(rsi_sell_threshold,   'RSI_SELL_THRESHOLD',    80.0)
        self.profit_target_percent = _resolve_float(profit_target_percent, 'PROFIT_TARGET_PERCENT', 10.0)
        self.rsi_buy_threshold    = _resolve_float(rsi_buy_threshold,     'RSI_BUY_THRESHOLD',      3.0)

        # CASH_RESERVE_RATIO: 퍼센트(20) 또는 소수(0.2) 두 형식 모두 허용
        if cash_reserve_ratio is not None:
            self.cash_reserve_ratio = float(cash_reserve_ratio)
        else:
            v = os.getenv('CASH_RESERVE_RATIO')
            if v is not None:
                tmp = float(v)
                self.cash_reserve_ratio = tmp / 100.0 if tmp > 1 else tmp
            else:
                self.cash_reserve_ratio = 0.2

        # RSI 계산 방식
        _rsi_method = rsi_method if rsi_method is not None else os.getenv('RSI_METHOD', 'cutler')
        _rsi_method = _rsi_method.strip().lower() if isinstance(_rsi_method, str) else 'cutler'
        if _rsi_method not in ('cutler', 'wilder'):
            logger.warning(f"Invalid RSI method '{_rsi_method}', using 'cutler'")
            _rsi_method = 'cutler'
        self.rsi_method = _rsi_method

        # 거래 비용: 명시적 파라미터 우선, 없으면 KIWOOM_MODE에 맞는 env 키에서 로드
        try:
            mode = os.getenv('KIWOOM_MODE', os.getenv('KIW_MODE', 'mock')).strip().lower()
        except Exception:
            mode = 'mock'

        def _parse_percent_env(val):
            """퍼센트 표기(0.35 → 0.0035) 변환"""
            if val is None:
                return None
            try:
                return float(val) / 100.0
            except Exception:
                return None

        def _parse_rate_env(name):
            """소수 또는 퍼센트 표기 자동 감지 변환"""
            val = os.getenv(name)
            if val is None:
                return None
            try:
                f = float(val)
                return f / 100.0 if f > 0.01 else f
            except Exception:
                return None

        if commission_rate is not None:
            self.commission_rate = float(commission_rate)
        else:
            parsed = _parse_percent_env(os.getenv(f'TRADING_FEE_PERCENT_{mode.upper()}')) \
                     or _parse_rate_env('COMMISSION_RATE')
            self.commission_rate = parsed if parsed is not None else 0.0035

        if tax_rate is not None:
            self.tax_rate = float(tax_rate)
        else:
            parsed = _parse_percent_env(os.getenv(f'TRADING_TAX_PERCENT_{mode.upper()}')) \
                     or _parse_rate_env('TAX_RATE')
            self.tax_rate = parsed if parsed is not None else 0.0015

        self.buy_fee_rate  = 1 + self.commission_rate
        self.sell_fee_rate = 1 + self.commission_rate + self.tax_rate

        # 손절 설정
        self.enable_stop_loss      = enable_stop_loss
        self.price_stop_loss_pct   = price_stop_loss_pct
        self.enable_time_stop_loss = enable_time_stop_loss
        self.time_stop_loss_days   = time_stop_loss_days

        # 포트폴리오 상태
        self.cash = initial_capital
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
        df['ma20'] = df['close'].rolling(window=self.ma_short, min_periods=1).mean()
        df['ma60'] = df['close'].rolling(window=self.ma_long, min_periods=1).mean()
        df['ma200'] = df['close'].rolling(window=self.ma_trend, min_periods=1).mean()
        
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
            
            close = current['close']
            rsi = current['rsi']

            # display (name(code)) for logging
            display = f"{self.symbol_names.get(code)}({code})" if self.symbol_names.get(code) else code
            logger.debug("check_sell_signal %s date=%s rsi=%.2f close=%d", display, date, rsi, close)
            
            # 값 유효성 체크
            if np.isnan(rsi):
                return False, None
            
            # 매도 시 수수료+세금을 고려한 손익분기점 계산
            # RSIStrategy와 동일: math.ceil() 적용 (가격은 정수)
            breakeven_price = math.ceil(avg_purchase_price * self.sell_fee_rate)

            # 목표 가격 계산: 손익분기점 대비 목표 수익률 충족
            target_price = math.ceil(breakeven_price * (1 + (self.profit_target_percent / 100)))
            
            # 매도 조건 확인
            # 1) RSI > 80 (과매수) AND 손익분기점 초과
            # 2) AND 목표가 도달 (손익분기점 기준 수익률)
            if (
                rsi > self.rsi_sell_threshold
                and close > breakeven_price
                and close >= target_price
            ):
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
            price: 매수 가격
            date: 거래 날짜
            budget: 매수에 사용할 예산
        """
        # 매수 가능 수량 계산 (RSIStrategy와 동일하게 math.floor 사용)
        quantity = math.floor(budget / price)
        
        if quantity < 1:
            return
        
        # 수수료 포함 실제 매수 금액 (RSIStrategy와 동일하게 math.floor 사용)
        buy_amount = quantity * price
        total_cost = math.floor(buy_amount * self.buy_fee_rate)
        
        # 예산 체크
        if total_cost > self.cash:
            # 예산에 맞게 수량 재조정
            quantity = int((self.cash / self.buy_fee_rate) / price)
            if quantity < 1:
                return
            buy_amount = quantity * price
            total_cost = math.floor(buy_amount * self.buy_fee_rate)
        
        # 현금 차감
        self.cash -= total_cost
        
        # 포지션 추가 또는 업데이트
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
                'avg_price': price,
                'buy_date': date
            }
        
        # 거래 기록
        commission = total_cost - buy_amount
        self.trades.append({
            'date': date,
            'code': code,
            'type': 'buy',
            'price': price,
            'quantity': quantity,
            'amount': buy_amount,
            'commission': commission,
            'total_cost': total_cost
        })
        
        display = f"{self.symbol_names.get(code)}({code})" if self.symbol_names.get(code) else code
        logger.info(f"[{date}] 매수: {display}, 가격: {price:,.0f}, 수량: {quantity}, 총액: {total_cost:,.0f}")
    
    def execute_sell(self, code: str, price: float, date: str):
        """매도 주문 실행 (RSIStrategy와 동일한 로직)
        
        Args:
            code: 종목 코드
            price: 매도 가격
            date: 거래 날짜
        """
        if code not in self.holdings:
            return
        
        quantity = self.holdings[code]['quantity']
        avg_price = self.holdings[code]['avg_price']
        
        # 수수료 + 거래세 포함 실제 매도 금액 (RSIStrategy와 동일)
        sell_amount = quantity * price
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
        self.trades.append({
            'date': date,
            'code': code,
            'type': 'sell',
            'price': price,
            'quantity': quantity,
            'amount': sell_amount,
            'commission': total_fee * (self.commission_rate / (self.commission_rate + self.tax_rate)),
            'tax': total_fee * (self.tax_rate / (self.commission_rate + self.tax_rate)),
            'net_proceeds': net_proceeds,
            'avg_buy_price': avg_price,
            'profit': profit,
            'profit_rate': profit_rate
        })
        
        display = f"{self.symbol_names.get(code)}({code})" if self.symbol_names.get(code) else code
        logger.info(f"[{date}] 매도: {display}, 가격: {price:,.0f}, 수량: {quantity}, "
                   f"수익: {profit:,.0f} ({profit_rate:.2f}%)")
    
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
        end_date: str = None
    ) -> Dict:
        """백테스트 실행
        
        Args:
            price_data: {종목코드: OHLCV DataFrame} 딕셔너리
            start_date: 시작 날짜 (YYYYMMDD)
            end_date: 종료 날짜 (YYYYMMDD)
            
        Returns:
            백테스트 결과 딕셔너리
        """
        logger.info("백테스트 시작...")
        
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
                    codes_to_sell.append((code, sell_price, "RSI매도"))
                    continue
                
                # 손절 체크
                stop_loss_signal, stop_price, stop_reason = self.check_stop_loss(
                    code, date, df, avg_price, buy_date
                )
                if stop_loss_signal:
                    codes_to_sell.append((code, stop_price, stop_reason))
                    self.stop_loss_count += 1
            
            # 매도 실행
            for code, price, reason in codes_to_sell:
                self.execute_sell(code, price, date)
                if "손절" in reason:
                    logger.info(f"[{date}] {reason}: {code}, 가격: {price:,.0f}")
            
            # 2) 매수 신호 확인 (미보유 종목)
            current_holdings = len(self.holdings)
            available_slots = self.max_holdings - current_holdings
            
            if available_slots > 0:
                buy_candidates = []
                
                for code, df in processed_data.items():
                    # 이미 보유 중이면 스킵
                    if code in self.holdings:
                        continue
                    
                    buy_signal, buy_price = self.check_buy_signal(
                        code, date, df, current_holdings
                    )
                    
                    if buy_signal:
                        buy_candidates.append((code, buy_price))

                # 매수 가능한 종목 수만큼만 매수 (universe 처리 순서 유지 — 실전과 동일)
                buy_candidates = buy_candidates[:available_slots]
                
                # 매수 예산 배분 (RSIStrategy와 동일: 현금 보유 비율 적용)
                # 전체 예수금의 (1 - CASH_RESERVE_RATIO)만 투자에 사용
                # 남은 슬롯으로 나누어 종목당 예산 계산
                if buy_candidates:
                    investable_cash = self.cash * (1 - self.cash_reserve_ratio)
                    budget_per_stock = investable_cash / available_slots
                    
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
            'stop_loss_count': self.stop_loss_count,  # 손절 횟수
            'stop_loss_enabled': self.enable_stop_loss,  # 가격 손절 활성화 여부
            'time_stop_loss_enabled': self.enable_time_stop_loss,  # 시간 손절 활성화 여부
            'price_stop_loss_pct': self.price_stop_loss_pct,  # 가격 손절 기준
            'time_stop_loss_days': self.time_stop_loss_days,  # 시간 손절 기준
            'open_positions': dict(self.holdings),  # 미청산 포지션
            'open_positions_value': final_value - self.cash,  # 미청산 포지션 평가금액
            'daily_values': df
        }
        
        return results
