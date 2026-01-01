# 백테스트용 과거 데이터 수집 프로그램

키움 REST API를 이용하여 국내 주식 10년치 일봉 데이터를 수집하고 `backtest_data.db` 파일로 저장하는 프로그램입니다.

## 주요 기능

- 자동매매 프로그램의 유니버스 종목 리스트를 그대로 사용
- 키움 REST API를 통해 종목별 10년치 (약 2,400개) 일봉 데이터 수집
- API 한번에 최대 600개 데이터 수집 가능 → 4번 연속 조회로 10년치 확보
- 자동매매 프로그램과 동일한 DB 구조 사용 (SQLite)
- 레이트 리밋 방지 및 재시도 로직 포함

## 사용 방법

### 1. 환경 설정

프로젝트 루트에 `.env` 파일이 있는지 확인합니다:

```bash
KIWOOM_APPKEY=your_app_key
KIWOOM_SECRETKEY=your_secret_key
```

### 2. 프로그램 실행

프로젝트 루트 디렉토리에서 실행합니다:

```bash
cd /path/to/SystemTrading
poetry run python -m backtest.fetch_historical_data
```

### 3. 실행 과정

1. 키움 API 인증
2. 유니버스 종목 리스트 로드
3. 종목별 과거 데이터 수집 (종목당 약 4번 API 호출)
4. `backtest_data.db` 파일에 저장

## 출력 결과

- **DB 파일**: `backtest_data.db`
- **테이블 구조**:
  - `universe`: 유니버스 종목 정보 (code, code_name, created_at)
  - `{종목코드}`: 각 종목별 OHLCV 데이터 (index=날짜, open, high, low, close, volume)

## DB 구조

자동매매 프로그램과 동일한 형태로 저장됩니다:

```
backtest_data.db
├── universe (테이블)
│   ├── code: 종목코드
│   ├── code_name: 종목명
│   └── created_at: 생성일자
│
├── {종목코드1} (테이블)
│   ├── index: 날짜 (YYYYMMDD)
│   ├── open: 시가
│   ├── high: 고가
│   ├── low: 저가
│   ├── close: 종가
│   └── volume: 거래량
│
├── {종목코드2} (테이블)
└── ...
```

## 예상 소요 시간

- 종목당 약 2초 (4번 API 호출 + 대기 시간)
- 유니버스 종목 수에 따라 다름 (예: 30개 종목 = 약 1분)

## 주의사항

1. **API 레이트 리밋**: 키움 API는 요청 제한이 있으므로 종목 수가 많을 경우 시간이 오래 걸릴 수 있습니다
2. **네트워크 안정성**: 중간에 네트워크 오류가 발생하면 해당 종목은 건너뛰고 다음 종목으로 진행합니다
3. **데이터 업데이트**: 기존에 데이터가 있는 종목은 덮어쓰기(replace)됩니다
4. **Mock API**: 현재 코드는 `mock=True`로 설정되어 있습니다. 실거래 데이터 수집 시 `mock=False`로 변경하세요

## 수집 데이터 확인

SQLite 클라이언트나 Python으로 확인할 수 있습니다:

```python
import sqlite3
import pandas as pd

# DB 연결
conn = sqlite3.connect('backtest_data.db')

# 유니버스 확인
universe_df = pd.read_sql("SELECT * FROM universe", conn)
print(universe_df)

# 특정 종목 데이터 확인
code = '005930'  # 예: 삼성전자
stock_df = pd.read_sql(f"SELECT * FROM `{code}`", conn)
print(stock_df.head())
print(f"총 {len(stock_df)}개 데이터")

conn.close()
```

## 백테스트에서 사용

수집된 `backtest_data.db` 파일을 백테스트 엔진에서 사용하면 됩니다:

```python
# 간단한 실행
poetry run python -m backtest.run_backtest
```

또는 직접 사용:

```python
from backtest.backtest_engine import BacktestEngine
from backtest.run_backtest import load_price_data_from_db

# DB에서 데이터 로드
price_data, date_range = load_price_data_from_db('backtest_data')

# 백테스트 실행 (최적 전략 적용됨)
engine = BacktestEngine(
    initial_capital=10_000_000 * 0.8,  # 20% 현금 보유
    max_holdings=10,
    rsi_buy_threshold=3,  # 최적화: 5→3
    price_drop_threshold=-5.0,  # 최적화: -2→-5
)
results = engine.run_backtest(price_data)

print(f"연평균 수익률: {results['annual_return']:.2f}%")
print(f"MDD: {results['mdd']:.2f}%")
print(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
```

## 백테스트 최적 전략 (2026-01-01 발견)

9.7년간의 백테스트(2016-2025)를 통해 발견한 최적 전략:

### 핵심 매개변수
- **현금 비중**: 20% (항상 현금 보유)
- **RSI 매수 기준**: 3 이하 (기존 5 → 더 강한 과매도)
- **가격 하락 기준**: -5% 이상 (기존 -2% → 더 큰 하락)
- **최대 보유 종목**: 10개
- **손절**: 사용 안 함 (백테스트 결과 불필요)

### 성과 비교

| 지표 | 기본 전략 | 최적 전략 | 개선 |
|------|-----------|-----------|------|
| 연수익률 | 21.71% | 25.53% | +3.82%p (+17.6%) |
| MDD | -55.15% | -49.35% | +5.80%p (-10.5%) |
| Sharpe | 0.84 | 0.98 | +0.14 (+16.7%) |
| 위험조정 | 0.3937 | 0.5175 | +0.1238 (+31.4%) |

### 왜 이 전략이 최적인가?

1. **현금 20% 보유**
   - MDD 비례 감소 (자동으로 ~11% 개선)
   - 극단적 하락 시 안전 버퍼
   - 추가 기회 대응 여력

2. **진입 조건 강화 (RSI<3, 하락>-5%)**
   - 더 강한 과매도 신호만 포착
   - 거짓 신호 감소
   - 반등 확률 증가

3. **손절 불사용**
   - RSI(2) 역추세 전략 특성상 손절 시 수익 기회 상실
   - 100% 승률 유지
   - 단, 실전에서는 관리종목 지정 시 즉시 매도 권장

자세한 연구 내용은 다음 문서를 참고하세요:
- [README.md](README.md): 종합 전략 문서
- [MDD_REDUCTION_METHODS.md](MDD_REDUCTION_METHODS.md): 8가지 MDD 감소 방법
- [STOP_LOSS_ANALYSIS.md](STOP_LOSS_ANALYSIS.md): 손절 분석
- [MDD_REDUCTION_FINAL_RECOMMENDATION.md](MDD_REDUCTION_FINAL_RECOMMENDATION.md): 최종 권장사항

## 문제 해결

### API 인증 실패
- `.env` 파일의 API 키 확인
- 키움 API 콘솔에서 앱 키 상태 확인

### 데이터 수집 실패
- 로그 확인: 파일 실행 시 로그가 출력됨
- 네트워크 연결 상태 확인
- API 요청 한도 확인

### DB 파일 위치
- 기본적으로 프로그램 실행 디렉토리에 생성됨
- 절대 경로로 지정하려면 `DB_NAME` 상수 수정
