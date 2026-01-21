import pandas as pd
import numpy as np
import pytest
from types import SimpleNamespace

from strategy.RSIStrategy import RSIStrategy


def wilder_rsi_reference(prices, period):
    prices = np.asarray(prices, dtype=float)
    n = prices.size
    delta = np.empty(n)
    delta[0] = np.nan
    delta[1:] = prices[1:] - prices[:-1]
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)

    avg_gain = np.full(n, np.nan)
    avg_loss = np.full(n, np.nan)
    p = int(period)
    if n > p:
        start = 1
        end = p + 1
        init_g = gains[start:end]
        init_l = losses[start:end]
        if len(init_g) == p:
            avg_gain[p] = init_g.mean()
            avg_loss[p] = init_l.mean()
            for t in range(p + 1, n):
                avg_gain[t] = (avg_gain[t-1] * (p - 1) + gains[t]) / p
                avg_loss[t] = (avg_loss[t-1] * (p - 1) + losses[t]) / p

    rsi = np.full(n, np.nan)
    for t in range(n):
        ag = avg_gain[t]
        al = avg_loss[t]
        if np.isnan(ag) or np.isnan(al):
            continue
        if al == 0.0 and ag == 0.0:
            rsi[t] = 50.0
        elif al == 0.0:
            rsi[t] = 100.0
        elif ag == 0.0:
            rsi[t] = 0.0
        else:
            rs = ag / al
            rsi[t] = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def cutler_rsi_reference(prices, period):
    prices = np.asarray(prices, dtype=float)
    n = prices.size
    delta = np.empty(n)
    delta[0] = np.nan
    delta[1:] = prices[1:] - prices[:-1]
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)

    avg_gain = np.full(n, np.nan)
    avg_loss = np.full(n, np.nan)
    p = int(period)
    if n >= p + 1:
        # rolling mean over window of size p on gains/losses aligned to price index
        # For price index t, the rolling window is gains[t-p+1:t+1] but we follow simple pandas-like alignment
        for t in range(p, n):
            window_g = gains[t-p+1:t+1]
            window_l = losses[t-p+1:t+1]
            if len(window_g) == p:
                avg_gain[t] = window_g.mean()
                avg_loss[t] = window_l.mean()

    rsi = np.full(n, np.nan)
    for t in range(n):
        ag = avg_gain[t]
        al = avg_loss[t]
        if np.isnan(ag) or np.isnan(al):
            continue
        if al == 0.0 and ag == 0.0:
            rsi[t] = 50.0
        elif al == 0.0:
            rsi[t] = 100.0
        elif ag == 0.0:
            rsi[t] = 0.0
        else:
            rs = ag / al
            rsi[t] = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def make_strategy_with_prices(prices, period=2, method='wilder'):
    # Prepare RSIStrategy instance without full init
    s = RSIStrategy.__new__(RSIStrategy)
    # allow test to select method under test
    s.RSI_METHOD = method
    s.RSI_PERIOD = period
    s.universe = {}
    # dummy kiwoom with required structure
    s.kiwoom = SimpleNamespace()
    s.kiwoom.universe_realtime_transaction_info = {}

    # Build price_df with all but last price; calculate_rsi will append today's price
    idx = []
    rows = []
    for i, p in enumerate(prices[:-1]):
        idx.append(f"202601{10+i:02d}")
        rows.append([p, p, p, p, 0])
    df = pd.DataFrame(rows, columns=['open', 'high', 'low', 'close', 'volume'], index=idx)

    code = 'TST'
    s.universe[code] = {'price_df': df}

    # realtime info uses last price as current
    last = prices[-1]
    s.kiwoom.universe_realtime_transaction_info[code] = {'시가': last, '고가': last, '저가': last, '현재가': last, '누적거래량': 0}
    return s, code


@pytest.mark.parametrize("prices", [
    [1,2,3,4,5,6,7],            # strong up
    [7,6,5,4,3,2,1],            # strong down
    [100,100,101,101,101],      # flat then up (avg_loss==0 cases)
    [100,100,100,100,100],      # flat (both zero -> RSI=50)
])
def test_rsi_matches_reference(prices):
    period = 2
    # validate both methods
    for method in ('wilder', 'cutler'):
        s, code = make_strategy_with_prices(prices, period=period, method=method)
    df, _ = s.calculate_rsi(code)
    assert df is not None
    actual = df[f'RSI({period})'].to_numpy(dtype=float)

    # Build full price series used by calculate_rsi (initial rows + appended current)
    full_prices = np.asarray(prices, dtype=float)
    if method == 'wilder':
        expected = wilder_rsi_reference(full_prices, period)
    else:
        expected = cutler_rsi_reference(full_prices, period)

    np.testing.assert_allclose(actual, expected, equal_nan=True, rtol=0, atol=1e-8)
