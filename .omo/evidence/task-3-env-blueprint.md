# VALUE 모드 전환 .env 블루프린트

> Phase 2 실행 시 VPS `.env` 파일에 적용할 설정

---

## 변경할 값

VPS `.env`에서 아래 값들을 **추가 또는 수정**합니다.

```env
# ===================================================================
# [필수] Phase 2: RSI → VALUE 전환
# ===================================================================

# 전략 모드 전환: 'rsi' → 'value'
STRATEGY_MODE=value

# 기존 RSI 보유 종목 유지 (Phase 2에서 -29% 등 기존 포지션 보호)
# 1=True: 첫 실행 시 기존 종목 스냅샷 저장, VALUE 리밸런싱에서 제외
# 0=False: 모든 포지션 청산 후 VALUE 리밸런싱 시작
VALUE_KEEP_HOLDINGS=1

# ===================================================================
# [권장] VALUE 전략 세부 설정 (기본값 사용 가능)
# ===================================================================

# 목표 보유 종목 수 (기존 RSI MAX_HOLDINGS=10과 동일)
# VALUE_HOLDINGS=10

# KOSPI200(069500) > MA200 마켓 필터
# 1=True: 약세장(KOSPI200 < MA200) 시 전량 청산
# 0=False: 필터 미사용
# VALUE_MARKET_FILTER=1

# 최대 투자 금액 (원), 0=예수금 전액 사용
# VALUE_MAX_BUDGET=0

# 시간 손절 기준 (VALUE 모드에서는 미사용, RSI 모드 전환 시 적용)
# feature 브랜치 기본값: 90일
# TIME_STOP_LOSS_DAYS=90

# ===================================================================
# [유지] 변경 불필요한 값 (현행 유지)
# ===================================================================

# API 키, KIWOOM_MODE, 텔레그램 설정 등은 그대로 유지
# KIWOOM_MODE=real      # (변경 불필요)
# KIWOOM_REAL_APPKEY=... # (변경 불필요)
# KIWOOM_REAL_SECRETKEY=... # (변경 불필요)

# RSI 전용 파라미터는 VALUE 모드에서 사용되지 않지만, 향후 RSI 모드 전환 대비 유지
# RSI_SELL_THRESHOLD=70
# RSI_BUY_THRESHOLD=3
# CASH_RESERVE_RATIO=0.2

# 거래 비용 설정 (변경 불필요)
# TRADING_FEE_PERCENT_REAL=0.015
# TRADING_TAX_PERCENT_REAL=0.20
```

---

## 변경 전후 비교표

| 환경변수 | 변경 전 (VPS develop, RSI 모드) | 변경 후 (VPS develop, VALUE 모드) | 비고 |
|---------|-------------------------------|----------------------------------|------|
| `STRATEGY_MODE` | (없음, 기본값=rsi) | **value** | 필수 변경 |
| `VALUE_KEEP_HOLDINGS` | (없음, 기본값=False) | **1** | 필수 변경 |
| `TIME_STOP_LOSS_DAYS` | 180 | **90** (또는 180 유지) | VALUE엔 영향 없음 |
| `VALUE_HOLDINGS` | (없음, 기본값=10) | 10 | 기본값 유지 |
| `VALUE_MARKET_FILTER` | (없음, 기본값=True) | 1 | 기본값 유지 |
| `VALUE_MAX_BUDGET` | (없음, 기본값=0) | 0 | 기본값 유지 |

---

## 추가 고려사항

1. **TIME_STOP_LOSS_DAYS=90 vs 180**: VALUE 모드에서는 `_rebalance_value()`만 실행되어 time-stop-loss 체크가 이뤄지지 않습니다. 따라서 TIME_STOP_LOSS_DAYS 값은 VALUE 모드에 영향을 주지 않습니다. feature 브랜치 기본값(90)을 그대로 사용해도 무방합니다.

2. **VALUE_KEEP_HOLDINGS=1의 동작**: 첫 VALUE 리밸런싱 시점에 `_value_initial_holdings` 스냅샷이 저장됩니다. 이 스냅샷에 포함된 종목은 향후 VALUE 리밸런싱에서 청산되지 않고 영구 보유됩니다. 수동 매도가 필요하면 직접 처리해야 합니다.

3. **VALUE 모드 첫 리밸런싱 타이밍**: 재시작 후 첫 거래일 장중(09:00~15:20)에 PBR 데이터를 pykrx로 조회하고 리밸런싱을 실행합니다. PBR 데이터가 없으면 "PBR 데이터가 있는 종목이 없습니다" 로그가 출력되고 리밸런싱이 생략됩니다.

4. **롤백 절차**: 
   ```bash
   # .env 복원
   cp .env.backup.YYYYMMDD_HHMMSS .env
   # watchdog 재시작
   sh scripts/stop_watchdog.sh && sleep 2 && sh scripts/start_watchdog.sh
   ```
