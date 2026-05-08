"""
유니버스 정책 시나리오를 일괄 실행하기 위한 배치 스크립트.

기본 동작은 실행 계획만 출력한다. 실제 실행은 --execute 옵션으로 수행한다.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class Scenario:
    name: str
    db_name: str
    etf_mode: str
    whitelist_codes: str = ''


DEFAULT_SCENARIOS = [
    Scenario(name='etf_all', db_name='backtest_data_etf_all', etf_mode='all'),
    Scenario(name='etf_exclude', db_name='backtest_data_etf_exclude', etf_mode='exclude'),
    Scenario(name='etf_auto_default', db_name='backtest_data_etf_auto', etf_mode='auto', whitelist_codes='229200,381180'),
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='유니버스 시나리오 배치 실행')
    parser.add_argument('--execute', action='store_true', help='명령을 실제 실행')
    parser.add_argument('--fetch-data', action='store_true', help='시나리오별 과거 데이터 수집도 함께 실행')
    parser.add_argument('--run-backtest', action='store_true', default=True, help='시나리오별 백테스트 실행')
    parser.add_argument('--years', type=int, default=None, help='백테스트 최근 N년')
    parser.add_argument('--start', type=str, default=None, help='백테스트 시작일 YYYYMMDD')
    parser.add_argument('--end', type=str, default=None, help='백테스트 종료일 YYYYMMDD')
    parser.add_argument('--walk-forward', action='store_true', help='워크포워드 백테스트 활성화')
    parser.add_argument('--scenario', action='append', dest='scenario_names', help='실행할 시나리오 이름 지정 (복수 가능)')
    parser.add_argument('--output-dir', default='backtest/output', help='실행 계획/요약 출력 디렉토리')
    return parser


def select_scenarios(names: list[str] | None) -> list[Scenario]:
    if not names:
        return DEFAULT_SCENARIOS
    wanted = set(names)
    return [scenario for scenario in DEFAULT_SCENARIOS if scenario.name in wanted]


def build_fetch_command(scenario: Scenario) -> list[str]:
    return [
        sys.executable,
        '-m',
        'backtest.fetch_historical_data',
        '--db-name', scenario.db_name,
        '--universe-etf-mode', scenario.etf_mode,
        '--universe-etf-whitelist-codes', scenario.whitelist_codes,
    ]


def build_backtest_command(scenario: Scenario, args) -> list[str]:
    command = [
        sys.executable,
        '-m',
        'backtest.run_backtest',
        '--db', scenario.db_name,
        '--tag', scenario.name,
    ]
    if args.start:
        command.extend(['--start', args.start])
    if args.end:
        command.extend(['--end', args.end])
    if args.years:
        command.extend(['--years', str(args.years)])
    if args.walk_forward:
        command.append('--walk-forward')
    return command


def save_plan(output_dir: Path, rows: list[dict]):
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    plan_path = output_dir / f'universe_scenario_plan_{timestamp}.csv'
    with plan_path.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=['scenario', 'step', 'command'])
        writer.writeheader()
        writer.writerows(rows)
    return plan_path


def main() -> int:
    args = build_parser().parse_args()
    scenarios = select_scenarios(args.scenario_names)
    if not scenarios:
        print('선택된 시나리오가 없습니다.')
        return 1

    plan_rows = []
    commands: list[tuple[str, list[str]]] = []
    for scenario in scenarios:
        if args.fetch_data:
            fetch_cmd = build_fetch_command(scenario)
            commands.append((f'{scenario.name}:fetch', fetch_cmd))
            plan_rows.append({'scenario': scenario.name, 'step': 'fetch', 'command': ' '.join(fetch_cmd)})
        if args.run_backtest:
            backtest_cmd = build_backtest_command(scenario, args)
            commands.append((f'{scenario.name}:backtest', backtest_cmd))
            plan_rows.append({'scenario': scenario.name, 'step': 'backtest', 'command': ' '.join(backtest_cmd)})

    plan_path = save_plan(Path(args.output_dir), plan_rows)
    print(f'실행 계획 저장: {plan_path}')
    for label, command in commands:
        print(f"[{label}] {' '.join(command)}")

    if not args.execute:
        print('실제 실행은 --execute 옵션으로 수행하세요.')
        return 0

    for label, command in commands:
        print(f'실행 중: {label}')
        subprocess.run(command, check=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())