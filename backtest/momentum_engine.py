"""
한국형 단기 모멘텀 백테스트 엔진

[2026-06-20] 실험 결과: 5일 모멘텀 + 거래량 폭발 전략은 2021-2026년 한국 시장에서
유의미한 edge를 확인할 수 없었습니다. 시그널의 fwd5 평균 수익률은 -0.46% (수수료 제외,
M7 검증), 수수료 포함 시 -1.10%로 기대값이 음수입니다.

종합 분석 결과:
- 분석(2016-2026, 300종목): fwd5 +2.04%, 승률 59.4%
- 엔진 재현(2021-2026, 동일 조건): fwd5 -0.46%, 승률 38.1%
- 원인: 2021년 이후 한국 테마주 싸이클 단축으로 모멘텀 지속성 소멸

용도 변경:
- 백테스트 엔진 코드 자체는 구조적으로 완성되어 있음
- 다른 진입/청산 조건으로 재활용 가능 (볼린저밴드, MACD, 변동성 전략)
- 단기 모멘텀(5d)과 듀얼 엔진 구성은 현재 시장 환경에서 부적합

MomentumBacktestEngine 클래스는 리팩토링하여 다른 전략에도 사용 가능한
범용 백테스트 프레임워크로 발전시킬 수 있습니다.
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


class MomentumBacktestEngine:
    """한국형 단기 모멘텀 백테스트 엔진
    
    RSI(2)와 다른 신호 체계:
    - 매수: 20일 상승 + 거래량 폭발 (추세 추종)
    - 매도: 트레일링 스탑 + 모멘텀 소멸 (손절이 핵심)
    - RSI(2)가 deep loser를 감내하는 대신, 모멘텀은 빠르게 손절
    """
    
    DEFAULT_INITIAL_CAPITAL = 10_000_000
    DEFAULT_COMMISSION_RATE_REAL = 0.00015
    DEFAULT_TAX_RATE_REAL = 0.0020
    DEFAULT_SLIPPAGE_BUY = 0.002
    DEFAULT_SLIPPAGE_SELL = 0.002
    
    def __init__(
        self,
        initial_capital: Optional[float] = None,
        max_holdings: int = 10,
        # 모멘텀 파라미터
        momentum_entry_period: int = 5,   # 모멘텀 진입 기준 기간 (일) — 한국 테마주는 3-5일 싸이클
        momentum_long: int = 20,          # 추세 확인 기간 (일)
        momentum_entry_threshold: float = 8.0,    # 모멘텀 진입 기준 (%): 5일 8% = 급등 시작
        momentum_long_threshold: float = 3.0,     # 추세 확인 기준 (%): 20일 3% = 완만 상승 유지
        volume_surge_ratio: float = 3.0,          # 거래량 폭발 기준 (3배=진짜 이탈만 필터)
        # 청산 파라미터 (한국 단기 모멘텀은 수익 목표+손절 고정이 트레일링보다 효과적)
        profit_target_pct: float = 8.0,   # 수익 목표 (%): 도달 시 즉시 매도
        stop_loss_pct: float = 8.0,       # 손절 기준 (%): 진입가 대비 손실 시 매도
        momentum_decay_threshold: float = -5.0,   # 모멘텀 소멸 기준 (진입기간 수익률, 음수=추세반전)
        max_holding_days: int = 30,       # 최대 보유 일수 (한국 테마주는 2-4주 싸이클)
        # 추세 필터
        use_ma200_filter: bool = False,   # 한국 단기 모멘텀은 MA200 필터 불필요 (느림)
        ma200_period: int = 200,
        # 포지션 관리
        cash_reserve_ratio: float = 0.2,  # 현금 보유 비율
        max_position_ratio: float = 0.10, # 종목당 최대 비중 (모멘텀은 더 높은 집중 허용)
        # 거래 비용
        commission_rate: Optional[float] = None,
        tax_rate: Optional[float] = None,
        slippage_buy: Optional[float] = None,
        slippage_sell: Optional[float] = None,
        # 유니버스 필터
        min_market_cap: float = 500,      # 최소 시가총액 (억원, 유니버스에서 필터링)
    ):
        self.initial_capital = initial_capital or self.DEFAULT_INITIAL_CAPITAL
        self.max_holdings = max_holdings
        
        # 모멘텀 파라미터
        self.momentum_entry_period = momentum_entry_period
        self.momentum_long = momentum_long
        self.momentum_entry_threshold = momentum_entry_threshold
        self.momentum_long_threshold = momentum_long_threshold
        self.volume_surge_ratio = volume_surge_ratio
        
        # 청산 파라미터
        self.profit_target_pct = profit_target_pct
        self.stop_loss_pct = stop_loss_pct
        self.momentum_decay_threshold = momentum_decay_threshold
        self.max_holding_days = max_holding_days
        
        # 추세 필터
        self.use_ma200_filter = use_ma200_filter
        self.ma200_period = ma200_period
        
        # 포지션 관리
        self.cash_reserve_ratio = cash_reserve_ratio
        self.max_position_ratio = max_position_ratio
        
        # 거래 비용 (실전 기준)
        default_commission = self.DEFAULT_COMMISSION_RATE_REAL
        default_tax = self.DEFAULT_TAX_RATE_REAL
        
        self.commission_rate = commission_rate if commission_rate is not None else default_commission
        self.tax_rate = tax_rate if tax_rate is not None else default_tax
        self.buy_fee_rate = 1 + self.commission_rate
        self.sell_fee_rate = 1 + self.commission_rate + self.tax_rate
        
        # 슬리피지
        self.slippage_buy = slippage_buy if slippage_buy is not None else self.DEFAULT_SLIPPAGE_BUY
        self.slippage_sell = slippage_sell if slippage_sell is not None else self.DEFAULT_SLIPPAGE_SELL
        
        self.min_market_cap = min_market_cap
        
        # 포트폴리오 상태
        self.cash = self.initial_capital
        self.holdings: Dict[str, Dict] = {}  # {code: {'quantity': int, 'avg_price': float, 'buy_date': str, 'high_water_mark': float}}
        self.trades: List[Dict] = []
        self.daily_portfolio_value: List[Dict] = []
        
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """모멘텀 전략용 지표 계산"""
        df = df.copy()
        
        # 진입 기준 수익률 (기본 5일 — 한국 테마주 싸이클)
        df['return_entry'] = df['close'].pct_change(periods=self.momentum_entry_period) * 100
        
        # 추세 확인용 수익률
        df['return_long'] = df['close'].pct_change(periods=self.momentum_long) * 100
        
        # 보조 지표
        df['return_5d'] = df['close'].pct_change(periods=5) * 100
        
        # 거래량 지표
        df['volume_ma20'] = df['volume'].rolling(window=20, min_periods=10).mean()
        df['volume_ratio'] = df['volume'] / df['volume_ma20'].replace(0, np.nan)
        
        # 이동평균
        df['ma200'] = df['close'].rolling(window=self.ma200_period, min_periods=self.ma200_period).mean()
        df['ma20'] = df['close'].rolling(window=20, min_periods=10).mean()
        df['ma5'] = df['close'].rolling(window=5, min_periods=3).mean()
        
        # 변동성 (ATR 기반 트레일링 스탑 참고용)
        df['atr14'] = self._calculate_atr(df, period=14)
        
        return df
    
    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """ATR (Average True Range) 계산"""
        high = df['high']
        low = df['low']
        close = df['close'].shift(1)
        
        tr1 = high - low
        tr2 = (high - close).abs()
        tr3 = (low - close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        return tr.rolling(window=period, min_periods=period).mean()
    
    def check_buy_signal(
        self,
        code: str,
        date: str,
        df: pd.DataFrame,
        current_holdings_count: int
    ) -> Tuple[bool, Optional[float]]:
        """모멘텀 매수 신호 확인
        
        조건:
        1. 진입기간 수익률 > entry_threshold (%)
        2. 추세기간 수익률 > long_threshold (%) (완만 상승 유지)
        3. 거래량 20일 평균 대비 surge_ratio 배 이상
        4. 최대 보유 종목 수 미만
        """
        try:
            if current_holdings_count >= self.max_holdings:
                return False, None
            
            if date not in df.index:
                return False, None
            
            idx = df.index.get_loc(date)
            # 최소 데이터 기간: 진입기간 + 버퍼 (20일이면 충분)
            min_period = self.momentum_entry_period + 15
            if idx < min_period:
                return False, None
            
            current = df.iloc[idx]
            
            # 값 유효성 체크
            if any(pd.isna(x) for x in [
                current.get('return_entry'), current.get('return_long'),
                current.get('volume_ratio'),
            ]):
                return False, None
            
            close = current['close']
            if np.isnan(close) or close <= 0:
                return False, None
            
            # 1) 진입 모멘텀 조건: 5일 급등
            if current['return_entry'] < self.momentum_entry_threshold:
                return False, None
            
            # 2) 추세 확인: 20일 완만 상승
            if current['return_long'] < self.momentum_long_threshold:
                return False, None
            
            # 3) 거래량 폭발: 진짜 이탈 확인
            if current['volume_ratio'] < self.volume_surge_ratio:
                return False, None
            
            return True, close
            
        except (KeyError, IndexError) as e:
            logger.warning(f"모멘텀 매수 신호 오류 ({code}, {date}): {e}")
            return False, None
    
    def check_sell_signal(
        self,
        code: str,
        date: str,
        df: pd.DataFrame,
        avg_price: float,
        buy_date: str,
        high_water_mark: float
    ) -> Tuple[bool, Optional[float], str]:
        """모멘텀 매도 신호 확인
        
        조건 (OR):
        1. 수익 목표: 당일 고가 기준 수익률 > profit_target_pct (%)
        2. 손절: 당일 저가 기준 손실률 > stop_loss_pct (%)
        3. 모멘텀 소멸: 진입기간 수익률 < decay_threshold
        4. 최대 보유 기간 초과
        """
        try:
            if date not in df.index:
                return False, None, ""
            
            idx = df.index.get_loc(date)
            if idx < 5:
                return False, None, ""
            
            current = df.iloc[idx]
            close = current['close']
            
            if np.isnan(close) or close <= 0:
                return False, None, ""
            
            reasons = []
            
            # 현재 수익률 (종가 기준)
            profit_rate = (close - avg_price) / avg_price * 100
            
            # 1) 수익 목표 도달 (종가 기준)
            if profit_rate >= self.profit_target_pct:
                reasons.append(f"수익목표(종가수익률={profit_rate:.2f}%)")
            
            # 2) 손절 (종가 기준)
            if profit_rate <= -self.stop_loss_pct:
                reasons.append(f"손절(종가수익률={profit_rate:.2f}%)")
            
            # 3) 모멘텀 소멸 (진입기간 수익률이 음수 = 추세 반전)
            current_return = current.get('return_entry')
            if current_return is not None and not np.isnan(current_return):
                if current_return < self.momentum_decay_threshold:
                    reasons.append(f"모멘텀소멸({self.momentum_entry_period}일수익률={current_return:.1f}%)")
            
            # 4) 최대 보유 기간
            if buy_date:
                buy_dt = pd.to_datetime(buy_date, format='%Y%m%d')
                current_dt = pd.to_datetime(date, format='%Y%m%d')
                holding_days = (current_dt - buy_dt).days
                if holding_days > self.max_holding_days:
                    reasons.append(f"최대보유초과({holding_days}일)")
            
            if reasons:
                return True, close, " + ".join(reasons)
            
            return False, None, ""
            
        except (KeyError, IndexError) as e:
            logger.warning(f"모멘텀 매도 신호 오류 ({code}, {date}): {e}")
            return False, None, ""
    
    def execute_buy(self, code: str, price: float, date: str, budget: float):
        """매수 실행"""
        if budget <= 0 or price <= 0:
            return
        
        execution_price = math.ceil(price * (1 + self.slippage_buy))
        quantity = int(budget / (execution_price * self.buy_fee_rate))
        if quantity < 1:
            return
        
        buy_amount = quantity * execution_price
        total_cost = math.floor(buy_amount * self.buy_fee_rate)
        
        if total_cost > self.cash:
            quantity = int((self.cash / self.buy_fee_rate) / execution_price)
            if quantity < 1:
                return
            buy_amount = quantity * execution_price
            total_cost = math.floor(buy_amount * self.buy_fee_rate)
        
        self.cash -= total_cost
        
        self.holdings[code] = {
            'quantity': quantity,
            'avg_price': execution_price,
            'buy_date': date,
            'high_water_mark': execution_price,  # 최초 고점은 매입가
        }
        
        self.trades.append({
            'date': date,
            'code': code,
            'type': 'buy',
            'price': price,
            'execution_price': execution_price,
            'quantity': quantity,
            'amount': buy_amount,
            'total_cost': total_cost,
        })
        
        display = f"{code}"
        logger.info(f"[모멘텀][{date}] 매수: {display}, 체결가: {execution_price:,.0f}, 수량: {quantity}, 총액: {total_cost:,.0f}")
    
    def execute_sell(self, code: str, price: float, date: str, reason: str = ""):
        """매도 실행"""
        if code not in self.holdings:
            return
        
        holding = self.holdings[code]
        quantity = holding['quantity']
        avg_price = holding['avg_price']
        
        execution_price = math.floor(price * (1 - self.slippage_sell))
        sell_amount = quantity * execution_price
        net_proceeds = math.floor(sell_amount / self.sell_fee_rate)
        
        self.cash += net_proceeds
        
        buy_cost = quantity * avg_price * self.buy_fee_rate
        profit = net_proceeds - buy_cost
        profit_rate = (profit / buy_cost) * 100
        
        del self.holdings[code]
        
        self.trades.append({
            'date': date,
            'code': code,
            'type': 'sell',
            'price': price,
            'execution_price': execution_price,
            'quantity': quantity,
            'amount': sell_amount,
            'net_proceeds': net_proceeds,
            'avg_buy_price': avg_price,
            'profit': profit,
            'profit_rate': profit_rate,
            'reason': reason,
        })
        
        display = f"{code}"
        logger.info(f"[모멘텀][{date}] 매도: {display}, 체결가: {execution_price:,.0f}, 수량: {quantity}, 수익: {profit:,.0f} ({profit_rate:.2f}%) [{reason}]")
    
    def calculate_portfolio_value(self, date: str, price_data: Dict[str, pd.DataFrame]) -> float:
        """포트폴리오 가치 계산"""
        total_value = self.cash
        for code, holding in self.holdings.items():
            if code not in price_data:
                continue
            df = price_data[code]
            if date in df.index:
                price = df.loc[date, 'close']
            else:
                price = df.iloc[-1]['close']
            total_value += holding['quantity'] * price
        return total_value
    
    def run_backtest(
        self,
        price_data: Dict[str, pd.DataFrame],
        start_date: str = None,
        end_date: str = None,
        availability_map: Dict[str, tuple] = None,
        monthly_universe_map: Dict[str, list] = None,
    ) -> Dict:
        """백테스트 실행"""
        logger.info("모멘텀 백테스트 시작...")
        if availability_map:
            logger.info("워크포워드 모드: 종목별 데이터 가용 기간 필터 적용")
        if monthly_universe_map:
            logger.info("워크포워드 모드: 월별 유니버스 스냅샷 필터 적용")
        
        # 초기화
        self.cash = self.initial_capital
        self.holdings = {}
        self.trades = []
        self.daily_portfolio_value = []
        
        # 지표 계산
        processed_data = {}
        for code, df in price_data.items():
            processed_data[code] = self.calculate_indicators(df)
        
        # 모든 거래일 추출
        all_dates = set()
        for df in processed_data.values():
            all_dates.update(df.index)
        trading_dates = sorted(list(all_dates))
        
        # pending orders
        pending_orders = defaultdict(list)
        
        # 날짜 필터링
        if start_date:
            trading_dates = [d for d in trading_dates if d >= start_date]
        if end_date:
            trading_dates = [d for d in trading_dates if d <= end_date]
        
        logger.info(f"모멘텀 백테스트 기간: {trading_dates[0]} ~ {trading_dates[-1]}")
        logger.info(f"총 거래일: {len(trading_dates)}일")
        logger.info(f"종목 수: {len(processed_data)}")
        
        for idx, date in enumerate(trading_dates):
            # ---- 매도 신호 확인 및 당일 종가 즉시 체결 ----
            codes_to_sell = []
            for code in list(self.holdings.keys()):
                if code not in processed_data:
                    continue
                df = processed_data[code]
                holding = self.holdings[code]
                
                # 고가 갱신 (트레일링 불필요하지만 잔여 데이터 보존)
                if date in df.index:
                    close = df.loc[date, 'close']
                    if close > holding['high_water_mark']:
                        self.holdings[code]['high_water_mark'] = close
                
                sell_signal, sell_price, reason = self.check_sell_signal(
                    code, date, df,
                    holding['avg_price'],
                    holding['buy_date'],
                    holding['high_water_mark'],
                )
                if sell_signal:
                    codes_to_sell.append((code, sell_price, reason, date))
            
            # 당일 종가로 매도 체결 (슬리피지 적용)
            for code, price, reason, signal_date in codes_to_sell:
                self.execute_sell(code, price, date, reason)
            
            # ---- 매수 신호 확인 및 당일 종가 즉시 체결 ----
            current_holdings = len(self.holdings)
            available_slots = self.max_holdings - current_holdings
            
            if available_slots > 0:
                if monthly_universe_map:
                    yyyymm = date[:6]
                    snapshot_key = yyyymm if date[6:] >= '15' else _prev_yyyymm(yyyymm)
                    date_codes = monthly_universe_map.get(snapshot_key) or monthly_universe_map.get(yyyymm)
                    codes_to_check = [c for c in date_codes if c in processed_data and c not in self.holdings] if date_codes else []
                else:
                    codes_to_check = [c for c in processed_data if c not in self.holdings]
                
                buy_candidates = []
                for code in codes_to_check:
                    df = processed_data[code]
                    
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
                
                buy_candidates = buy_candidates[:available_slots]
                
                if buy_candidates:
                    investable_cash = self.cash * (1 - self.cash_reserve_ratio)
                    try:
                        portfolio_value = self.calculate_portfolio_value(date, processed_data)
                        cap_amount = portfolio_value * self.max_position_ratio
                    except Exception:
                        cap_amount = self.initial_capital * self.max_position_ratio
                    
                    b = investable_cash / len(buy_candidates)
                    if cap_amount is not None:
                        b = min(b, cap_amount)
                    
                    for (code, price) in buy_candidates:
                        df = processed_data.get(code)
                        if df is None or date not in df.index or code in self.holdings:
                            continue
                        exec_price = df.loc[date, 'close']
                        if np.isnan(exec_price) or exec_price <= 0:
                            continue
                        self.execute_buy(code, exec_price, date, b)
            
            # 일별 포트폴리오 가치 기록
            pv = self.calculate_portfolio_value(date, processed_data)
            self.daily_portfolio_value.append({
                'date': date,
                'portfolio_value': pv,
                'holdings_count': len(self.holdings),
                'cash': self.cash,
            })
        
        # 결과 집계
        return self._collect_results()
    
    def _collect_results(self) -> Dict:
        """백테스트 결과 집계"""
        final_value = self.calculate_portfolio_value(
            self.daily_portfolio_value[-1]['date'] if self.daily_portfolio_value else '',
            {}
        )
        
        total_return = ((final_value - self.initial_capital) / self.initial_capital) * 100
        
        # 연환산 수익률
        first_date = self.daily_portfolio_value[0]['date'] if self.daily_portfolio_value else ''
        last_date = self.daily_portfolio_value[-1]['date'] if self.daily_portfolio_value else ''
        if first_date and last_date:
            days = (pd.to_datetime(last_date) - pd.to_datetime(first_date)).days
            years = days / 365.0 if days > 0 else 1
            annual_return = ((1 + total_return / 100) ** (1 / years) - 1) * 100
        else:
            annual_return = 0
        
        # 샤프 비율 (일별 수익률 기준)
        daily_returns = []
        prev_value = self.initial_capital
        for d in self.daily_portfolio_value:
            v = d['portfolio_value']
            if prev_value > 0:
                daily_returns.append((v - prev_value) / prev_value)
            prev_value = v
        
        if daily_returns:
            avg_return = np.mean(daily_returns)
            std_return = np.std(daily_returns)
            sharpe = (avg_return / std_return * np.sqrt(252)) if std_return > 0 else 0
        else:
            sharpe = 0
        
        # MDD
        peak = self.initial_capital
        mdd = 0
        for d in self.daily_portfolio_value:
            v = d['portfolio_value']
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > mdd:
                mdd = dd
        
        # 승률
        sell_trades = [t for t in self.trades if t['type'] == 'sell']
        win_trades = [t for t in sell_trades if t['profit'] > 0]
        win_rate = (len(win_trades) / len(sell_trades) * 100) if sell_trades else 0
        
        avg_profit = np.mean([t['profit_rate'] for t in sell_trades]) if sell_trades else 0
        total_profit = sum(t['profit'] for t in sell_trades)
        
        return {
            'initial_capital': self.initial_capital,
            'final_value': final_value,
            'total_return': total_return,
            'annual_return': annual_return,
            'sharpe_ratio': sharpe,
            'mdd': mdd,
            'total_trades': len(self.trades),
            'buy_trades': len([t for t in self.trades if t['type'] == 'buy']),
            'sell_trades': len([t for t in self.trades if t['type'] == 'sell']),
            'win_rate': win_rate,
            'avg_profit_rate': avg_profit,
            'total_profit': total_profit,
        }


def _prev_yyyymm(yyyymm: str) -> str:
    """YYYYMM → 이전 월"""
    year = int(yyyymm[:4])
    month = int(yyyymm[4:6])
    if month == 1:
        return f"{year - 1}12"
    return f"{year}{month - 1:02d}"
