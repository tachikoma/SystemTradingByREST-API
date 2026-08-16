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
    """MC-D 신호 permutation 훅 지원 여부
    (vb/trend_follow는 selection_patch, vb_daily는 buy_patch)"""
    return name in ('vb', 'vb_daily', 'trend_follow')


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
        if engine_name in ('trend_follow', 'vb'):
            params['selection_patch'] = buy_patch
        else:
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


def make_random_selection_patch(rng):
    """월간 리밸런싱 시 유니버스에서 n개를 무작위 선정하는 null 패치 (trend_follow용)."""

    def patch(sorted_codes, date, n):
        if len(sorted_codes) <= n:
            return list(sorted_codes)
        return rng.choice(sorted_codes, size=n, replace=False).tolist()

    return patch


def run_mc_d_selection(
    engine_name, price_data, availability_map, monthly_universe_map, symbol_names,
    start_date, end_date, baseline_trades, n_iter=30, seed=42, engine_kwargs=None,
):
    """trend_follow 등 selection 기반 엔진의 MC-D.

    null: 매월 리밸런싱 시 상대강도/MA 대신 유니버스에서 무작위 n개 선정.
    Returns:
        RSI MC-D와 동일 구조의 판정 딕셔너리.
    """
    rng = np.random.default_rng(seed)
    annuals = []
    for _ in range(n_iter):
        patch = make_random_selection_patch(rng)
        kw = dict(engine_kwargs or {})
        kw['_buy_patch'] = patch
        results, _ = run_strategy(
            engine_name, price_data, availability_map, monthly_universe_map,
            symbol_names, start_date, end_date, kw,
        )
        annuals.append(results.get('annual_return', 0) or 0)

    arr = np.array(annuals)
    kw_base = dict(engine_kwargs or {})
    results_base, _ = run_strategy(
        engine_name, price_data, availability_map, monthly_universe_map,
        symbol_names, start_date, end_date, kw_base,
    )
    base_annual = results_base.get('annual_return', 0) or 0

    n_worse = int((arr <= base_annual).sum())
    p_worse = n_worse / len(arr)
    p_better = 1 - p_worse
    p5 = float(np.percentile(arr, 5))
    p95 = float(np.percentile(arr, 95))

    if p5 <= base_annual <= p95:
        verdict = 'SIGNAL_NO_INFO'
    elif base_annual > p95:
        verdict = 'SIGNAL_HAS_POSITIVE_INFO'
    else:
        verdict = 'SIGNAL_HAS_NEGATIVE_INFO'

    return {
        'engine': engine_name,
        'mode': 'random-selection',
        'n_iter': n_iter,
        'annual_mean': round(float(arr.mean()), 2),
        'annual_std': round(float(arr.std()), 3),
        'annual_p5': round(p5, 2),
        'annual_p95': round(p95, 2),
        'baseline_annual': round(base_annual, 2),
        'baseline_trades': baseline_trades,
        'p_worse': round(p_worse, 4),
        'p_better': round(p_better, 4),
        'verdict': verdict,
    }


def count_candidate_evals(engine_name, price_data, availability_map, monthly_universe_map,
                          symbol_names, start_date, end_date, engine_kwargs=None):
    """신호 조건에 도달하는 후보일 수(C)를 센다.

    buy_patch를 항상 False를 반환하는 카운팅 패치로 교체해 실행하면
    매수 신호 판단 지점에 도달한 (code, date) 쌍의 총 수를 얻는다.
    (RSI MC-D의 estimate_candidate_days에 해당)
    """
    counter = [0]

    def counting_patch(code, date, df, idx, holdings_count):
        counter[0] += 1
        return False

    kw = dict(engine_kwargs or {})
    kw['_buy_patch'] = counting_patch
    run_strategy(
        engine_name, price_data, availability_map, monthly_universe_map,
        symbol_names, start_date, end_date, kw,
    )
    return counter[0]


def calibrate_buy_prob(engine_name, price_data, availability_map, monthly_universe_map,
                       symbol_names, start_date, end_date, baseline_trades,
                       engine_kwargs=None, target_iters=2, seed=42):
    """실현 거래수를 baseline과 맞추도록 buy_prob를 보정한다.

    후보일 C를 먼저 세어 p = baseline_trades / C로 초기화한 뒤,
    실현 거래수 비율로 수렴 보정한다. (슬롯 포화 엔진에서 경계 포화 방지)
    """
    rng = np.random.default_rng(seed)
    C = count_candidate_evals(
        engine_name, price_data, availability_map, monthly_universe_map,
        symbol_names, start_date, end_date, engine_kwargs,
    )
    p = baseline_trades / max(C, 1)
    p = min(max(p, 1e-6), 1.0)

    for _ in range(target_iters):
        patch, counter = make_random_buy_patch(rng, p)
        kw = dict(engine_kwargs or {})
        kw['_buy_patch'] = patch
        results, _ = run_strategy(
            engine_name, price_data, availability_map, monthly_universe_map,
            symbol_names, start_date, end_date, kw,
        )
        realized = results.get('buy_trades', 0) or 0
        if realized > 0:
            p *= baseline_trades / realized
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
