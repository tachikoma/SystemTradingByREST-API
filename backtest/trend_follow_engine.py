"""추세추종 엔진

1) KODEX 200 (069500) 200MA로 시장 추세 필터
2) 추세장에서만 개별 종목 상대강도 상위 N개 매수
3) 약세장 전환 시 전량 청산
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import pandas as pd
from backtest.run_backtest import load_price_data_from_db, load_universe_availability, load_monthly_universe_snapshots
from util.db_helper import execute_sql, resolve_date_column

INITIAL_CAPITAL = 10_000_000
MAX_HOLDINGS = 5
COMMISSION = 0.00015
TAX = 0.0020
SLIPPAGE = 0.002
MARKET_CODE = '069500'


def get_market_df(price_data: Dict) -> 'pd.DataFrame':
    """KODEX 200 데이터를 DataFrame으로 반환"""
    import pandas as pd
    if MARKET_CODE not in price_data:
        raise ValueError(f"{MARKET_CODE} (KODEX 200) not found in price_data")
    df = price_data[MARKET_CODE].copy()
    df.index = pd.to_datetime(df.index, format='%Y%m%d')
    return df


def compute_market_regime(market_df: 'pd.DataFrame', date: datetime, ma_period: int = 200) -> bool:
    """시장 추세 여부 (종가 > MA)"""
    if date not in market_df.index:
        return False
    pos = market_df.index.get_loc(date)
    if pos < ma_period:
        return False
    close = float(market_df.loc[date, 'close'])
    ma = market_df['close'].iloc[pos - ma_period:pos].mean()
    return close > ma


def _is_market_bullish(use_market_filter, market_df, date, ma_filter):
    """시장 추세 여부 (필터 OFF면 항상 True)"""
    if not use_market_filter:
        return True
    return compute_market_regime(market_df, pd.Timestamp(date), ma_period=ma_filter)


def _select_stocks_by_rs(price_data, sorted_codes, date, lookback_days, min_stock_price):
    """상대강도 상위 N개 선정"""
    candidates = []
    for code in sorted_codes:
        df = price_data[code]
        if date not in df.index:
            continue
        date_idx = df.index.get_loc(date)
        if date_idx < lookback_days:
            continue
        past_close = float(df.iloc[date_idx - lookback_days]['close'])
        curr_close = float(df.iloc[date_idx]['close'])
        if past_close <= 0 or curr_close <= 0:
            continue
        ret = (curr_close - past_close) / past_close
        if curr_close < min_stock_price:
            continue
        candidates.append((code, ret))
    candidates.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in candidates[:MAX_HOLDINGS]]


def _select_stocks_by_ma_bull(price_data, sorted_codes, date, min_stock_price, short_ma=50, long_ma=200):
    """MA 정배열 (단기MA > 장기MA) 종목 선정. 정렬: close - long_ma 차이 큰 순"""
    candidates = []
    for code in sorted_codes:
        df = price_data[code]
        if date not in df.index:
            continue
        date_idx = df.index.get_loc(date)
        if date_idx < long_ma:
            continue
        close = float(df.iloc[date_idx]['close'])
        if close < min_stock_price:
            continue
        ma_s = df['close'].iloc[date_idx - short_ma:date_idx].mean()
        ma_l = df['close'].iloc[date_idx - long_ma:date_idx].mean()
        if np.isnan(ma_s) or np.isnan(ma_l) or ma_s <= ma_l:
            continue
        candidates.append((code, close - ma_l))
    candidates.sort(key=lambda x: x[1], reverse=True)
    return [c[0] for c in candidates[:MAX_HOLDINGS]]


def simulate_trend_follow(
    price_data: Dict,
    availability_map: Dict,
    monthly_universe_map: Dict,
    start_date: str,
    end_date: str = '20260604',
    lookback_months: int = 6,
    ma_filter: int = 200,
    stop_loss_pct: float = -8.0,
    use_market_filter: bool = True,
    min_stock_price: int = 1000,
    stock_selection: str = 'rs',
) -> Dict:
    import pandas as pd

    cash = float(INITIAL_CAPITAL)
    holdings: Dict[str, Dict] = {}
    daily_values = []
    total_buys = 0
    total_sells = 0
    wins = 0
    losses = 0
    peak_value = INITIAL_CAPITAL
    max_drawdown = 0.0

    market_df = get_market_df(price_data)

    all_dates = sorted(set(
        date for df in price_data.values() for date in df.index
        if start_date <= date <= end_date
    ))
    logger.info(f"총 거래일: {len(all_dates)}")

    last_rebalance_ym = ''

    for date in all_dates:
        yyyymm = date[:6]

        if date not in market_df.index or date not in price_data.get(MARKET_CODE, pd.DataFrame()).index:
            continue

        market_bullish = _is_market_bullish(use_market_filter, market_df, date, ma_filter)

        # 약세장 전량 청산
        if use_market_filter and not market_bullish:
            for code in list(holdings.keys()):
                if code not in price_data or date not in price_data[code].index:
                    holdings.pop(code, None)
                    total_sells += 1
                    continue
                row = price_data[code].loc[date]
                sp = float(row['open']) if not np.isnan(row['open']) and row['open'] > 0 else float(row['close'])
                ep = sp * (1 - SLIPPAGE)
                h = holdings.pop(code)
                rev = ep * h['qty']
                cost = h['avg_price'] * h['qty']
                fee = rev * (COMMISSION + TAX)
                cash += rev - fee
                total_sells += 1
                if rev - cost - fee > 0: wins += 1
                else: losses += 1
            daily_values.append({'date': date, 'portfolio_value': cash})
            peak_value = max(peak_value, cash)
            dd = (cash - peak_value) / peak_value * 100
            max_drawdown = min(max_drawdown, dd)
            continue

        # 손절
        for code in list(holdings.keys()):
            if code not in price_data or date not in price_data[code].index:
                continue
            df = price_data[code]
            row = df.loc[date]
            if stop_loss_pct != 0:
                low = float(row['low'])
                stop_level = holdings[code]['avg_price'] * (1 + stop_loss_pct / 100)
                if not np.isnan(low) and low <= stop_level:
                    sp = min(stop_level, low) * (1 - SLIPPAGE)
                    h = holdings.pop(code)
                    rev = sp * h['qty']
                    cost = h['avg_price'] * h['qty']
                    fee = rev * (COMMISSION + TAX)
                    cash += rev - fee
                    total_sells += 1
                    if rev - cost - fee > 0: wins += 1
                    else: losses += 1

        # 월초 리밸런싱
        if yyyymm != last_rebalance_ym:
            last_rebalance_ym = yyyymm

            # 기존 보유 전량 청산
            for code in list(holdings.keys()):
                h = holdings.pop(code)
                if code not in price_data or date not in price_data[code].index:
                    continue
                row = price_data[code].loc[date]
                sp = float(row['open']) if not np.isnan(row['open']) and row['open'] > 0 else float(row['close'])
                ep = sp * (1 - SLIPPAGE)
                rev = ep * h['qty']
                cost = h['avg_price'] * h['qty']
                fee = rev * (COMMISSION + TAX)
                cash += rev - fee
                total_sells += 1
                if rev - cost - fee > 0: wins += 1
                else: losses += 1

            # 새 종목 선정
            if market_bullish:
                sorted_codes = sorted(
                    c for c in price_data
                    if c in availability_map and c != MARKET_CODE
                )
                if monthly_universe_map and yyyymm in monthly_universe_map:
                    universe_set = set(monthly_universe_map[yyyymm])
                    sorted_codes = [c for c in sorted_codes if c in universe_set]

                if stock_selection == 'rs':
                    selected = _select_stocks_by_rs(price_data, sorted_codes, date, lookback_months * 21, min_stock_price)
                elif stock_selection == 'ma_bull':
                    selected = _select_stocks_by_ma_bull(price_data, sorted_codes, date, min_stock_price)
                else:
                    selected = []

                investable = cash * 0.8 / len(selected) if selected else 0
                for code in selected:
                    if code not in price_data or date not in price_data[code].index:
                        continue
                    row = price_data[code].loc[date]
                    open_p = float(row['open'])
                    if np.isnan(open_p) or open_p <= 0:
                        continue
                    bp = open_p * (1 + SLIPPAGE)
                    cwf = bp * (1 + COMMISSION)
                    qty = max(1, int(investable / cwf))
                    gc = cwf * qty
                    if gc > cash:
                        qty = int(cash / cwf)
                        gc = cwf * qty
                    if qty < 1:
                        continue
                    cash -= gc
                    holdings[code] = {'qty': qty, 'avg_price': cwf, 'buy_date': date}
                    total_buys += 1

        # 포트폴리오 가치
        pv = cash
        for code, h in holdings.items():
            if code in price_data and date in price_data[code].index:
                close = float(price_data[code].loc[date, 'close'])
                if not np.isnan(close):
                    pv += close * h['qty']
        daily_values.append({'date': date, 'portfolio_value': pv})
        peak_value = max(peak_value, pv)
        dd = (pv - peak_value) / peak_value * 100
        max_drawdown = min(max_drawdown, dd)

    if not daily_values:
        return {'total_return': 0, 'annual_return': 0, 'mdd': 0, 'total_trades': 0, 'buy_trades': 0}

    final_value = daily_values[-1]['portfolio_value']
    total_return = (final_value - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    days = len(daily_values)
    years = days / 252
    annual_return = ((1 + total_return / 100) ** (1 / years) - 1) * 100 if years > 0 else 0
    buy_hold_return = 0
    if MARKET_CODE in price_data:
        mdf = price_data[MARKET_CODE]
        first_date = daily_values[0]['date']
        last_date = daily_values[-1]['date']
        if first_date in mdf.index and last_date in mdf.index:
            bh = (float(mdf.loc[last_date, 'close']) - float(mdf.loc[first_date, 'close'])) / float(mdf.loc[first_date, 'close']) * 100
            buy_hold_return = bh

    win_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0

    return {
        'total_return': total_return,
        'annual_return': annual_return,
        'mdd': max_drawdown,
        'buy_trades': total_buys,
        'sell_trades': total_sells,
        'total_trades': total_buys + total_sells,
        'win_rate': win_rate,
        'buy_hold_return': buy_hold_return,
    }


def _load_single_stock(code: str, db_name: str = 'backtest_data'):
    """DB에서 단일 종목 데이터 로드"""
    from util.db_helper import check_table_exist, execute_sql
    if not check_table_exist(db_name, code):
        return None
    sql = f"SELECT * FROM `{code}`"
    cur = execute_sql(db_name, sql)
    cols = [column[0] for column in cur.description]
    df = pd.DataFrame.from_records(data=cur.fetchall(), columns=cols)
    if df.empty:
        return None
    date_col = resolve_date_column(df)
    df = df.set_index(date_col)
    df.index.name = 'date'
    df.index = df.index.astype(str)
    return df


if __name__ == '__main__':
    start_date = (datetime.now() - timedelta(days=10 * 365)).strftime('%Y%m%d')
    logger.info(f"추세추종 백테스트 시작일: {start_date}")

    price_data, _ = load_price_data_from_db()

    # 069500 (KODEX 200) 별도 로드 (universe 미포함)
    mkt_df = _load_single_stock('069500')
    if mkt_df is not None:
        price_data['069500'] = mkt_df
        logger.info(f"069500 (KODEX 200) 로드 완료: {mkt_df.index.min()} ~ {mkt_df.index.max()}")
    else:
        logger.error("069500 (KODEX 200) 로드 실패")

    availability = load_universe_availability('backtest_data')
    monthly_snapshots = load_monthly_universe_snapshots('backtest_data')
    availability_map = {code: (info[0], info[1]) for code, info in availability.items()}

    param_sets = [
        # (tag, stock_selection, lookback_months, stop_loss, use_market_filter, ma_filter)
        ('rs_mkt+m6_s8',     'rs', 6,  -8.0, True,  200),
        ('rs_mkt+m3_s8',     'rs', 3,  -8.0, True,  200),
        ('rs_mkt+m12_s8',    'rs', 12, -8.0, True,  200),
        ('rs_mkt+m6_s5',     'rs', 6,  -5.0, True,  200),
        ('rs_mkt+m6_s10',    'rs', 6, -10.0, True,  200),
        ('rs_mkt+m6_s8_ma100','rs', 6,  -8.0, True,  100),
        ('rs_nofilter_m6_s8','rs', 6,  -8.0, False, 200),
        ('rs_nofilter_m3_s8','rs', 3,  -8.0, False, 200),
        ('ma_mkt+m6_s8',     'ma_bull', 6,  -8.0, True,  200),
        ('ma_mkt+m3_s8',     'ma_bull', 3,  -8.0, True,  200),
        ('ma_mkt+m12_s8',    'ma_bull', 12, -8.0, True,  200),
        ('ma_nofilter_m6_s8','ma_bull', 6,  -8.0, False, 200),
    ]

    results_summary = []
    for tag, sel, lb, stop, mkt_filter, maf in param_sets:
        logger.info(f"\n--- [{tag}] sel={sel} lb={lb}월 stop={stop}% filter={mkt_filter} MA={maf} ---")
        results = simulate_trend_follow(
            price_data=price_data, availability_map=availability_map,
            monthly_universe_map=monthly_snapshots, start_date=start_date,
            lookback_months=lb, stop_loss_pct=stop, use_market_filter=mkt_filter,
            ma_filter=maf, stock_selection=sel,
        )
        logger.info(f"[{tag}] 수익률={results['total_return']:.2f}% 연={results['annual_return']:.2f}% MDD={results['mdd']:.2f}% 매수={results['buy_trades']} 승률={results['win_rate']:.1f}% BH={results.get('buy_hold_return',0):.1f}%")
        results['tag'] = tag
        results_summary.append(results)

    print(f"\n{'='*72}")
    print(f"{'추세추종 10년 결과 (KF=KODEX200MA필터, RS=상대강도, MA=정배열)':^72}")
    print(f"{'='*72}")
    print(f"{'Tag':<22} {'수익률':>8} {'연환산':>8} {'MDD':>8} {'매수':>5} {'승률':>6} {'BH':>8}")
    print('-'*65)
    for r in results_summary:
        print(f"{r['tag']:<22} {r['total_return']:>8.2f} {r['annual_return']:>8.2f} {r['mdd']:>8.2f} {r['buy_trades']:>5} {r['win_rate']:>6.1f} {r.get('buy_hold_return',0):>8.1f}")
