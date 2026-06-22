# rsi-to-value-transition - Work Plan

## TL;DR (For humans)

**What you'll get:** 두 단계로 RSI 전략을 가치 전략(PBR 저평가)으로 전환합니다.
1단계(지금): VPS는 그대로 develop 브랜치 RSI 모드로 유지, -29% 종목은 자연 매도 대기.
2단계(RSI 종목 청산 후): feature/strategy-research를 develop에 머지 → .env에 `STRATEGY_MODE=value` 설정 → VPS 재시작 → PBR 기반 가치투자 시작.

**Why this approach:** 
- -29% 종목을 지금 강제 청산하면 손실이 확정됩니다. RSI 반등 시 breakeven 이상에서 자연 매도되는 것이 최선입니다.
- feature/strategy-research 브랜치의 RSI 모드는 매도 조건이 현재보다 더 엄격하므로(RSI>80 + 목표수익률>10%), -29% 종목 매도가 더 어려워집니다. 따라서 **매도 완료 후에 머지**해야 합니다.
- VALUE_KEEP_HOLDINGS=1을 설정하면 기존 RSI 종목을 강제 청산하지 않고 가치 전략이 신규 포지션만 채웁니다.

**What it will NOT do:**
- 현재 보유한 RSI 종목을 강제/수동 청산하지 않습니다.
- 긴급청산(EMERGENCY_LIQUIDATION)을 활성화하지 않습니다.
- feature/strategy-research의 RSI 모드 매도 조건(RSI>80, 목표수익률>10%)을 develop에 적용하지 않습니다.

**Effort:** Short (Phase 1: 모니터링 외 무대응, Phase 2: 머지+설정변경+재시작 = 30분)
**Risk:** Medium (가치 전략은 백테스트 검증 필요, PBR 데이터 품질에 의존)
**Decisions to sanity-check:** TIME_STOP_LOSS_DAYS=90 (develop 180→feature 90)로 머지 시 기존 보유 종목의 시간손절이 조기 발동할 수 있음. 단 VALUE 모드에서는 time-stop-loss 미실행되어 실제 영향 없음.

**Wave 1 ✅ 완료 (2026-06-22):** 전환 청사진, 머지 충돌 분석, .env 블루프린트, VPS 런북 모두 작성 완료.

Your next move: **Phase 1 (대기):** VPS develop 유지, -29% 종목 반등하여 breakeven+RSI>70 매도 대기.
  **Phase 2:** RSI 종목 전량 매도 확인 후 `$start-work` 로 머지+전환 실행.

---

> TL;DR (machine): Short effort, Medium risk. 2-phase: (1) keep develop RSI mode, wait for -29% exit; (2) merge feature/strategy-research, enable VALUE mode. Wave 1 prepares artifacts (merge-prep, .env blueprint, runbook). Wave 2 is trigger-based execution.

## Scope
### Must have
- VPS develop 브랜치 유지 (Phase 1)
- feature/strategy-research → develop 머지 준비 (충돌 사전 해결)
- VALUE 모드 전환 .env 블루프린트 작성
- VPS 재시작 절차 문서화 (docker-compose)
- Phase 2 실행 시 검증 (텔레그램 알림, 로그 확인, 1일 후 리밸런싱 확인)

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **현재 develop의 RSI_SELL_THRESHOLD=70, PROFIT_TARGET_PERCENT=0.0 유지** (feature 브랜치 머지 전까지)
- **긴급청산 활성화 금지** (RSI_EMERGENCY_LIQUIDATION_ENABLED=0 유지)
- RSI_BUY_THRESHOLD(3), PRICE_DROP_THRESHOLD(-5%), CASH_RESERVE_RATIO(0.2) 등 핵심 파라미터 수정 금지 (copilot-instructions 보호)
- 새 전략 코드 작성 금지 (feature 브랜치 기존 코드만 사용)
- .env API 키 수정 금지
- Phase 2에서 VALUE_KEEP_HOLDINGS=1은 반드시 설정 (기존 종목 보호)

## Verification strategy
- Test decision: tests-after (Phase 2 머지 후 테스트 실행)
- Evidence: .omo/evidence/
  - Git merge log
  - .env 블루프린트
  - Phase 2 실행 시 텔레그램 스크린샷 or 로그 발췌

## Execution strategy
### Parallel execution waves

**Wave 1 — Phase 1: 전환 준비 (지금 실행 가능)**
전환에 필요한 모든 아티팩트를 미리 준비합니다. VPS는 건드리지 않습니다.

**Wave 2 — Phase 2: RSI 종목 매도 후 실행 (트리거 기반)**
RSI 종목(-29% 외 다른 종목 포함)이 모두 청산된 후 실행합니다. 이 시점을 사용자가 판단하여 $start-work 명령으로 시작합니다.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1. 브랜치 차이 분석 문서화 | - | 2, 3 | - |
| 2. 머지 충돌 해결 | 1 | 3, 4 | - |
| 3. .env 블루프린트 작성 | 1 | 4 | 2 |
| 4. VPS 전환 런북 작성 | 2, 3 | (Phase 2 시작 조건) | - |
| 5. 머지 실행 (Phase 2) | 4 | 6, 7 | - |
| 6. VPS .env 업데이트 | 5 | 7 | - |
| 7. VPS 재시작 및 검증 | 6 | - | - |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

### Wave 1 — Phase 1 준비 (지금 실행)

- [ ] 1. `strategy/RSIStrategy.py` 브랜치 간 차이점 분석 및 전환 영향 문서화
  What to do / Must NOT do:
  - `feature/strategy-research`의 RSIStrategy.py에서 develop 대비 변경된 모든 파라미터를 나열
  - 특히 RSI 모드 매도 조건 변화(SELL_THRESHOLD 70→80, PROFIT_TARGET 0→10%)가 -29% 종목에 미치는 영향 분석
  - VALUE 모드로 전환 시 RSI 보유 종목의 처리 방식(매도되지 않고 영구 보유) 분석 및 문서화
  - Must NOT: 실제 코드 수정 금지, 단순 분석/문서화만
  References:
  - develop: `strategy/RSIStrategy.py:78-82` (RSI_SELL_THRESHOLD=70, PROFIT_TARGET_PERCENT=0.0, TIME_STOP_LOSS_DAYS=180)
  - develop: `strategy/RSIStrategy.py:2160-2161` (sell condition: RSI>70 AND close>breakeven)
  - feature: `strategy/RSIStrategy.py:112-116` (RSI_SELL_THRESHOLD=80, PROFIT_TARGET_PERCENT=10.0, TIME_STOP_LOSS_DAYS=90)
  - feature: `strategy/RSIStrategy.py:2412-2416` (sell condition: RSI>80 AND close>breakeven AND close>=target_price)
  - feature: `strategy/RSIStrategy.py:773-900` (VALUE 모드 _rebalance_value 함수)
  Acceptance criteria: 위 분석 내용을 `.omo/evidence/branch-diff-analysis.md`에 문서화 완료
  QA scenarios:
  - happy: `git diff develop feature/strategy-research -- strategy/RSIStrategy.py` 실행 결과와 문서가 일치
  - failure: 문서 누락 시 불완전으로 간주
  Evidence: `.omo/evidence/task-1-branch-diff-analysis.md`
  Commit: Y | `docs: RSI→VALUE 전환 영향 분석 문서화`

- [ ] 2. `feature/strategy-research` → `develop` 머지 충돌 사전 해결
  What to do / Must NOT do:
  - 로컬에서 `git checkout develop && git merge feature/strategy-research --no-commit` 실행
  - 충돌(conflict) 발생 시 해결 (충돌 파일 목록 확인 및 해결 방안 기록)
  - 충돌이 없다면 미리 해결할 것은 없음 — 대신 diff만 기록
  - Must NOT: 머지 결과를 원격에 푸시하거나 VPS에 적용 금지 (Phase 2에서 실행)
  References:
  - 현재 브랜치: `feature/strategy-research`
  - 대상: `develop`
  - 사전 정보: `git diff develop feature/strategy-research --stat` → 23개 파일 변경, +2159/-2473 lines
  Acceptance criteria: `git merge --no-commit --no-ff` 결과 충돌 0건 확인
  QA scenarios:
  - happy: 충돌 없음 → 결과를 `.omo/evidence/merge-prep-result.md`에 기록
  - failure: 충돌 발생 → 해결 방안 상세 기록
  Evidence: `.omo/evidence/task-2-merge-prep.md`
  Commit: N (머지 결과는 Phase 2에서만 커밋)

- [ ] 3. VALUE 모드 전환 `.env` 블루프린트 작성
  What to do / Must NOT do:
  - VPS `.env`에 추가/변경할 값들을 정확히 명시
  - 각 값의 설명, 기본값, feature 브랜치 기본값과의 차이를 주석으로 포함
  - Must NOT: 실제 .env 파일 수정 금지 (VPS에서 직접 수정)
  References:
  - `.env.example` (feature/strategy-research): Value Strategy 섹션 (STRATEGY_MODE, VALUE_HOLDINGS, VALUE_KEEP_HOLDINGS 등)
  - `strategy/RSIStrategy.py:321-358` (feature): VALUE 모드 환경변수 파싱 로직
  Acceptance criteria: 다음 값이 포함된 `.omo/evidence/value-mode-env-blueprint.md` 완성
  ```
  # Phase 2 전환 시 .env 변경값:
  STRATEGY_MODE=value       # RSI → VALUE 전환
  VALUE_KEEP_HOLDINGS=1     # 기존 RSI 보유 종목 유지
  VALUE_HOLDINGS=10         # 목표 보유 종목 수 (기본값)
  VALUE_MARKET_FILTER=1     # KOSPI200 MA200 필터 ON
  TIME_STOP_LOSS_DAYS=90    # feature 브랜치 기본값 (주의: 기존 종목 즉시 시간손절 가능)
  # 유지할 값 (변경 불필요):
  # RSI_SELL_THRESHOLD=70 (VALUE 모드에서 미사용)
  # CASH_RESERVE_RATIO=0.2 (VALUE 모드에서 미사용)
  ```
  QA scenarios:
  - happy: 블루프린트의 모든 키가 `git show feature/strategy-research:.env.example`의 VALUE 섹션과 일치
  - failure: 누락된 키가 있으면 불완전
  Evidence: `.omo/evidence/task-3-env-blueprint.md`
  Commit: Y | `docs: VALUE 모드 전환 .env 블루프린트 작성`

- [ ] 4. VPS 전환 실행 런북 작성
  What to do / Must NOT do:
  - Phase 2 실행 시 VPS에서 수행할 정확한 명령어 시퀀스 작성 (복붙 가능하게)
  - SSH 접속 → git pull → .env 수정 → docker-compose 재시작 → 로그 확인 순서
  - must NOT: VPS에 직접 접속하거나 명령 실행 금지 (Phase 2에서만)
  References:
  - `deploy/systemtrading-watchdog.service.template` (systemd 템플릿)
  - `docker-compose.yml` (Docker 서비스 설정)
  - `Dockerfile` (ENTRYPOINT: docker-entrypoint.sh, CMD: python main.py)
  Acceptance criteria: 다음 항목을 포함한 `.omo/evidence/vps-runbook.md` 완성
  ```
  ## VPS 전환 런북 (Phase 2 실행 시)
  
  ### 사전 조건
  - 로컬에서 feature/strategy-research → develop 머지 완료 및 원격 푸시
  - 텔레그램 봇 동작 확인
  
  ### Step 1: VPS 접속
  ssh user@vps-ip
  
  ### Step 2: develop 브랜치 최신 코드 가져오기
  cd /path/to/project
  git checkout develop
  git pull origin develop
  
  ### Step 3: .env 파일 백업 및 수정
  cp .env .env.backup.$(date +%Y%m%d_%H%M%S)
  # .env에 아래 값 추가/수정:
  # STRATEGY_MODE=value
  # VALUE_KEEP_HOLDINGS=1
  # VALUE_HOLDINGS=10
  # VALUE_MARKET_FILTER=1
  # TIME_STOP_LOSS_DAYS=90
  
  ### Step 4: docker-compose 재시작
  docker-compose down
  docker-compose up -d
  
  ### Step 5: 로그 확인
  docker-compose logs --tail=50 -f
  
  ### Step 6: 텔레그램 알림 확인
  # "전략 모드: Value" 메시지 확인
  
  ### Step 7: 장중 리밸런싱 확인
  # 다음 거래일 장중 VALUE 리밸런싱 로그 확인
  ```
  QA scenarios:
  - happy: 각 Step이 구체적이고 복붙 가능한 명령어로 작성됨
  - failure: 모호한 설명이 포함되면 수정
  Evidence: `.omo/evidence/task-4-vps-runbook.md`
  Commit: Y | `docs: VPS VALUE 모드 전환 런북 작성`

### Wave 2 — Phase 2 실행 (RSI 종목 매도 후, 사용자 트리거)

- [ ] 5. `feature/strategy-research` → `develop` 머지 실행
  What to do / Must NOT do:
  - 로컬에서 `git checkout develop && git merge feature/strategy-research` 실행
  - 충돌 해결 (Todo 2에서 사전 분석한 내용 기반)
  - 머지 커밋 작성
  - 원격 푸시: `git push origin develop`
  - Must NOT: VPS에 아직 적용 금지 (다음 todo에서)
  References:
  - Todo 1, 2의 결과물
  - 현재 diff: `git diff develop feature/strategy-research --stat`
  - `.omo/evidence/task-2-merge-prep.md`
  Acceptance criteria: `git log --oneline -3 develop`에 머지 커밋 존재
  QA scenarios:
  - happy: `git log --oneline develop -1` → "Merge branch 'feature/strategy-research' into develop"
  - failure: 충돌 미해결 시 중단
  Evidence: `.omo/evidence/task-5-merge-result.md`
  Commit: Y | `feat: RSI+Value 듀얼 모드 전략 머지 (#feature/strategy-research)`

- [ ] 6. VPS `.env` VALUE 모드 설정 및 재시작
  What to do / Must NOT do:
  - VPS SSH 접속 → git pull
  - .env 백업 → .env 수정 (Todo 3 블루프린트 기반)
  - docker-compose down && docker-compose up -d
  - 텔레그램 알림 첫 메시지 확인
  - Must NOT: .env API 키 변경 금지
  References:
  - Todo 3: `.omo/evidence/task-3-env-blueprint.md`
  - Todo 4: `.omo/evidence/task-4-vps-runbook.md`
  - `docker-compose.yml` (서비스명: systemtrading)
  Acceptance criteria:
  - `docker-compose logs --tail=30` 로그에 "전략 모드: Value" 출력 확인
  - 텔레그램으로 "🚀 Starting System Trading in real mode..." 수신
  QA scenarios:
  - happy: 텔레그램 "전략 모드: Value" 메시지 확인
  - failure: 로그에 오류 또는 STRATEGY_MODE 파싱 실패 확인 시 롤백
  Evidence: `.omo/evidence/task-6-deploy-result.md`
  Commit: N

- [ ] 7. VALUE 모드 1일 정상 동작 검증
  What to do / Must NOT do:
  - Phase 2 실행 후 첫 거래일에 VALUE 리밸런싱 로그 확인
  - 다음 항목 확인:
    - PBR 데이터 갱신 성공 ("가치 지표 갱신 완료" 로그)
    - KOSPI200 MA200 마켓 필터 통과 여부
    - VALUE 리밸런싱 대상 선정 및 매수 주문 접수
    - 기존 RSI 보유 종목 유지 (VALUE_KEEP_HOLDINGS)
  - Must NOT: 장중 강제 개입 금지
  References:
  - `strategy/RSIStrategy.py:773-900` (feature): _rebalance_value 함수
  - 텔레그램 알림 패턴
  Acceptance criteria: 텔레그램으로 "Value 리밸런싱 대상 N개" 메시지 확인
  QA scenarios:
  - happy: TVALUE 리밸런싱 로그 및 매수/매도 주문 접수 확인
  - failure: PBR 데이터 없음("PBR 데이터가 있는 종목이 없습니다") → pykrx 이슈 확인
  Evidence: `.omo/evidence/task-7-value-verification.md`
  Commit: N

## Final verification wave
> Runs in parallel after Wave 2 완료 시. ALL must APPROVE.
- [ ] F1. Plan compliance audit: 모든 반영 사항이 plan scope와 일치하는지 확인
- [ ] F2. Code quality review: 머지된 코드에 문제 없는지 (RSIStrategy.py diff 검토)
- [ ] F3. 동작 검증: 텔레그램 알림 및 로그로 VALUE 모드 정상 기동 확인
- [ ] F4. Scope fidelity: 기존 RSI 종목 유지 확인, 신규 VALUE 매수 정상 동작 확인

## Commit strategy
- Todo 1 (분석 문서): `docs: RSI→VALUE 전환 영향 분석 문서화`
- Todo 3 (.env 블루프린트): `docs: VALUE 모드 전환 .env 블루프린트 작성`
- Todo 4 (런북): `docs: VPS VALUE 모드 전환 런북 작성`
- Todo 5 (머지): `feat: RSI+Value 듀얼 모드 전략 머지 (#feature/strategy-research)`
- Todo 6, 7: VPS 작업이므로 커밋 불필요

## Success criteria
- [ ] Phase 1 완료: 전환 준비 아티팩트(분석 문서, 머지 준비, .env 블루프린트, 런북) 완성
- [ ] Phase 2 완료: VPS가 VALUE 모드로 정상 기동
- [ ] Phase 2 완료: 텔레그램 "전략 모드: Value" 알림 확인
- [ ] Phase 2 완료: 첫 거래일 VALUE 리밸런싱 정상 동작 확인
- [ ] Phase 2 완료: 기존 RSI 보유 종목 유지 확인
- [ ] Phase 2 실패 조건: 롤백 절차 (git reset --hard 전 커밋, .env 복원, docker-compose 재시작)
