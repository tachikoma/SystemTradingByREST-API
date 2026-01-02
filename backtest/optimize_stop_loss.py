"""
다양한 손절 파라미터 테스트
최적의 손절 조합을 찾습니다
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
configure_logging(file_name='backtest_optimize.log')
logger = get_logger(__name__)


def main():
    print("\n" + "="*80)
    print("손절 파라미터 최적화")
    print("="*80)
    print()
    
    # 데이터 로드
    print("데이터 로딩 중...")
    price_data, date_range = load_price_data_from_db('backtest_data')
    
    if not price_data:
        print("가격 데이터를 찾을 수 없습니다.")
        return
    
    # 테스트할 파라미터 조합
    test_scenarios = [
        {'name': '손절 없음', 'enabled': False, 'price': 0, 'time': 0},
        {'name': '시간만 60일', 'enabled': True, 'price': -100, 'time': 60},
        {'name': '시간만 90일', 'enabled': True, 'price': -100, 'time': 90},
        {'name': '시간만 120일', 'enabled': True, 'price': -100, 'time': 120},
        {'name': '시간만 180일', 'enabled': True, 'price': -100, 'time': 180},
        {'name': '가격만 -10%', 'enabled': True, 'price': -10, 'time': 99999},
        {'name': '가격만 -15%', 'enabled': True, 'price': -15, 'time': 99999},
        {'name': '가격만 -20%', 'enabled': True, 'price': -20, 'time': 99999},
        {'name': '복합: -10%, 120일', 'enabled': True, 'price': -10, 'time': 120},
        {'name': '복합: -15%, 120일', 'enabled': True, 'price': -15, 'time': 120},
        {'name': '복합: -20%, 180일', 'enabled': True, 'price': -20, 'time': 180},
    ]
    
    results = []
    
    for idx, scenario in enumerate(test_scenarios, 1):
        print(f"[{idx}/{len(test_scenarios)}] {scenario['name']} 테스트 중...")
        
        engine = BacktestEngine(
            enable_stop_loss=scenario['enabled'],
            price_stop_loss_pct=scenario['price'],
            time_stop_loss_days=scenario['time']
        )
        
        result = engine.run_backtest(price_data)
        
        # 위험조정 수익률 계산
        risk_adjusted = result['annual_return'] / abs(result['mdd']) if result['mdd'] != 0 else 0
        
        results.append({
            'name': scenario['name'],
            'annual_return': result['annual_return'],
            'mdd': result['mdd'],
            'sharpe': result['sharpe_ratio'],
            'win_rate': result['win_rate'],
            'trades': result['total_trades'],
            'risk_adjusted': risk_adjusted,
            'stop_count': result.get('stop_loss_count', 0)
        })
    
    # 결과 출력
    print("\n" + "="*120)
    print("📊 손절 파라미터 최적화 결과")
    print("="*120)
    print()
    
    print(f"{'전략':<20} {'연수익률':>10} {'MDD':>10} {'Sharpe':>8} {'승률':>8} {'거래':>8} {'손절':>8} {'위험조정':>10}")
    print("-"*120)
    
    for r in results:
        print(f"{r['name']:<20} {r['annual_return']:>9.2f}% {r['mdd']:>9.2f}% {r['sharpe']:>8.2f} "
              f"{r['win_rate']:>7.1f}% {r['trades']:>7,}회 {r['stop_count']:>7,}회 {r['risk_adjusted']:>10.4f}")
    
    print()
    print("="*120)
    print()
    
    # 최고 성과 찾기
    best_return = max(results, key=lambda x: x['annual_return'])
    best_risk_adjusted = max(results, key=lambda x: x['risk_adjusted'])
    best_sharpe = max(results, key=lambda x: x['sharpe'])
    best_mdd = max(results, key=lambda x: x['mdd'])  # MDD는 음수이므로 max가 더 작은 손실
    
    print("🏆 최우수 전략:")
    print()
    print(f"  • 최고 연수익률: {best_return['name']}")
    print(f"    - 연수익률: {best_return['annual_return']:.2f}%")
    print(f"    - MDD: {best_return['mdd']:.2f}%")
    print(f"    - 위험조정: {best_return['risk_adjusted']:.4f}")
    print()
    
    print(f"  • 최고 위험조정 수익률: {best_risk_adjusted['name']}")
    print(f"    - 연수익률: {best_risk_adjusted['annual_return']:.2f}%")
    print(f"    - MDD: {best_risk_adjusted['mdd']:.2f}%")
    print(f"    - 위험조정: {best_risk_adjusted['risk_adjusted']:.4f}")
    print()
    
    print(f"  • 최고 Sharpe Ratio: {best_sharpe['name']}")
    print(f"    - Sharpe: {best_sharpe['sharpe']:.2f}")
    print(f"    - 연수익률: {best_sharpe['annual_return']:.2f}%")
    print()
    
    print(f"  • 최소 MDD (가장 안전): {best_mdd['name']}")
    print(f"    - MDD: {best_mdd['mdd']:.2f}%")
    print(f"    - 연수익률: {best_mdd['annual_return']:.2f}%")
    print()
    
    print("="*120)
    print()
    
    print("💡 추천:")
    print()
    if best_risk_adjusted == results[0]:  # 손절 없음이 최고면
        print("  ⚠️  손절 전략이 백테스트에서는 효과가 없습니다.")
        print("     하지만 실전에서는 다음 이유로 손절 필수:")
        print("     1. 상장폐지 위험 (백테스트는 생존편향)")
        print("     2. 레버리지 사용 시 청산 위험")
        print("     3. 심리적 안정성")
        print()
        print(f"     대안: 보수적 손절 ({best_mdd['name']} 또는 가격 -15% + 시간 120일)")
    else:
        print(f"  ✅ 추천 전략: {best_risk_adjusted['name']}")
        print(f"     - 위험 대비 수익이 가장 효율적입니다")
        print(f"     - 실전 적용을 권장합니다")
    
    print()
    print("="*120)


if __name__ == '__main__':
    main()
