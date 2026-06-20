# BACKTEST DOMAIN KNOWLEDGE BASE

**생성:** 2026-06-20
**대상:** `./backtest/` (백테스트 및 전략 분석 도구)

## OVERVIEW
백테스트 엔진 및 전략 분석 스크립트 모음. 과거 시세를 바탕으로 RSIStrategy의 성과를 검증하고 리스크를 측정합니다.

## FILES
| 파일 | 역할 |
|------|------|
| backtest_engine.py | 백테스트 시뮬레이션 엔진 (핵심 로직) |
| run_backtest.py | 백테스트 실행 및 결과 출력 |
| fetch_historical_data.py | 과거 시세 데이터 수집 (실행 전 필수) |
| analyze_results.py | 기본적인 백테스트 결과 분석 |
| analyze_risk_metrics.py | MDD, Sharpe Ratio 등 리스크 지표 계산 |
| analyze_profitability_focus.py | 수익성 및 매매 효율 집중 분석 |
| optimize_mdd_reduction.py | 최대 낙폭(MDD) 감소를 위한 최적화 탐색 |
| optimize_stop_loss.py | 손절매 적용 여부 및 기준점 최적화 |
| compare_results.py | 서로 다른 백테스트 결과 간의 성과 비교 |
| build_historical_universe.py | 시점별 과거 유니버스 재구축 |
| test_cumulative_rsi.py | 누적 RSI 기반 진입 전략 테스트 |
| test_mdd_reduction.py | MDD 개선 방안별 성능 테스트 |

## CONVENTIONS
- 모든 출력물은 `backtest/output/` 디렉토리에 저장합니다.
- 결과 보고서는 `README.md` 또는 마크다운 형식의 리포트로 생성합니다.
- 시간 처리는 `util.time_helper`를 사용하여 한국 시장 기준을 따릅니다.
- 데이터 캐싱은 SQLite DB와 Parquet 포맷을 사용합니다.

## KEY SELL LOGIC (2026-06-20 WALK-FORWARD OPTIMIZATION)
**매도 조건:** `RSI(2) > 70 AND close >= breakeven_price` (수익 목표 제거)

변경 전: `RSI > 85 AND close >= breakeven * 1.10` (10% 목표 수익률)
변경 후: `RSI > 70 AND close >= breakeven` (breakeven 이상 즉시 매도)
시간 손절: 비활성화 (deep loser 강제 현금화 방지)

**근거:** Walk-forward 백테스트 (2016-2026, 생존편향 제거):
- Before: -35.90% 총수익률, MDD -40.86%, 승률 65.28%
- After: +23.85% 총수익률, MDD -15.16%, 승률 94.8%
- 개선: 수익률 +59.75%p, MDD -64.3% 감소, 승률 +29.5%p

핵심 인사이트:
1. 10% 수익 목표가 deep loser(-40%)를 유발 — 작은 수익을 빨리 실현하는 것이 전체 성과에 유리
2. 시간 손절은 deep loser를 강제로 현금화하여 손실을 고정시킴
3. RSI(2)>70로 낮춰 약한 반등도 포착 — 94.8%의 압도적 승률 달성

## NOTES
- 백테스트는 `RSIStrategy`에 정의된 매개변수와 로직을 그대로 모사합니다.
- 시뮬레이션 시작 전 반드시 `fetch_historical_data.py`로 데이터를 확보하세요.
- 거래 수수료와 세금 등 실전 환경의 거래 비용을 시뮬레이션에 반영합니다.
- 최적화 스크립트는 수천 개의 조합을 테스트하므로 실행 시간이 길 수 있습니다.
- 백테스트 결과물(.png, .md)은 파일명이 겹치지 않게 타임스탬프를 포함합니다.
