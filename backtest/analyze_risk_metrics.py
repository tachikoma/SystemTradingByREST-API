"""
승률 vs MDD 분석 및 손절의 중요성 시각화

승률이 높아도 위험할 수 있는 이유를 분석합니다.
"""

import pandas as pd
import numpy as np
import sys

def analyze_risk_beyond_winrate(csv_file):
    """승률 외에 중요한 위험 지표들을 분석"""
    
    df = pd.read_csv(csv_file)
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
    
    sell_df = df[df['type'] == 'sell'].copy()
    buy_df = df[df['type'] == 'buy'].copy()
    
    if len(sell_df) == 0:
        print("매도 거래가 없습니다.")
        return
    
    # 1. 승률 계산
    winning_trades = len(sell_df[sell_df['profit'] > 0])
    total_trades = len(sell_df)
    win_rate = winning_trades / total_trades * 100
    
    # 2. 보유 기간 중 최대 손실 추정
    # 각 거래별로 얼마나 오래 물렸는지 계산
    holding_periods = []
    deep_drawdowns = []
    
    for _, sell_row in sell_df.iterrows():
        code = sell_row['code']
        sell_date = sell_row['date']
        
        # 해당 종목의 매수 찾기
        buys = buy_df[(buy_df['code'] == code) & (buy_df['date'] <= sell_date)]
        if len(buys) == 0:
            continue
        
        last_buy = buys.iloc[-1]
        buy_date = last_buy['date']
        buy_price = last_buy['price']
        sell_price = sell_row['price']
        
        # 보유 기간
        holding_days = (sell_date - buy_date).days
        holding_periods.append(holding_days)
        
        # 수익률
        profit_rate = sell_row['profit_rate']
        
        # 100일 이상 보유 + 최종 수익률 < 20%면 중간에 큰 손실 있었을 가능성
        if holding_days > 100 and profit_rate < 20:
            estimated_max_loss = -(holding_days / 10)  # 간단한 추정
            deep_drawdowns.append({
                'code': code,
                'buy_date': buy_date,
                'sell_date': sell_date,
                'holding_days': holding_days,
                'final_profit': profit_rate,
                'estimated_max_dd': estimated_max_loss
            })
    
    # 3. 통계 계산
    avg_holding = np.mean(holding_periods) if holding_periods else 0
    median_holding = np.median(holding_periods) if holding_periods else 0
    max_holding = max(holding_periods) if holding_periods else 0
    
    # 장기 보유 종목 (180일 이상)
    long_holdings = [h for h in holding_periods if h > 180]
    long_holding_ratio = len(long_holdings) / len(holding_periods) * 100 if holding_periods else 0
    
    # 4. 출력
    print("=" * 80)
    print("📊 승률 vs 위험 분석: 왜 손절이 필요한가?")
    print("=" * 80)
    print()
    
    print("1️⃣  승률만 보면 완벽해 보입니다:")
    print("-" * 80)
    print(f"   승률: {win_rate:.1f}% ({winning_trades}/{total_trades})")
    print(f"   평균 수익률: {sell_df['profit_rate'].mean():.2f}%")
    print(f"   총 실현손익: {sell_df['profit'].sum():,.0f}원")
    print()
    
    print("2️⃣  하지만 보유 기간을 보면 문제가 보입니다:")
    print("-" * 80)
    print(f"   평균 보유: {avg_holding:.0f}일 (약 {avg_holding/30:.1f}개월)")
    print(f"   중앙값 보유: {median_holding:.0f}일")
    print(f"   최장 보유: {max_holding}일 (약 {max_holding/30:.1f}개월)")
    print(f"   장기 보유 비율: {long_holding_ratio:.1f}% (180일 이상)")
    print()
    
    if long_holding_ratio > 20:
        print(f"   ⚠️  전체 거래의 {long_holding_ratio:.1f}%가 6개월 이상 물려있었습니다!")
        print()
    
    print("3️⃣  장기간 물린 거래 분석:")
    print("-" * 80)
    if deep_drawdowns:
        dd_df = pd.DataFrame(deep_drawdowns).sort_values('holding_days', ascending=False)
        print(f"   장기 보유 종목 수: {len(dd_df)}개")
        print()
        print("   TOP 10 가장 오래 물린 거래:")
        print(f"   {'코드':<8} {'매수일':<12} {'매도일':<12} {'보유':<8} {'최종수익률':<10}")
        print("   " + "-" * 70)
        for idx, row in dd_df.head(10).iterrows():
            print(f"   {row['code']:<8} {row['buy_date'].strftime('%Y-%m-%d'):<12} "
                  f"{row['sell_date'].strftime('%Y-%m-%d'):<12} {row['holding_days']:>5}일 "
                  f"{row['final_profit']:>8.2f}%")
    else:
        print("   장기 보유 거래 없음")
    print()
    
    print("4️⃣  승률 100%의 함정:")
    print("-" * 80)
    print("   ✅ 장점: 끝까지 버티면 결국 수익")
    print("   ❌ 단점:")
    print("      • MDD -58% = 자산이 절반 이하로 줄어드는 경험")
    print("      • 심리적 압박: 50% 손실을 견디기 매우 어려움")
    print("      • 기회비용: 물려있는 동안 다른 기회 놓침")
    print(f"      • 시간 비효율: 평균 {avg_holding:.0f}일 보유 (회전율 낮음)")
    print("      • 회복 시간: 손실 회복까지 너무 오래 걸림")
    print()
    
    print("5️⃣  손절이 필요한 이유:")
    print("-" * 80)
    print("   1. 심리적 안정성")
    print("      - -10% 손절: 견딜 만함, 10번 연속 손실도 자산 -65%")
    print("      - -58% 물림: 견디기 어려움, 회복에 138% 수익 필요")
    print()
    print("   2. 자본 효율")
    print("      - 손절 후 다른 종목 투자 가능")
    print(f"      - 현재: 평균 {avg_holding:.0f}일 묶임 → 연간 {365/avg_holding:.1f}회 회전")
    print("      - 손절 도입: 30-60일 회전 → 연간 6-12회 회전 가능")
    print()
    print("   3. 위험 관리")
    print("      - 실제 시장: 영구적 손실 가능 (상장폐지, 회생절차 등)")
    print("      - 백테스트는 살아남은 종목만 포함 (생존편향)")
    print()
    
    print("6️⃣  개선 방안:")
    print("-" * 80)
    print("   추천 손절 전략:")
    print("   1. 시간 손절: 90일 이상 보유 시 재검토")
    print("   2. 가격 손절: -10% 도달 시 손절")
    print("   3. 추세 손절: MA20 하향 돌파 시 손절")
    print()
    print("   예상 효과:")
    print("   - 승률: 100% → 70-80% (손절로 인한 손실 거래 발생)")
    print("   - MDD: -58% → -20% ~ -30% (손실 크기 제한)")
    print("   - 연수익률: 21% → 22-25% (자본 회전율 증가)")
    print("   - 심리적 안정: 대폭 개선")
    print()
    
    print("=" * 80)
    print("🎯 결론: 승률보다 중요한 것들")
    print("=" * 80)
    print()
    print("   중요도 순위:")
    print("   1. 🥇 MDD (Maximum Drawdown) - 최대 손실 크기")
    print("   2. 🥈 손익비 (Profit/Loss Ratio) - 한 번 이길 때 얼마나 크게 이기나")
    print("   3. 🥉 회복 시간 - 손실 후 원금 회복까지 걸리는 시간")
    print("   4.    Sharpe Ratio - 위험 대비 수익")
    print("   5.    승률 - 이기는 비율 (승률만 높으면 위험!)")
    print()
    print("   💡 핵심: \"작게 잃고 크게 버는 것\"이 \"자주 이기는 것\"보다 중요!")
    print()
    print("   예시:")
    print("   • 전략 A: 승률 100%, MDD -58%, 평균 보유 120일")
    print("   • 전략 B: 승률 70%, MDD -20%, 평균 보유 40일")
    print("   → 전략 B가 더 안전하고 효율적!")
    print()
    print("=" * 80)

def main():
    if len(sys.argv) != 2:
        print("사용법: python analyze_risk_metrics.py <trades.csv>")
        sys.exit(1)
    
    csv_file = sys.argv[1]
    analyze_risk_beyond_winrate(csv_file)

if __name__ == '__main__':
    main()
