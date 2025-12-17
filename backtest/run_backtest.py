"""
RSI 전략 백테스트 실행 스크립트

사용법:
    python -m backtest.run_backtest
"""

import sys
import os
from pathlib import Path
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime

# 프로젝트 루트를 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from backtest.backtest_engine import BacktestEngine
from util.db_helper import execute_sql, check_table_exist
from util.logging_config import configure_logging, get_logger

# 로깅 설정
configure_logging(file_name='backtest.log')
logger = get_logger(__name__)

# 한글 폰트 설정 (matplotlib)
plt.rcParams['font.family'] = 'AppleGothic'  # macOS
plt.rcParams['axes.unicode_minus'] = False  # 마이너스 기호 깨짐 방지


def load_price_data_from_db(strategy_name: str = 'RSIStrategy') -> dict:
    """DB에서 가격 데이터 로드
    
    Args:
        strategy_name: 전략 이름 (DB 이름)
        
    Returns:
        {종목코드: DataFrame} 딕셔너리
    """
    logger.info("DB에서 유니버스 로드 중...")
    
    # 유니버스 조회
    sql = "SELECT * FROM universe"
    cur = execute_sql(strategy_name, sql)
    universe_list = cur.fetchall()
    
    price_data = {}
    
    for item in universe_list:
        idx, code, code_name, created_at = item
        
        # 해당 종목의 가격 데이터 테이블 존재 확인
        if not check_table_exist(strategy_name, code):
            logger.warning(f"가격 데이터 테이블이 없습니다: {code}")
            continue
        
        # 가격 데이터 로드
        sql = f"SELECT * FROM `{code}`"
        cur = execute_sql(strategy_name, sql)
        cols = [column[0] for column in cur.description]
        
        df = pd.DataFrame.from_records(data=cur.fetchall(), columns=cols)
        
        if df.empty:
            logger.warning(f"가격 데이터가 비어있습니다: {code}")
            continue
        
        # 인덱스 설정 및 컬럼 이름 정리
        df = df.set_index('index')
        df.index.name = 'date'
        
        price_data[code] = df
        logger.info(f"로드 완료: {code} ({code_name}) - {len(df)}일")
    
    logger.info(f"총 {len(price_data)} 종목 로드 완료")
    return price_data


def print_results(results: dict):
    """백테스트 결과 출력
    
    Args:
        results: 백테스트 결과 딕셔너리
    """
    print("\n" + "="*60)
    print("백테스트 결과")
    print("="*60)
    print(f"초기 자본금:        {results['initial_capital']:>15,.0f} 원")
    print(f"최종 자산:          {results['final_value']:>15,.0f} 원")
    print(f"총 수익:            {results['final_value'] - results['initial_capital']:>15,.0f} 원")
    print(f"총 수익률:          {results['total_return']:>15.2f} %")
    print(f"연환산 수익률:      {results['annual_return']:>15.2f} %")
    print(f"샤프 비율:          {results['sharpe_ratio']:>15.2f}")
    print(f"MDD:                {results['mdd']:>15.2f} %")
    print("-"*60)
    print(f"총 거래 횟수:       {results['total_trades']:>15} 회")
    print(f"매수:               {results['buy_trades']:>15} 회")
    print(f"매도:               {results['sell_trades']:>15} 회")
    print(f"승률:               {results['win_rate']:>15.2f} %")
    print(f"평균 수익률:        {results['avg_profit_rate']:>15.2f} %")
    print(f"총 실현 손익:       {results['total_profit']:>15,.0f} 원")
    print("="*60 + "\n")


def plot_results(results: dict, save_path: str = None):
    """백테스트 결과 시각화
    
    Args:
        results: 백테스트 결과 딕셔너리
        save_path: 그래프 저장 경로 (None이면 화면에 표시)
    """
    df = results['daily_values']
    df['date_dt'] = pd.to_datetime(df['date'], format='%Y%m%d')
    
    fig, axes = plt.subplots(3, 1, figsize=(14, 10))
    fig.suptitle('RSI 전략 백테스트 결과', fontsize=16, fontweight='bold')
    
    # 1) 포트폴리오 가치 변화
    ax1 = axes[0]
    ax1.plot(df['date_dt'], df['portfolio_value'], linewidth=2, label='포트폴리오 가치')
    ax1.axhline(y=results['initial_capital'], color='red', linestyle='--', alpha=0.5, label='초기 자본')
    ax1.set_ylabel('포트폴리오 가치 (원)')
    ax1.set_title(f'총 수익률: {results["total_return"]:.2f}% | 연환산 수익률: {results["annual_return"]:.2f}%')
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


def main():
    """메인 실행 함수"""
    logger.info("="*60)
    logger.info("RSI 전략 백테스트 시작")
    logger.info("="*60)
    
    # 1) DB에서 가격 데이터 로드
    try:
        price_data = load_price_data_from_db('RSIStrategy')
    except Exception as e:
        logger.error(f"가격 데이터 로드 실패: {e}")
        logger.info("대신 샘플 데이터를 생성하여 테스트합니다...")
        # 샘플 데이터 생성 (간단한 예시)
        price_data = generate_sample_data()
    
    if not price_data:
        logger.error("가격 데이터가 없습니다. 백테스트를 중단합니다.")
        return
    
    # 2) 백테스트 엔진 생성
    engine = BacktestEngine(
        initial_capital=10_000_000,  # 1천만원
        max_holdings=10,
        rsi_period=2,
        ma_short=20,
        ma_long=60,
        rsi_sell_threshold=80,
        rsi_buy_threshold=5,
        price_drop_threshold=-2,
        commission_rate=0.00035,
        tax_rate=0.0015
    )
    
    # 3) 백테스트 실행
    # 기간 설정 (예: 최근 1년)
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now().replace(year=datetime.now().year - 1)).strftime('%Y%m%d')
    
    logger.info(f"백테스트 기간 설정: {start_date} ~ {end_date}")
    
    results = engine.run_backtest(
        price_data=price_data,
        start_date=start_date,
        end_date=end_date
    )
    
    # 4) 결과 출력
    print_results(results)
    
    # 5) 결과 시각화
    output_dir = project_root / 'backtest' / 'output'
    output_dir.mkdir(exist_ok=True)
    
    plot_path = output_dir / f'backtest_result_{datetime.now().strftime("%Y%m%d_%H%M%S")}.png'
    plot_results(results, save_path=str(plot_path))
    
    # 6) 거래 내역 저장
    trades_path = output_dir / f'trades_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
    export_trades_to_csv(engine, str(trades_path))
    
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
