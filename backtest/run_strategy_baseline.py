"""전략 엔진 전체 baseline 실행 (full universe, 2016-2026)

Usage:
    .venv/bin/python backtest/run_strategy_baseline.py [--engine vb|vb_daily|trend_follow] [--period 20160104:20260814]
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
    run_strategy, engine_names, engine_defaults, get_simulate,
)
from backtest.trend_follow_engine import _load_single_stock

ENGINE_DATA_EXTRA = {
    'trend_follow': lambda price_data: _inject_market(price_data),
}

def _inject_market(price_data):
    mkt = _load_single_stock('069500')
    if mkt is not None:
        price_data['069500'] = mkt
    return price_data

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--engine', choices=engine_names(), default=None)
    ap.add_argument('--period', default='20160104:20260814')
    ap.add_argument('--codes', type=int, default=0, help='>0이면 서브샘플(빠른 스모크)')
    args = ap.parse_args()

    start_date, end_date = args.period.split(':')
    price_data, availability_map, monthly_universe_map, symbol_names, (s, e) = load_all()

    targets = [args.engine] if args.engine else engine_names()
    if args.codes > 0:
        sub = sorted(c for c in price_data if c != '069500')[:args.codes]
        price_data = {c: price_data[c] for c in sub}
        availability_map = {c: availability_map[c] for c in sub if c in availability_map}

    out = {}
    for name in targets:
        pd_use = dict(price_data)
        t0 = time.time()
        try:
            if name == 'value':
                raise RuntimeError('value 엔진은 pykrx 필요 (미설치) — 보류')
            if name in ENGINE_DATA_EXTRA:
                pd_use = ENGINE_DATA_EXTRA[name](pd_use)
            sim = get_simulate(name)
            kw = engine_defaults(name)
            results, _ = run_strategy(name, pd_use, availability_map, monthly_universe_map,
                                      symbol_names, start_date, end_date, kw)
            elapsed = time.time() - t0
            if results:
                row = {
                    'engine': name,
                    'start': start_date, 'end': end_date,
                    'total_return': results.get('total_return', 0),
                    'annual_return': results.get('annual_return', 0),
                    'mdd': results.get('mdd', 0),
                    'sharpe': results.get('sharpe_ratio', 0),
                    'win_rate': results.get('win_rate', 0),
                    'buy_trades': results.get('buy_trades', 0),
                    'sell_trades': results.get('sell_trades', 0),
                    'days': 0 if results.get('daily_values') is None else len(results['daily_values']),
                    'elapsed_s': round(elapsed, 1),
                }
            else:
                row = {'engine': name, 'error': 'empty results', 'elapsed_s': round(time.time() - t0, 1)}
            out[name] = row
            print(json.dumps(row, ensure_ascii=False))
        except Exception as ex:
            import traceback
            out[name] = {'engine': name, 'error': str(ex), 'elapsed_s': round(time.time() - t0, 1)}
            print(json.dumps(out[name], ensure_ascii=False))
            traceback.print_exc()

    path = Path('backtest/output') / f'strategy_baselines_{time.strftime("%Y%m%d_%H%M%S")}.json'
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"저장: {path}")

if __name__ == '__main__':
    main()
