"""
모멘텀 전략 백테스트 실행 스크립트

사용법:
    poetry run python -m backtest.run_momentum [--years YEARS] [options]
    
예시:
    poetry run python -m backtest.run_momentum --years 5
    poetry run python -m backtest.run_momentum --start 20200101 --end 20231231
"""

import sys
import os
import argparse
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

env_path = project_root / '.env'
load_dotenv(dotenv_path=env_path)

from backtest.momentum_engine import MomentumBacktestEngine
from util.db_helper import execute_sql, check_table_exist, resolve_date_column
from util.logging_config import configure_logging, get_logger

configure_logging(file_name='backtest.log')
logger = get_logger(__name__)


def load_cache_data(db_name: str) -> Dict[str, pd.DataFrame]:
    """Parquet 캐시에서 가격 데이터 로드"""
    cache_dir = Path(__file__).parent.parent / 'data'
    parquet_path = cache_dir / 'price_data_cache.parquet'
    
    if parquet_path.exists():
        logger.info(f"캐시 로드 중: {parquet_path}")
        df = pd.read_parquet(parquet_path)
        price_data = {}
        for code in df['code'].unique():
            code_df = df[df['code'] == code].copy()
            code_df = code_df.set_index('date')
            code_df.index = code_df.index.astype(str)
            price_data[code] = code_df
        logger.info(f"캐시 로드 완료: {len(price_data)} 종목")
        return price_data
    
    # 캐시 없으면 DB에서 로드
    logger.info("캐시 없음, DB에서 로드 시도...")
    if not check_table_exist(db_name, 'universe_availability'):
        logger.error(f"DB '{db_name}'에 데이터 없음")
        return {}
    
    sql = "SELECT code, code_name, earliest_yyyymm, latest_yyyymm FROM universe_availability"
    cur = execute_sql(db_name, sql)
    rows = cur.fetchall()
    
    price_data = {}
    for row in rows:
        code = row[0]
        if not check_table_exist(db_name, code):
            continue
        sql = f"SELECT * FROM `{code}` ORDER BY date"
        try:
            cur2 = execute_sql(db_name, sql)
            cols = [c[0] for c in cur2.description]
            df = pd.DataFrame.from_records(data=cur2.fetchall(), columns=cols)
            if df.empty:
                continue
            date_col = resolve_date_column(df)
            df = df.set_index(date_col)
            df.index = df.index.astype(str)
            price_data[code] = df
        except Exception as e:
            logger.warning(f"종목 {code} 로드 실패: {e}")
    
    logger.info(f"DB 로드 완료: {len(price_data)} 종목")
    return price_data


def load_universe_availability(db_name: str) -> dict:
    """종목별 데이터 가용 기간 로드"""
    if not check_table_exist(db_name, 'universe_availability'):
        return {}
    sql = "SELECT code, code_name, earliest_yyyymm, latest_yyyymm FROM universe_availability"
    cur = execute_sql(db_name, sql)
    result = {}
    for row in cur.fetchall():
        result[row[0]] = (row[2], row[3], row[1] if len(row) > 3 else '')
    return result


def load_monthly_universe(db_name: str) -> Dict[str, list]:
    """월별 유니버스 스냅샷 로드"""
    monthly = {}
    for tbl in ['universe_monthly', 'monthly_universe_snapshot']:
        if check_table_exist(db_name, tbl):
            sql = f"SELECT * FROM `{tbl}`"
            try:
                cur = execute_sql(db_name, sql)
                cols = [c[0] for c in cur.description]
                for row in cur.fetchall():
                    record = dict(zip(cols, row))
                    yyyymm = str(record.get('yyyymm', ''))
                    codes = record.get('codes', record.get('code', ''))
                    if isinstance(codes, str):
                        codes = codes.split(',')
                    if yyyymm and codes:
                        monthly[yyyymm] = codes
                if monthly:
                    break
            except Exception:
                continue
    return monthly


def parse_arguments():
    parser = argparse.ArgumentParser(description='모멘텀 전략 백테스트')
    parser.add_argument('--years', type=int, default=None, help='백테스트 기간 (년)')
    parser.add_argument('--start', type=str, help='시작 날짜 (YYYYMMDD)')
    parser.add_argument('--end', type=str, help='종료 날짜 (YYYYMMDD)')
    parser.add_argument('--db', type=str, default='backtest_data', help='DB 이름')
    parser.add_argument('--tag', type=str, default='', help='시나리오 태그')
    parser.add_argument('--init-capital', type=float, default=10_000_000)
    parser.add_argument('--max-holdings', type=int, default=10)
    parser.add_argument('--entry-threshold', type=float, default=8.0, help='모멘텀 진입 기준 (%)')
    parser.add_argument('--volume-surge', type=float, default=3.0, help='거래량 폭발 기준 (배수)')
    parser.add_argument('--profit-target', type=float, default=8.0, help='수익 목표 (%)')
    parser.add_argument('--stop-loss', type=float, default=8.0, help='손절 기준 (%)')
    parser.add_argument('--cache-mode', action='store_true', default=True, help='캐시 사용')
    parser.add_argument('--no-walk-forward', action='store_true', default=False)
    return parser.parse_args()


def export_summary_to_csv(results: dict, filepath: str, metadata: dict = None):
    summary = {
        'generated_at': datetime.now().strftime('%Y%m%d_%H%M%S'),
        'initial_capital': results.get('initial_capital'),
        'final_value': results.get('final_value'),
        'total_return': results.get('total_return'),
        'annual_return': results.get('annual_return'),
        'sharpe_ratio': results.get('sharpe_ratio'),
        'mdd': results.get('mdd'),
        'total_trades': results.get('total_trades'),
        'buy_trades': results.get('buy_trades'),
        'sell_trades': results.get('sell_trades'),
        'win_rate': results.get('win_rate'),
        'avg_profit_rate': results.get('avg_profit_rate'),
        'total_profit': results.get('total_profit'),
    }
    if metadata:
        summary.update(metadata)
    pd.DataFrame([summary]).to_csv(filepath, index=False, encoding='utf-8-sig')
    logger.info(f"요약 저장: {filepath}")


def main():
    args = parse_arguments()
    
    logger.info("=" * 60)
    logger.info("모멘텀 전략 백테스트 시작")
    logger.info("=" * 60)
    
    # 데이터 로드
    price_data = load_cache_data(args.db)
    if not price_data:
        logger.error("가격 데이터 없음")
        return
    
    # 유니버스 정보 로드
    availability_map = load_universe_availability(args.db) if not args.no_walk_forward else {}
    monthly_universe = load_monthly_universe(args.db) if not args.no_walk_forward else {}
    
    # 시작/종료 날짜
    all_dates = set()
    for df in price_data.values():
        all_dates.update(df.index)
    dates = sorted(all_dates)
    
    db_start_date = dates[0] if dates else None
    db_end_date = dates[-1] if dates else None
    
    if args.start:
        start_date = args.start
        end_date = args.end if args.end else datetime.now().strftime('%Y%m%d')
    elif args.years:
        end_date = args.end if args.end else datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now().replace(year=datetime.now().year - args.years)).strftime('%Y%m%d')
    else:
        start_date = db_start_date
        end_date = db_end_date
    
    logger.info(f"백테스트 기간: {start_date} ~ {end_date}")
    if monthly_universe:
        logger.info(f"월별 유니버스 스냅샷 적용: {len(monthly_universe)}개월")
    
    # 엔진 생성
    engine = MomentumBacktestEngine(
        initial_capital=args.init_capital,
        max_holdings=args.max_holdings,
        momentum_entry_threshold=args.entry_threshold,
        volume_surge_ratio=args.volume_surge,
        profit_target_pct=args.profit_target,
        stop_loss_pct=args.stop_loss,
    )
    
    # 실행
    logger.info(f"모멘텀 진입 기준: {args.entry_threshold}%")
    logger.info(f"거래량 폭발 기준: {args.volume_surge}배")
    logger.info(f"수익 목표: {args.profit_target}%")
    logger.info(f"손절 기준: {args.stop_loss}%")
    
    results = engine.run_backtest(
        price_data=price_data,
        start_date=start_date,
        end_date=end_date,
        availability_map=availability_map,
        monthly_universe_map=monthly_universe,
    )
    
    # 결과 출력
    logger.info("")
    logger.info("모멘텀 백테스트 결과")
    logger.info("=" * 60)
    logger.info(f"초기 자본금:             {results['initial_capital']:>12,.0f} 원")
    logger.info(f"최종 자산:               {results['final_value']:>12,.0f} 원")
    logger.info(f"총 수익:                 {results['total_profit']:>12,.0f} 원")
    logger.info(f"총 수익률:               {results['total_return']:>10.2f} %")
    logger.info(f"연환산 수익률:           {results['annual_return']:>10.2f} %")
    logger.info(f"샤프 비율:               {results['sharpe_ratio']:>10.2f}")
    logger.info(f"MDD:                     {results['mdd']:>10.2f} %")
    logger.info("-" * 60)
    logger.info(f"총 거래 횟수:             {results['total_trades']:>6} 회")
    logger.info(f"매수:                     {results['buy_trades']:>6} 회")
    logger.info(f"매도:                     {results['sell_trades']:>6} 회")
    logger.info(f"승률:                    {results['win_rate']:>8.2f} %")
    logger.info(f"평균 수익률:             {results['avg_profit_rate']:>8.2f} %")
    logger.info("=" * 60)
    
    # 결과 저장
    output_dir = Path(__file__).parent / 'output'
    output_dir.mkdir(parents=True, exist_ok=True)
    
    tag = args.tag or f"MOM_{args.entry_threshold}p_{args.volume_surge}x_{args.stop_loss}sl_{args.profit_target}pt"
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = output_dir / f'backtest_summary_{tag}_{timestamp}.csv'
    
    export_summary_to_csv(results, str(csv_path), {
        'db': args.db,
        'tag': tag,
        'start_date': start_date,
        'end_date': end_date,
        'entry_threshold': args.entry_threshold,
        'volume_surge': args.volume_surge,
        'stop_loss': args.stop_loss,
        'profit_target': args.profit_target,
    })
    
    logger.info("백테스트 완료!")
    return results


if __name__ == '__main__':
    main()
