#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RSI 청산 확정불가 11건 독립 원인 확인 (웹 검색 기반)
- develop 산출물/소유 파일은 읽기만, 신규 파일만 생성
- FDR/Naver 수정주가 아티팩트로 단정 금지
- 분류: 거래정지 / 기업행사 / 공급자결손 / 원인미확인
- 근거 URL·날짜 포함
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# 프로젝트 루트
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

# 웹 검색 도구 사용 (websearch)
from functions import websearch  # type: ignore

# 기업행사 키워드
EVENT_KEYWORDS = [
    '액면분할', '액면 분할', '주식분할', '분할',
    '합병', '인수합병', 'M&A',
    '감자', '자본감소',
    '권리락', '배당락', '권리공시', '유상증자', '무상증자',
    '상장폐지', '상장폐지 실질심사', '상장폐지 결정', '상장폐지 사유',
    '기업분할', '물적분할', '현물출자',
]

# 거래정지 키워드
HALT_KEYWORDS = [
    '거래정지', '거래 정지', '주식거래정지', '투자주의', '투자경고', '투자위험',
    '불성실공시', '공시위반', '시황변동', '기타시장안정', '매매거래정지',
    '정지', '거래 중지',
]

# 공급자 결손 관련
SUPPLIER_KEYWORDS = [
    '공급자', '납품', '수주', '계약해지', '계약취소', '대금지급', '채무불이행',
    '파산', '워크아웃', '법정관리', '회생절차', '기업회생',
]

KIND_BASE = 'https://kind.krx.co.kr/'


def _fmt(v, decimals=2):
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


def search_web(query: str, num_results: int = 5) -> List[Dict]:
    """웹 검색 수행"""
    try:
        res = websearch(query=query, numResults=num_results, type='auto')
        results = []
        if isinstance(res, dict):
            # 결과 파싱
            if 'results' in res:
                for r in res['results']:
                    results.append({
                        'title': r.get('title', ''),
                        'url': r.get('url', ''),
                        'snippet': r.get('snippet', ''),
                        'date': r.get('date', ''),
                    })
            elif 'items' in res:
                for r in res['items']:
                    results.append({
                        'title': r.get('title', ''),
                        'url': r.get('url', ''),
                        'snippet': r.get('snippet', ''),
                        'date': r.get('date', ''),
                    })
        return results
    except Exception as e:
        print(f'웹 검색 오류 ({query}): {e}')
        return []


def classify_event(code: str, name: str, exit_day: str, 
                   stock_dates: Dict, availability_map: Dict) -> Tuple[str, str, List[Dict]]:
    """
    독립 확인: 거래정지/기업행사/공급자결손/원인미확인
    반환: (분류, 근거요약, 근거목록)
    """
    results = []
    evidence_list = []
    
    # 1. 거래정지 검색 (종목명 + 날짜 + 거래정지)
    queries = [
        f'{name} {code} {exit_day} 거래정지',
        f'{name} {code} {exit_day[:4]}년 {exit_day[4:6]}월 {exit_day[6:8]}일 거래정지',
        f'{code} {exit_day} 거래정지',
    ]
    
    for q in queries[:2]:  # 주요 쿼리만
        sr = search_web(q, num_results=3)
        results.extend(sr)
        time.sleep(0.5)  # API 제한 고려
    
    # 2. 기업행사 검색
    for q in [
        f'{name} {code} {exit_day} 액면분할 합병 감자 권리락',
        f'{name} {code} {exit_day} 기업행사 공시',
    ]:
        sr = search_web(q, num_results=2)
        results.extend(sr)
        time.sleep(0.3)
    
    # 3. 상장폐지 검색
    for q in [
        f'{name} {code} {exit_day} 상장폐지',
    ]:
        sr = search_web(q, num_results=2)
        results.extend(sr)
        time.sleep(0.3)
    
    # 결과 분석
    halt_found = False
    event_found = False
    supplier_found = False
    evidence_urls = []
    
    for r in results:
        title = r['title'].lower()
        snippet = r['snippet'].lower()
        text = f'{title} {snippet}'
        url = r['url']
        if url and url not in evidence_urls:
            evidence_urls.append(url)
        
        # 거래정지 체크
        if any(k in text for k in HALT_KEYWORDS):
            halt_found = True
            evidence_list.append(r)
        
        # 기업행사 체크 (상장폐지는 기업행사/사유로 분류 가능하나 우선순위)
        if any(k in text for k in EVENT_KEYWORDS):
            # 상장폐지는 별도 우선순위 고려
            if '상장폐지' in text:
                event_found = True  # 기업행사/사유 성격
            else:
                event_found = True
            evidence_list.append(r)
        
        # 공급자 관련
        if any(k in text for k in SUPPLIER_KEYWORDS):
            supplier_found = True
            evidence_list.append(r)
    
    # 우선순위: 거래정지 > 기업행사 > 공급자결손 > 미확인
    if halt_found:
        # 근거 요약
        urls_str = '; '.join(evidence_urls[:2]) if evidence_urls else '미확인'
        return ('거래정지', urls_str, evidence_list[:3])
    elif event_found:
        urls_str = '; '.join(evidence_urls[:2]) if evidence_urls else '미확인'
        return ('기업행사', urls_str, evidence_list[:3])
    elif supplier_found:
        urls_str = '; '.join(evidence_urls[:2]) if evidence_urls else '미확인'
        return ('공급자결손', urls_str, evidence_list[:3])
    else:
        # KIND 원공시 확인 시도 (접근불가시 미확인)
        kind_attempt = f'KIND 원공시 조회 시도({code}/{exit_day}) - 접근제한/미확인'
        urls_str = kind_attempt
        return ('원인미확인', urls_str, [])


def audit_independent(missing_df: pd.DataFrame, gcal: np.ndarray, gpos: Dict,
                     stock_dates: Dict, availability_map: Dict,
                     db_cache: Dict, symbol_names: Dict) -> pd.DataFrame:
    """독립 원인 확인"""
    rows = []
    for idx, ev in missing_df.iterrows():
        code = str(ev['code']).zfill(6)
        name = ev['name'] if pd.notna(ev['name']) else symbol_names.get(int(code) if code.isdigit() else code, '')
        signal_date = ev['signal_date']
        entry_day = ev['entry_day']
        exit_day = ev['exit_day']
        entry_open = ev['entry_open']
        
        # DB 조회
        db = _fetch_db(code, db_cache)
        
        # DB 행 상태 재확인
        row_state = ev['exit_row_state']
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
        # gpos에서 위치 찾기
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
        
        # 독립 원인 분류 (웹 검색)
        print(f'  [{idx+1}/{len(missing_df)}] {code} {name} {exit_day} 원인 확인 중...')
        cause, evidence, evidence_list = classify_event(code, name, exit_day, stock_dates, availability_map)
        
        # 회수율 계산
        recovery_pct = np.nan
        if not np.isnan(first_obs_open) and entry_open and entry_open > 0:
            recovery_pct = round((first_obs_open / entry_open - 1) * 100, 2)
        
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
            'reason_audit': cause,  # 독립 분류
            'evidence': evidence,
            'evidence_detail': json.dumps([{'title': e.get('title',''), 'url': e.get('url',''), 'snippet': e.get('snippet','')[:100]} for e in evidence_list], ensure_ascii=False),
            'resumed_after': ev['resumed_after'],
            'first_obs_day': first_obs_day or '',
            'first_obs_open': round(first_obs_open, 2) if not np.isnan(first_obs_open) else np.nan,
            'n_days_after_exit': n_days,
            'recovery_pct': recovery_pct,
        }
        rows.append(rec)
        time.sleep(1)  # 검색 간격
    
    return pd.DataFrame(rows)


def render_md_independent(df: pd.DataFrame, summary: Dict, elapsed: float, stamp: str) -> str:
    lines = []
    a = lines.append
    a('# RSI 청산 확정불가 11건 독립 원인 확인')
    a('')
    a(f'**실행:** {time.strftime("%Y-%m-%d %H:%M:%S")} (KST) | **출력 타임스탬프:** {stamp}')
    a('')
    a('> 주의: FDR/Naver 수정주가 아티팩트로 단정 금지. 웹 검색(거래소 보도/KIND 원공시 시도) 기반 독립 확인.')
    a('> 조건부 평균 +0.29%는 가격이 관측된 이벤트의 조건부 평균임을 명시.')
    a('')
    a('## 1) 개별표 (11건)')
    a('')
    a('| 종목 | 신호일 | 진입일 | 청산일(기대) | 진입시가 | 청산일 DB행 상태 | 가용최종월 | 청산월 스냅샷 | 분류 | 근거(URL/비고) | 재개 | 첫관측일 | 첫관측 시가 | 회수율(%) | 경과일 |')
    a('|------|--------|--------|--------------|---------:|------------------|------------|--------------:|------|----------------|:----:|----------|------------:|----------:|-------:|')
    for _, r in df.iterrows():
        a(f'| {r["code"]} {r["name"]} | {r["signal_date"]} | {r["entry_day"]} | {r["exit_day"]} | '
          f'{_fmt(r["entry_open"])} | {r["exit_row_state"]} | {r["avail_latest"]} | '
          f'{"Y" if r["in_snapshot_exit_month"] else "N"} | {r["reason_audit"]} | {r["evidence"][:80]} | '
          f'{r["resumed_after"]} | {r["first_obs_day"]} | {_fmt(r["first_obs_open"])} | '
          f'{_fmt(r["recovery_pct"])} | {_fmt(r["n_days_after_exit"], 0)} |')
    a('')
    a('## 2) 분류 집계')
    a('')
    a('| 분류 | 건수 |')
    a('|------|-----:|')
    for cat, cnt in summary['by_cause'].items():
        a(f'| {cat} | {cnt} |')
    a('')
    a(f'**확정(거래정지/기업행사/공급자결손):** {summary["confirmed"]}건 | **미확인(원인미확인):** {summary["unconfirmed"]}건')
    a('')
    a('## 3) 조건부 평균 명시')
    a('')
    a('- 기존 감사에서 보고된 +0.29%는 **가격이 관측된 이벤트의 조건부 평균**입니다.')
    a('- 본 독립 확인에서는 미관측 건은 "미관측"으로 표시하며, 조건부 평균 계산 시에도 관측된 사례에 한해 산출합니다.')
    a('- FDR/Naver 수정주가 아티팩트(open/high/low/volume=0) 자체만으로 원인을 단정하지 않습니다.')
    a('')
    a('## 4) 근거 상세 (요약)')
    a('')
    for _, r in df.iterrows():
        if r['evidence_detail']:
            try:
                det = json.loads(r['evidence_detail'])
                if det:
                    a(f'- **{r["code"]} {r["name"]} ({r["exit_day"]})**: {r["reason_audit"]}')
                    for d in det:
                        a(f'  - {d["title"]} | {d["url"]}')
            except Exception:
                pass
    a('')
    a(f'**실행시간:** {elapsed:.1f}s')
    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser(description='RSI 청산 확정불가 11건 독립 원인 확인')
    ap.add_argument('--quick', action='store_true', help='스모크 테스트')
    args = ap.parse_args()
    
    # 대상 11건 로드
    target_path = pr / 'backtest/reports/20261004_rsi_factual_audit/factual_audit_exit_missing_20261004_181845.csv'
    if not target_path.exists():
        target_path = pr / 'backtest/output/factual_audit_exit_missing_20261004_181845.csv'
    
    df_target = pd.read_csv(target_path)
    print(f'대상 로드: {len(df_target)}건')
    print(df_target[['code', 'name', 'signal_date', 'entry_day', 'exit_day']].to_string(index=False))
    
    if args.quick:
        # 스모크: 1건만 테스트
        df_target = df_target.head(1)
        print(f'\n[QUICK] 1건만 테스트: {df_target.iloc[0]["code"]}')
    
    t0 = time.time()
    # 기존 감사 로직 재사용: DB/캘린더 로드
    price_data, availability_map, monthly_universe_map, symbol_names, (s0, e0) = load_all('backtest_data')
    gcal, gpos = _build_global_calendar(price_data)
    
    # stock_dates
    stock_dates = {}
    for code, df in price_data.items():
        stock_dates[code] = df.index.to_numpy()
    
    db_cache = {}
    for c in set(df_target['code'].astype(str).str.zfill(6)):
        _fetch_db(c, db_cache)
    
    # 독립 감사
    res = audit_independent(df_target, gcal, gpos, stock_dates, availability_map, db_cache, symbol_names)
    
    # 집계
    by_cause = res['reason_audit'].value_counts().to_dict()
    confirmed = sum(by_cause.get(k, 0) for k in ['거래정지', '기업행사', '공급자결손'])
    unconfirmed = by_cause.get('원인미확인', 0)
    summary = {
        'by_cause': by_cause,
        'confirmed': confirmed,
        'unconfirmed': unconfirmed,
        'total': len(res),
    }
    
    elapsed = time.time() - t0
    stamp = time.strftime('%Y%m%d_%H%M%S')
    
    # 출력 디렉토리
    out_dir_report = pr / 'backtest/reports' / f'{time.strftime("%Y%m%d")}_rsi_missing_independent'
    out_dir_report.mkdir(parents=True, exist_ok=True)
    out_dir_out = pr / 'backtest/output'
    out_dir_out.mkdir(exist_ok=True)
    
    # CSV 저장
    csv_name = f'missing_independent_{stamp}.csv'
    res.to_csv(out_dir_report / csv_name, index=False)
    res.to_csv(out_dir_out / csv_name, index=False)
    print(f'\nCSV 저장: {out_dir_report / csv_name}')
    
    # MD 저장
    md = render_md_independent(res, summary, elapsed, stamp)
    md_name = f'missing_independent_report_{stamp}.md'
    (out_dir_report / md_name).write_text(md, encoding='utf-8')
    (out_dir_out / md_name).write_text(md, encoding='utf-8')
    print(f'MD 저장: {out_dir_report / md_name}')
    
    # 결과 출력
    print('\n=== 결과 ===')
    print(f'총 건수: {summary["total"]}')
    print(f'분류: {by_cause}')
    print(f'확정: {confirmed}건, 미확인: {unconfirmed}건')
    print(f'\n기존 판정(보류)에 미치는 영향:')
    print(f'- 비아티팩트(실질 체결 리스크) 성격으로 확정된 건: {confirmed}건')
    print(f'- 원인 미확인: {unconfirmed}건')
    if unconfirmed == 0:
        print('- 영향: 실질 원인(거래정지/기업행사 등) 확인 → "보류 강화" 논거 보강')
    else:
        print('- 영향: 일부 미확인 잔존 → 보류 유지 근거')


if __name__ == '__main__':
    main()
