"""
RSI 전략 백테스트 엔진

RSIStrategy의 매매 로직을 재현하여 과거 데이터로 백테스트를 수행합니다.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class BacktestEngine:
    """백테스트 실행 엔진"""
    
    def __init__(
        self,
        initial_capital: float = 10_000_000,  # 초기 자본금 (1천만원)
        max_holdings: int = 10,  # 최대 보유 종목 수
        rsi_period: int = 2,  # RSI 계산 기간
        ma_short: int = 20,  # 단기 이동평균
        ma_long: int = 60,  # 장기 이동평균
        rsi_sell_threshold: float = 80,  # RSI 매도 기준
        rsi_buy_threshold: float = 5,  # RSI 매수 기준
        price_drop_threshold: float = -2,  # 가격 하락 기준 (%)
        commission_rate: float = 0.00015,  # 거래 수수료율 (편도 0.015%)
        tax_rate: float = 0.0025,  # 거래세 (매도 시만 0.25%)
    ):
        self.initial_capital = initial_capital
        self.max_holdings = max_holdings
        self.rsi_period = rsi_period
        self.ma_short = ma_short
        self.ma_long = ma_long
        self.rsi_sell_threshold = rsi_sell_threshold
        self.rsi_buy_threshold = rsi_buy_threshold
        self.price_drop_threshold = price_drop_threshold
        self.commission_rate = commission_rate
        self.tax_rate = tax_rate
        
        # 포트폴리오 상태
        self.cash = initial_capital
        self.holdings: Dict[str, Dict] = {}  # {code: {'quantity': int, 'avg_price': float}}
        
        # 거래 기록
        self.trades: List[Dict] = []
        self.daily_portfolio_value: List[Dict] = []
        
    def calculate_rsi(self, prices: pd.Series, period: int = None) -> pd.Series:
        """RSI 계산
        
        Args:
            prices: 종가 시계열
            period: RSI 기간 (기본값은 self.rsi_period)
            
        Returns:
            RSI 시계열
        """
        if period is None:
            period = self.rsi_period
            
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        # ZeroDivision 방지
        rs = gain / loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi = rsi.fillna(0)
        
        return rsi
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """기술적 지표 계산
        
        Args:
            df: OHLCV 데이터프레임
            
        Returns:
            지표가 추가된 데이터프레임
        """
        df = df.copy()
        
        # RSI 계산
        df['rsi'] = self.calculate_rsi(df['close'], self.rsi_period)
        
        # 이동평균 계산
        df['ma20'] = df['close'].rolling(window=self.ma_short, min_periods=1).mean()
        df['ma60'] = df['close'].rolling(window=self.ma_long, min_periods=1).mean()
        
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
            
            # 2거래일 전 종가
            close_2days_ago = df.iloc[idx - 2]['close']
            
            # 값 유효성 체크
            if np.isnan(rsi) or np.isnan(ma20) or np.isnan(ma60) or close_2days_ago == 0:
                return False, None
            
            # 가격 변동률 계산
            price_diff = (close - close_2days_ago) / close_2days_ago * 100
            
            # 매수 조건 확인
            # 1) ma20 > ma60 (단기 이평 > 장기 이평)
            # 2) RSI < 5 (과매도)
            # 3) 2일 전 대비 -2% 이상 하락
            if ma20 > ma60 and rsi < self.rsi_buy_threshold and price_diff < self.price_drop_threshold:
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
            
            # 값 유효성 체크
            if np.isnan(rsi):
                return False, None
            
            # 매도 조건 확인
            # 1) RSI > 80 (과매수)
            # 2) 현재가 > 매입가 (수익 실현)
            if rsi > self.rsi_sell_threshold and close > avg_purchase_price:
                return True, close
            
            return False, None
            
        except (KeyError, IndexError) as e:
            logger.warning(f"매도 신호 확인 중 오류 ({code}, {date}): {e}")
            return False, None
    
    def execute_buy(self, code: str, price: float, date: str, budget: float):
        """매수 주문 실행
        
        Args:
            code: 종목 코드
            price: 매수 가격
            date: 거래 날짜
            budget: 매수에 사용할 예산
        """
        # 매수 가능 수량 계산
        quantity = int(budget / price)
        
        if quantity < 1:
            return
        
        # 수수료 포함 실제 매수 금액
        buy_amount = quantity * price
        commission = buy_amount * self.commission_rate
        total_cost = buy_amount + commission
        
        # 예산 체크
        if total_cost > self.cash:
            # 예산에 맞게 수량 재조정
            quantity = int((self.cash / (1 + self.commission_rate)) / price)
            if quantity < 1:
                return
            buy_amount = quantity * price
            commission = buy_amount * self.commission_rate
            total_cost = buy_amount + commission
        
        # 현금 차감
        self.cash -= total_cost
        
        # 포지션 추가 또는 업데이트
        if code in self.holdings:
            # 기존 보유 종목 추가 매수
            old_quantity = self.holdings[code]['quantity']
            old_avg_price = self.holdings[code]['avg_price']
            new_quantity = old_quantity + quantity
            new_avg_price = (old_quantity * old_avg_price + buy_amount) / new_quantity
            
            self.holdings[code] = {
                'quantity': new_quantity,
                'avg_price': new_avg_price
            }
        else:
            # 신규 매수
            self.holdings[code] = {
                'quantity': quantity,
                'avg_price': price
            }
        
        # 거래 기록
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
        
        logger.info(f"[{date}] 매수: {code}, 가격: {price:,.0f}, 수량: {quantity}, 총액: {total_cost:,.0f}")
    
    def execute_sell(self, code: str, price: float, date: str):
        """매도 주문 실행
        
        Args:
            code: 종목 코드
            price: 매도 가격
            date: 거래 날짜
        """
        if code not in self.holdings:
            return
        
        quantity = self.holdings[code]['quantity']
        avg_price = self.holdings[code]['avg_price']
        
        # 수수료 + 거래세 포함 실제 매도 금액
        sell_amount = quantity * price
        commission = sell_amount * self.commission_rate
        tax = sell_amount * self.tax_rate
        net_proceeds = sell_amount - commission - tax
        
        # 현금 증가
        self.cash += net_proceeds
        
        # 수익률 계산
        profit = sell_amount - (quantity * avg_price)
        profit_rate = (profit / (quantity * avg_price)) * 100
        
        # 포지션 제거
        del self.holdings[code]
        
        # 거래 기록
        self.trades.append({
            'date': date,
            'code': code,
            'type': 'sell',
            'price': price,
            'quantity': quantity,
            'amount': sell_amount,
            'commission': commission,
            'tax': tax,
            'net_proceeds': net_proceeds,
            'avg_buy_price': avg_price,
            'profit': profit,
            'profit_rate': profit_rate
        })
        
        logger.info(f"[{date}] 매도: {code}, 가격: {price:,.0f}, 수량: {quantity}, "
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
        logger.info(f"유니버스 종목 수: {len(processed_data)}")
        
        # 각 거래일마다 시뮬레이션
        for date in trading_dates:
            # 1) 매도 신호 확인 (보유 종목)
            codes_to_sell = []
            for code in list(self.holdings.keys()):
                if code not in processed_data:
                    continue
                
                df = processed_data[code]
                avg_price = self.holdings[code]['avg_price']
                
                sell_signal, sell_price = self.check_sell_signal(code, date, df, avg_price)
                if sell_signal:
                    codes_to_sell.append((code, sell_price))
            
            # 매도 실행
            for code, price in codes_to_sell:
                self.execute_sell(code, price, date)
            
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
                
                # 매수 가능한 종목 수만큼만 매수
                buy_candidates = buy_candidates[:available_slots]
                
                # 매수 예산 배분 (균등 분할)
                if buy_candidates:
                    budget_per_stock = self.cash / len(buy_candidates)
                    
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
        
        # MDD (Maximum Drawdown)
        cumulative = (1 + df['returns']).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max
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
            'daily_values': df
        }
        
        return results
