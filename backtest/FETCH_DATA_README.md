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
from backtest.backtest_engine import BacktestEngine
import sqlite3
import pandas as pd

# DB에서 데이터 로드
conn = sqlite3.connect('backtest_data.db')
universe_df = pd.read_sql("SELECT * FROM universe", conn)

# 백테스트 실행
for _, row in universe_df.iterrows():
    code = row['code']
    stock_df = pd.read_sql(f"SELECT * FROM `{code}`", conn, index_col='index')
    # ... 백테스트 로직
```

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
