# 쉽게 따라 만드는 주식자동매매시스템 (REST API 버전)

## 프로젝트 개요
이 프로젝트는 키움 OpenAPI의 REST API와 WebSocket을 사용하는 한국 주식 시장을 위한 OS 독립적인 자동 트레이딩 봇입니다. 기존 ActiveX 기반의 프로젝트를 리팩터링하여 Windows 종속성을 제거했습니다.

## 아키텍처
- **진입점 (`main.py`):** `Kiwoom` 클래스와 `RSIStrategy` 스레드를 초기화하고 실행합니다. PyQt5 종속성이 제거되었습니다.
- **핵심 로직 (`strategy/RSIStrategy.py`):** RSI와 이동평균선을 기반으로 매매 신호를 생성하고 주문을 실행하는 주식 트레이딩 알고리즘을 포함합니다. `threading.Thread`를 상속받아 백그라운드에서 실행됩니다.
- **API 추상화 (`api/Kiwoom.py`):** 키움 REST API와 WebSocket을 사용하여 통신합니다. `requests` 라이브러리를 사용하여 HTTP 요청을 보내고, `websockets` 라이브러리를 사용하여 실시간 시세를 수신합니다.
- **종목 선정 (`util/make_up_universe.py`):** 네이버 금융에서 KOSPI/KOSDAQ 종목 정보를 스크래핑하여 ROE, PER 등의 지표를 기반으로 상위 200개 종목을 선정합니다.
- **데이터베이스 (`util/db_helper.py`):** SQLite를 사용하여 종목 유니버스 및 과거 시세 데이터를 캐시하여 빠른 시작과 API 요청 제한을 회피합니다.

## 요구 사항
1.  Conda 환경을 생성하고 활성화합니다.
    ```bash
    conda env create -f environment.yml
    conda activate system_trading
    ```
2.  필요한 라이브러리는 `environment.yml` 파일에 명시되어 있습니다.

## 설정
애플리케이션을 실행하기 전에, 키움증권에서 발급받은 API 키를 환경 변수로 설정해야 합니다.

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
python main.py
```
애플리케이션을 중지하려면 `Ctrl+C`를 누르세요.