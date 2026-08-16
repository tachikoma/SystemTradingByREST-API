"""
WFA / 파라미터 안정성 / 몬테카를로 검증 공통 헬퍼

BacktestEngine을 다회 반복 실행할 때 지표 계산(calculate_indicators)이
매번 재수행되는 것을 피하기 위해 id 기반 캐시로 패치한다. 이는
파라미터 그리드(RSI_BUY_THRESHOLD 등 임계값)가 지표 계산에 영향을 주지
않는다는 점을 활용한 비침습적 최적화로, 엔진 코드는 변경하지 않는다.
"""

import sys
from pathlib import Path
from dotenv import load_dotenv

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

load_dotenv(dotenv_path=project_root / '.env')

import pandas as pd

from backtest.backtest_engine import BacktestEngine
from backtest.run_backtest import (
    load_price_data_from_db,
    load_universe_availability,
    load_monthly_universe_snapshots,
)

# ---------------------------------------------------------------
# 지표 계산 캐시 (id(df) 기반)
# ---------------------------------------------------------------
_INDICATOR_CACHE = {}
_ORIG_CALC = BacktestEngine.calculate_indicators


def _cached_calculate_indicators(self, df):
    key = id(df)
    cached = _INDICATOR_CACHE.get(key)
    if cached is not None:
        return cached
    result = _ORIG_CALC(self, df)
    _INDICATOR_CACHE[key] = result
    return result


def enable_indicator_cache():
    """BacktestEngine.calculate_indicators를 id 기반 캐시로 대체한다.

    주의: 동일한 df 객체(price_data dict의 값)를 재사용하는 실행에서만 유효.
    """
    BacktestEngine.calculate_indicators = _cached_calculate_indicators


def disable_indicator_cache():
    BacktestEngine.calculate_indicators = _ORIG_CALC


# ---------------------------------------------------------------
# 데이터 로드
# ---------------------------------------------------------------
def load_all(db_name: str = 'backtest_data'):
    """가격 데이터 + 유니버스 가용성 + 월별 스냅샷을 한 번에 로드한다.

    Returns:
        (price_data, availability_map, monthly_universe_map, symbol_names, date_range)
    """
    price_data, (db_start, db_end) = load_price_data_from_db(db_name)

    availability = load_universe_availability(db_name)
    symbol_names = {code: info[2] for code, info in availability.items()}
    availability_map = {code: (info[0], info[1]) for code, info in availability.items()}
    monthly_universe_map = load_monthly_universe_snapshots(db_name)

    return price_data, availability_map, monthly_universe_map, symbol_names, (db_start, db_end)


# ---------------------------------------------------------------
# 단일 백테스트 실행 (웹크포워드 유니버스 적용)
# ---------------------------------------------------------------
def run_single(
    price_data,
    availability_map,
    monthly_universe_map,
    symbol_names,
    start_date,
    end_date,
    engine_kwargs: dict,
):
    """지정 파라미터로 단일 백테스트를 실행하고 (results, engine)을 반환한다.

    engine_kwargs에 특수 키 '_buy_patch'를 넣으면 (callable) 주어졌을 때
    엔진 인스턴스의 check_buy_signal을 해당 콜백으로 교체한다.
    (permutation 검정 등 신호 로직 실험용)
    """
    kwargs = dict(engine_kwargs or {})
    buy_patch = kwargs.pop('_buy_patch', None)
    kwargs.setdefault('max_holdings', 10)
    kwargs.setdefault('rsi_min_periods', 2)
    kwargs.setdefault('symbol_names', symbol_names)
    engine = BacktestEngine(**kwargs)
    if buy_patch is not None:
        engine.check_buy_signal = buy_patch(engine)
    results = engine.run_backtest(
        price_data=price_data,
        start_date=start_date,
        end_date=end_date,
        availability_map=availability_map,
        monthly_universe_map=monthly_universe_map,
    )
    return results, engine


# ---------------------------------------------------------------
# 기준선 파라미터 (walk-forward v3 최적: TSL180_PT0_SELL70_NO_MA20)
# ---------------------------------------------------------------
def baseline_params() -> dict:
    return {
        'rsi_buy_threshold': 3,
        'entry_price_filter_enabled': False,
        'entry_price_filter_pct': -5.0,
        'use_ma20_filter': False,
        'use_ma200_filter': True,
        'rsi_sell_threshold': 70,
        'profit_target_percent': 0.0,
        'enable_time_stop_loss': True,
        'time_stop_loss_days': 180,
        'cash_reserve_ratio': 0.2,
        'rsi_period': 2,
    }


def metric_summary(results: dict) -> dict:
    """결과 딕셔너리에서 핵심 지표만 추린다."""
    dv = results.get('daily_values')
    days = len(dv) if dv is not None else 0
    start = end = ''
    if days:
        date_col = 'date' if 'date' in dv.columns else None
        if date_col:
            start = str(dv[date_col].iloc[0])
            end = str(dv[date_col].iloc[-1])
    return {
        'start': start,
        'end': end,
        'total_return': round(results.get('total_return', 0), 2),
        'annual_return': round(results.get('annual_return', 0), 2),
        'mdd': round(results.get('mdd', 0), 2),
        'sharpe': round(results.get('sharpe_ratio', 0), 3),
        'win_rate': round(results.get('win_rate', 0), 2),
        'sell_trades': results.get('sell_trades', 0),
        'avg_profit_rate': round(results.get('avg_profit_rate', 0), 2),
        'total_profit': results.get('total_profit', 0),
        'days': days,
    }


def agg_oos_metrics(segment_results) -> dict:
    """여러 OS 구간의 daily_values를 이어 붙여 통합 성과를 계산한다.

    Args:
        segment_results: [{segment_metrics, daily_values(df)} ...]

    Returns:
        통합 연환산/총수익/MDD/Sharpe/승률 딕셔너리
    """
    import numpy as np

    frames = [sr['daily_values'] for sr in segment_results if sr is not None]
    if not frames:
        return {}
    df = pd.concat(frames)
    date_col = 'date' if 'date' in df.columns else df.index.name or 'index'
    if date_col != 'index':
        df = df.set_index(date_col)
    df = df[~df.index.duplicated(keep='first')]
    df = df.sort_index()
    if len(df) < 2:
        return {}

    value = df['portfolio_value'].astype(float)
    returns = value.pct_change().dropna()

    initial = float(df.iloc[0]['portfolio_value'])
    final = float(df.iloc[-1]['portfolio_value'])
    total_return = (final / initial - 1) * 100
    n = len(df)
    annual = ((final / initial) ** (252 / n) - 1) * 100
    sharpe = returns.mean() / returns.std() * np.sqrt(252) if returns.std() != 0 else 0

    peak = value.expanding().max()
    drawdown = (value - peak) / peak
    mdd = drawdown.min() * 100

    return {
        'total_return': round(total_return, 2),
        'annual_return': round(annual, 2),
        'mdd': round(mdd, 2),
        'sharpe': round(sharpe, 3),
        'days': n,
        'start': str(df.index[0]),
        'end': str(df.index[-1]),
    }
