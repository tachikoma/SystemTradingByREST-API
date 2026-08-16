"""검증 보고서용 차트 생성 스크립트

baseline(전체 기간, 스냅샷 유니버스)을 1회 실행해 다음 차트를 생성한다.
- equity_curve.png     : 포트폴리오 자산 곡선 + 낙폭(drawdown) 서브플롯
- yearly_returns.png   : 연도별 실현 수익률/거래수 바차트

사용법:
    python -m backtest.make_validation_charts [--start 2016-01-01] [--end 2026-06-30]
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))
load_dotenv(dotenv_path=project_root / '.env')

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from util.logging_config import configure_logging, get_logger
from backtest.validation_common import (
    enable_indicator_cache,
    load_all,
    run_single,
    baseline_params,
)

configure_logging(file_name='validation_charts.log')
logger = get_logger('validation_charts')


def main():
    parser = argparse.ArgumentParser(description='검증 차트 생성')
    parser.add_argument('--db-name', default='backtest_data')
    parser.add_argument('--start', default='2016-01-01')
    parser.add_argument('--end', default='2026-06-30')
    args = parser.parse_args()

    enable_indicator_cache()
    price_data, availability_map, monthly_universe_map, symbol_names, _ = load_all(args.db_name)
    logger.info("로드 완료: 종목 %d개", len(price_data))

    start_ts = args.start.replace('-', '')
    end_ts = args.end.replace('-', '')
    res, engine = run_single(
        price_data, availability_map, monthly_universe_map, symbol_names,
        start_ts, end_ts, baseline_params(),
    )

    dv = res['daily_values']
    if 'date' in dv.columns:
        dv = dv.set_index('date')
    pv = dv['portfolio_value']
    initial = float(pv.iloc[0])
    equity = pv / initial

    # 낙폭
    peak = equity.cummax()
    drawdown = (equity - peak) / peak * 100

    # 차트 1: 자산곡선 + 낙폭
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                                   gridspec_kw={'height_ratios': [3, 1]})
    ax1.plot(equity.index, equity.values, color='steelblue', lw=1.2,
             label=f'Portfolio ({initial:,.0f}원 → {pv.iloc[-1]:,.0f}원)')
    ax1.axhline(1.0, color='gray', ls='--', lw=0.8)
    ax1.set_ylabel('Portfolio multiple')
    ax1.legend(loc='upper left')
    ax1.grid(alpha=0.3)
    ax1.set_title(f'RSI(2) 전략 자산곡선 ({args.start} ~ {args.end})')

    ax2.fill_between(drawdown.index, drawdown.values, 0, color='tomato', alpha=0.6)
    ax2.set_ylabel('Drawdown (%)')
    ax2.grid(alpha=0.3)
    for ax in (ax1, ax2):
        ax.xaxis.set_major_locator(mticker.MaxNLocator(10))
    plt.tight_layout()
    p1 = project_root / 'backtest' / 'output' / 'validation_equity_curve.png'
    plt.savefig(p1, dpi=120)
    plt.close()
    logger.info(f"저장: {p1}")

    # 차트 2: 연도별 실현 손익 / 거래수
    sell_trades = [t for t in engine.trades if t['type'] == 'sell']
    import pandas as pd
    if sell_trades:
        tdf = pd.DataFrame(sell_trades)
        tdf['date'] = tdf['date'].astype(str)
        tdf['year'] = tdf['date'].str[:4]
        yearly = tdf.groupby('year').agg(
            n_trades=('profit_rate', 'size'),
            profit=('profit', 'sum'),
        )
        yearly['profit_rate_sum'] = tdf.groupby('year')['profit_rate'].sum()
        fig, ax1 = plt.subplots(figsize=(12, 5))
        ax1.bar(yearly.index, yearly['profit'] / 1e6, color=['#4C9F70' if v >= 0 else '#C0504D' for v in yearly['profit']])
        ax1.set_ylabel('실현 손익 (백만원)')
        ax1.set_xlabel('연도')
        ax1.axhline(0, color='gray', lw=0.8)
        ax2 = ax1.twinx()
        ax2.plot(yearly.index, yearly['n_trades'], color='steelblue', marker='o', label='거래수')
        ax2.set_ylabel('매도 거래수')
        ax1.set_title('연도별 실현 손익 & 거래수')
        ax1.grid(axis='y', alpha=0.3)
        plt.tight_layout()
        p2 = project_root / 'backtest' / 'output' / 'validation_yearly_returns.png'
        plt.savefig(p2, dpi=120)
        plt.close()
        logger.info(f"저장: {p2}")
        for yr, row in yearly.iterrows():
            logger.info(f"{yr}: 손익 {row['profit']:,.0f}원 / 거래 {int(row['n_trades'])}건")


if __name__ == '__main__':
    main()
