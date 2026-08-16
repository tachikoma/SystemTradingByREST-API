"""전략 엔진 MC-D(신호 정보량 permutation) 실행

vb / vb_daily 엔진에 대해 무작위 매수 null 분포와 baseline을 비교해
신호 정보량을 판정한다.

Usage:
    .venv/bin/python backtest/run_strategy_mc_d.py --engine vb --mc-d-iters 30
"""
import sys
import time
import json
import argparse
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from backtest.validation_common import load_all
from backtest.validation_strategy_adapter import (
    run_strategy, engine_names, engine_defaults, supports_buy_patch,
    run_mc_d_engine, run_mc_d_selection,
)
from backtest.trend_follow_engine import _load_single_stock


def _inject_market(price_data):
    mkt = _load_single_stock('069500')
    if mkt is not None:
        price_data['069500'] = mkt
    return price_data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--engine', choices=engine_names(), required=True)
    ap.add_argument('--period', default='20160104:20260814')
    ap.add_argument('--mc-d-iters', type=int, default=30)
    ap.add_argument('--codes', type=int, default=0)
    args = ap.parse_args()

    if not supports_buy_patch(args.engine):
        print(f"엔진 {args.engine}은 buy_patch 미지원 (trend_follow는 selection 패치 필요)")
        return 1

    start_date, end_date = args.period.split(':')
    price_data, availability_map, monthly_universe_map, symbol_names, (s, e) = load_all()

    if args.codes > 0:
        sub = sorted(c for c in price_data if c != '069500')[:args.codes]
        price_data = {c: price_data[c] for c in sub}
        availability_map = {c: availability_map[c] for c in sub if c in availability_map}

    if args.engine == 'trend_follow':
        price_data = _inject_market(price_data)

    kw = engine_defaults(args.engine)

    # baseline (신호 그대로)
    t0 = time.time()
    base, _ = run_strategy(args.engine, price_data, availability_map, monthly_universe_map,
                           symbol_names, start_date, end_date, kw)
    baseline_trades = base.get('buy_trades', 0) or 0
    print(f"baseline: 총수익 {base.get('total_return',0):.2f}% 연 {base.get('annual_return',0):.2f}% "
          f"MDD {base.get('mdd',0):.2f}% 거래 {baseline_trades} ({time.time()-t0:.1f}s)")

    # MC-D (vb/trend_follow: selection null, vb_daily: buy_patch null)
    t0 = time.time()
    if args.engine in ('vb', 'trend_follow'):
        out = run_mc_d_selection(
            args.engine, price_data, availability_map, monthly_universe_map, symbol_names,
            start_date, end_date, baseline_trades, n_iter=args.mc_d_iters, engine_kwargs=kw,
        )
    else:
        out = run_mc_d_engine(
            args.engine, price_data, availability_map, monthly_universe_map, symbol_names,
            start_date, end_date, baseline_trades, n_iter=args.mc_d_iters,
            engine_kwargs=kw,
        )
    out['elapsed_s'] = round(time.time() - t0, 1)
    print(json.dumps(out, ensure_ascii=False, indent=2))

    path = Path('backtest/output') / f'mc_d_{args.engine}_{time.strftime("%Y%m%d_%H%M%S")}.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"저장: {path}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
