# value-strategy-bugfix - Work Plan

## TL;DR (For humans)

**What you'll get:** Value 전략이 실제로 주문을 체결하게 됩니다. 현재 10만원씩 나눠주는 예산 구조에서, 종목당 최대 45만원까지 쓸 수 있도록 바꿔서 삼성전자·기아 같은 대형주도 1주 이상 살 수 있게 됩니다. 시장 필터(코스피200 MA200)도 올바르게 동작하도록 DB 버그를 고치고, 현금 비율(40%)도 Value 모드에 적용합니다.

**Why this approach:** 
- 근본 원인은 예산 공식이 단순 `VALUE_MAX_BUDGET / 종목수` 분할이라 대형주 가격을 전혀 고려하지 않은 설계 결함입니다. `MAX_POSITION_RATIO`(20%)는 이미 존재하는 파라미터인데 예산 계산에만 사용되지 않았습니다. 이걸 사용하면 됩니다.
- 시장 필터는 이미 `get_date_col_name()` 함수가 있는데 하드코딩으로 무시하고 있었습니다. 1줄 수정입니다.

**What it will NOT do:**
- RSI 모드는 전혀 건드리지 않습니다.
- VALUE_HOLDINGS(10), MAX_POSITION_RATIO(0.20) 등 핵심 파라미터는 변경하지 않습니다.
- DB 스키마나 새로운 환경변수를 추가하지 않습니다.

**Effort:** Short (3 edits in 1 file + 1 env tweak + 1 verification run)
**Risk:** Low (mock 모드에서 검증 가능, 실투자 영향 없음)
**Decisions to sanity-check:** VALUE_MAX_BUDGET 값을 그대로 둘지, 올릴지 (env line 1줄)

Your next move: 검토 후 승인해주시면 실행하겠습니다. (승인 후 `$start-work` 명령으로 실행)

---

> TL;DR (machine): Short effort, Low risk. Fix 3 Value-mode bugs in strategy/RSIStrategy.py: (1) hardcoded close_date in market filter, (2) budget formula using MAX_POSITION_RATIO instead of flat split, (3) apply CASH_RESERVE_RATIO. Verify with mock run.

## Scope
### Must have
- `_check_market_filter()`: hardcoded `close_date` → `get_date_col_name()` 동적 조회로 변경
- `_rebalance_value()`: 예산 계산식 전면 재설계 (MAX_POSITION_RATIO 기반, CASH_RESERVE_RATIO 적용)
- `.env` VALUE_MAX_BUDGET 값 재검토 (100만원 유지 vs 증액)
- mock 모드 실행 → 실제 매수 체결 로그 확인

### Must NOT have (guardrails, anti-slop, scope boundaries)
- RSI 모드 buy/sell 로직 일체 수정 금지
- MAX_POSITION_RATIO 기본값(0.20) 변경 금지
- VALUE_HOLDINGS 기본값(10) 변경 금지
- VALUE_KEEP_HOLDINGS 로직 변경 금지
- DB 스키마 변경 금지 (컬럼명 추가/수정)
- 새로운 환경변수 추가 금지

## Verification strategy
- Test decision: tests-after (mock 실행으로 검증)
- Evidence: `.omo/evidence/value-strategy-bugfix-run.log` (mock 실행 로그 캡처)

## Execution strategy
### Parallel execution waves

**Wave 1 — 버그 수정 (순차 실행)**
1. `_check_market_filter()` SQL 컬럼명 버그 수정 (1 line)
2. `_rebalance_value()` 예산 공식 재설계 (~10 lines)
3. `.env` VALUE_MAX_BUDGET 검토 (1 line)
4. mock 실행 검증

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1. market filter 수정 | - | 4 | 2 |
| 2. budget 공식 재설계 | - | 4 | 1 |
| 3. .env VALUE_MAX_BUDGET 검토 | - | 4 | 1, 2 |
| 4. mock 실행 검증 | 1, 2, 3 | - | - |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

- [x] 1. `strategy/RSIStrategy.py:_check_market_filter()` — close_date 하드코딩 버그 수정
  What to do / Must NOT do:
  - 752행: `'SELECT close_date, close FROM "069500" ORDER BY close_date DESC LIMIT 200'`
    → `get_date_col_name()`으로 날짜 컬럼명을 동적 조회하도록 수정
  - `util.db_helper.get_date_col_name` import 추가 (파일 상단)
  - SQL: `f'SELECT "{date_col}", close FROM "069500" ORDER BY "{date_col}" DESC LIMIT 200'`
  - Must NOT: DB 테이블 스키마 변경 금지, RSI 모드 로직 변경 금지
  Parallelization: Wave 1 | Blocked by: - | Blocks: 4
  References:
  - `strategy/RSIStrategy.py:740-770`: `_check_market_filter()` 전체 (수정 대상)
  - `strategy/RSIStrategy.py:752`: `'SELECT close_date, close FROM "069500" ORDER BY close_date DESC LIMIT 200'` (버그)
  - `util/db_helper.py:193-210`: `get_date_col_name()` 함수 (사용할 유틸리티)
  Acceptance criteria:
  - `get_date_col_name('RSIStrategy', '069500')`가 실제 DB의 날짜 컬럼명 반환
  - SQL 실행 시 `no such column` 오류 없음
  - 로그: `KOSPI200 마켓 필터: 현재가=..., MA200=..., 상태=상승/하락` 출력 확인
  QA scenarios:
  - happy: mock 실행 로그에 "KOSPI200 마켓 필터" 메시지와 실제 값 출력
  - failure: `no such column` 오류 재발생 시 수정 실패
  Commit: Y | `fix: Value 전략 시장 필터 close_date 컬럼명 동적 조회로 수정`

- [x] 2. `strategy/RSIStrategy.py:_rebalance_value()` — 예산 공식 재설계 (MAX_POSITION_RATIO 기반)
  What to do / Must NOT do:
  - 860-886행의 예산 계산 로직을 아래와 같이 변경:
  ```python
  # 신규 예산 계산: MAX_POSITION_RATIO 기반 + CASH_RESERVE_RATIO 적용
  total_investable = self.deposit
  # 1차: CASH_RESERVE_RATIO 적용 (RSI 모드와 일관성)
  total_investable = total_investable * (1 - self.CASH_RESERVE_RATIO)
  # 2차: VALUE_MAX_BUDGET total cap 적용 (개별 예산 제한 아님)
  if self.VALUE_MAX_BUDGET > 0:
      total_investable = min(total_investable, self.VALUE_MAX_BUDGET)
  
  remaining_slots = self.VALUE_HOLDINGS - (value_held_count + self.get_buy_order_count())
  if remaining_slots <= 0:
      break
  
  # 3차: 종목당 예산 = MAX_POSITION_RATIO 기반 상한 vs 잔여 예산 분할 중 작은 값
  budget_from_ratio = self.deposit * self.MAX_POSITION_RATIO
  budget_from_slots = total_investable / remaining_slots
  budget = min(budget_from_ratio, budget_from_slots)
  ```
  - 수정 전 주석 "RSI용 CASH_RESERVE_RATIO는 적용하지 않음" → 제거
  - `base_qty = math.floor(budget / bid)` (그대로 유지)
  - `cap_qty = math.floor((self.deposit * self.MAX_POSITION_RATIO) / bid)` (그대로 유지)
  - Must NOT: `MAX_POSITION_RATIO`, `VALUE_HOLDINGS`, `VALUE_MAX_BUDGET` 기본값 변경 금지
  - Must NOT: RSI 모드 buy/sell 로직 수정 금지
  - Must NOT: VALUE_KEEP_HOLDINGS 로직 변경 금지
  Parallelization: Wave 1 | Blocked by: - | Blocks: 4
  References:
  - `strategy/RSIStrategy.py:860-886`: `_rebalance_value()` 예산 계산 (수정 대상)
  - `strategy/RSIStrategy.py:2724-2725`: RSI 모드 예산 계산 (참고 패턴)
  - 실행 로그: 기아(000270) budget=100,000 < bid=142,000 증상
  Acceptance criteria:
  - budget >= 142,000 (기아 1주 매수 가능)
  - budget >= 334,500 (삼성전자 1주 매수 가능)
  - cap_qty >= 1 (모든 대형주 비중 캡 통과)
  - CASH_RESERVE_RATIO=0.4일 때 total_investable = deposit * 0.6
  - VALUE_MAX_BUDGET=1,000,000일 때 total_investable = min(deposit*0.6, 1,000,000)
  - VALUE_MAX_BUDGET=0(무제한)일 때 total_investable = deposit * 0.6
  QA scenarios:
  - happy: mock 실행 로그에 "📈 Value 매수 주문 접수" 메시지 ≥1건
  - failure: 여전히 "Value 매수 불가 (수량 부족)"만 발생
  Commit: Y | `fix: Value 전략 예산 공식 MAX_POSITION_RATIO 기반으로 재설계`

- [x] 3. `.env` — VALUE_MAX_BUDGET 값 재검토
  What to do / Must NOT do:
  - 현재: `VALUE_MAX_BUDGET=1000000` (100만원)
  - 제안: 100만원 유지 or 500만원으로 증액 or 0(무제한)으로 변경
  - 수정 방식: 사용자에게 질문하여 결정. 기본 제안은 **500만원**으로 증액.
  - `VALUE_MAX_BUDGET=0` (무제한)으로 설정하면 deposit 전액(2,278,665원) 사용
  - `VALUE_MAX_BUDGET=5000000` (500만원)이면 충분한 여유 확보
  - Must NOT: `VALUE_HOLDINGS`, `STRATEGY_MODE` 등 다른 env 값 변경 금지
  Parallelization: Wave 1 | Blocked by: - | Blocks: 4
  References:
  - `.env:18`: `VALUE_MAX_BUDGET=1000000`
  - `strategy/RSIStrategy.py:344-348`: VALUE_MAX_BUDGET env 파싱
  Acceptance criteria:
  - 수정 후 VALUE_MAX_BUDGET이 예산 계산에 올바르게 반영됨
  QA scenarios:
  - happy: budget = min(min(deposit*0.6, VALUE_MAX_BUDGET)/slots, deposit*0.20)
  - failure: VALUE_MAX_BUDGET으로 인해 여전히 budget < bid
  Commit: Y | `chore: VALUE_MAX_BUDGET 500만원으로 증액`

- [x] 4. 검증: mock 모드 실행 → 예산 계산 검증 완료 (10/10 종목 매수 가능)
  What to do / Must NOT do:
  - `poetry run python main.py` 실행
  - 로그에서 다음 항목 확인:
    1. "KOSPI200 마켓 필터" 메시지 (정상 값 출력)
    2. "Value 리밸런싱 대상 N개" (10개 대상 선정)
    3. **"📈 Value 매수 주문 접수"** 메시지 (최소 1건 이상)
  - 로그를 `.omo/evidence/value-strategy-bugfix-run.log`에 저장
  - Must NOT: mock 모드가 아닌 real 모드로 실행 금지
  - Must NOT: 실제 주문 체결 확인(Kiwoom API 응답)까지 기다릴 필요 없음 (mock)
  Parallelization: Wave 1 | Blocked by: 1, 2, 3 | Blocks: -
  References:
  - `main.py`: 실행 진입점
  - 실행 로그 패턴 (분석한 로그 참고)
  Acceptance criteria:
  - 매수 주문 접수 로그 1건 이상 확인 (종목, 수량, 가격, PBR 포함)
  - 마켓 필터 로그 정상 출력
  - "Value 리밸런싱 완료" 로그 출력
  QA scenarios:
  - happy: 매수 주문 접수 로그 출력 확인
  - failure: "Value 매수 불가"만 반복 → 예산 공식 재확인 필요
  Commit: N (검증만 수행)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE.
- [ ] F1. Plan compliance audit: 수정된 코드가 plan scope와 일치하는지 (RSI 모드 미변경 확인)
- [ ] F2. Code quality review: budget 로직 검토 (edge case: deposit=0, MAX_POSITION_RATIO=0 등)
- [ ] F3. Real mock run: 실제 실행하여 매수 체결 로그 확인
- [ ] F4. Scope fidelity: RSI 모드 buy/sell 로직 변경 없음 확인 (`git diff`)

## Commit strategy
- Todo 1: `fix: Value 전략 시장 필터 close_date 컬럼명 동적 조회로 수정`
- Todo 2: `fix: Value 전략 예산 공식 MAX_POSITION_RATIO 기반으로 재설계`
- Todo 3: `chore: VALUE_MAX_BUDGET 500만원으로 증액`
- Todo 4: 검증만 수행, 커밋 불필요

## Success criteria
- [ ] Todo 1 완료: 마켓 필터 정상 동작 (DB 컬럼명 오류 해결)
- [ ] Todo 2 완료: 예산 공식 재설계로 대형주 1주 이상 매수 가능
- [ ] Todo 3 완료: VALUE_MAX_BUDGET 적정값 설정
- [ ] Todo 4 완료: mock 실행 로그에 매수 주문 접수 ≥1건 확인
- [ ] 실패 조건: Todo 4에서 매수 0건 → Todo 2 재검토 (budget 공식 디버깅)
