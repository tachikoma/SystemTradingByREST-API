# RSI 전략 백테스트

RSIStrategy의 매매 로직을 재현하여 과거 데이터로 백테스트를 수행하는 프레임워크입니다.

## 📋 기능

- **전략 시뮬레이션**: RSI 기반 매매 전략을 과거 데이터로 시뮬레이션
- **성과 분석**: 수익률, 샤프 비율, MDD 등 다양한 지표 계산
- **시각화**: 포트폴리오 가치 변화, 보유 종목 수, 누적 수익률 그래프
- **거래 내역 추출**: 모든 매매 기록을 CSV로 저장

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
    rsi_sell_threshold=80,        # RSI 매도 기준
    rsi_buy_threshold=5,          # RSI 매수 기준
    price_drop_threshold=-2,      # 가격 하락 기준 (%)
    commission_rate=0.00015,      # 거래 수수료율
    tax_rate=0.0025               # 거래세 (매도 시)
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

백테스트는 다음과 같은 RSI 전략을 구현합니다:

### 매수 조건
1. **단기 이동평균 > 장기 이동평균** (MA20 > MA60)
2. **RSI < 5** (과매도 상태)
3. **2거래일 전 대비 -2% 이상 하락**
4. 최대 보유 종목 수 미만

### 매도 조건
1. **RSI > 80** (과매수 상태)
2. **현재가 > 매입가** (수익 실현)

## 📈 백테스트 결과 예시

```
============================================================
백테스트 결과
============================================================
초기 자본금:         10,000,000 원
최종 자산:           12,345,678 원
총 수익:              2,345,678 원
총 수익률:                23.46 %
연환산 수익률:            18.32 %
샤프 비율:                 1.45
MDD:                     -12.34 %
------------------------------------------------------------
총 거래 횟수:                150 회
매수:                        75 회
매도:                        75 회
승률:                       62.67 %
평균 수익률:                 5.23 %
총 실현 손익:          2,345,678 원
============================================================
```

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

## 🔧 파라미터 튜닝

전략 파라미터를 조정하여 최적화할 수 있습니다:

```python
# 보수적인 설정
engine = BacktestEngine(
    max_holdings=5,           # 적은 종목 수
    rsi_buy_threshold=10,     # 높은 RSI 매수 기준
    price_drop_threshold=-3   # 큰 하락폭 요구
)

# 공격적인 설정
engine = BacktestEngine(
    max_holdings=20,          # 많은 종목 수
    rsi_buy_threshold=3,      # 낮은 RSI 매수 기준
    price_drop_threshold=-1   # 작은 하락폭 허용
)
```

## ⚠️ 주의사항

1. **과거 성과가 미래 성과를 보장하지 않습니다**
2. **실제 거래 시 슬리피지, 체결 지연 등 추가 비용 발생**
3. **백테스트는 완벽한 체결을 가정하므로 실제와 차이 존재**
4. **장 마감 후 거래 불가 시간 등 실제 제약사항 미반영**

## 📚 참고

- 전략 소스코드: `strategy/RSIStrategy.py`
- 백테스트 엔진: `backtest/backtest_engine.py`
- 실행 스크립트: `backtest/run_backtest.py`
