"""
엔진 기반 파라미터 안정성(민감도) 검증 — vb 엔진용

k×MA 그리드를 전체 기간 + 두 부분 기간(전반/후반)에 걸쳐 실행해
최적 파라미터의 표면(고원 vs 스파이크)과 기간간 안정성을 측정한다.

판정:
  - 최적 지점이 외로운 스파이크(주변 노치에서 급변) → 과적합
  - 인접 파라미터에도 완만한 고원 + 부분기간 일치 → 안정적
  - 부분기간 간 최적 k/MA가 뒤집히면 → 불안정

Usage:
    .venv/bin/python backtest/run_strategy_param_stability.py [--workers 2]
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
from backtest.validation_common import load_all
from backtest.validation_strategy_adapter import run_strategy, normalize_results

configure_logging(file_name='param_stability_vb.log')
logger = get_logger('param_stability_vb')

_CTX = {}

VB_BASE = {'k': 0.5, 'ma_filter_period': 0, 'stop_loss_pct': -5.0, 'hold_days': 1}

# 표면 그리드
K_RANGE = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
MA_RANGE = [0, 5, 20]


def _run(start, end, params):
    res, _ = run_strategy('vb', _CTX['price_data'], _CTX['availability_map'],
                          _CTX['monthly_universe_map'], _CTX['symbol_names'], start, end, params)
    res = normalize_results(res, 'vb')
    return {
        'total_return': round(res.get('total_return', 0), 2),
        'annual_return': round(res.get('annual_return', 0), 2),
        'mdd': round(res.get('mdd', 0), 2),
        'sharpe': round(res.get('sharpe_ratio', 0), 3),
        'win_rate': round(res.get('win_rate', 0), 2),
        'buy_trades': res.get('buy_trades', 0),
    }


def _worker_run(task):
    label, start, end, params = task
    return label, _run(start, end, params)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db-name', default='backtest_data')
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

    periods = {
        'full': ('20160101', '20260814'),
        'half1_2016_2021': ('20160101', '20211231'),
        'half2_2021_2026': ('20210101', '20260814'),
    }

    combos = [(f"k{k}_MA{ma}", dict(VB_BASE, k=k, ma_filter_period=ma))
              for k in K_RANGE for ma in MA_RANGE]

    results = {}
    for pname, (s, e) in periods.items():
        logger.info(f"기간 {pname} ({s}~{e}) 실행...")
        tasks = [(label, s, e, params) for label, params in combos]
        pool = mp.Pool(args.workers)
        try:
            out = pool.map(_worker_run, tasks)
        finally:
            pool.close()
            pool.join()
        results[pname] = {label: m for label, m in out}

    # ---- 저장 ----
    output_dir = project_root / 'backtest' / 'output'
    output_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    json_path = output_dir / f'param_stability_vb_{ts}.json'
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    logger.info(f"결과 저장: {json_path}")

    # ---- 보고서 ----
    md = [
        "# vb 파라미터 안정성(민감도) 검증 결과",
        "",
        "- 그리드: k ∈ " + str(K_RANGE) + ", MA ∈ " + str(MA_RANGE) + " (hold=1, stop=-5% 고정)",
        "",
        "## 전체 기간 연환산 수익률 표면 (%)",
        "",
        "| k \\ MA | " + " | ".join(str(m) for m in MA_RANGE) + " |",
        "|--------" + "|------" * len(MA_RANGE) + "|",
    ]
    full = results['full']
    best_full = max(full, key=lambda t: full[t]['annual_return'])
    for k in K_RANGE:
        row = [f"**{k}**"]
        for ma in MA_RANGE:
            m = full[f'k{k}_MA{ma}']
            star = '*' if f'k{k}_MA{ma}' == best_full else ''
            row.append(f"{m['annual_return']}{star}")
        md.append("| " + " | ".join(row) + " |")

    md += [
        "",
        "## 부분 기간 최적 조합",
        "",
        "| 기간 | 최적 조합 | 연환산% | MDD% | Sharpe |",
        "|------|-----------|--------|------|--------|",
    ]
    for pname, m in [(p, max(results[p], key=lambda t: results[p][t]['annual_return']))
                     for p in periods]:
        mm = results[pname][m]
        md.append(f"| {pname} | {m} | {mm['annual_return']} | {mm['mdd']} | {mm['sharpe']} |")

    md += [
        "",
        "## k별 / MA별 민감도 (전체 기간)",
        "",
        "| 축 | 값 | 연환산% | 총수익% | MDD% | Sharpe |",
        "|----|----|--------|--------|------|--------|",
    ]
    for k in K_RANGE:
        for ma in MA_RANGE:
            m = full[f'k{k}_MA{ma}']
            md.append(f"| k={k} | MA={ma} | {m['annual_return']} | {m['total_return']} | {m['mdd']} | {m['sharpe']} |")

    md_path = output_dir / f'param_stability_vb_{ts}.md'
    md_path.write_text('\n'.join(md) + '\n')
    logger.info(f"요약 저장: {md_path}")

    # 콘솔 요약
    print("\n=== 전체 기간 연환산 표면 (%) ===")
    header = "k\\MA  " + "  ".join(f"{m:>8}" for m in MA_RANGE)
    print(header)
    for k in K_RANGE:
        vals = "  ".join(f"{full[f'k{k}_MA{ma}']['annual_return']:>8.2f}" for ma in MA_RANGE)
        print(f"{k:<5}" + vals)
    print(f"\n전체기간 최적: {best_full} 연 {full[best_full]['annual_return']}%")
    for p in periods:
        b = max(results[p], key=lambda t: results[p][t]['annual_return'])
        print(f"{p} 최적: {b} 연 {results[p][b]['annual_return']}% MDD {results[p][b]['mdd']}%")


if __name__ == '__main__':
    main()
