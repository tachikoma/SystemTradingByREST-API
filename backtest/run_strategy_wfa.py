"""
엔진 기반 WFA(워크 포워드) 검증 — vb 엔진용

36개월 IS(in-sample) 구간에서 k/MA 그리드를 최적화하고, 다음 12개월
OS(out-of-sample) 구간의 성과를 측정한다. 12개월 단위로 순환하여
전체 기간의 진짜 out-of-sample 성과를 산출한다.

고정 baseline(k=0.5, MA=0)과 'IS 재최적화'의 OS 성과를 비교해
동적 재최적화가 과적합인지 판정한다.

Usage:
    .venv/bin/python backtest/run_strategy_wfa.py [--workers 2] [--grid full|quick]
"""
import argparse
import json
import multiprocessing as mp
import sys
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from util.logging_config import configure_logging, get_logger
from backtest.validation_common import load_all, agg_oos_metrics
from backtest.run_wfa_validation import build_windows
from backtest.validation_strategy_adapter import run_strategy, normalize_results

configure_logging(file_name='wfa_vb_validation.log')
logger = get_logger('wfa_vb_validation')

_CTX = {}

# vb 엔진 고정 파라미터 (hold>1은 이미 -99%로 실패 확인 → hold=1 고정, stop은 hold=1에서 무효)
VB_BASE = {'k': 0.5, 'ma_filter_period': 0, 'stop_loss_pct': -5.0, 'hold_days': 1}

GRID_FULL = {'k': [0.5, 0.6, 0.7, 0.8, 0.9], 'ma_filter_period': [0, 5, 10, 20]}
GRID_QUICK = {'k': [0.5, 0.6, 0.7, 0.8], 'ma_filter_period': [0, 5, 20]}


def build_grid(grid_name):
    spec = GRID_FULL if grid_name == 'full' else GRID_QUICK
    combos = [{}]
    for key, values in spec.items():
        combos = [dict(c, **{key: v}) for c in combos for v in values]
    return [(f"k{c['k']}_MA{c['ma_filter_period']}", c) for c in combos]


def _run(price_data, availability_map, monthly_universe_map, symbol_names,
         start_date, end_date, params):
    res, _ = run_strategy(
        'vb', price_data, availability_map, monthly_universe_map, symbol_names,
        start_date, end_date, params,
    )
    res = normalize_results(res, 'vb')
    return res


def _metric(res):
    dv = res.get('daily_values')
    return {
        'total_return': round(res.get('total_return', 0), 2),
        'annual_return': round(res.get('annual_return', 0), 2),
        'mdd': round(res.get('mdd', 0), 2),
        'sharpe': round(res.get('sharpe_ratio', 0), 3),
        'win_rate': round(res.get('win_rate', 0), 2),
        'buy_trades': res.get('buy_trades', 0),
        'days': len(dv) if dv is not None else 0,
    }


def _worker_run(task):
    label, params, start_date, end_date = task
    res = _run(_CTX['price_data'], _CTX['availability_map'], _CTX['monthly_universe_map'],
               _CTX['symbol_names'], start_date, end_date, params)
    return label, _metric(res), res['daily_values']


def _agg_daily(df):
    """date가 index면 column으로 되돌린다 (agg_oos_metrics 호환)."""
    if df is None:
        return None
    out = df.copy()
    if 'date' not in out.columns and out.index.name == 'date':
        out = out.reset_index()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db-name', default='backtest_data')
    ap.add_argument('--start', default='2016-01-01')
    ap.add_argument('--end', default='2026-06-30')
    ap.add_argument('--grid', default='full', choices=['full', 'quick'])
    ap.add_argument('--workers', type=int, default=2)
    args = ap.parse_args()

    logger.info("데이터 로드 중...")
    price_data, availability_map, monthly_universe_map, symbol_names, date_range = load_all(args.db_name)
    _CTX.update({
        'price_data': price_data,
        'availability_map': availability_map,
        'monthly_universe_map': monthly_universe_map,
        'symbol_names': symbol_names,
    })

    windows = build_windows(args.start.replace('-', '')[:6], args.end.replace('-', '')[:6])
    logger.info("WFA 윈도우 %d개 (36개월 IS + 12개월 OS)", len(windows))
    grid = build_grid(args.grid)
    logger.info("vb 그리드 %d개 조합", len(grid))

    start_ts = args.start.replace('-', '')
    end_ts = args.end.replace('-', '')

    # 전체 기간 baseline 참고 지표
    base_res = _run(price_data, availability_map, monthly_universe_map, symbol_names,
                    start_ts, end_ts, dict(VB_BASE))
    full_baseline = _metric(base_res)
    logger.info(f"전체 기간 vb baseline: {full_baseline}")

    oos_wfa_segments, oos_base_segments = [], []
    window_records = []

    for w_idx, w in enumerate(windows, 1):
        logger.info(f"\n=== 윈도우 {w_idx}/{len(windows)}: IS {w['is_start']}~{w['is_end']} / OS {w['os_start']}~{w['os_end']} ===")

        tasks = [(name, dict(VB_BASE, **ov), w['is_start'], w['is_end']) for name, ov in grid]
        pool = mp.Pool(args.workers)
        try:
            is_results = pool.map(_worker_run, tasks)
        finally:
            pool.close()
            pool.join()

        is_map = {label: (summary, dv) for label, summary, dv in is_results}
        best = max(is_map.items(), key=lambda kv: (kv[1][0]['sharpe'], kv[1][0]['total_return']))
        best_combo, (best_sum, _) = best
        logger.info(f"IS 최적: {best_combo} (Sharpe {best_sum['sharpe']}, 총수익 {best_sum['total_return']}%)")

        os_tasks = [
            (f"os_wfa_{best_combo}", dict(VB_BASE, **dict(grid)[best_combo]), w['os_start'], w['os_end']),
            ('os_baseline', dict(VB_BASE), w['os_start'], w['os_end']),
        ]
        pool = mp.Pool(min(2, args.workers))
        try:
            os_results = pool.map(_worker_run, os_tasks)
        finally:
            pool.close()
            pool.join()

        os_map = {label: (summary, dv) for label, summary, dv in os_results}
        os_wfa_sum, os_wfa_dv = os_map[f"os_wfa_{best_combo}"]
        os_base_sum, os_base_dv = os_map['os_baseline']

        oos_wfa_segments.append({'segment': w, 'metrics': os_wfa_sum, 'daily_values': _agg_daily(os_wfa_dv)})
        oos_base_segments.append({'segment': w, 'metrics': os_base_sum, 'daily_values': _agg_daily(os_base_dv)})

        window_records.append({
            'window': w_idx,
            'is_start': w['is_start'], 'is_end': w['is_end'],
            'os_start': w['os_start'], 'os_end': w['os_end'],
            'is_best_combo': best_combo,
            'is_best_sharpe': best_sum['sharpe'],
            'is_best_return': best_sum['total_return'],
            'os_wfa': os_wfa_sum,
            'os_baseline': os_base_sum,
        })
        logger.info(
            f"OS-WFA({best_combo}): 연 {os_wfa_sum['annual_return']}% MDD {os_wfa_sum['mdd']}% | "
            f"OS-BASE: 연 {os_base_sum['annual_return']}% MDD {os_base_sum['mdd']}%"
        )

    wfa_agg = agg_oos_metrics(oos_wfa_segments)
    base_agg = agg_oos_metrics(oos_base_segments)
    logger.info("=" * 60)
    logger.info(f"통합 OOS (WFA 재최적화): {wfa_agg}")
    logger.info(f"통합 OOS (고정 baseline): {base_agg}")
    logger.info("=" * 60)

    output_dir = project_root / 'backtest' / 'output'
    output_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    report = {
        'engine': 'vb',
        'period': {'start': args.start, 'end': args.end},
        'grid_name': args.grid,
        'grid': GRID_FULL if args.grid == 'full' else GRID_QUICK,
        'windows': len(windows),
        'full_period_baseline': full_baseline,
        'oos_wfa_aggregated': wfa_agg,
        'oos_baseline_aggregated': base_agg,
        'window_records': window_records,
    }
    json_path = output_dir / f'wfa_vb_validation_{ts}.json'
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    logger.info(f"결과 저장: {json_path}")

    md = [
        "# vb WFA(워크 포워드) 검증 결과",
        "",
        f"- 기간: {args.start} ~ {args.end}, 윈도우 {len(windows)}개 (IS 36개월 → OS 12개월)",
        f"- 그리드: {args.grid} ({len(grid)} 조합), 고정 baseline: k=0.5 MA=0 hold=1",
        "",
        "## 전체 기간 baseline (참고)",
        "",
        f"총수익 {full_baseline['total_return']}% / 연환산 {full_baseline['annual_return']}% / "
        f"MDD {full_baseline['mdd']}% / Sharpe {full_baseline['sharpe']} / 거래 {full_baseline['buy_trades']}건",
        "",
        "## 통합 OOS 성과",
        "",
        "| 방식 | 연환산 | 총수익 | MDD | Sharpe | 일수 |",
        "|------|--------|--------|-----|--------|------|",
        f"| **WFA 재최적화** | {wfa_agg.get('annual_return')}% | {wfa_agg.get('total_return')}% | {wfa_agg.get('mdd')}% | {wfa_agg.get('sharpe')} | {wfa_agg.get('days')} |",
        f"| **고정 baseline** | {base_agg.get('annual_return')}% | {base_agg.get('total_return')}% | {base_agg.get('mdd')}% | {base_agg.get('sharpe')} | {base_agg.get('days')} |",
        "",
        "> 해석: IS 재최적화가 OS 성과를 고정 baseline보다 개선하면 동적 재최적화가 의미 있다.",
        "> 개선하지 못하거나 절대 성과가 마이너스면 파라미터가 노이즈에 피팅된 과적합이다.",
        "",
        "## 윈도우별 상세",
        "",
        "| 윈도우 | IS 기간 | OS 기간 | IS 최적 | IS Sharpe | OS-WFA 연% | OS-WFA MDD% | OS-BASE 연% | OS-BASE MDD% |",
        "|--------|---------|---------|---------|-----------|-----------|-------------|-------------|--------------|",
    ]
    for r in window_records:
        md.append(
            f"| {r['window']} | {r['is_start']}~{r['is_end']} | {r['os_start']}~{r['os_end']} | "
            f"{r['is_best_combo']} | {r['is_best_sharpe']:.3f} | "
            f"{r['os_wfa']['annual_return']} | {r['os_wfa']['mdd']} | "
            f"{r['os_baseline']['annual_return']} | {r['os_baseline']['mdd']} |"
        )
    md_path = output_dir / f'wfa_vb_validation_{ts}.md'
    md_path.write_text('\n'.join(md) + '\n')
    logger.info(f"요약 저장: {md_path}")


if __name__ == '__main__':
    main()
