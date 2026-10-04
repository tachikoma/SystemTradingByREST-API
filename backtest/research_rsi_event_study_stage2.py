#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RSI 강도·MA200 event study (2단계)

1단계(research_rsi_event_study.py)와 동일 방법론 유지:
- prev_month 상위 250 유니버스, 2016-01-01~2026-06-30
- 체결 close(t) → open(t+1) 진입 → open(t+1+h) 청산, h∈{1,2,3,5}, 주판단 h=3
- 날짜블록 bootstrap 1000회, 비용 왕복 0.63% 기본(수수료 0.015%×2+세금 0.20%
  +슬리피지 편도 0.2%×2) + 슬리피지 민감도

추가 분석:
1. RSI 누적 임계값 3/5/10/20 — close>MA200 하, 급락조건 없이 RSI(2) 단독
2. 비중첩 구간 0-3/3-5/5-10/10-20 — 같은 universe 조건 (상호 배타)
3. MA200 ON vs OFF — 위 각 신호에 대해 close>MA200 적용/미적용 두 버전 비교.
   OFF 버전 대조군 = OFF universe(해당 유니버스 전체) 내 비신호로 재정의.
4. 급락률 대조군 대비 RSI 추가기여 — B(급락만) vs C(복합), A(RSI<3 단독) vs B
   차이의 bootstrap CI.

Usage:
    .venv/bin/python backtest/research_rsi_event_study_stage2.py --quick
    .venv/bin/python backtest/research_rsi_event_study_stage2.py --bootstrap-n 1000
"""
import argparse
import sys
import time
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import numpy as np
import pandas as pd

# 1단계 모듈 재사용 (읽기 전용, 수정 금지)
from backtest.validation_common import load_all
from backtest.research_rsi_event_study import (
    HORIZONS, PRIMARY_H, SLIPPAGE_LEVELS, DEFAULT_SLIPPAGE,
    _total_cost_pct, _build_code_eligible_months,
    date_block_bootstrap, _ci, _fmt,
)
from util.rsi_calc import compute_rsi

SIGNAL_NAMES = ('T3', 'T5', 'T10', 'T20', 'B0_3', 'B3_5', 'B5_10', 'B10_20', 'B', 'C')
SIGNAL_LABELS = {
    'T3': 'RSI(2)<3', 'T5': 'RSI(2)<5', 'T10': 'RSI(2)<10', 'T20': 'RSI(2)<20',
    'B0_3': '0≤RSI<3', 'B3_5': '3≤RSI<5', 'B5_10': '5≤RSI<10', 'B10_20': '10≤RSI<20',
    'B': '2일하락률<-5%', 'C': 'RSI<3 & 하락률<-5%',
}
VARIANTS = ('ON', 'OFF')
PAIRWISE = (
    ('C_vs_B', 'C', 'B', '급락 대비 RSI<3 추가기여 (C vs B)'),
    ('A_vs_B', 'T3', 'B', 'RSI 단독(A) vs 급락 단독(B)'),
)


def _compute_stock_rows(code, df, code_eligible_months, availability_map, start, end):
    """단일 종목: 지표 + 사후수익률 계산 후 유니버스 적격 행(전체, MA200 무관) 반환."""
    df = df.sort_index()
    close = df['close'].astype('float64')
    open_ = df['open'].astype('float64')

    rsi2 = compute_rsi(close, period=2, min_periods=2, method='wilder')
    ma200 = close.rolling(window=200, min_periods=200).mean()
    close2 = close.shift(2)
    drop2 = ((close - close2) / close2 * 100.0).replace([np.inf, -np.inf], np.nan)

    # --- 체결: 익일 시가 진입 (시가 부재시 종가 fallback + 플래그) ---
    entry_open_raw = open_.shift(-1)
    entry_close_fb = close.shift(-1)
    entry = entry_open_raw.where(entry_open_raw.notna(), entry_close_fb)
    entry = entry.where(entry.notna(), close)
    entry = entry.where(entry > 0)
    entry_flag = entry_open_raw.isna().astype('int8')
    gap = (entry / close - 1.0) * 100.0

    out = {
        'date': df.index, 'code': code, 'close': close,
        'rsi2': rsi2, 'ma200': ma200, 'drop2': drop2,
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

    keep = elig & avail & in_range
    return res.loc[keep]


def _sig_masks(pool):
    """신호 마스크: 지표는 유효(rsi2/ma200/drop2)하다는 전제 하에 조건만 적용."""
    r = pool['rsi2']
    d = pool['drop2']
    return {
        'T3': r < 3, 'T5': r < 5, 'T10': r < 10, 'T20': r < 20,
        'B0_3': (r >= 0) & (r < 3), 'B3_5': (r >= 3) & (r < 5),
        'B5_10': (r >= 5) & (r < 10), 'B10_20': (r >= 10) & (r < 20),
        'B': d < -5, 'C': (r < 3) & (d < -5),
    }


def build_variant_events(pool):
    """variant(ON/OFF)별 이벤트 프레임 + 신호별 대조군 일평균 수익률.

    대조군 = 해당 pool 내 비신호 종목 (OFF variant는 OFF universe로 재정의).
    """
    masks = _sig_masks(pool)
    ret_cols = [f'r{h}' for h in HORIZONS]
    control_means = {s: pool.loc[~m].groupby('date')[ret_cols].mean()
                     for s, m in masks.items()}
    events = {}
    for s, m in masks.items():
        ev = pool.loc[m].copy()
        cm = control_means[s]
        for h in HORIZONS:
            ev[f'ex_{h}'] = ev[f'r{h}'] - ev['date'].map(cm[f'r{h}'])
        events[s] = ev
    return events, control_means


def _summarize(ev, variant, signal, h, boot_n):
    rc = f'r{h}'
    sub = ev.dropna(subset=[rc])
    row = {'variant': variant, 'signal': signal, 'h': h, 'n_events': len(sub)}
    if len(sub) == 0:
        row.update({'n_stocks': 0, 'n_dates': 0, 'mean': np.nan, 'median': np.nan,
                    'win_rate': np.nan, 'control_mean': np.nan, 'excess': np.nan,
                    'exc_lo': np.nan, 'exc_hi': np.nan, 'ci_lo': np.nan, 'ci_hi': np.nan})
        return row
    row['n_stocks'] = sub['code'].nunique()
    row['n_dates'] = sub['date'].nunique()
    row['mean'] = round(sub[rc].mean(), 4)
    row['median'] = round(sub[rc].median(), 4)
    row['win_rate'] = round((sub[rc] > 0).mean() * 100, 2)
    exc_col = f'ex_{h}'
    if exc_col in sub.columns:
        se = sub.dropna(subset=[exc_col])
        row['control_mean'] = round(sub[rc].mean() - se[exc_col].mean(), 4)
        row['excess'] = round(se[exc_col].mean(), 4)
        lo, hi = _ci(date_block_bootstrap(se, exc_col, boot_n))
        row['exc_lo'], row['exc_hi'] = round(lo, 4), round(hi, 4)
    else:
        row['control_mean'] = row['excess'] = np.nan
        row['exc_lo'] = row['exc_hi'] = np.nan
    lo, hi = _ci(date_block_bootstrap(sub, rc, boot_n))
    row['ci_lo'], row['ci_hi'] = round(lo, 4), round(hi, 4)
    return row


def build_summary(variant_events, variant_pools, boot_n):
    rows = []
    for v in VARIANTS:
        evs = variant_events[v]
        pool = variant_pools[v]
        for s in SIGNAL_NAMES:
            for h in HORIZONS:
                rows.append(_summarize(evs[s], v, s, h, boot_n))
        for h in HORIZONS:
            rows.append(_summarize(pool, v, 'BASE', h, boot_n))  # 참조: pool 전체
    return pd.DataFrame(rows)


def build_cost(events, variant='ON'):
    rows = []
    evs = events[variant]
    for s in SIGNAL_NAMES:
        for h in HORIZONS:
            sub = evs[s].dropna(subset=[f'r{h}'])
            raw = sub[f'r{h}'].mean()
            row = {'variant': variant, 'signal': s, 'h': h, 'raw_mean': round(raw, 4)}
            for slip in SLIPPAGE_LEVELS:
                row[f'after_cost_slip{int(slip * 1000)}bp'] = round(raw - _total_cost_pct(slip), 4)
            rows.append(row)
    return pd.DataFrame(rows)


def diff_bootstrap(ev1, ev2, col, n_iter, seed=42):
    """날짜 블록 부트스트랩: mean(ev1)-mean(ev2) 분포.

    두 이벤트 프레임의 날짜 합집합을 복원추출하고, 각 프레임의 해당 날짜
    이벤트 수익률 평균의 차를 구한다 (날짜 내 횡단면 의존성 보존).
    """
    def _agg(ev):
        if ev is None or len(ev) == 0:
            return None
        g = ev.groupby('date')[col].agg(['sum', 'count']).dropna(subset=['sum'])
        return g if len(g) else None

    g1, g2 = _agg(ev1), _agg(ev2)
    if g1 is None or g2 is None:
        return np.full(n_iter, np.nan)
    dates = sorted(set(g1.index) | set(g2.index))
    s1 = g1['sum'].reindex(dates).fillna(0).to_numpy()
    c1 = g1['count'].reindex(dates).fillna(0).to_numpy().astype('float64')
    s2 = g2['sum'].reindex(dates).fillna(0).to_numpy()
    c2 = g2['count'].reindex(dates).fillna(0).to_numpy().astype('float64')
    rng = np.random.default_rng(seed)
    n = len(dates)
    diffs = np.empty(n_iter)
    for i in range(n_iter):
        idx = rng.integers(0, n, n)
        cc1, cc2 = c1[idx].sum(), c2[idx].sum()
        m1 = s1[idx].sum() / cc1 if cc1 > 0 else np.nan
        m2 = s2[idx].sum() / cc2 if cc2 > 0 else np.nan
        diffs[i] = m1 - m2
    return diffs


def build_onoff_diff(variant_events, boot_n):
    rows = []
    for s in SIGNAL_NAMES:
        for h in HORIZONS:
            on = variant_events['ON'][s].dropna(subset=[f'r{h}'])
            off = variant_events['OFF'][s].dropna(subset=[f'r{h}'])
            if not len(on) or not len(off):
                continue
            row = {'signal': s, 'h': h, 'n_on': len(on), 'n_off': len(off),
                   'mean_on': round(on[f'r{h}'].mean(), 4),
                   'mean_off': round(off[f'r{h}'].mean(), 4)}
            row['diff_mean'] = round(row['mean_on'] - row['mean_off'], 4)
            lo, hi = _ci(diff_bootstrap(on, off, f'r{h}', boot_n))
            row['diff_lo'], row['diff_hi'] = round(lo, 4), round(hi, 4)
            eon = on.dropna(subset=[f'ex_{h}'])
            eoff = off.dropna(subset=[f'ex_{h}'])
            row['exc_on'] = round(eon[f'ex_{h}'].mean(), 4)
            row['exc_off'] = round(eoff[f'ex_{h}'].mean(), 4)
            row['diff_exc'] = round(row['exc_on'] - row['exc_off'], 4)
            elo, ehi = _ci(diff_bootstrap(eon, eoff, f'ex_{h}', boot_n))
            row['exc_diff_lo'], row['exc_diff_hi'] = round(elo, 4), round(ehi, 4)
            rows.append(row)
    return pd.DataFrame(rows)


def build_pairwise(variant_events, boot_n):
    rows = []
    for pid, s1, s2, desc in PAIRWISE:
        for v in VARIANTS:
            for h in HORIZONS:
                ev1 = variant_events[v][s1].dropna(subset=[f'r{h}'])
                ev2 = variant_events[v][s2].dropna(subset=[f'r{h}'])
                if not len(ev1) or not len(ev2):
                    continue
                row = {'comparison': pid, 'desc': desc, 'variant': v, 'h': h,
                       'sig1': s1, 'sig2': s2, 'n1': len(ev1), 'n2': len(ev2)}
                m1 = ev1[f'r{h}'].mean()
                m2 = ev2[f'r{h}'].mean()
                row['mean1'] = round(m1, 4)
                row['mean2'] = round(m2, 4)
                row['diff'] = round(m1 - m2, 4)
                lo, hi = _ci(diff_bootstrap(ev1, ev2, f'r{h}', boot_n))
                row['diff_lo'], row['diff_hi'] = round(lo, 4), round(hi, 4)
                e1 = ev1.dropna(subset=[f'ex_{h}'])
                e2 = ev2.dropna(subset=[f'ex_{h}'])
                row['exc1'] = round(e1[f'ex_{h}'].mean(), 4)
                row['exc2'] = round(e2[f'ex_{h}'].mean(), 4)
                row['exc_diff'] = round(row['exc1'] - row['exc2'], 4)
                elo, ehi = _ci(diff_bootstrap(e1, e2, f'ex_{h}', boot_n))
                row['exc_diff_lo'], row['exc_diff_hi'] = round(elo, 4), round(ehi, 4)
                rows.append(row)
    return pd.DataFrame(rows)


def build_yearly(variant_events, variant_pools, variant='ON'):
    rows = []
    evs = variant_events[variant]
    pool = variant_pools[variant]
    for s in list(SIGNAL_NAMES) + ['BASE']:
        ev = evs[s] if s in evs else pool
        sub = ev.dropna(subset=[f'r{PRIMARY_H}'])
        if sub.empty:
            continue
        y = sub['date'].str[:4]
        grp = sub.groupby(y).agg(
            n_events=('code', 'size'),
            n_stocks=('code', 'nunique'),
            mean=(f'r{PRIMARY_H}', lambda x: round(x.mean(), 4)),
            win_rate=(f'r{PRIMARY_H}', lambda x: round((x > 0).mean() * 100, 2)),
        )
        if f'ex_{PRIMARY_H}' in sub.columns:
            grp['excess'] = sub.groupby(y)[f'ex_{PRIMARY_H}'].mean().round(4)
        else:
            grp['excess'] = np.nan
        grp.insert(0, 'signal', s)
        grp.insert(0, 'variant', variant)
        rows.append(grp.reset_index().rename(columns={'date': 'year'}))
    return pd.concat(rows, ignore_index=True)


def build_gap_table(variant_events, variant_pools):
    rows = []
    for v in VARIANTS:
        evs = variant_events[v]
        pool = variant_pools[v]
        for s in list(SIGNAL_NAMES) + ['BASE']:
            ev = evs[s] if s in evs else pool
            sub = ev.dropna(subset=['gap'])
            rows.append({
                'variant': v, 'signal': s, 'n': len(sub),
                'gap_mean': round(sub['gap'].mean(), 4),
                'gap_median': round(sub['gap'].median(), 4),
                'entry_fallback_pct': round(sub['entry_flag'].mean() * 100, 2),
            })
    return pd.DataFrame(rows)


def load_stage1_summary(n_events_ref):
    """1단계 summary 중 현재 실행과 A 신호 이벤트 수(h별)가 일치하는 파일을 찾는다.

    같은 기간·정의라면 이벤트 수는 결정적이므로, 이벤트 수로 실행을 식별해
    quick(2024)과 전체(2016-2026) 각각에 대응하는 1단계 산출물을 선택한다.
    없으면 None.
    """
    cands = sorted((project_root / 'backtest/reports').glob('*_rsi_event_study/event_study_summary_*.csv'))
    for p in reversed(cands):
        df = pd.read_csv(p)
        a = df[df.signal == 'A']
        if a.empty:
            continue
        counts = {int(r['h']): int(r['n_events']) for _, r in a.iterrows()}
        if counts == n_events_ref:
            return df
    return None


def _md_pair_ci(lo, hi):
    return f'[{_fmt(lo)}, {_fmt(hi)}]' if not np.isnan(lo) else '—'


def render_md(summary, onoff, pairwise, cost, yearly, gap, consis, args,
              pool_sizes, elapsed):
    lines = []
    a = lines.append
    a('# RSI 강도·MA200 event study (2단계)')
    a('')
    a(f'**실행:** {time.strftime("%Y-%m-%d %H:%M:%S")} (KST) | **DB:** {args.db_name} | '
      f'**기간:** {args.start} ~ {args.end} | **bootstrap:** {args.bootstrap_n}회')
    a('')
    a('**목적:** 1단계에서 검증한 RSI(2) 급락매수 신호 정보량을 신호 강도(임계값/구간)와 '
      'MA200 필터 효과로 분해한다. 방법론은 1단계와 완전 동일.')
    a('')
    a('## 데이터 및 방법')
    a('')
    a('- 1단계와 동일: `validation_common.load_all("backtest_data")`, prev_month 상위 250 스냅샷, '
      'RSI(2) Wilder / MA200 / 2거래일 하락률.')
    a('- 체결: close(t) → open(t+1) 진입 → open(t+1+h) 청산, h∈{1,2,3,5}, **주 판단기간 h=3**.')
    a('- 부트스트랩: 날짜블록 resample 1000회, 90% CI. 대조군 = 해당 variant pool 내 비신호 종목.')
    a(f'- 비용: 수수료 0.015%×2 + 세금 0.20% + 슬리피지(편도 0.2% 기본) → **왕복 0.63%**. '
      f'민감도(편도 0/0.1/0.2/0.3%) 표 참조.')
    a('- 변형: **ON** = close>MA200 (1단계와 동일 universe), **OFF** = MA200 필터 미적용 '
      '(같은 universe, 지표 유효 행 전체. OFF 대조군도 OFF pool 내 비신호로 재정의).')
    a('')
    a(f'적격 유니버스-일 행 수: ON **{pool_sizes["ON"]:,}**, OFF **{pool_sizes["OFF"]:,}**.')
    a('')
    a('## 1단계 정합성 체크 (RSI<3 & MA200 ON = 1단계 A)')
    a('')
    if consis is not None:
        a('| h | 2단계 T3_ON 평균 | 1단계 A 평균 | 2단계 T3_ON 초과 | 1단계 A 초과 | 이벤트 수 |')
        a('|---|-----------------:|-------------:|----------------:|-------------:|---------:|')
        for _, r in consis.iterrows():
            a(f'| {int(r["h"])} | {_fmt(r["mean2"])} | {_fmt(r["mean1"])} | '
              f'{_fmt(r["exc2"])} | {_fmt(r["exc1"])} | {int(r["n2"]):,} vs {int(r["n1"]):,} |')
        a('')
        a(f'- 최대 절대 오차(평균): **{consis["mean_abs_diff"].max():.4f}%p**, '
          f'최대 절대 오차(초과): **{consis["exc_abs_diff"].max():.4f}%p** → '
          f'{"일치" if consis["mean_abs_diff"].max() < 0.01 and consis["exc_abs_diff"].max() < 0.01 else "불일치"}')
    else:
        a('- 1단계 summary 파일을 찾지 못해 정합성 자동 비교 생략.')
    a('')
    a('## 핵심 결과 (h=3 주판단기간, RSI 누적 임계값)')
    a('')
    a('| variant | 신호 | 이벤트 | 평균(%) | 중앙값(%) | 승률(%) | 대조군(%) | 초과(%) | 초과 90% CI | 비용후(%) |')
    a('|---------|------|-------:|--------:|----------:|--------:|----------:|--------:|-------------|----------:|')
    h3 = summary[summary.h == PRIMARY_H]
    for _, r in h3[h3.signal.isin(('T3', 'T5', 'T10', 'T20', 'B', 'C'))].iterrows():
        ci = _md_pair_ci(r['exc_lo'], r['exc_hi'])
        cost_row = cost[(cost.variant == r['variant']) & (cost.signal == r['signal']) & (cost.h == r['h'])]
        after = _fmt(cost_row.iloc[0]['after_cost_slip2bp']) if len(cost_row) else '—'
        a(f'| {r["variant"]} | {SIGNAL_LABELS[r["signal"]]} | {r["n_events"]:,} | {_fmt(r["mean"])} | '
          f'{_fmt(r["median"])} | {_fmt(r["win_rate"], 1)} | {_fmt(r["control_mean"])} | '
          f'{_fmt(r["excess"])} | {ci} | {after} |')
    a('')
    a('> 비용후 = 평균 - 0.63%p (슬리피지 편도 0.2% 기본 가정).')
    a('')
    a('## 비중첩 구간 (h=3, 상호 배타 구간)')
    a('')
    a('| variant | 구간 | 이벤트 | 평균(%) | 초과(%) | 초과 90% CI |')
    a('|---------|------|-------:|--------:|--------:|-------------|')
    for _, r in h3[h3.signal.isin(('B0_3', 'B3_5', 'B5_10', 'B10_20'))].iterrows():
        ci = _md_pair_ci(r['exc_lo'], r['exc_hi'])
        a(f'| {r["variant"]} | {SIGNAL_LABELS[r["signal"]]} | {r["n_events"]:,} | {_fmt(r["mean"])} | '
          f'{_fmt(r["excess"])} | {ci} |')
    a('')
    a('## MA200 ON vs OFF (h=3)')
    a('')
    a('| 신호 | ON 평균 | OFF 평균 | 차(ON-OFF) | 차 90% CI | ON 초과 | OFF 초과 | 초과 차 | 초과차 CI |')
    a('|------|---------:|---------:|-----------:|-----------|---------:|---------:|--------:|-----------|')
    for _, r in onoff[onoff.h == PRIMARY_H].iterrows():
        a(f'| {SIGNAL_LABELS[r["signal"]]} | {_fmt(r["mean_on"])} | {_fmt(r["mean_off"])} | '
          f'{_fmt(r["diff_mean"])} | {_md_pair_ci(r["diff_lo"], r["diff_hi"])} | '
          f'{_fmt(r["exc_on"])} | {_fmt(r["exc_off"])} | {_fmt(r["diff_exc"])} | '
          f'{_md_pair_ci(r["exc_diff_lo"], r["exc_diff_hi"])} |')
    a('')
    a('> MA200 필터(추세 상승)의 기여를 나타낸다. 초과차는 각 variant의 자체 대조군 기준.')
    a('')
    a('## RSI 추가기여 (pairwise, h=3)')
    a('')
    a('| 비교 | variant | sig1 | sig2 | 평균1 | 평균2 | 차 | 차 90% CI | 초과1 | 초과2 | 초과차 | 초과차 CI |')
    a('|------|---------|------|------|------:|------:|----:|-----------|------:|------:|-------:|-----------|')
    for _, r in pairwise[pairwise.h == PRIMARY_H].iterrows():
        a(f'| {r["desc"]} | {r["variant"]} | {r["sig1"]} | {r["sig2"]} | {_fmt(r["mean1"])} | '
          f'{_fmt(r["mean2"])} | {_fmt(r["diff"])} | {_md_pair_ci(r["diff_lo"], r["diff_hi"])} | '
          f'{_fmt(r["exc1"])} | {_fmt(r["exc2"])} | {_fmt(r["exc_diff"])} | '
          f'{_md_pair_ci(r["exc_diff_lo"], r["exc_diff_hi"])} |')
    a('')
    a('> C vs B: 급락(-5%) 종목 중 RSI<3 조건을 추가로 요구했을 때의 증분. '
      'A vs B: RSI 단독과 급락 단독 신호의 직접 비교.')
    a('')
    a('## 슬리피지 민감도 (비용차감 후, h=3, ON)')
    a('')
    a('| 신호 | 슬립0.0% | 슬립0.1% | 슬립0.2%(기본) | 슬립0.3% |')
    a('|------|---------:|---------:|---------------:|---------:|')
    for _, r in cost[(cost.variant == 'ON') & (cost.h == PRIMARY_H)].iterrows():
        a(f'| {SIGNAL_LABELS[r["signal"]]} | {_fmt(r["after_cost_slip0bp"])} | {_fmt(r["after_cost_slip1bp"])} | '
          f'{_fmt(r["after_cost_slip2bp"])} | {_fmt(r["after_cost_slip3bp"])} |')
    a('')
    a('## 연도별 분해 (h=3, ON, 주요 신호)')
    a('')
    a('| 신호 | 연도 | 이벤트 | 평균(%) | 승률(%) | 초과(%) |')
    a('|------|------|-------:|--------:|--------:|--------:|')
    y3 = yearly[yearly.signal.isin(('T3', 'T5', 'T10', 'T20', 'B', 'C')) & (yearly.n_events > 0)]
    for _, r in y3.iterrows():
        a(f'| {SIGNAL_LABELS[r["signal"]]} | {r["year"]} | {r["n_events"]:,} | {_fmt(r["mean"])} | '
          f'{_fmt(r["win_rate"], 1)} | {_fmt(r["excess"])} |')
    a('')
    a('## 갭수익·결측 플래그 (h=3 진입 기준)')
    a('')
    a('| variant | 신호 | n | 갭 평균(%) | 진입 결측(%) |')
    a('|---------|------|----:|----------:|-------------:|')
    for _, r in gap[gap.signal.isin(('T3', 'B', 'C', 'BASE'))].iterrows():
        a(f'| {r["variant"]} | {r["signal"]} | {r["n"]:,} | {_fmt(r["gap_mean"])} | '
          f'{_fmt(r["entry_fallback_pct"], 1)} |')
    a('')
    a('## 재현 명령')
    a('')
    a('```bash')
    a('# 스모크 테스트 (2024년, bootstrap 100)')
    a(f'.venv/bin/python backtest/research_rsi_event_study_stage2.py --quick')
    a('# 전체 (2016-01-01 ~ 2026-06-30, bootstrap 1000)')
    a(f'.venv/bin/python backtest/research_rsi_event_study_stage2.py --db-name backtest_data '
      f'--start 20160101 --end 20260630 --bootstrap-n 1000')
    a('```')
    a('')
    a('## 재현 주의점')
    a('')
    a('- 유니버스 prev_month 정렬(룩어헤드 방지), 첫 스냅샷(201601)은 당월 폴백.')
    a('- ON/OFF 모두 지표 유효(rsi2/ma200/drop2 notna) 행 기준 — MA200 미형성 종목은 양쪽에서 제외해 '
      '필터 효과만을 격리 (OFF라도 200일 이력 없으면 제외).')
    a('- OFF 대조군은 OFF pool(MA200 무관 전체) 내 비신호로 재정의.')
    a('- T3_ON(RSI<3 & close>MA200)은 1단계 신호 A와 동일 정의 — 정합성 체크로 검증.')
    a('- 비용은 슬리피지 편도 가정에 민감 — 기본 0.2% 명시, 민감도 표로 범위 제시.')
    a(f'- 실행 시간: {elapsed:.1f}s.')
    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser(description='RSI 강도·MA200 event study (2단계)')
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

    frames = []
    for i, (code, df) in enumerate(price_data.items()):
        res = _compute_stock_rows(code, df, code_months, availability_map, start, end)
        if len(res):
            frames.append(res)
        if (i + 1) % 1000 == 0:
            print(f'  처리 {i + 1}/{len(price_data)}')
    U_all = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if U_all.empty:
        print('적격 행 없음 — 종료')
        return

    valid = U_all['rsi2'].notna() & U_all['ma200'].notna() & U_all['drop2'].notna()
    U_off = U_all.loc[valid].copy()
    U_on = U_off.loc[U_off['close'] > U_off['ma200']].copy()
    pools = {'ON': U_on, 'OFF': U_off}
    print(f'적격 유니버스 행: ON {len(U_on):,} / OFF {len(U_off):,} '
          f'(종목 {U_off["code"].nunique():,}, 일수 {U_off["date"].nunique():,})')

    variant_events = {}
    for v in VARIANTS:
        evs, _ = build_variant_events(pools[v])
        variant_events[v] = evs
        for s in SIGNAL_NAMES:
            ev = evs[s]
            print(f'  {v} {s:<5}: 이벤트 {len(ev):,} (종목 {ev["code"].nunique():,}, '
                  f'일수 {ev["date"].nunique():,})')

    summary = build_summary(variant_events, pools, boot_n)
    onoff = build_onoff_diff(variant_events, boot_n)
    pairwise = build_pairwise(variant_events, boot_n)
    cost_on = build_cost(variant_events, 'ON')
    cost_off = build_cost(variant_events, 'OFF')
    cost = pd.concat([cost_on, cost_off], ignore_index=True)
    yearly = build_yearly(variant_events, pools, 'ON')
    gap = build_gap_table(variant_events, pools)
    elapsed = time.time() - t0

    # ---- 1단계 정합성 체크 (같은 기간·이벤트 수인 1단계 실행과 비교) ----
    consis = None
    t3_on = summary[(summary.variant == 'ON') & (summary.signal == 'T3')]
    n_ref = {int(r['h']): int(r['n_events']) for _, r in t3_on.iterrows()}
    s1 = load_stage1_summary(n_ref)
    if s1 is not None:
        a1 = s1[s1.signal == 'A'][['h', 'mean', 'excess']].rename(columns={'mean': 'mean1', 'excess': 'exc1'})
        a2 = summary[(summary.variant == 'ON') & (summary.signal == 'T3')][['h', 'mean', 'excess', 'n_events']] \
            .rename(columns={'mean': 'mean2', 'excess': 'exc2', 'n_events': 'n2'})
        m = a1.merge(a2, on='h')
        m = m[m.n2.notna()]
        a1s = s1[s1.signal == 'A'][['h', 'n_events']].rename(columns={'n_events': 'n1'})
        m = m.merge(a1s, on='h')
        m['mean_abs_diff'] = (m['mean2'] - m['mean1']).abs()
        m['exc_abs_diff'] = (m['exc2'] - m['exc1']).abs()
        consis = m
        print('\n=== 1단계 정합성 (T3_ON vs A) ===')
        print(consis[['h', 'mean1', 'mean2', 'exc1', 'exc2', 'mean_abs_diff', 'exc_abs_diff']].to_string(index=False))

    # ---- 출력 저장 ----
    stamp = time.strftime('%Y%m%d_%H%M%S')
    report_dir = project_root / 'backtest' / 'reports' / f'{time.strftime("%Y%m%d")}_rsi_event_study_stage2'
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

    save('stage2_summary', summary)
    save('stage2_onoff_diff', onoff)
    save('stage2_pairwise', pairwise)
    save('stage2_cost', cost)
    save('stage2_yearly', yearly)
    save('stage2_gap', gap)
    if consis is not None:
        save('stage2_consistency_stage1', consis)
    md = render_md(summary, onoff, pairwise, cost, yearly, gap, consis, args,
                   {v: len(p) for v, p in pools.items()}, elapsed)
    save('stage2_report', md)

    # ---- stdout 핵심 요약 ----
    print('\n=== 핵심 결과 (h=3) ===')
    h3 = summary[(summary.h == PRIMARY_H) & summary.signal.isin(('T3', 'T5', 'T10', 'T20', 'B', 'C'))]
    print(f'{"variant":<5}{"신호":<6}{"이벤트":>8}{"평균%":>8}{"초과%":>8}{"CI":>16}{"비용후%":>8}')
    for _, r in h3.iterrows():
        ci = f"[{_fmt(r['exc_lo'])},{_fmt(r['exc_hi'])}]" if not np.isnan(r['exc_lo']) else ''
        cost_row = cost[(cost.variant == r['variant']) & (cost.signal == r['signal']) & (cost.h == r['h'])]
        after = _fmt(cost_row.iloc[0]['after_cost_slip2bp']) if len(cost_row) else ''
        print(f'{r["variant"]:<5}{r["signal"]:<6}{r["n_events"]:>8,}{_fmt(r["mean"]):>8}'
              f'{_fmt(r["excess"]):>8}{ci:>16}{after:>8}')
    print('\n=== MA200 ON-OFF (h=3) ===')
    for _, r in onoff[onoff.h == PRIMARY_H].iterrows():
        ci = f"[{_fmt(r['diff_lo'])},{_fmt(r['diff_hi'])}]" if not np.isnan(r['diff_lo']) else ''
        print(f'{r["signal"]:<6}ON={_fmt(r["mean_on"]):>7} OFF={_fmt(r["mean_off"]):>7} '
              f'차={_fmt(r["diff_mean"]):>7} {ci}')
    print('\n=== RSI 추가기여 (h=3) ===')
    for _, r in pairwise[pairwise.h == PRIMARY_H].iterrows():
        ci = f"[{_fmt(r['diff_lo'])},{_fmt(r['diff_hi'])}]" if not np.isnan(r['diff_lo']) else ''
        print(f'{r["desc"]:<28} {r["variant"]:<4} 차={_fmt(r["diff"]):>7} {ci}')


if __name__ == '__main__':
    main()