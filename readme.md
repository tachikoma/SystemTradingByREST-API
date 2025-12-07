# 쉽게 따라 만드는 주식자동매매시스템 (REST API & Poetry 버전)

## 프로젝트 개요
이 프로젝트는 키움 OpenAPI의 REST API와 WebSocket을 사용하는 한국 주식 시장을 위한 OS 독립적인 자동 트레이딩 봇입니다. 기존 ActiveX 기반의 프로젝트를 리팩터링하고, `Poetry`를 사용하여 의존성을 관리하도록 변경했습니다.

## 아키텍처
- **진입점 (`main.py`):** `Kiwoom` 클래스와 `RSIStrategy` 스레드를 초기화하고 실행합니다.
- **핵심 로직 (`strategy/RSIStrategy.py`):** RSI와 이동평균선을 기반으로 매매 신호를 생성하고 주문을 실행하는 주식 트레이딩 알고리즘을 포함합니다. `threading.Thread`를 상속받아 백그라운드에서 실행됩니다.
- **API 추상화 (`api/Kiwoom.py`):** 키움 REST API와 WebSocket을 사용하여 통신합니다. `requests` 라이브러리를 사용하여 HTTP 요청을 보내고, `websockets` 라이브러리를 사용하여 실시간 시세를 수신합니다.
- **종목 선정 (`util/make_up_universe.py`):** 네이버 금융에서 KOSPI/KOSDAQ 종목 정보를 스크래핑하여 ROE, PER 등의 지표를 기반으로 상위 200개 종목을 선정합니다.
- **데이터베이스 (`util/db_helper.py`):** SQLite를 사용하여 종목 유니버스 및 과거 시세 데이터를 캐시하여 빠른 시작과 API 요청 제한을 회피합니다.

## 요구 사항
1.  **Python 3.11** 이상이 설치되어 있어야 합니다.
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

### 2. 대체 방법: 환경 변수 직접 설정
필요한 경우, 환경 변수를 직접 설정할 수도 있습니다:

**macOS/Linux:**
```bash
export KIWOOM_APPKEY=your_app_key
export KIWOOM_SECRETKEY=your_secret_key
```

**Windows (Command Prompt):**
```cmd
set KIWOOM_APPKEY=your_app_key
set KIWOOM_SECRETKEY=your_secret_key
```

**Windows (PowerShell):**
```powershell
$env:KIWOOM_APPKEY="your_app_key"
$env:KIWOOM_SECRETKEY="your_secret_key"
```
`your_app_key`와 `your_secret_key`를 실제 키로 교체해주세요.

## 실행 방법
환경 변수를 설정한 후, 다음 명령어로 애플리케이션을 실행합니다.
```bash
poetry run python3.11 main.py
```
애플리케이션을 중지하려면 `Ctrl+C`를 누르세요.
