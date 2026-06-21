# RSI 전략 백테스트

RSIStrategy의 매매 로직을 재현하여 과거 데이터로 백테스트를 수행하는 프레임워크입니다.

## ⚠️ 주요 변경사항 (2026-06-21 — 시간 손절 ON walk-forward 검증 v2)

**매도 조건이 변경되었습니다 (2026-06-20 v1):**
- 변경 전: `RSI > 85 AND close >= breakeven * 1.10` (10% 목표 수익률)
- 변경 후: `RSI > 70 AND close >= breakeven` (수익 목표 제거, breakeven 이상 즉시 매도)

**엔진 기본값 변경 (2026-06-21 v2):**
- `enable_time_stop_loss`: False → **True** (시간 손절 기본 활성화)
- `DEFAULT_PROFIT_TARGET_PERCENT`: 10.0 → **0.0** (수익 목표 제거)
- `DEFAULT_TIME_STOP_LOSS_DAYS`: 90 → **180** (180일 시간 손절)

v1 walk-forward(2026-06-20)는 시간 손절이 OFF인 상태에서 실행되어
deep loser(-98.22%)가 영원히 청산되지 않아 결과가 과장되었습니다.
v2는 시간 손절 ON으로 재최적화한 결과입니다.
자세한 내용은 `backtest/AGENTS.md`를 참고하세요.

## 🎯 최적 전략 (2026-06-21 walk-forward 검증 v2)

**TSL180_PT0_SELL70_NO_MA20**: 시간 손절 180일, 수익 목표 0%, RSI 매도 70, MA20>MA60 필터 OFF

```python
BacktestEngine(
    initial_capital=10_000_000,
    cash_reserve_ratio=0.2,
    rsi_sell_threshold=70,        # walk-forward v2 검증: 70이 최적
    profit_target_percent=0.0,    # walk-forward v2 검증: 0%가 최적 (breakeven 즉시 매도)
    rsi_buy_threshold=3,
    price_drop_threshold=-5.0,
    enable_time_stop_loss=True,   # 시간 손절 ON (기본값)
    time_stop_loss_days=180,      # walk-forward v2 검증: 180일이 최적
    use_ma20_filter=False,        # walk-forward v3 검증: OFF가 최적, +77.11% / MDD -22.85%
)
```

**v3 성과 (2016-2026, 생존편향 제거):**
- 총수익률: +77.11%, 연환산: +4.90%
- MDD: -22.85%, Sharpe: 0.484
- 승률: 81.11%, 총거래: 1,778회

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
    rsi_sell_threshold=70,        # RSI 매도 기준 (walk-forward 검증: 70이 최적)
    rsi_buy_threshold=3,          # RSI 매수 기준
    price_drop_threshold=-5.0,    # 가격 하락 기준 (%)
    rsi_method='wilder',          # RSI 계산 방식
    commission_rate=0.00015,      # 거래 수수료율
    tax_rate=0.0020,              # 거래세 (매도 시)
    time_stop_loss_days=180        # 시간 손절 기준 (일) (walk-forward v2 검증: 180일이 최적)
    use_ma20_filter=False,          # MA20>MA60 필터 (walk-forward v3: OFF가 최적, +77.11%)
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

**청산 조건 (매도) — walk-forward 검증 v2 (2026-06-21, 시간 손절 ON):**
1. **RSI > 70** (과매수 상태, 기존 80→70)
2. **현재가 > 손익분기점** (수수료+세금 포함, breakeven 이상 즉시 매도)
3. ~~목표 수익률(10%) 조건 제거~~ — 수익 목표가 deep loser를 유발하여 제거함
4. **시간 손절 180일** — 매수 후 180일 초과 시 강제 매도 (기본 활성화)

⭐ 표시: 최적화를 통해 변경된 파라미터

### 기존 전략 (참고)

**진입 조건:**
- RSI < 5 (기존)
- 하락 > -2% (기존)
- 현금 비중 없음 (기존)
- 시간 손절 OFF (기존, deep loser 영원히 보유)
- MA20>MA60 필터 항상 ON (기존)

**성과 비교 (v1 TSL OFF vs v3 NO_MA20):**
- v1 (TSL OFF): 연 25.53%, MDD -49.35%, 승률 100% (deep loser 미반영)
- v2 (TSL ON, 최적): 총 +41.23%, MDD -35.39%, 승률 79.67%
- v3 (NO_MA20): **총 +77.11%, MDD -22.85%, 승률 81.11%** ✅

## 📈 백테스트 결과 (최적 전략 — v3 NO_MA20)

### 2016-2026 (10년) walk-forward 결과 (시간 손절 ON + MA20 필터 OFF)

**최적 구성: TSL180_PT0_SELL70_NO_MA20** (time_stop_loss_days=180, profit_target_percent=0, rsi_sell=70, use_ma20_filter=False)

| 지표 | 값 |
|------|-----|
| 총수익률 | **+77.11%** |
| 연환산 수익률 | +4.90% |
| MDD | **-22.85%** |
| Sharpe Ratio | **0.484** |
| 승률 | 81.11% |
| 총 거래 | 1,778회 |

**v1(TSL OFF) → v2(TSL ON) → v3(NO_MA20) 진화:**
- v1: TSL OFF, deep loser 미반영으로 +23.85% 과장 (미실현 -3,288,777원)
- v2: TSL ON (180일), PT=0%, SELL=70 → +41.23%, MDD -35.39%
- v3: **v2 + MA20>MA60 필터 OFF → +77.11%, MDD -22.85%** 🏆

### 전략별 성과 비교

| 전략 | 총수익률 | MDD | Sharpe | 승률 | 거래수 |
|------|---------|-----|--------|------|-------|
| **TSL180_PT0_SELL70_NO_MA20 (v3 최적)** | **+77.11%** | **-22.85%** | **0.484** | **81.11%** | **1,778** ⭐ |
| TSL180_PT0_SELL70 (v2) | +41.23% | -35.39% | 0.316 | 79.67% | 1,682 |
| TSL360_PT0_SELL70 | +28.81% | -23.60% | 0.257 | 82.21% | 1,258 |
| TSL90_PT0_SELL80 (v2) | +17.46% | -35.23% | 0.170 | 75.37% | 2,186 |
| 기존(v1 TSL OFF, PT=10, SELL=85) | -28.43% | -48.00% | -0.15 | 65.28% | ~800 |

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
    price_drop_threshold=-5.0,          # 큰 하락만 진입
    use_ma20_filter=False,              # MA20>MA60 필터 OFF (v3 최적)
)
# 예상: +77.11% 총수익률, MDD -22.85%
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
