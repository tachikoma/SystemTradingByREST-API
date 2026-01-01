"""
손절 있음 vs 없음 백테스트 비교
"""

import sys
from pathlib import Path

# 프로젝트 루트를 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from backtest.backtest_engine import BacktestEngine
from backtest.run_backtest import load_price_data_from_db
from util.logging_config import configure_logging, get_logger

# 로깅 설정
configure_logging(file_name='backtest_comparison.log')
logger = get_logger(__name__)


def main():
    print("\n" + "="*80)
    print("손절 전략 비교 백테스트")
    print("="*80)
    print()
    
    # 데이터 로드
    print("데이터 로딩 중...")
    price_data, date_range = load_price_data_from_db('backtest_data')
    
    if not price_data:
        print("가격 데이터를 찾을 수 없습니다.")
        return
    
    print(f"데이터 기간: {date_range[0]} ~ {date_range[1]}")
    print()
    
    # 1. 손절 없는 백테스트
    print("[ 1 ] 손절 없는 전략 백테스트 실행 중...")
    engine_no_stop = BacktestEngine(
        enable_stop_loss=False
    )
    results_no_stop = engine_no_stop.run_backtest(price_data)
    
    # 2. 손절 있는 백테스트 (가격 -5% + 시간 120일)
    print("[ 2 ] 손절 전략 (-5%, 120일) 백테스트 실행 중...")
    engine_with_stop = BacktestEngine(
        enable_stop_loss=True,
        price_stop_loss_pct=-5.0,
        time_stop_loss_days=120
    )
    results_with_stop = engine_with_stop.run_backtest(price_data)
    
    # 결과 비교 출력
    print("\n" + "="*80)
    print("📊 백테스트 결과 비교")
    print("="*80)
    print()
    
    print(f"{'지표':<20} {'손절 없음':>20} {'손절 있음':>20} {'차이':>15}")
    print("-"*80)
    
    # 수익률 비교
    print(f"{'연평균 수익률':<20} {results_no_stop['annual_return']:>19.2f}% {results_with_stop['annual_return']:>19.2f}% "
          f"{results_with_stop['annual_return'] - results_no_stop['annual_return']:>14.2f}%p")
    
    # MDD 비교
    print(f"{'MDD':<20} {results_no_stop['mdd']:>19.2f}% {results_with_stop['mdd']:>19.2f}% "
          f"{results_with_stop['mdd'] - results_no_stop['mdd']:>14.2f}%p")
    
    # Sharpe Ratio 비교
    print(f"{'Sharpe Ratio':<20} {results_no_stop['sharpe_ratio']:>20.2f} {results_with_stop['sharpe_ratio']:>20.2f} "
          f"{results_with_stop['sharpe_ratio'] - results_no_stop['sharpe_ratio']:>+15.2f}")
    
    # 승률 비교
    print(f"{'승률':<20} {results_no_stop['win_rate']:>19.2f}% {results_with_stop['win_rate']:>19.2f}% "
          f"{results_with_stop['win_rate'] - results_no_stop['win_rate']:>14.2f}%p")
    
    # 거래 횟수 비교
    print(f"{'총 거래 횟수':<20} {results_no_stop['total_trades']:>20,} {results_with_stop['total_trades']:>20,} "
          f"{results_with_stop['total_trades'] - results_no_stop['total_trades']:>+15,}")
    
    # 손절 정보
    if results_with_stop.get('stop_loss_enabled'):
        print()
        print("손절 상세 정보:")
        print(f"  • 손절 횟수: {results_with_stop.get('stop_loss_count', 0):,}회")
        print(f"  • 손절 비율: {results_with_stop.get('stop_loss_count', 0) / results_with_stop['sell_trades'] * 100:.2f}%")
        print(f"  • 가격 손절: {results_with_stop.get('price_stop_loss_pct', 0):.1f}%")
        print(f"  • 시간 손절: {results_with_stop.get('time_stop_loss_days', 0)}일")
    
    print()
    print("="*80)
    print("🎯 종합 평가")
    print("="*80)
    print()
    
    # 개선 여부 판단
    improvements = []
    warnings = []
    
    if results_with_stop['annual_return'] > results_no_stop['annual_return']:
        improvements.append(f"✅ 연수익률 {results_with_stop['annual_return'] - results_no_stop['annual_return']:+.2f}%p 향상")
    else:
        warnings.append(f"⚠️  연수익률 {results_with_stop['annual_return'] - results_no_stop['annual_return']:.2f}%p 감소")
    
    if results_with_stop['mdd'] > results_no_stop['mdd']:  # MDD는 음수이므로 > 면 개선
        improvements.append(f"✅ MDD {abs(results_with_stop['mdd'] - results_no_stop['mdd']):.2f}%p 개선 (위험 감소)")
    else:
        warnings.append(f"⚠️  MDD {abs(results_with_stop['mdd'] - results_no_stop['mdd']):.2f}%p 악화")
    
    if results_with_stop['sharpe_ratio'] > results_no_stop['sharpe_ratio']:
        improvements.append(f"✅ Sharpe Ratio {results_with_stop['sharpe_ratio'] - results_no_stop['sharpe_ratio']:+.2f} 향상")
    else:
        warnings.append(f"⚠️  Sharpe Ratio {results_with_stop['sharpe_ratio'] - results_no_stop['sharpe_ratio']:.2f} 감소")
    
    if improvements:
        print("개선 사항:")
        for imp in improvements:
            print(f"  {imp}")
        print()
    
    if warnings:
        print("주의 사항:")
        for warn in warnings:
            print(f"  {warn}")
        print()
    
    # 최종 추천
    print("💡 최종 평가:")
    
    # 위험조정 수익률로 판단
    risk_adjusted_no_stop = results_no_stop['annual_return'] / abs(results_no_stop['mdd']) if results_no_stop['mdd'] != 0 else 0
    risk_adjusted_with_stop = results_with_stop['annual_return'] / abs(results_with_stop['mdd']) if results_with_stop['mdd'] != 0 else 0
    
    print(f"  • 위험조정 수익률 (연수익률/MDD)")
    print(f"    - 손절 없음: {risk_adjusted_no_stop:.4f}")
    print(f"    - 손절 있음: {risk_adjusted_with_stop:.4f}")
    print()
    
    if risk_adjusted_with_stop > risk_adjusted_no_stop:
        print("  ✅ 손절 전략이 위험 대비 더 효율적입니다!")
        print("     실전 적용을 권장합니다.")
    else:
        print("  ⚠️  손절 전략이 위험 대비 효율이 낮습니다.")
        print("     파라미터 조정이 필요합니다.")
    
    print()
    print("="*80)


if __name__ == '__main__':
    main()
