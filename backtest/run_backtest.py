"""
RSI 전략 백테스트 실행 스크립트

사용법:
    python -m backtest.run_backtest [--years YEARS] [--start START_DATE] [--end END_DATE]
    
    예시:
        # 최근 5년
        poetry run python -m backtest.run_backtest --years 5
        
        # 특정 기간 지정
        poetry run python -m backtest.run_backtest --start 20200101 --end 20231231
"""

import sys
import os
import argparse
from pathlib import Path
from dotenv import load_dotenv
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import re

# 프로젝트 루트를 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# .env를 먼저 로드하여 백테스트 설정과 로깅 설정이 반영되도록 함
env_path = project_root / '.env'
load_dotenv(dotenv_path=env_path)

from backtest.backtest_engine import BacktestEngine
from util.db_helper import execute_sql, check_table_exist
from util.logging_config import configure_logging, get_logger

# 로깅 설정
configure_logging(file_name='backtest.log')
logger = get_logger(__name__)

# 한글 폰트 설정 (matplotlib)
plt.rcParams['font.family'] = 'AppleGothic'  # macOS
plt.rcParams['axes.unicode_minus'] = False  # 마이너스 기호 깨짐 방지


def load_universe_availability(db_name: str) -> dict:
    """DB에서 종목별 데이터 가용 기간 로드

    fetch_historical_data.py가 생성한 'universe_availability' 테이블을 읽어
    {code: (earliest_yyyymm, latest_yyyymm, code_name)} 딕셔너리를 반환합니다.

    테이블이 없으면 빈 딕셔너리를 반환합니다.
    """
    if not check_table_exist(db_name, 'universe_availability'):
        return {}
    sql = "SELECT code, code_name, earliest_yyyymm, latest_yyyymm FROM universe_availability"
    cur = execute_sql(db_name, sql)
    result = {}
    for row in cur.fetchall():
        code, code_name, earliest, latest = row
        result[code] = (earliest, latest, code_name)
    return result


def load_monthly_universe_snapshots(db_name: str) -> dict:
    """DB에서 YYYYMM별 유니버스 스냅샷 로드

    Returns:
        {YYYYMM: set([code1, code2, ...])}
    """
    if not check_table_exist(db_name, 'universe_snapshots'):
        return {}

    sql = "SELECT yyyymm, code FROM universe_snapshots"
    cur = execute_sql(db_name, sql)

    snapshot_map = {}
    for yyyymm, code in cur.fetchall():
        if yyyymm not in snapshot_map:
            snapshot_map[yyyymm] = set()
        snapshot_map[yyyymm].add(code)
    return snapshot_map


def run_walk_forward_backtest(
    price_data: dict,
    start_date: str,
    end_date: str,
    db_name: str = 'backtest_data',
) -> dict:
    """워크포워드 백테스트 실행

    매수 신호 탐색 시 각 날짜에 데이터가 실제 존재하는 종목만 유니버스로 사용합니다.
    이를 통해 생존편향(survivorship bias)을 제거합니다.

    Args:
        price_data: {종목코드: DataFrame} 전체 가격 데이터
        start_date: 백테스트 시작 날짜 (YYYYMMDD)
        end_date: 백테스트 종료 날짜 (YYYYMMDD)
        db_name: DB 이름

    Returns:
        백테스트 결과 딕셔너리 (calculate_results() 형식과 동일)
    """
    availability = load_universe_availability(db_name)
    monthly_snapshots = load_monthly_universe_snapshots(db_name)

    if not availability:
        logger.warning(
            "universe_availability 테이블이 없습니다. "
            "fetch_historical_data.py를 재실행하여 스냅샷을 생성하거나, "
            "--walk-forward 없이 실행하세요."
        )
        return {}

    if not monthly_snapshots:
        logger.warning(
            "universe_snapshots 테이블이 없습니다. "
            "fetch_historical_data.py를 재실행하여 월별 스냅샷을 생성하거나, "
            "--walk-forward 없이 실행하세요."
        )
        return {}

    symbol_names = {code: info[2] for code, info in availability.items()}
    availability_map = {code: (info[0], info[1]) for code, info in availability.items()}

    engine = BacktestEngine(
        max_holdings=10,
        symbol_names=symbol_names,
    )

    logger.info(
        f"워크포워드 백테스트: {start_date} ~ {end_date}, "
        f"{len(availability_map)}개 종목 가용성 + {len(monthly_snapshots)}개월 월별 유니버스 스냅샷 적용"
    )

    return engine.run_backtest(
        price_data=price_data,
        start_date=start_date,
        end_date=end_date,
        availability_map=availability_map,
        monthly_universe_map=monthly_snapshots,
    )


def load_price_data_from_db(strategy_name: str = 'backtest_data') -> tuple:
    """DB에서 가격 데이터 로드 (Parquet 캐시 적용)
    
    Args:
        strategy_name: 전략 이름 (DB 이름)
        
    Returns:
        (price_data, date_range): 가격 데이터 딕셔너리와 데이터 기간 (start_date, end_date)
    """
    import json
    from util.db_helper import _db_path

    CACHE_DIR = project_root / 'cache'
    cache_file = CACHE_DIR / f'{strategy_name}_price_data.parquet'
    meta_file = CACHE_DIR / f'{strategy_name}_price_data.json'
    db_path = _db_path(strategy_name)

    # 1) 캐시 확인
    if cache_file.exists() and meta_file.exists():
        try:
            with open(meta_file) as f:
                meta = json.load(f)
            if meta.get('db_mtime') == os.path.getmtime(db_path):
                logger.info("캐시에서 가격 데이터 로드 중...")
                df = pd.read_parquet(cache_file)
                df['date'] = df['date'].astype(str)
                df = df.set_index(['code', 'date'])
                price_data = {code: grp.droplevel('code') for code, grp in df.groupby(level='code')}
                logger.info(f"캐시 로드 완료: {meta['count']} 종목")
                return price_data, (meta['min_date'], meta['max_date'])
        except Exception as e:
            logger.warning(f"캐시 로드 실패, DB에서 다시 로드합니다: {e}")

    # 2) DB에서 로드
    logger.info("DB에서 유니버스 로드 중...")

    sql = "SELECT * FROM universe"
    cur = execute_sql(strategy_name, sql)
    universe_list = cur.fetchall()
    total = len(universe_list)
    logger.info(f"유니버스: {total}개 종목")

    price_data = {}
    min_date = None
    max_date = None

    for idx, item in enumerate(universe_list, 1):
        code, code_name = item[1], item[2]

        if not check_table_exist(strategy_name, code):
            continue

        sql = f"SELECT * FROM `{code}`"
        cur = execute_sql(strategy_name, sql)
        cols = [column[0] for column in cur.description]
        df = pd.DataFrame.from_records(data=cur.fetchall(), columns=cols)

        if df.empty:
            continue

        if 'date' in df.columns and 'index' not in df.columns:
            df = df.rename(columns={'date': 'index'})
        df = df.set_index('index')
        df.index.name = 'date'

        price_data[code] = df

        df_min = df.index.min()
        df_max = df.index.max()
        if min_date is None or df_min < min_date:
            min_date = df_min
        if max_date is None or df_max > max_date:
            max_date = df_max

        if idx % 100 == 0 or idx == total:
            logger.info(f"데이터 로드 진행: {idx}/{total}")

    logger.info(f"총 {len(price_data)} 종목 로드 완료")
    if min_date and max_date:
        logger.info(f"데이터 기간: {min_date} ~ {max_date}")

    # 3) 캐시 저장
    if price_data:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            combined = pd.concat(price_data, names=['code', 'date']).reset_index()
            combined.to_parquet(cache_file, index=False)
            with open(meta_file, 'w') as f:
                json.dump({
                    'db_mtime': os.path.getmtime(db_path),
                    'min_date': min_date,
                    'max_date': max_date,
                    'count': len(price_data),
                }, f)
            logger.info(f"가격 데이터 캐시 저장 완료 ({cache_file})")
        except Exception as e:
            logger.warning(f"캐시 저장 실패: {e}")

    return price_data, (min_date, max_date)


def print_results(results: dict):
    """백테스트 결과 출력
    
    Args:
        results: 백테스트 결과 딕셔너리
    """
    logger.info("\n" + "="*60)
    logger.info("백테스트 결과")
    logger.info("="*60)
    logger.info(f"초기 자본금:        {results['initial_capital']:>15,.0f} 원")
    logger.info(f"최종 자산:          {results['final_value']:>15,.0f} 원")
    logger.info(f"총 수익:            {results['final_value'] - results['initial_capital']:>15,.0f} 원")
    logger.info(f"총 수익률:          {results['total_return']:>15.2f} %")
    logger.info(f"연환산 수익률:      {results['annual_return']:>15.2f} %")
    logger.info(f"ATR 트레일링 배수:    {results.get('atr_trailing_multiple', 'N/A'):>15} ")
    logger.info(f"ATR 기간:            {results.get('atr_period', 'N/A'):>15} ")  
    mfc = results.get('market_filter_code', None)
    mmp = results.get('market_ma_period', None)
    if mfc:
        logger.info(f"시장필터:             {mfc}(MA{mmp})")
    logger.info(f"샤프 비율:          {results['sharpe_ratio']:>15.2f}")
    logger.info(f"MDD:                {results['mdd']:>15.2f} %")
    logger.info("-"*60)
    logger.info(f"총 거래 횟수:       {results['total_trades']:>15} 회")
    logger.info(f"매수:               {results['buy_trades']:>15} 회")
    logger.info(f"매도:               {results['sell_trades']:>15} 회")
    logger.info(f"승률:               {results['win_rate']:>15.2f} %")
    logger.info(f"평균 수익률:        {results['avg_profit_rate']:>15.2f} %")
    logger.info(f"총 실현 손익:       {results['total_profit']:>15,.0f} 원")
    
    # 슬리피지 정보 출력
    slippage_buy = results.get('slippage_buy', 0)
    slippage_sell = results.get('slippage_sell', 0)
    if slippage_buy or slippage_sell:
        logger.info("-"*60)
        logger.info(f"매수 슬리피지:      {slippage_buy * 100:>14.2f} %")
        logger.info(f"매도 슬리피지:      {slippage_sell * 100:>14.2f} %")
    
    # 미청산 포지션 출력
    open_positions = results.get('open_positions', {})
    if open_positions:
        open_value = results.get('open_positions_value', 0)
        logger.info("-"*60)
        logger.info(f"미청산 포지션:      {len(open_positions):>15} 종목  (평가금액: {open_value:,.0f} 원)")
        logger.info("  ※ 위 최종 자산에는 미청산 포지션 평가금액이 포함됩니다.")
        for code, pos in open_positions.items():
            logger.info(
                f"  [{code}] 수량: {pos['quantity']}주, 평균단가: {pos['avg_price']:,.0f}원, 매수일: {pos['buy_date']}"
            )
    
    # 손절 정보 출력
    if results.get('stop_loss_enabled', False) or results.get('time_stop_loss_enabled', False):
        logger.info("-"*60)
        logger.info("손절 설정:")
        if results.get('stop_loss_enabled', False):
            logger.info(f"  가격 손절:        {results.get('price_stop_loss_pct', 0):>15.1f} %")
        # 시간 손절은 독립 플래그(`time_stop_loss_enabled`)가 True일 때만 출력
        if results.get('time_stop_loss_enabled', False):
            logger.info(f"  시간 손절:        {results.get('time_stop_loss_days', 0):>15} 일")
        logger.info(f"  손절 횟수:        {results.get('stop_loss_count', 0):>15} 회")
        stop_loss_ratio = (results.get('stop_loss_count', 0) / results['sell_trades'] * 100) if results['sell_trades'] > 0 else 0
        logger.info(f"  손절 비율:        {stop_loss_ratio:>15.2f} %")
    
    logger.info("="*60 + "\n")


def plot_results(results: dict, save_path: str = None):
    """백테스트 결과 시각화
    
    Args:
        results: 백테스트 결과 딕셔너리
        save_path: 그래프 저장 경로 (None이면 화면에 표시)
    """
    df = results['daily_values']
    df['date_dt'] = pd.to_datetime(df['date'], format='%Y%m%d')
    
    fig, axes = plt.subplots(3, 1, figsize=(14, 10))
    fig.suptitle('Pullback + ATR 트레일링 백테스트 결과', fontsize=16, fontweight='bold')
    
    # 1) 포트폴리오 가치 변화
    ax1 = axes[0]
    ax1.plot(df['date_dt'], df['portfolio_value'], linewidth=2, label='포트폴리오 가치')
    ax1.axhline(y=results['initial_capital'], color='red', linestyle='--', alpha=0.5, label='초기 자본')

    ax1.set_ylabel('포트폴리오 가치 (원)')
    ax1.set_title(
        f'총 수익률: {results["total_return"]:.2f}% | 연환산 수익률: {results["annual_return"]:.2f}% '
        f'| ATR 트레일링: {results.get("atr_trailing_multiple", "N/A")}x'
    )
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x/1e6:.1f}M'))
    
    # 2) 보유 종목 수 변화
    ax2 = axes[1]
    ax2.plot(df['date_dt'], df['holdings_count'], linewidth=2, color='green', label='보유 종목 수')
    ax2.set_ylabel('보유 종목 수')
    ax2.set_title('보유 종목 수 변화')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    
    # 3) 누적 수익률
    df['cumulative_return'] = (df['portfolio_value'] / results['initial_capital'] - 1) * 100
    ax3 = axes[2]
    ax3.plot(df['date_dt'], df['cumulative_return'], linewidth=2, color='purple', label='누적 수익률')
    ax3.axhline(y=0, color='red', linestyle='--', alpha=0.5)
    ax3.fill_between(df['date_dt'], 0, df['cumulative_return'], 
                      where=(df['cumulative_return'] >= 0), alpha=0.3, color='green', label='수익 구간')
    ax3.fill_between(df['date_dt'], 0, df['cumulative_return'], 
                      where=(df['cumulative_return'] < 0), alpha=0.3, color='red', label='손실 구간')
    ax3.set_ylabel('수익률 (%)')
    ax3.set_xlabel('날짜')
    ax3.set_title(f'누적 수익률 (MDD: {results["mdd"]:.2f}%)')
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    
    # x축 날짜 포맷 설정
    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
    
    plt.tight_layout()
    
    if save_path:
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        logger.info(f"그래프 저장: {save_path}")
    else:
        plt.show()


def export_trades_to_csv(engine: BacktestEngine, filepath: str):
    """거래 내역을 CSV로 저장
    
    Args:
        engine: 백테스트 엔진 인스턴스
        filepath: 저장할 파일 경로
    """
    if not engine.trades:
        logger.warning("저장할 거래 내역이 없습니다.")
        return
    
    trades_df = pd.DataFrame(engine.trades)
    trades_df.to_csv(filepath, index=False, encoding='utf-8-sig')
    logger.info(f"거래 내역 저장: {filepath} ({len(trades_df)} 건)")


def sanitize_tag(value: str) -> str:
    """파일명에 안전한 시나리오 태그로 정규화한다."""
    if not value:
        return ''
    normalized = re.sub(r'[^A-Za-z0-9._-]+', '_', str(value).strip())
    return normalized.strip('_')


def export_summary_to_csv(results: dict, filepath: str, metadata: dict | None = None):
    """백테스트 결과 요약을 단일 행 CSV로 저장한다."""
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
        'profit_target_percent': results.get('profit_target_percent'),
        'stop_loss_count': results.get('stop_loss_count', 0),
    }
    if metadata:
        summary.update(metadata)
    pd.DataFrame([summary]).to_csv(filepath, index=False, encoding='utf-8-sig')
    logger.info(f"요약 저장: {filepath}")


def parse_arguments():
    """커맨드라인 인자 파싱"""
    parser = argparse.ArgumentParser(
        description='RSI 전략 백테스트 실행',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    # 기간 설정 옵션
    period_group = parser.add_mutually_exclusive_group()
    period_group.add_argument(
        '--years',
        type=int,
        default=None,
        help='백테스트 기간 (년 단위, 기본값: DB의 전체 기간)'
    )
    period_group.add_argument(
        '--start',
        type=str,
        help='시작 날짜 (YYYYMMDD 형식, 예: 20200101)'
    )
    
    parser.add_argument(
        '--end',
        type=str,
        help='종료 날짜 (YYYYMMDD 형식, 기본값: 오늘, 예: 20231231)'
    )
    
    parser.add_argument(
        '--db',
        type=str,
        default='backtest_data',
        help='DB 이름 (기본값: backtest_data)'
    )

    parser.add_argument(
        '--no-walk-forward',
        action='store_true',
        default=False,
        help=(
            '워크포워드 모드 비활성화 (기본값: 활성화). '
            '비활성화 시 생존자 편향이 있을 수 있습니다.'
        )
    )

    parser.add_argument(
        '--tag',
        type=str,
        default='',
        help='출력 파일명에 사용할 시나리오 태그'
    )
    
    return parser.parse_args()


def main():
    """메인 실행 함수"""
    # 커맨드라인 인자 파싱
    args = parse_arguments()
    
    logger.info("="*60)
    logger.info("Pullback + ATR 트레일링 전략 백테스트 시작")
    logger.info("="*60)
    
    # 1) DB에서 가격 데이터 로드
    try:
        price_data, (db_start_date, db_end_date) = load_price_data_from_db(args.db)
    except Exception as e:
        logger.error(f"가격 데이터 로드 실패: {e}")
        logger.info("대신 샘플 데이터를 생성하여 테스트합니다...")
        # 샘플 데이터 생성 (간단한 예시)
        price_data = generate_sample_data()
        db_start_date = '20230101'
        db_end_date = '20241231'
    
    if not price_data:
        logger.error("가격 데이터가 없습니다. 백테스트를 중단합니다.")
        return
    
    # 2) 백테스트 엔진 생성
    # 환경 변수 대상 항목은 BacktestEngine 기본값 또는 .env 값 사용
    engine = BacktestEngine(
        max_holdings=10,
    )
    
    # 3) 백테스트 실행 - 기간 설정
    if args.start:
        # 특정 시작 날짜가 지정된 경우
        start_date = args.start
        end_date = args.end if args.end else datetime.now().strftime('%Y%m%d')
        logger.info(f"백테스트 기간 설정: {start_date} ~ {end_date} (사용자 지정)")
    elif args.years:
        # years 파라미터로 최근 N년 설정
        end_date = args.end if args.end else datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now().replace(year=datetime.now().year - args.years)).strftime('%Y%m%d')
        logger.info(f"백테스트 기간 설정: {start_date} ~ {end_date} (최근 {args.years}년)")
    else:
        # 기본값: DB의 전체 데이터 기간 사용
        start_date = db_start_date if db_start_date else datetime.now().strftime('%Y%m%d')
        end_date = args.end if args.end else (db_end_date if db_end_date else datetime.now().strftime('%Y%m%d'))
        logger.info(f"백테스트 기간 설정: {start_date} ~ {end_date} (DB 전체 기간)")

    # 4) 워크포워드 or 일반 백테스트 분기 (기본값: 워크포워드)
    use_walk_forward = not args.no_walk_forward
    if use_walk_forward:
        logger.info("워크포워드 모드로 백테스트를 실행합니다.")
        results = run_walk_forward_backtest(
            price_data=price_data,
            start_date=start_date,
            end_date=end_date,
            db_name=args.db,
        )
        if not results:
            logger.error("워크포워드 백테스트 실패. universe_snapshots / universe_availability 테이블을 확인하세요.")
            return
    else:
        logger.warning(
            "일반 모드(비 워크포워드)로 실행합니다. "
            "이 모드는 생존자 편향이 있을 수 있습니다."
        )
        results = engine.run_backtest(
            price_data=price_data,
            start_date=start_date,
            end_date=end_date
        )
    results['profit_target_percent'] = engine.profit_target_percent
    print_results(results)
    
    # 5) 결과 시각화
    output_dir = project_root / 'backtest' / 'output'
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    tag = sanitize_tag(args.tag)
    suffix = f'_{tag}_{timestamp}' if tag else f'_{timestamp}'
    
    plot_path = output_dir / f'backtest_result{suffix}.png'
    plot_results(results, save_path=str(plot_path))
    
    # 6) 거래 내역 저장 (일반 모드에서만 engine 거래내역 있음)
    if not use_walk_forward:
        trades_path = output_dir / f'trades{suffix}.csv'
        export_trades_to_csv(engine, str(trades_path))

    summary_path = output_dir / f'backtest_summary{suffix}.csv'
    export_summary_to_csv(
        results,
        str(summary_path),
        metadata={
            'db': args.db,
            'tag': tag,
            'start_date': start_date,
            'end_date': end_date,
            'walk_forward': use_walk_forward,
            'plot_path': str(plot_path),
            'trades_path': str(trades_path) if not use_walk_forward else '',
        },
    )
    
    logger.info("백테스트 완료!")


def generate_sample_data() -> dict:
    """샘플 데이터 생성 (테스트용)
    
    Returns:
        {종목코드: DataFrame} 딕셔너리
    """
    logger.info("샘플 데이터 생성 중...")
    
    # 임의의 종목 데이터 생성
    dates = pd.date_range(start='2023-01-01', end='2024-12-31', freq='B')
    
    sample_data = {}
    
    for i, code in enumerate(['000001', '000002', '000003']):
        # 랜덤워크 기반 가격 생성
        np.random.seed(i)
        returns = np.random.normal(0.001, 0.02, len(dates))
        prices = 10000 * (1 + returns).cumprod()
        
        df = pd.DataFrame({
            'open': prices * (1 + np.random.normal(0, 0.01, len(dates))),
            'high': prices * (1 + np.abs(np.random.normal(0.01, 0.01, len(dates)))),
            'low': prices * (1 - np.abs(np.random.normal(0.01, 0.01, len(dates)))),
            'close': prices,
            'volume': np.random.randint(100000, 1000000, len(dates))
        }, index=dates.strftime('%Y%m%d'))
        
        sample_data[code] = df
    
    logger.info(f"샘플 데이터 생성 완료: {len(sample_data)} 종목")
    return sample_data


if __name__ == '__main__':
    main()
