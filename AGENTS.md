# PROJECT KNOWLEDGE BASE

**생성:** 2026-06-20 21:57 KST
**Commit:** `7e561e7` (`feature/new-exit`)
**Stack:** Python 3.11+, Poetry, Kiwoom REST API + WebSocket, SQLite, pytest

## OVERVIEW

키움증권 OpenAPI REST 기반 한국 주식 자동매매 봇. RSI + 이동평균 전략으로 KOSPI/KOSDAQ 종목 선정, 실시간 시세 모니터링, 자동 매매 실행. 백테스트 엔진 내장.

## STRUCTURE

```
./
├── api/                  # Kiwoom REST/WebSocket 클라이언트
├── strategy/             # RSIStrategy (매매 엔진, 2665라인)
├── util/                 # 공용 유틸리티 (14개 모듈)
├── backtest/             # 백테스트 엔진 + 분석 스크립트 (21개)
├── tests/                # pytest 단위/통합 테스트 (12개)
├── scripts/              # 보조 스크립트 (캐시 갱신, watchdog, mock 테스트)
├── data/                 # SQLite DB, Parquet 캐시 파일
├── docs/                 # API 문서, 비즈니스 기획
├── deploy/               # systemd 템플릿, logrotate
├── main.py               # 진입점
└── pyproject.toml        # Poetry 의존성 + 설정
```

## WHERE TO LOOK

| 작업 | 위치 | 비고 |
|------|------|------|
| 매매 전략 수정 | `strategy/RSIStrategy.py` | RSI_BUY_THRESHOLD 등 상수 변경 금지 |
| API 호출/인증 | `api/Kiwoom.py` | `_request()` retry + rate limit 처리 |
| 유니버스(종목) 선정 | `util/make_up_universe.py` | 키움 API + 네이버 크롤링 |
| DB/캐싱 | `util/db_helper.py` | SQLite 기반 |
| 시간/휴장일 처리 | `util/time_helper.py` | `get_korea_time()` 사용強制 |
| 로깅 설정 | `util/logging_config.py` | RotatingFileHandler |
| 종료/셧다운 | `util/shutdown.py` | signal handler + cleanup 등록 |
| 알림 | `util/notifier.py` | 텔레그램 전송 (notify_on_exception 데코레이터) |
| 백테스트 실행 | `backtest/run_backtest.py` | run_backtest → fetch_historical_data 선행 |
| 백테스트 분석 | `backtest/analyze_*.py` | 리스크/수익성/MDD 분석 |
| 테스트 실행 | `tests/` | `pytest` (integration 마커로 분리) |

## CONVENTIONS

- **시간:** `datetime.now()` 직접 사용 금지. `time_helper.get_korea_time()` 사용.
- **휴장일:** `is_market_closed_day()` + MARKET_HOLIDAYS_{YEAR} 상수 사용. 매년 초 업데이트.
- **초기화 순서:** `.env` 로드 → `configure_logging()` → 나머지 import.
- **API 호출:** `_request()` 래퍼 필수 사용. retry는 rate-limit 응답(return_code=5)에서만.
- **로깅:** `util.logging_config.get_logger(__name__)` 사용.
- **언어:** 채팅/문서/주석은 한국어. 기술 용어 모호시 영어 병기.
- **패키지:** Poetry. `pyproject.toml` 기반.

## ANTI-PATTERNS (THIS PROJECT)

- `as any`, `@ts-ignore` 계열 금지
- 민감 정보(.env) 커밋 금지
- `KIWOOM_MODE`·API 키·`KIW_*` 환경변수는 `main.py` 외에서 직접 변경 금지
- `--no-verify` git 플래그 사용 금지
- 전략 상수(`RSI_BUY_THRESHOLD=3`, `PRICE_DROP_THRESHOLD=-5.0`, `CASH_RESERVE_RATIO=0.2`, `enable_stop_loss=False`) 변경 금지

## COMMANDS

```bash
# 실행
poetry run python main.py                          # 모의투자
KIWOOM_MODE=real poetry run python main.py          # 실전투자

# 테스트
poetry run pytest                                   # 전체
poetry run pytest -m integration                    # 통합 테스트

# 백테스트
poetry run python -m backtest.fetch_historical_data  # 데이터 수집 (최초 1회)
poetry run python -m backtest.run_backtest           # 백테스트 실행

# 의존성
poetry add <package>
poetry install
```

## NOTES

- **Kiwoom REST API**는 모의(`mockapi.kiwoom.com`) / 실전(`api.kiwoom.com`) URL 분리.
- **WebSocket** 재연결시 구독 자동 재등록. 로그인 중복 방지 Lock 적용.
- **백테스트 출력물**은 `backtest/output/`에 누적됨 (467개 파일).
- **거래 수수료/세금**은 모의/실전 각각 환경변수로 분리 (`TRADING_FEE_PERCENT_MOCK` 등).
- **유니버스 캐시 모드:** `UNIVERSE_CACHE_MODE` (startup/eod/on_demand).
- **실전투자 확인 프롬프트**는 `-y`/`--yes` 플래그로 스킵 가능 (자동화용).
