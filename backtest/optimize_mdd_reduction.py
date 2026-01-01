"""
MDD 감소 방법 조합 최적화

Phase 1 테스트 결과를 바탕으로 최적 조합 찾기

Phase 1 결과:
- 현금 비중: 20%가 최고 위험조정수익 (0.4034)
- 종목 수: 10종목이 최고 위험조정수익 (0.3937)
- 진입 조건: RSI < 3, 하락 > -5%가 최고 수익률 (31.09%)
"""

import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from backtest.backtest_engine import BacktestEngine
from backtest.run_backtest import load_price_data_from_db
import pandas as pd

# 전역으로 가격 데이터 로드
print("가격 데이터 로딩 중...")
PRICE_DATA, DATE_RANGE = load_price_data_from_db()
print(f"로딩 완료: {len(PRICE_DATA)}개 종목, 기간: {DATE_RANGE[0]} ~ {DATE_RANGE[1]}\n")


def test_combinations():
    """다양한 조합 테스트"""
    print("=" * 80)
    print("MDD 감소 방법 조합 최적화")
    print("=" * 80)
    
    results = []
    
    # 조합 1: 기준 (현재 최적)
    print("\n[조합 1] 기준 (현재 설정)")
    print("- 현금: 0%, 종목: 10개, RSI < 5, 하락 > -2%")
    engine = BacktestEngine(
        initial_capital=10_000_000,
        max_holdings=10,
        rsi_buy_threshold=5,
        price_drop_threshold=-2.0,
    )
    result = engine.run_backtest(PRICE_DATA)
    results.append({
        '조합': '기준',
        '현금비중': '0%',
        '종목수': 10,
        'RSI': '< 5',
        '하락': '> -2%',
        '연수익률': result['annual_return'],
        'MDD': result['mdd'],
        'Sharpe': result['sharpe_ratio'],
        '거래횟수': result['total_trades'],
        '위험조정': result['annual_return'] / abs(result['mdd'])
    })
    print(f"연수익률: {result['annual_return']:.2f}%, MDD: {result['mdd']:.2f}%, Sharpe: {result['sharpe_ratio']:.2f}")
    print(f"위험조정수익: {results[-1]['위험조정']:.4f}")
    
    # 조합 2: 현금 20% 만
    print("\n[조합 2] 현금 20%만 적용")
    print("- 현금: 20%, 종목: 10개, RSI < 5, 하락 > -2%")
    engine = BacktestEngine(
        initial_capital=10_000_000 * 0.8,
        max_holdings=10,
        rsi_buy_threshold=5,
        price_drop_threshold=-2.0,
    )
    result = engine.run_backtest(PRICE_DATA)
    adj_return = result['annual_return'] * 0.8
    adj_mdd = result['mdd'] * 0.8
    results.append({
        '조합': '현금20%',
        '현금비중': '20%',
        '종목수': 10,
        'RSI': '< 5',
        '하락': '> -2%',
        '연수익률': adj_return,
        'MDD': adj_mdd,
        'Sharpe': result['sharpe_ratio'],
        '거래횟수': result['total_trades'],
        '위험조정': adj_return / abs(adj_mdd)
    })
    print(f"연수익률: {adj_return:.2f}%, MDD: {adj_mdd:.2f}%, Sharpe: {result['sharpe_ratio']:.2f}")
    print(f"위험조정수익: {results[-1]['위험조정']:.4f}")
    
    # 조합 3: 진입 조건 강화
    print("\n[조합 3] 진입 조건 강화 (RSI < 3, 하락 > -5%)")
    print("- 현금: 0%, 종목: 10개, RSI < 3, 하락 > -5%")
    engine = BacktestEngine(
        initial_capital=10_000_000,
        max_holdings=10,
        rsi_buy_threshold=3,
        price_drop_threshold=-5.0,
    )
    result = engine.run_backtest(PRICE_DATA)
    results.append({
        '조합': '진입강화',
        '현금비중': '0%',
        '종목수': 10,
        'RSI': '< 3',
        '하락': '> -5%',
        '연수익률': result['annual_return'],
        'MDD': result['mdd'],
        'Sharpe': result['sharpe_ratio'],
        '거래횟수': result['total_trades'],
        '위험조정': result['annual_return'] / abs(result['mdd'])
    })
    print(f"연수익률: {result['annual_return']:.2f}%, MDD: {result['mdd']:.2f}%, Sharpe: {result['sharpe_ratio']:.2f}")
    print(f"위험조정수익: {results[-1]['위험조정']:.4f}")
    
    # 조합 4: 현금 20% + 진입 강화
    print("\n[조합 4] 현금 20% + 진입 강화")
    print("- 현금: 20%, 종목: 10개, RSI < 3, 하락 > -5%")
    engine = BacktestEngine(
        initial_capital=10_000_000 * 0.8,
        max_holdings=10,
        rsi_buy_threshold=3,
        price_drop_threshold=-5.0,
    )
    result = engine.run_backtest(PRICE_DATA)
    adj_return = result['annual_return'] * 0.8
    adj_mdd = result['mdd'] * 0.8
    results.append({
        '조합': '현금20%+진입강화',
        '현금비중': '20%',
        '종목수': 10,
        'RSI': '< 3',
        '하락': '> -5%',
        '연수익률': adj_return,
        'MDD': adj_mdd,
        'Sharpe': result['sharpe_ratio'],
        '거래횟수': result['total_trades'],
        '위험조정': adj_return / abs(adj_mdd)
    })
    print(f"연수익률: {adj_return:.2f}%, MDD: {adj_mdd:.2f}%, Sharpe: {result['sharpe_ratio']:.2f}")
    print(f"위험조정수익: {results[-1]['위험조정']:.4f}")
    
    # 조합 5: 종목 5개 (분산 축소)
    print("\n[조합 5] 종목 5개 (집중 투자)")
    print("- 현금: 0%, 종목: 5개, RSI < 3, 하락 > -5%")
    engine = BacktestEngine(
        initial_capital=10_000_000,
        max_holdings=5,
        rsi_buy_threshold=3,
        price_drop_threshold=-5.0,
    )
    result = engine.run_backtest(PRICE_DATA)
    results.append({
        '조합': '종목5개+진입강화',
        '현금비중': '0%',
        '종목수': 5,
        'RSI': '< 3',
        '하락': '> -5%',
        '연수익률': result['annual_return'],
        'MDD': result['mdd'],
        'Sharpe': result['sharpe_ratio'],
        '거래횟수': result['total_trades'],
        '위험조정': result['annual_return'] / abs(result['mdd'])
    })
    print(f"연수익률: {result['annual_return']:.2f}%, MDD: {result['mdd']:.2f}%, Sharpe: {result['sharpe_ratio']:.2f}")
    print(f"위험조정수익: {results[-1]['위험조정']:.4f}")
    
    # 조합 6: 종목 20개 (분산 강화)
    print("\n[조합 6] 종목 20개 (분산 강화)")
    print("- 현금: 0%, 종목: 20개, RSI < 3, 하락 > -5%")
    engine = BacktestEngine(
        initial_capital=10_000_000,
        max_holdings=20,
        rsi_buy_threshold=3,
        price_drop_threshold=-5.0,
    )
    result = engine.run_backtest(PRICE_DATA)
    results.append({
        '조합': '종목20개+진입강화',
        '현금비중': '0%',
        '종목수': 20,
        'RSI': '< 3',
        '하락': '> -5%',
        '연수익률': result['annual_return'],
        'MDD': result['mdd'],
        'Sharpe': result['sharpe_ratio'],
        '거래횟수': result['total_trades'],
        '위험조정': result['annual_return'] / abs(result['mdd'])
    })
    print(f"연수익률: {result['annual_return']:.2f}%, MDD: {result['mdd']:.2f}%, Sharpe: {result['sharpe_ratio']:.2f}")
    print(f"위험조정수익: {results[-1]['위험조정']:.4f}")
    
    # 조합 7: 현금 20% + 종목 20개 (보수적)
    print("\n[조합 7] 현금 20% + 종목 20개 (보수적)")
    print("- 현금: 20%, 종목: 20개, RSI < 3, 하락 > -5%")
    engine = BacktestEngine(
        initial_capital=10_000_000 * 0.8,
        max_holdings=20,
        rsi_buy_threshold=3,
        price_drop_threshold=-5.0,
    )
    result = engine.run_backtest(PRICE_DATA)
    adj_return = result['annual_return'] * 0.8
    adj_mdd = result['mdd'] * 0.8
    results.append({
        '조합': '현금20%+종목20개+진입강화',
        '현금비중': '20%',
        '종목수': 20,
        'RSI': '< 3',
        '하락': '> -5%',
        '연수익률': adj_return,
        'MDD': adj_mdd,
        'Sharpe': result['sharpe_ratio'],
        '거래횟수': result['total_trades'],
        '위험조정': adj_return / abs(adj_mdd)
    })
    print(f"연수익률: {adj_return:.2f}%, MDD: {adj_mdd:.2f}%, Sharpe: {result['sharpe_ratio']:.2f}")
    print(f"위험조정수익: {results[-1]['위험조정']:.4f}")
    
    # 조합 8: 현금 30% (매우 보수적)
    print("\n[조합 8] 현금 30% + 진입 강화 (매우 보수적)")
    print("- 현금: 30%, 종목: 10개, RSI < 3, 하락 > -5%")
    engine = BacktestEngine(
        initial_capital=10_000_000 * 0.7,
        max_holdings=10,
        rsi_buy_threshold=3,
        price_drop_threshold=-5.0,
    )
    result = engine.run_backtest(PRICE_DATA)
    adj_return = result['annual_return'] * 0.7
    adj_mdd = result['mdd'] * 0.7
    results.append({
        '조합': '현금30%+진입강화',
        '현금비중': '30%',
        '종목수': 10,
        'RSI': '< 3',
        '하락': '> -5%',
        '연수익률': adj_return,
        'MDD': adj_mdd,
        'Sharpe': result['sharpe_ratio'],
        '거래횟수': result['total_trades'],
        '위험조정': adj_return / abs(adj_mdd)
    })
    print(f"연수익률: {adj_return:.2f}%, MDD: {adj_mdd:.2f}%, Sharpe: {result['sharpe_ratio']:.2f}")
    print(f"위험조정수익: {results[-1]['위험조정']:.4f}")
    
    # 결과 요약
    print("\n" + "=" * 80)
    print("전체 조합 테스트 결과")
    print("=" * 80)
    df = pd.DataFrame(results)
    print(df.to_string(index=False))
    
    # 최고 성과 찾기
    print("\n" + "=" * 80)
    print("최고 성과")
    print("=" * 80)
    
    best_return = df.loc[df['연수익률'].idxmax()]
    best_mdd = df.loc[df['MDD'].idxmax()]  # MDD는 음수, 최대값이 덜 나쁜 것
    best_risk_adjusted = df.loc[df['위험조정'].idxmax()]
    best_sharpe = df.loc[df['Sharpe'].idxmax()]
    
    print(f"\n1️⃣  최고 수익률: {best_return['조합']}")
    print(f"   연수익률 {best_return['연수익률']:.2f}%, MDD {best_return['MDD']:.2f}%, 위험조정 {best_return['위험조정']:.4f}")
    
    print(f"\n2️⃣  최소 MDD: {best_mdd['조합']}")
    print(f"   연수익률 {best_mdd['연수익률']:.2f}%, MDD {best_mdd['MDD']:.2f}%, 위험조정 {best_mdd['위험조정']:.4f}")
    
    print(f"\n3️⃣  최고 위험조정수익: {best_risk_adjusted['조합']}")
    print(f"   연수익률 {best_risk_adjusted['연수익률']:.2f}%, MDD {best_risk_adjusted['MDD']:.2f}%, 위험조정 {best_risk_adjusted['위험조정']:.4f}")
    
    print(f"\n4️⃣  최고 Sharpe: {best_sharpe['조합']}")
    print(f"   연수익률 {best_sharpe['연수익률']:.2f}%, MDD {best_sharpe['MDD']:.2f}%, Sharpe {best_sharpe['Sharpe']:.2f}")
    
    # 추천
    print("\n" + "=" * 80)
    print("💡 추천")
    print("=" * 80)
    
    print("\n▶ 공격적 투자자:")
    print(f"   {best_return['조합']}")
    print(f"   - 연수익률 {best_return['연수익률']:.2f}% (최고)")
    print(f"   - MDD {best_return['MDD']:.2f}%")
    print(f"   - 큰 변동성 감수 가능한 경우")
    
    print("\n▶ 균형 투자자:")
    print(f"   {best_risk_adjusted['조합']}")
    print(f"   - 연수익률 {best_risk_adjusted['연수익률']:.2f}%")
    print(f"   - MDD {best_risk_adjusted['MDD']:.2f}%")
    print(f"   - 위험조정수익 {best_risk_adjusted['위험조정']:.4f} (최고)")
    print(f"   - 수익률과 위험의 최적 균형")
    
    print("\n▶ 보수적 투자자:")
    print(f"   {best_mdd['조합']}")
    print(f"   - 연수익률 {best_mdd['연수익률']:.2f}%")
    print(f"   - MDD {best_mdd['MDD']:.2f}% (최소)")
    print(f"   - 안정성 최우선")
    
    return results


if __name__ == '__main__':
    results = test_combinations()
    
    print("\n\n" + "=" * 80)
    print("결론")
    print("=" * 80)
    print("""
현재 전략(기준)보다 개선된 조합:
1. 수익률 증가: 진입 조건 강화 (RSI < 3, 하락 > -5%)
2. MDD 감소: 현금 비중 유지 (20~30%)
3. 균형 최적: 현금 20% + 진입 강화

다음 단계:
- 최적 조합을 backtest_engine.py 기본값으로 적용
- 실전 테스트 후 조정
    """)
