"""백테스트 결과 상세 분석 스크립트"""

import pandas as pd
import numpy as np
from datetime import datetime
import sys

def analyze_backtest_results(csv_file):
    """백테스트 결과 CSV 파일 분석"""
    
    # CSV 파일 로드
    df = pd.read_csv(csv_file)
    
    # 날짜를 datetime으로 변환
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
    
    # 매도 거래만 필터링 (수익률 분석)
    sells = df[df['type'] == 'sell'].copy()
    buys = df[df['type'] == 'buy'].copy()
    
    print("="*70)
    print("📊 변동성 Universe 백테스트 상세 분석")
    print("="*70)
    
    # 1. 데이터 기간
    print(f"\n📅 데이터 기간:")
    print(f"   시작일: {df['date'].min().strftime('%Y-%m-%d')}")
    print(f"   종료일: {df['date'].max().strftime('%Y-%m-%d')}")
    days = (df['date'].max() - df['date'].min()).days
    print(f"   기간: {days}일 ({days/365:.1f}년)")
    
    # 2. 거래 통계
    print(f"\n📈 거래 통계:")
    print(f"   총 거래: {len(df)}건 (매수: {len(buys)}건, 매도: {len(sells)}건)")
    print(f"   거래 종목 수: {buys['code'].nunique()}개")
    print(f"   평균 수익률: {sells['profit_rate'].mean():.2f}%")
    print(f"   중앙값 수익률: {sells['profit_rate'].median():.2f}%")
    print(f"   표준편차: {sells['profit_rate'].std():.2f}%")
    print(f"   최대 수익률: {sells['profit_rate'].max():.2f}%")
    print(f"   최소 수익률: {sells['profit_rate'].min():.2f}%")
    
    # 3. 수익률 분포
    print(f"\n💰 수익률 분포:")
    bins = [0, 5, 10, 15, 20, 100]
    labels = ['0-5%', '5-10%', '10-15%', '15-20%', '20%+']
    sells['rate_bin'] = pd.cut(sells['profit_rate'], bins=bins, labels=labels)
    dist = sells['rate_bin'].value_counts().sort_index()
    for label, count in dist.items():
        pct = count / len(sells) * 100
        bar = '█' * int(pct / 2)
        print(f"   {label:8s}: {count:3d}건 ({pct:5.1f}%) {bar}")
    
    # 4. 연도별 성과
    print(f"\n📆 연도별 성과:")
    sells['year'] = sells['date'].dt.year
    yearly = sells.groupby('year').agg({
        'profit_rate': ['count', 'mean', 'sum'],
        'profit': 'sum'
    }).round(2)
    yearly.columns = ['거래수', '평균수익률(%)', '누적수익률(%)', '실현손익']
    print(yearly.to_string())
    
    # 5. 월별 거래 빈도
    print(f"\n📅 월별 거래 빈도:")
    buys['year_month'] = buys['date'].dt.to_period('M')
    monthly_trades = buys.groupby('year_month').size()
    print(f"   평균: {monthly_trades.mean():.1f}건/월")
    print(f"   최대: {monthly_trades.max()}건/월 ({monthly_trades.idxmax()})")
    print(f"   최소: {monthly_trades.min()}건/월 ({monthly_trades.idxmin()})")
    
    # 6. 보유 기간 분석
    print(f"\n⏱️  보유 기간 분석:")
    # 매수-매도 매칭
    buy_dict = {}
    for _, row in buys.iterrows():
        key = (row['code'], row['date'])
        buy_dict[key] = row['date']
    
    hold_days = []
    for _, row in sells.iterrows():
        code = row['code']
        sell_date = row['date']
        # 해당 종목의 가장 최근 매수일 찾기
        buy_dates = [d for (c, d) in buy_dict.keys() if c == code and d <= sell_date]
        if buy_dates:
            buy_date = max(buy_dates)
            hold_days.append((sell_date - buy_date).days)
    
    if hold_days:
        print(f"   평균 보유: {np.mean(hold_days):.1f}일")
        print(f"   중앙값: {np.median(hold_days):.0f}일")
        print(f"   최소: {min(hold_days)}일")
        print(f"   최대: {max(hold_days)}일")
    
    # 7. 상위 수익 종목
    print(f"\n🏆 상위 수익 종목 TOP 10:")
    top_stocks = sells.nlargest(10, 'profit_rate')[['date', 'code', 'profit', 'profit_rate']]
    for idx, row in top_stocks.iterrows():
        print(f"   {row['date'].strftime('%Y-%m-%d')} {row['code']}: "
              f"{row['profit']:>10,.0f}원 ({row['profit_rate']:>6.2f}%)")
    
    # 8. 분기별 성과
    print(f"\n📊 분기별 성과:")
    sells['quarter'] = sells['date'].dt.to_period('Q')
    quarterly = sells.groupby('quarter').agg({
        'profit_rate': ['count', 'mean'],
        'profit': 'sum'
    }).round(2)
    quarterly.columns = ['거래수', '평균수익률(%)', '실현손익']
    print(quarterly.tail(12).to_string())
    
    # 9. 손실 거래 분석
    losses = sells[sells['profit_rate'] < 0]
    print(f"\n⚠️  손실 거래 분석:")
    if len(losses) > 0:
        print(f"   손실 거래: {len(losses)}건 ({len(losses)/len(sells)*100:.2f}%)")
        print(f"   평균 손실: {losses['profit_rate'].mean():.2f}%")
        print(f"   최대 손실: {losses['profit_rate'].min():.2f}%")
    else:
        print(f"   손실 거래: 0건 (100% 승률!)")
    
    # 10. 종목별 거래 횟수
    print(f"\n🔢 거래 빈도 TOP 10 종목:")
    stock_counts = buys['code'].value_counts().head(10)
    for code, count in stock_counts.items():
        print(f"   {code}: {count}회")
    
    print("\n" + "="*70)


if __name__ == '__main__':
    csv_file = 'backtest/output/trades_20260101_152001.csv'
    if len(sys.argv) > 1:
        csv_file = sys.argv[1]
    
    analyze_backtest_results(csv_file)
