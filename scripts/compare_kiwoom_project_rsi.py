#!/usr/bin/env python3
"""Compare Kiwoom sample RSI calculation with project RSI (pandas ewm/Wilder)"""
import json
import argparse
import math

import numpy as np
import pandas as pd


def kiwoom_prices_from_chart(chart, to_int=True):
    return [int(day['cur_prc']) if to_int else float(day['cur_prc']) for day in reversed(chart)]


def calculate_rsi_kiwoom(prices, period=14):
    if len(prices) < period + 2:
        return []
    deltas = [prices[i+1] - prices[i] for i in range(len(prices)-1)]
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_values = []
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss != 0:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        else:
            # follow similar handling: if avg_gain>0 -> 100, both zero -> 50, else 0
            if math.isclose(avg_gain, 0.0):
                rsi = 50.0
            else:
                rsi = 100.0
        rsi_values.append(float(rsi))
    return rsi_values


def calculate_rsi_project(prices, period=14):
    s = pd.Series(prices)
    delta = s.diff(1)
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    if len(gain) > 0:
        gain.iloc[0] = np.nan
        loss.iloc[0] = np.nan

    avg_gain = gain.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))

    both_zero = np.isclose(avg_gain, 0.0) & np.isclose(avg_loss, 0.0)
    loss_zero = np.isclose(avg_loss, 0.0) & (~both_zero)
    gain_zero = np.isclose(avg_gain, 0.0) & (~both_zero)

    rsi = rsi.astype(float)
    rsi.loc[both_zero] = 50.0
    rsi.loc[loss_zero] = 100.0
    rsi.loc[gain_zero] = 0.0

    # return as list aligned with prices index (may contain NaN at front)
    return rsi.tolist()


def compare(prices, period=2, int_prices=True, tol=1e-8, show_all=False):
    k_rsi = calculate_rsi_kiwoom(prices, period=period)
    p_rsi_full = calculate_rsi_project(prices, period=period)

    # Align indices: kiwoom rsi corresponds to deltas indices starting at period
    # For prices length n, deltas length n-1, k_rsi length = (n-1)-period
    n = len(prices)
    deltas_len = n - 1

    # p_rsi_full is length n, with NaNs for first entries. We will extract values corresponding to k_rsi positions.
    p_rsi_aligned = []
    start_idx = period + 0  # kiwoom loop starts at i=period on deltas; corresponding price index in prices is (period+1)
    # Map k_rsi[j] -> p_rsi_full at index start_idx + j + 1 ?
    # Let's compute by reconstructing: when kiwoom uses deltas[i], that delta is between prices[i] and prices[i+1].
    # The RSI value computed for deltas index i corresponds to price index i+1 (the newer price).
    # Since k loop starts at i=period, the first RSI corresponds to price index period+1.
    for j in range(len(k_rsi)):
        price_index = (period + 1) + j
        if price_index < len(p_rsi_full):
            p_val = p_rsi_full[price_index]
        else:
            p_val = float('nan')
        p_rsi_aligned.append(p_val)

    # Comparison
    diffs = []
    mismatches = []
    for idx, (kv, pv) in enumerate(zip(k_rsi, p_rsi_aligned)):
        if pv is None or (isinstance(pv, float) and (math.isnan(pv))):
            diffs.append(float('nan'))
            mismatches.append((idx, kv, pv))
        else:
            diff = abs(kv - pv)
            diffs.append(diff)
            if not math.isclose(kv, pv, rel_tol=0.0, abs_tol=tol):
                mismatches.append((idx, kv, pv, diff))

    report = {
        'n_prices': n,
        'period': period,
        'kiwoom_rsi_len': len(k_rsi),
        'project_rsi_effective_len': len([x for x in p_rsi_aligned if not (isinstance(x, float) and math.isnan(x))]),
        'mismatches_count': len(mismatches),
        'mismatches': mismatches[:20],
        'max_diff': max([d for d in diffs if not (isinstance(d, float) and math.isnan(d))], default=0.0),
    }

    if show_all:
        report['kiwoom_rsi'] = k_rsi
        report['project_rsi_aligned'] = p_rsi_aligned
        report['diffs'] = diffs

    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--chart', type=str, help='Path to Kiwoom chart JSON file (list of dicts with cur_prc)')
    p.add_argument('--period', type=int, default=2)
    p.add_argument('--int', dest='to_int', action='store_true', help='Cast prices to int like Kiwoom example')
    p.add_argument('--no-int', dest='to_int', action='store_false')
    p.set_defaults(to_int=True)
    p.add_argument('--tol', type=float, default=1e-8)
    p.add_argument('--show-all', action='store_true')
    args = p.parse_args()

    if args.chart:
        with open(args.chart, 'r', encoding='utf-8') as f:
            data = json.load(f)
        chart = data.get('stk_dt_pole_chart_qry', data)
    else:
        # fallback sample data
        chart = [
            {'cur_prc': '100'},
            {'cur_prc': '102'},
            {'cur_prc': '101'},
            {'cur_prc': '98'},
            {'cur_prc': '95'},
            {'cur_prc': '96'},
            {'cur_prc': '97'},
            {'cur_prc': '99'},
            {'cur_prc': '101'},
        ]

    prices = kiwoom_prices_from_chart(chart, to_int=args.to_int)
    report = compare(prices, period=args.period, int_prices=args.to_int, tol=args.tol, show_all=args.show_all)

    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
