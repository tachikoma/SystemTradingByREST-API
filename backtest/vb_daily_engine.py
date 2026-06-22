"""변동성 돌파 — 일단위 T+1 버전

D일 종가 ≥ 목표가 확인 → D+1일 시가 매수 → N일 보유 후 시가 매도
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from backtest.run_backtest import load_price_data_from_db, load_universe_availability, load_monthly_universe_snapshots

INITIAL_CAPITAL = 10_000_000
MAX_HOLDINGS = 5
COMMISSION = 0.00015
TAX = 0.0020
SLIPPAGE = 0.002


def simulate_vb_daily(
    price_data: Dict,
    availability_map: Dict,
    monthly_universe_map: Dict,
    start_date: str,
    end_date: str = '20260604',
    k: float = 0.5,
    ma_filter_period: int = 0,
    stop_loss_pct: float = -8.0,
    hold_days: int = 5,
) -> Dict:
    cash = float(INITIAL_CAPITAL)
    holdings: Dict[str, Dict] = {}
    pending_signals: List[Tuple[str, float]] = []
    daily_values = []
    total_buys = 0
    total_sells = 0
    wins = 0
    losses = 0
    peak_value = INITIAL_CAPITAL
    max_drawdown = 0.0

    all_dates = sorted(set(
        date for df in price_data.values() for date in df.index
        if start_date <= date <= end_date
    ))
    logger.info(f"총 거래일: {len(all_dates)}")
    date_set = set(all_dates)

    for date in all_dates:
        date_dt = datetime.strptime(date, '%Y%m%d')
        yyyymm = date[:6]

        # 0) 전일 신호 → 익일 시가 매수
        if pending_signals:
            investable = cash * 0.8 / len(pending_signals)
            for code, _ in pending_signals:
                if code not in price_data or date not in price_data[code].index:
                    continue
                row = price_data[code].loc[date]
                open_p = row['open']
                if np.isnan(open_p) or open_p <= 0:
                    continue
                buy_price = open_p * (1 + SLIPPAGE)
                cost_w_fee = buy_price * (1 + COMMISSION)
                qty = int(investable / cost_w_fee)
                if qty < 1:
                    qty = 1
                gross_cost = cost_w_fee * qty
                if gross_cost > cash:
                    qty = int(cash / cost_w_fee)
                    gross_cost = cost_w_fee * qty
                if qty < 1:
                    continue
                cash -= gross_cost
                holdings[code] = {
                    'qty': qty,
                    'avg_price': cost_w_fee,
                    'buy_date': date,
                }
                total_buys += 1
            pending_signals = []

        # 1) 매도 처리
        codes_to_sell = []
        for code, h in holdings.items():
            if code not in price_data or date not in price_data[code].index:
                continue
            df = price_data[code]
            idx = df.index.get_loc(date)
            row = df.iloc[idx]

            buy_date = h['buy_date']
            buy_dt = datetime.strptime(buy_date, '%Y%m%d')
            days_held = (date_dt - buy_dt).days

            # 보유일 경과 → 시가 매도
            if days_held >= hold_days:
                sell_price = row['open']
                if np.isnan(sell_price) or sell_price <= 0:
                    continue
                exec_price = sell_price * (1 - SLIPPAGE)
                revenue = exec_price * h['qty']
                cost = h['avg_price'] * h['qty']
                fee = revenue * (COMMISSION + TAX)
                profit = revenue - cost - fee
                codes_to_sell.append((code, profit, revenue, fee))
                continue

            # 손절
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
                    codes_to_sell.append((code, profit, revenue, fee))

        for code, profit, revenue, fee in codes_to_sell:
            if code in holdings:
                holdings.pop(code)
                cash += revenue - fee
                total_sells += 1
                if profit > 0:
                    wins += 1
                else:
                    losses += 1

        # 2) 매수 신호 (D일 종가 기준)
        available_slots = MAX_HOLDINGS - len(holdings)
        if available_slots > 0:
            signals = []
            sorted_codes = sorted(
                c for c in price_data
                if c in availability_map and date in price_data[c].index
            )
            if monthly_universe_map and yyyymm in monthly_universe_map:
                universe_set = set(monthly_universe_map[yyyymm])
                sorted_codes = [c for c in sorted_codes if c in universe_set]

            for code in sorted_codes:
                if len(signals) >= available_slots:
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
                close = row['close']
                prev_range = prev['high'] - prev['low']

                if np.isnan(open_p) or np.isnan(close) or np.isnan(prev_range) or prev_range <= 0:
                    continue

                target = open_p + prev_range * k

                if close < target:
                    continue

                # MA 필터 (당일 시가 기준)
                if ma_filter_period > 0:
                    if idx < ma_filter_period:
                        continue
                    ma = df['close'].iloc[idx - ma_filter_period:idx].mean()
                    if np.isnan(ma) or open_p <= ma:
                        continue

                signals.append((code, target))

            # 내일 매수 예약
            pending_signals = signals
        else:
            pending_signals = []

        # 3) 포트폴리오 가치
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

    final_value = daily_values[-1]['portfolio_value'] if daily_values else INITIAL_CAPITAL
    total_return = (final_value - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    days = len(daily_values)
    years = days / 252
    annual_return = ((1 + total_return / 100) ** (1 / years) - 1) * 100 if years > 0 else 0
    win_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0

    return {
        'total_return': total_return, 'annual_return': annual_return,
        'mdd': max_drawdown, 'total_trades': total_buys, 'buy_trades': total_buys,
        'win_rate': win_rate,
    }


if __name__ == '__main__':
    start_date = (datetime.now() - timedelta(days=10 * 365)).strftime('%Y%m%d')
    logger.info(f"VB 일단위 백테스트 시작일: {start_date}")

    price_data, _ = load_price_data_from_db()
    availability = load_universe_availability('backtest_data')
    monthly_snapshots = load_monthly_universe_snapshots('backtest_data')
    availability_map = {code: (info[0], info[1]) for code, info in availability.items()}

    param_sets = [
        # (tag, k, ma_filter, stop_loss, hold_days)
        ('vb_d_k03_h5',  0.3, 0, -8.0, 5),
        ('vb_d_k05_h5',  0.5, 0, -8.0, 5),
        ('vb_d_k07_h5',  0.7, 0, -8.0, 5),
        ('vb_d_k05_h3',  0.5, 0, -8.0, 3),
        ('vb_d_k05_h10', 0.5, 0, -8.0, 10),
        ('vb_d_k05_ma5', 0.5, 5, -8.0, 5),
        ('vb_d_k05_stop5', 0.5, 0, -5.0, 5),
        ('vb_d_k05_stop10', 0.5, 0, -10.0, 5),
    ]

    results_summary = []
    for tag, k, ma_f, stop, hold in param_sets:
        logger.info(f"\n--- [{tag}] k={k} MA={ma_f} stop={stop} hold={hold}일 ---")
        results = simulate_vb_daily(
            price_data=price_data, availability_map=availability_map,
            monthly_universe_map=monthly_snapshots, start_date=start_date,
            k=k, ma_filter_period=ma_f, stop_loss_pct=stop, hold_days=hold,
        )
        logger.info(f"[{tag}] 수익률={results['total_return']:.2f}% 연환산={results['annual_return']:.2f}% MDD={results['mdd']:.2f}% 매수={results['buy_trades']} 승률={results['win_rate']:.1f}%")
        results_summary.append(results)
        results_summary[-1]['tag'] = tag

    print(f"\n{'='*60}")
    print(f"{'VB 일단위(T+1) 10년 결과':^60}")
    print(f"{'='*60}")
    print(f"{'Tag':<18} {'수익률':>8} {'연환산':>8} {'MDD':>8} {'매수':>5} {'승률':>6}")
    print('-'*58)
    for r in results_summary:
        print(f"{r['tag']:<18} {r['total_return']:>8.2f} {r['annual_return']:>8.2f} {r['mdd']:>8.2f} {r['buy_trades']:>5} {r['win_rate']:>6.1f}")
