"""모멘텀 전략 백테스트

월간 리밸런싱: 과거 12개월 수익률 상위 N개 선정 → 익월 시가 동일비중 매수
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple

from backtest.run_backtest import load_price_data_from_db, load_universe_availability, load_monthly_universe_snapshots

INITIAL_CAPITAL = 10_000_000
MAX_HOLDINGS = 10
COMMISSION = 0.00015
TAX = 0.0020
SLIPPAGE = 0.002


def calc_returns_for_month(price_data, universe_codes, eval_date, lookback_months=12, skip_months=1):
    """모멘텀 스코어 계산: 과거 lookback_months 수익률 (최근 skip_months 제외)"""
    scores = {}
    eval_dt = datetime.strptime(eval_date, '%Y%m%d')

    # lookback 시작일 (예: 12개월 전)
    start_dt = eval_dt - timedelta(days=lookback_months * 31)
    # skip 기간 시작일 (예: 1개월 전)
    skip_dt = eval_dt - timedelta(days=skip_months * 31)

    for code in universe_codes:
        if code not in price_data:
            continue
        df = price_data[code]
        if len(df) < 2:
            continue

        # 평가일의 데이터 확인
        if eval_date not in df.index:
            continue

        # skip 기간 시작일 이후 가장 가까운 거래일 찾기
        all_dates = df.index.tolist()
        skip_idx = None
        start_idx = None
        eval_idx = df.index.get_loc(eval_date)

        for i in range(eval_idx, -1, -1):
            d = datetime.strptime(all_dates[i], '%Y%m%d')
            if d >= skip_dt and skip_idx is None:
                skip_idx = i
            if d <= start_dt:
                start_idx = i
                break

        if skip_idx is None or start_idx is None or skip_idx <= start_idx:
            continue

        close_start = df.iloc[start_idx]['close']
        close_end = df.iloc[skip_idx]['close']

        if np.isnan(close_start) or np.isnan(close_end) or close_start <= 0:
            continue

        ret = (close_end - close_start) / close_start * 100
        scores[code] = ret

    return scores


def simulate_momentum_backtest(
    price_data: Dict,
    availability_map: Dict,
    monthly_universe_map: Dict,
    start_date: str,
    end_date: str = '20260604',
    lookback_months: int = 12,
    skip_months: int = 1,
    top_n: int = 10,
    rebalance_freq_months: int = 1,
    use_ma200_filter: bool = False,
) -> Dict:
    cash = float(INITIAL_CAPITAL)
    holdings: Dict[str, Dict] = {}
    pending_buys: List[Tuple[str, float]] = []
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
    months_done = set()
    last_day_of_month_cache = {}

    def is_last_trading_day(d):
        """d가 이번 달의 마지막 거래일인지 확인"""
        next_d = (d + timedelta(days=1)).strftime('%Y%m%d')
        return next_d[:6] != d.strftime('%Y%m%d') or next_d not in date_set

    def is_first_trading_day(d):
        """d가 이번 달의 첫 거래일인지 확인"""
        prev_d = (d - timedelta(days=1)).strftime('%Y%m%d')
        return prev_d[:6] != d.strftime('%Y%m%d') or prev_d not in date_set

    for date in all_dates:
        date_dt = datetime.strptime(date, '%Y%m%d')
        yyyymm = date[:6]

        # 0) 익일 시가 매수 (pending_buys — 전월말 선정된 종목을 금일 시가에 매수)
        if pending_buys and is_first_trading_day(date_dt):
            investable = cash * 0.8 / len(pending_buys)
            codes_to_buy = []
            for code, _ in pending_buys:
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
            pending_buys = []

        # 1) 리밸런싱: 매도 (전월 마지막 거래일 종가)
        codes_to_sell = []
        if is_last_trading_day(date_dt):
            for code, h in list(holdings.items()):
                if code not in price_data or date not in price_data[code].index:
                    continue
                close = price_data[code].loc[date, 'close']
                if np.isnan(close) or close <= 0:
                    continue
                sell_price = close * (1 - SLIPPAGE)
                revenue = sell_price * h['qty']
                cost = h['avg_price'] * h['qty']
                fee = revenue * (COMMISSION + TAX)
                profit = revenue - cost - fee
                cash += revenue - fee
                holdings.pop(code)
                total_sells += 1
                if profit > 0:
                    wins += 1
                else:
                    losses += 1

        # 2) 리밸런싱: 신규 선정 (전월 마지막 거래일)
        if is_last_trading_day(date_dt) and yyyymm not in months_done:
            months_done.add(yyyymm)

            if monthly_universe_map and yyyymm in monthly_universe_map:
                universe_set = set(monthly_universe_map[yyyymm])
            else:
                universe_set = set(price_data.keys())

            scores = calc_returns_for_month(
                price_data, universe_set, date,
                lookback_months=lookback_months, skip_months=skip_months
            )

            if scores:
                sorted_stocks = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                selected = sorted_stocks[:top_n]

                if use_ma200_filter:
                    filtered = []
                    for code, score in selected:
                        if code not in price_data or date not in price_data[code].index:
                            continue
                        df = price_data[code]
                        idx = df.index.get_loc(date)
                        if idx < 200:
                            continue
                        ma200 = df['close'].iloc[idx - 200:idx].mean()
                        close_ = df.iloc[idx]['close']
                        if not np.isnan(ma200) and not np.isnan(close_) and close_ > ma200:
                            filtered.append((code, score))
                    selected = filtered[:top_n]

                # 승수 저장 — 익일 시가 매수를 위해
                pending_buys = [(code, 1.0) for code, _ in selected]

        # 포트폴리오 가치 기록
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
        'total_return': total_return,
        'annual_return': annual_return,
        'mdd': max_drawdown,
        'total_trades': total_buys,
        'buy_trades': total_buys,
        'win_rate': win_rate,
    }


if __name__ == '__main__':
    start_date = (datetime.now() - timedelta(days=10 * 365)).strftime('%Y%m%d')
    logger.info(f"모멘텀 백테스트 시작일: {start_date}")

    price_data, _ = load_price_data_from_db()
    availability = load_universe_availability('backtest_data')
    monthly_snapshots = load_monthly_universe_snapshots('backtest_data')
    availability_map = {code: (info[0], info[1]) for code, info in availability.items()}

    param_sets = [
        # (tag, lookback, skip, top_n, freq, ma200)
        ('mom_12m_top10',    12, 1, 10, 1, False),
        ('mom_12m_top20',    12, 1, 20, 1, False),
        ('mom_12m_top5',     12, 1,  5, 1, False),
        ('mom_6m_top10',      6, 1, 10, 1, False),
        ('mom_6m_top20',      6, 1, 20, 1, False),
        ('mom_3m_top10',      3, 1, 10, 1, False),
        ('mom_12m_top10_f',  12, 1, 10, 1, True),
        ('mom_6m_top10_f',    6, 1, 10, 1, True),
        ('mom_12m_top20_f',  12, 1, 20, 1, True),
        ('mom_6m_top20_f',    6, 1, 20, 1, True),
    ]

    results_summary = []
    for tag, lookback, skip, top_n, freq, ma200 in param_sets:
        logger.info(f"\n--- [{tag}] lookback={lookback}M skip={skip}M top={top_n} freq={freq}M MA200={ma200} ---")
        results = simulate_momentum_backtest(
            price_data=price_data,
            availability_map=availability_map,
            monthly_universe_map=monthly_snapshots,
            start_date=start_date,
            lookback_months=lookback,
            skip_months=skip,
            top_n=top_n,
            rebalance_freq_months=freq,
            use_ma200_filter=ma200,
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
    print(f"{'모멘텀 전략 10년 결과':^60}")
    print(f"{'='*60}")
    print(f"{'Tag':<18} {'수익률':>8} {'연환산':>8} {'MDD':>8} {'매수':>5} {'승률':>6}")
    print('-'*58)
    for r in results_summary:
        print(f"{r['tag']:<18} {r['total_return']:>8.2f} {r['annual_return']:>8.2f} {r['mdd']:>8.2f} {r['buy_trades']:>5} {r['win_rate']:>6.1f}")
