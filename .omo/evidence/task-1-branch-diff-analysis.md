# 브랜치 차이 분석: develop vs feature/strategy-research

> 작성일: 2026-06-22
> 분석 범위: RSI→VALUE 전환에 영향 있는 파일

---

## 1. 주요 변경 파일 (23개)

```
 .env.example                          |  71 +-  (충돌)
 backtest/momentum_engine.py           | 869 +++ ---  (add/add 충돌)
 backtest/value_engine.py              | 404 +++++     (신규 - 가치전략 백테스트)
 backtest/vb_daily_engine.py           | 253 +++++     (신규 - VB 데일리 백테스트)
 backtest/vb_engine.py                 | 283 +++++     (신규 - VB 백테스트)
 backtest/trend_follow_engine.py       | 361 +++++     (신규 - 추세추종 백테스트)
 strategy/RSIStrategy.py               | 548 +++++----  (충돌 - 듀얼 모드 전환)
 main.py                               |   0           (변경 없음 - 중요!)
 api/Kiwoom.py                         |  19 +-
 util/make_up_universe.py              |  45 +-
```

**핵심 발견**: `main.py` 변경 없음 → 전략 모드 전환은 전적으로 `.env`의 `STRATEGY_MODE` 값으로 제어.

---

## 2. RSIStrategy.py: 매도 조건 비교 (Phase 1 영향)

| 조건 | develop (현재 VPS) | feature/strategy-research |
|------|-------------------|--------------------------|
| **RSI_SELL_THRESHOLD** | **70** | **80** |
| **PROFIT_TARGET_PERCENT** | **0.0** (deprecated) | **10.0** (재활성화) |
| **TIME_STOP_LOSS_DAYS** | **180** | **90** |
| **USE_MA20_FILTER** | **False** (존재) | **제거됨** |
| **RSI_METHOD** | **wilder** | **cutler** |
| 긴급청산 (EMERGENCY) | 존재 (기본 비활성) | **코드에서 제거됨** |
| RSI_PAUSE_NEW_BUYS | 존재 | **코드에서 제거됨** |
| 저PBR 필터 (MAX_PBR) | 없음 | **있음 (inf=비활성)** |

### 매도 조건 상세

**develop (현재)**:
```python
# 2154-2167
breakeven_price = ceil(purchase_price * SELL_FEE_RATE)
condition = (
    rsi > 70                      # RSI 과열
    and close > breakeven_price   # 손익분기점 돌파 (=매수가 이상)
)
```

**feature/strategy-research**:
```python
# 2395-2425
breakeven_price = ceil(purchase_price * SELL_FEE_RATE)
target_price = ceil(breakeven_price * (1 + 10/100))  # 10% 수익 목표
condition = (
    rsi > 80                      # RSI 과열 (더 엄격)
    and close > breakeven_price   # 손익분기점
    and close >= target_price      # 목표가 도달 (10% 수익)
)
```

### ⚠️ -29% 종목 매도 가능성 비교

| 시나리오 | develop (현행) | feature (RSI 모드) |
|----------|---------------|-------------------|
| breakeven(-0%) + RSI>70 | ✅ 매도 | ❌ RSI>80 미달성 |
| breakeven + RSI>80 | — | ❌ 목표가(10%) 미달성 |
| breakeven +10% + RSI>80 | — | ✅ 매도 |

→ **feature 브랜치 머지 후 RSI 모드 유지 시 -29% 종목 매도가 더 어려워짐**
→ **Phase 1에서는 머지하지 말고 develop 유지가 정답**

---

## 3. VALUE 모드 동작 방식 (Phase 2 핵심)

### `_rebalance_value()` 함수 (feature/strategy-research, line 773-900)

```
매일 1회 장중 (09:00~15:20):
  1. KOSPI200(069500) MA200 마켓 필터 체크
     - 약세장(KOSPI200 < MA200): 전량 청산 후 종료
  2. PBR 데이터 갱신 (pykrx)
  3. Universe 전체 종목 PBR 오름차순 정렬
  4. 상위 VALUE_HOLDINGS(기본 10)개 선정
  5. VALUE_KEEP_HOLDINGS=True:
     - 기존 보유 종목(_value_initial_holdings 스냅샷)은 유지
     - 신규 VALUE 슬롯만 채움
  6. 매도: 밀려난 종목 중 KEEP 대상 아닌 것 매도
  7. 매수: 미보유 대상 종목 예산 계산 → 지정가 매수 주문
```

### 중요: VALUE 모드에서 RSI 보유 종목의 운명

- `VALUE_KEEP_HOLDINGS=1` → **영구 보유** (절대 매도되지 않음)
- `VALUE_KEEP_HOLDINGS=0` → **첫 리밸런싱 시 전량 매도** (청산)
- 체크: VALUE 모드는 `check_sell_signal()`을 호출하지 않음 → RSI 매도 조건, 시간손절 모두 작동 안 함

### RSI Buy 조건: PBR 필터 추가 (feature 브랜치)

```python
# feature/strategy-research 추가됨
fund = self.fundamental.get(code)
pbr_ok = True
if fund is not None and self.MAX_PBR < float('inf'):
    if fund.get('PBR', float('inf')) > self.MAX_PBR:
        pbr_ok = False  # PBR 높으면 매수 차단
```

---

## 4. .env.example 주요 차이

| 항목 | develop | feature/strategy-research | 결정 |
|------|---------|--------------------------|------|
| MAX_POSITION_RATIO | 0.20 | **제거됨** | 제거 유지 |
| TIME_STOP_LOSS_DAYS | **180** | **90** | **90** (VALUE 모드 영향 없음) |
| STRATEGY_MODE | 없음 | **rsi / value** | Phase 2: **value** |
| VALUE_HOLDINGS | 없음 | **10** | 10 |
| VALUE_MARKET_FILTER | 없음 | **1** | 1 |
| VALUE_KEEP_HOLDINGS | 없음 | **False** | Phase 2: **1** (True) |
| VALUE_MAX_BUDGET | 없음 | **0** (무제한) | 0 |
| RSI_PAUSE_NEW_BUYS | 있음 | **제거됨** | 제거 유지 |
| RSI_EMERGENCY_* | 있음 | **제거됨** | 제거 유지 |
| USE_MA20_FILTER | false | **제거됨** | 제거 유지 |

---

## 5. 백테스트 엔진 신규 파일 (VALUE 모드 관련)

| 파일 | 용도 | LOC |
|------|------|-----|
| `backtest/value_engine.py` | PBR/PER 가치투자 백테스트 | 404 |
| `backtest/vb_engine.py` | VB 전략 백테스트 | 283 |
| `backtest/vb_daily_engine.py` | VB 데일리 백테스트 | 253 |
| `backtest/trend_follow_engine.py` | 추세추종 백테스트 | 361 |

→ Phase 2 머지 시 신규 파일은 충돌 없이 자동 추가됨 (add/add 없음)

---

## 6. 결론

1. **Phase 1 (지금): develop 유지** — feature 브랜치 머지 시 RSI 매도 조건이 강화되어 -29% 종목 매도가 더 어려워짐
2. **Phase 2 (매도 후): 머지 + VALUE 전환** — feature 브랜치의 VALUE 모드 기능을 사용하며, RSI 파라미터 차이는 VALUE 모드에서 무의미
3. **3개 파일 충돌 예상**: `.env.example`, `strategy/RSIStrategy.py`, `backtest/momentum_engine.py` — 모두 해결 가능
