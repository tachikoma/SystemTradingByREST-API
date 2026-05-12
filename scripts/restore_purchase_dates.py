"""restore_purchase_dates.py
보유 종목의 최초 매수 체결일을 조회하여 DB에 자동 복구합니다.

동작 요약:
- 로컬 `logs/trade_history.csv`에서 먼저 매수 기록을 찾아 최초 매수일을 채웁니다.
- 로컬에서 찾지 못하면 Kiwoom API(kt00007)로 전체 체결이력 조회, 여전히 없으면 ord_dt로 일별 역탐색합니다.
- `--codes`를 지정하면 보유 종목 중 해당 코드만 처리합니다(보유하지 않은 종목은 스킵).
- 진행 상황을 출력하여 장시간 실행 시 멈춤처럼 보이지 않도록 합니다.

사용 예:
    poetry run python scripts/restore_purchase_dates.py --lookback-days 365 --sleep 0.3
    poetry run python scripts/restore_purchase_dates.py --codes 032820 091160 --force
"""

import argparse
import sys
import os
import time
import datetime
import traceback
import csv
from pathlib import Path
from dotenv import load_dotenv

# 프로젝트 루트를 sys.path에 추가하여 상대 import가 동작하도록 함
repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from util.db_helper import upsert_purchase_date, get_purchase_date
from util.time_helper import get_korea_time
from api.Kiwoom import Kiwoom


def load_trade_history_earliest_buy(log_path: Path):
    """
    trade_history.csv를 읽어 종목별 최초(최소) 매수일(YYYYMMDD) 반환
    """
    result = {}
    if not log_path.exists():
        return result

    try:
        # BOM(\ufeff) 처리를 위해 utf-8-sig로 읽습니다
        with log_path.open('r', encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = (row.get('code') or '').strip().lstrip('A')
                if not code:
                    continue
                action = (row.get('action') or '').lower()
                # 영어/한글 모두 대응
                if 'buy' not in action and '매' not in action:
                    continue

                ts = (row.get('timestamp') or row.get('ord_dt') or '').strip()
                if not ts:
                    continue
                try:
                    dt = datetime.datetime.fromisoformat(ts)
                except Exception:
                    try:
                        dt = datetime.datetime.strptime(ts[:10], '%Y-%m-%d')
                    except Exception:
                        continue

                date_str = dt.date().strftime('%Y%m%d')
                prev = result.get(code)
                if not prev or date_str < prev:
                    result[code] = date_str
    except Exception:
        return {}

    return result


def find_earliest_buy_for_code(kiwoom: Kiwoom, code: str, lookback_days: int = 1825, sleep_per_call: float = 0.12):
    """
    Kiwoom API로 code의 최초 매수일을 찾음.
    - 전체 체결 이력에서 `체결일자`가 있으면 그중 최소 반환
    - 없으면 ord_dt로 일별 역탐색(역순)하여 최초 매수일 발견 시 반환
    """
    # 1) 전체 체결 이력 요청
    try:
        results = kiwoom.get_executions_for_code(code)
    except Exception as e:
        raise

    buy_dates = set()
    for r in results:
        if '매수' in (r.get('구분') or '') and r.get('체결일자'):
            buy_dates.add(r.get('체결일자'))
    if buy_dates:
        return min(buy_dates)

    # 2) 일별 역탐색
    today = get_korea_time().date()
    scanned = 0
    for delta in range(0, lookback_days):
        d = today - datetime.timedelta(days=delta)
        date_str = d.strftime('%Y%m%d')

        # 단계적 진행 메시지 (사용자가 진행 상황을 볼 수 있게끔)
        scanned += 1
        if scanned % 30 == 0:
            print(f"    [scan] {code}: 역탐색 {scanned}/{lookback_days}일 진행 중...", flush=True)

        try:
            page = kiwoom.get_executions_for_code(code, ord_dt=date_str)
        except Exception:
            # 간단 재시도
            time.sleep(1.0)
            try:
                page = kiwoom.get_executions_for_code(code, ord_dt=date_str)
            except Exception:
                page = []

        for r in page:
            if '매수' in (r.get('구분') or ''):
                return date_str

        time.sleep(sleep_per_call)

    return None


def main():
    parser = argparse.ArgumentParser(description='Restore purchase dates from execution history')
    parser.add_argument('--lookback-days', type=int, default=1825,
                        help='역탐색 일수(기본: 1825일 = 5년)')
    parser.add_argument('--force', action='store_true', help='기존 DB에 값이 있어도 덮어쓰기')
    parser.add_argument('--sleep', type=float, default=0.12, help='일별 조회 사이 대기시간(초)')
    parser.add_argument('--codes', nargs='*', help='테스트할 종목 코드들(공백 구분, 예: 032820 091160)')
    args = parser.parse_args()

    # .env 로드
    env_path = Path(__file__).resolve().parents[1] / '.env'
    load_dotenv(dotenv_path=env_path)

    mode = os.environ.get('KIWOOM_MODE', 'mock').lower()
    is_mock = mode == 'mock'
    if is_mock:
        appkey = os.environ.get('KIWOOM_MOCK_APPKEY') or os.environ.get('KIWOOM_APPKEY')
        secretkey = os.environ.get('KIWOOM_MOCK_SECRETKEY') or os.environ.get('KIWOOM_SECRETKEY')
    else:
        appkey = os.environ.get('KIWOOM_REAL_APPKEY')
        secretkey = os.environ.get('KIWOOM_REAL_SECRETKEY')

    if not appkey or not secretkey:
        print('API 키가 설정되어 있지 않습니다. .env를 확인하세요.', flush=True)
        sys.exit(1)

    kiwoom = Kiwoom(appkey=appkey, secretkey=secretkey, mock=is_mock)
    print('Kiwoom 인스턴스 생성, 잔고 조회 중...', flush=True)
    kiwoom.get_balance()

    # trade_history 먼저 로드
    trade_history_path = Path(__file__).resolve().parents[1] / 'logs' / 'trade_history.csv'

    local_earliest = load_trade_history_earliest_buy(trade_history_path)
    # 보유 종목만 필터링
    held_codes = set(k.lstrip('A') for k in kiwoom.balance.keys())
    local_earliest_held = {c: d for c, d in local_earliest.items() if c in held_codes}
    if local_earliest:
        print(f"보유 종목 중 trade_history에서 매수 기록이 있는 종목 수: {len(local_earliest_held)}", flush=True)
    else:
        print("trade_history에 매수 기록이 없습니다 or 파일을 찾을 수 없음.", flush=True)

    # 보유 종목 맵(정규화된 코드 -> (원본키, info))
    balance_map = {k.lstrip('A'): (k, v) for k, v in kiwoom.balance.items()}

    # 처리할 종목 결정
    if args.codes:
        requested = [c.strip().lstrip('A') for c in args.codes if c and c.strip()]
        codes_list = []
        for rc in requested:
            if rc in balance_map:
                codes_list.append(balance_map[rc])
            else:
                print(f"[SKIP-NOT-HELD] {rc} - 보유 종목 아님(호출 건너뜀)", flush=True)
    else:
        codes_list = list(kiwoom.balance.items())

    total = len(codes_list)
    print(f"총 {total}개 보유 종목 처리 시작 (trade_history 우선 조회).", flush=True)

    for idx, (code, info) in enumerate(codes_list, start=1):
        norm_code = code.lstrip('A')
        print(f"[{idx}/{total}] 처리중: {norm_code} ({info.get('종목명')})", flush=True)

        try:
            existing = get_purchase_date(norm_code)
            if existing and not args.force:
                print(f"  [SKIP] {norm_code} - 이미 DB에 매수일 존재: {existing}", flush=True)
                continue

            # 1) 로컬 로그 우선(보유 종목만)
            local_date = local_earliest_held.get(norm_code)
            if local_date:
                upsert_purchase_date(norm_code, local_date)
                print(f"  [OK-LOCAL] {norm_code} → {local_date} (trade_history.csv 기반)", flush=True)
                continue

            # 2) API 호출
            print(f"  [API] trade_history에 없음 — 전체 체결 이력 조회 시작", flush=True)
            earliest = find_earliest_buy_for_code(kiwoom, code, lookback_days=args.lookback_days, sleep_per_call=args.sleep)
            if earliest:
                upsert_purchase_date(norm_code, earliest)
                print(f"  [OK] {norm_code} → {earliest} (API 기반)", flush=True)
            else:
                print(f"  [NOT FOUND] {norm_code} - 지정 기간({args.lookback_days}일) 내 매수 체결 없음", flush=True)

        except Exception as e:
            print(f"  [ERROR] {norm_code} 처리 중 오류: {e}", flush=True)
            traceback.print_exc()


if __name__ == '__main__':
    main()
