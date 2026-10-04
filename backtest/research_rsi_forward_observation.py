#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
전향적 관찰 프로토콜 실행 스켈레톤 — 복합신호 C · h=3 고정

규칙 (backtest/reports/<날짜>_rsi_forward_observation/forward_observation_protocol.md):
- 신호 C(RSI(2)<3 & 2일하락률<-5% & close>MA200), h=3 고정.
- 유니버스 prev_month 상위 250 엄격, 진입 open(t+1)·청산 open(t+1+3) 시가만.
- 진입불가=미관찰(ENTRY_MISS), 청산불가=별도추적(EXIT_MISS). 가격 대체 없음.
- 미래정보(고가·거래량·이후 가격) 사후필터 금지. 과거 전체 재실행·최적화 없음.

--dry-run: 스키마·입출력만 검증 (샘플 레코드 1건 출력, 데이터 로드 최소화).
--date: 관찰일 1일만 처리 (기본 오늘). 레코드 누적 저장.

Usage:
    .venv/bin/python backtest/research_rsi_forward_observation.py --dry-run
    .venv/bin/python backtest/research_rsi_forward_observation.py --date 20261005
"""
import argparse
import sys
import time
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import numpy as np
import pandas as pd

from util.time_helper import get_korea_time

PRIMARY_H = 3
COST_ASSUMPTION_PCT = 0.63          # 수수료 0.015%×2 + 세금 0.20% + 슬리피지 편도 0.2%×2
QTY_ASSUMPTION = 5_000_000          # 가정 포지션 500만원
RSI_THRESHOLD = 3.0
DROP_THRESHOLD = -5.0

SCHEMA_COLUMNS = [
    'observe_date', 'code', 'name', 'rsi2', 'drop2_pct', 'close', 'ma200',
    'in_universe', 'entry_date', 'entry_price', 'exit_date', 'exit_price',
    'qty_assumption', 'cost_assumption_pct', 'ret_pct', 'ret_after_cost_pct',
    'reason_code',
]


def build_record(observe_date, code, name, row, universe_ok, entry_date, entry_price,
                 exit_date, exit_price, reason):
    """레코드 1건 생성 (순수 함수 — 스키마 고정)."""
    ret = (exit_price / entry_price - 1.0) * 100.0 if (entry_price and exit_price
                                                       and entry_price > 0) else np.nan
    return {
        'observe_date': observe_date, 'code': code, 'name': name,
        'rsi2': round(float(row['rsi2']), 3), 'drop2_pct': round(float(row['drop2']), 3),
        'close': round(float(row['close']), 2), 'ma200': round(float(row['ma200']), 2),
        'in_universe': 'Y' if universe_ok else 'N',
        'entry_date': entry_date, 'entry_price': entry_price,
        'exit_date': exit_date, 'exit_price': exit_price,
        'qty_assumption': round(QTY_ASSUMPTION / entry_price, 0) if entry_price and entry_price > 0 else np.nan,
        'cost_assumption_pct': COST_ASSUMPTION_PCT,
        'ret_pct': round(ret, 4) if not np.isnan(ret) else np.nan,
        'ret_after_cost_pct': round(ret - COST_ASSUMPTION_PCT, 4) if not np.isnan(ret) else np.nan,
        'reason_code': reason,
    }


def sample_record():
    """--dry-run용 샘플 레코드 (스키마 검증 목적)."""
    row = {'rsi2': 2.1, 'drop2': -6.5, 'close': 10000.0, 'ma200': 9500.0}
    return build_record('20261005', '000000', '샘플', row, True,
                        '20261006', 9900.0, '20261012', 10100.0, 'OK')


def _load_snapshot_and_availability(db_name):
    """월별 스냅샷·가용기간 로드 (관찰일 유니버스 판정용)."""
    from backtest.run_backtest import (
        load_universe_availability, load_monthly_universe_snapshots,
    )
    return load_universe_availability(db_name), load_monthly_universe_snapshots(db_name)


def observe_date(observe_date, db_name='backtest_data'):
    """관찰일 1일의 신호 C 관찰. 과거 재실행 없음 (당일만 처리).

    Returns: (records_df, global_calendar)
    """
    from backtest.validation_common import load_all
    from backtest.research_rsi_event_study_stage25 import _build_code_eligible_months_strict
    from util.rsi_calc import compute_rsi

    price_data, availability_map, monthly_universe_map, symbol_names, _ = load_all(db_name)
    gcal = sorted({d for df in price_data.values() for d in df.index})
    gpos = {d: i for i, d in enumerate(gcal)}
    if observe_date not in gpos:
        print(f'[skip] {observe_date} — 시장 거래일 아님 (또는 데이터 범위 밖)')
        return pd.DataFrame(columns=SCHEMA_COLUMNS), gcal

    st = _build_code_eligible_months_strict(monthly_universe_map)
    st_pairs = {(c, m) for c, ms in st.items() for m in ms}
    month = observe_date[:6]
    p = gpos[observe_date]
    entry_day = gcal[p + 1] if p + 1 < len(gcal) else None
    exit_day = gcal[p + 1 + PRIMARY_H] if p + 1 + PRIMARY_H < len(gcal) else None

    records = []
    for code, df in price_data.items():
        df = df.sort_index()
        if observe_date not in df.index:
            continue
        # 유니버스(엄격 prev_month) + 가용기간
        uni_ok = (code, month) in st_pairs
        if code in availability_map:
            earliest, latest = availability_map[code][:2]
            uni_ok = uni_ok and earliest <= month <= latest
        if not uni_ok:
            continue
        close = df['close'].astype('float64')
        rsi2 = compute_rsi(close, period=2, min_periods=2, method='wilder')
        ma200 = close.rolling(200, min_periods=200).mean()
        c2 = close.shift(2)
        drop2 = ((close - c2) / c2 * 100.0).replace([np.inf, -np.inf], np.nan)
        r = df.loc[observe_date]
        if not (close.loc[observe_date] > ma200.loc[observe_date]
                and not np.isnan(rsi2.loc[observe_date]) and not np.isnan(drop2.loc[observe_date])):
            continue
        if not (rsi2.loc[observe_date] < RSI_THRESHOLD and drop2.loc[observe_date] < DROP_THRESHOLD):
            continue
        # 진입·청산 시가 (공통 캘린더, 시가만)
        row = {'close': float(r['close']), 'rsi2': float(rsi2.loc[observe_date]),
               'drop2': float(drop2.loc[observe_date]), 'ma200': float(ma200.loc[observe_date])}
        def _open_at(d):
            if d is None or d not in df.index:
                return None
            o = float(df.loc[d, 'open'])
            return o if o > 0 else None
        entry_price = _open_at(entry_day)
        exit_price = _open_at(exit_day)
        if entry_price is None:
            reason = 'ENTRY_MISS'  # 미관찰
        elif exit_price is None:
            reason = 'EXIT_MISS'   # 별도추적(보유 리스크)
        else:
            reason = 'OK'
        records.append(build_record(observe_date, code, symbol_names.get(code, ''), row,
                                    True, entry_day, entry_price, exit_day, exit_price, reason))
    return pd.DataFrame(records, columns=SCHEMA_COLUMNS), gcal


def main():
    ap = argparse.ArgumentParser(description='전향적 관찰 스켈레톤 — 신호 C·h=3')
    ap.add_argument('--dry-run', action='store_true', help='스키마·입출력만 검증')
    ap.add_argument('--date', default=None, help='관찰일 YYYYMMDD (기본: 오늘 KST)')
    ap.add_argument('--db-name', default='backtest_data')
    ap.add_argument('--out-dir', default=None, help='레코드 저장 디렉터리 (기본: reports/<날짜>_rsi_forward_observation/records)')
    args = ap.parse_args()

    report_dir = project_root / 'backtest' / 'reports' / f'{time.strftime("%Y%m%d")}_rsi_forward_observation'
    report_dir.mkdir(parents=True, exist_ok=True)
    records_dir = args.out_dir or (report_dir / 'records')
    records_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        rec = sample_record()
        df = pd.DataFrame([rec], columns=SCHEMA_COLUMNS)
        out = report_dir / f'forward_sample_{time.strftime("%Y%m%d_%H%M%S")}.csv'
        df.to_csv(out, index=False)
        print('스키마 컬럼:', SCHEMA_COLUMNS)
        print('샘플 레코드:')
        print(df.to_string(index=False))
        print(f'저장: {out}')
        print('dry-run OK — 스키마·입출력 검증 완료')
        return

    obs_date = args.date or get_korea_time().strftime('%Y%m%d')
    print(f'관찰일: {obs_date}')
    records, gcal = observe_date(obs_date, args.db_name)
    if len(records):
        out = records_dir / f'records_{obs_date}.csv'
        records.to_csv(out, index=False)
        print(f'관찰 레코드 {len(records)}건 저장: {out}')
        print(records['reason_code'].value_counts().to_dict())
    else:
        print('관찰 레코드 없음 (신호 없음 또는 휴장일)')


if __name__ == '__main__':
    main()