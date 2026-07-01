---
slug: value-strategy-github-actions
status: awaiting-approval
intent: clear
pending-action: write .omo/plans/value-strategy-github-actions.md
approach: >
  PBR ranking value strategy를 GitHub Actions에서 실행하는 독립 실행형
  one-shot 스크립트로 구현. 기존 `RSIStrategy._rebalance_value()` 로직과
  `util/make_up_universe.fetch_fundamental_data()` (pykrx)를 재사용.
  Kiwoom API 키는 GitHub Secrets로 주입. 매일 09:00 KST 실행.
---

# Draft: value-strategy-github-actions

## Components (topology ledger)

| id | outcome (one line) | status | evidence path |
|----|---------|--------|---------------|
| A | `scripts/run_value_strategy.py` 독립 실행 스크립트 | active | RSIStrategy.py:773-902 _rebalance_value, Kiwoom.py:28-60 인증, make_up_universe.py:1708-1740 fetch_fundamental_data |
| B | `.github/workflows/value-strategy.yml` Workflow | active | cron schedule: 0 0 * * 1-5, secret injection, pykrx 의존성 |
| C | GitHub Secrets 설정 (KIWOOM_REAL_APPKEY 등) | deferred | VPS `.env` 참조하여 수동 설정 |
| D | Telegram 알림 (GitHub Actions → Telegram) | deferred | `util.notifier.send_telegram_message()` 재사용 or simple requests |
| E | 로깅 (GitHub Actions Artifacts) | active | stdout/stderr → GHA artifact with `actions/upload-artifact` |
| F | 로컬 테스트용 Makefile entry / README | deferred | `make run-value-strategy` or `poetry run python scripts/run_value_strategy.py` |

## Open assumptions (announced defaults)

| assumption | adopted default | rationale | reversible? |
|------------|----------------|-----------|-------------|
| GitHub Actions Runner 환경 (ubuntu-latest) | Python 3.11 + 프로젝트 의존성 설치 | pyproject.toml 지정 | Yes |
| Kiwoom API 접근 | Runner public IP에서 직접 REST 호출 | Kiwoom REST API는 app key + secret 기반 인증, IP 제한 없음 | No (아키텍처 결정) |
| 실전/모의 전환 | Workflow 변수 MODE=mock|real 로 제어 | .env.example과 동일한 변수명 | Yes |
| PBR 데이터 | `fetch_fundamental_data()` (pykrx) 매번 새로 조회 | GitHub Actions stateless — 로컬 캐시 불가 | Yes |
| Secrets 변수명 | KIWOOM_REAL_APPKEY, KIWOOM_REAL_SECRETKEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID | .env.example 키 이름과 일치 | Yes |
| Universe | REALTIME_MAX_CODES + POLLING_MAX_CODES (기본 250) 대신 VALUE_HOLDINGS(10)개만 필요 | PBR 랭킹용으로 전체 universe 불필요 | Yes |
| 마켓 필터 | VALUE_MARKET_FILTER 기본 True (KOSPI200 > MA200) | 기존 value mode와 동일 | Yes |
| time_stop_loss | GitHub Actions one-shot에서는 미적용 (스크립트 실행 시점에 보유 종목 없음) | one-shot 실행 시에는 기존 보유 종목이 없으므로 시간 손절 불필요 | N/A |

## Findings (cited - path:lines)

### 1. 현재 VPS 실행 구조
- `scripts/watchdog.py` (187 lines) → `main.py --yes` 를 child process로 실행 (`watchdog.py:41-45`)
- `scripts/start_watchdog.sh`: `sh "$PROJECT_ROOT/watchdog.sh"` (`start_watchdog.sh:8`)
- `scripts/stop_watchdog.sh`: PID file (`run/watchdog.pid`) 기반 kill (`stop_watchdog.sh:5-24`)
- Watchdog는 신호 포워딩, 자동 재시작, Telegram 알림 지원 (`watchdog.py:48-150`)

### 2. main.py 실행 흐름
1. `.env` 로드 (`main.py:18-19`)
2. 로깅 초기화 (`main.py:22-23`)
3. Kiwoom 인스턴스 생성 (`main.py:69-70`): `Kiwoom(appkey=appkey, secretkey=secretkey, mock=is_mock)`
4. RSIStrategy 스레드 시작 (`main.py:74`): `RSIStrategy(kiwoom, universe_cache_mode=...)`
5. 메인 스레드는 sleep loop (`main.py:187-201`)
- API 키 선택: mock → `KIWOOM_MOCK_APPKEY` / real → `KIWOOM_REAL_APPKEY` (`main.py:36-43`)

### 3. `RSIStrategy._rebalance_value()` (RSIStrategy.py:773-902) — 핵심 로직
```
1. _value_rebalance_done_today 플래그 체크 (line 778)
2. check_transaction_open() — 장중 시간 확인 (line 782)
3. _check_market_filter() — KOSPI200 MA200 필터 (line 786)
   - VALUE_MARKET_FILTER=False 시 항상 통과 (line 745)
   - DB에서 069500 종가 200일 조회 → MA200 계산 (lines 748-767)
4. _refresh_fundamental_data() — pykrx로 전 종목 PER/PBR 조회 (line 796)
   - 내부: `util/make_up_universe.fetch_fundamental_data()` 호출 (line 726-727)
   - self.universe에 속한 종목만 self.fundamental에 저장 (lines 731-734)
5. Universe 종목 중 PBR 존재하는 것만 추려서 오름차순 정렬 (lines 803-811)
6. 상위 VALUE_HOLDINGS(기본 10)개 선택 (line 817)
7. 기존 보유 종목 중 밀려난 종목 매도 — 단 VALUE_KEEP_HOLDINGS 시 스킵 (lines 829-834)
8. 대상 종목 중 미보유/미주문 종목 매수 (lines 840-902)
   - 예산 계산: deposit / remaining_slots (line 865)
   - 호가 조회: _get_order_price() (line 867)
   - 수량 계산: min(budget/bid, deposit*MAX_POSITION_RATIO/bid) (lines 877-882)
   - 주문: kiwoom.send_order('send_buy_order', '1001', 0, code, quantity, bid, '00') (line 895)
```

### 4. `fetch_fundamental_data()` (make_up_universe.py:1708-1740)
```python
from pykrx import stock as krx_stock
df = krx_stock.get_market_fundamental_by_ticker(today, market='ALL')
# returns dict: {code: {'PER': float, 'PBR': float, 'EPS': float, 'BPS': float, 'DIV': float}, ...}
```

### 5. Kiwoom API (api/Kiwoom.py)
- `__init__(self, appkey, secretkey, mock=False)` (line 28)
- 인증 과정: appkey + secretkey → access token 발급 (내부 `_request`로 처리)
- `send_order(rqname, screen_no, order_type, code, quantity, price, order_classification, origin_order_number="")` (line 488)
- `get_balance(cont_yn='N', ...)` (line 683) — 현재 보유 종목 조회
- `get_deposit()` — 예수금 조회
- `mock=True` → 모의투자 API, `mock=False` → 실전투자 API

### 6. Value 전략 환경변수 (.env.example:121-147)
```
STRATEGY_MODE=value        # 전략 모드
VALUE_HOLDINGS=10          # 목표 보유 종목 수
VALUE_MARKET_FILTER=1      # KOSPI200 > MA200 필터
VALUE_MAX_BUDGET=0         # 최대 투자 금액 (0=무제한)
VALUE_KEEP_HOLDINGS=False  # 기존 종목 유지
TIME_STOP_LOSS_DAYS=90     # (one-shot에서는 미사용)
```

### 7. GitHub Actions Workflow 디렉토리
- 현재 `.github/workflows/` 디렉토리 없음 (`glob` 결과 0개)
- `.github/prompts/plan-resolveStockNamePlan.prompt.md` 만 존재
- 최초 생성 필요

### 8. 의존성 (pyproject.toml)
- pykrx (PBR/PER 데이터)
- requests, websockets (Kiwoom API)
- python-dotenv (환경변수 로드)
- pandas (데이터 처리)

## Decisions (with rationale)

1. **독립 실행 스크립트 `scripts/run_value_strategy.py` 생성**
   - 기존 RSIStrategy를 import하지 않고, `Kiwoom` 인스턴스 생성 + `fetch_fundamental_data()` 호출 + PBR 정렬 로직 + 주문 placement를 하나의 스크립트로 작성
   - rationale: 최소 의존성, 빠른 실행, 기존 봇과 완전 독립

2. **PBR 정렬 로직은 새로 작성**
   - `fetch_fundamental_data()` 전체 종목 조회 → 시총/거래대금 필터 적용 → PBR 정렬 → 상위 N개 선택
   - 기존 `_rebalance_value()`의 DB/캐시/WebSocket 의존성 제거

3. **GitHub Actions Workflow**
   - cron: `0 0 * * 1-5` = 평일 00:00 UTC / 09:00 KST
   - secrets: `${{ secrets.KIWOOM_REAL_APPKEY }}` 형태로 env 주입
   - run: `poetry install && poetry run python scripts/run_value_strategy.py`
   - artifacts: 로그 파일 업로드
   - **실전 모드(dry_run=false + mode=real)는 workflow 레벨에서 조건부 차단**: `if: !(inputs.mode == 'real' && inputs.dry_run != 'true')`

4. **모의투자 우선 → 추후 실전투자 전환**
   - Workflow 변수 `MODE` 로 제어 (default: mock)
   - 실전 전환 시 `MODE=real` + dry_run 단계적 진행

5. **pykrx 데이터는 실행 시 매번 새로 조회**
   - GitHub Actions stateless 특성상 캐시 불가
   - pykrx API 1회 호출은 부하가 낮음
   - **주의**: pykrx `get_market_fundamental_by_ticker()`는 **전일 기준** PBR 반환 — 09:00 KST 실행 시 전일 데이터로 리밸런싱

6. **시장 필터 (KOSPI200 MA200) — pykrx OHLCV로 대체**
   - DB 의존성을 없애고 pykrx `get_market_ohlcv_by_date("069500")`로 최근 200일 OHLCV 조회
   - `<close> > MA200` 계산 후 판정
   - 실패 시 필터 우회 (graceful fallback)

7. **유니버스 필터 — 시가총액 하위 10% 제외**
   - pykrx의 `get_market_cap_by_ticker()`로 전 종목 시총 조회
   - 시총 하위 10% 제외 (부실기업/유동성 부족 종목 필터링)
   - 옵션: `VALUE_MIN_MARKET_CAP` 환경변수로 절대값 기준支持
   - `VALUE_MARKET_FILTER_ONLY_KOSPI` 옵션으로 KOSPI/KOSDAQ150 제한 가능

8. **중복 실행 방지 — 당일 주문 내역 확인**
   - Kiwoom `get_order()`로 당일 체결된 매수 주문 조회
   - 이미 매수 주문이 접수된 종목은 건너뜀
   - 스크립트 내에서 당일 실행 기록을 간단한 플래그로 관리

9. **pykrx 데이터 시점 문서화**
   - pykrx `get_market_fundamental_by_ticker()`는 전일 기준 데이터를 반환
   - 이는 PBR이 일 단위로 느리게 변하므로 실전에서 문제되지 않음
   - 단, README와 스크립트 docstring에 "**전일 기준 PBR**로 리밸런싱" 명시

10. **실전 모드 안전장치**
    - `mode=real` && `dry_run=false` 일 때 스크립트 내에서 경고 메시지 출력 후 10초 대기 (Ctrl+C 취소 기회)
    - 대기 후에도 진행 시 `send_order()` 호출 전에 최종 확인 로그 출력
    - workflow 레벨: `if: !(inputs.mode == 'real' && inputs.dry_run != 'true')` 로 실전 모드 차단
    - 실전 전환은 README에 별도 절차로 문서화하고, workflow 변수로 한 번에 전환 불가능하게 설계

## Scope IN

- `scripts/run_value_strategy.py`: 독립 실행 스크립트
  - Kiwoom API 인증 (mock/real)
  - pykrx PBR 데이터 조회 (전일 기준)
  - 시가총액 필터 (하위 10% 제외) + KOSPI/KOSDAQ150 옵션
  - KOSPI200 MA200 시장 필터 (pykrx OHLCV 기반)
  - PBR 정렬 → 상위 N개 매수 주문
  - 중복 실행 방지 (당일 주문 내역 확인)
  - 실전 모드 안전 2중 장치 (workflow 차단 + 스크립트 내 경고 대기)
  - `--dry-run` 플래그 (기본값: true)
  - Telegram 알림 (성공/실패/오류)
  - 로깅 (stdout + 파일)
- `.github/workflows/value-strategy.yml`: GitHub Actions Workflow
  - cron schedule (평일 00:00 UTC = 09:00 KST)
  - workflow_dispatch (수동 실행)
  - `mode` + `dry_run` 입력 파라미터
  - secrets mapping (5개)
  - artifact upload (로그 파일)
  - 실전 모드 조건부 차단 (`if:` 조건)
- README 업데이트
  - GitHub Actions 설정 방법
  - Secrets 설정 가이드 (변수명, 출처)
  - pykrx 데이터 시점 주의사항
  - 실전 전환 절차

## Scope OUT (Must NOT have)

- 기존 RSI 전략 코드 수정 금지 (`strategy/RSIStrategy.py`, `main.py`, `scripts/watchdog.py`)
- WebSocket 사용 금지 (REST API만 사용)
- 데이터베이스 스키마 변경 금지
- Docker/VPS 인프라 수정 금지
- 장기 실행 데몬/워치독 금지
- UI/웹 인터페이스 금지
- **실전 모드(dry_run=false + mode=real) workflow 기본 허용 금지** — workflow 조건으로 차단
- GitHub Actions Runner 외부 인프라 의존성 금지
- 기존 전략의 env 파라미터(RSI_BUY_THRESHOLD 등) 변경 금지

## Open questions (resolved)

1. ~~069500(KOSPI200) 가격 데이터 출처~~ → pykrx `get_market_ohlcv_by_date()` 로 해결
2. ~~Universe 필터~~ → 시총 하위 10% 제외 + KOSPI/KOSDAQ150 옵션으로 해결
3. ~~Kiwoom API mock 테스트~~ → 실전 키만 Secrets 저장, MODE 변수로 제어
4. ~~pykrx 데이터 시점~~ → 전일 기준 PBR, README에 명시
5. ~~실전 모드 안전~~ → workflow 조건 + 스크립트 내 대기 이중 장치

## Approval gate

status: awaiting-approval
