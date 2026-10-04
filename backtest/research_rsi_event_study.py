#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RSI 신호 분해 event study (1단계)

현행 RSI(2) 급락매수 복합 신호를 구성 요소별로 분해해 사후 수익률을 검증한다.
모든 신호는 close > MA200 조건(추세 상승) 하에서 정의되며, 대조군(같은 날짜·유니버스
내 비신호 종목) 대비 초과수익으로 시장효과를 분리한다.

- 신호 A: RSI(2) < 3                (wilder, period=2)
- 신호 B: 2거래일 하락률 < -5%       (close vs close.shift(2))
- 신호 C: A AND B                   (현행 복합)

체결 모델:
- 신호일 종가 t 기준 → 익일 시가 진입 open(t+1) → 1/2/3/5거래일 후 시가 청산 open(t+1+h)
- 시가 부재시 종가 fallback + 결측 플래그. 갭수익 close(t)→open(t+1) 별도 집계.

데이터/유니버스:
- backtest.validation_common.load_all('backtest_data')
- 유니버스 스냅샷 정렬: prev_month (룩어헤드 방지, 실전 UNIVERSE_SNAPSHOT_ALIGNMENT와 동일)
- 월별 상위 250 스냅샷 기준. 첫 스냅샷(201601) 전월(201512) 부재 시 당월 폴백 (vb_engine 관례).

비용:
- 수수료 0.015% × 2(편도) + 거래세 0.20%(매도) + 슬리피지 편도 0.2% 기본 가정
- 슬리피지 민감도: 편도 0.0/0.1/0.2/0.3%

Usage:
    .venv/bin/python backtest/research_rsi_event_study.py --quick
    .venv/bin/python backtest/research_rsi_event_study.py --bootstrap-n 1000
"""
import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import numpy as np
import pandas as pd

from backtest.validation_common import load_all
from util.rsi_calc import compute_rsi

HORIZONS = (1, 2, 3, 5)
SIGNALS = ('A', 'B', 'C')
PRIMARY_H = 3  # 주 판단기간 (사전 지정)

# 비용 (backtest_engine 실전 체계와 동일, 슬리피지는 민감도로 표시)
COMMISSION_RATE = 0.00015      # 수수료 0.015%
TAX_RATE = 0.0020              # 거래세 0.20%
DEFAULT_SLIPPAGE = 0.002       # 슬리피지 기본 가정: 편도 0.2%
SLIPPAGE_LEVELS = (0.0, 0.001, 0.002, 0.003)


def _prev_yyyymm(yyyymm: str) -> str:
    y, m = int(yyyymm[:4]), int(yyyymm[4:6]) - 1
    if m == 0:
        return f'{y - 1:04d}12'
    return f'{y:04d}{m:02d}'


def _next_yyyymm(yyyymm: str) -> str:
    y, m = int(yyyymm[:4]), int(yyyymm[4:6]) + 1
    if m == 13:
        return f'{y + 1:04d}01'
    return f'{y:04d}{m:02d}'


def _total_cost_pct(slip_one_way: float) -> float:
    """편도 슬리피지 s 가정 시 왕복 총비용(%)"""
    return (COMMISSION_RATE * 2 + TAX_RATE + 2 * slip_one_way) * 100.0


def _compute_stock(code, df, code_eligible_months, availability_map, start, end):
    """단일 종목: 지표 + 신호 + 사후수익률 계산 후 유니버스 적격 행만 반환."""
    df = df.sort_index()
    close = df['close'].astype('float64')
    open_ = df['open'].astype('float64')

    rsi2 = compute_rsi(close, period=2, min_periods=2, method='wilder')
    ma200 = close.rolling(window=200, min_periods=200).mean()
    close2 = close.shift(2)
    drop2 = ((close - close2) / close2 * 100.0).replace([np.inf, -np.inf], np.nan)

    base = (close > ma200) & rsi2.notna() & ma200.notna() & drop2.notna()
    sigA = base & (rsi2 < 3.0)
    sigB = base & (drop2 < -5.0)
    sigC = sigA & sigB

    # --- 체결: 익일 시가 진입 (시가 부재시 종가 fallback + 플래그) ---
    entry_open_raw = open_.shift(-1)
    entry_close_fb = close.shift(-1)
    entry = entry_open_raw.where(entry_open_raw.notna(), entry_close_fb)
    entry = entry.where(entry.notna(), close)
    entry = entry.where(entry > 0)  # 0 이하 가격은 결측 처리
    entry_flag = entry_open_raw.isna().astype('int8')
    gap = (entry / close - 1.0) * 100.0

    out = {
        'date': df.index, 'code': code,
        'sigA': sigA, 'sigB': sigB, 'sigC': sigC,
        'gap': gap, 'entry_flag': entry_flag,
    }
    last_close = close.iloc[-1]
    for h in HORIZONS:
        ex_open = open_.shift(-(1 + h))
        ex_close = close.shift(-(1 + h))
        ex = ex_open.where(ex_open.notna(), ex_close)
        out[f'exit_flag_{h}'] = ex_open.isna().astype('int8')
        ex = ex.where(ex.notna(), last_close).where(ex > 0)
        out[f'r{h}'] = (ex / entry - 1.0) * 100.0
    res = pd.DataFrame(out)

    # --- 유니버스: prev_month 스냅샷 적격성 + 가용기간 + 기간 필터 ---
    months = res['date'].str[:6]
    elig_months = code_eligible_months.get(code, ())
    elig = months.isin(elig_months)
    if code in availability_map:
        earliest, latest = availability_map[code][:2]
        avail = months.ge(earliest) & months.le(latest)
    else:
        avail = pd.Series(True, index=res.index)
    in_range = (res['date'] >= start) & (res['date'] <= end)

    keep = elig & avail & in_range & base
    return res.loc[keep]


def _build_code_eligible_months(monthly_universe_map):
    """prev_month 정렬(부재 시 당월 폴백)로 {code: {적격월...}} 구성."""
    snap_months = set(monthly_universe_map.keys())
    snap_key_for_month = {}
    for m in sorted(snap_months):
        pk = _prev_yyyymm(m)
        snap_key_for_month[m] = pk if pk in snap_months else m  # 첫 월 폴백
    used_by = defaultdict(list)
    for m, s in snap_key_for_month.items():
        used_by[s].append(m)
    code_months = defaultdict(set)
    for snap, months in used_by.items():
        for c in monthly_universe_map.get(snap, ()):
            for m in months:
                code_months[c].add(m)
    return code_months


def date_block_bootstrap(events, col, n_iter, seed=42):
    """날짜 단위 resample 부트스트랩 (날짜 내 횡단면 의존성 보존).

    events: 'date' + col 컬럼 DataFrame. 날짜를 복원추출해 해당 날짜의
    모든 이벤트 수익률을 평균한 분포를 만든다.
    """
    if events is None or len(events) == 0:
        return np.full(n_iter, np.nan)
    g = events.groupby('date')[col].agg(['sum', 'count']).dropna(subset=['sum'])
    if g.empty:
        return np.full(n_iter, np.nan)
    sums = g['sum'].to_numpy()
    counts = g['count'].to_numpy().astype('float64')
    rng = np.random.default_rng(seed)
    n = len(g)
    means = np.empty(n_iter)
    for i in range(n_iter):
        idx = rng.integers(0, n, n)
        c = counts[idx].sum()
        means[i] = sums[idx].sum() / c if c > 0 else np.nan
    return means


def _ci(arr):
    return np.nanpercentile(arr, [2.5, 97.5])


def _event_frame(U, sig_col, control_means):
    """신호 S 이벤트 프레임: date/code/r*/ex_*/gap/플래그."""
    ev = U.loc[U[sig_col]].copy()
    cm = control_means[sig_col]
    for h in HORIZONS:
        rc = f'r{h}'
        ev[f'ex_{h}'] = ev[rc] - ev['date'].map(cm[rc])
    return ev


def build_control_means(U):
    """신호별 대조군(비신호) 일별 평균 수익률: {sigX: date→r_h}"""
    return {f'sig{s}': U.loc[~U[f'sig{s}']].groupby('date')[[f'r{h}' for h in HORIZONS]].mean()
            for s in SIGNALS}


def build_summary(events_by_signal, U, bootstrap_n):
    rows = []
    sig_list = list(SIGNALS) + ['BASE']
    for s in sig_list:
        ev = events_by_signal.get(s)
        if ev is None:
            ev = U.copy() if s == 'BASE' else None
        if ev is None:
            continue
        for h in HORIZONS:
            rc = f'r{h}'
            sub = ev.dropna(subset=[rc])
            n = len(sub)
            row = {'signal': s, 'h': h, 'n_events': n}
            if n == 0:
                row.update({'n_stocks': 0, 'n_dates': 0, 'mean': np.nan, 'median': np.nan,
                            'win_rate': np.nan, 'control_mean': np.nan, 'excess': np.nan,
                            'ci_lo': np.nan, 'ci_hi': np.nan, 'exc_lo': np.nan, 'exc_hi': np.nan})
                rows.append(row)
                continue
            row['n_stocks'] = sub['code'].nunique()
            row['n_dates'] = sub['date'].nunique()
            row['mean'] = round(sub[rc].mean(), 4)
            row['median'] = round(sub[rc].median(), 4)
            row['win_rate'] = round((sub[rc] > 0).mean() * 100, 2)
            if s != 'BASE' and f'ex_{h}' in sub.columns:
                se = sub.dropna(subset=[f'ex_{h}'])
                row['control_mean'] = round(sub[rc].mean() - se[f'ex_{h}'].mean(), 4)
                row['excess'] = round(se[f'ex_{h}'].mean(), 4)
                lo, hi = _ci(date_block_bootstrap(se, f'ex_{h}', bootstrap_n))
                row['exc_lo'], row['exc_hi'] = round(lo, 4), round(hi, 4)
            else:
                row['control_mean'], row['excess'] = np.nan, np.nan
                row['exc_lo'], row['exc_hi'] = np.nan, np.nan
            lo, hi = _ci(date_block_bootstrap(sub, rc, bootstrap_n))
            row['ci_lo'], row['ci_hi'] = round(lo, 4), round(hi, 4)
            rows.append(row)
    return pd.DataFrame(rows)


def build_yearly(events_by_signal, U):
    rows = []
    for s in list(SIGNALS) + ['BASE']:
        ev = events_by_signal[s] if s in events_by_signal else (U.copy() if s == 'BASE' else None)
        if ev is None:
            continue
        for h in HORIZONS:
            rc = f'r{h}'
            sub = ev.dropna(subset=[rc])
            if sub.empty:
                continue
            y = sub['date'].str[:4]
            grp = sub.groupby(y).agg(
                n_events=('code', 'size'),
                n_stocks=('code', 'nunique'),
                mean=(rc, lambda x: round(x.mean(), 4)),
                win_rate=(rc, lambda x: round((x > 0).mean() * 100, 2)),
            )
            if s != 'BASE' and f'ex_{h}' in sub.columns:
                exc = sub.groupby(y)[f'ex_{h}'].mean().round(4)
                grp['excess'] = exc
            else:
                grp['excess'] = np.nan
            grp.insert(0, 'signal', s)
            grp.insert(1, 'h', h)
            rows.append(grp.reset_index().rename(columns={'date': 'year'}))
    return pd.concat(rows, ignore_index=True)


def build_cost_table(events_by_signal):
    rows = []
    for s in SIGNALS:
        ev = events_by_signal[s]
        for h in HORIZONS:
            sub = ev.dropna(subset=[f'r{h}'])
            raw = sub[f'r{h}'].mean()
            row = {'signal': s, 'h': h, 'raw_mean': round(raw, 4)}
            for slip in SLIPPAGE_LEVELS:
                row[f'after_cost_slip{int(slip * 1000)}bp'] = round(raw - _total_cost_pct(slip), 4)
            rows.append(row)
    return pd.DataFrame(rows)


def build_gap_table(events_by_signal, U):
    rows = []
    for s in list(SIGNALS) + ['BASE']:
        ev = events_by_signal[s] if s in events_by_signal else U
        sub = ev.dropna(subset=['gap'])
        rows.append({
            'signal': s, 'n': len(sub),
            'gap_mean': round(sub['gap'].mean(), 4),
            'gap_median': round(sub['gap'].median(), 4),
            'entry_fallback_pct': round(sub['entry_flag'].mean() * 100, 2),
        })
    return pd.DataFrame(rows)


def build_fallback_table(events_by_signal):
    rows = []
    for s in SIGNALS:
        ev = events_by_signal[s]
        row = {'signal': s}
        for h in HORIZONS:
            sub = ev.dropna(subset=[f'r{h}'])
            row[f'exit_fallback_pct_h{h}'] = round(sub[f'exit_flag_{h}'].mean() * 100, 2)
        rows.append(row)
    return pd.DataFrame(rows)


def _fmt(v, nd=2):
    return '—' if v is None or (isinstance(v, float) and np.isnan(v)) else f'{v:.{nd}f}'


def render_md(summary, yearly, cost, gap, fallback, args, n_universe_rows, elapsed):
    lines = []
    a = lines.append
    a('# RSI 신호 분해 event study (1단계)')
    a('')
    a(f'**실행:** {time.strftime("%Y-%m-%d %H:%M:%S")} (KST) | **DB:** {args.db_name} | '
      f'**기간:** {args.start} ~ {args.end} | **bootstrap:** {args.bootstrap_n}회')
    a('')
    a('**목적:** 현행 복합 신호(RSI(2)<3 AND 2일하락률<-5%)를 구성 요소(A/B/C)로 분해해 '
      '각 신호의 사후 수익률 정보량을 대조군(시장효과) 대비 초과수익으로 검증한다.')
    a('')
    a('## 데이터 및 방법')
    a('')
    a('- 데이터: `validation_common.load_all("backtest_data")` (2,962종목), 유니버스 prev_month 정렬(룩어헤드 방지), 월별 상위 250 스냅샷.')
    a('- 지표: RSI(2) Wilder(`util/rsi_calc.compute_rsi`), MA200(rolling 200), 2거래일 하락률=(close-shift(2))/shift(2).')
    a('- 신호 조건: **모두 close > MA200** 하에서 정의. 대조군 = 같은 날짜·유니버스 내 비신호 종목(close>MA200).')
    a('- 체결: 신호일 종가 t → 익일 시가 진입 open(t+1) → open(t+1+h) 시가 청산, h∈{1,2,3,5}. '
      '시가 부재시 종가 fallback + 결측 플래그. **주 판단기간 h=3 (사전 지정).**')
    a('- 갭수익 close(t)→open(t+1) 별도 집계 (진입 시점을 개장가로 고정하므로 trade P&L은 open→open).')
    a('- 부트스트랩: 날짜 단위 resample 1000회, 90% CI(2.5~97.5 백분위). 날짜 내 종목 간 의존성 보존.')
    a(f'- 비용: 수수료 0.015%×2 + 거래세 0.20% = **0.23% 고정** + 슬리피지(기본 편도 0.2% → 왕복 0.40%). '
      f'기본 왕복 총비용 = **{_total_cost_pct(DEFAULT_SLIPPAGE):.2f}%**. 민감도 표 참조.')
    a('')
    a(f'적격 유니버스-일 행 수: **{n_universe_rows:,}** (close>MA200 & prev_month 유니버스).')
    a('')
    a('## 핵심 결과 (신호 × 보유기간)')
    a('')
    a('| 신호 | h | 이벤트 | 종목 | 날짜 | 평균(%) | 중앙값(%) | 승률(%) | 대조군(%) | 초과(%) | 초과 90% CI | 비용후(%) |')
    a('|------|---|-------:|------:|------:|--------:|----------:|--------:|----------:|--------:|-------------|----------:|')
    for _, r in summary.iterrows():
        sig = r['signal']
        mark = '**' if (sig != 'BASE' and r['h'] == PRIMARY_H) else ''
        excess = _fmt(r['excess'])
        ci = f"[{_fmt(r['exc_lo'])}, {_fmt(r['exc_hi'])}]" if not np.isnan(r['exc_lo']) else '—'
        after = '—'
        if sig != 'BASE':
            cost_row = cost[(cost.signal == sig) & (cost.h == r['h'])]
            if len(cost_row):
                after = _fmt(cost_row.iloc[0]['after_cost_slip2bp'])
        a(f'| {mark}{sig}{mark} | {r["h"]} | {r["n_events"]:,} | {r["n_stocks"]:,} | {r["n_dates"]:,} | '
          f'{_fmt(r["mean"])} | {_fmt(r["median"])} | {_fmt(r["win_rate"], 1)} | {_fmt(r["control_mean"])} | '
          f'{excess} | {ci} | {after} |')
    a('')
    a('> 비용후 = 평균수익 - (수수료 0.015%×2 + 세금 0.20% + 슬리피지 편도 0.2%×2) = 평균 - 0.63%p. '
      'BASE 행은 대조군 전체(전체 적격 유니버스) 참조.')
    a('')
    a('## 슬리피지 민감도 (비용차감 후 평균 수익률 %)')
    a('')
    a('| 신호 | h | 슬립0.0% | 슬립0.1% | 슬립0.2%(기본) | 슬립0.3% |')
    a('|------|---|---------:|---------:|---------------:|---------:|')
    for _, r in cost.iterrows():
        a(f'| {r["signal"]} | {r["h"]} | {_fmt(r["after_cost_slip0bp"])} | {_fmt(r["after_cost_slip1bp"])} | '
          f'{_fmt(r["after_cost_slip2bp"])} | {_fmt(r["after_cost_slip3bp"])} |')
    a('')
    a('## 갭수익 close(t)→open(t+1) 및 결측 플래그')
    a('')
    a('| 신호 | n | 갭 평균(%) | 갭 중앙값(%) | 진입 시가 결측(%) |')
    a('|------|----:|----------:|-------------:|------------------:|')
    for _, r in gap.iterrows():
        a(f'| {r["signal"]} | {r["n"]:,} | {_fmt(r["gap_mean"])} | {_fmt(r["gap_median"])} | {_fmt(r["entry_fallback_pct"], 1)} |')
    a('')
    a('| 신호 | h1 청산결측(%) | h2 | h3 | h5 |')
    a('|------|---------------:|---:|---:|---:|')
    for _, r in fallback.iterrows():
        a(f'| {r["signal"]} | {_fmt(r["exit_fallback_pct_h1"], 1)} | {_fmt(r["exit_fallback_pct_h2"], 1)} | '
          f'{_fmt(r["exit_fallback_pct_h3"], 1)} | {_fmt(r["exit_fallback_pct_h5"], 1)} |')
    a('')
    a('## 연도별 분해 (주 판단기간 h=3)')
    a('')
    a('| 신호 | 연도 | 이벤트 | 평균(%) | 승률(%) | 초과(%) |')
    a('|------|------|-------:|--------:|--------:|--------:|')
    y3 = yearly[(yearly.h == PRIMARY_H) & (yearly.n_events > 0)]
    for _, r in y3.iterrows():
        a(f'| {r["signal"]} | {r["year"]} | {r["n_events"]:,} | {_fmt(r["mean"])} | {_fmt(r["win_rate"], 1)} | {_fmt(r["excess"])} |')
    a('')
    a('## 재현 명령')
    a('')
    a('```bash')
    a('# 스모크 테스트 (2024년, bootstrap 100)')
    a(f'.venv/bin/python backtest/research_rsi_event_study.py --quick')
    a('# 전체 (2016-01-01 ~ 2026-06-30, bootstrap 1000)')
    a(f'.venv/bin/python backtest/research_rsi_event_study.py --db-name backtest_data '
      f'--start 20160101 --end 20260630 --bootstrap-n 1000')
    a('```')
    a('')
    a('## 재현 주의점')
    a('')
    a('- 유니버스 스냅샷은 반드시 **prev_month** 정렬 (당월 사용 시 룩어헤드). 첫 스냅샷(201601)은 전월 부재로 당월 폴백.')
    a('- 종목별 자체 거래일 기준 t+1/t+1+h (개별 종목 휴장/상장폐지 시 종가 fallback + 플래그 집계).')
    a('- 대조군은 close>MA200 적격 유니버스 내 비신호 종목. 신호 간 종목 중복은 의도됨 (B⊂C 성립 구조 아님, A·B 각각 독립 평가).')
    a('- 신호일 종가 대비 익일 시가 진입이므로 갭은 별도 집계이며 trade 수익률에는 미포함.')
    a('- 비용은 편도 슬리피지 가정에 민감 — 기본 0.2% 명시, 민감도 표로 범위 제시.')
    a(f'- 실행 시간: {elapsed:.1f}s.')
    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser(description='RSI 신호 분해 event study (1단계)')
    ap.add_argument('--db-name', default='backtest_data')
    ap.add_argument('--start', default='20160101')
    ap.add_argument('--end', default='20260630')
    ap.add_argument('--quick', action='store_true', help='스모크: 2024년 1년 + bootstrap 100')
    ap.add_argument('--bootstrap-n', type=int, default=1000)
    args = ap.parse_args()

    start, end, boot_n = args.start, args.end, args.bootstrap_n
    if args.quick:
        start, end, boot_n = '20240101', '20241231', min(boot_n, 100)
        print(f'[quick] start={start} end={end} bootstrap_n={boot_n}')

    t0 = time.time()
    price_data, availability_map, monthly_universe_map, symbol_names, (s0, e0) = load_all(args.db_name)
    print(f'로드 완료: {len(price_data)}종목, DB 기간 {s0}~{e0}')

    code_months = _build_code_eligible_months(monthly_universe_map)
    print(f'유니버스 적격 (code, month): {sum(len(v) for v in code_months.values()):,}')

    frames = []
    n_stocks = 0
    for i, (code, df) in enumerate(price_data.items()):
        res = _compute_stock(code, df, code_months, availability_map, start, end)
        if len(res):
            frames.append(res)
        if (i + 1) % 1000 == 0:
            print(f'  처리 {i + 1}/{len(price_data)}')
    U = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    n_stocks = U['code'].nunique() if len(U) else 0
    print(f'적격 유니버스 행: {len(U):,} ({n_stocks}종목, {U["date"].nunique() if len(U) else 0}일)')
    if U.empty:
        print('적격 행 없음 — 종료')
        return

    control_means = build_control_means(U)
    events = {s: _event_frame(U, f'sig{s}', control_means) for s in SIGNALS}
    for s in SIGNALS:
        print(f'  신호 {s}: 이벤트 {len(events[s]):,} (종목 {events[s]["code"].nunique():,}, '
              f'일수 {events[s]["date"].nunique():,})')

    summary = build_summary(events, U, boot_n)
    yearly = build_yearly(events, U)
    cost = build_cost_table(events)
    gap = build_gap_table(events, U)
    fallback = build_fallback_table(events)
    elapsed = time.time() - t0

    # ---- 출력 저장: reports/<날짜>_rsi_event_study/ + output/ (타임스탬프) ----
    stamp = time.strftime('%Y%m%d_%H%M%S')
    report_dir = project_root / 'backtest' / 'reports' / f'{time.strftime("%Y%m%d")}_rsi_event_study'
    report_dir.mkdir(parents=True, exist_ok=True)
    out_dir = project_root / 'backtest' / 'output'
    out_dir.mkdir(exist_ok=True)

    def save(name, obj):
        path = report_dir / f'{name}_{stamp}'
        out = out_dir / f'{name}_{stamp}'
        if isinstance(obj, pd.DataFrame):
            obj.to_csv(path.with_suffix('.csv'), index=False)
            obj.to_csv(out.with_suffix('.csv'), index=False)
        else:
            path.with_suffix('.md').write_text(obj, encoding='utf-8')
            out.with_suffix('.md').write_text(obj, encoding='utf-8')
        print(f'저장: {path.with_suffix(".csv" if isinstance(obj, pd.DataFrame) else ".md")}')

    save('event_study_summary', summary)
    save('event_study_yearly', yearly)
    save('event_study_cost', cost)
    save('event_study_gap', gap)
    save('event_study_fallback', fallback)
    ev_all = pd.concat([events[s].assign(signal=s) for s in SIGNALS], ignore_index=True)
    ev_all['code'] = ev_all['code'].astype(str).str.zfill(6)  # 선행 0 보존 (CSV 재해석 대비)
    ev_all = ev_all[['signal', 'date', 'code', 'gap', 'entry_flag'] +
                    [c for h in HORIZONS for c in (f'r{h}', f'ex_{h}', f'exit_flag_{h}')]]
    ev_all = ev_all.sort_values(['signal', 'date', 'code'])
    save('event_study_events', ev_all)

    md = render_md(summary, yearly, cost, gap, fallback, args, len(U), elapsed)
    save('event_study_report', md)

    # ---- stdout 핵심 요약 ----
    print('\n=== 핵심 결과 (h=3 주판단기간) ===')
    h3 = summary[summary.h == PRIMARY_H]
    print(f'{"신호":<5}{"이벤트":>8}{"평균%":>8}{"대조군%":>8}{"초과%":>8}{"CI":>16}{"비용후%":>8}')
    for _, r in h3.iterrows():
        sig = r['signal']
        after = ''
        if sig != 'BASE':
            after = _fmt(cost[(cost.signal == sig) & (cost.h == PRIMARY_H)].iloc[0]['after_cost_slip2bp'])
        ci = f"[{_fmt(r['exc_lo'])},{_fmt(r['exc_hi'])}]" if not np.isnan(r['exc_lo']) else ''
        print(f'{sig:<5}{r["n_events"]:>8,}{_fmt(r["mean"]):>8}{_fmt(r["control_mean"]):>8}'
              f'{_fmt(r["excess"]):>8}{ci:>16}{after:>8}')


if __name__ == '__main__':
    main()