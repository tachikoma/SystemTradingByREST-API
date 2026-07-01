# value-strategy-github-actions - Work Plan

## TL;DR (For humans)

**What you'll get:** A GitHub Actions workflow that executes the value strategy (PBR ranking-based) once daily as a one-shot process. It will fetch today's PBR data from pykrx, select top N low-PBR stocks, and place buy orders without running continuously on VPS.

**Why this approach:** The current VPS watchdog runs RSI strategy 24/7. For value strategy (PBR ranking rebalancing), we only need once-daily execution at market open. GitHub Actions provides free cron scheduling, built-in secret management, and zero infrastructure cost. The standalone script imports existing pykrx PBR logic and Kiwoom REST API, avoiding any modification to the running trading bot.

**What it will NOT do:** Run continuously or as a daemon. It won't modify `main.py`, `RSIStrategy.py`, or the VPS deployment. It won't use WebSocket or require database access.

**Effort:** Medium (~1-2 hours)
**Risk:** Low-Medium — Kiwoom API works with app key authentication from any IP; pykrx free API has no daily limit concerns
**Decisions already made:** Standalone script (not importing RSIStrategy); mock → real phased rollout; no DB/WebSocket dependency

---

> TL;DR (machine): Medium effort, Low-Medium risk. Deliverables: scripts/run_value_strategy.py (with 시총필터+마켓필터+중복방지+실전안전장치), .github/workflows/value-strategy.yml (with safety gate), README Secrets guide. Phased rollout: mock dry-run → mock live → real (별도 승인).

## Scope
### Must have
1. A standalone Python script that can be executed independently
2. GitHub Actions workflow file (.github/workflows/value-strategy.yml)
3. Daily execution at 09:00 KST (market open)
4. Proper secret management for Kiwoom API keys
5. Logging and error handling
6. One-shot execution (not continuous)
7. Telegram notification on completion/error
8. Dry-run mode for verification without real orders
9. 시장 필터 (KOSPI200 MA200, pykrx OHLCV 기반)
10. 유니버스 필터 (시총 하위 10% 제외, KOSPI/KOSDAQ150 옵션)
11. 중복 실행 방지 (당일 주문 내역 확인)
12. 실전 모드 안전 2중 장치 (workflow 차단 + 스크립트 경고 대기)
13. pykrx 데이터 시점 문서화 ("전일 기준 PBR")

### Must NOT have (guardrails, anti-slop, scope boundaries)
1. No modification of existing trading bot code
2. No continuous background processes
3. No interference with existing RSI strategy operations
4. No manual intervention required
5. No complex UI or web interface
6. No database schema changes
7. No WebSocket usage (REST API only)
8. No Docker/VPS infrastructure changes
9. **실전 모드(dry_run=false + mode=real) workflow 기본 허용 금지**
10. 기존 전략의 env 파라미터(RSI_BUY_THRESHOLD 등) 변경 금지

## Verification strategy
- Test decision: tests-after (dry-run mode for verification)
- Evidence: .omo/evidence/task-<N>-value-strategy-github-actions.<ext>
  - Task 1: dry-run output (PBR ranking table)
  - Task 2: workflow YAML syntax validation
  - Task 3: README review
  - Task 4: GitHub Actions workflow dispatch result
  - Task 5: mock mode run log + Telegram screenshot (if available)

## Execution strategy
### Parallel execution waves

**Wave 1 — Create & verify standalone script**
Script creation, GitHub Actions workflow, documentation, local dry-run test.

**Wave 2 — Deploy & validate on GitHub**
Push to GitHub, GitHub Actions dispatch test, mock mode run.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1. run_value_strategy.py | - | 3, 4 | - |
| 2. GitHub Actions workflow | - | 4 | 1 |
| 3. Telegram helper & README | 1 | 4 | 2 |
| 4. Local dry-run test | 1, 2, 3 | 5 | - |
| 5. Push & GitHub Actions test | 4 | 6 | - |
| 6. Mock mode verification | 5 | 7 | - |
| 7. 실전 모드 안전장치 검증 | 6 | F | - |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

### Wave 1 — Create & verify standalone script

- [ ] 1. `scripts/run_value_strategy.py` — 독립 실행 스크립트 생성
  What to do / Must NOT do:
  - pykrx PBR 데이터 조회 → 시총 필터 → 저PBR 상위 N개 선정 → Kiwoom 주문을 수행하는 독립 실행 스크립트 생성
  - 핵심 임포트: `from util.make_up_universe import fetch_fundamental_data` (pykrx PBR), `from api.Kiwoom import Kiwoom` (REST API)
  - 실행 흐름:
    1. 환경변수 로드 (KIWOOM_MODE, KIWOOM_REAL_APPKEY, KIWOOM_REAL_SECRETKEY, VALUE_HOLDINGS=10, VALUE_MARKET_FILTER=1, VALUE_MAX_BUDGET=0, VALUE_MIN_MARKET_CAP, VALUE_MARKET_FILTER_ONLY_KOSPI, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    2. `--dry-run` 플래그 지원 (기본 true, 실제 주문은 건너뜀)
    3. `fetch_fundamental_data()` 로 전체 종목 PBR 조회
    4. **유니버스 필터** (신규):
       - pykrx `get_market_cap_by_ticker()`로 전 종목 시가총액 조회
       - 시총 하위 10% 제외 (또는 `VALUE_MIN_MARKET_CAP` 절대값 적용)
       - `VALUE_MARKET_FILTER_ONLY_KOSPI=true` 시 KOSPI/KOSDAQ150으로 제한
    5. **시장 필터** (신규):
       - `VALUE_MARKET_FILTER=true` 시 pykrx `get_market_ohlcv_by_date("069500")` 로 200일 OHLCV 조회
       - `close > MA200` 계산, 실패 시 우회 (graceful fallback)
    6. PBR > 0 이고 PBR < inf 인 종목만 필터 → PBR 오름차순 정렬
    7. 상위 VALUE_HOLDINGS(=10)개 선택
    8. PBR 순위표 출력 (순위, 종목코드, 종목명, PBR, PER, 시가총액)
    9. **중복 실행 방지** (신규): `--dry-run=false` 시 Kiwoom `get_order()`로 당일 체결 매수 주문 조회 → 이미 주문된 종목 제외
    10. **실전 모드 안전장치** (신규):
        - `mode=real` && `--dry-run=false` 시 강력 경고 메시지 출력 + 10초 카운트다운 (Ctrl+C 취소 가능)
        - 대기 후에도 진행 시 최종 확인 로그 출력 후 주문 실행
    11. Kiwoom 인증 → 보유 잔고 확인 → 미보유 종목 매수 주문
    12. Telegram 알림 전송 (성공/실패/PBR 순위표)
    13. **pykrx 데이터 시점 문서화**: 스크립트 첫 줄 로그 및 README에 "전일 기준 PBR" 명시
  - 에러 핸들링: 모든 주요 단계 try/except → stderr 로깅 + Telegram 알림
  - Must NOT: main.py, RSIStrategy.py, watchdog.py 등 기존 파일 수정 금지
  - Must NOT: WebSocket 사용 금지
  - Must NOT: 데이터베이스 의존성 추가 금지
  - Must NOT: 실전 모드 dry_run=false 기본값 금지
  References:
  - `util/make_up_universe.py:1708-1740` — `fetch_fundamental_data()` (pykrx)
  - `api/Kiwoom.py:28-60` — Kiwoom 인증
  - `api/Kiwoom.py:488` — `send_order()` API
  - `api/Kiwoom.py:683` — `get_balance()` API
  - `api/Kiwoom.py:623` — `get_order()` API (당일 주문 조회)
  - `strategy/RSIStrategy.py:773-902` — `_rebalance_value()` 참고 로직
  - `strategy/RSIStrategy.py:740-770` — `_check_market_filter()` 참고
  - `.env.example:121-147` — Value 전략 환경변수
  - `util/notifier.py` — Telegram 알림 (선택적 import)
  - `main.py:36-43` — API 키 선택 로직 (mock/real)
  - pykrx docs: `get_market_ohlcv_by_date()`, `get_market_cap_by_ticker()`
  Acceptance criteria:
  - `poetry run python scripts/run_value_strategy.py --dry-run` 실행 성공
  - PBR 순위표가 stdout에 출력됨 (순위, 종목코드, 종목명, PBR, 시가총액 포함)
  - 시총 하위 10%가 제외된 결과 확인
  - 실제 주문이 발생하지 않음
  - `MODE=real scripts/run_value_strategy.py` 실행 시 경고 카운트다운 출력
  모드별 차이점:
  - mock 모드: `KIWOOD_MODE=mock` + `KIWOOM_MOCK_APPKEY`
  - real 모드: `KIWOOM_MODE=real` + `KIWOOM_REAL_APPKEY`
  - 기본값 mock
  QA scenarios:
  - happy: `poetry run python scripts/run_value_strategy.py --dry-run` → PBR 순위표 출력 후 정상 종료 (시총 필터 적용 확인)
  - success: --dry-run=false + mode=mock → Kiwoom 모의투자 API로 주문 접수 확인
  - safety: mode=real + --dry-run=false → 경고 메시지 + 카운트다운 + 사용자 인터럽트 가능 확인
  - failure: pykrx 네트워크 오류 시 → 적절한 에러 메시지 로깅 && Telegram 알림
  Evidence: `.omo/evidence/task-1-dry-run-output.txt` (command output capture)
  Commit: Y | `feat: value strategy standalone execution script with market cap filter, safety guards`

- [ ] 2. `.github/workflows/value-strategy.yml` — GitHub Actions Workflow 생성
  What to do / Must NOT do:
  - `.github/workflows/value-strategy.yml` 생성
  - Workflow 구조:
    ```yaml
    name: Value Strategy Daily Run
    on:
      schedule:
        - cron: '0 0 * * 1-5'   # 평일 00:00 UTC = 09:00 KST
      workflow_dispatch:
        inputs:
          mode:
            description: 'Trading mode (mock/real)'
            required: true
            default: 'mock'
          dry_run:
            type: boolean
            description: 'Dry-run (no actual orders)'
            required: true
            default: true
    ```
  - **실전 모드 조건부 차단** (신규):
    ```yaml
    jobs:
      run-value:
        if: >
          !(github.event.inputs.mode == 'real' && github.event.inputs.dry_run != 'true')
          || github.event_name == 'schedule'
        ...
    ```
  - Secrets 매핑 (env:):
    - KIWOOM_MODE: ${{ inputs.mode || 'mock' }}
    - KIWOOM_REAL_APPKEY: ${{ secrets.KIWOOM_REAL_APPKEY }}
    - KIWOOM_REAL_SECRETKEY: ${{ secrets.KIWOOM_REAL_SECRETKEY }}
    - TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
    - TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
    - VALUE_HOLDINGS, VALUE_MARKET_FILTER, VALUE_MAX_BUDGET, VALUE_MIN_MARKET_CAP, VALUE_MARKET_FILTER_ONLY_KOSPI
  - Steps:
    1. Checkout
    2. Set up Python 3.11
    3. Poetry install (or pip install .)
    4. Run: `poetry run python scripts/run_value_strategy.py`
       - dry_run: ${{ inputs.dry_run != false || github.event_name == 'schedule' }}
    5. Upload logs artifact: `actions/upload-artifact@v4`
  - schedule 실행 시 dry_run=true (기본 안전)
  - Must NOT: secrets를 로그에 출력하거나 artifact에 포함
  References:
  - GitHub Actions docs: cron syntax, secrets, workflow_dispatch, `if:` 조건
  - `scripts/run_value_strategy.py` (Task 1 출력물)
  Acceptance criteria:
  - `yamllint .github/workflows/value-strategy.yml` 통과
  - workflow_dispatch(mode=real, dry_run=false) 실행 시 job skipped
  - workflow_dispatch(mode=mock, dry_run=true) 실행 시 정상 동작
  QA scenarios:
  - happy: `git push` → GitHub Actions 탭에 workflow 표시 → 수동 dispatch → 성공
  - safety: mode=real,dry_run=false dispatch → job skipped (자물쇠 아이콘)
  - failure: secrets 누락 시 → graceful fail + Telegram 알림
  Evidence: `.omo/evidence/task-2-workflow-lint.txt`
  Commit: Y | `ci: value strategy daily workflow with dry-run safety gate`

- [ ] 3. Telegram 알림 통합 & README 문서화
  What to do / Must NOT do:
  - `scripts/run_value_strategy.py`가 완료 시 Telegram 알림을 보내도록 구현
    - `util.notifier.send_telegram_message()` 재사용 (혹은 없는 경우 requests로 직접 구현)
    - 알림 내용: 날짜, 선택된 종목 리스트 (PBR 포함), 매수 주문 수량, 예수금, 오류 발생 시 상세
  - README 업데이트:
    - GitHub Actions 설정 방법 섹션 추가
    - GitHub Secrets 설정 방법 (키 이름, 값 출처)
    - workflow_dispatch 실행 방법
    - dry-run 모드 설명
  - Must NOT: README에 실제 API 키 노출
  - Must NOT: 기존 README 내용 삭제
  References:
  - `util/notifier.py` — Telegram 전송 함수
  - `README.md` — 기존 문서 구조
  Acceptance criteria:
  - `grep -q "GitHub Actions" README.md` → True
  - README에 secrets.KIWOOM_REAL_APPKEY 등 4개 이상의 secrets 변수명 명시
  QA scenarios:
  - happy: README 지침대로 GitHub Secrets 설정 후 workflow_dispatch 성공
  - failure: secrets 누락 시 workflow이지만 script에서 graceful fail
  Evidence: `.omo/evidence/task-3-readme-update.txt`
  Commit: Y | `docs: GitHub Actions value strategy setup guide`

- [ ] 4. 로컬 dry-run 테스트
  What to do / Must NOT do:
  - 프로젝트 루트에서 `poetry run python scripts/run_value_strategy.py --dry-run` 실행
  - stdout 출력 확인: PBR 순위표 (종목코드, 종목명, PBR, PER) 상위 10개
  - `--dry-run` 모드이므로 Kiwoom API 호출하지 않고 pykrx만 호출
  - 출력 결과를 `.omo/evidence/task-4-dry-run-output.txt` 저장
  - Must NOT: 실제 Kiwoom 주문 발생
  - Must NOT: .env 파일에서 real API 키 사용
  References:
  - `scripts/run_value_strategy.py` (Task 1)
  Acceptance criteria:
  - dry-run 출력에 "순위, 종목코드, PBR" 헤더 포함
  - 최소 10개 종목 출력
  - Exit code 0
  QA scenarios:
  - happy: 예상된 PBR 순위표 출력
  - failure: pykrx 오류 시 → stderr 에러 메시지 확인
  Evidence: `.omo/evidence/task-4-dry-run-output.txt`
  Commit: N (test evidence only)

### Wave 2 — Deploy & validate on GitHub

- [ ] 5. GitHub 푸시 & workflow_dispatch 검증
  What to do / Must NOT do:
  - 현재 브랜치가 feature/value-strategy-github-actions (or equivalent)인지 확인
  - `git push origin HEAD` 실행
  - GitHub Actions 탭에서 workflow_dispatch로 수동 실행 (mode=mock, dry_run=true)
  - Actions 실행 로그 확인:
    - Checkout, Python setup, 의존성 설치 성공 여부
    - `run_value_strategy.py --dry-run` 실행 로그
    - Telegram 알림 정상 발송 여부
  - 실행 로그 스크린샷 or 로그 캡처
  - Must NOT: 실전 모드로 실행
  - Must NOT: secrets가 로그에 노출되는지 확인 (노출 시 즉시 중단)
  References:
  - Task 2 workflow 파일
  - GitHub Actions UI
  Acceptance criteria:
  - GitHub Actions run이 성공 (초록색 체크)
  - dry-run PBR 순위표 출력 확인
  - Secrets가 로그에 노출되지 않음
  QA scenarios:
  - happy: workflow 성공, 올바른 PBR 순위표 출력, Telegram 알림 수신
  - failure: workflow 실패 → run log 확인 후 수정
  Evidence: `.omo/evidence/task-5-gha-result.txt`
  Commit: N

- [ ] 6. 모의투자(mock) 모드 실전 검증
  What to do / Must NOT do:
  - workflow_dispatch로 mode=mock, dry_run=false 실행
  - Kiwoom 모의투자 키가 GitHub Secrets에 설정되어 있어야 함
  - 실행 로그 확인:
    - Kiwoom 인증 성공 (모의투자)
    - 보유 잔고 조회
    - 시가총액 필터 적용 확인
    - PBR 순위표 출력
    - 당일 주문 내역 확인 (중복 방지)
    - 실제 주문 접수 확인 ("매수 주문 접수" 로그)
  - 로그를 `.omo/evidence/task-6-mock-run.txt` 저장
  - Must NOT: 실전투자 키 사용 (mode=real 금지)
  - Must NOT: Secrets 노출 확인
  References:
  - Task 5 결과
  - `scripts/run_value_strategy.py`
  Acceptance criteria:
  - GitHub Actions run 성공
  - "매수 주문 접수" 로그 출력 확인 (모의투자)
  - 예수금 차감 확인
  QA scenarios:
  - happy: 모든 단계 성공, 주문 접수 확인
  - failure: Kiwoom 인증 실패 시 → 적절한 에러 로깅 && Telegram 알림
  Evidence: `.omo/evidence/task-6-mock-run.txt`
  Commit: N

- [ ] 7. 실전 모드 안전장치 검증
  What to do / Must NOT do:
  - workflow_dispatch로 mode=real, dry_run=false 실행
  - **workflow 레벨 차단 확인**: job이 skipped되어야 함 (자물쇠 아이콘)
  - 스크립트 로컬 테스트: `mode=real dry-run false`로 직접 실행
    - 10초 카운트다운 경고 메시지 출력 확인
    - Ctrl+C로 취소 가능 확인
    - 취소 없이 진행 시 최종 확인 로그 출력 확인
  - 실제 주문이 발생하지 않음을 확인
  - Must NOT: 실제 Kiwoom 실전 API 호출
  - Must NOT: workflow job이 실행됨
  References:
  - Task 2 workflow 조건부 차단
  - `scripts/run_value_strategy.py` 실전 안전장치 로직
  Acceptance criteria:
  - workflow_dispatch(mode=real, dry_run=false) → job skipped
  - 스크립트 직접 실행 시 경고 메시지 + 10초 카운트다운 출력
  - 실제 주문 0건
  QA scenarios:
  - safety: workflow skip 확인
  - safety: 스크립트 내 경고 + 대기 + 취소 가능 확인
  Evidence: `.omo/evidence/task-7-safety-gate.txt`
  Commit: N

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. **Plan compliance audit**
  - 모든 Todo 완료 여부 확인
  - scope IN 항목 모두 충족
  - scope OUT 항목 침범 없음
  - **5가지 개선사항 반영 확인** (시장필터, 유니버스필터, 중복방지, 데이터시점문서화, 실전안전장치)
  - Evidence: `.omo/evidence/f1-compliance.md`
- [ ] F2. **Code quality review**
  - `scripts/run_value_strategy.py`: 시총 필터, 시장 필터, 중복 방지, 실전 안전장치, Telegram 알림, 에러 핸들링 포함 확인
  - `.github/workflows/value-strategy.yml`: 세이프티 게이트, secrets 노출 위험 없음, 적절한 cron
  - README: Secrets 설정 가이드 명확, pykrx 전일 데이터 주의사항 명시, 실전 전환 절차 명시
  - Evidence: `.omo/evidence/f2-code-review.md`
- [ ] F3. **GitHub Actions 동작 검증**
  - workflow_dispatch(mock, dry_run=true) → 성공
  - workflow_dispatch(mock, dry_run=false) → 실제 주문 접수 확인
  - workflow_dispatch(real, dry_run=false) → job **skipped** (안전장치)
  - Evidence: `.omo/evidence/f3-gha-verification.md`
- [ ] F4. **Scope fidelity**
  - 기존 RSI 전략 코드 미수정 확인 (git diff)
  - WebSocket 미사용 확인
  - 데이터베이스 스키마 미변경 확인
  - 시총 하위 10% 제외 확인
  - 당일 주문 중복 확인 로직 존재 확인
  - 실전 모드 이중 안전장치 존재 확인
  - Evidence: `.omo/evidence/f4-scope-fidelity.md`

## Commit strategy
- Todo 1: `feat: value strategy standalone execution script with market cap filter, safety guards`
- Todo 2: `ci: value strategy daily workflow with dry-run safety gate`
- Todo 3: `docs: GitHub Actions value strategy setup guide with safety precautions`
- Todo 4-7: 증거만 저장, 커밋 불필요

## Success criteria
- [ ] `poetry run python scripts/run_value_strategy.py --dry-run` 로 PBR 순위표 출력 (시총 필터 적용)
- [ ] 시총 하위 10% 제외 확인
- [ ] GitHub Actions Workflow가 평일 09:00 KST에 자동 실행
- [ ] mock 모드에서 Kiwoom 모의투자 API로 주문 접수 성공
- [ ] **실전 모드(mode=real, dry_run=false) workflow skip 확인**
- [ ] Telegram 알림 정상 발송 (실행 결과, 오류)
- [ ] 실전 전환은 별도 절차 필요 (workflow 변수만으로 전환 불가)
- [ ] 기존 RSI 전략 코드 미변경 확인
- [ ] pykrx 전일 데이터 주의사항 README에 명시
