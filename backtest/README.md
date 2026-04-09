# RSI 전략 백테스트

RSIStrategy의 매매 로직을 재현하여 과거 데이터로 백테스트를 수행하는 프레임워크입니다.

## 🎯 최적 전략 적용 (2026-01-01 업데이트)

**현재 적용된 전략: 기존 RSI + 현금 20% + 진입 조건 강화**

```python
BacktestEngine(
    initial_capital=10_000_000,   # 초기 자본금 (env: INITIAL_CAPITAL)
    cash_reserve_ratio=0.2,       # 현금 20% 유지
    rsi_sell_threshold=85,        # 매도 기준 강화
    rsi_buy_threshold=3,          # 5 → 3 (더 강한 과매도)
    price_drop_threshold=-5.0,    # -2% → -5% (더 큰 하락)
    rsi_method='wilder',          # 현재 기본 RSI 방식
    time_stop_loss_days=90,       # 90일 초과 보유 시 청산
)
```

**성과 (2016-2025, 9.7년):**
- 연수익률: **25.53%** (기존 21.71% 대비 +3.82%p)
- MDD: **-49.35%** (기존 -55.15% 대비 +5.80%p 개선)
- Sharpe: **0.98** (기존 0.84 대비 +0.14)
- 위험조정수익: **0.5175** (기존 0.3937 대비 +31% 개선)

자세한 내용: [MDD_REDUCTION_FINAL_RECOMMENDATION.md](MDD_REDUCTION_FINAL_RECOMMENDATION.md)

## 📋 기능

- **전략 시뮬레이션**: RSI 기반 매매 전략을 과거 데이터로 시뮬레이션
- **성과 분석**: 수익률, 샤프 비율, MDD 등 다양한 지표 계산
- **시각화**: 포트폴리오 가치 변화, 보유 종목 수, 누적 수익률 그래프
- **거래 내역 추출**: 모든 매매 기록을 CSV로 저장
- **MDD 감소 기법**: 현금 비중, 진입 조건 강화 등 다양한 방법 제공
- **Cumulative RSI**: Larry Connors의 누적 RSI 전략 지원

## 🚀 사용법

### 1. 기본 실행

DB에 저장된 유니버스와 가격 데이터를 사용하여 백테스트를 실행합니다:

```bash
# 프로젝트 루트 디렉토리에서 실행
# 기본: DB의 전체 데이터 기간 사용
poetry run python -m backtest.run_backtest

# 최근 5년 데이터만 사용
poetry run python -m backtest.run_backtest --years 5

# 특정 기간 지정
poetry run python -m backtest.run_backtest --start 20200101 --end 20231231

# 다른 DB 사용
poetry run python -m backtest.run_backtest --db my_backtest_data
```

### 파라미터 옵션

- `--years N`: 최근 N년 데이터 사용 (예: `--years 5`)
- `--start YYYYMMDD`: 백테스트 시작 날짜 (예: `--start 20200101`)
- `--end YYYYMMDD`: 백테스트 종료 날짜 (예: `--end 20231231`, 기본값: 오늘)
- `--db DB_NAME`: 사용할 DB 이름 (기본값: `backtest_data`)

**참고**: 파라미터를 지정하지 않으면 DB에 저장된 데이터의 전체 기간을 사용합니다.

### 2. 프로그램적 사용

Python 코드에서 직접 백테스트를 설정하고 실행할 수 있습니다:

```python
from backtest import BacktestEngine
import pandas as pd

# 가격 데이터 준비 (예시)
price_data = {
    '005930': pd.DataFrame({
        'open': [...],
        'high': [...],
        'low': [...],
        'close': [...],
        'volume': [...]
    }, index=['20230101', '20230102', ...]),
    # 다른 종목들...
}

# 백테스트 엔진 생성
engine = BacktestEngine(
    initial_capital=10_000_000,  # 초기 자본금 (원)
    max_holdings=10,              # 최대 보유 종목 수
    rsi_period=2,                 # RSI 계산 기간
    ma_short=20,                  # 단기 이동평균
    ma_long=60,                   # 장기 이동평균
    cash_reserve_ratio=0.2,       # 현금 보유 비율
    rsi_sell_threshold=85,        # RSI 매도 기준
    rsi_buy_threshold=3,          # RSI 매수 기준
    price_drop_threshold=-5.0,    # 가격 하락 기준 (%)
    rsi_method='wilder',          # RSI 계산 방식
    commission_rate=0.00015,      # 거래 수수료율
    tax_rate=0.0020,              # 거래세 (매도 시)
    time_stop_loss_days=90        # 시간 손절 기준 (일)
)

# 백테스트 실행
results = engine.run_backtest(
    price_data=price_data,
    start_date='20230101',  # 시작 날짜 (선택)
    end_date='20241231'     # 종료 날짜 (선택)
)

# 결과 확인
print(f"총 수익률: {results['total_return']:.2f}%")
print(f"샤프 비율: {results['sharpe_ratio']:.2f}")
print(f"MDD: {results['mdd']:.2f}%")
```

## 📊 전략 로직

### 현재 적용된 전략 (최적화)

**진입 조건 (매수):**
1. **단기 이동평균 > 장기 이동평균** (MA20 > MA60)
2. **현재가 > 장기 추세선** (Close > MA200)
3. **RSI < 3** (극도의 과매도 상태) ⭐
4. **2거래일 전 대비 -5% 이상 하락** ⭐
5. 최대 보유 종목 수 미만
6. **현금 20% 보유** (총 자본의 80%만 투자) ⭐

**청산 조건 (매도):**
1. **RSI > 80** (과매수 상태)
2. **현재가 > 손익분기점** (수수료+세금 포함)

⭐ 표시: 최적화를 통해 변경된 파라미터

### 기존 전략 (참고)

**진입 조건:**
- RSI < 5 (기존)
- 하락 > -2% (기존)
- 현금 비중 없음 (기존)

**성과 비교:**
- 기존: 연 21.71%, MDD -55.15%
- 최적화: 연 25.53%, MDD -49.35% ✅

## 📈 백테스트 결과 (최적 전략)

### 2016-2025 (9.7년) 백테스트 결과

```
============================================================
백테스트 결과 (최적 전략)
============================================================
초기 자본금:              8,000,000 원 (투자금, 현금 20% 별도)
최종 자산:              111,904,851 원
총 수익:                103,904,851 원
총 수익률:                  1298.81 % (투자금 기준)
연환산 수익률:                31.92 % (투자금 기준)
                              25.53 % (전체 자본 기준) ⭐
샤프 비율:                     0.98
MDD:                         -61.68 % (투자금 기준)
                             -49.35 % (전체 자본 기준) ⭐
------------------------------------------------------------
총 거래 횟수:                   406 회
매수:                           208 회
매도:                           198 회
승률:                        100.00 %
평균 수익률:                  10.42 %
============================================================
```

⭐ 전체 자본 기준: 현금 20% 포함한 실질 포트폴리오 성과

### 전략별 성과 비교

| 전략 | 연수익률 | MDD | Sharpe | 위험조정 | 적합 대상 |
|-----|----------|-----|--------|----------|----------|
| **최적(현금20%+진입강화)** | **25.53%** | **-49.35%** | **0.98** | **0.5175** | **균형 투자자** ⭐ |
| 기존 RSI | 21.71% | -55.15% | 0.84 | 0.3937 | 공격적 |
| Cumulative RSI + 진입강화 | 10.98% | -12.10% | 1.11 | 0.9075 | 보수적 |

## 📁 출력 파일

백테스트 실행 시 다음 파일들이 `backtest/output/` 디렉토리에 저장됩니다:

- `backtest_result_YYYYMMDD_HHMMSS.png` - 백테스트 결과 그래프
  - 포트폴리오 가치 변화
  - 보유 종목 수 변화
  - 누적 수익률 및 손익 구간
  
- `trades_YYYYMMDD_HHMMSS.csv` - 거래 내역 상세 기록
  - 날짜, 종목코드, 매매 유형, 가격, 수량
  - 수수료, 세금, 순수익
  - 평균 매입가, 수익, 수익률

## 📚 연구 및 분석 문서

백테스트 최적화 과정에서 생성된 분석 문서들:

### 핵심 문서
1. **[MDD_REDUCTION_FINAL_RECOMMENDATION.md](MDD_REDUCTION_FINAL_RECOMMENDATION.md)** ⭐
   - 최종 권장 전략 및 성과
   - 투자자 성향별 추천 전략
   - 실전 적용 가이드

2. **[STOP_LOSS_ANALYSIS.md](STOP_LOSS_ANALYSIS.md)**
   - 손절 전략 테스트 (11가지 시나리오)
   - 결론: RSI(2) 역추세 전략에서는 손절 비활성화가 최적

3. **[CUMULATIVE_RSI_ANALYSIS.md](CUMULATIVE_RSI_ANALYSIS.md)**
   - Larry Connors의 Cumulative RSI 전략 분석
   - 보수적 투자자를 위한 저MDD 전략 (MDD -12%)

### 방법론 문서
4. **[MDD_REDUCTION_METHODS.md](MDD_REDUCTION_METHODS.md)**
   - MDD 감소 8가지 방법 설명
   - 현금 비중, 포지션 크기, 진입 조건 등

### 테스트 스크립트
- `test_mdd_reduction.py` - MDD 감소 방법 개별 테스트
- `optimize_mdd_reduction.py` - 조합 최적화
- `test_cumulative_rsi.py` - Cumulative RSI 전략 테스트
- `optimize_stop_loss.py` - 손절 파라미터 최적화

## 🔧 전략 변형

다양한 투자 성향에 맞는 전략 설정:

### 1. 최적 전략 (현재 적용) ⭐
```python
# 균형 투자자용 - 수익과 안정성 최적화
engine = BacktestEngine(
    initial_capital=10_000_000 * 0.8,  # 현금 20% 유지
    rsi_buy_threshold=3,                # 강한 과매도
    price_drop_threshold=-5.0           # 큰 하락만 진입
)
# 예상: 연 25.53%, MDD -49.35%
```

### 2. 보수적 전략 (안정성 최우선)
```python
# Cumulative RSI + 진입 강화
from backtest.test_cumulative_rsi import CumulativeRSIEngine

engine = CumulativeRSIEngine(
    initial_capital=10_000_000,
    cumulative_days=2,
    cumulative_buy_threshold=10,
    price_drop_threshold=-5.0
)
# 예상: 연 10.98%, MDD -12.10% (심리적 부담 최소)
```

### 3. 공격적 전략 (수익률 최우선)
```python
# 진입 조건 강화 + 현금 0%
engine = BacktestEngine(
    initial_capital=10_000_000,         # 전액 투자
    rsi_buy_threshold=3,
    price_drop_threshold=-5.0
)
# 예상: 연 31.09%, MDD -61.79%
```

### 4. 매우 보수적 (MDD 최소화)
```python
# 현금 30% + 진입 강화
engine = BacktestEngine(
    initial_capital=10_000_000 * 0.7,   # 현금 30%
    rsi_buy_threshold=3,
    price_drop_threshold=-5.0
)
# 예상: 연 21.57%, MDD -42.39%
```

## ⚠️ 주의사항

1. **과거 성과가 미래 성과를 보장하지 않습니다**
2. **실제 거래 시 슬리피지, 체결 지연 등 추가 비용 발생**
3. **백테스트는 완벽한 체결을 가정하므로 실제와 차이 존재**
4. **생존편향 (Survivorship Bias)**: 백테스트는 상장폐지 종목 미포함
5. **심리적 요인**: MDD -49%를 실제로 견디기는 매우 어려움
6. **현금 비중 관리**: 레버리지 절대 사용 금지
7. **관리종목 대응**: 백테스트에 없는 위험, 관리종목 즉시 매도 필요

### 실전 적용 시 권장사항
- 소액으로 시작 (백테스트 자본의 10-20%)
- 1-3개월 검증 기간 필수
- 심리적으로 견딜 수 있는 금액만 투자
- 정기적 성과 점검 및 전략 재평가

## 🚀 빠른 시작 가이드

```bash
# 1. 최적 전략으로 백테스트 실행
poetry run python -m backtest.run_backtest

# 2. MDD 감소 방법 테스트
poetry run python backtest/test_mdd_reduction.py

# 3. Cumulative RSI 전략 테스트
poetry run python backtest/test_cumulative_rsi.py

# 4. 손절 최적화 테스트
poetry run python backtest/optimize_stop_loss.py
```

## 📚 참고

### 코드
- 전략 소스코드: `strategy/RSIStrategy.py`
- 백테스트 엔진: `backtest/backtest_engine.py`
- 실행 스크립트: `backtest/run_backtest.py`

### 연구 자료
- Larry Connors의 RSI(2) 전략
- Cumulative RSI 개념 (Larry Connors)
- 내부 백테스트 및 최적화 연구 (2026-01-01)

### 관련 링크
- [systrader79 블로그](https://stock79.tistory.com/)
- [누적 RSI 전략](https://stock79.tistory.com/entry/누적-RSI-전략을-이용한-절대-수익-전략)
