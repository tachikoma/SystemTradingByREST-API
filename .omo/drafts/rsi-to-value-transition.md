---
slug: rsi-to-value-transition
status: awaiting-approval
intent: clear
pending-action: write .omo/plans/rsi-to-value-transition.md
approach: >
  VPS develop 브랜치에서 실행 중인 RSI 전략을 안전하게 가치 전략(Value mode)으로 전환.
  현재 -29% 보유 종목은 그대로 유지하고(자연 매도 대기),
  feature/strategy-research 브랜치를 develop으로 머지한 뒤,
  VPS .env에 STRATEGY_MODE=value + VALUE_KEEP_HOLDINGS=1 을 설정하여
  RSI 보유 종목을 살린 채 가치 전략이 신규 슬롯을 채워 나가도록 전환.
---

# Draft: rsi-to-value-transition

## Components (topology ledger)

| id | outcome | status | evidence path |
|----|---------|--------|---------------|
| A | feature/strategy-research → develop 머지 | active | git diff/log |
| B | VPS .env 값 수정 (STRATEGY_MODE, VALUE_KEEP_HOLDINGS 등) | active | .env.example:196-225 |
| C | VPS 봇 재시작 (systemd / docker) | active | deploy/ 확인 필요 |
| D | 재시작 후 동작 검증 (텔레그램 알림, 로그) | active | logs/ |

## Open assumptions (announced defaults)

| assumption | adopted default | rationale | reversible? |
|------------|----------------|-----------|-------------|
| RSI 보유 종목 유지 | VALUE_KEEP_HOLDINGS=1 | -29% 강제 청산 시 확정 손실, 자연 매도 대기가 최선 | Yes - .env 수정으로 언제든 변경 |
| 가치 전략 목표 종목 수 | VALUE_HOLDINGS=10 (기본값 유지) | 기존 포트폴리오 크기와 동일 | Yes |
| 마켓 필터 | VALUE_MARKET_FILTER=1 (기본값 유지) | KOSPI200 < MA200 약세장 시 전량청산 방어 | Yes |
| 시간 손절 | TIME_STOP_LOSS_DAYS=90 (feature 브랜치 기본값) | 백테스트 최적 90일 (develop은 180일) | Yes |
| feature/strategy-research 머지 대상 | develop 브랜치 | VPS가 develop 실행 중 | Yes |

## Findings (cited - path:lines)

### 현재 매도 조건 (develop 브랜치)
- `RSI(2) > 70` **AND** `현재가 > breakeven가(매수가×수수료율)` 동시 충족 시 매도
- `strategy/RSIStrategy.py:2160-2161` (develop)
- **-29% 종목은 현재가가 매수가 이하이므로 breakeven 조건 불충족 → 자동 매도 불가**
- RSI가 70 이상으로 급등(강한 반등)할 때만 매도 신호 발생

### 긴급청산 (develop 브랜치)
- `RSI_EMERGENCY_LIQUIDATION_ENABLED=0` (기본 비활성)
- Hard stop: -30%, Partial stop: -20%
- 현재 -29%이면 hard stop -30%에 0.56%p 차이 → **활성화 시 즉시 청산 위험**
- `strategy/RSIStrategy.py:2302-2348` (develop)

### 시간 손절 (develop 브랜치)
- `TIME_STOP_LOSS_DAYS=180` (develop 기본값)
- 매수일로부터 180일 초과 시 강제 매도
- `strategy/RSIStrategy.py:2122-2129`

### feature/strategy-research 브랜치 가치 전략
- `STRATEGY_MODE=value` 환경변수로 전환
- `_rebalance_value()`: 매일 1회 장중 PBR 하위 N개 종목으로 리밸런싱
- `VALUE_KEEP_HOLDINGS=1`: 기존 RSI 보유 종목 매도 없이 유지 (RSI→Value 전환 전용)
  - `_value_initial_holdings` 스냅샷으로 기존 종목 제외 후 신규 슬롯만 채움
- `strategy/RSIStrategy.py:773-900` (feature/strategy-research)
- VPS에서 .env만 수정 후 재시작으로 즉시 전환 가능

### 브랜치 차이 규모
- `git diff develop feature/strategy-research --stat`: 23개 파일, +2159/-2473 lines
- 주요 신규: `backtest/value_engine.py`, `backtest/vb_engine.py`, `backtest/trend_follow_engine.py`
- 전략 파일: `strategy/RSIStrategy.py` +548/-548 (듀얼 모드 추가)

### VPS 배포 방식 확인 필요
- `deploy/` 디렉토리 존재, `docker-compose.yml`, `watchdog.sh` 존재
- 재시작 방법: docker-compose restart 또는 systemd service restart

## Decisions (with rationale)

1. **-29% 종목 그대로 유지**: 지금 강제 청산하면 손실 확정. RSI 반등 시 breakeven 이상에서 자동 매도를 기다리는 것이 합리적.
2. **VALUE_KEEP_HOLDINGS=1**: RSI 보유 종목을 가치 전략이 강제 청산하지 않도록 보호.
3. **feature/strategy-research → develop 머지**: VPS가 develop을 추적 중이므로 이 브랜치에 반영.
4. **긴급청산 미활성화 유지**: 현재 -29%에서 hard stop -30%를 켜면 -30% 도달 즉시 청산 위험. 유지 권장.

## Scope IN

- feature/strategy-research → develop 브랜치 머지 (로컬 + VPS git pull)
- VPS `.env` 수정: `STRATEGY_MODE=value`, `VALUE_KEEP_HOLDINGS=1`, `TIME_STOP_LOSS_DAYS=90`
- VPS 봇 재시작
- 재시작 후 동작 확인 (로그/텔레그램)
- 전환 후 모니터링 체크리스트 작성

## Scope OUT (Must NOT have)

- 현재 보유 RSI 종목 수동/강제 청산 금지
- 긴급청산(`RSI_EMERGENCY_LIQUIDATION_ENABLED`) 활성화 금지
- RSI_BUY_THRESHOLD, PRICE_DROP_THRESHOLD, CASH_RESERVE_RATIO 등 copilot-instructions 불변 파라미터 수정 금지
- 새로운 전략 코드 작성/수정 금지 (feature 브랜치 기존 코드 그대로 사용)
- `.env` API 키 수정 금지

## Open questions

(없음 - 모든 포크 해소됨)

## Approval gate

status: awaiting-approval
pending-action: 사용자 승인 후 .omo/plans/rsi-to-value-transition.md 상세 실행 플랜 작성
approach: feature/strategy-research→develop 머지 → VPS .env 수정 → 재시작 → 검증
