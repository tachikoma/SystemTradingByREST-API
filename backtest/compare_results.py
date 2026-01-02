"""백테스트 결과 Before/After 비교 분석"""

import pandas as pd
import numpy as np

print("="*80)
print("🔍 Universe 변경 전후 비교 분석")
print("="*80)

# Before: 가치주 Universe (2024년 1년)
before = {
    'period': '2024년 (1년)',
    'universe': '가치주 (저PER + 고ROE)',
    'total_return': 12.20,
    'annual_return': 12.62,
    'sharpe': 0.63,
    'mdd': -17.88,
    'total_trades': 100,
    'buy_trades': 55,
    'sell_trades': 45,
    'win_rate': 100.0,
    'avg_return': 8.34,
    'monthly_avg': 3.7,
    'unique_stocks': 33
}

# After: 변동성 Universe (10년)
after = {
    'period': '2016-2025 (9.7년)',
    'universe': '변동성주 (변동성 + 거래활발도)',
    'total_return': 470.11,
    'annual_return': 20.05,
    'sharpe': 0.87,
    'mdd': -58.25,
    'total_trades': 428,
    'buy_trades': 219,
    'sell_trades': 209,
    'win_rate': 100.0,
    'avg_return': 7.55,
    'monthly_avg': 3.0,
    'unique_stocks': 64
}

print("\n" + "="*80)
print("📊 핵심 지표 비교")
print("="*80)

print(f"\n{'지표':20s} {'Before (가치주)':>25s} {'After (변동성주)':>25s} {'변화':>10s}")
print("-"*80)

# 수익률 비교
print(f"{'연환산 수익률':20s} {before['annual_return']:>24.2f}% {after['annual_return']:>24.2f}% {after['annual_return']-before['annual_return']:>9.2f}%p")

# 샤프 비율
change = ((after['sharpe'] / before['sharpe'] - 1) * 100)
print(f"{'샤프 비율':20s} {before['sharpe']:>25.2f} {after['sharpe']:>25.2f} {change:>9.1f}%")

# MDD
print(f"{'MDD':20s} {before['mdd']:>24.2f}% {after['mdd']:>24.2f}% {after['mdd']-before['mdd']:>9.2f}%p")

# 거래 빈도
print(f"{'월평균 거래':20s} {before['monthly_avg']:>24.1f}건 {after['monthly_avg']:>24.1f}건 {after['monthly_avg']-before['monthly_avg']:>9.1f}건")

# 평균 수익률
print(f"{'평균 수익률':20s} {before['avg_return']:>24.2f}% {after['avg_return']:>24.2f}% {after['avg_return']-before['avg_return']:>9.2f}%p")

# 거래 종목 수
change = ((after['unique_stocks'] / before['unique_stocks'] - 1) * 100)
print(f"{'거래 종목 수':20s} {before['unique_stocks']:>24d}개 {after['unique_stocks']:>24d}개 {change:>9.1f}%")

print("\n" + "="*80)
print("✅ 주요 개선 사항")
print("="*80)

improvements = [
    ("연환산 수익률", f"{before['annual_return']:.2f}% → {after['annual_return']:.2f}%", 
     f"+{after['annual_return']-before['annual_return']:.2f}%p ({(after['annual_return']/before['annual_return']-1)*100:.1f}% 증가)"),
    
    ("샤프 비율", f"{before['sharpe']:.2f} → {after['sharpe']:.2f}",
     f"위험 조정 수익률 {((after['sharpe']/before['sharpe']-1)*100):.1f}% 개선"),
    
    ("거래 다각화", f"{before['unique_stocks']}개 → {after['unique_stocks']}개 종목",
     f"포트폴리오 분산 {((after['unique_stocks']/before['unique_stocks']-1)*100):.1f}% 증가"),
    
    ("승률", "100% → 100%", "안정적 수익 구조 유지"),
]

for idx, (metric, change, desc) in enumerate(improvements, 1):
    print(f"\n{idx}. {metric}")
    print(f"   • {change}")
    print(f"   • {desc}")

print("\n" + "="*80)
print("⚠️  주의사항")
print("="*80)

warnings = [
    ("MDD 증가", f"{before['mdd']:.2f}% → {after['mdd']:.2f}%",
     "변동성이 큰 종목으로 인해 최대 낙폭 증가\n   → 리스크 관리 강화 필요 (손절 규칙, 포지션 사이징)"),
    
    ("장기 데이터 필요", "10년 vs 1년 비교",
     "다양한 시장 환경 포함으로 더 신뢰할 수 있는 결과\n   → Before는 2024년 한 해만의 결과로 제한적"),
    
    ("월평균 거래 감소", f"{before['monthly_avg']:.1f}건 → {after['monthly_avg']:.1f}건",
     "변동성 종목이지만 RSI < 5 신호는 여전히 제한적\n   → 추가 최적화 여지 있음"),
]

for idx, (issue, change, desc) in enumerate(warnings, 1):
    print(f"\n{idx}. {issue}")
    print(f"   • {change}")
    print(f"   • {desc}")

print("\n" + "="*80)
print("🎯 결론")
print("="*80)

print("""
1. **수익성 대폭 개선**
   - 연환산 수익률: 12.62% → 20.05% (+7.43%p, 59% 증가)
   - 장기적으로 안정적인 초과 수익 달성

2. **위험 조정 수익률 향상**
   - 샤프 비율: 0.63 → 0.87 (+38% 개선)
   - 변동성 대비 수익이 더 효율적으로 개선

3. **포트폴리오 다각화**
   - 거래 종목: 33개 → 64개 (+94% 증가)
   - 특정 종목 의존도 감소, 분산 투자 효과

4. **리스크 관리 필요**
   - MDD: -17.88% → -58.25%
   - 변동성 종목의 특성상 낙폭 확대
   - 손절 규칙, 포지션 사이징 등 리스크 관리 강화 권장

5. **추가 최적화 기회**
   - 월평균 거래: 3.0건 (여전히 낮음)
   - RSI 파라미터 조정 (기간, 임계값)
   - 추가 매매 신호 (볼린저밴드, MACD 등) 결합 고려
""")

print("="*80)
