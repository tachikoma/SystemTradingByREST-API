#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
2.5단계 보수적 재검증 — 복합신호 C (RSI(2)<3 & 2일하락률<-5% & close>MA200, h=3 주판단)

1/2단계의 C 비용후 +0.29%(h=3) 결과가 보수적 처리에서도 살아남는지 재검증한다.

보수적 수정 (1/2단계와의 차이):
1. 유니버스: prev_month 스냅샷 **엄격 적용** — 전월 스냅샷 부재 월(201601 등)은
   당월 fallback 대신 해당 월 이벤트 제외 (제외 건수 보고).
2. 거래일: 종목별 shift가 아닌 **공통 시장 거래일 캘린더** 기준 t+1/t+1+h.
   거래정지·상장폐지·가격누락으로 진입/청산가 확정 불가 이벤트는 수익률에서 제외하고
   별도 집계 (last_close 대체 없음, 시가만 사용).
3. 신뢰구간: 단일날짜 resample이 아닌 **연속 날짜 블록 부트스트랩**(블록길이 5거래일,
   bootstrap 1000회). 원수익·비용후수익(왕복 0.63% 고정 차감) CI 각각 직접 출력.
   2.5/97.5 분위수는 **95% CI**로 명시 (90% 표기 금지).
4. 매칭 대조: 같은 날짜 & 유사 급락강도(drop2 ±1%p 밴드) 내 비신호(RSI>=3) 대조군
   대비 C의 추가기여 재측정. 반복신호 포함 전체 vs 종목당 최초신호만(5일 중복 제거) 버전.
5. 집중도: 상위 수익 이벤트 제외(상위 1%/5%), 연도 제외(leave-one-year-out),
   종목 제외 상위기여자 확인.

Usage:
    .venv/bin/python backtest/research_rsi_event_study_stage25.py --quick
    .venv/bin/python backtest/research_rsi_event_study_stage25.py --bootstrap-n 1000 --block-len 5
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
from backtest.research_rsi_event_study import (
    HORIZONS, PRIMARY_H, DEFAULT_SLIPPAGE, SLIPPAGE_LEVELS,
    _prev_yyyymm, _next_yyyymm, _total_cost_pct, _build_code_eligible_months,
    _ci, _fmt,
)
from util.rsi_calc import compute_rsi

COST = _total_cost_pct(DEFAULT_SLIPPAGE)  # 왕복 0.63% (고정 차감)


def _build_code_eligible_months_strict(monthly_universe_map):
    """엄격 prev_month: 전월 스냅샷 부재 월은 유니버스 없음 (fallback 없음).

    {code: {적격월...}}. 201601은 전월(201512) 부재 → 유니버스 미부여.
    """
    snap_months = set(monthly_universe_map.keys())
    code_months = defaultdict(set)
    for snap, codes in monthly_universe_map.items():
        m = _next_yyyymm(snap)
        if m in snap_months:  # prev_month(m) == snap 이 존재하는 거래월만
            for c in codes:
                code_months[c].add(m)
    return code_months


def _build_global_calendar(price_data):
    """전 종목 거래일 합집합 = 공통 시장 거래일 캘린더."""
    all_dates = set()
    for df in price_data.values():
        all_dates.update(df.index)
    gcal = sorted(all_dates)
    gpos = {d: i for i, d in enumerate(gcal)}
    return gcal, gpos


def _compute_stock_strict(code, df, gcal, gpos, code_eligible_months, availability_map, start, end):
    """단일 종목: 지표(자체 거래일) + 진입/청산(공통 시장 캘린더, 시가만, 대체 없음).

    반환: fallback 유니버스 적격 월 + 기간 내 행 전체. strict 여부는 후처리.
    """
    df = df.sort_index()
    close = df['close'].astype('float64')
    rsi2 = compute_rsi(close, period=2, min_periods=2, method='wilder')
    ma200 = close.rolling(window=200, min_periods=200).mean()
    close2 = close.shift(2)
    drop2 = ((close - close2) / close2 * 100.0).replace([np.inf, -np.inf], np.nan)

    # 공통 시장 캘린더 기준 진입/청산 (시가만, 결측·비정상가(≤0) 시 대체 없음)
    dfg = df.reindex(gcal)
    g_open = dfg['open'].astype('float64')
    g_open = g_open.where(g_open > 0)  # 0/음수 가격 → 결측 처리 (inf 방지)
    entry_open = g_open.shift(-1)
    entry_missing = entry_open.isna()

    out = {'date': df.index, 'code': code, 'close': close,
           'rsi2': rsi2, 'ma200': ma200, 'drop2': drop2,
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


def block_bootstrap(events, col, n_iter, block_len, seed=42):
    """연속 날짜 블록 부트스트랩 (moving block, 블록길이 block_len 거래일).

    날짜 순열을 block_len 간격의 연속 블록으로 복원추출해 count-weighted 평균 분포 생성.
    """
    if events is None or len(events) == 0:
        return np.full(n_iter, np.nan)
    g = events.groupby('date')[col].agg(['sum', 'count']).dropna(subset=['sum'])
    if g.empty:
        return np.full(n_iter, np.nan)
    sums = g['sum'].to_numpy()
    counts = g['count'].to_numpy().astype('float64')
    n = len(g)
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


def _summarize_h(ev, h, block_len, boot_n, control_map=None):
    rc = f'r{h}'
    ac = f'ac{h}'
    sub = ev.dropna(subset=[rc])
    n = len(sub)
    row = {'h': h, 'n_events': n}
    if n == 0:
        row.update({'n_stocks': 0, 'n_dates': 0, 'mean': np.nan, 'median': np.nan,
                    'win_rate': np.nan, 'control_mean': np.nan, 'excess': np.nan,
                    'raw_95ci_lo': np.nan, 'raw_95ci_hi': np.nan,
                    'ac_mean': np.nan, 'ac_95ci_lo': np.nan, 'ac_95ci_hi': np.nan,
                    'exc_95ci_lo': np.nan, 'exc_95ci_hi': np.nan})
        return row
    row['n_stocks'] = sub['code'].nunique()
    row['n_dates'] = sub['date'].nunique()
    row['mean'] = round(sub[rc].mean(), 4)
    row['median'] = round(sub[rc].median(), 4)
    row['win_rate'] = round((sub[rc] > 0).mean() * 100, 2)
    row['ac_mean'] = round(sub[ac].mean(), 4)
    lo, hi = _ci(block_bootstrap(sub, rc, boot_n, block_len))
    row['raw_95ci_lo'], row['raw_95ci_hi'] = round(lo, 4), round(hi, 4)
    alo, ahi = _ci(block_bootstrap(sub, ac, boot_n, block_len))
    row['ac_95ci_lo'], row['ac_95ci_hi'] = round(alo, 4), round(ahi, 4)
    exc_col = f'ex{h}'
    if control_map is not None and exc_col in sub.columns:
        se = sub.dropna(subset=[exc_col])
        row['control_mean'] = round(sub[rc].mean() - se[exc_col].mean(), 4)
        row['excess'] = round(se[exc_col].mean(), 4)
        lo, hi = _ci(block_bootstrap(se, exc_col, boot_n, block_len))
        row['exc_95ci_lo'], row['exc_95ci_hi'] = round(lo, 4), round(hi, 4)
    else:
        row['control_mean'] = row['excess'] = np.nan
        row['exc_95ci_lo'] = row['exc_95ci_hi'] = np.nan
    return row


def _first_and_dedup5(ev, gpos):
    """종목당 최초신호(FIRST) / 5거래일 중복 제거(DEDUP5) 마스크."""
    evs = ev.sort_values(['code', 'date'])
    first_mask = pd.Series(False, index=evs.index)
    dedup_mask = pd.Series(False, index=evs.index)
    seen = set()
    last_pos = {}
    for idx, code, date in zip(evs.index, evs['code'], evs['date']):
        p = gpos[date]
        if code not in seen:
            seen.add(code)
            first_mask.loc[idx] = True
        if code not in last_pos or p - last_pos[code] >= 5:
            dedup_mask.loc[idx] = True
            last_pos[code] = p
    return first_mask, dedup_mask


def matched_excess(events, ctrl_by_date, h):
    """같은 날짜 & drop2 ±1%p 밴드 내 비신호(RSI>=3) 대조군 대비 추가기여.

    Returns: (matched_excess Series, n_no_match)
    """
    rc = f'r{h}'
    exc = pd.Series(np.nan, index=events.index)
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
                exc.loc[idx] = evd.loc[idx, rc] - cr[m].mean()
            else:
                n_no_match += 1
    return exc, n_no_match


def _matched_stats(events, matched_exc, n_no_match, block_len, boot_n):
    sub = events.dropna(subset=[f'r{PRIMARY_H}'])
    n_total = len(sub)
    m = matched_exc.dropna()
    row = {'n_events': n_total, 'n_matched': len(m), 'n_no_match': n_no_match,
           'control_mean': round(sub[f'r{PRIMARY_H}'].mean() - m.mean(), 4)
           if len(m) else np.nan,
           'matched_excess': round(m.mean(), 4) if len(m) else np.nan}
    if len(m):
        lo, hi = _ci(block_bootstrap(m.to_frame('v').assign(date=sub.loc[m.index, 'date'].to_numpy()),
                                     'v', boot_n, block_len))
        row['me_95ci_lo'], row['me_95ci_hi'] = round(lo, 4), round(hi, 4)
        ac = m - COST
        alo, ahi = _ci(block_bootstrap(pd.DataFrame({'v': ac.to_numpy(), 'date': sub.loc[m.index, 'date'].to_numpy()}),
                                       'v', boot_n, block_len))
        row['ac_me_95ci_lo'], row['ac_me_95ci_hi'] = round(alo, 4), round(ahi, 4)
        row['ac_matched_excess'] = round(ac.mean(), 4)
    else:
        row.update({'me_95ci_lo': np.nan, 'me_95ci_hi': np.nan,
                    'ac_me_95ci_lo': np.nan, 'ac_me_95ci_hi': np.nan,
                    'ac_matched_excess': np.nan})
    return row


def load_stage_summary(dir_suffix, file_prefix, signal='C'):
    """1/2단계 summary 중 해당 신호 이벤트 수가 최대인(전체 실행) 파일을 찾는다."""
    cands = sorted((project_root / 'backtest/reports').glob(f'*{dir_suffix}/{file_prefix}*.csv'))
    best = None
    best_n = -1
    for p in cands:
        df = pd.read_csv(p)
        s = df[df.signal == signal]
        if s.empty:
            continue
        n = int(s[s.h == PRIMARY_H]['n_events'].iloc[0])
        if n > best_n:
            best_n = n
            best = df
    return best


def render_md(main, excl, matched, repeats, conc, yearly, top_stocks, comp,
              args, n_on, n_uni_excl, verdict, elapsed):
    lines = []
    a = lines.append
    a('# 복합신호 C 보수적 재검증 (2.5단계)')
    a('')
    a(f'**실행:** {time.strftime("%Y-%m-%d %H:%M:%S")} (KST) | **DB:** {args.db_name} | '
      f'**기간:** {args.start} ~ {args.end} | **bootstrap:** {args.bootstrap_n}회, '
      f'**블록길이:** {args.block_len}거래일')
    a('')
    a('**신호 C:** RSI(2)<3 & 2일하락률<-5% & close>MA200. **주 판단기간 h=3.**')
    a('')
    a('## 보수적 수정 사항 (1/2단계 대비)')
    a('')
    a('- **유니버스 엄격 prev_month**: 전월 스냅샷 부재 월은 fallback 없이 이벤트 제외.')
    a('- **공통 시장 거래일 캘린더** 기준 t+1/t+1+h (종목별 shift 아님). 시가만 사용, '
      '진입/청산가 확정 불가 이벤트는 제외·별도 집계 (last_close 대체 금지).')
    a('- **연속 날짜 블록 부트스트랩**(블록 5거래일) 95% CI (2.5/97.5 분위수). '
      '원수익·비용후수익(왕복 0.63% 고정) CI 각각 직접 출력.')
    a('- **매칭 대조**: 같은 날짜 & drop2 ±1%p 밴드 내 비신호(RSI>=3) 대조군.')
    a('- **집중도**: 상위 1%/5% 제외, leave-one-year-out, 상위기여 종목 제외.')
    a('')
    a(f'엄격 유니버스 ON 행 수: **{n_on:,}** | 유니버스(전월 부재) 제외 C 이벤트: **{n_uni_excl:,}**')
    a('')
    a('## 제외 건수')
    a('')
    a('| 구분 | h | 제외 건수 |')
    a('|------|---|----------:|')
    for _, r in excl.iterrows():
        a(f'| {r["category"]} | {int(r["h"])} | {int(r["n"]):,} |')
    a('')
    a('## 보수적 C 핵심 결과 (신호 × 보유기간, 95% CI)')
    a('')
    a('| h | 이벤트 | 종목 | 날짜 | 평균(%) | 비용후(%) | 원수익 95% CI | 비용후 95% CI | 대조군(%) | 초과(%) | 초과 95% CI |')
    a('|---|-------:|-----:|-----:|--------:|----------:|--------------|----------------|----------:|--------:|-------------|')
    for _, r in main.iterrows():
        mark = '**' if r['h'] == PRIMARY_H else ''
        a(f'| {mark}{int(r["h"])}{mark} | {int(r["n_events"]):,} | {int(r["n_stocks"]):,} | {int(r["n_dates"]):,} | '
          f'{_fmt(r["mean"])} | {_fmt(r["ac_mean"])} | [{_fmt(r["raw_95ci_lo"])}, {_fmt(r["raw_95ci_hi"])}] | '
          f'[{_fmt(r["ac_95ci_lo"])}, {_fmt(r["ac_95ci_hi"])}] | {_fmt(r["control_mean"])} | '
          f'{_fmt(r["excess"])} | [{_fmt(r["exc_95ci_lo"])}, {_fmt(r["exc_95ci_hi"])}] |')
    a('')
    a('> 비용후 = 평균 - 0.63%p (수수료 0.015%×2 + 세금 0.20% + 슬리피지 편도 0.2%×2).')
    a('')
    a('## 매칭 대조 (같은 날짜 & drop2 ±1%p, RSI>=3) — h=3')
    a('')
    a('| 버전 | 이벤트 | 매칭됨 | 무매칭 | 대조군(%) | 추가기여(%) | 추가기여 95% CI | 비용후 추가기여(%) | 비용후 95% CI |')
    a('|------|-------:|-------:|------:|----------:|------------:|----------------|-------------------:|---------------|')
    for _, r in matched.iterrows():
        a(f'| {r["version"]} | {int(r["n_events"]):,} | {int(r["n_matched"]):,} | {int(r["n_no_match"]):,} | '
          f'{_fmt(r["control_mean"])} | {_fmt(r["matched_excess"])} | '
          f'[{_fmt(r["me_95ci_lo"])}, {_fmt(r["me_95ci_hi"])}] | {_fmt(r["ac_matched_excess"])} | '
          f'[{_fmt(r["ac_me_95ci_lo"])}, {_fmt(r["ac_me_95ci_hi"])}] |')
    a('')
    a('> 추가기여 = C 수익률 - (같은 날짜·유사 급락강도의 RSI>=3 종목 평균 수익률). '
      '비용후 추가기여 = 추가기여 - 0.63%p.')
    a('')
    a('## 반복신호 처리 (h=3, 표준 대조군 초과)')
    a('')
    a('| 버전 | 이벤트 | 평균(%) | 초과(%) | 초과 95% CI | 비용후(%) |')
    a('|------|-------:|--------:|--------:|-------------|----------:|')
    for _, r in repeats.iterrows():
        a(f'| {r["version"]} | {int(r["n_events"]):,} | {_fmt(r["mean"])} | {_fmt(r["excess"])} | '
          f'[{_fmt(r["exc_95ci_lo"])}, {_fmt(r["exc_95ci_hi"])}] | {_fmt(r["ac_mean"])} |')
    a('')
    a('## 집중도 (h=3)')
    a('')
    a('| 항목 | n | 평균(%) | 초과(%) |')
    a('|------|----:|--------:|--------:|')
    for _, r in conc.iterrows():
        a(f'| {r["item"]} | {int(r["n"]):,} | {_fmt(r["mean"])} | {_fmt(r["excess"])} |')
    a('')
    a('> leave-one-year-out 최소/최대는 제외 연도별 결과 중 min/max. 상위기여 종목 제외는 '
      '추가기여 합(ex3 sum) 기준 상위 종목 제외 후 재계산.')
    a('')
    a('## 연도 제외 상세 (leave-one-year-out, h=3)')
    a('')
    a('| 제외 연도 | 제외 이벤트 | 남은 이벤트 | 평균(%) | 초과(%) |')
    a('|-----------|------------:|------------:|--------:|--------:|')
    for _, r in yearly.iterrows():
        a(f'| {r["year_removed"]} | {int(r["n_removed"]):,} | {int(r["n"]):,} | {_fmt(r["mean"])} | {_fmt(r["excess"])} |')
    a('')
    a('## 상위기여 종목 (h=3, ex3 합 기준)')
    a('')
    a('| 구분 | 종목 | 이벤트 | 평균(%) | 추가기여 합(%) |')
    a('|------|------|-------:|--------:|---------------:|')
    for _, r in top_stocks.iterrows():
        a(f'| {r["grp"]} | {r["code"]} {r.get("name", "")} | {int(r["n"])} | {_fmt(r["mean"])} | {_fmt(r["sum_exc"])} |')
    a('')
    a('## 1/2단계와의 비교 (신호 C, h=3)')
    a('')
    if comp is not None:
        a('| 단계 | 처리 | 이벤트 | 평균(%) | 초과(%) | 비용후(%) |')
        a('|------|------|-------:|--------:|--------:|----------:|')
        for _, r in comp.iterrows():
            a(f'| {r["stage"]} | {r["note"]} | {int(r["n_events"]):,} | {_fmt(r["mean"])} | '
              f'{_fmt(r["excess"])} | {_fmt(r["ac_mean"])} |')
    a('')
    a(f'## 판정 제안: **{verdict}**')
    a('')
    a('## 재현 명령')
    a('')
    a('```bash')
    a(f'.venv/bin/python backtest/research_rsi_event_study_stage25.py --quick')
    a(f'.venv/bin/python backtest/research_rsi_event_study_stage25.py --db-name backtest_data '
      f'--start 20160101 --end 20260630 --bootstrap-n 1000 --block-len 5')
    a('```')
    a('')
    a('## 재현 주의점')
    a('')
    a('- 엄격 prev_month: 전월 스냅샷 부재 월(201601)은 유니버스 미부여 → 이벤트 제외.')
    a('- 공통 시장 캘린더 기준이므로 거래정지 종목은 진입/청산 시점에 가격이 없으면 제외.')
    a('- 시가만 사용, 종가 fallback 및 last_close 대체 없음 (보수적).')
    a('- 블록 부트스트랩은 날짜 순서를 블록(5거래일) 단위로 복원추출 — 단일날짜 resample 대비 '
      '자기상관 보존.')
    a(f'- 실행 시간: {elapsed:.1f}s.')
    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser(description='2.5단계 보수적 재검증 — 복합신호 C')
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
    print(f'로드 완료: {len(price_data)}종목, DB 기간 {s0}~{e0}')

    gcal, gpos = _build_global_calendar(price_data)
    print(f'공통 시장 캘린더: {len(gcal)}거래일 ({gcal[0]}~{gcal[-1]})')

    fb_months = _build_code_eligible_months(monthly_universe_map)  # 1/2단계와 동일(fallback)
    st_months = _build_code_eligible_months_strict(monthly_universe_map)
    st_pairs = {(c, m) for c, ms in st_months.items() for m in ms}
    print(f'엄격 유니버스 (code, month): {sum(len(v) for v in st_months.values()):,} '
          f'(fallback {sum(len(v) for v in fb_months.values()):,})')

    frames = []
    for i, (code, df) in enumerate(price_data.items()):
        res = _compute_stock_strict(code, df, gcal, gpos, fb_months, availability_map, start, end)
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
    print(f'fallback 유니버스 행: {len(pool):,} / strict {int(pool["strict"].sum()):,}')

    # ON pool (엄격 유니버스 + close>MA200 + 지표 유효)
    valid = pool['rsi2'].notna() & pool['ma200'].notna() & pool['drop2'].notna()
    on = pool.loc[pool['strict'] & valid & (pool['close'] > pool['ma200'])].copy()
    sigC = (on['rsi2'] < 3) & (on['drop2'] < -5)
    n_on = len(on)
    print(f'ON(엄격) 행: {n_on:,} | C 신호(전체): {int(sigC.sum()):,}')

    # 유니버스 제외 (fallback 월만 존재 = 전월 스냅샷 부재 월)
    uni_excl = pool.loc[~pool['strict'] & valid & (pool['close'] > pool['ma200'])]
    uni_excl = uni_excl[(uni_excl['rsi2'] < 3) & (uni_excl['drop2'] < -5)]
    n_uni_excl = len(uni_excl)
    print(f'유니버스(전월 부재) 제외 C 이벤트: {n_uni_excl:,} (월: {sorted(uni_excl["month"].unique().tolist())})')

    # 표준 대조군 (같은 날짜, ON pool 내 비-C)
    ret_cols = [f'r{h}' for h in HORIZONS]
    ctrl = on.loc[~sigC].groupby('date')[ret_cols].mean()

    # h별 이벤트 (진입/청산 확정 불가 제외)
    events_by_h = {}
    excl_rows = []
    for h in HORIZONS:
        ok = ~on['entry_missing'] & ~on[f'exit_missing_{h}']
        n_ent_excl = int((sigC & on['entry_missing']).sum())
        n_exit_excl = int((sigC & ~on['entry_missing'] & on[f'exit_missing_{h}']).sum())
        excl_rows.append({'category': '진입(시가) 확정 불가', 'h': h, 'n': n_ent_excl})
        excl_rows.append({'category': f'청산(시가) 확정 불가 h{h}', 'h': h, 'n': n_exit_excl})
        ev = on.loc[sigC & ok].copy()
        ev[f'ex{h}'] = ev[f'r{h}'] - ev['date'].map(ctrl[f'r{h}'])
        ev[f'ac{h}'] = ev[f'r{h}'] - COST
        events_by_h[h] = ev
        print(f'  h={h}: C 이벤트(수익률 확정) {len(ev):,} '
              f'(진입제외 {n_ent_excl:,} + 청산제외 {n_exit_excl:,})')
    for h in HORIZONS:
        excl_rows.append({'category': f'수익률 분석 포함 h{h}', 'h': h,
                          'n': int((sigC & ~on['entry_missing'] & ~on[f'exit_missing_{h}']).sum())})
    excl_rows.append({'category': '유니버스 제외(전월 부재 월)', 'h': 0, 'n': n_uni_excl})
    excl = pd.DataFrame(excl_rows)
    for m, cnt in uni_excl.groupby('month').size().items():
        excl = pd.concat([excl, pd.DataFrame([{'category': f'유니버스 제외 {m}', 'h': 0, 'n': int(cnt)}])],
                         ignore_index=True)

    # ---- 메인 통계 (h별) ----
    main_rows = [_summarize_h(events_by_h[h], h, block_len, boot_n, control_map=ctrl)
                 for h in HORIZONS]
    main = pd.DataFrame(main_rows)

    # ---- h=3 중심 분석 ----
    ev3 = events_by_h[PRIMARY_H]
    h3_valid = ~on['entry_missing'] & ~on[f'exit_missing_{PRIMARY_H}']
    on_h3 = on.loc[sigC & h3_valid].copy()

    # 반복신호: ALL / FIRST / DEDUP5
    first_mask, dedup_mask = _first_and_dedup5(ev3, gpos)
    versions = {
        'ALL': ev3,
        'FIRST': ev3.loc[first_mask],
        'DEDUP5': ev3.loc[dedup_mask],
    }
    repeat_rows = []
    for vname, evv in versions.items():
        r = _summarize_h(evv, PRIMARY_H, block_len, boot_n, control_map=ctrl)
        r['version'] = vname
        repeat_rows.append(r)
    repeats = pd.DataFrame(repeat_rows)
    for vname, evv in versions.items():
        print(f'  {vname}: {len(evv):,}')

    # 매칭 대조 (같은 날짜 & drop2 ±1%p, RSI>=3)
    ctrl_by_date = {d: g.dropna(subset=[f'r{PRIMARY_H}'])
                    for d, g in on.loc[on['rsi2'] >= 3].groupby('date')}
    matched_rows = []
    for vname, evv in versions.items():
        me, n_nm = matched_excess(evv, ctrl_by_date, PRIMARY_H)
        row = _matched_stats(evv, me, n_nm, block_len, boot_n)
        row['version'] = vname
        matched_rows.append(row)
    matched = pd.DataFrame(matched_rows)
    for _, r in matched.iterrows():
        print(f'  매칭 {r["version"]}: 매칭 {r["n_matched"]:,} / 무매칭 {r["n_no_match"]:,} '
              f'/ 추가기여 {r["matched_excess"]}%')

    # ---- 집중도 (h=3) ----
    sub = ev3.dropna(subset=['r3'])
    full_n = len(sub)
    full_mean = sub['r3'].mean()
    full_exc = sub['ex3'].mean()
    conc_rows = [{'item': '전체', 'n': full_n, 'mean': round(full_mean, 4), 'excess': round(full_exc, 4)}]
    for pct in (0.01, 0.05):
        k = int(np.ceil(full_n * pct))
        rk = sub['r3'].rank(ascending=False, method='first')
        keep = rk > k
        conc_rows.append({'item': f'상위 {int(pct * 100)}% 제외', 'n': int(keep.sum()),
                          'mean': round(sub.loc[keep, 'r3'].mean(), 4),
                          'excess': round(sub.loc[keep, 'ex3'].mean(), 4)})
    # 연도 제외
    yrows = []
    for y in sorted(sub['date'].str[:4].unique()):
        keep = sub['date'].str[:4] != y
        yrows.append({'year_removed': y, 'n_removed': int((~keep).sum()), 'n': int(keep.sum()),
                      'mean': round(sub.loc[keep, 'r3'].mean(), 4),
                      'excess': round(sub.loc[keep, 'ex3'].mean(), 4)})
    yearly = pd.DataFrame(yrows)
    if len(yearly) >= 2 and yearly['mean'].notna().sum() >= 2:
        ymn = yearly.loc[yearly['mean'].idxmin()]
        ymx = yearly.loc[yearly['mean'].idxmax()]
        conc_rows.append({'item': f'연도제외 최소({ymn["year_removed"]})', 'n': ymn['n'],
                          'mean': ymn['mean'], 'excess': ymn['excess']})
        conc_rows.append({'item': f'연도제외 최대({ymx["year_removed"]})', 'n': ymx['n'],
                          'mean': ymx['mean'], 'excess': ymx['excess']})
    # 종목 기여
    g = sub.groupby('code').agg(n=('r3', 'size'), mean=('r3', 'mean'), sum_exc=('ex3', 'sum'))
    top_pos = g.nlargest(5, 'sum_exc')
    top_neg = g.nsmallest(5, 'sum_exc')
    for label, codes in [('상위기여 1종목 제외', top_pos.index[:1]),
                         ('상위기여 5종목 제외', top_pos.index[:5]),
                         ('하위기여 5종목 제외', top_neg.index[:5])]:
        keep = ~sub['code'].isin(codes)
        conc_rows.append({'item': label, 'n': int(keep.sum()),
                          'mean': round(sub.loc[keep, 'r3'].mean(), 4),
                          'excess': round(sub.loc[keep, 'ex3'].mean(), 4)})
    conc = pd.DataFrame(conc_rows)

    top_stocks_rows = []
    for grp, frame in [('상위 5 (추가기여 합)', top_pos), ('하위 5 (추가기여 합)', top_neg)]:
        for code, r in frame.iterrows():
            top_stocks_rows.append({'grp': grp, 'code': str(code).zfill(6),
                                    'name': symbol_names.get(code, ''), 'n': int(r['n']),
                                    'mean': round(r['mean'], 4), 'sum_exc': round(r['sum_exc'], 4)})
    top_stocks = pd.DataFrame(top_stocks_rows)

    # ---- 1/2단계 비교 ----
    comp_rows = []
    s1 = load_stage_summary('_rsi_event_study', 'event_study_summary')
    if s1 is not None:
        c1 = s1[(s1.signal == 'C') & (s1.h == PRIMARY_H)].iloc[0]
        comp_rows.append({'stage': '1단계', 'note': 'fallback 유니버스·종목별 shift·종가 fallback',
                          'n_events': int(c1['n_events']), 'mean': round(c1['mean'], 4),
                          'excess': round(c1['excess'], 4),
                          'ac_mean': round(c1['mean'] - COST, 4)})
    s2 = load_stage_summary('_rsi_event_study_stage2', 'stage2_summary')
    if s2 is not None:
        c2 = s2[(s2.signal == 'C') & (s2.variant == 'ON') & (s2.h == PRIMARY_H)]
        if len(c2):
            c2 = c2.iloc[0]
            comp_rows.append({'stage': '2단계', 'note': 'fallback 유니버스·종목별 shift·종가 fallback',
                              'n_events': int(c2['n_events']), 'mean': round(c2['mean'], 4),
                              'excess': round(c2['excess'], 4),
                              'ac_mean': round(c2['mean'] - COST, 4)})
    comp_rows.append({'stage': '2.5단계', 'note': '엄격 유니버스·공통 캘린더·시가만',
                      'n_events': int(main.loc[main.h == PRIMARY_H, 'n_events'].iloc[0]),
                      'mean': main.loc[main.h == PRIMARY_H, 'mean'].iloc[0],
                      'excess': main.loc[main.h == PRIMARY_H, 'excess'].iloc[0],
                      'ac_mean': main.loc[main.h == PRIMARY_H, 'ac_mean'].iloc[0]})
    comp = pd.DataFrame(comp_rows)

    # ---- 판정 ----
    r3row = main.loc[main.h == PRIMARY_H].iloc[0]
    ac_lo, ac_hi = r3row['ac_95ci_lo'], r3row['ac_95ci_hi']
    m_row = matched.loc[matched.version == 'ALL'].iloc[0]
    if not np.isnan(r3row['ac_mean']) and r3row['ac_mean'] > 0 and ac_lo > 0:
        verdict = '유지 — 보수적 처리에서도 비용후 양수 + 95% CI가 0 제외'
    elif not np.isnan(r3row['ac_mean']) and r3row['ac_mean'] > 0:
        verdict = '축소 — 비용후 양수지만 95% CI가 0 포함 (불확실)'
    elif not np.isnan(r3row['ac_mean']) and r3row['ac_mean'] <= 0:
        verdict = '소멸 — 보수적 처리에서 비용후 0 이하'
    else:
        verdict = '판정 불가'
    print(f'\n판정 제안: {verdict}')

    elapsed = time.time() - t0

    # ---- 출력 저장 ----
    stamp = time.strftime('%Y%m%d_%H%M%S')
    report_dir = project_root / 'backtest' / 'reports' / f'{time.strftime("%Y%m%d")}_rsi_event_study_stage25'
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

    save('stage25_main', main)
    save('stage25_exclusions', excl)
    save('stage25_matched', matched)
    save('stage25_repeats', repeats)
    save('stage25_concentration', conc)
    save('stage25_yearly', yearly)
    save('stage25_top_stocks', top_stocks)
    save('stage25_comparison', comp)
    md = render_md(main, excl, matched, repeats, conc, yearly, top_stocks, comp,
                   args, n_on, n_uni_excl, verdict, elapsed)
    save('stage25_report', md)

    # ---- stdout 요약 ----
    print('\n=== 보수적 C (h=3) ===')
    print(f'이벤트 {r3row["n_events"]:,} | 평균 {_fmt(r3row["mean"])}% | 초과 {_fmt(r3row["excess"])}% '
          f'| 비용후 {_fmt(r3row["ac_mean"])}% | 비용후 95% CI [{_fmt(ac_lo)}, {_fmt(ac_hi)}]')
    print(f'매칭대조(ALL): 추가기여 {_fmt(m_row["matched_excess"])}% '
          f'[{_fmt(m_row["me_95ci_lo"])}, {_fmt(m_row["me_95ci_hi"])}]')
    print(f'반복처리: FIRST {repeats.loc[repeats.version=="FIRST","n_events"].iloc[0]:,} '
          f'| DEDUP5 {repeats.loc[repeats.version=="DEDUP5","n_events"].iloc[0]:,}')
    print(f'집중도: 상위1%제외 {conc.loc[conc.item=="상위 1% 제외","mean"].iloc[0]:.4f}% '
          f'| 상위5%제외 {conc.loc[conc.item=="상위 5% 제외","mean"].iloc[0]:.4f}%')
    print(f'제외: 유니버스 {n_uni_excl:,} | 진입 {int(excl[(excl.category=="진입(시가) 확정 불가")&(excl.h==PRIMARY_H)]["n"].iloc[0]):,} '
          f'| 청산(h3) {int(excl[(excl.category==f"청산(시가) 확정 불가 h{PRIMARY_H}")]["n"].iloc[0]):,}')


if __name__ == '__main__':
    main()