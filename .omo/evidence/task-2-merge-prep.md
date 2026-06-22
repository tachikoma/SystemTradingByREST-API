# 머지 충돌 사전 해결: feature/strategy-research → develop

> 테스트 일시: 2026-06-22
> 명령어: `git merge --no-commit --no-ff feature/strategy-research`

---

## 충돌 요약

| 파일 | 충돌 유형 | 충돌 영역 | 해결 난이도 |
|------|----------|-----------|------------|
| `.env.example` | content | RSI config → Value section 전환 영역 1곳 | 하 |
| `strategy/RSIStrategy.py` | content | 3개 영역 (class constants, init, main loop, buy signal) | 중 |
| `backtest/momentum_engine.py` | add/add | 완전히 다른 파일 내용 2개 충돌 | 중(선택) |

---

## 충돌 1: `.env.example`

**위치**: RSI 설정 마지막 부분 (MAX_POSITION_RATIO / TIME_STOP_LOSS_DAYS ↔ Value Strategy 섹션)

**develop (HEAD)**:
```env
MAX_POSITION_RATIO=0.20
TIME_STOP_LOSS_DAYS=180
```

**feature/strategy-research**:
```env
# --- Value Strategy 섹션 (신규) ---
STRATEGY_MODE=rsi
VALUE_HOLDINGS=10
VALUE_KEEP_HOLDINGS=False
TIME_STOP_LOSS_DAYS=90
```

**해결 방안**: **feature 브랜치 승리**
- `TIME_STOP_LOSS_DAYS=90` 유지 (VALUE 모드에서는 time-stop-loss 미사용)
- Value Strategy 전체 섹션 채택
- `MAX_POSITION_RATIO=0.20` 제거 (feature 브랜치에서 삭제됨, VALUE 모드에서 미사용)

---

## 충돌 2: `strategy/RSIStrategy.py`

### 충돌 2-1: 클래스 상수 (class constants)

**develop**:
```python
RSI_METHOD = 'wilder'  # walk-forward 검증 완료
```

**feature**:
```python
RSI_METHOD = 'cutler'
VALUE_HOLDINGS = 10   # 신규
VALUE_MARKET_FILTER = True  # 신규
VALUE_MAX_BUDGET = 0  # 신규
VALUE_KEEP_HOLDINGS = False  # 신규
```

**해결**: **feature 브랜치 승리 + RSI_METHOD='wilder' 유지**
```python
RSI_METHOD = 'wilder'  # walk-forward 검증 완료
# Value 전략 파라미터
VALUE_HOLDINGS = 10
VALUE_MARKET_FILTER = True
VALUE_MAX_BUDGET = 0
VALUE_KEEP_HOLDINGS = False
```

### 충돌 2-2: `__init__` 초기화 로직

**develop**: 단순 로거 1줄
```python
logger.info("환경변수 파라미터: RSI_SELL_THRESHOLD=%s ... USE_MA20_FILTER=%s", ...)
```

**feature**: MAX_PBR 파싱 + STRATEGY_MODE 파싱 + VALUE 파라미터 파싱 + 확장된 로거
```python
# 저PBR 필터 임계값
# 전략 모드: 'rsi' 또는 'value'
# Value 전략 파라미터 오버라이드
logger.info("환경변수 파라미터: RSI_SELL_THRESHOLD=%s ... STRATEGY_MODE=%s ... VALUE_KEEP_HOLDINGS=%s", ...)
```

**해결**: **feature 브랜치 승리** (VALUE 모드 초기화에 필수)

### 충돌 2-3: 메인 루프 (보유 종목 처리)

**develop**:
```python
elif code in self.kiwoom.balance.keys():
    emergency_sell = self.get_emergency_liquidation_order(code)
    if emergency_sell:
        quantity, reason = emergency_sell
        self.order_sell(code, quantity=quantity, sell_reason=reason)
        continue
    if self.check_sell_signal(code):
        self.order_sell(code)
```

**feature**:
```python
elif code in self.kiwoom.balance.keys():
    if self.check_sell_signal(code):
        self.order_sell(code)
```

**해결**: **feature 브랜치 승리** (긴급청산 코드 제거 — 사용자 결정과 일치)

### 충돌 2-4: 매수 신호 조건

**develop**:
```python
ma20_ok = (not self.USE_MA20_FILTER) or (ma20 > ma60)
if ma20_ok and close > ma200 and rsi < RSI_BUY_THRESHOLD and price_diff < PRICE_DROP_THRESHOLD:
```

**feature**:
```python
# 저PBR 필터 추가
fund = self.fundamental.get(code)
pbr_ok = True
if fund is not None and self.MAX_PBR < float('inf'):
    if fund.get('PBR', float('inf')) > self.MAX_PBR:
        pbr_ok = False
# MA20>MA60 필터 제거 (USE_MA20_FILTER 자체 삭제), PBR 필터 추가
if ma20 > ma60 and close > ma200 and rsi < RSI_BUY_THRESHOLD and price_diff < PRICE_DROP_THRESHOLD and pbr_ok:
```

**해결**: **feature 브랜치 승리** (PBR 필터 추가로 매수 품질 향상, USE_MA20_FILTER 제거는 walk-forward 검증 완료)

---

## 충돌 3: `backtest/momentum_engine.py` (add/add)

| 버전 | 내용 | LOC |
|------|------|-----|
| develop | `MomentumBacktestEngine` 클래스 (완전한 ML 엔진) | 546 |
| feature | 간단한 모멘텀 백테스트 스크립트 | 323 |

**develop 버전 특징**:
- 완전한 OOP 백테스트 엔진 클래스
- docstring에 "유의미한 edge 확인 불가" 명시
- 거래량 분석, ATR, 트레일링 스탑 등 고급 기능 포함

**feature 버전 특징**:
- 단순 월간 리밸런싱 모멘텀 스크립트
- 12/6/3개월 lookback + skip 기간
- WIN/LOSS 추적

**해결**: **feature 브랜치 승리**
- feature 브랜치의 코드가 더 가볍고 실험 결과와 일관됨
- develop 버전은 실패한 실험 결과를 포함

---

## 최종 해결 전략 (Phase 2 실행 시)

```
모든 충돌에서 feature/strategy-research 승리
단, RSI_METHOD='wilder' 유지 (walk-forward 검증 반영)
```

구체적인 해결 명령어 (Phase 2에서 실행):
```bash
git checkout develop
git merge feature/strategy-research

# 충돌 해결
# .env.example: feature 버전 채택
git checkout --theirs .env.example

# RSIStrategy.py: feature 버전 채택 후 RSI_METHOD='wilder' 수동 복원
git checkout --theirs strategy/RSIStrategy.py
# RSI_METHOD = 'wilder' 로 수동 수정

# momentum_engine.py: feature 버전 채택
git checkout --theirs backtest/momentum_engine.py

git add .
git commit -m "feat: RSI+Value 듀얼 모드 전략 머지 (#feature/strategy-research)"
```
