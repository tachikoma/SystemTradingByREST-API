"""
신규 전략 엔진 검증 어댑터

feature/strategy-research 브랜치의 독립 엔진(vb/vb_daily/trend_follow/value)을
검증 gate(validation_common)의 표준 results 포맷으로 변환한다.

- 각 엔진의 simulate_* 함수는 daily_values(DataFrame)를 반환하도록 확장되어 있다.
- run_strategy()는 engine_kwargs(전략 파라미터)를 받아 엔진을 호출하고
  표준 결과(daily_values 포함, sharpe 계산)를 반환한다.
- buy_patch 훅을 통해 MC-D(신호 permutation) null 모델을 주입할 수 있다.
  (vb / vb_daily 엔진은 simulate_* 시그니처에 buy_patch 파라미터 지원)
"""

import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import numpy as np
import pandas as pd

# ---------------------------------------------------------------
# 엔진 레지스트리: 이름 -> (모듈, simulate 함수, 기본 파라미터)
# ---------------------------------------------------------------
def _import(module_name):
    import importlib
    return importlib.import_module(module_name)

_ENGINES = {}


def _register(name, module_name, func_name, defaults):
    _ENGINES[name] = {
        'module_name': module_name,
        'func_name': func_name,
        'defaults': defaults,
    }


_register('vb', 'backtest.vb_engine', 'simulate_vb_backtest',
          {'k': 0.5, 'ma_filter_period': 0, 'stop_loss_pct': -5.0, 'hold_days': 1})
_register('vb_daily', 'backtest.vb_daily_engine', 'simulate_vb_daily',
          {'k': 0.5, 'ma_filter_period': 0, 'stop_loss_pct': -8.0, 'hold_days': 5})
_register('trend_follow', 'backtest.trend_follow_engine', 'simulate_trend_follow',
          {'lookback_months': 6, 'ma_filter': 200, 'stop_loss_pct': -8.0,
           'use_market_filter': True, 'min_stock_price': 1000, 'stock_selection': 'rs'})
_register('value', 'backtest.value_engine', 'simulate_value_strategy',
          {'factor': 'PBR', 'factor_direction': 'low', 'num_holdings': 5,
           'stop_loss_pct': -10.0, 'use_market_filter': True, 'ma_filter': 200})


def engine_names():
    return sorted(_ENGINES.keys())


def engine_defaults(name):
    return dict(_ENGINES[name]['defaults'])


def get_simulate(name):
    reg = _ENGINES[name]
    mod = _import(reg['module_name'])
    return getattr(mod, reg['func_name'])


def supports_buy_patch(name):
    """MC-D 신호 permutation 훅 지원 여부"""
    return name in ('vb', 'vb_daily')


# ---------------------------------------------------------------
# 표준 결과 정규화
# ---------------------------------------------------------------
def normalize_results(results: dict, name: str) -> dict:
    """엔진 결과를 gate 표준 포맷으로 보정한다 (sharpe/daily_values 보장)."""
    if not results:
        return {}
    out = dict(results)

    dv = out.get('daily_values')
    if dv is not None and not isinstance(dv, pd.DataFrame):
        dv = pd.DataFrame(dv)
    if dv is not None and 'date' in dv.columns:
        dv = dv.set_index('date').sort_index()

    out['daily_values'] = dv

    if dv is not None and len(dv) > 1:
        value = dv['portfolio_value'].astype(float)
        rets = value.pct_change().dropna()
        std = rets.std()
        out['sharpe_ratio'] = round(float(rets.mean() / std * np.sqrt(252)), 3) if std != 0 else 0.0
    else:
        out['sharpe_ratio'] = 0.0

    out.setdefault('sell_trades', 0)
    out.setdefault('avg_profit_rate', 0.0)
    out.setdefault('total_profit', 0.0)
    return out


# ---------------------------------------------------------------
# 단일 백테스트 실행 (검증 gate용)
# ---------------------------------------------------------------
def run_strategy(
    engine_name: str,
    price_data,
    availability_map,
    monthly_universe_map,
    symbol_names,
    start_date: str,
    end_date: str,
    engine_kwargs: dict,
):
    """지정 파라미터로 전략 엔진을 실행하고 (results, simulate_fn) 반환.

    engine_kwargs에 특수 키 '_buy_patch'를 넣으면 (callable) 주어졌을 때
    buy_patch 파라미터로 엔진에 전달한다 (MC-D permutation 실험용).
    """
    kwargs = dict(engine_kwargs or {})
    buy_patch = kwargs.pop('_buy_patch', None)
    sim = get_simulate(engine_name)
    params = engine_defaults(engine_name)
    params.update(kwargs)
    if buy_patch is not None and supports_buy_patch(engine_name):
        params['buy_patch'] = buy_patch

    results = sim(
        price_data=price_data,
        availability_map=availability_map,
        monthly_universe_map=monthly_universe_map,
        start_date=start_date,
        end_date=end_date,
        **params,
    )
    return normalize_results(results, engine_name), sim


# ---------------------------------------------------------------
# MC-D null 모델 (무작위 매수 permutation)
# ---------------------------------------------------------------
def make_random_buy_patch(rng, buy_prob):
    """후보일마다 확률 buy_prob로 매수 신호를 발생시키는 null 패치.

    Returns:
        (patch, counter): patch는 buy_patch 시그니처, counter는 [평가 횟수, 발화 횟수]
    """
    counter = [0, 0]

    def patch(code, date, df, idx, holdings_count):
        counter[0] += 1
        if rng.random() < buy_prob:
            counter[1] += 1
            return True
        return False

    return patch, counter


def calibrate_buy_prob(engine_name, price_data, availability_map, monthly_universe_map,
                       symbol_names, start_date, end_date, baseline_trades,
                       engine_kwargs=None, target_iters=2, seed=42):
    """실현 거래수를 baseline과 맞추도록 buy_prob를 보정한다 (2회 반복 수렴).

    Returns:
        보정된 buy_prob
    """
    rng = np.random.default_rng(seed)
    p = 0.5
    for _ in range(target_iters):
        patch, counter = make_random_buy_patch(rng, p)
        kw = dict(engine_kwargs or {})
        kw['_buy_patch'] = patch
        results, _ = run_strategy(
            engine_name, price_data, availability_map, monthly_universe_map,
            symbol_names, start_date, end_date, kw,
        )
        realized = results.get('buy_trades', 0) or 0
        evals = counter[0]
        if evals > 0:
            p *= baseline_trades / max(realized, 1)
        p = min(max(p, 1e-6), 1.0)
    return p


def run_mc_d_engine(
    engine_name, price_data, availability_map, monthly_universe_map, symbol_names,
    start_date, end_date, baseline_trades, n_iter=30, buy_prob=None, seed=42,
    engine_kwargs=None,
):
    """엔진용 MC-D: 무작위 매수 null 분포를 만들어 판정한다.

    Returns:
        {'annual_mean', 'annual_std', 'annual_p5', 'annual_p95',
         'baseline_annual', 'p_worse' (무작위가 baseline보다 나쁠 확률),
         'p_better', 'verdict'}
    """
    rng = np.random.default_rng(seed)

    if buy_prob is None:
        buy_prob = calibrate_buy_prob(
            engine_name, price_data, availability_map, monthly_universe_map, symbol_names,
            start_date, end_date, baseline_trades, engine_kwargs=engine_kwargs,
        )

    annuals = []
    for _ in range(n_iter):
        patch, _ = make_random_buy_patch(rng, buy_prob)
        kw = dict(engine_kwargs or {})
        kw['_buy_patch'] = patch
        results, _ = run_strategy(
            engine_name, price_data, availability_map, monthly_universe_map,
            symbol_names, start_date, end_date, kw,
        )
        annuals.append(results.get('annual_return', 0) or 0)

    arr = np.array(annuals)
    mean = float(arr.mean())
    std = float(arr.std())
    p5 = float(np.percentile(arr, 5))
    p95 = float(np.percentile(arr, 95))

    kw_baseline = dict(engine_kwargs or {})
    results_base, _ = run_strategy(
        engine_name, price_data, availability_map, monthly_universe_map,
        symbol_names, start_date, end_date, kw_baseline,
    )
    base_annual = results_base.get('annual_return', 0) or 0

    n_worse = int((arr <= base_annual).sum())
    p_worse = n_worse / len(arr)
    p_better = 1 - p_worse

    if p5 <= base_annual <= p95:
        verdict = 'SIGNAL_NO_INFO'
    elif base_annual > p95:
        verdict = 'SIGNAL_HAS_POSITIVE_INFO'
    else:
        verdict = 'SIGNAL_HAS_NEGATIVE_INFO'

    return {
        'engine': engine_name,
        'buy_prob': buy_prob,
        'n_iter': n_iter,
        'annual_mean': round(mean, 2),
        'annual_std': round(std, 3),
        'annual_p5': round(p5, 2),
        'annual_p95': round(p95, 2),
        'baseline_annual': round(base_annual, 2),
        'baseline_trades': baseline_trades,
        'p_worse': round(p_worse, 4),
        'p_better': round(p_better, 4),
        'verdict': verdict,
    }
