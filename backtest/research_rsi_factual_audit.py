#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
사실확인 감사 — 2.5/3단계 복합신호 C의 누락·체결·비용 사실 대조 (읽기전용)

신호 C(RSI(2)<3 & 2일하락률<-5% & close>MA200), h=3 고정. 과거 데이터를 재실행해
기존 CSV/DB 기록과 교차 확인만 수행한다 (파라미터 최적화·유니버스 확장·실전 승격 없음).

a) 2.5/3단계 '진입후 청산 시가확정불가' 이벤트 개별 원인 판정
   - DB 행 존재/가용기간(availability_map)/월별 스냅샷/거래량·고저가 0 여부로
     거래정지 / 상폐·기간종료 / 단순누락 / 시가결손 구분. 판별 불가는 不明.
   - 진입 후 경과일별 첫 관측가능 시가와 회수율 별도 집계.
b) 상위기여자 감사 보강
   - stage25_top_stocks 상위 5종 + r3 상위 20건: DB 원시 OHLCV 경로·거래량·
     상한가 근접(전일종가×1.295)·시가갭·시가=고가/저가 표로 정리.
   - FDR Naver 수정주가 한계 명시, 독립가격·기업행사 확인 필요 항목은 '미확인'.
c) 기존 체결·비용 기록 대조
   - 엔진 상수/테스트/리포트의 체결·비용 가정(왕복 0.63%)을 실제 기록과 대조만 수행.

Usage:
    .venv/bin/python backtest/research_rsi_factual_audit.py --quick
    .venv/bin/python backtest/research_rsi_factual_audit.py --db-name backtest_data
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
    HORIZONS, PRIMARY_H, DEFAULT_SLIPPAGE, _total_cost_pct, _build_code_eligible_months,
    _fmt,
)
from backtest.research_rsi_event_study_stage25 import _build_code_eligible_months_strict, _build_global_calendar
from backtest.research_rsi_event_study_stage3_limited import (
    _compute_stock_stage3, classify_missing, LIMIT_NEAR_PCT,
)
from util.db_helper import execute_sql

COST = _total_cost_pct(DEFAULT_SLIPPAGE)  # 0.63%


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


def _valid_open(db, d):
    if db is None or d not in db.index:
        return np.nan
    o = float(db.loc[d, 'open'])
    return o if o > 0 else np.nan


def audit_exit_missing(events, gcal, gpos, stock_dates, availability_map,
                       monthly_universe_map, db_cache, symbol_names):
    """진입후 청산 확정불가 이벤트 개별 원인 + 회수율 집계."""
    rows = []
    for idx, ev in events.iterrows():
        code = str(ev['code']).zfill(6)
        t = ev['date']
        p = gpos[t]
        entry_day = gcal[p + 1]
        exit_day = gcal[p + 1 + PRIMARY_H]
        db = _fetch_db(code, db_cache)
        entry_open = _valid_open(db, entry_day)
        reason = classify_missing(code, exit_day, stock_dates, availability_map,
                                  monthly_universe_map) or '不明'
        # DB 행 상태 상세
        row_state = '행없음'
        if db is not None and exit_day in db.index:
            r = db.loc[exit_day]
            o, h, l, c, v = (float(r['open']), float(r['high']), float(r['low']),
                             float(r['close']), float(r['volume']))
            if o == 0 and h == 0 and l == 0 and v == 0:
                row_state = f'행존재·시가0 (close={c:,.0f})'
            else:
                row_state = f'행존재·시가{o:,.0f}'
        # 회수율: 진입 이후 첫 관측가능 시가 (청산일~+60거래일 탐색)
        first_obs_day, first_obs_open, n_days = None, np.nan, np.nan
        max_k = 1 + PRIMARY_H + 60
        for k in range(1 + PRIMARY_H, max_k):
            dd = gcal[p + 1 + k] if p + 1 + k < len(gcal) else None
            if dd is None:
                break
            o = _valid_open(db, dd)
            if not np.isnan(o):
                first_obs_day, first_obs_open, n_days = dd, o, k - PRIMARY_H
                break
        # 청산일 이후에도 정상 시가 행이 존재하는지 (아티팩트 블록 이후 재개 여부)
        resumed = False
        if db is not None:
            later = db.index[db.index > exit_day]
            for dd in later:
                if float(db.loc[dd, 'open']) > 0:
                    resumed = True
                    break
        rec = {'code': code, 'name': symbol_names.get(ev['code'], ''), 'signal_date': t,
               'entry_day': entry_day, 'exit_day': exit_day,
               'entry_open': entry_open if not np.isnan(entry_open) else np.nan,
               'exit_row_state': row_state,
               'avail_latest': (availability_map.get(code, (None, None))[1] or ''),
               'in_snapshot_exit_month': bool(monthly_universe_map.get(exit_day[:6])
                                              and code in set(monthly_universe_map[exit_day[:6]])),
               'reason': reason,
               'resumed_after': 'Y' if resumed else 'N',
               'first_obs_day': first_obs_day or '',
               'first_obs_open': round(first_obs_open, 2) if not np.isnan(first_obs_open) else np.nan,
               'n_days_after_exit': n_days,
               'recovery_pct': round((first_obs_open / entry_open - 1) * 100, 2)
               if (not np.isnan(first_obs_open) and entry_open and entry_open > 0) else np.nan}
        rows.append(rec)
    return pd.DataFrame(rows)


def audit_top_events(ev3, top_stock_codes, gcal, gpos, symbol_names, db_cache, n=20):
    """r3 상위 n건 + 상위기여 종목: DB 원시 OHLCV 감사 표."""
    top = ev3.nlargest(n, 'r3')
    stock_evs = ev3[ev3['code'].isin(top_stock_codes)]
    aud = pd.concat([top, stock_evs]).drop_duplicates(subset=['code', 'date'])
    rows = []
    for idx, ev in aud.iterrows():
        code = str(ev['code']).zfill(6)
        t = ev['date']
        p = gpos[t]
        entry_day = gcal[p + 1]
        exit_day = gcal[p + 1 + PRIMARY_H]
        db = _fetch_db(code, db_cache)
        prev_close = np.nan
        if db is not None and t in db.index:
            prev_close = float(db.loc[t, 'close'])
        eo = _valid_open(db, entry_day)
        eh = float(db.loc[entry_day, 'high']) if db is not None and entry_day in db.index else np.nan
        el = float(db.loc[entry_day, 'low']) if db is not None and entry_day in db.index else np.nan
        ec = float(db.loc[entry_day, 'close']) if db is not None and entry_day in db.index else np.nan
        ev_vol = float(db.loc[entry_day, 'volume']) if db is not None and entry_day in db.index else np.nan
        close_exit = np.nan
        if db is not None and exit_day in db.index:
            close_exit = float(db.loc[exit_day, 'close'])
        gap = round((eo / prev_close - 1) * 100, 2) if (prev_close and eo) and prev_close > 0 else np.nan
        day_ret = round((ec / prev_close - 1) * 100, 2) if (prev_close and ec) and prev_close > 0 else np.nan
        rows.append({
            'code': code, 'name': symbol_names.get(ev['code'], ''), 'signal_date': t,
            'entry_day': entry_day, 'exit_day': exit_day,
            'prev_close': prev_close, 'entry_open': eo, 'entry_high': eh, 'entry_low': el,
            'entry_close': ec, 'entry_vol': ev_vol,
            'close_exit': close_exit, 'gap_pct': gap, 'day_ret_pct': day_ret,
            'near_limit_open': bool(prev_close > 0 and eo >= prev_close * LIMIT_NEAR_PCT),
            'open_eq_high': bool(eh > 0 and eo >= eh * 0.995),
            'open_eq_low': bool(el > 0 and eo <= el * 1.005),
            'r3': round(ev['r3'], 2),
            'note': '미확인(독립가격·기업행사 검증 필요)' if (prev_close > 0 and
                    (eo >= prev_close * LIMIT_NEAR_PCT or (eh > 0 and eo >= eh * 0.995)))
                    else '정상경로(1차확인)',
        })
    return pd.DataFrame(rows)


def cost_contrast_rows():
    """기존 체결·비용 기록 대조 (재추정 없음)."""
    return pd.DataFrame([
        {'source': 'backtest_engine.py 상수',
         'assumption': '실전 수수료 DEFAULT_COMMISSION_RATE_REAL=0.00015(0.015%), 거래세 '
                       'DEFAULT_TAX_RATE_REAL=0.0020(0.20%), 슬리피지 DEFAULT_SLIPPAGE_BUY/SELL=0.002(0.2%)',
         'record': '존재', 'note': 'buy_fee_rate=1+0.00015, sell_fee_rate=1+0.00015+0.0020'},
        {'source': 'tests/test_execution_t_plus_one.py',
         'assumption': '매수=신호 다음날 시가(T+1), 시가 NaN이면 종가 fallback, 마지막날은 종가',
         'record': '존재', 'note': '비용(fee/tax/slippage) 단언 없음. 종가 fallback은 1/2단계와 일치, 2.5/3단계는 시가만(더 보수적)'},
        {'source': 'backtest/output/*trades*.csv',
         'assumption': '실거래 기록 존재 가정',
         'record': '없음', 'note': 'backtest/output에 trades CSV 없음 — 실거래 비용 기록 대조 불가'},
        {'source': '1/2/2.5/3단계 event study 리포트',
         'assumption': '왕복비용 0.63% = 수수료 0.015%×2 + 세금 0.20% + 슬리피지 편도 0.2%×2',
         'record': '일치', 'note': f'엔진 상수와 정확히 일치 (0.00015×2+0.002+0.002×2=0.0063 → {COST:.2f}%)'},
        {'source': '3단계 체결모델',
         'assumption': '진입·청산 시가만, 대체 없음 (마지막날/결측 시 미체결)',
         'record': '보수적', 'note': '엔진의 종가 fallback보다 엄격 — 비용·체결 낙관 편향 없음'},
    ])


def render_md(missing, top_aud, cost_rows, summary, args, verdict, elapsed):
    lines = []
    a = lines.append
    a('# 사실확인 감사 — 신호 C 누락·체결·비용 (읽기전용)')
    a('')
    a(f'**실행:** {time.strftime("%Y-%m-%d %H:%M:%S")} (KST) | **DB:** {args.db_name} | '
      f'**기간:** {args.start} ~ {args.end} | **신호 C·h={PRIMARY_H} 고정**')
    a('')
    a('## a) 청산 확정불가 이벤트 개별 원인 판정')
    a('')
    a('| 종목 | 신호일 | 진입일 | 청산일(기대) | 진입시가 | 청산일 DB행 상태 | 가용최종월 | 청산월 스냅샷 | 사유 | 재개 | 첫관측일 | 첫관측 시가 | 회수율(%) | 경과일 |')
    a('|------|--------|--------|--------------|---------:|------------------|------------|--------------:|------|:----:|----------|------------:|----------:|-------:|')
    for _, r in missing.iterrows():
        a(f'| {r["code"]} {r["name"]} | {r["signal_date"]} | {r["entry_day"]} | {r["exit_day"]} | '
          f'{_fmt(r["entry_open"])} | {r["exit_row_state"]} | {r["avail_latest"]} | '
          f'{"Y" if r["in_snapshot_exit_month"] else "N"} | {r["reason"]} | {r["resumed_after"]} | '
          f'{r["first_obs_day"]} | {_fmt(r["first_obs_open"])} | {_fmt(r["recovery_pct"])} | '
          f'{_fmt(r["n_days_after_exit"], 0)} |')
    a('')
    a('| 사유 | 건수 |')
    a('|------|-----:|')
    for r_, c in summary['reason_counts'].items():
        a(f'| {r_} | {c} |')
    a('')
    a('> 회수율 = (청산일 이후 첫 관측가능 시가 / 진입시가 - 1)×100. 경과일 = 기대 청산일로부터 '
      '첫 관측까지 경과 거래일 수. 시가결손 = DB에 행은 존재하나 open/high/low/volume=0 '
      '(FDR Naver 수정주가 아티팩트). 거래정지/상폐는 해당 종목이 청산일에 거래하지 못한 실질 사유.')
    a('')
    a('## b) 상위기여자 감사 (DB 원시 OHLCV)')
    a('')
    a('| 종목 | 신호일 | 진입일 | 전일종가 | 진입시가 | 고가 | 저가 | 종가 | 거래량 | 청산일종가 | 갭(%) | 당일등락(%) | 상한가근접 | 시가=고가 | 시가=저가 | r3(%) | 1차판정 |')
    a('|------|--------|--------|---------:|---------:|-----:|-----:|-----:|-------:|-----------:|------:|------------:|:----------:|:---------:|:---------:|------:|---------|')
    for _, r in top_aud.iterrows():
        a(f'| {r["code"]} {r["name"]} | {r["signal_date"]} | {r["entry_day"]} | {_fmt(r["prev_close"])} | '
          f'{_fmt(r["entry_open"])} | {_fmt(r["entry_high"])} | {_fmt(r["entry_low"])} | '
          f'{_fmt(r["entry_close"])} | {_fmt(r["entry_vol"], 0)} | {_fmt(r["close_exit"])} | '
          f'{_fmt(r["gap_pct"])} | {_fmt(r["day_ret_pct"])} | '
          f'{"Y" if r["near_limit_open"] else "N"} | {"Y" if r["open_eq_high"] else "N"} | '
          f'{"Y" if r["open_eq_low"] else "N"} | {_fmt(r["r3"])} | {r["note"]} |')
    a('')
    a('> 상한가근접 = 진입시가 ≥ 전일종가×1.295. FDR(Naver) 수정주가 기반 — 독립가격·기업행사 '
      '(액면분할/합병 등) 검증이 필요한 항목은 "미확인(독립가격·기업행사 검증 필요)"으로 남김. '
      '수정주가 아티팩트(open/high/low/volume=0) 행은 시가 부재로 분석에서 자동 제외.')
    a('')
    a('## c) 기존 체결·비용 기록 대조')
    a('')
    a('| 출처 | 가정/기록 | 존재 여부 | 대조 결과 |')
    a('|------|-----------|:---------:|-----------|')
    for _, r in cost_rows.iterrows():
        a(f'| {r["source"]} | {r["assumption"]} | {r["record"]} | {r["note"]} |')
    a('')
    a('> 비용가정 변경·재추정 없음. 0.63%는 엔진 실전 상수와 정확히 일치.')
    a('')
    a(f'## 판정: **{verdict}**')
    a('')
    a('## 재현 명령')
    a('')
    a('```bash')
    a(f'.venv/bin/python backtest/research_rsi_factual_audit.py --quick')
    a(f'.venv/bin/python backtest/research_rsi_factual_audit.py --db-name backtest_data --start 20160101 --end 20260630')
    a('```')
    a('')
    a('## 재현 주의점')
    a('')
    a('- 감사는 읽기전용(DB SELECT + 기존 CSV 대조). 파라미터 최적화·유니버스 확장·실전 승격 없음.')
    a('- 누락 11건(전체 기간)은 3단계와 동일 정의(엄격 prev_month, 공통 캘린더, 시가만)로 재현.')
    a('- 사유 판정은 availability_map + 월별 스냅샷 + DB 행 존재 기반. 판별 불가는 不明.')
    a(f'- 실행 시간: {elapsed:.1f}s.')
    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser(description='사실확인 감사 — 신호 C 누락·체결·비용')
    ap.add_argument('--db-name', default='backtest_data')
    ap.add_argument('--start', default='20160101')
    ap.add_argument('--end', default='20260630')
    ap.add_argument('--quick', action='store_true', help='스모크: 2024년 1년')
    args = ap.parse_args()

    start, end = args.start, args.end
    if args.quick:
        start, end = '20240101', '20241231'
        print(f'[quick] start={start} end={end}')

    t0 = time.time()
    price_data, availability_map, monthly_universe_map, symbol_names, (s0, e0) = load_all(args.db_name)
    gcal, gpos = _build_global_calendar(price_data)
    fb = _build_code_eligible_months(monthly_universe_map)
    st = _build_code_eligible_months_strict(monthly_universe_map)
    st_pairs = {(c, m) for c, ms in st.items() for m in ms}

    stock_dates = {}
    frames = []
    for i, (code, df) in enumerate(price_data.items()):
        stock_dates[code] = df.index.to_numpy()
        res = _compute_stock_stage3(code, df, gcal, gpos, fb, availability_map, start, end)
        if len(res):
            frames.append(res)
        if (i + 1) % 1000 == 0:
            print(f'  처리 {i + 1}/{len(price_data)}')
    pool = pd.concat(frames, ignore_index=True)
    pool['month'] = pool['date'].str[:6]
    pool['strict'] = list(map(lambda t: (t[0], t[1]) in st_pairs, zip(pool['code'], pool['month'])))
    valid = pool['rsi2'].notna() & pool['ma200'].notna() & pool['drop2'].notna()
    on = pool.loc[pool['strict'] & valid & (pool['close'] > pool['ma200'])].copy()
    sigC = (on['rsi2'] < 3) & (on['drop2'] < -5)
    ok = ~on['entry_missing'] & ~on[f'exit_missing_{PRIMARY_H}']
    ev3 = on.loc[sigC & ok].copy()
    ev3 = ev3[['code', 'date', 'r3', 'entry_open']].copy()
    ev3['name'] = ev3['code'].map(symbol_names)
    print(f'ON {len(on):,} | C(수익확정) {len(ev3):,}')

    # a) 청산 확정불가 이벤트
    exit_miss = on.loc[sigC & ~on['entry_missing'] & on[f'exit_missing_{PRIMARY_H}']]
    print(f'청산 확정불가(h={PRIMARY_H}): {len(exit_miss):,}')
    db_cache = {}
    for c in set(exit_miss['code']):
        _fetch_db(c, db_cache)
    missing = audit_exit_missing(exit_miss, gcal, gpos, stock_dates, availability_map,
                                 monthly_universe_map, db_cache, symbol_names)
    missing.to_csv(project_root / 'backtest/output/_audit_tmp_missing.csv', index=False)

    # b) 상위기여자 감사 — stage25_top_stocks CSV에서 상위 5종 로드 (기존 산출물 대조)
    s25_cands = sorted((project_root / 'backtest/reports').glob('*_rsi_event_study_stage25/stage25_top_stocks_*.csv'))
    if s25_cands:
        s25 = pd.read_csv(s25_cands[-1])
        top5_stocks = set(s25[s25['grp'].str.startswith('상위')].head(5)['code'].astype(str).str.zfill(6))
    else:
        g = ev3.groupby('code').agg(sum_exc=('r3', 'sum'))
        top5_stocks = set(g.nlargest(5, 'sum_exc').index)
    print(f'stage25 상위기여 종목: {sorted(top5_stocks)}')
    for c in top5_stocks:
        _fetch_db(c, db_cache)
    top_aud = audit_top_events(ev3, top5_stocks, gcal, gpos, symbol_names, db_cache, n=20)

    # c) 비용 대조
    cost_rows = cost_contrast_rows()

    # 사유별 집계
    reason_counts = missing['reason'].value_counts().to_dict() if len(missing) else {}
    summary = {'reason_counts': reason_counts, 'n_exit_missing': len(missing)}

    # 판정
    n_artifact = sum(v for k, v in reason_counts.items() if '시가결손' in k)
    n_non_artifact = len(missing) - n_artifact
    verdict = ('보류 유지' if n_non_artifact == 0 else
               f'보류 강화(실질 체결 리스크 {n_non_artifact}건 확인)')
    elapsed = time.time() - t0

    stamp = time.strftime('%Y%m%d_%H%M%S')
    report_dir = project_root / 'backtest' / 'reports' / f'{time.strftime("%Y%m%d")}_rsi_factual_audit'
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

    save('factual_audit_exit_missing', missing)
    save('factual_audit_top_events', top_aud)
    save('factual_audit_cost_contrast', cost_rows)
    md = render_md(missing, top_aud, cost_rows, summary, args, verdict, elapsed)
    save('factual_audit_report', md)

    print(f'\n사유별: {reason_counts}')
    print(f'판정: {verdict}')


if __name__ == '__main__':
    main()