# 쉽게 따라 만드는 주식자동매매시스템 (REST API & Poetry 버전)

## 프로젝트 개요
이 프로젝트는 키움 OpenAPI의 REST API와 WebSocket을 사용하는 한국 주식 시장을 위한 OS 독립적인 자동 트레이딩 봇입니다. 기존 ActiveX 기반의 프로젝트를 리팩터링하고, `Poetry`를 사용하여 의존성을 관리하도록 변경했습니다.

## 아키텍처
- **진입점 (`main.py`):** `Kiwoom` 클래스와 `RSIStrategy` 스레드를 초기화하고 실행합니다.
- **핵심 로직 (`strategy/RSIStrategy.py`):** RSI와 이동평균선을 기반으로 매매 신호를 생성하고 주문을 실행하는 주식 트레이딩 알고리즘을 포함합니다. `threading.Thread`를 상속받아 백그라운드에서 실행됩니다.
- **API 추상화 (`api/Kiwoom.py`):** 키움 REST API와 WebSocket을 사용하여 통신합니다. `requests` 라이브러리를 사용하여 HTTP 요청을 보내고, `websockets` 라이브러리를 사용하여 실시간 시세를 수신합니다.
- **종목 선정 (`util/make_up_universe.py`):** 키움 API(장 종료 후) 또는 네이버 금융(장 중) 크롤링으로 KOSPI/KOSDAQ 전체 종목 정보를 수집하여 거래량, 시가총액, 변동성 등의 지표를 기반으로 상위 100개 종목을 선정합니다. 매일 장 종료 후 자동으로 데이터를 캐싱하여 다음날 빠르게 시작할 수 있습니다.
- **데이터베이스 (`util/db_helper.py`):** SQLite를 사용하여 종목 유니버스 및 과거 시세 데이터를 캐시하여 빠른 시작과 API 요청 제한을 회피합니다.

## 요구 사항
1.  **Python 3.11 이상**이 설치되어 있어야 합니다.
    
    > **macOS 사용자 주의**: 최신 macOS에도 기본으로 Python 3.9가 설치되어 있습니다. Python 3.11 이상을 별도로 설치해야 합니다.
    > 
    > **Homebrew를 통한 설치:**
    > ```bash
    > brew install python@3.11
    > # 또는 최신 버전
    > brew install python@3.12
    > ```
    > 
    > 설치 후 버전 확인:
    > ```bash
    > python3 --version
    > # 또는
    > python3.11 --version
    > ```

2.  **Poetry**를 설치합니다.
    ```bash
    pip install poetry
    ```
3.  프로젝트 의존성을 설치합니다.
    ```bash
    poetry install
    ```
    이 명령어는 `pyproject.toml` 파일을 읽어 필요한 라이브러리를 가상 환경에 설치합니다.

## 설정

### 1. API 키 설정
애플리케이션을 실행하기 전에, 키움증권에서 발급받은 API 키를 `.env` 파일에 설정해야 합니다.

1. `.env.example` 파일을 복사하여 `.env` 파일을 생성합니다:
   ```bash
   cp .env.example .env
   ```

2. `.env` 파일을 열어 실제 API 키를 입력합니다:
   ```env
   KIWOOM_APPKEY=your_actual_app_key
   KIWOOM_SECRETKEY=your_actual_secret_key
   ```

**⚠️ 보안 주의사항:**
- `.env` 파일은 민감한 정보를 포함하므로 **절대 Git에 커밋하지 마세요**
- `.env` 파일은 이미 `.gitignore`에 포함되어 있습니다
- `.env.example` 파일만 버전 관리에 포함됩니다

```markdown
# 쉽게 따라 만드는 주식자동매매시스템 (REST API 버전)

## 프로젝트 개요
이 저장소는 키움 OpenAPI의 REST 및 WebSocket 인터페이스를 사용하는 자동매매(백테스트/실전) 도구입니다. 기존 ActiveX/COM 기반 접근을 REST로 대체하고, 운영성을 개선하기 위해 다음과 같은 변경을 적용했습니다.

## 주요 구성
- `main.py`: 애플리케이션 진입점
- `api/Kiwoom.py`: Kiwoom REST + WebSocket 클라이언트 래퍼
- `strategy/RSIStrategy.py`: 예시 매매 전략 (RSI 기반)
- `util/`: 헬퍼 유틸리티들 (`time_helper.py`, `db_helper.py`, `logging_config.py` 등)

## 최근 변경사항(요약)

### 2026-01-01: 백테스트 최적 전략 발견 및 실전 적용 ⭐
- **백테스트 최적화 (9.7년 검증: 2016-2025)**
  - 최적 매개변수 발견: 현금 20% + RSI<3 + 하락>-5%
  - 연수익률: 21.71% → 25.53% (+3.82%p, +17.6%)
  - MDD: -55.15% → -49.35% (+5.80%p 개선, -10.5%)
  - Sharpe Ratio: 0.84 → 0.98 (+16.7%)
  - 위험조정 수익률: 0.3937 → 0.5175 (+31.4% 개선)

- **실전 전략 동기화 (`strategy/RSIStrategy.py`)**
  - `RSI_BUY_THRESHOLD`: 5 → 3 (더 강한 과매도에서 진입)
  - `PRICE_DROP_THRESHOLD`: -2% → -5% (더 큰 하락 후 진입)
  - `CASH_RESERVE_RATIO`: 0.2 추가 (항상 20% 현금 보유)
  - 예산 계산: 전체 예수금의 80%만 투자에 사용

- **API 안정성 개선**
  - `get_deposit()` 함수에 retry 처리 추가 (rate limit 대응)
  - 주기적 동기화에 API 호출 간 대기시간 추가 (0.3초)
  - `get_order()`, `get_balance()`, `get_deposit()` 모두 retry 지원

- **백테스트 연구 문서 추가**
  - `backtest/README.md`: 종합 전략 문서
  - `backtest/MDD_REDUCTION_METHODS.md`: 8가지 MDD 감소 방법
  - `backtest/STOP_LOSS_ANALYSIS.md`: 손절 분석 (손절 불필요 결론)
  - `backtest/CUMULATIVE_RSI_ANALYSIS.md`: Cumulative RSI 전략
  - `backtest/MDD_REDUCTION_FINAL_RECOMMENDATION.md`: 최적 전략 권장

### 최근 변경사항 (2026-01-03)
- **유니버스 생성 최적화**: 키움 API 호출 간격을 0.2초→0.1초로 최적화하여 전체 종목(4,234개) 수집 시간을 14분→약 7분으로 단축
- **데이터 단위 수정**: 키움 API `ka10001`의 시가총액(mrkt_cap)이 억원 단위임을 확인하고, 필터링 로직에서 백만원으로 변환(×100)하도록 수정
  - 기존: API가 백만원을 반환한다고 잘못 가정하여 유니버스 필터링 실패 (1개 종목만 통과)
  - 수정: `fetch_all_stocks_from_kiwoom()` 함수에서 시가총액 ×100 변환 추가 (예: 7,780억원 → 778,000백만원)
  - 관련 파일: `util/make_up_universe.py`, `api/Kiwoom.py` 주석 및 docstring 업데이트
- **거래 비용 환경변수 분리**: 모의투자와 실전투자의 거래 수수료·세금 설정을 별도 환경변수로 분리
  - 모의투자: `TRADING_FEE_PERCENT_MOCK=0.35`, `TRADING_TAX_PERCENT_MOCK=0.0`
  - 실전투자: `TRADING_FEE_PERCENT_REAL=0.015`, `TRADING_TAX_PERCENT_REAL=0.20`
  - 전략 실행 시 `kiwoom.mock` 플래그에 따라 자동 선택
- **함수명 변경**: `create_universe_from_kiwoom_api()` → `fetch_all_stocks_from_kiwoom()`로 명확화 (실제로는 유니버스 생성이 아닌 전체 종목 목록 가져오기)
- **캐시 저장 로직 개선**: `cache_daily_data()` 함수의 `use_cache`/`save_cache` 파라미터 분리로 매일 장 종료 후 데이터 자동 저장 기능 수정
- **CLI 옵션 추가**: `main.py`에 `-y/--yes` 플래그 추가로 실전투자 확인 프롬프트 스킵 가능 (자동화 환경용)
- **오타 수정**: "캠싱" → "캐싱" (strategy/RSIStrategy.py 내 8곳)

### 이전 변경사항
- REST 페이징 개선: `get_price_data`, `get_order`, `get_balance` 등에 `cont_yn` 파라미터를 추가하여 do-while 스타일로 첫 페이지를 반드시 조회하도록 변경함.
- 페이징별 재시도: API의 과금·요청 한도(rate-limit) 응답(응답코드 `5` 및 메시지 `허용된 요청 개수를 초과하였습니다`)에 한해서만 페이지 단위 재시도 로직을 추가함.
- WebSocket 안정화:
    - 토큰 만료 감지 시 비동기 인증 갱신을 수행하여 메인 이벤트 루프를 블로킹하지 않음.
    - 재접속 후 실시간 구독(re-register)을 자동으로 재등록함.
    - 중복 `LOGIN` 전송 방지: 로그인 상태 플래그와 웹소켓 루프에 바인딩된 `asyncio.Lock`을 도입함.
- 로깅 통합:
    - `util/logging_config.py` 추가: 중앙 설정으로 `RotatingFileHandler`를 구성.
    - 모듈별 `get_logger(__name__)` 사용으로 일관된 로깅 확보.
    - 환경변수로 로그 디렉터리, 레벨 및 회전 정책을 제어하도록 지원(`.env.example`에 예시 추가).
- 시간 처리: 모든 시간 판정에 `util.time_helper.get_korea_time()` 사용으로 한국 시간대 기준 일관성 유지.

## 요구사항
1. Python 3.11 이상
2. 의존성 설치 (Poetry 권장):
```bash
poetry install
```

## 설정
1. `.env.example`을 복사하여 `.env`를 생성합니다:
```bash
cp .env.example .env
```

2. `.env` 주요 항목 예시 (`.env.example` 참고):
```env
KIWOOM_MODE=mock  # 'mock' 또는 'real'
KIWOOM_MOCK_APPKEY=your_mock_app_key
KIWOOM_MOCK_SECRETKEY=your_mock_secret_key
KIWOOM_REAL_APPKEY=your_real_app_key
KIWOOM_REAL_SECRETKEY=your_real_secret_key

# API Rate Limit Configuration
# 키움 API 호출 간격 (초 단위)
KIWOOM_API_SLEEP_MOCK=0.2  # 모의투자: rate limit이 더 엄격
KIWOOM_API_SLEEP_REAL=0.1  # 실전투자: 0.1초로 안정적

# Trading Fees (모의투자와 실전투자 별도 설정)
TRADING_FEE_PERCENT_MOCK=0.35  # 모의투자 수수료 0.35%
TRADING_TAX_PERCENT_MOCK=0.0   # 모의투자 거래세 없음
TRADING_FEE_PERCENT_REAL=0.015 # 실전투자 수수료 0.015%
TRADING_TAX_PERCENT_REAL=0.20  # 실전투자 거래세 0.20% (매도만)

# Logging
KIW_LOG_DIR=./logs
KIW_LOG_LEVEL=INFO
KIW_LOG_ROTATION_MAX_BYTES=10485760
KIW_LOG_BACKUP_COUNT=5

# Telegram Bot (알림용)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

# Integration tests gate
# RUN_INTEGRATION=0
```

### 텔레그램 봇 설정 (선택사항)
전략 실행 중 매매 알림을 받으려면 텔레그램 봇을 설정하세요:

1. **봇 생성**
   - 텔레그램에서 [@BotFather](https://t.me/BotFather)와 대화
   - `/newbot` 명령으로 새 봇 생성
   - 봇 토큰 획득 (예: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

2. **Chat ID 확인**
   - 생성한 봇과 대화 시작 (메시지 1개 전송)
   - 브라우저에서 다음 URL 접속:
     ```
     https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
     ```
   - 응답에서 `"chat":{"id":123456789}` 부분의 숫자를 확인

3. **.env에 설정**
   ```env
   TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
   TELEGRAM_CHAT_ID=123456789
   ```

4. **테스트**
   ```bash
   poetry run python scripts/test_telegram.py
   ```

텔레그램 설정이 없으면 알림이 전송되지 않지만 전략은 정상 작동합니다.

환경 변수를 직접 설정해도 됩니다 (macOS/Linux 예시):
```bash
export KIWOOM_APPKEY=...
export KIWOOM_SECRETKEY=...
```

## 실행

### 실전 거래
```bash
# 모의투자 (기본값)
poetry run python main.py

# 실전투자 (확인 프롬프트 표시)
KIWOOM_MODE=real poetry run python main.py
# 또는 .env 파일에서 KIWOOM_MODE=real 설정

# 실전투자 자동 실행 (확인 프롬프트 스킵, 자동화용)
KIWOOM_MODE=real poetry run python main.py -y
# 또는
KIWOOM_MODE=real poetry run python main.py --yes
```

**주의**: `-y` 또는 `--yes` 옵션은 실전투자 확인 프롬프트를 건너뛰므로 자동화 환경에서만 사용하세요.

### 백테스트
백테스트를 통해 전략을 검증할 수 있습니다:

```bash
# 1. 과거 데이터 수집 (최초 1회)
poetry run python -m backtest.fetch_historical_data

# 2. 백테스트 실행
poetry run python -m backtest.run_backtest
```

**백테스트 결과 예시 (최적 전략: 2016-2025, 9.7년):**
- 연평균 수익률: 25.53%
- MDD: -49.35%
- Sharpe Ratio: 0.98
- 승률: 100%
- 총 거래: 412회

자세한 내용은 [backtest/README.md](backtest/README.md)를 참고하세요.

## 동작 및 구현 상세 (실무 참고)
 - 페이징 호출: `get_price_data(..., cont_yn='N')` 기본 동작은 첫 페이지를 가져오고, 이후 API 반환값에 따라 `cont_yn`을 사용해 다음 페이지를 반복 조회합니다. 내부적으로 do-while 형태를 흉내내며 `max_loops`, `max_retries`, `retry_delay`로 안전장치를 제공합니다.
 - 재시도 조건: 페이지별 재시도는 오직 API의 rate-limit 응답(응답코드 `5` + 메시지 포함 여부)에서만 발생합니다.
 - WebSocket: 장기 실행 환경에서의 안정성을 위해 다음을 적용했습니다:
     - 토큰 갱신을 비동기(executor로 백그라운드 실행)로 처리하여 메시지 송수신 루프 차단 방지
     - 로그인 동작은 플래그(`_websocket_logged_in`, `_websocket_login_sent`)와 `asyncio.Lock`으로 동기화하여 중복 로그인 메시지 전송을 방지
     - 재접속 시 실시간 데이터(구독) 자동 재등록
 - 로깅: `util/logging_config.configure_logging()`으로 초기화하면 환경변수에 따라 `RotatingFileHandler`가 자동으로 구성됩니다. 로그 파일 회전 및 보관 개수는 `KIW_LOG_ROTATION_MAX_BYTES`/`KIW_LOG_BACKUP_COUNT`로 제어됩니다.

## 테스트
 - 단위 테스트: `pytest` 사용. 일부 테스트는 REST 응답을 목(mock) 처리합니다.
 - 통합 테스트: 실제 API 호출이 필요한 테스트는 `RUN_INTEGRATION=1`로 활성화하여 수동으로 실행합니다(안전상 기본은 비활성).

## 추가 참고
 - 민감한 정보는 `.env`에 두고 절대 커밋하지 마세요.
 - 운영 환경에서 로그 디렉터리 권한 및 디스크 용량을 모니터링하세요 (로그 회전 설정이 있어도 장기간 미관리 시 디스크를 채울 수 있음).

## 기여
 - 버그 리포트 및 PR 환영합니다. 변경 시 간단한 설명과 재현 방법을 적어주시면 빠르게 리뷰하겠습니다.

```
