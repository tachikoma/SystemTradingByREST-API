"""
시나리오별 backtest_summary CSV를 모아 최신 결과 비교표를 생성한다.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='유니버스 시나리오 결과 요약')
    parser.add_argument('--output-dir', default='backtest/output', help='summary CSV 디렉토리')
    parser.add_argument('--pattern', default='backtest_summary_*.csv', help='summary CSV 패턴')
    parser.add_argument('--save-prefix', default='universe_scenario_compare', help='출력 파일 prefix')
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir)
    files = sorted(output_dir.glob(args.pattern))
    if not files:
        print(f'요약 파일이 없습니다: pattern={args.pattern}')
        return 1

    frames = []
    for file in files:
        df = pd.read_csv(file)
        df['source_file'] = file.name
        frames.append(df)
    summary = pd.concat(frames, ignore_index=True)
    summary['tag'] = summary['tag'].fillna('')
    summary['generated_at'] = summary['generated_at'].astype(str)
    summary = summary.sort_values(['tag', 'generated_at'])
    latest = summary.groupby('tag', as_index=False).tail(1).copy()
    latest = latest.sort_values('total_return', ascending=False)

    keep_cols = [
        'tag', 'db', 'start_date', 'end_date', 'walk_forward',
        'total_return', 'annual_return', 'mdd', 'sharpe_ratio',
        'sell_trades', 'win_rate', 'avg_profit_rate', 'total_profit',
        'source_file',
    ]
    latest = latest[[col for col in keep_cols if col in latest.columns]]

    compare_csv = output_dir / f'{args.save_prefix}.csv'
    compare_md = output_dir / f'{args.save_prefix}.md'
    latest.to_csv(compare_csv, index=False, encoding='utf-8-sig')
    with compare_md.open('w', encoding='utf-8') as file:
        file.write('# 유니버스 시나리오 비교\n\n')
        file.write(latest.to_markdown(index=False))
        file.write('\n')

    print(compare_csv)
    print(compare_md)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())