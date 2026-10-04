#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
3단계(제한) — 복합신호 C 비용후 마진의 데이터·체결 처리 아티팩트 검증

2.5단계 복합신호 C(RSI(2)<3 & 2일하락률<-5% & close>MA200, h=3 고정)의 비용후
+0.29%가 데이터/체결 처리의 아티팩트인지 좁은 범위에서 재검증한다.

필수 범위 (4가지):
1. 통계·표기 보정
   - 전체 시장 캘린더(신호 없는 거래일 포함) 기반 moving-block bootstrap(블록 5,
     1000회)으로 원수익·비용후(왕복 0.63% 고정)·초과의 95% CI 재계산.
   - 매칭 비교는 매칭된 동일 표본의 신호평균/대조평균/차이로 통일 표시.
   - 절대순수익(신호 평균 - 비용) vs 상대예측력(매칭 차이) 분리. 매칭차이에
     왕복비용을 통째 차감하지 않음 (양쪽 체결 비용이 동일해 상쇄).
2. 누락 거래 구분
   - 진입 시가 확정불가(미체결 가정) vs 진입후 청산 시가 확정불가(보유 리스크) 분리.
   - 청산불가 사유: 거래정지 / 상장폐지·가용기간종료 / 단순 데이터누락
     (availability_map + 월별 스냅샷 + DB 행 존재로 판별, 판별불가는 '不明').
   - 기간말 캘린더 잘림 영향 별도 표시.
3. 큰 반등 감사
   - 상위기여 종목·상위 1%/5% 이벤트(특히 450140 2025-09, 066430 2026-06,
     136510 등)에 대해 DB 원시 OHLCV 읽기전용 조회로 가격경로·거래량·상한가 근접·
     시가갭·시가=고가/저가 확인. 수정주가(FDR Naver 소스) 한계 명시.
   - 검증된 정상 vs 체결의심 분류. 의심분량 제외 민감도만 추가 (신규 필터 설계 금지).
4. 포착률 스트레스
   - 보수적 체결 가정 2가지 (A: 상한가 근접 개장 제외, B: 일별 거래대금 대비
     가정 포지션 상한)만 적용, 현재 신호·h=3 고정 평가. 최적 가정 탐색 금지.

Usage:
    .venv/bin/python backtest/research_rsi_event_study_stage3_limited.py --quick
    .venv/bin/python backtest/research_rsi_event_study_stage3_limited.py --bootstrap-n 1000 --block-len 5
"""
import argparse
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import numpy as np
import pandas as pd

from backtest.validation_common import load_all
from backtest.research_rsi_event_study import (
    HORIZONS, PRIMARY_H, DEFAULT_SLIPPAGE,
    _total_cost_pct, _build_code_eligible_months, _ci, _fmt,
)
from backtest.research_rsi_event_study_stage25 import (
    _build_code_eligible_months_strict, _build_global_calendar, _first_and_dedup5,
)
from util.rsi_calc import compute_rsi
from util.db_helper import execute_sql

COST = _total_cost_pct(DEFAULT_SLIPPAGE)  # 왕복 0.63% 고정
# 보수적 체결 가정 (사전 고정, 탐색 금지)
LIMIT_NEAR_PCT = 1.295          # A: 전일종가 대비 +29.5% 이상 개장 = 상한가 근접
ASSUMED_POSITION = 5_000_000    # B: 가정 포지션 500만원 (1억 자본의 5%)
POSITION_TURNOVER_CAP = 0.01    # B: 포지션이 당일 거래대금의 1% 초과 시 체결 곤란


def _compute_stock_stage3(code, df, gcal, gpos, code_eligible_months, availability_map, start, end):
    """단일 종목: 지표(자체 거래일) + 진입/청산(공통 시장 캘린더, 시가만).

    진입일 시가/고가/거래량 + 수익률을 반환 (missing 분류·감사·스트레스용).
    """
    df = df.sort_index()
    close = df['close'].astype('float64')
    rsi2 = compute_rsi(close, period=2, min_periods=2, method='wilder')
    ma200 = close.rolling(window=200, min_periods=200).mean()
    close2 = close.shift(2)
    drop2 = ((close - close2) / close2 * 100.0).replace([np.inf, -np.inf], np.nan)

    dfg = df.reindex(gcal)
    g_open = dfg['open'].astype('float64').where(lambda s: s > 0)
    g_high = dfg['high'].astype('float64').where(lambda s: s > 0)
    g_vol = dfg['volume'].astype('float64').where(lambda s: s > 0)
    entry_open = g_open.shift(-1)
    entry_high = g_high.shift(-1)
    entry_vol = g_vol.shift(-1)
    entry_missing = entry_open.isna()

    out = {'date': df.index, 'code': code, 'close': close,
           'rsi2': rsi2, 'ma200': ma200, 'drop2': drop2,
           'entry_open': entry_open.loc[df.index].to_numpy(),
           'entry_high': entry_high.loc[df.index].to_numpy(),
           'entry_vol': entry_vol.loc[df.index].to_numpy(),
           'entry_missing': entry_missing.loc[df.index].to_numpy()}
    for h in HORIZONS:
        exit_open = g_open.shift(-(1 + h))
        exit_missing = exit_open.isna()
        r_h = (exit_open / entry_open - 1.0) * 100.0
        out[f'exit_missing_{h}'] = exit_missing.loc[df.index].to_numpy()
        out[f'r{h}'] = r_h.loc[df.index].to_numpy()
    res = pd.DataFrame(out)

    months = res['date'].str[:6]
    elig = months.isin(code_eligible_months.get(code, ()))
    if code in availability_map:
        earliest, latest = availability_map[code][:2]
        avail = months.ge(earliest) & months.le(latest)
    else:
        avail = pd.Series(True, index=res.index)
    in_range = (res['date'] >= start) & (res['date'] <= end)
    keep = elig & avail & in_range
    return res.loc[keep]


def block_bootstrap_calendar(events, col, gcal, block_len, n_iter, seed=42):
    """전체 시장 캘린더(신호 없는 거래일 포함) 기반 moving-block bootstrap.

    날짜별 합/건수를 전 캘린더에 정렬(신호 없는 날은 0)하고 5거래일 연속 블록으로
    복원추출해 count-weighted 평균 분포를 만든다.
    """
    if events is None or len(events) == 0:
        return np.full(n_iter, np.nan)
    g = events.groupby('date')[col].agg(['sum', 'count']).dropna(subset=['sum'])
    if g.empty:
        return np.full(n_iter, np.nan)
    sums = g['sum'].reindex(gcal).fillna(0).to_numpy()
    counts = g['count'].reindex(gcal).fillna(0).to_numpy()
    n = len(gcal)
    if n <= block_len:
        c = counts.sum()
        return np.full(n_iter, sums.sum() / c if c > 0 else np.nan)
    nb = int(np.ceil(n / block_len))
    rng = np.random.default_rng(seed)
    means = np.empty(n_iter)
    for i in range(n_iter):
        idx = []
        for _ in range(nb):
            s = rng.integers(0, n - block_len + 1)
            idx.extend(range(s, s + block_len))
        idx = np.array(idx)
        c = counts[idx].sum()
        means[i] = sums[idx].sum() / c if c > 0 else np.nan
    return means


def _summarize_corrected(ev, h, gcal, block_len, boot_n, control_map=None):
    rc = f'r{h}'
    ac = f'ac{h}'
    sub = ev.dropna(subset=[rc])
    n = len(sub)
    row = {'h': h, 'n_events': n}
    if n == 0:
        row.update({'n_stocks': 0, 'n_dates': 0, 'mean': np.nan, 'median': np.nan,
                    'win_rate': np.nan, 'ac_mean': np.nan,
                    'raw_95ci_lo': np.nan, 'raw_95ci_hi': np.nan,
                    'ac_95ci_lo': np.nan, 'ac_95ci_hi': np.nan,
                    'excess': np.nan, 'exc_95ci_lo': np.nan, 'exc_95ci_hi': np.nan})
        return row
    row['n_stocks'] = sub['code'].nunique()
    row['n_dates'] = sub['date'].nunique()
    row['mean'] = round(sub[rc].mean(), 4)
    row['median'] = round(sub[rc].median(), 4)
    row['win_rate'] = round((sub[rc] > 0).mean() * 100, 2)
    row['ac_mean'] = round(sub[ac].mean(), 4)   # 절대순수익(비용후)
    lo, hi = _ci(block_bootstrap_calendar(sub, rc, gcal, block_len, boot_n))
    row['raw_95ci_lo'], row['raw_95ci_hi'] = round(lo, 4), round(hi, 4)
    alo, ahi = _ci(block_bootstrap_calendar(sub, ac, gcal, block_len, boot_n))
    row['ac_95ci_lo'], row['ac_95ci_hi'] = round(alo, 4), round(ahi, 4)
    exc_col = f'ex{h}'
    if control_map is not None and exc_col in sub.columns:
        se = sub.dropna(subset=[exc_col])
        row['excess'] = round(se[exc_col].mean(), 4)   # 상대예측력(표준 대조군)
        lo, hi = _ci(block_bootstrap_calendar(se, exc_col, gcal, block_len, boot_n))
        row['exc_95ci_lo'], row['exc_95ci_hi'] = round(lo, 4), round(hi, 4)
    else:
        row['excess'] = np.nan
        row['exc_95ci_lo'] = row['exc_95ci_hi'] = np.nan
    return row


def matched_same_sample(events, ctrl_by_date, h):
    """매칭된 동일 표본: 신호값/대조값/차이 Series 반환 (date 포함)."""
    rc = f'r{h}'
    sig_vals, ctrl_vals, dates = [], [], []
    n_no_match = 0
    for date, evd in events.groupby('date'):
        grp = ctrl_by_date.get(date)
        if grp is None or grp.empty:
            n_no_match += len(evd)
            continue
        cd = grp['drop2'].to_numpy()
        cr = grp[rc].to_numpy()
        for idx, dropv in zip(evd.index, evd['drop2'].to_numpy()):
            m = (cd >= dropv - 1.0) & (cd <= dropv + 1.0)
            if m.any():
                sig_vals.append(evd.loc[idx, rc])
                ctrl_vals.append(cr[m].mean())
                dates.append(date)
            else:
                n_no_match += 1
    mdf = pd.DataFrame({'date': dates, 'sig': sig_vals, 'ctrl': ctrl_vals})
    if len(mdf):
        mdf['diff'] = mdf['sig'] - mdf['ctrl']
    return mdf, n_no_match


def _matched_stats_same(mdf, n_no_match, gcal, block_len, boot_n):
    n = len(mdf)
    row = {'n_matched': n, 'n_no_match': n_no_match}
    if n == 0:
        row.update({'signal_mean': np.nan, 'control_mean': np.nan, 'diff_mean': np.nan,
                    'diff_95ci_lo': np.nan, 'diff_95ci_hi': np.nan,
                    'ac_signal_mean': np.nan})
        return row
    row['signal_mean'] = round(mdf['sig'].mean(), 4)          # 절대(비용전)
    row['control_mean'] = round(mdf['ctrl'].mean(), 4)
    row['diff_mean'] = round(mdf['diff'].mean(), 4)           # 상대예측력(비용 미차감)
    lo, hi = _ci(block_bootstrap_calendar(mdf, 'diff', gcal, block_len, boot_n))
    row['diff_95ci_lo'], row['diff_95ci_hi'] = round(lo, 4), round(hi, 4)
    row['ac_signal_mean'] = round(mdf['sig'].mean() - COST, 4)  # 절대순수익
    return row


def classify_missing(code, d, stock_dates, availability_map, monthly_universe_map):
    """결측 거래일 d의 사유 분류.

    우선순위: (1) 행 존재하나 시가 0/NaN(수정주가 아티팩트) → 시가결손,
    (2) 마지막 행 이후 → 상장폐지·가용기간종료, (3) 중간 갭 → 거래정지
    (당월 유니버스 스냅샷 존재 시) / 단순 데이터누락, (4) 기타 → 不明.
    """
    dates = stock_dates[code]
    n = len(dates)
    i = int(np.searchsorted(dates, d))
    if i < n and dates[i] == d:
        # 행은 존재하나 시가 결손 (open=0/NaN — FDR 수정주가 아티팩트 등)
        return '시가결손(행존재·수정주가 아티팩트 가능)'
    if d > dates[-1]:
        return '상장폐지·가용기간종료'
    if i > 0 and i < n:
        snap = monthly_universe_map.get(d[:6])
        if snap and code in snap:
            return '거래정지'
        return '단순 데이터누락'
    return '不明'


def collect_missing(on, sigC, gcal, gpos, stock_dates, availability_map, monthly_universe_map):
    """진입/청산 확정불가 이벤트를 사유별로 분해. 기간말 캘린더 잘림 별도."""
    evs = on.loc[sigC]
    rows = []
    for h in HORIZONS:
        em = Counter()
        ex = Counter()
        truncated = 0
        n_ent = n_exit = 0
        for date, code, e_miss, x_miss in zip(evs['date'], evs['code'],
                                              evs['entry_missing'], evs[f'exit_missing_{h}']):
            p = gpos[date]
            entry_day = gcal[p + 1] if p + 1 < len(gcal) else None
            exit_day = gcal[p + 1 + h] if p + 1 + h < len(gcal) else None
            if e_miss:
                n_ent += 1
                em[classify_missing(code, entry_day, stock_dates, availability_map,
                                    monthly_universe_map) or '不明'] += 1
            else:
                if exit_day is None:
                    truncated += 1
                elif x_miss:
                    n_exit += 1
                    ex[classify_missing(code, exit_day, stock_dates, availability_map,
                                        monthly_universe_map) or '不明'] += 1
        rows.append({'category': '진입(미체결 가정)', 'h': h, 'reason': '합계', 'n': n_ent})
        for r, c in sorted(em.items()):
            rows.append({'category': '진입(미체결 가정)', 'h': h, 'reason': r, 'n': c})
        rows.append({'category': '진입후 청산(보유 리스크)', 'h': h, 'reason': '합계', 'n': n_exit})
        for r, c in sorted(ex.items()):
            rows.append({'category': '진입후 청산(보유 리스크)', 'h': h, 'reason': r, 'n': c})
        rows.append({'category': '기간말 캘린더 잘림', 'h': h, 'reason': 't+1+h > 마지막 거래일', 'n': truncated})
    return pd.DataFrame(rows)


def audit_events(events, gcal, gpos, symbol_names, db_cache):
    """큰 반등 이벤트 감사: DB 원시 OHLCV로 가격경로·거래량·상한가 근접·시가갭 확인."""
    rows = []
    for idx, row in events.iterrows():
        code = str(row['code']).zfill(6)
        t = row['date']
        p = gpos[t]
        entry_day = gcal[p + 1]
        db = db_cache.get(code)
        rec = {'code': code, 'name': symbol_names.get(row['code'], ''), 'date': t,
               'entry_day': entry_day, 'r3': round(row['r3'], 2)}
        if db is None:
            rec.update({'verdict': 'DB조회불가'})
            rows.append(rec)
            continue
        def _row(d):
            return db.loc[d] if d in db.index else None
        rt = _row(t)
        re_ = _row(entry_day)
        prev_close = float(rt['close']) if rt is not None else np.nan
        if re_ is not None:
            eo = float(re_['open']); eh = float(re_['high']); el = float(re_['low'])
            ec = float(re_['close']); ev = float(re_['volume'])
        else:
            eo = eh = el = ec = ev = np.nan
        rec.update({'prev_close': prev_close, 'entry_open': eo, 'entry_high': eh,
                    'entry_low': el, 'entry_close': ec, 'entry_volume': int(ev) if not np.isnan(ev) else np.nan,
                    'entry_vol_krw': eo * ev if not (np.isnan(eo) or np.isnan(ev)) else np.nan})
        rec['gap_pct'] = round((eo / prev_close - 1) * 100, 2) if (prev_close and eo) and prev_close > 0 else np.nan
        rec['day_ret_pct'] = round((ec / prev_close - 1) * 100, 2) if (prev_close and ec) and prev_close > 0 else np.nan
        rec['near_limit_open'] = bool(prev_close > 0 and eo >= prev_close * LIMIT_NEAR_PCT)
        rec['open_eq_high'] = bool(eh > 0 and eo >= eh * 0.995)
        rec['open_eq_low'] = bool(el > 0 and eo <= el * 1.005)
        # 가격경로: 신호일 종가 / 진입일 종가 / 청산일 종가
        rc3 = _row(gcal[p + 1 + PRIMARY_H])
        rec['close_exit'] = float(rc3['close']) if rc3 is not None else np.nan
        if rec['near_limit_open'] or rec['open_eq_high']:
            rec['verdict'] = '체결의심'
        else:
            rec['verdict'] = '정상'
        rows.append(rec)
    return pd.DataFrame(rows)


def _fetch_db(code, cache):
    import re
    code = str(code).zfill(6)
    if code in cache:
        return cache[code]
    if not re.fullmatch(r'\d{6}', code):
        cache[code] = None
        return None
    try:
        cur = execute_sql('backtest_data',
                          f'SELECT `Date`, open, high, low, close, volume FROM `{code}`')
        cols = [d[0] for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=cols)
        df['Date'] = df['Date'].astype(str)
        cache[code] = df.set_index('Date').astype('float64')
    except Exception:
        cache[code] = None
    return cache[code]


def stress_eval(ev3, ctrl_by_date, gcal, block_len, boot_n):
    """포착률 스트레스: A(상한가 근접 개장 제외), B(거래대금 유동성 상한), A+B,
    + 태스크3 감사 민감도(체결의심 = 상한가 근접 OR 시가=고가) 제외."""
    sub = ev3.dropna(subset=['r3']).copy()
    turnover = sub['entry_open'] * sub['entry_vol']
    near_limit = sub['entry_open'] >= sub['close'] * LIMIT_NEAR_PCT
    open_at_high = sub['entry_open'] >= sub['entry_high'] * 0.995
    audit_suspect = near_limit | open_at_high
    masks = {
        'base': pd.Series(True, index=sub.index),
        'A_상한가근접제외': ~near_limit,
        'B_유동성상한': turnover >= ASSUMED_POSITION / POSITION_TURNOVER_CAP,
        'A+B': (~near_limit) & (turnover >= ASSUMED_POSITION / POSITION_TURNOVER_CAP),
        '감사체결의심제외(태스크3)': ~audit_suspect,
    }
    rows = []
    for name, m in masks.items():
        s = sub.loc[m]
        n = len(s)
        row = {'stress': name, 'n_events': n, 'n_excluded': len(sub) - n}
        if n == 0:
            row.update({'mean': np.nan, 'ac_mean': np.nan, 'excess': np.nan,
                        'matched_diff': np.nan, 'diff_95ci_lo': np.nan, 'diff_95ci_hi': np.nan})
            rows.append(row)
            continue
        row['mean'] = round(s['r3'].mean(), 4)
        row['ac_mean'] = round(s['ac3'].mean(), 4)
        row['excess'] = round(s['ex3'].dropna().mean(), 4) if 'ex3' in s else np.nan
        mdf, _ = matched_same_sample(s, ctrl_by_date, PRIMARY_H)
        if len(mdf):
            row['matched_diff'] = round(mdf['diff'].mean(), 4)
            lo, hi = _ci(block_bootstrap_calendar(mdf, 'diff', gcal, block_len, boot_n))
            row['diff_95ci_lo'], row['diff_95ci_hi'] = round(lo, 4), round(hi, 4)
        else:
            row['matched_diff'] = np.nan
            row['diff_95ci_lo'] = row['diff_95ci_hi'] = np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def load_stage25_main():
    cands = sorted((project_root / 'backtest/reports').glob('*_rsi_event_study_stage25/stage25_main_*.csv'))
    return pd.read_csv(cands[-1]) if cands else None


def render_md(main, missing, matched, stress, audit_sum, audit_top, comp, args,
              n_on, n_uni_excl, verdict, elapsed):
    lines = []
    a = lines.append
    a('# 복합신호 C 데이터·체결 아티팩트 검증 (3단계, 제한)')
    a('')
    a(f'**실행:** {time.strftime("%Y-%m-%d %H:%M:%S")} (KST) | **DB:** {args.db_name} | '
      f'**기간:** {args.start} ~ {args.end} | **bootstrap:** {args.bootstrap_n}회, '
      f'**블록:** {args.block_len}거래일 | **신호 C·h={PRIMARY_H} 고정**')
    a('')
    a('## 보정 요약 (2.5단계 대비)')
    a('')
    a('- 부트스트랩: **전체 시장 캘린더**(신호 없는 거래일 포함) 기반 moving-block(블록 5) 95% CI.')
    a('- 매칭 비교: 매칭된 동일 표본의 신호평균/대조평균/차이로 통일 표시.')
    a('- **절대순수익**(신호평균 - 왕복 0.63%) vs **상대예측력**(매칭 차이) 분리. '
      '매칭 차이에 왕복비용 통째 차감 없음(양쪽 체결 비용 동일 → 상쇄).')
    a('- 누락 거래: 진입 확정불가(미체결 가정) / 진입후 청산 확정불가(보유 리스크) 사유 분해.')
    a('- 큰 반등 감사: DB 원시 OHLCV 읽기전용 조회. 수정주가(FDR Naver 소스) 한계 명시.')
    a('')
    a(f'엄격 유니버스 ON 행: **{n_on:,}** | 유니버스(전월 부재) 제외 C 이벤트: **{n_uni_excl:,}**')
    a('')
    a('## ① 보정 통계 (전체 캘린더 블록 bootstrap, 95% CI)')
    a('')
    a('| h | 이벤트 | 평균(%) | 절대순수익(%) | 원수익 95% CI | 비용후 95% CI | 초과(%) | 초과 95% CI |')
    a('|---|-------:|--------:|--------------:|--------------|----------------|--------:|-------------|')
    for _, r in main.iterrows():
        mark = '**' if r['h'] == PRIMARY_H else ''
        a(f'| {mark}{int(r["h"])}{mark} | {int(r["n_events"]):,} | {_fmt(r["mean"])} | {_fmt(r["ac_mean"])} | '
          f'[{_fmt(r["raw_95ci_lo"])}, {_fmt(r["raw_95ci_hi"])}] | '
          f'[{_fmt(r["ac_95ci_lo"])}, {_fmt(r["ac_95ci_hi"])}] | {_fmt(r["excess"])} | '
          f'[{_fmt(r["exc_95ci_lo"])}, {_fmt(r["exc_95ci_hi"])}] |')
    a('')
    a('> 절대순수익 = 신호 평균 - 0.63%p (비용은 실제 체결되는 신호 측에만 차감). '
      '초과 = 표준 대조군(같은 날짜·유니버스 내 비-C) 대비 상대예측력.')
    a('')
    a('## ② 매칭 동일 표본 (같은 날짜 & drop2 ±1%p, RSI>=3) — h=3')
    a('')
    a('| 버전 | 매칭됨 | 무매칭 | 신호평균(%) | 대조평균(%) | 차이(%) | 차이 95% CI | 절대순수익(%) |')
    a('|------|-------:|------:|------------:|------------:|--------:|-------------|--------------:|')
    for _, r in matched.iterrows():
        a(f'| {r["version"]} | {r["n_matched"]:,} | {r["n_no_match"]:,} | {_fmt(r["signal_mean"])} | '
          f'{_fmt(r["control_mean"])} | {_fmt(r["diff_mean"])} | '
          f'[{_fmt(r["diff_95ci_lo"])}, {_fmt(r["diff_95ci_hi"])}] | {_fmt(r["ac_signal_mean"])} |')
    a('')
    a('> 차이 = 상대예측력(매칭 대조). 절대순수익 = 신호평균 - 0.63%p (별도 표시, 차이에서 '
      '비용 통째 차감하지 않음).')
    a('')
    a('## ③ 누락 거래 분해')
    a('')
    a('| 구분 | h | 사유 | 건수 |')
    a('|------|---|------|-----:|')
    for _, r in missing.iterrows():
        a(f'| {r["category"]} | {r["h"]} | {r["reason"]} | {r["n"]:,} |')
    a('')
    a('> 사유 판별: availability_map + 월별 스냅샷 + DB 행 존재. 판별불가는 不明.')
    a('')
    a('## ④ 큰 반등 감사 (DB 원시 OHLCV)')
    a('')
    a('| 그룹 | 이벤트 | 정상 | 체결의심 | 의심 비율(%) | 의심 제외 평균(%) | 의심 제외 초과(%) |')
    a('|------|-------:|-----:|--------:|-------------:|------------------:|------------------:|')
    for _, r in audit_sum.iterrows():
        a(f'| {r["group"]} | {r["n"]:,} | {r["n_ok"]:,} | {r["n_suspect"]:,} | {_fmt(r["suspect_pct"], 1)} | '
          f'{_fmt(r["mean_ex_suspect"])} | {_fmt(r["exc_ex_suspect"])} |')
    a('')
    a('| 종목 | 신호일 | 진입일 | 전일종가 | 진입시가 | 진입고가 | 갭(%) | 당일등락(%) | 상한가근접 | 시가=고가 | r3(%) | 판정 |')
    a('|------|--------|--------|---------:|---------:|---------:|------:|------------:|:----------:|:---------:|------:|------|')
    for _, r in audit_top.iterrows():
        a(f'| {r["code"]} {r["name"]} | {r["date"]} | {r["entry_day"]} | {_fmt(r["prev_close"])} | '
          f'{_fmt(r["entry_open"])} | {_fmt(r["entry_high"])} | {_fmt(r["gap_pct"])} | '
          f'{_fmt(r["day_ret_pct"])} | {"Y" if r["near_limit_open"] else "N"} | '
          f'{"Y" if r["open_eq_high"] else "N"} | {_fmt(r["r3"])} | {r["verdict"]} |')
    a('')
    a('> 상한가근접 = 진입시가 ≥ 전일종가×1.295. 시가=고가 = 시가가 고가의 99.5% 이상. '
      '체결의심 = 상한가근접 개장 또는 시가=고가(개장가 체결 곤란).')
    a('- 수정주가 주의: 데이터는 FDR(Naver) 수정주가 기반. 일부 종목(예: 450140 말미)에 '
      'open/high/low/volume=0·close만 존재하는 조정 아티팩트 행 확인 — 해당 행은 시가 부재로 '
      '분석에서 자동 제외됨(영향 없음).')
    a('')
    a('## ⑤ 포착률 스트레스 (h=3, 현재 신호 고정)')
    a('')
    a('| 가정 | 이벤트 | 제외 | 평균(%) | 절대순수익(%) | 초과(%) | 매칭차이(%) | 차이 95% CI |')
    a('|------|-------:|-----:|--------:|--------------:|--------:|------------:|-------------|')
    for _, r in stress.iterrows():
        a(f'| {r["stress"]} | {r["n_events"]:,} | {r["n_excluded"]:,} | {_fmt(r["mean"])} | '
          f'{_fmt(r["ac_mean"])} | {_fmt(r["excess"])} | {_fmt(r["matched_diff"])} | '
          f'[{_fmt(r["diff_95ci_lo"])}, {_fmt(r["diff_95ci_hi"])}] |')
    a('')
    a('> A = 진입시가 ≥ 전일종가×1.295(상한가 근접 개장) 제외. '
      f'B = 가정 포지션 {ASSUMED_POSITION/1e6:.0f}백만원이 당일 거래대금의 '
      f'{POSITION_TURNOVER_CAP*100:.0f}% 초과 시 제외. 탐색 없이 사전 고정. '
      '감사체결의심제외(태스크3) = 상한가 근접 OR 시가=고가(개장가 체결 곤란) 제외 민감도.')
    a('')
    a('## ⑥ 2.5단계와의 변경점 (h=3)')
    a('')
    if comp is not None:
        a('| 항목 | 2.5단계(이벤트날짜 MBB) | 3단계(전체 캘린더 MBB) |')
        a('|------|-------------------------|------------------------|')
        a(f'| 이벤트 | {comp["n25"]:,} | {comp["n3"]:,} |')
        a(f'| 평균(%) | {_fmt(comp["mean25"])} | {_fmt(comp["mean3"])} |')
        a(f'| 절대순수익(%) | {_fmt(comp["ac25"])} | {_fmt(comp["ac3"])} |')
        a(f'| 비용후 95% CI | [{_fmt(comp["ac25_lo"])}, {_fmt(comp["ac25_hi"])}] | '
          f'[{_fmt(comp["ac3_lo"])}, {_fmt(comp["ac3_hi"])}] |')
        a(f'| 초과(%) | {_fmt(comp["exc25"])} | {_fmt(comp["exc3"])} |')
    a('')
    a(f'## 판정: **{verdict}**')
    a('')
    a('## 재현 명령')
    a('')
    a('```bash')
    a(f'.venv/bin/python backtest/research_rsi_event_study_stage3_limited.py --quick')
    a(f'.venv/bin/python backtest/research_rsi_event_study_stage3_limited.py --db-name backtest_data '
      f'--start 20160101 --end 20260630 --bootstrap-n 1000 --block-len 5')
    a('```')
    a('')
    a('## 재현 주의점')
    a('')
    a('- 전체 시장 캘린더 MBB: 신호 없는 거래일도 블록 구성에 포함 (2.5단계의 이벤트날짜-only '
      'MBB와 다름 — CI 폭 변화에 주의).')
    a('- 매칭 차이는 상대예측력으로 비용 미차감; 절대순수익(신호평균-비용)은 별도 표시.')
    a('- 감사는 DB 원시 OHLCV 읽기전용. 수정주가(FDR/Naver)로 인한 제한적 정확도.')
    a('- 포착률 가정 2가지는 사전 고정, 최적화 없음.')
    a(f'- 실행 시간: {elapsed:.1f}s.')
    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser(description='3단계(제한) — C 비용후 마진 아티팩트 검증')
    ap.add_argument('--db-name', default='backtest_data')
    ap.add_argument('--start', default='20160101')
    ap.add_argument('--end', default='20260630')
    ap.add_argument('--quick', action='store_true', help='스모크: 2024년 1년 + bootstrap 100')
    ap.add_argument('--bootstrap-n', type=int, default=1000)
    ap.add_argument('--block-len', type=int, default=5)
    args = ap.parse_args()

    start, end, boot_n, block_len = args.start, args.end, args.bootstrap_n, args.block_len
    if args.quick:
        start, end, boot_n = '20240101', '20241231', min(boot_n, 100)
        print(f'[quick] start={start} end={end} bootstrap_n={boot_n} block_len={block_len}')

    t0 = time.time()
    price_data, availability_map, monthly_universe_map, symbol_names, (s0, e0) = load_all(args.db_name)
    print(f'로드 완료: {len(price_data)}종목')

    gcal, gpos = _build_global_calendar(price_data)
    fb_months = _build_code_eligible_months(monthly_universe_map)
    st_months = _build_code_eligible_months_strict(monthly_universe_map)
    st_pairs = {(c, m) for c, ms in st_months.items() for m in ms}

    stock_dates = {}
    frames = []
    for i, (code, df) in enumerate(price_data.items()):
        stock_dates[code] = df.index.to_numpy()
        res = _compute_stock_stage3(code, df, gcal, gpos, fb_months, availability_map, start, end)
        if len(res):
            frames.append(res)
        if (i + 1) % 1000 == 0:
            print(f'  처리 {i + 1}/{len(price_data)}')
    pool = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if pool.empty:
        print('적격 행 없음 — 종료')
        return
    pool['month'] = pool['date'].str[:6]
    pool['strict'] = list(map(lambda t: (t[0], t[1]) in st_pairs, zip(pool['code'], pool['month'])))
    print(f'pool: {len(pool):,} (strict {int(pool["strict"].sum()):,})')

    valid = pool['rsi2'].notna() & pool['ma200'].notna() & pool['drop2'].notna()
    on = pool.loc[pool['strict'] & valid & (pool['close'] > pool['ma200'])].copy()
    sigC = (on['rsi2'] < 3) & (on['drop2'] < -5)
    n_on = len(on)

    uni_excl = pool.loc[~pool['strict'] & valid & (pool['close'] > pool['ma200'])]
    uni_excl = uni_excl[(uni_excl['rsi2'] < 3) & (uni_excl['drop2'] < -5)]
    n_uni_excl = len(uni_excl)
    print(f'ON(엄격) {n_on:,} | C 전체 {int(sigC.sum()):,} | 유니버스제외 {n_uni_excl:,}')

    # ① 보정 통계
    ctrl = on.loc[~sigC].groupby('date')[[f'r{h}' for h in HORIZONS]].mean()
    events_by_h = {}
    for h in HORIZONS:
        ok = ~on['entry_missing'] & ~on[f'exit_missing_{h}']
        ev = on.loc[sigC & ok].copy()
        ev[f'ex{h}'] = ev[f'r{h}'] - ev['date'].map(ctrl[f'r{h}'])
        ev[f'ac{h}'] = ev[f'r{h}'] - COST
        events_by_h[h] = ev
        print(f'  h={h}: {len(ev):,}')
    main_rows = [_summarize_corrected(events_by_h[h], h, gcal, block_len, boot_n, control_map=ctrl)
                 for h in HORIZONS]
    main = pd.DataFrame(main_rows)

    # ② 누락 거래 분해
    missing = collect_missing(on, sigC, gcal, gpos, stock_dates, availability_map, monthly_universe_map)

    # ③ 매칭 동일 표본 (h=3)
    ev3 = events_by_h[PRIMARY_H]
    ctrl_by_date = {d: g.dropna(subset=[f'r{PRIMARY_H}'])
                    for d, g in on.loc[on['rsi2'] >= 3].groupby('date')}
    first_mask, dedup_mask = _first_and_dedup5(ev3, gpos)
    versions = {'ALL': ev3, 'FIRST': ev3.loc[first_mask], 'DEDUP5': ev3.loc[dedup_mask]}
    matched_rows = []
    for vname, evv in versions.items():
        mdf, n_nm = matched_same_sample(evv, ctrl_by_date, PRIMARY_H)
        row = _matched_stats_same(mdf, n_nm, gcal, block_len, boot_n)
        row['version'] = vname
        matched_rows.append(row)
        print(f'  매칭 {vname}: {row["n_matched"]:,} / 무매칭 {row["n_no_match"]:,} / 차이 {row["diff_mean"]}%')
    matched = pd.DataFrame(matched_rows)

    # ④ 큰 반등 감사 (DB 원시 OHLCV)
    sub3 = ev3.dropna(subset=['r3'])
    top1 = sub3.nlargest(int(np.ceil(len(sub3) * 0.01)), 'r3')
    top5 = sub3.nlargest(int(np.ceil(len(sub3) * 0.05)), 'r3')
    g_stock = sub3.groupby('code').agg(n=('r3', 'size'), sum_exc=('ex3', 'sum'))
    top5_stocks = set(g_stock.nlargest(5, 'sum_exc').index)
    top5_stock_evs = sub3[sub3['code'].isin(top5_stocks)]
    named_evs = sub3[sub3['code'].isin({'450140', '066430', '136510'})]
    audited = pd.concat([top1, top5, top5_stock_evs, named_evs]).drop_duplicates(subset=['code', 'date'])
    db_cache = {}
    for c in set(audited['code']):
        _fetch_db(c, db_cache)
    audit_df = audit_events(audited, gcal, gpos, symbol_names, db_cache)
    # 그룹별 감사 요약
    audit_sum_rows = []
    for gname, evs in [('top1%', top1), ('top5%', top5), ('top5기여종목', top5_stock_evs),
                       ('명시종목(450140/066430/136510)', named_evs)]:
        ad = audit_df[audit_df.set_index(['code', 'date']).index.isin(
            evs.set_index(['code', 'date']).index)]
        n = len(ad)
        n_sus = int((ad['verdict'] == '체결의심').sum())
        ok = ad[ad['verdict'] != '체결의심']
        audit_sum_rows.append({
            'group': gname, 'n': n, 'n_ok': n - n_sus, 'n_suspect': n_sus,
            'suspect_pct': round(n_sus / n * 100, 1) if n else np.nan,
            'mean_ex_suspect': round(ok['r3'].mean(), 4) if len(ok) else np.nan,
            'exc_ex_suspect': round((ok['r3'] - ok['date'].map(ctrl[f'r3'])).mean(), 4)
            if len(ok) else np.nan,
        })
    audit_sum = pd.DataFrame(audit_sum_rows)
    audit_top = audit_df.sort_values('r3', ascending=False).head(15).copy()
    print(f'  감사: {len(audit_df)}건 (체결의심 {int((audit_df["verdict"]=="체결의심").sum()):,})')

    # ⑤ 포착률 스트레스 (h=3)
    stress = stress_eval(ev3, ctrl_by_date, gcal, block_len, boot_n)

    # ⑥ 2.5단계 비교
    s25 = load_stage25_main()
    comp = None
    if s25 is not None:
        r25 = s25[s25.h == PRIMARY_H].iloc[0]
        r3 = main[main.h == PRIMARY_H].iloc[0]
        comp = {'n25': int(r25['n_events']), 'n3': int(r3['n_events']),
                'mean25': r25['mean'], 'mean3': r3['mean'],
                'ac25': r25['ac_mean'], 'ac3': r3['ac_mean'],
                'ac25_lo': r25['ac_95ci_lo'], 'ac25_hi': r25['ac_95ci_hi'],
                'ac3_lo': r3['ac_95ci_lo'], 'ac3_hi': r3['ac_95ci_hi'],
                'exc25': r25['excess'], 'exc3': r3['excess']}

    # 판정
    r3row = main[main.h == PRIMARY_H].iloc[0]
    st_base = stress[stress.stress == 'base'].iloc[0]
    st_ab = stress[stress.stress == 'A+B'].iloc[0]
    m_all = matched[matched.version == 'ALL'].iloc[0]
    ac_lo = r3row['ac_95ci_lo']
    reasons = []
    if not np.isnan(ac_lo) and r3row['ac_mean'] > 0 and ac_lo > 0:
        reasons.append('비용후 95% CI가 0을 제외')
    else:
        reasons.append('비용후 95% CI가 0을 포함')
    if m_all['n_matched'] and not np.isnan(m_all['diff_mean']) and m_all['diff_mean'] > 0 \
            and m_all['diff_95ci_lo'] > 0:
        reasons.append('매칭 대조 대비 추가기여가 95% 유의')
    else:
        reasons.append('매칭 대조 추가기여 95% 불확실')
    if st_ab['n_excluded'] > 0 and not np.isnan(st_ab['ac_mean']) and st_ab['ac_mean'] <= 0:
        reasons.append('A+B 스트레스에서 비용후 0 이하')
    verdict = '유지' if ('비용후 95% CI가 0을 제외' in reasons
                         and '매칭 대조 대비 추가기여가 95% 유의' in reasons) else \
              ('종료' if (st_ab['n_excluded'] > 0 and not np.isnan(st_ab['ac_mean'])
                          and st_ab['ac_mean'] <= 0 and r3row['ac_mean'] > 0) else '보류')
    verdict += ' — ' + '; '.join(reasons)
    print(f'\n판정: {verdict}')

    elapsed = time.time() - t0

    # 저장
    stamp = time.strftime('%Y%m%d_%H%M%S')
    report_dir = project_root / 'backtest' / 'reports' / f'{time.strftime("%Y%m%d")}_rsi_event_study_stage3_limited'
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

    save('stage3_main', main)
    save('stage3_missing', missing)
    save('stage3_matched', matched)
    save('stage3_audit_summary', audit_sum)
    save('stage3_audit_top', audit_top)
    save('stage3_stress', stress)
    if comp is not None:
        save('stage3_compare_stage25', pd.DataFrame([comp]))
    md = render_md(main, missing, matched, stress, audit_sum, audit_top, comp, args,
                   n_on, n_uni_excl, verdict, elapsed)
    save('stage3_report', md)

    # stdout 요약
    print('\n=== 3단계(제한) 보정 결과 (h=3) ===')
    print(f'이벤트 {int(r3row["n_events"]):,} | 평균 {_fmt(r3row["mean"])}% '
          f'| 절대순수익 {_fmt(r3row["ac_mean"])}% | 비용후 95% CI [{_fmt(r3row["ac_95ci_lo"])}, {_fmt(r3row["ac_95ci_hi"])}]')
    print(f'매칭 동일표본: 신호 {_fmt(m_all["signal_mean"])}% vs 대조 {_fmt(m_all["control_mean"])}% '
          f'| 차이 {_fmt(m_all["diff_mean"])}% [{_fmt(m_all["diff_95ci_lo"])}, {_fmt(m_all["diff_95ci_hi"])}]')
    print(f'스트레스: A+B 제외 {int(st_ab["n_excluded"]):,}건 → 절대순수익 {_fmt(st_ab["ac_mean"])}% '
          f'| 매칭차이 {_fmt(st_ab["matched_diff"])}%')
    print(f'감사: 체결의심 {int((audit_df["verdict"]=="체결의심").sum()):,}/{len(audit_df)}건')


if __name__ == '__main__':
    main()