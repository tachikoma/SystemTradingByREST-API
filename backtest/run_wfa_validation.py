"""
WFA(워크 포워드) 검증 스크립트

36개월 IS(in-sample) 구간에서 파라미터 그리드를 최적화하고,
다음 12개월 OS(out-of-sample) 구간의 성과를 측정한다. 이를 12개월 단위로
순환 반복하여 전체 기간의 진짜 out-of-sample 성과를 산출한다.

또한 각 OS 구간에 대해 고정 baseline 파라미터(v3 최적)를 함께 실행해
"동적 재최적화가 고정 파라미터보다 우월한지"를 비교한다.
재최적화가 OS 성과를 고정 파라미터보다 개선하지 못한다면
과적합(파라미터가 노이즈에 피팅) 가능성이 높다는 의미다.

IS 실행은 multiprocessing(fork)으로 병렬화한다.

사용법:
    python -m backtest.run_wfa_validation [--db-name backtest_data]
                                          [--start 2016-01-01]
                                          [--end 2026-06-30]
                                          [--grid full|quick]
                                          [--workers 6]
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
    agg_oos_metrics,
)

configure_logging(file_name='wfa_validation.log')
logger = get_logger('wfa_validation')

# fork로 상속될 공유 컨텍스트
_CTX = {}


# ---------------------------------------------------------------
# 파라미터 그리드 정의
# ---------------------------------------------------------------
GRID_FULL = {
    'rsi_sell_threshold': [70, 75, 80],
    'profit_target_percent': [0.0, 10.0],
    'time_stop_loss_days': [90, 180],
}
GRID_QUICK = {
    'rsi_sell_threshold': [70, 80],
    'profit_target_percent': [0.0],
    'time_stop_loss_days': [180],
}


def build_grid(grid_name: str) -> list:
    """그리드 딕셔너리의 직적곱을 (이름, 오버라이드) 리스트로 변환한다."""
    spec = GRID_FULL if grid_name == 'full' else GRID_QUICK
    combos = [{}]
    for key, values in spec.items():
        combos = [dict(c, **{key: v}) for c in combos for v in values]
    named = []
    for c in combos:
        name = f"SELL{c.get('rsi_sell_threshold', 70)}_PT{c.get('profit_target_percent', 0.0)}_TSL{c.get('time_stop_loss_days', 180)}"
        named.append((name, c))
    return named


# ---------------------------------------------------------------
# 워커 (fork 프로세스에서 실행)
# ---------------------------------------------------------------
def _worker_run(task):
    """task: (label, params, start_date, end_date) → (label, metric_summary, daily_values)"""
    label, params, start_date, end_date = task
    res, _ = run_single(
        _CTX['price_data'],
        _CTX['availability_map'],
        _CTX['monthly_universe_map'],
        _CTX['symbol_names'],
        start_date,
        end_date,
        params,
    )
    return label, metric_summary(res), res['daily_values']


# ---------------------------------------------------------------
# 날짜 → YYYYMM / 월 이동 헬퍼
# ---------------------------------------------------------------
def shift_month(yyyymm: str, delta: int) -> str:
    y = int(yyyymm[:4])
    m = int(yyyymm[4:6]) + delta
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y}{m:02d}"


def build_windows(start_ym: str, end_ym: str, is_months: int = 36, os_months: int = 12):
    """IS/OS 윈도우 리스트를 생성한다. OS는 12개월 간격으로 이동. 날짜는 YYYYMMDD 문자열."""
    windows = []
    cur = start_ym
    while shift_month(cur, is_months) <= end_ym:
        is_start = f"{cur[:4]}{cur[4:6]}01"
        is_end_ym = shift_month(cur, is_months - 1)
        is_end = f"{is_end_ym[:4]}{is_end_ym[4:6]}01"
        os_start_ym = shift_month(cur, is_months)
        os_end = shift_month(os_start_ym, os_months - 1)
        os_end = f"{os_end[:4]}{os_end[4:6]}01"
        windows.append({
            'is_start': is_start,
            'is_end': is_end,
            'os_start': f"{os_start_ym[:4]}{os_start_ym[4:6]}01",
            'os_end': os_end,
        })
        cur = shift_month(cur, os_months)
    return windows


# ---------------------------------------------------------------
# 메인
# ---------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description='WFA 검증')
    parser.add_argument('--db-name', default='backtest_data')
    parser.add_argument('--start', default='2016-01-01', help='시작일 (YYYY-MM-DD)')
    parser.add_argument('--end', default='2026-06-30', help='종료일 (YYYY-MM-DD)')
    parser.add_argument('--grid', default='full', choices=['full', 'quick'])
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()

    enable_indicator_cache()

    logger.info("데이터 로드 중...")
    price_data, availability_map, monthly_universe_map, symbol_names, date_range = load_all(args.db_name)
    logger.info(
        "로드 완료: 종목 %d개, DB 기간 %s ~ %s",
        len(price_data), date_range[0], date_range[1],
    )
    _CTX.update({
        'price_data': price_data,
        'availability_map': availability_map,
        'monthly_universe_map': monthly_universe_map,
        'symbol_names': symbol_names,
    })

    start_ym = args.start.replace('-', '')[:6]
    end_ym = args.end.replace('-', '')[:6]
    windows = build_windows(start_ym, end_ym)
    logger.info("WFA 윈도우 %d개 생성 (36개월 IS + 12개월 OS)", len(windows))
    if not windows:
        logger.error("윈도우를 생성할 수 없습니다. 기간을 확인하세요.")
        return

    grid = build_grid(args.grid)
    logger.info("파라미터 그리드 %d개 조합", len(grid))

    baseline = baseline_params()
    start_ts = args.start.replace('-', '')
    end_ts = args.end.replace('-', '')

    # 지표 캐시 프리웜 + 전체 기간 baseline 참고 지표
    logger.info("전체 기간 baseline 실행 (지표 캐시 프리웜)...")
    base_res, _ = run_single(
        price_data, availability_map, monthly_universe_map, symbol_names,
        start_ts, end_ts, baseline,
    )
    full_baseline = metric_summary(base_res)
    logger.info(f"전체 기간 baseline: {full_baseline}")

    oos_wfa_segments = []   # 최적화 파라미터 기반 OS 결과
    oos_base_segments = []  # 고정 baseline 기반 OS 결과
    window_records = []

    for w_idx, w in enumerate(windows, 1):
        logger.info(
            f"\n=== 윈도우 {w_idx}/{len(windows)}: IS {w['is_start']}~{w['is_end']} / "
            f"OS {w['os_start']}~{w['os_end']} ==="
        )

        # 1) IS 구간 그리드 전체 실행 (병렬)
        tasks = [(name, dict(baseline, **override), w['is_start'], w['is_end']) for name, override in grid]
        pool = mp.Pool(args.workers)
        try:
            results = pool.map(_worker_run, tasks)
        finally:
            pool.close()
            pool.join()

        is_summaries = {label: summary for label, summary, _ in results}
        best_combo = None
        best_sharpe = -1e9
        best_return = -1e9
        for name, summary in is_summaries.items():
            if summary['sharpe'] > best_sharpe:
                best_sharpe = summary['sharpe']
                best_return = summary['total_return']
                best_combo = name
            elif summary['sharpe'] == best_sharpe and summary['total_return'] > best_return:
                best_return = summary['total_return']
                best_combo = name

        if best_combo is None:
            logger.error("IS 최적화 결과 없음 — 중단")
            return

        best_override = [o for n, o in grid if n == best_combo][0]
        logger.info(f"IS 최적 조합: {best_combo} (Sharpe {best_sharpe:.3f}, 총수익 {best_return:.2f}%)")

        # 2) OS 구간 실행 (최적 조합 + 고정 baseline) — 병렬
        os_tasks = [
            (f"os_wfa_{best_combo}", dict(baseline, **best_override), w['os_start'], w['os_end']),
            ('os_baseline', baseline, w['os_start'], w['os_end']),
        ]
        pool = mp.Pool(min(2, args.workers))
        try:
            os_results = pool.map(_worker_run, os_tasks)
        finally:
            pool.close()
            pool.join()

        os_map = {label: (summary, daily) for label, summary, daily in os_results}
        os_wfa_sum, os_wfa_dv = os_map[f'os_wfa_{best_combo}']
        os_base_sum, os_base_dv = os_map['os_baseline']

        oos_wfa_segments.append({'segment': w, 'metrics': os_wfa_sum, 'daily_values': os_wfa_dv})
        oos_base_segments.append({'segment': w, 'metrics': os_base_sum, 'daily_values': os_base_dv})

        rec = {
            'window': w_idx,
            'is_start': w['is_start'], 'is_end': w['is_end'],
            'os_start': w['os_start'], 'os_end': w['os_end'],
            'is_best_combo': best_combo,
            'is_best_sharpe': best_sharpe,
            'is_best_return': best_return,
            'os_wfa': os_wfa_sum,
            'os_baseline': os_base_sum,
        }
        window_records.append(rec)
        logger.info(
            f"OS-WFA({best_combo}): 연 {os_wfa_sum['annual_return']}% MDD {os_wfa_sum['mdd']}% | "
            f"OS-BASE: 연 {os_base_sum['annual_return']}% MDD {os_base_sum['mdd']}%"
        )

    # 3) 통합 OOS 성과
    wfa_agg = agg_oos_metrics(oos_wfa_segments)
    base_agg = agg_oos_metrics(oos_base_segments)
    logger.info("=" * 60)
    logger.info(f"통합 OOS (WFA 재최적화): {wfa_agg}")
    logger.info(f"통합 OOS (고정 baseline): {base_agg}")
    logger.info("=" * 60)

    # 4) 출력 저장
    output_dir = project_root / 'backtest' / 'output'
    output_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')

    report = {
        'db': args.db_name,
        'period': {'start': args.start, 'end': args.end},
        'grid_name': args.grid,
        'grid': GRID_FULL if args.grid == 'full' else GRID_QUICK,
        'windows': len(windows),
        'full_period_baseline': full_baseline,
        'oos_wfa_aggregated': wfa_agg,
        'oos_baseline_aggregated': base_agg,
        'window_records': window_records,
    }
    path = output_dir / f'wfa_validation_{ts}.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    logger.info(f"결과 저장: {path}")

    # 5) 마크다운 요약
    md = [
        "# WFA(워크 포워드) 검증 결과",
        "",
        f"- 기간: {args.start} ~ {args.end}",
        f"- 윈도우: {len(windows)}개 (IS 36개월 → OS 12개월, 12개월 이동)",
        f"- 그리드: {args.grid} ({len(grid)} 조합)",
        "",
        "## 전체 기간 baseline (참고)",
        "",
        f"총수익 {full_baseline['total_return']}% / 연환산 {full_baseline['annual_return']}% / "
        f"MDD {full_baseline['mdd']}% / Sharpe {full_baseline['sharpe']} / "
        f"승률 {full_baseline['win_rate']}% / 거래 {full_baseline['sell_trades']}건",
        "",
        "## 통합 OOS 성과",
        "",
        "| 방식 | 연환산 | 총수익 | MDD | Sharpe | 일수 |",
        "|------|--------|--------|-----|--------|------|",
        f"| **WFA 재최적화** | {wfa_agg.get('annual_return')}% | {wfa_agg.get('total_return')}% | {wfa_agg.get('mdd')}% | {wfa_agg.get('sharpe')} | {wfa_agg.get('days')} |",
        f"| **고정 baseline** | {base_agg.get('annual_return')}% | {base_agg.get('total_return')}% | {base_agg.get('mdd')}% | {base_agg.get('sharpe')} | {base_agg.get('days')} |",
        "",
        "> 해석: WFA 재최적화가 고정 baseline보다 OS 성과가 나아야 동적 재최적화가 의미가 있다.",
        "",
        "## 윈도우별 상세",
        "",
        "| 윈도우 | IS 기간 | OS 기간 | IS 최적 조합 | IS Sharpe | OS-WFA 연% | OS-WFA MDD% | OS-BASE 연% | OS-BASE MDD% |",
        "|--------|---------|---------|--------------|-----------|-----------|-------------|-------------|--------------|",
    ]
    for r in window_records:
        md.append(
            f"| {r['window']} | {r['is_start']}~{r['is_end']} | {r['os_start']}~{r['os_end']} | "
            f"{r['is_best_combo']} | {r['is_best_sharpe']:.3f} | "
            f"{r['os_wfa']['annual_return']} | {r['os_wfa']['mdd']} | "
            f"{r['os_baseline']['annual_return']} | {r['os_baseline']['mdd']} |"
        )
    md_path = output_dir / f'wfa_validation_{ts}.md'
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(md) + '\n')
    logger.info(f"요약 저장: {md_path}")


if __name__ == '__main__':
    main()
