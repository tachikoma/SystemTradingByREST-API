#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSI 청산 확정불가 11건 정정본 (검증된 근거 하드코딩)
- 기존 파일 수정 금지, 신규 파일만 생성
- from functions import websearch 사용 금지
- 사유 필드 분리: 시장상태 vs 원인
- 회수 구분: 60일내 관측/이후관측/합병승계확인필요/미관측
- 확정/조건부/미확인 3단 표기
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))

from backtest.research_rsi_factual_audit import (
    PRIMARY_H,
    _build_global_calendar,
    _fetch_db,
    _valid_open,
    load_all,
    project_root as pr,
)

TARGET_PATH = pr / 'backtest/reports/20261004_rsi_factual_audit/factual_audit_exit_missing_20261004_181845.csv'

# 검증된 근거 (하드코딩)
CORRECTED = {
    '003620': {
        'market_state': '거래정지',
        'cause': '회생',
        'evidence': 'https://kind.krx.co.kr/common/disclsviewer.do?acptno=20201221000262 (2020-12-21 회생신청·거래정지)',
        'recovery_class': '미관측',
    },
    '024810': {
        'market_state': '거래정지',
        'cause': '경영권변동/공시벌점 관련 실질심사',
        'evidence': 'https://marketinsight.hankyung.com/article/2023051562721 (2023-05-12 이화그룹 계열 거래정지)',
        'recovery_class': '미관측',
    },
    '035600': {
        'market_state': '거래정지',
        'cause': '실질심사',
        'evidence': 'https://www.asiae.co.kr/article/2017121408414410841 (2017-12-14 상장적격성 실질심사)',
        'recovery_class': '60일내 관측',
    },
    '042670': {
        'market_state': '거래정지',
        'cause': '흡수합병(신주 2026-01-26 예정, 비율 0.1621707) — 승계 확인 필요',
        'evidence': 'https://kind.krx.co.kr/common/disclsviewer.do?acptno=20251219000176 (2025-12-19 합병 관련 거래정지)',
        'recovery_class': '합병승계확인필요',
    },
    '048260': {
        'market_state': '거래정지',
        'cause': '경영권변동 관련 실질심사',
        'evidence': 'https://www.pharmnews.com/news/articleView.html?idxno=219196 (2023-02-28 경영권변동 관련 거래정지)',
        'recovery_class': '60일내 관측',
    },
    '093230': {
        'market_state': '거래정지',
        'cause': '경영권변동/공시벌점 관련 실질심사',
        'evidence': 'https://marketinsight.hankyung.com/article/2023051562721 (2023-05-12 이화그룹 계열 거래정지)',
        'recovery_class': '미관측',
    },
    '096040': {
        'market_state': '거래정지',
        'cause': '경영권변동/공시벌점 관련 실질심사',
        'evidence': 'https://marketinsight.hankyung.com/article/2023051562721 (2023-05-12 이화그룹 계열 거래정지)',
        'recovery_class': '미관측',
    },
    '208340': {
        'market_state': '거래정지',
        'cause': '공시벌점 관련 실질심사',
        'evidence': 'https://www.biospectator.com/news/view/20864 (2024-01-22 불성실공시법인 지정 거래정지)',
        'recovery_class': '미관측',
    },
    '221610': {
        'market_state': '거래정지',
        'cause': '주식병합 등 전자등록변경',
        'evidence': 'https://www.kmib.co.kr/article/view.asp?arcid=0015781828 (2021-04-26 주식병합 관련 거래정지)',
        'recovery_class': '60일내 관측',
    },
}


def fmt(v, decimals=2):
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return ''
    if decimals == 0:
        try:
            return f'{int(round(float(v))):,}'
        except Exception:
            return str(v)
    try:
        return f'{float(v):,.{decimals}f}'
    except Exception:
        return str(v)


def classify_confidence(market_state, cause):
    # 확정/조건부/미확인 3단
    if market_state in ('거래정지', '전자등록변경', '상장폐지절차'):
        # 대부분 확정
        if '승계 확인 필요' in cause or '합병' in cause:
            return '조건부'
        return '확정'
    return '미확인'


def main():
    if not TARGET_PATH.exists():
        alt = pr / 'backtest/output/factual_audit_exit_missing_20261004_181845.csv'
        if alt.exists():
            df_target = pd.read_csv(alt)
        else:
            raise FileNotFoundError(str(TARGET_PATH))
    else:
        df_target = pd.read_csv(TARGET_PATH)

    price_data, availability_map, monthly_universe_map, symbol_names, (s0, e0) = load_all('backtest_data')
    gcal, gpos = _build_global_calendar(price_data)

    stock_dates = {}
    for code, df in price_data.items():
        stock_dates[code] = df.index.to_numpy()

    db_cache = {}
    for c in set(df_target['code'].astype(str).str.zfill(6)):
        _fetch_db(c, db_cache)

    rows = []
    for idx, ev in df_target.iterrows():
        code_raw = str(ev['code'])
        code = code_raw.zfill(6)
        name = ev['name'] if pd.notna(ev['name']) else symbol_names.get(int(code) if code.isdigit() else code, '')
        signal_date = str(ev['signal_date'])
        entry_day = str(ev['entry_day'])
        exit_day = str(ev['exit_day'])
        entry_open = ev['entry_open']

        db = _fetch_db(code, db_cache)

        row_state = ev['exit_row_state']
        if db is not None and exit_day in db.index:
            r = db.loc[exit_day]
            o, h, l, c, v = (float(r['open']), float(r['high']), float(r['low']),
                             float(r['close']), float(r['volume']))
            if o == 0 and h == 0 and l == 0 and v == 0:
                row_state = f'행존재·시가0 (close={c:,.0f})'
            else:
                row_state = f'행존재·시가{o:,.0f}'

        first_obs_day, first_obs_open, n_days = None, np.nan, np.nan
        try:
            p = gpos.get(signal_date)
            if p is not None:
                max_k = 1 + PRIMARY_H + 60
                for k in range(1 + PRIMARY_H, max_k):
                    dd_idx = p + 1 + k
                    if dd_idx < len(gcal):
                        dd = gcal[dd_idx]
                        o = _valid_open(db, dd)
                        if not np.isnan(o):
                            first_obs_day, first_obs_open, n_days = dd, o, k - PRIMARY_H
                            break
        except Exception:
            pass

        corr = CORRECTED.get(code_raw.zfill(6)) or CORRECTED.get(code_raw) or CORRECTED.get(code)
        if not corr:
            corr = {
                'market_state': '원인미확인',
                'cause': '원인미확인',
                'evidence': 'KIND 원공시 조회 시도 - 접근제한/미확인',
                'recovery_class': '미관측',
            }

        recovery_pct = np.nan
        if not np.isnan(first_obs_open) and entry_open and entry_open > 0:
            recovery_pct = round((first_obs_open / entry_open - 1) * 100, 2)

        conf = classify_confidence(corr['market_state'], corr['cause'])
        rec = {
            'code': code,
            'name': name,
            'signal_date': signal_date,
            'entry_day': entry_day,
            'exit_day': exit_day,
            'entry_open': entry_open if not np.isnan(entry_open) else np.nan,
            'exit_row_state': row_state,
            'avail_latest': ev['avail_latest'],
            'in_snapshot_exit_month': ev['in_snapshot_exit_month'],
            'market_state': corr['market_state'],
            'cause': corr['cause'],
            'confidence': conf,
            'evidence': corr['evidence'],
            'evidence_detail': json.dumps([], ensure_ascii=False),
            'resumed_after': ev['resumed_after'],
            'first_obs_day': first_obs_day or '',
            'first_obs_open': round(first_obs_open, 2) if not np.isnan(first_obs_open) else np.nan,
            'n_days_after_exit': n_days,
            'recovery_pct': recovery_pct,
            'recovery_class': corr['recovery_class'],
        }
        rows.append(rec)

    res = pd.DataFrame(rows)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_dir_r = pr / 'backtest/reports' / f'{datetime.now().strftime("%Y%m%d")}_rsi_missing_corrected'
    out_dir_r.mkdir(parents=True, exist_ok=True)
    out_dir_o = pr / 'backtest/output'
    out_dir_o.mkdir(exist_ok=True)

    csv_name = f'missing_corrected_{stamp}.csv'
    res.to_csv(out_dir_r / csv_name, index=False)
    res.to_csv(out_dir_o / csv_name, index=False)

    # MD
    lines = []
    a = lines.append
    a('# RSI 청산 확정불가 11건 정정본')
    a('')
    a(f'**실행:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} (KST) | **출력 타임스탬프:** {stamp}')
    a('')
    a('> 주의: FDR/Naver 수정주가 아티팩트로 단정 금지. 검증된 근거(1차 출처) 기반 하드코딩 입력.')
    a('> 기존 +0.29%는 가격이 관측된 이벤트의 조건부 평균임을 명시.')
    a('')
    a('## 1) 개별표 (11건)')
    a('')
    a('| 종목 | 신호일 | 진입일 | 청산일(기대) | 진입시가 | 청산일 DB행 상태 | 가용최종월 | 청산월 스냅샷 | 시장상태 | 원인 | 신뢰도 | 근거(URL/비고) | 재개 | 첫관측일 | 첫관측 시가 | 회수율(%) | 경과일 | 회수구분 |')
    a('|------|--------|--------|--------------|---------:|------------------|------------|--------------:|----------|------|--------|----------------|:----:|----------|------------:|----------:|-------:|----------|')
    for _, r in res.iterrows():
        a(f'| {r["code"]} {r["name"]} | {r["signal_date"]} | {r["entry_day"]} | {r["exit_day"]} | '
          f'{fmt(r["entry_open"])} | {r["exit_row_state"]} | {r["avail_latest"]} | '
          f'{"Y" if r["in_snapshot_exit_month"] else "N"} | {r["market_state"]} | {r["cause"]} | {r["confidence"]} | {str(r["evidence"])[:80]} | '
          f'{r["resumed_after"]} | {r["first_obs_day"]} | {fmt(r["first_obs_open"])} | '
          f'{fmt(r["recovery_pct"])} | {fmt(r["n_days_after_exit"], 0)} | {r["recovery_class"]} |')
    a('')
    a('## 2) 집계 (확정/조건부/미확인)')
    a('')
    cnt = res['confidence'].value_counts().to_dict()
    a('| 구분 | 건수 |')
    a('|------|-----:|')
    for k in ['확정', '조건부', '미확인']:
        a(f'| {k} | {cnt.get(k, 0)} |')
    a('')
    a('## 3) 조건부 평균 명시')
    a('')
    a('- 기존 감사에서 보고된 +0.29%는 **가격이 관측된 이벤트의 조건부 평균**입니다.')
    a('- 본 정정본에서는 미관측 건을 손실0 또는 전액손실로 대체하지 않고 "미관측"으로 분류하며, 회수구분(60일내 관측/이후관측/합병승계확인필요/미관측)으로 구분합니다.')
    a('- FDR/Naver 수정주가 아티팩트(open/high/low/volume=0) 자체만으로 원인을 단정하지 않습니다.')
    a('')
    a('## 4) 근거 상세')
    a('')
    for _, r in res.iterrows():
        a(f'- **{r["code"]} {r["name"]} ({r["exit_day"]})** [{r["confidence"]}]: {r["market_state"]} / {r["cause"]} | {r["evidence"]}')
    a('')
    a('## 5) 미확인 목록')
    a('')
    unconf = res[res['confidence'] == '미확인']
    if len(unconf) == 0:
        a('- 없음')
    else:
        for _, r in unconf.iterrows():
            a(f'- {r["code"]} {r["name"]} ({r["exit_day"]})')
    a('')
    md = '\n'.join(lines) + '\n'
    md_name = f'missing_corrected_report_{stamp}.md'
    (out_dir_r / md_name).write_text(md, encoding='utf-8')
    (out_dir_o / md_name).write_text(md, encoding='utf-8')

    print(f'CSV: {out_dir_r / csv_name}')
    print(f'MD: {out_dir_r / md_name}')
    print(f'집계: {cnt}')
    print(f'미확인: {len(unconf)}건')


if __name__ == '__main__':
    main()
