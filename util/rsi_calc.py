"""RSI 계산 공통 모듈

RSIStrategy와 BacktestEngine이 공유하는 순수 RSI 계산 커널.
데이터 소스 접근 로직(universe, realtime 데이터 등)은 포함하지 않는다.
"""

import numpy as np
import pandas as pd


def compute_rsi(prices: pd.Series, period: int, min_periods: int, method: str) -> pd.Series:
    """Cutler(SMA) 또는 Wilder(EWMA) 방식으로 RSI를 계산한다.

    Args:
        prices: 종가 시계열 (float64)
        period: RSI 기간
        min_periods: 최소 유효 데이터 기간
        method: 'cutler' (SMA 기반) 또는 'wilder' (EWMA 기반)

    Returns:
        RSI 시계열 (동일 인덱스)
    """
    # gain/loss 계산
    delta = prices.diff(1)
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    # 첫 번째 값은 NaN으로 설정 (diff 기준점이므로 의미 없음)
    if len(gain) > 0:
        gain.iloc[0] = np.nan
        loss.iloc[0] = np.nan

    if min_periods < 1:
        min_periods = 1

    if method == 'cutler':
        # Cutler's RSI (SMA 기반)
        _min = min(min_periods, period)
        avg_gain = gain.rolling(window=period, min_periods=_min).mean()
        avg_loss = loss.rolling(window=period, min_periods=_min).mean()

        with np.errstate(divide='ignore', invalid='ignore'):
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        rsi = rsi.astype(float)
        rsi.loc[avg_loss == 0.0] = 100.0
        both_zero = (avg_gain == 0.0) & (avg_loss == 0.0)
        rsi.loc[both_zero] = 50.0

    else:
        # Wilder's RSI (EWMA 기반)
        _min = min(min_periods, max(1, len(prices)))
        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=_min, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=_min, adjust=False).mean()

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

        both_zero = np.isclose(avg_gain, 0.0) & np.isclose(avg_loss, 0.0)
        loss_zero = np.isclose(avg_loss, 0.0) & (~both_zero)
        gain_zero = np.isclose(avg_gain, 0.0) & (~both_zero)

        rsi = rsi.astype(float)
        rsi.loc[both_zero] = 50.0
        rsi.loc[loss_zero] = 100.0
        rsi.loc[gain_zero] = 0.0

    return rsi
