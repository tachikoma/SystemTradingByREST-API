"""가치 전략 백테스트 엔진

월별 PER/PBR 데이터 (pykrx)로 저PBR/저PER 종목 선정 후 월간 리밸런싱
"""
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv; load_dotenv()
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import pickle

# krx 로그인
os.environ['KRX_ID'] = os.getenv('KRX_ID', '')
os.environ['KRX_PW'] = os.getenv('KRX_PW', '')
from pykrx import stock as krx_stock

from backtest.run_backtest import load_price_data_from_db, load_universe_availability, load_monthly_universe_snapshots
from util.db_helper import execute_sql, resolve_date_column

INITIAL_CAPITAL = 10_000_000
MAX_HOLDINGS = 5
COMMISSION = 0.00015
TAX = 0.0020
SLIPPAGE = 0.002
CACHE_FILE = 'cache/value_factors.pkl'


def load_factor_cache() -> Dict:
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'rb') as f:
            return pickle.load(f)
    return {}


def save_factor_cache(cache: Dict):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    with open(CACHE_FILE, 'wb') as f:
        pickle.dump(cache, f)
    logger.info(f"팩터 캐시 저장 완료: {len(cache)}개월")


def fetch_factor_for_month(yyyymm: str, price_data: Dict) -> Dict:
    """특정 월의 PER/PBR 데이터를 pykrx로 조회

    해당 월의 첫 거래일을 price_data에서 찾아 조회, 결과 캐싱
    """
    cache = load_factor_cache()
    if yyyymm in cache:
        return cache[yyyymm]

    # 해당 월의 첫 거래일 찾기
    first_day = None
    for date in sorted(price_data.get('069500', pd.DataFrame()).index):
        if date.startswith(yyyymm):
            first_day = date
            break

    if first_day is None:
        logger.warning(f"{yyyymm}: 거래일 없음")
        return {}

    try:
        df = krx_stock.get_market_fundamental_by_ticker(first_day, market='ALL')
        # {code: {'PER': ..., 'PBR': ..., 'EPS': ..., 'BPS': ...}} 형태로 변환
        result = {}
        for code, row in df.iterrows():
            code = str(code).zfill(6)
            result[code] = {
                'PER': float(row['PER']) if row['PER'] > 0 else np.inf,
                'PBR': float(row['PBR']) if row['PBR'] > 0 else np.inf,
                'EPS': float(row['EPS']),
                'BPS': float(row['BPS']),
                'DIV': float(row['DIV']),
            }
        cache[yyyymm] = result
        save_factor_cache(cache)
        return result
    except Exception as e:
        logger.error(f"{yyyymm} ({first_day}) 팩터 조회 실패: {e}")
        return {}


def _get_first_trading_day(price_data: Dict, yyyymm: str) -> str:
    """특정 월의 첫 거래일 반환"""
    mkt = price_data.get('069500')
    if mkt is None:
        return ''
    for date in mkt.index:
        if isinstance(date, str) and date.startswith(yyyymm):
            return date
        elif isinstance(date, str) and date[:6] == yyyymm:
            return date
    return ''


def _lag_yyyymm(yyyymm: str, lag_months: int) -> str:
    """YYYYMM에서 lag_months만큼 이전 월 반환"""
    y = int(yyyymm[:4])
    m = int(yyyymm[4:])
    total_m = y * 12 + m - 1 - lag_months
    return f"{total_m // 12:04d}{total_m % 12 + 1:02d}"


def _cap_price(price: float, avg_price: float, max_pct: float) -> float:
    if max_pct <= 0 or avg_price <= 0:
        return price
    max_price = avg_price * (1 + max_pct / 100)
    min_price = avg_price * (1 - max_pct / 100)
    return max(min(price, max_price), min_price)


def simulate_value_strategy(
    price_data: Dict,
    universe_map: Dict,
    start_date: str,
    end_date: str = '20260604',
    factor: str = 'PBR',
    factor_direction: str = 'low',
    num_holdings: int = 5,
    stop_loss_pct: float = -10.0,
    use_market_filter: bool = True,
    ma_filter: int = 200,
    factor_lag_months: int = 0,
    max_trade_return_pct: float = 0,
) -> Dict:
    cash = float(INITIAL_CAPITAL)
    holdings: Dict[str, Dict] = {}
    daily_values = []
    total_buys = 0
    total_sells = 0
    wins = 0
    losses = 0
    peak_value = INITIAL_CAPITAL
    max_drawdown = 0.0

    # 시장 데이터 (069500) 로드
    market_df = price_data.get('069500')
    if market_df is None:
        logger.error("069500 데이터 없음")
        return {}

    all_dates = sorted(set(
        date for df in price_data.values() for date in df.index
        if start_date <= date <= end_date
    ))
    logger.info(f"총 거래일: {len(all_dates)}")

    # 월별 리밸런싱
    last_rebalance_ym = ''

    for date in all_dates:
        yyyymm = date[:6]

        if market_df is not None and date not in market_df.index:
            continue

        # 시장 필터
        is_bullish = True
        if use_market_filter:
            try:
                pos = list(market_df.index).index(date)
                if pos >= ma_filter:
                    close = float(market_df.loc[date, 'close'])
                    ma = market_df['close'].iloc[pos - ma_filter:pos].mean()
                    is_bullish = close > ma
            except (ValueError, AttributeError):
                pass

        # 약세장 전량 청산
        if use_market_filter and not is_bullish:
            for code in list(holdings.keys()):
                if code not in price_data or date not in price_data[code].index:
                    holdings.pop(code, None)
                    total_sells += 1
                    continue
                row = price_data[code].loc[date]
                sp = float(row['open']) if not np.isnan(row['open']) and row['open'] > 0 else float(row['close'])
                h = holdings.pop(code)
                capped = _cap_price(sp, h['avg_price'], max_trade_return_pct)
                ep = capped * (1 - SLIPPAGE)
                rev = ep * h['qty']
                cost = h['avg_price'] * h['qty']
                fee = rev * (COMMISSION + TAX)
                cash += rev - fee
                total_sells += 1
                if rev - cost - fee > 0:
                    wins += 1
                else:
                    losses += 1
            daily_values.append({'date': date, 'portfolio_value': cash})
            peak_value = max(peak_value, cash)
            dd = (cash - peak_value) / peak_value * 100
            max_drawdown = min(max_drawdown, dd)
            continue

        # 손절
        for code in list(holdings.keys()):
            if code not in price_data or date not in price_data[code].index:
                continue
            row = price_data[code].loc[date]
            if stop_loss_pct != 0:
                low = float(row['low'])
                stop_level = holdings[code]['avg_price'] * (1 + stop_loss_pct / 100)
                if not np.isnan(low) and low <= stop_level:
                    sp = min(stop_level, low)
                    h = holdings.pop(code)
                    capped = _cap_price(sp, h['avg_price'], max_trade_return_pct)
                    ep = capped * (1 - SLIPPAGE)
                    rev = ep * h['qty']
                    cost = h['avg_price'] * h['qty']
                    fee = rev * (COMMISSION + TAX)
                    cash += rev - fee
                    total_sells += 1
                    if rev - cost - fee > 0:
                        wins += 1
                    else:
                        losses += 1

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
                capped = _cap_price(sp, h['avg_price'], max_trade_return_pct)
                ep = capped * (1 - SLIPPAGE)
                rev = ep * h['qty']
                cost = h['avg_price'] * h['qty']
                fee = rev * (COMMISSION + TAX)
                cash += rev - fee
                total_sells += 1
                if rev - cost - fee > 0:
                    wins += 1
                else:
                    losses += 1

            # 새 종목 선정
            if is_bullish:
                # PER/PBR 데이터 조회 (lag 적용)
                factor_ym = _lag_yyyymm(yyyymm, factor_lag_months)
                if factor_ym < '201001':
                    logger.debug(f"{yyyymm}: lag 팩터월 {factor_ym} 이전, 스킵")
                    continue
                factors = fetch_factor_for_month(factor_ym, price_data)
                if not factors:
                    continue

                # 유니버스
                universe = universe_map.get(yyyymm, [])
                if not universe:
                    continue

                # 유니버스 내 팩터 데이터로 정렬
                candidates = []
                for code in universe:
                    if code not in factors:
                        continue
                    f = factors[code]
                    val = f.get(factor, np.inf)
                    if not np.isfinite(val) or val <= 0:
                        continue
                    candidates.append((code, val))

                if factor_direction == 'low':
                    candidates.sort(key=lambda x: x[1])
                else:
                    candidates.sort(key=lambda x: x[1], reverse=True)

                selected = [c[0] for c in candidates[:num_holdings]]

                # 매수
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
                    capped_close = _cap_price(close, h['avg_price'], max_trade_return_pct)
                    pv += capped_close * h['qty']
        daily_values.append({'date': date, 'portfolio_value': pv})
        peak_value = max(peak_value, pv)
        dd = (pv - peak_value) / peak_value * 100
        max_drawdown = min(max_drawdown, dd)

    if not daily_values:
        return {}

    fv = daily_values[-1]['portfolio_value']
    tr = (fv - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100
    days = len(daily_values)
    years = days / 252
    ar = ((1 + tr / 100) ** (1 / years) - 1) * 100 if years > 0 else 0
    wr = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0

    # BH
    bh = 0
    if market_df is not None:
        fd = daily_values[0]['date']
        ld = daily_values[-1]['date']
        if fd in market_df.index and ld in market_df.index:
            bh = (float(market_df.loc[ld, 'close']) - float(market_df.loc[fd, 'close'])) / float(
                market_df.loc[fd, 'close']) * 100

    return {
        'total_return': tr, 'annual_return': ar, 'mdd': max_drawdown,
        'buy_trades': total_buys, 'total_trades': total_buys + total_sells,
        'win_rate': wr, 'buy_hold_return': bh,
    }


if __name__ == '__main__':
    start_date = (datetime.now() - timedelta(days=10 * 365)).strftime('%Y%m%d')
    logger.info(f"가치 전략 백테스트 시작일: {start_date}")

    price_data, _ = load_price_data_from_db()
    # 069500 로드
    mkt_df = None
    try:
        sql = "SELECT * FROM `069500`"
        cur = execute_sql('backtest_data', sql)
        cols = [c[0] for c in cur.description]
        mkt_df = pd.DataFrame.from_records(data=cur.fetchall(), columns=cols)
        date_col = resolve_date_column(mkt_df)
        mkt_df = mkt_df.set_index(date_col)
        mkt_df.index.name = 'date'
        mkt_df.index = mkt_df.index.astype(str)
        price_data['069500'] = mkt_df
        logger.info(f"069500 로드: {mkt_df.index.min()} ~ {mkt_df.index.max()}")
    except Exception as e:
        logger.warning(f"069500 로드 실패: {e}")

    snapshots = load_monthly_universe_snapshots('backtest_data')
    logger.info(f"월별 스냅샷: {len(snapshots)}개월")

    param_sets = [
        # (tag, factor, direction, holdings, stop_loss, market_filter, lag_months, cap_pct)
        ('pbr5_mkt',      'PBR', 'low', 5,  -10.0, True,  0,  0),
        ('pbr5_mkt_c30',  'PBR', 'low', 5,  -10.0, True,  0, 30),
        ('pbr5_mkt_l6',   'PBR', 'low', 5,  -10.0, True,  6,  0),
        ('pbr5_mkt_l6c30','PBR', 'low', 5,  -10.0, True,  6, 30),
        ('pbr10_mkt',     'PBR', 'low', 10, -10.0, True,  0,  0),
        ('pbr10_mkt_c30', 'PBR', 'low', 10, -10.0, True,  0, 30),
        ('pbr20_mkt',     'PBR', 'low', 20, -10.0, True,  0,  0),
        ('per5_mkt',      'PER', 'low', 5,  -10.0, True,  0,  0),
        ('pbr5_nof',      'PBR', 'low', 5,  -10.0, False, 0,  0),
        ('per5_nof',      'PER', 'low', 5,  -10.0, False, 0,  0),
    ]

    results_summary = []
    for tag, fact, direc, nhold, stop, mf, lag, cap in param_sets:
        logger.info(f"\n--- [{tag}] {fact} {direc} n={nhold} stop={stop}% filter={mf} lag={lag}m cap={cap}% ---")
        results = simulate_value_strategy(
            price_data=price_data, universe_map=snapshots,
            start_date=start_date, end_date='20260604',
            factor=fact, factor_direction=direc, num_holdings=nhold,
            stop_loss_pct=stop, use_market_filter=mf, factor_lag_months=lag,
            max_trade_return_pct=cap,
        )
        if results:
            logger.info(f"[{tag}] 수익률={results['total_return']:.2f}% 연={results['annual_return']:.2f}% MDD={results['mdd']:.2f}% 매수={results['buy_trades']} 승률={results['win_rate']:.1f}% BH={results.get('buy_hold_return',0):.1f}%")
            results['tag'] = tag
            results_summary.append(results)
        else:
            logger.error(f"[{tag}] 실패")

    if results_summary:
        print(f"\n{'='*70}")
        print(f"{'가치 전략 (KRX PER/PBR) 10년 결과':^70}")
        print(f"{'='*70}")
        print(f"{'Tag':<16} {'수익률':>10} {'연환산':>8} {'MDD':>8} {'매수':>5} {'승률':>6} {'BH':>8}")
        print('-'*65)
        for r in results_summary:
            print(f"{r['tag']:<16} {r['total_return']:>10.2f} {r['annual_return']:>8.2f} {r['mdd']:>8.2f} {r['buy_trades']:>5} {r['win_rate']:>6.1f} {r.get('buy_hold_return',0):>8.1f}")
