"""변동성 돌파 전략 백테스트

강환국 스타일: 당일 시가 + 전일 변동성*k 돌파 시 매수 → 익일 시가 매도
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple

from backtest.run_backtest import load_price_data_from_db, load_universe_availability, load_monthly_universe_snapshots

# 전역 설정
INITIAL_CAPITAL = 10_000_000
MAX_HOLDINGS = 5
COMMISSION = 0.00015  # 실전 수수료
TAX = 0.0020          # 거래세
SLIPPAGE = 0.002      # 슬리피지


def simulate_vb_backtest(
    price_data: Dict,
    availability_map: Dict,
    monthly_universe_map: Dict,
    start_date: str,
    end_date: str = '20260604',
    k: float = 0.5,
    ma_filter_period: int = 0,
    stop_loss_pct: float = -5.0,
    hold_days: int = 1,
) -> Dict:
    """변동성 돌파 백테스트

    Args:
        k: 변동성 계수 (0.3~0.7)
        ma_filter_period: 0=비활성, 5=종가>5MA 조건, 20=종가>20MA 조건
        stop_loss_pct: 손절 % (0=비활성)
        hold_days: 강제 보유일 수 (0=RSI 매도 조건 사용x, 단순 익일 청산)
    """
    cash = float(INITIAL_CAPITAL)
    holdings: Dict[str, Dict] = {}  # code -> {qty, avg_price, buy_date}
    daily_values = []
    total_buys = 0
    total_sells = 0
    wins = 0
    losses = 0
    peak_value = INITIAL_CAPITAL
    max_drawdown = 0.0

    # 전체 기간 설정
    all_dates = sorted(set(
        date for df in price_data.values() for date in df.index
        if start_date <= date <= end_date
    ))
    logger.info(f"총 거래일: {len(all_dates)}")

    for date in all_dates:
        date_dt = datetime.strptime(date, '%Y%m%d')
        yyyymm = date[:6]

        # 월말 청산/교체 (모멘텀 스타일이 아닌 VB는 일 단위 — skip)

        # 1) 매도 처리 (보유 종목)
        codes_to_sell = []
        for code, h in holdings.items():
            if code not in price_data or date not in price_data[code].index:
                continue
            df = price_data[code]
            idx = df.index.get_loc(date)
            row = df.iloc[idx]

            # 보유일 체크
            buy_date = h['buy_date']
            buy_dt = datetime.strptime(buy_date, '%Y%m%d')
            days_held = (date_dt - buy_dt).days

            # 매도 조건: hold_days 경과 후 익일 시가 매도
            if days_held >= hold_days:
                sell_price = row['open']
                # 익일 시가 매도 (이미 익일이므로 open 사용)
                if np.isnan(sell_price) or sell_price <= 0:
                    continue
                # 슬리피지
                exec_price = sell_price * (1 - SLIPPAGE)
                revenue = exec_price * h['qty']
                cost = h['avg_price'] * h['qty']
                fee = revenue * (COMMISSION + TAX)
                profit = revenue - cost - fee
                codes_to_sell.append((code, exec_price, profit, revenue))

            # 손절 (당일 저가 기준 — 현실적 체결가: stop_price와 low 중 낮은 쪽 + 슬리피지)
            if stop_loss_pct != 0:
                low = row['low']
                stop_level = h['avg_price'] * (1 + stop_loss_pct / 100)
                if not np.isnan(low) and low <= stop_level:
                    sell_price = min(stop_level, low)
                    exec_price = sell_price * (1 - SLIPPAGE)
                    revenue = exec_price * h['qty']
                    cost = h['avg_price'] * h['qty']
                    fee = revenue * (COMMISSION + TAX)
                    profit = revenue - cost - fee
                    codes_to_sell.append((code, exec_price, profit, revenue))

        for code, exec_price, profit, revenue in codes_to_sell:
            if code in holdings:
                h = holdings.pop(code)
                fee = revenue * (COMMISSION + TAX)
                cash += revenue - fee
                total_sells += 1
                if profit > 0:
                    wins += 1
                else:
                    losses += 1

        # 2) 매수 신호 체크
        available_slots = MAX_HOLDINGS - len(holdings)
        if available_slots > 0:
            buy_candidates = []

            # 무작위 순서 방지를 위해 코드 정렬
            sorted_codes = [c for c in sorted(price_data.keys())
                          if c in availability_map
                          and c in price_data and date in price_data[c].index]

            # 유니버스 필터: 상장 상태
            if monthly_universe_map and yyyymm in monthly_universe_map:
                universe_set = set(monthly_universe_map[yyyymm])
                sorted_codes = [c for c in sorted_codes if c in universe_set]

            for code in sorted_codes:
                if len(buy_candidates) >= available_slots:
                    break
                if code in holdings:
                    continue

                df = price_data[code]
                idx = df.index.get_loc(date)
                if idx < 2:
                    continue
                row = df.iloc[idx]
                prev = df.iloc[idx - 1]

                open_p = row['open']
                high = row['high']
                prev_range = prev['high'] - prev['low']

                if np.isnan(open_p) or np.isnan(high) or np.isnan(prev_range) or prev_range <= 0:
                    continue

                # 목표가 = 시가 + 전일 변동성 * k
                target = open_p + prev_range * k

                # 돌파 확인: 당일 고가 >= 목표가
                if high < target:
                    continue

                # MA 필터
                if ma_filter_period > 0:
                    if idx < ma_filter_period:
                        continue
                    ma = df['close'].iloc[idx - ma_filter_period:idx].mean()
                    if np.isnan(ma) or open_p <= ma:
                        continue

                buy_candidates.append((code, target))

            # 매수 실행 (돌파 당일 종가 or 목표가로 체결)
            if buy_candidates:
                investable = cash * 0.8 / len(buy_candidates)
                for code, target_price in buy_candidates:
                    buy_price = target_price * (1 + SLIPPAGE)
                    qty = int(investable / buy_price)
                    if qty < 1:
                        qty = 1
                    gross_cost = buy_price * qty * (1 + COMMISSION)
                    if gross_cost > cash:
                        qty = int(cash / (buy_price * (1 + COMMISSION)))
                        gross_cost = buy_price * qty * (1 + COMMISSION)
                    if qty < 1:
                        continue
                    cash -= gross_cost
                    avg_price = buy_price * (1 + COMMISSION)
                    holdings[code] = {
                        'qty': qty,
                        'avg_price': avg_price,
                        'buy_date': date,
                    }
                    total_buys += 1
                    logger.debug(f"[{date}] 매수: {code} target={target_price:.0f} exec={buy_price:.0f} qty={qty}")

        # 3) 포트폴리오 가치 기록
        portfolio_value = cash
        for code, h in holdings.items():
            if code in price_data and date in price_data[code].index:
                close = price_data[code].loc[date, 'close']
                if not np.isnan(close):
                    portfolio_value += float(close) * h['qty']

        daily_values.append({'date': date, 'portfolio_value': portfolio_value})
        peak_value = max(peak_value, portfolio_value)
        dd = (portfolio_value - peak_value) / peak_value * 100
        max_drawdown = min(max_drawdown, dd)

    # 최종 수익률
    final_value = daily_values[-1]['portfolio_value'] if daily_values else INITIAL_CAPITAL
    total_return = (final_value - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

    # 연환산
    days = len(daily_values)
    years = days / 252
    annual_return = ((1 + total_return / 100) ** (1 / years) - 1) * 100 if years > 0 else 0

    win_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0

    return {
        'total_return': total_return,
        'annual_return': annual_return,
        'mdd': max_drawdown,
        'total_trades': total_buys,
        'buy_trades': total_buys,
        'win_rate': win_rate,
    }


if __name__ == '__main__':
    start_date = (datetime.now() - timedelta(days=10 * 365)).strftime('%Y%m%d')
    logger.info(f"변동성 돌파 백테스트 시작일: {start_date}")

    price_data, _ = load_price_data_from_db()
    availability = load_universe_availability('backtest_data')
    monthly_snapshots = load_monthly_universe_snapshots('backtest_data')
    availability_map = {code: (info[0], info[1]) for code, info in availability.items()}

    param_sets = [
        # (tag, k, ma_filter, stop_loss, hold_days)
        ('vb_k03_ma0',  0.3, 0, -5.0, 1),
        ('vb_k04_ma0',  0.4, 0, -5.0, 1),
        ('vb_k05_ma0',  0.5, 0, -5.0, 1),
        ('vb_k06_ma0',  0.6, 0, -5.0, 1),
        ('vb_k07_ma0',  0.7, 0, -5.0, 1),
        ('vb_k05_ma5',  0.5, 5, -5.0, 1),
        ('vb_k05_ma20', 0.5, 20, -5.0, 1),
        ('vb_k05_stop3',0.5, 0, -3.0, 1),
        ('vb_k05_stop8',0.5, 0, -8.0, 1),
        ('vb_k05_hold2',0.5, 0, -5.0, 2),
        ('vb_k05_hold3',0.5, 0, -5.0, 3),
    ]

    results_summary = []
    for tag, k, ma_f, stop, hold in param_sets:
        logger.info(f"\n--- [{tag}] k={k} MA={ma_f} stop={stop}% hold={hold}일 ---")
        results = simulate_vb_backtest(
            price_data=price_data,
            availability_map=availability_map,
            monthly_universe_map=monthly_snapshots,
            start_date=start_date,
            k=k,
            ma_filter_period=ma_f,
            stop_loss_pct=stop,
            hold_days=hold,
        )
        tr = results['total_return']
        ar = results['annual_return']
        mdd = results['mdd']
        buys = results['buy_trades']
        wr = results['win_rate']
        logger.info(f"[{tag}] 수익률={tr:.2f}% 연환산={ar:.2f}% MDD={mdd:.2f}% 매수={buys} 승률={wr:.1f}%")
        results_summary.append(results)
        results_summary[-1]['tag'] = tag

    print(f"\n{'='*60}")
    print(f"{'변동성 돌파 10년 결과':^60}")
    print(f"{'='*60}")
    print(f"{'Tag':<15} {'수익률':>8} {'연환산':>8} {'MDD':>8} {'매수':>5} {'승률':>6}")
    print('-'*55)
    for r in results_summary:
        print(f"{r['tag']:<15} {r['total_return']:>8.2f} {r['annual_return']:>8.2f} {r['mdd']:>8.2f} {r['buy_trades']:>5} {r['win_rate']:>6.1f}")
