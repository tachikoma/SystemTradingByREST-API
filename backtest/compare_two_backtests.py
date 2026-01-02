"""
두 개의 백테스트 결과를 비교 분석하는 스크립트

사용법:
    python compare_two_backtests.py <file1.csv> <file2.csv>
"""

import pandas as pd
import numpy as np
import sys
from datetime import datetime

def load_and_analyze(csv_file):
    """백테스트 CSV 파일을 로드하고 기본 통계를 계산"""
    df = pd.read_csv(csv_file)
    
    # 날짜 파싱
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
    
    # 매도 거래만 필터링 (실제 수익 발생)
    sell_df = df[df['type'] == 'sell'].copy()
    
    if len(sell_df) == 0:
        return None
    
    # 기간 계산
    start_date = df['date'].min()
    end_date = df['date'].max()
    days = (end_date - start_date).days
    years = days / 365.25
    
    # 수익 통계
    total_profit = sell_df['profit'].sum()
    avg_profit_rate = sell_df['profit_rate'].mean()
    median_profit_rate = sell_df['profit_rate'].median()
    max_profit_rate = sell_df['profit_rate'].max()
    min_profit_rate = sell_df['profit_rate'].min()
    std_profit_rate = sell_df['profit_rate'].std()
    
    # 승률 계산
    win_count = (sell_df['profit'] > 0).sum()
    total_trades = len(sell_df)
    win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
    
    # 연평균 수익률 계산 (복리)
    # 초기 자본 = 1000만원 가정
    initial_capital = 10_000_000
    final_value = initial_capital + total_profit
    total_return = (final_value - initial_capital) / initial_capital * 100
    annual_return = (((final_value / initial_capital) ** (1 / years)) - 1) * 100 if years > 0 else 0
    
    # 거래 빈도
    monthly_trades = total_trades / (years * 12) if years > 0 else 0
    
    # 종목 수
    unique_stocks = df['code'].nunique()
    
    # 보유 기간 계산
    buy_df = df[df['type'] == 'buy'].copy()
    holding_periods = []
    
    for code in sell_df['code'].unique():
        buys = buy_df[buy_df['code'] == code].sort_values('date')
        sells = sell_df[sell_df['code'] == code].sort_values('date')
        
        for _, sell_row in sells.iterrows():
            # 해당 매도보다 앞선 매수 찾기
            prior_buys = buys[buys['date'] <= sell_row['date']]
            if len(prior_buys) > 0:
                last_buy = prior_buys.iloc[-1]
                holding_days = (sell_row['date'] - last_buy['date']).days
                holding_periods.append(holding_days)
    
    avg_holding = np.mean(holding_periods) if holding_periods else 0
    median_holding = np.median(holding_periods) if holding_periods else 0
    
    # 연도별 성과
    sell_df['year'] = sell_df['date'].dt.year
    yearly_stats = sell_df.groupby('year').agg({
        'profit': 'sum',
        'profit_rate': 'mean',
        'code': 'count'
    }).rename(columns={'code': 'trades'})
    
    return {
        'file': csv_file,
        'start_date': start_date,
        'end_date': end_date,
        'days': days,
        'years': years,
        'total_trades': len(df),
        'buy_trades': len(buy_df),
        'sell_trades': total_trades,
        'unique_stocks': unique_stocks,
        'total_profit': total_profit,
        'total_return': total_return,
        'annual_return': annual_return,
        'avg_profit_rate': avg_profit_rate,
        'median_profit_rate': median_profit_rate,
        'max_profit_rate': max_profit_rate,
        'min_profit_rate': min_profit_rate,
        'std_profit_rate': std_profit_rate,
        'win_rate': win_rate,
        'win_count': win_count,
        'monthly_trades': monthly_trades,
        'avg_holding': avg_holding,
        'median_holding': median_holding,
        'yearly_stats': yearly_stats,
        'df': df,
        'sell_df': sell_df
    }

def print_comparison(stats1, stats2):
    """두 백테스트 결과를 비교하여 출력"""
    
    print("=" * 80)
    print("📊 두 백테스트 결과 비교")
    print("=" * 80)
    print()
    
    # 파일명 출력
    print(f"📁 비교 대상:")
    print(f"   [A] {stats1['file'].split('/')[-1]}")
    print(f"   [B] {stats2['file'].split('/')[-1]}")
    print()
    
    # 기간 비교
    print("📅 백테스트 기간:")
    print(f"   [A] {stats1['start_date'].strftime('%Y-%m-%d')} ~ {stats1['end_date'].strftime('%Y-%m-%d')} ({stats1['years']:.1f}년)")
    print(f"   [B] {stats2['start_date'].strftime('%Y-%m-%d')} ~ {stats2['end_date'].strftime('%Y-%m-%d')} ({stats2['years']:.1f}년)")
    print()
    
    # 거래 통계 비교
    print("📈 거래 통계:")
    print(f"   총 거래:")
    print(f"      [A] {stats1['total_trades']:3d}건 (매수: {stats1['buy_trades']:3d}, 매도: {stats1['sell_trades']:3d})")
    print(f"      [B] {stats2['total_trades']:3d}건 (매수: {stats2['buy_trades']:3d}, 매도: {stats2['sell_trades']:3d})")
    print(f"      차이: {stats2['total_trades'] - stats1['total_trades']:+d}건 ({(stats2['total_trades'] / stats1['total_trades'] - 1) * 100:+.1f}%)")
    print()
    
    print(f"   거래 종목 수:")
    print(f"      [A] {stats1['unique_stocks']}개")
    print(f"      [B] {stats2['unique_stocks']}개")
    print(f"      차이: {stats2['unique_stocks'] - stats1['unique_stocks']:+d}개 ({(stats2['unique_stocks'] / stats1['unique_stocks'] - 1) * 100:+.1f}%)")
    print()
    
    # 수익률 비교
    print("💰 수익률:")
    print(f"   총 실현손익:")
    print(f"      [A] {stats1['total_profit']:>15,.0f}원")
    print(f"      [B] {stats2['total_profit']:>15,.0f}원")
    print(f"      차이: {stats2['total_profit'] - stats1['total_profit']:>15,.0f}원 ({(stats2['total_profit'] / stats1['total_profit'] - 1) * 100:+.1f}%)")
    print()
    
    print(f"   총 수익률:")
    print(f"      [A] {stats1['total_return']:6.2f}%")
    print(f"      [B] {stats2['total_return']:6.2f}%")
    print(f"      차이: {stats2['total_return'] - stats1['total_return']:+6.2f}%p")
    print()
    
    print(f"   연평균 수익률:")
    print(f"      [A] {stats1['annual_return']:6.2f}%")
    print(f"      [B] {stats2['annual_return']:6.2f}%")
    print(f"      차이: {stats2['annual_return'] - stats1['annual_return']:+6.2f}%p")
    print()
    
    print(f"   평균 수익률:")
    print(f"      [A] {stats1['avg_profit_rate']:6.2f}%")
    print(f"      [B] {stats2['avg_profit_rate']:6.2f}%")
    print(f"      차이: {stats2['avg_profit_rate'] - stats1['avg_profit_rate']:+6.2f}%p")
    print()
    
    print(f"   승률:")
    print(f"      [A] {stats1['win_rate']:6.2f}% ({stats1['win_count']}/{stats1['sell_trades']})")
    print(f"      [B] {stats2['win_rate']:6.2f}% ({stats2['win_count']}/{stats2['sell_trades']})")
    print()
    
    # 거래 특성 비교
    print("📊 거래 특성:")
    print(f"   월평균 거래:")
    print(f"      [A] {stats1['monthly_trades']:.1f}건/월")
    print(f"      [B] {stats2['monthly_trades']:.1f}건/월")
    print(f"      차이: {stats2['monthly_trades'] - stats1['monthly_trades']:+.1f}건/월")
    print()
    
    print(f"   평균 보유기간:")
    print(f"      [A] {stats1['avg_holding']:6.1f}일 (중앙값: {stats1['median_holding']:6.0f}일)")
    print(f"      [B] {stats2['avg_holding']:6.1f}일 (중앙값: {stats2['median_holding']:6.0f}일)")
    print(f"      차이: {stats2['avg_holding'] - stats1['avg_holding']:+6.1f}일")
    print()
    
    # 연도별 비교 (공통 연도만)
    common_years = sorted(set(stats1['yearly_stats'].index) & set(stats2['yearly_stats'].index))
    if common_years:
        print("📆 연도별 실현손익 비교 (공통 기간):")
        print(f"   {'연도':<6} {'[A] 손익':>15} {'[B] 손익':>15} {'차이':>15} {'변화율':>10}")
        print("   " + "-" * 70)
        for year in common_years:
            profit_a = stats1['yearly_stats'].loc[year, 'profit']
            profit_b = stats2['yearly_stats'].loc[year, 'profit']
            diff = profit_b - profit_a
            pct_change = (profit_b / profit_a - 1) * 100 if profit_a != 0 else 0
            print(f"   {year:<6} {profit_a:>15,.0f} {profit_b:>15,.0f} {diff:>15,.0f} {pct_change:>9.1f}%")
        print()
    
    # 종합 평가
    print("=" * 80)
    print("🎯 종합 평가:")
    print("=" * 80)
    
    # 어느 것이 더 나은지 비교
    better_return = "[B]" if stats2['annual_return'] > stats1['annual_return'] else "[A]"
    better_trades = "[B]" if stats2['monthly_trades'] > stats1['monthly_trades'] else "[A]"
    better_holding = "[A]" if stats1['avg_holding'] < stats2['avg_holding'] else "[B]"
    better_stocks = "[B]" if stats2['unique_stocks'] > stats1['unique_stocks'] else "[A]"
    
    print(f"✅ 더 높은 연수익률: {better_return}")
    print(f"✅ 더 많은 월평균 거래: {better_trades}")
    print(f"✅ 더 짧은 보유기간: {better_holding}")
    print(f"✅ 더 많은 종목 다각화: {better_stocks}")
    print()
    
    # 주요 차이점
    return_diff = abs(stats2['annual_return'] - stats1['annual_return'])
    profit_diff = abs(stats2['total_profit'] - stats1['total_profit'])
    
    print("📌 주요 차이점:")
    if return_diff > 5:
        print(f"   • 연수익률 차이가 큼: {return_diff:.2f}%p")
    if profit_diff > 5_000_000:
        print(f"   • 총 손익 차이가 큼: {profit_diff:,.0f}원")
    if abs(stats2['monthly_trades'] - stats1['monthly_trades']) > 1:
        trade_diff = stats2['monthly_trades'] - stats1['monthly_trades']
        print(f"   • 거래 빈도 차이: {trade_diff:+.1f}건/월")
    if abs(stats2['unique_stocks'] - stats1['unique_stocks']) > 10:
        stock_diff = stats2['unique_stocks'] - stats1['unique_stocks']
        print(f"   • 종목 수 차이: {stock_diff:+d}개")
    
    print()
    print("=" * 80)

def main():
    if len(sys.argv) != 3:
        print("사용법: python compare_two_backtests.py <file1.csv> <file2.csv>")
        sys.exit(1)
    
    file1 = sys.argv[1]
    file2 = sys.argv[2]
    
    print("백테스트 파일 로딩 중...")
    stats1 = load_and_analyze(file1)
    stats2 = load_and_analyze(file2)
    
    if stats1 is None or stats2 is None:
        print("❌ 파일을 로드할 수 없거나 유효한 매도 거래가 없습니다.")
        sys.exit(1)
    
    print_comparison(stats1, stats2)

if __name__ == '__main__':
    main()
