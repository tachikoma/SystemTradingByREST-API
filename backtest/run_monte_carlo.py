"""
몬테카를로 검증 스크립트

세 가지 독립된 무작위성 검증을 수행한다.

MC-A. 수익률 블록 부트스트랩 (빠름, 재시뮬레이션 불필요)
    baseline 전체 기간의 일별 포트폴리오 수익률을 블록 단위로 재추출하여
    (블록길이 20일, 원본과 동일 길이) 2,000개의 합성 수익곡선을 만든다.
    → CAGR / MDD / Sharpe의 분포와 P(손실), P(MDD < -30%)를 추정.

MC-B. 거래 레벨 부트스트랩 (빠름, 재시뮬레이션 불필요)
    baseline의 매도 거래 수익률을 복원 추출하여 5,000번 재추출한다.
    → 총수익률 분포, 승률 분포, 최대 연속 손실 분포를 추정.
    ※ 거래 간 독립성을 가정하므로, 겹치는 포지션/순서 효과는 미반영 (근사).

MC-C. 유니버스 서브샘플링 (느림, 재시뮬레이션 필요)
    유니버스를 무작위로 일부(기본 80%)만 남기고 전체 기간 백테스트를
    K회 반복한다. → 전략의 유니버스 의존도를 측정.
    결과 분포가 원본(100%) 결과와 크게 다르면 유니버스 의존도가 높다.

사용법:
    python -m backtest.run_monte_carlo [--db-name backtest_data]
                                       [--start 2016-01-01]
                                       [--end 2026-06-30]
                                       [--mc-a-iters 2000]
                                       [--mc-b-iters 5000]
                                       [--mc-c-iters 20]
                                       [--mc-c-frac 0.8]
                                       [--seed 42]
"""
import argparse
import json
import multiprocessing as mp
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
load_dotenv(dotenv_path=project_root / '.env')

import numpy as np
import pandas as pd

from util.logging_config import configure_logging, get_logger
from backtest.validation_common import (
    enable_indicator_cache,
    load_all,
    run_single,
    baseline_params,
    metric_summary,
)

configure_logging(file_name='monte_carlo.log')
logger = get_logger('monte_carlo')

# fork로 상속될 공유 컨텍스트
_CTX = {}


# ---------------------------------------------------------------
# MC-A: 수익률 블록 부트스트랩
# ---------------------------------------------------------------
def block_bootstrap(returns: np.ndarray, block_len: int, n_iter: int, seed: int) -> list:
    """순환 블록 부트스트랩. 원본과 동일 길이의 재추출 수익률 시리즈 리스트를 반환."""
    rng = np.random.default_rng(seed)
    n = len(returns)
    series_list = []
    for _ in range(n_iter):
        resampled = np.empty(n)
        filled = 0
        while filled < n:
            start = rng.integers(0, n)
            blk = returns[(np.arange(block_len) + start) % n]
            take = min(block_len, n - filled)
            resampled[filled:filled + take] = blk[:take]
            filled += take
        series_list.append(resampled)
    return series_list


def metrics_from_returns(returns: np.ndarray) -> dict:
    """일별 수익률 시리즈에서 CAGR/MDD/Sharpe를 계산한다."""
    equity = 1.0 + returns
    equity = equity.cumprod()
    final = equity[-1]
    n = len(returns)
    cagr = (final ** (252 / n) - 1) * 100 if final > 0 else -100.0
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / peak
    mdd = dd.min() * 100
    sharpe = returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0.0
    return {'cagr': cagr, 'mdd': mdd, 'sharpe': sharpe}


def run_mc_a(daily_values: pd.DataFrame, n_iter: int, block_len: int, seed: int) -> dict:
    returns = daily_values['portfolio_value'].pct_change().dropna().to_numpy(dtype=float)
    returns = np.nan_to_num(returns)
    series = block_bootstrap(returns, block_len, n_iter, seed)
    results = [metrics_from_returns(s) for s in series]

    cagrs = np.array([r['cagr'] for r in results])
    mdds = np.array([r['mdd'] for r in results])
    sharpes = np.array([r['sharpe'] for r in results])

    def pct(arr, p):
        return round(float(np.percentile(arr, p)), 2)

    return {
        'method': f'block_bootstrap(block={block_len}, iters={n_iter})',
        'n_iter': n_iter,
        'cagr': {'p5': pct(cagrs, 5), 'p25': pct(cagrs, 25), 'p50': pct(cagrs, 50),
                 'p75': pct(cagrs, 75), 'p95': pct(cagrs, 95)},
        'mdd': {'p5': pct(mdds, 5), 'p25': pct(mdds, 25), 'p50': pct(mdds, 50),
                'p75': pct(mdds, 75), 'p95': pct(mdds, 95)},
        'sharpe': {'p5': pct(sharpes, 5), 'p25': pct(sharpes, 25), 'p50': pct(sharpes, 50),
                   'p75': pct(sharpes, 75), 'p95': pct(sharpes, 95)},
        'prob_negative_cagr': round(float((cagrs < 0).mean()) * 100, 2),
        'prob_mdd_below_30': round(float((mdds < -30).mean()) * 100, 2),
        'prob_mdd_below_50': round(float((mdds < -50).mean()) * 100, 2),
    }


# ---------------------------------------------------------------
# MC-B: 거래 레벨 부트스트랩
# ---------------------------------------------------------------
def run_mc_b(engine, n_iter: int, seed: int) -> dict:
    sell_trades = [t for t in engine.trades if t['type'] == 'sell']
    profit_rates = np.array([float(t['profit_rate']) for t in sell_trades])
    profits = np.array([float(t.get('profit', 0)) for t in sell_trades])

    if len(profit_rates) == 0:
        return {}

    rng = np.random.default_rng(seed)
    n = len(profit_rates)
    total_profit_arr = np.empty(n_iter)
    win_rate_arr = np.empty(n_iter)
    max_loss_streak_arr = np.empty(n_iter)
    for i in range(n_iter):
        idx = rng.integers(0, n, size=n)
        rates = profit_rates[idx]
        wins = rates > 0
        win_rate_arr[i] = wins.mean() * 100
        total_profit_arr[i] = profits[idx].sum()
        # 최대 연속 손실 (1/0 시퀀스)
        loss_flag = (rates <= 0).astype(int)
        streaks = 0
        max_streak = 0
        for flag in loss_flag:
            streaks = streaks + 1 if flag else 0
            max_streak = max(max_streak, streaks)
        max_loss_streak_arr[i] = max_streak

    def pct(arr, p):
        return round(float(np.percentile(arr, p)), 2)

    return {
        'method': f'trade_bootstrap(iters={n_iter})',
        'n_trades': n,
        'total_profit': {'p5': pct(total_profit_arr, 5), 'p25': pct(total_profit_arr, 25),
                         'p50': pct(total_profit_arr, 50), 'p75': pct(total_profit_arr, 75),
                         'p95': pct(total_profit_arr, 95)},
        'win_rate': {'p5': pct(win_rate_arr, 5), 'p50': pct(win_rate_arr, 50),
                     'p95': pct(win_rate_arr, 95)},
        'max_loss_streak': {'p50': pct(max_loss_streak_arr, 50), 'p95': pct(max_loss_streak_arr, 95),
                            'max': int(max_loss_streak_arr.max())},
        'prob_total_profit_negative': round(float((total_profit_arr < 0).mean()) * 100, 2),
        'prob_total_profit_less_than_half': round(
            float((total_profit_arr < (profits.sum() * 0.5)).mean()) * 100, 2),
    }


# ---------------------------------------------------------------
# MC-C: 유니버스 서브샘플링 (재시뮬레이션)
# ---------------------------------------------------------------
def _mc_c_worker(task):
    """task: (keep_codes, start, end, params) → metric_summary"""
    keep, start, end, params = task
    keep = set(keep)
    sub_price = {c: df for c, df in _CTX['price_data'].items() if c in keep}
    sub_avail = {c: v for c, v in _CTX['availability_map'].items() if c in keep}
    sub_monthly = {ym: [c for c in codes if c in keep] for ym, codes in _CTX['monthly_universe_map'].items()}
    res, _ = run_single(sub_price, sub_avail, sub_monthly, _CTX['symbol_names'], start, end, params)
    return metric_summary(res)


def run_mc_c(
    price_data, availability_map, monthly_universe_map, symbol_names,
    start, end, params, n_iter, frac, seed,
) -> dict:
    rng = np.random.default_rng(seed)
    all_codes = list(price_data.keys())
    logger.info("[MC-C] 전체 유니버스 기준 실행...")
    full_metrics = metric_summary(
        run_single(price_data, availability_map, monthly_universe_map, symbol_names,
                   start, end, params)[0]
    )

    tasks = []
    for i in range(n_iter):
        keep = set(rng.choice(all_codes, size=max(1, int(len(all_codes) * frac)), replace=False))
        tasks.append((list(keep), start, end, params))

    logger.info(f"[MC-C] 병렬 실행 {n_iter}회 (frac={frac})")
    pool = mp.Pool(min(n_iter, 6))
    try:
        metrics_list = pool.map(_mc_c_worker, tasks)
    finally:
        pool.close()
        pool.join()

    df = pd.DataFrame(metrics_list)
    summary = {
        'method': f'universe_subsample(frac={frac}, iters={n_iter})',
        'n_iter': n_iter,
        'full_universe_metrics': full_metrics,
    }
    for col, label in [('total_return', '총수익'), ('mdd', 'MDD'), ('sharpe', 'Sharpe'),
                       ('annual_return', '연환산'), ('win_rate', '승률')]:
        arr = df[col].astype(float)
        summary[col] = {
            'p5': round(float(arr.quantile(0.05)), 2),
            'p50': round(float(arr.quantile(0.50)), 2),
            'p95': round(float(arr.quantile(0.95)), 2),
            'min': round(float(arr.min()), 2),
            'max': round(float(arr.max()), 2),
        }
    summary['prob_negative_total_return'] = round(float((df['total_return'] < 0).mean()) * 100, 2)
    return summary


# ---------------------------------------------------------------
# MC-D: 신호 정보량 permutation 검정 (재시뮬레이션)
# ---------------------------------------------------------------
def _patched_buy_signal_factory(mode: str, rng, buy_prob: float):
    """check_buy_signal 대체 콜백 생성자.

    인스턴스 속성으로 할당되므로 `self`가 자동 바인딩되지 않는다.
    클로저로 engine을 캡처해 호출한다.

    mode='random' : 안전성/필터(MA200, MA20)를 통과한 후보일에 확률 buy_prob로 무작위 매수.
                   RSI<3 + 2일 -5% 급락 조건만 제거 → "그 조건에 정보가 있는가"를 검정.
    mode='mirror' : RSI > (100-rsi_buy) AND 2일간 +|price_drop|% 상승 (반대 신호) — 방향 참조.
    """
    def factory(engine):
        def patched(code, date, df, current_holdings_count):
            if current_holdings_count >= engine.max_holdings:
                return False, None
            if date not in df.index:
                return False, None
            idx = df.index.get_loc(date)
            if idx < 2:
                return False, None
            current = df.iloc[idx]
            close = current['close']
            rsi = current['rsi']
            ma20 = current['ma20']
            ma60 = current['ma60']
            ma200 = current['ma200']
            close_2days_ago = df.iloc[idx - 2]['close']
            if np.isnan(ma200) and (idx + 1) < engine.ma_trend:
                return False, None
            if np.isnan(rsi) or np.isnan(ma20) or np.isnan(ma60) or np.isnan(ma200) or close_2days_ago == 0:
                return False, None
            price_diff = (close - close_2days_ago) / close_2days_ago * 100
            ma20_ok = (not engine.use_ma20_filter) or (ma20 > ma60)
            ma200_ok = (not engine.use_ma200_filter) or (close > ma200)
            if not (ma20_ok and ma200_ok):
                return False, None
            if mode == 'random':
                fire = rng.random() < buy_prob
            elif mode == 'mirror':
                fire = (rsi > (100.0 - engine.rsi_buy_threshold)) and (price_diff > -engine.price_drop_threshold)
            else:
                fire = False
            return (True, close) if fire else (False, None)
        return patched
    return factory


def estimate_candidate_days(price_data: dict, monthly_universe_map: dict, ma_trend: int = 200) -> int:
    """MA200 형성일 + 월별 스냅샷 소속 기준 후보일(candidate-day) 수 추정.

    (월, 종목)이 스냅샷에 있을 때 그 종목의 *해당 월 거래일 수*만 집계한다.
    (종목의 전체 역사를 스냅샷 월마다 반복 집계하는 과대추정 방지)
    """
    month_counts = {}
    for c, df in price_data.items():
        if df is None or len(df) < ma_trend:
            continue
        if isinstance(df.index, pd.RangeIndex):
            if 'date' not in df.columns:
                continue
            vals = df['date'].astype(str)
        else:
            vals = df.index.astype(str)
        month_counts[c] = vals.str[:6].value_counts()
    total = 0
    for ym, codes in monthly_universe_map.items():
        for c in codes:
            mc = month_counts.get(c)
            if mc is None:
                continue
            total += int(mc.get(ym, 0))
    return total


def _mc_d_worker(task):
    """task: (seed, mode, buy_prob, start, end, params) → (mode, metric_summary)"""
    seed, mode, buy_prob, start, end, params = task
    factory = _patched_buy_signal_factory(mode, np.random.default_rng(seed), buy_prob)
    p2 = dict(params)
    p2['_buy_patch'] = factory
    res, _ = run_single(
        _CTX['price_data'], _CTX['availability_map'], _CTX['monthly_universe_map'],
        _CTX['symbol_names'], start, end, p2,
    )
    return mode, metric_summary(res)


def run_mc_d(
    price_data, availability_map, monthly_universe_map, symbol_names,
    start, end, params, baseline_metrics, n_iter, seed,
) -> dict:
    """Null(무작위 매수) 분포 대비 baseline의 위치로 신호 정보량을 판정한다."""
    n_buys = int(baseline_metrics.get('sell_trades', 0))
    cand_days = estimate_candidate_days(price_data, monthly_universe_map)
    buy_prob = (n_buys / cand_days) if cand_days > 0 else 0.0
    logger.info(f"[MC-D] 매수율 보정: 거래 {n_buys}건 / 후보일 {cand_days} → p={buy_prob:.6f}")

    # 실현 거래수 보정: max_holdings/현금 제약 때문에 신호 수 대비 실현 거래가 줄어든다.
    # null 실현 거래수를 baseline과 맞춰 수수료 드래그를 동일하게 한다.
    _, cal = _mc_d_worker((seed + 100, 'random', buy_prob, start, end, params))
    cal_trades = int(cal.get('sell_trades', 0))
    if cal_trades > 0:
        adjusted = buy_prob * (n_buys / cal_trades)
        logger.info(f"[MC-D] null 실현 거래 {cal_trades}건 → 목표 {n_buys}건에 맞춰 p={adjusted:.6f}")
        buy_prob = adjusted
    else:
        logger.warning("[MC-D] 보정 실행에서 거래가 0건 — p를 그대로 사용합니다.")

    tasks = [(seed + 100 + i, 'random', buy_prob, start, end, params) for i in range(n_iter)]
    tasks.append((seed + 999, 'mirror', buy_prob, start, end, params))
    logger.info(f"[MC-D] 병렬 실행: random {n_iter}회 + mirror 1회")

    pool = mp.Pool(min(n_iter + 1, 6))
    try:
        results = pool.map(_mc_d_worker, tasks)
    finally:
        pool.close()
        pool.join()

    rand_metrics = [m for mode, m in results if mode == 'random']
    mirror_metrics = next((m for mode, m in results if mode == 'mirror'), {})

    def dist(col):
        arr = np.array([m.get(col, np.nan) for m in rand_metrics], dtype=float)
        arr = arr[~np.isnan(arr)]
        return {
            'mean': round(float(arr.mean()), 3),
            'std': round(float(arr.std()), 3),
            'p5': round(float(np.percentile(arr, 5)), 3),
            'p95': round(float(np.percentile(arr, 95)), 3),
            'min': round(float(arr.min()), 3),
            'max': round(float(arr.max()), 3),
        }

    base_ann = float(baseline_metrics.get('annual_return', 0))
    rand_ann = np.array([float(m.get('annual_return', 0)) for m in rand_metrics])
    p_worse = float((rand_ann >= base_ann).mean()) * 100   # baseline이 null보다 나쁨(작음)
    p_better = float((rand_ann <= base_ann).mean()) * 100  # baseline이 null보다 좋음(큼)

    if p_worse <= 2.5:
        verdict = ('SIGNAL_HAS_NEGATIVE_INFO', '신호가 무작위보다 유의하게 나쁨 → 역방향 정보 존재(신호 재설계 필요)')
    elif p_better <= 2.5:
        verdict = ('SIGNAL_HAS_POSITIVE_INFO', '신호가 무작위보다 유의하게 좋음 → 정보 존재(실행/유니버스 문제)')
    else:
        verdict = ('SIGNAL_NO_INFO', '신호가 무작위와 구분되지 않음 → 정보 없음(신호 패밀리 교체 필요)')

    return {
        'method': f'permutation(random-fire p={buy_prob:.6f}, iters={n_iter})',
        'n_iter': n_iter,
        'buy_prob': buy_prob,
        'candidate_days': cand_days,
        'baseline_trades': n_buys,
        'random': {
            'annual_return': dist('annual_return'),
            'total_return': dist('total_return'),
            'sharpe': dist('sharpe'),
            'mdd': dist('mdd'),
            'sell_trades': dist('sell_trades'),
        },
        'baseline_annual_return': base_ann,
        'p_baseline_worse_pct': round(p_worse, 2),
        'p_baseline_better_pct': round(p_better, 2),
        'verdict': {'code': verdict[0], 'message': verdict[1]},
        'mirror': mirror_metrics,
    }


# ---------------------------------------------------------------
# 메인
# ---------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description='몬테카를로 검증')
    parser.add_argument('--db-name', default='backtest_data')
    parser.add_argument('--start', default='2016-01-01')
    parser.add_argument('--end', default='2026-06-30')
    parser.add_argument('--mc-a-iters', type=int, default=2000)
    parser.add_argument('--mc-b-iters', type=int, default=5000)
    parser.add_argument('--mc-c-iters', type=int, default=20)
    parser.add_argument('--mc-c-frac', type=float, default=0.8)
    parser.add_argument('--mc-d-iters', type=int, default=30)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--skip-a', action='store_true')
    parser.add_argument('--skip-b', action='store_true')
    parser.add_argument('--skip-c', action='store_true')
    parser.add_argument('--skip-d', action='store_true')
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()

    enable_indicator_cache()
    logger.info("데이터 로드 중...")
    price_data, availability_map, monthly_universe_map, symbol_names, date_range = load_all(args.db_name)
    logger.info("로드 완료: 종목 %d개", len(price_data))
    _CTX.update({
        'price_data': price_data,
        'availability_map': availability_map,
        'monthly_universe_map': monthly_universe_map,
        'symbol_names': symbol_names,
    })

    params = baseline_params()
    # 날짜는 엔진 관례(YYYYMMDD)에 맞춰 전달
    start_ts = args.start.replace('-', '')
    end_ts = args.end.replace('-', '')
    output = {'period': {'start': args.start, 'end': args.end}, 'seed': args.seed}

    # baseline (전체 기간) 1회 실행 — MC-A/B의 기반 + baseline 지표
    logger.info("=== baseline 실행 ===")
    res, engine = run_single(
        price_data, availability_map, monthly_universe_map, symbol_names,
        start_ts, end_ts, params,
    )
    output['baseline'] = metric_summary(res)
    logger.info(f"baseline: {output['baseline']}")

    if not args.skip_a:
        logger.info("=== MC-A: 수익률 블록 부트스트랩 ===")
        output['mc_a'] = run_mc_a(res['daily_values'], args.mc_a_iters, 20, args.seed)
        logger.info(f"MC-A: {output['mc_a']}")

    if not args.skip_b:
        logger.info("=== MC-B: 거래 레벨 부트스트랩 ===")
        output['mc_b'] = run_mc_b(engine, args.mc_b_iters, args.seed + 1)
        logger.info(f"MC-B: {output['mc_b']}")

    if not args.skip_c:
        logger.info("=== MC-C: 유니버스 서브샘플링 ===")
        output['mc_c'] = run_mc_c(
            price_data, availability_map, monthly_universe_map, symbol_names,
            start_ts, end_ts, params,
            args.mc_c_iters, args.mc_c_frac, args.seed + 2,
        )
        logger.info(f"MC-C: {output['mc_c']}")

    if not args.skip_d:
        logger.info("=== MC-D: 신호 정보량 permutation 검정 ===")
        output['mc_d'] = run_mc_d(
            price_data, availability_map, monthly_universe_map, symbol_names,
            start_ts, end_ts, params, output['baseline'], args.mc_d_iters, args.seed + 3,
        )
        logger.info(f"MC-D: {output['mc_d']}")

    output_dir = project_root / 'backtest' / 'output'
    output_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    path = output_dir / f'monte_carlo_{ts}.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"결과 저장: {path}")

    # 마크다운 요약
    md = [
        "# 몬테카를로 검증 결과",
        "",
        f"- 기간: {args.start} ~ {args.end}",
        f"- 시드: {args.seed}",
        "",
        "## baseline",
        "",
        f"총수익 {output['baseline']['total_return']}% / 연환산 {output['baseline']['annual_return']}% / "
        f"MDD {output['baseline']['mdd']}% / Sharpe {output['baseline']['sharpe']} / "
        f"승률 {output['baseline']['win_rate']}% / 거래 {output['baseline']['sell_trades']}건",
    ]

    if 'mc_a' in output:
        a = output['mc_a']
        md += [
            "",
            "## MC-A. 수익률 블록 부트스트랩",
            "",
            f"방법: {a['method']}",
            "",
            "| 지표 | P5 | P25 | P50 | P75 | P95 |",
            "|------|----|----|----|----|----|",
            f"| 연환산(%) | {a['cagr']['p5']} | {a['cagr']['p25']} | {a['cagr']['p50']} | {a['cagr']['p75']} | {a['cagr']['p95']} |",
            f"| MDD(%) | {a['mdd']['p5']} | {a['mdd']['p25']} | {a['mdd']['p50']} | {a['mdd']['p75']} | {a['mdd']['p95']} |",
            f"| Sharpe | {a['sharpe']['p5']} | {a['sharpe']['p25']} | {a['sharpe']['p50']} | {a['sharpe']['p75']} | {a['sharpe']['p95']} |",
            "",
            f"- P(연환산 < 0): **{a['prob_negative_cagr']}%**",
            f"- P(MDD < -30%): **{a['prob_mdd_below_30']}%**",
            f"- P(MDD < -50%): **{a['prob_mdd_below_50']}%**",
        ]

    if 'mc_b' in output:
        b = output['mc_b']
        md += [
            "",
            "## MC-B. 거래 레벨 부트스트랩",
            "",
            f"방법: {b['method']} (거래 수: {b['n_trades']})",
            "",
            "| 지표 | P5 | P25 | P50 | P75 | P95 |",
            "|------|----|----|----|----|----|",
            f"| 총수익(원) | {b['total_profit']['p5']} | {b['total_profit']['p25']} | {b['total_profit']['p50']} | {b['total_profit']['p75']} | {b['total_profit']['p95']} |",
            f"| 승률(%) | {b['win_rate']['p5']} | — | {b['win_rate']['p50']} | — | {b['win_rate']['p95']} |",
            f"| 최대 연속 손실(회) | — | — | {b['max_loss_streak']['p50']} | — | {b['max_loss_streak']['p95']} (max {b['max_loss_streak']['max']}) |",
            "",
            f"- P(총수익 < 0): **{b['prob_total_profit_negative']}%**",
            f"- P(총수익 < 중앙값의 50%): **{b['prob_total_profit_less_than_half']}%**",
        ]

    if 'mc_c' in output:
        c = output['mc_c']
        md += [
            "",
            "## MC-C. 유니버스 서브샘플링",
            "",
            f"방법: {c['method']}",
            "",
            "| 지표 | P5 | P50 | P95 | Min | Max |",
            "|------|----|----|----|----|----|",
            f"| 총수익(%) | {c['total_return']['p5']} | {c['total_return']['p50']} | {c['total_return']['p95']} | {c['total_return']['min']} | {c['total_return']['max']} |",
            f"| MDD(%) | {c['mdd']['p5']} | {c['mdd']['p50']} | {c['mdd']['p95']} | {c['mdd']['min']} | {c['mdd']['max']} |",
            f"| Sharpe | {c['sharpe']['p5']} | {c['sharpe']['p50']} | {c['sharpe']['p95']} | {c['sharpe']['min']} | {c['sharpe']['max']} |",
            f"| 연환산(%) | {c['annual_return']['p5']} | {c['annual_return']['p50']} | {c['annual_return']['p95']} | {c['annual_return']['min']} | {c['annual_return']['max']} |",
            f"| 승률(%) | {c['win_rate']['p5']} | {c['win_rate']['p50']} | {c['win_rate']['p95']} | {c['win_rate']['min']} | {c['win_rate']['max']} |",
            "",
            f"- 전체 유니버스 결과: 총수익 {c['full_universe_metrics']['total_return']}% / "
            f"MDD {c['full_universe_metrics']['mdd']}% / Sharpe {c['full_universe_metrics']['sharpe']}",
            f"- P(총수익 < 0): **{c['prob_negative_total_return']}%**",
            "",
            "> 해석: 서브샘플 결과의 분포가 전체 유니버스 결과와 크게 다르지 않으면",
            "> 유니버스 구성에 덜 민감(안정적). 분포 폭이 크면 유니버스 의존도가 높음.",
        ]

    if 'mc_d' in output:
        d = output['mc_d']
        r = d['random']
        mir = d.get('mirror', {})
        v = d['verdict']
        md += [
            "",
            "## MC-D. 신호 정보량 permutation 검정",
            "",
            f"방법: {d['method']}",
            f"- 후보일: {d['candidate_days']:,} / baseline 거래: {d['baseline_trades']}건 / 무작위 매수 확률 p={d['buy_prob']:.6f}",
            "",
            "### 무작위 매수(null) 분포 vs baseline",
            "",
            "| 지표 | mean | std | P5 | P95 | baseline |",
            "|------|------|-----|-----|-----|----------|",
            f"| 연환산(%) | {r['annual_return']['mean']} | {r['annual_return']['std']} | {r['annual_return']['p5']} | {r['annual_return']['p95']} | **{d['baseline_annual_return']}** |",
            f"| 총수익(%) | {r['total_return']['mean']} | {r['total_return']['std']} | {r['total_return']['p5']} | {r['total_return']['p95']} | **{output['baseline']['total_return']}** |",
            f"| Sharpe | {r['sharpe']['mean']} | {r['sharpe']['std']} | {r['sharpe']['p5']} | {r['sharpe']['p95']} | — |",
            f"| MDD(%) | {r['mdd']['mean']} | {r['mdd']['std']} | {r['mdd']['p5']} | {r['mdd']['p95']} | — |",
            f"| 거래수 | {r['sell_trades']['mean']} | {r['sell_trades']['std']} | {r['sell_trades']['p5']} | {r['sell_trades']['p95']} | {d['baseline_trades']} |",
            "",
            f"- baseline이 무작위보다 나쁠 확률(P(무작위 >= baseline)): **{d['p_baseline_worse_pct']}%**",
            f"- baseline이 무작위보다 좋을 확률(P(무작위 <= baseline)): **{d['p_baseline_better_pct']}%**",
            f"- **판정: {v['code']}** — {v['message']}",
            "",
            "> 해석: baseline이 무작위(null) 분포의 중앙 95% 안에 있으면 신호에 정보가 없음.",
            "> 하단 2.5% 밖이면 신호가 무작위보다 나쁨(역방향 정보), 상단 2.5% 밖이면 유의한 정보.",
        ]
        if mir:
            md += [
                "",
                "### 참조: 반대 신호(mirror) 실행 — RSI>97 + 2일 +5% 상승 매수",
                "",
                f"총수익 {mir.get('total_return', '—')}% / 연환산 {mir.get('annual_return', '—')}% / "
                f"MDD {mir.get('mdd', '—')}% / Sharpe {mir.get('sharpe', '—')} / "
                f"승률 {mir.get('win_rate', '—')}% / 거래 {mir.get('sell_trades', '—')}건",
                "",
                "> 신호 방향이 반대인지(음의 정보)를 단일 실행으로 확인하는 참고 지표.",
            ]

    md_path = output_dir / f'monte_carlo_{ts}.md'
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md) + '\n')
    logger.info(f"요약 저장: {md_path}")


if __name__ == '__main__':
    main()
