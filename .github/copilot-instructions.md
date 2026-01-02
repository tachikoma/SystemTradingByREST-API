# System Trading Copilot Instructions

## 프로젝트 개요
키움증권 REST API 기반 한국 주식 자동매매 시스템. RSI 전략으로 KOSPI/KOSDAQ 종목을 매매하며, 9.7년(2016-2025) 백테스트 검증된 최적화 전략을 실전 운영 중.

## 핵심 아키텍처

### 주요 컴포넌트 (3-Layer Structure)
1. **API Layer** (`api/Kiwoom.py`): REST API + WebSocket 통신
   - 토큰 갱신, rate limit 처리(retry with exponential backoff)
   - 실시간 시세 수신 (WebSocket, 비동기 스레드)
   - Mock/Real 모드 지원 (`self.mock` flag)

2. **Strategy Layer** (`strategy/RSIStrategy.py`): Threading 기반 매매 로직
   - `threading.Thread` 상속, 백그라운드 실행
   - 5분마다 동기화 (`SYNC_INTERVAL = 300`)
   - 30일마다 유니버스 재구성 (`UNIVERSE_UPDATE_DAYS = 30`)

3. **Utility Layer** (`util/`): 공통 인프라
   - `db_helper.py`: SQLite 캐시 (유니버스, 가격 데이터)
   - `time_helper.py`: 한국 시간대 처리 (`ZoneInfo("Asia/Seoul")`)
   - `make_up_universe.py`: 네이버 금융 크롤링 (ROE/PER 기반 200종목 선정)

### 데이터 흐름
```
main.py → Kiwoom(API) ← RSIStrategy(Thread)
                ↓
         WebSocket → 실시간 시세
                ↓
         SQLite(캐시) ← make_up_universe(크롤러)
```

## 최적화된 전략 파라미터 (2026-01-01 적용)
**절대 변경하지 말 것** - 9.7년 백테스트로 검증된 최적값:
- `RSI_BUY_THRESHOLD = 3` (5→3, 더 강한 과매도에서 진입)
- `PRICE_DROP_THRESHOLD = -5.0` (-2→-5, 더 큰 하락 후 진입)
- `CASH_RESERVE_RATIO = 0.2` (20% 현금 유지)
- **손절 비활성화** (`enable_stop_loss=False`) - 백테스트상 손절 없음이 최고 성능

성과: 연수익률 25.53%, MDD -49.35%, Sharpe 0.98
문서: `backtest/MDD_REDUCTION_FINAL_RECOMMENDATION.md`

## 프로젝트별 관례

### 시간 처리
**항상** `util.time_helper.get_korea_time()` 사용:
```python
from util.time_helper import get_korea_time, check_transaction_open
now = get_korea_time()  # timezone-aware KST
if check_transaction_open():  # 장 중(09:00-15:20) 체크
```
❌ `datetime.now()` 직접 사용 금지 (서버 시간대 의존)

### 환경변수 로딩 순서
`main.py`의 초기화 순서 **반드시 준수**:
```python
# 1. .env 로드 (최우선)
load_dotenv(dotenv_path=Path(__file__).parent / '.env')
# 2. 로깅 초기화 (KIW_LOG_LEVEL 반영)
from util.logging_config import configure_logging
configure_logging()
# 3. 나머지 모듈 import
from api.Kiwoom import Kiwoom
```
이유: 로거 초기화 시점에 환경변수 필요

### 데이터베이스 패턴
- 전략별 DB 분리: `{strategy_name}.db` (예: `RSIStrategy.db`)
- 백테스트 DB: `backtest_data.db` (크롤링된 과거 데이터)
- 함수: `check_table_exist()`, `insert_df_to_db()`, `execute_sql()`
- **트랜잭션 자동처리**: `with sqlite3.connect(...)` 사용

### API Rate Limit 처리
Kiwoom API는 초당 요청 제한 있음:
```python
# 연속 API 호출 시 대기
self.kiwoom.get_order()
time.sleep(0.3)  # 300ms 대기
self.kiwoom.get_balance()
```
`api/Kiwoom.py`의 `_request()`: rate limit 시 retry + exponential backoff

### 모의투자 블랙리스트
모의투자에서 거래 불가 종목은 `strategy/RSIStrategy.py`:
```python
self.mock_trade_blacklist  # Set[str]
self.add_to_mock_blacklist(code, name, reason)  # DB 저장
```
실전 투자에서는 사용 안 함 (`if not self.kiwoom.mock: return`)

## 개발 워크플로우

### 환경 설정
```bash
# Python 3.11+ 필수 (macOS 기본 Python 3.9 사용 금지)
poetry install  # pyproject.toml 기반 의존성 설치
cp .env.example .env  # API 키 설정

# .env 파일 설정
# KIWOOM_MODE=mock  # 'mock' 또는 'real'
# KIWOOM_MOCK_APPKEY=...  # 모의투자 키
# KIWOOM_MOCK_SECRETKEY=...
# KIWOOM_REAL_APPKEY=...  # 실전투자 키 (선택)
# KIWOOM_REAL_SECRETKEY=...
```

### 실행
```bash
# 모의 투자 (기본값)
poetry run python main.py

# 실전 투자 (환경변수로 모드 변경)
KIWOOM_MODE=real poetry run python main.py
# 또는 .env 파일에서 KIWOOM_MODE=real 설정
```

### 백테스트
```bash
# 전체 기간 백테스트 (DB의 모든 데이터)
poetry run python -m backtest.run_backtest

# 최근 5년만
poetry run python -m backtest.run_backtest --years 5

# 특정 기간
poetry run python -m backtest.run_backtest --start 20200101 --end 20231231
```

결과: `backtest/output/trades_YYYYMMDD_HHMMSS.csv`

### 테스트
```bash
# 단위 테스트 (mock 기반, 빠름)
poetry run pytest

# 통합 테스트 (실제 API 호출, 모의투자 계정 필요)
export RUN_INTEGRATION=1
export KIW_APPKEY=<appkey>
export KIW_SECRET=<secret>
poetry run pytest -m integration tests/test_integration_readonly.py

# ⚠️ 실제 주문 테스트 (위험! 테스트 계정만 사용)
export RUN_REAL_ORDERS=1
poetry run pytest -m integration tests/test_integration_order.py
```

### 로깅
환경변수 또는 `pyproject.toml`에서 설정:
```bash
export KIW_LOG_LEVEL=DEBUG  # DEBUG, INFO, WARNING, ERROR
export KIW_LOG_DIR=./logs
```
로그: `logs/kiwoom.log` (RotatingFileHandler, 자동 rotate)

## 통합 지점 & 외부 의존성

### 외부 서비스
1. **키움증권 REST API** (OpenAPI+)
   - 인증: OAuth2 (client_credentials)
   - Base URL: `https://mockapi.kiwoom.com` (mock) / `https://api.kiwoom.com` (real)
   - WebSocket: `wss://mockapi.kiwoom.com:10000/api/dostk/websocket`
   - Rate limit: 초당 ~3-5 요청 (명시적 제한 없음, 경험적 수치)

2. **네이버 금융** (크롤링)
   - URL: `https://finance.naver.com/sise/sise_market_sum.nhn?sosok={0|1}`
   - 필드: 종목명, 현재가, ROE, PER, PBR 등
   - 장시간에만 실행 (`util/make_up_universe.py::is_market_hours()`)

3. **FinanceDataReader** (백테스트 데이터)
   - `pip install finance-datareader`
   - `fdr.DataReader('005930', '20200101')` 형식

### 크로스 컴포넌트 통신
- **Kiwoom ↔ RSIStrategy**: `Kiwoom` 인스턴스를 Strategy 생성자에 주입
- **WebSocket → Strategy**: `kiwoom.universe_realtime_transaction_info` Dict 업데이트
- **DB Cache**: SQLite로 API 재호출 방지 (가격 데이터, 유니버스)

## 백테스트 분석 도구
전략 최적화용 스크립트들 (`backtest/`):
- `compare_stop_loss.py`: 손절 전략 비교 (결론: 손절 불필요)
- `test_mdd_reduction.py`: MDD 감소 방법 8가지 테스트
- `test_cumulative_rsi.py`: Cumulative RSI 전략 검증
- `optimize_mdd_reduction.py`: 파라미터 그리드 서치

분석 문서: `backtest/STOP_LOSS_ANALYSIS.md`, `backtest/MDD_REDUCTION_METHODS.md`

## 주의사항

### 보안
- `.env` 파일 **절대 커밋 금지** (이미 `.gitignore`에 포함)
- API 키는 `.env.example`에 샘플로만 유지

### 거래 비용
`strategy/RSIStrategy.py` 초기화 시 .env에서 읽음:
```python
TRADING_FEE_PERCENT=0.35  # 수수료 (편도)
TRADING_TAX_PERCENT=0.15  # 증권거래세 (매도만)
```
백테스트에서도 동일 비율 사용 (`backtest_engine.py`)

### 스레드 안전성
- `RSIStrategy`는 `threading.Thread` - 별도 스레드 실행
- WebSocket은 `asyncio` 기반 독립 스레드
- Lock 필요 시 `threading.Lock()` 사용

### macOS 특이사항
- 기본 Python 3.9는 사용 불가 → Homebrew로 3.11+ 설치
- M1/M2 Mac: `pandas`, `numpy` arm64 버전 자동 설치됨

## 파일 참조
- 전략 로직: [strategy/RSIStrategy.py](strategy/RSIStrategy.py)
- API 클라이언트: [api/Kiwoom.py](api/Kiwoom.py)
- 백테스트 엔진: [backtest/backtest_engine.py](backtest/backtest_engine.py)
- 진입점: [main.py](main.py)
- 설정: [pyproject.toml](pyproject.toml)
