"""
파라미터 안정성(민감도) 검증 스크립트

walk-forward v3 최적 파라미터(baseline) 주변에서 각 파라미터를
하나씩 변형하며 성과 지표의 변화 폭을 측정한다.

해석 기준:
  - 주변 1단계 변화에 성과가 크게 요동치면(절벽/cliff) → 과적합 의심
  - 주변 변화에도 성과가 완만하면(고원/plateau) → 파라미터 안정적
  - MDD / Sharpe 변화도 함께 관찰 (수익만 보지 않음)

사용법:
    python -m backtest.run_parameter_stability [--db-name backtest_data]
                                               [--start 2016-01-01]
                                               [--end 2026-06-30]
                                               [--quick]
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

from util.logging_config import configure_logging, get_logger
from backtest.validation_common import (
    enable_indicator_cache,
    load_all,
    run_single,
    baseline_params,
    metric_summary,
)

configure_logging(file_name='parameter_stability.log')
logger = get_logger('parameter_stability')

# fork로 상속될 공유 컨텍스트
_CTX = {}


def _worker_run(task):
    """task: (label, params, start, end) → (label, metric_summary)"""
    label, params, start, end = task
    res, _ = run_single(
        _CTX['price_data'], _CTX['availability_map'], _CTX['monthly_universe_map'],
        _CTX['symbol_names'], start, end, params,
    )
    return label, metric_summary(res)


def _save_md(results_all: dict, stability: dict, ts: str, period: str):
    """결과를 마크다운 보고서로 저장한다."""
    output_dir = project_root / 'backtest' / 'output'
    md = [
        "# 파라미터 안정성(민감도) 검증 결과",
        "",
        f"- 기간: {period}",
        "- baseline: TSL180_PT0_SELL70_NO_MA20 (v3 최적)",
        "",
        "## 개별 파라미터 변경 결과",
        "",
        f"| 파라미터 | 값 | 총수익% | 연환산% | MDD% | Sharpe | 승률% | 거래수 |",
        "|---------|----|--------|--------|------|--------|-------|--------|",
    ]
    for tag, item in results_all.items():
        m = item['metrics']
        if tag == 'baseline':
            label = 'baseline'
        else:
            p, v = tag.split('=')
            mark = '*' if str(v) == str(PERTURBATIONS[p]['base']) else ''
            label = f"{p}={v}{mark}"
        md.append(
            f"| {label} | — | {m['total_return']} | {m['annual_return']} | {m['mdd']} | "
            f"{m['sharpe']} | {m['win_rate']} | {m['sell_trades']} |"
        )

    md += [
        "",
        "## 파라미터별 민감도 요약",
        "",
        "| 파라미터 | 수익범위(%p) | 수익CV(%) | MDD범위(%p) | Sharpe범위 | SharpeCV(%) | 최고/최저 총수익% |",
        "|---------|-------------|-----------|-------------|-----------|-------------|-------------------|",
    ]
    for param, s in stability.items():
        md.append(
            f"| {param} | {s['return_range_pct']} | {s['return_cv']} | {s['mdd_range_pct']} | "
            f"{s['sharpe_range']} | {s['sharpe_cv']} | {s['best_total_return']} / {s['worst_total_return']} |"
        )

    md_path = output_dir / f'parameter_stability_{ts}.md'
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md) + '\n')
    logger.info(f"요약 저장: {md_path}")

# baseline을 * 로 표기
PERTURBATIONS = {
    'rsi_buy_threshold': {'values': [2, 3, 4, 5], 'base': 3},
    'price_drop_threshold': {'values': [-8.0, -6.0, -5.0, -4.0], 'base': -5.0},
    'rsi_sell_threshold': {'values': [65, 70, 75, 80], 'base': 70},
    'profit_target_percent': {'values': [0.0, 5.0, 10.0], 'base': 0.0},
    'time_stop_loss_days': {'values': [90, 180, 360], 'base': 180},
    'use_ma20_filter': {'values': [False, True], 'base': False},
}


def main():
    parser = argparse.ArgumentParser(description='파라미터 안정성 검증')
    parser.add_argument('--db-name', default='backtest_data')
    parser.add_argument('--start', default='2016-01-01')
    parser.add_argument('--end', default='2026-06-30')
    parser.add_argument('--quick', action='store_true', help='일부 파라미터만 검증')
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

    baseline = baseline_params()
    # 날짜는 엔진 관례(YYYYMMDD)에 맞춰 전달
    start_ts = args.start.replace('-', '')
    end_ts = args.end.replace('-', '')
    results_all = {}

    # 1) baseline 실행
    logger.info("=== baseline 실행 ===")
    res, engine = run_single(
        price_data, availability_map, monthly_universe_map, symbol_names,
        start_ts, end_ts, baseline,
    )
    base_sum = metric_summary(res)
    logger.info(f"baseline: {base_sum}")
    results_all['baseline'] = {'params': baseline, 'metrics': base_sum}

    # 2) 파라미터별 변형 태스크 구성
    items = PERTURBATIONS.items()
    if args.quick:
        items = [kv for kv in items if kv[0] in ('rsi_sell_threshold', 'profit_target_percent', 'time_stop_loss_days')]

    tasks = []
    for param, spec in items:
        for value in spec['values']:
            if value == spec['base']:
                continue
            params = dict(baseline)
            params[param] = value
            tag = f"{param}={value}"
            tasks.append((tag, params, start_ts, end_ts))

    # 3) 병렬 실행
    logger.info(f"병렬 실행: {len(tasks)}건 (baseline 제외)")
    pool = mp.Pool(args.workers)
    try:
        outputs = pool.map(_worker_run, tasks)
    finally:
        pool.close()
        pool.join()
    for label, summary in outputs:
        logger.info(f"{label}: {summary}")
        tag = label
        results_all[tag] = {'params': None, 'metrics': summary}

    # 4) 민감도 집계
    import numpy as np

    stability = {}
    for param, spec in PERTURBATIONS.items():
        rows = []
        for value in spec['values']:
            tag = 'baseline' if value == spec['base'] else f"{param}={value}"
            rows.append({'value': value, **results_all[tag]['metrics']})
        if len(rows) < 2:
            continue
        rets = [r['total_return'] for r in rows]
        mdd = [r['mdd'] for r in rows]
        sharpe = [r['sharpe'] for r in rows]
        stability[param] = {
            'rows': rows,
            'return_range_pct': round(max(rets) - min(rets), 2),
            'return_cv': round(np.std(rets) / (abs(np.mean(rets)) + 1e-9) * 100, 1),
            'mdd_range_pct': round(max(mdd) - min(mdd), 2),
            'sharpe_range': round(max(sharpe) - min(sharpe), 3),
            'sharpe_cv': round(np.std(sharpe) / (abs(np.mean(sharpe)) + 1e-9) * 100, 1),
            'best_total_return': round(max(rets), 2),
            'worst_total_return': round(min(rets), 2),
            'best_sharpe': round(max(sharpe), 3),
            'worst_sharpe': round(min(sharpe), 3),
        }

    # 4) 저장
    output_dir = project_root / 'backtest' / 'output'
    output_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    report = {
        'period': {'start': args.start, 'end': args.end},
        'baseline': results_all['baseline'],
        'results': results_all,
        'stability': stability,
    }
    path = output_dir / f'parameter_stability_{ts}.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"결과 저장: {path}")

    # 5) 마크다운 요약
    _save_md(results_all, stability, ts, f"{args.start} ~ {args.end}")


if __name__ == '__main__':
    main()
