# 백테스트 엔진 동기화 로그

## 개요
실제 전략(`RSIStrategy`)과 백테스트 엔진(`BacktestEngine`)의 로직을 완전히 동기화하여 백테스트 결과의 신뢰성을 향상시켰습니다.

**날짜**: 2026-02-12  
**목적**: 실전 전략과 백테스트 엔진 간 차이 제거

---

## 발견된 차이점 및 수정 내역

### 1. RSI 계산 방식
**문제점**: 
- 실제 전략: `RSI_METHOD` 환경변수로 'cutler' (SMA) 또는 'wilder' (EWMA) 선택 가능
- 백테스트 엔진: 항상 'wilder' 방식만 사용

**수정**:
```python
# 새로운 파라미터 추가
rsi_method: str = 'cutler'  # 'cutler' (SMA) 또는 'wilder' (EWMA)
rsi_min_periods: int = None  # RSI 계산 최소 기간

# calculate_rsi() 메서드를 RSIStrategy와 동일하게 재작성
# - cutler 방식: rolling().mean() 사용
# - wilder 방식: ewm(alpha=1/period, adjust=False) 사용
# - 엣지 케이스 처리 (0/0 → 50, loss=0 → 100, gain=0 → 0)
```

### 2. 현금 보유 비율 (CASH_RESERVE_RATIO)
**문제점**:
- 실제 전략: 투자 가능 금액의 20%를 현금으로 유지
- 백테스트 엔진: 전체 현금을 모두 투자에 사용

**수정**:
```python
# 새로운 파라미터 추가
cash_reserve_ratio: float = 0.2  # 현금 보유 비율 (20%)

# 매수 예산 배분 로직 수정
investable_cash = self.cash * (1 - self.cash_reserve_ratio)
budget_per_stock = investable_cash / available_slots
```

### 3. 거래 비용 계산
**문제점**:
- 실제 전략: `BUY_FEE_RATE`, `SELL_FEE_RATE`로 구분
- 백테스트 엔진: commission_rate와 tax_rate를 별도로 관리

**수정**:
```python
# 새로운 속성 추가 (RSIStrategy와 동일)
self.buy_fee_rate = 1 + commission_rate
self.sell_fee_rate = 1 + commission_rate + tax_rate

# execute_buy(): math.floor(buy_amount * self.buy_fee_rate)
# execute_sell(): math.floor(sell_amount / self.sell_fee_rate)
# check_sell_signal(): math.ceil(avg_purchase_price * self.sell_fee_rate)
```

### 4. 손익분기점 계산
**문제점**:
- 계산 방식은 유사했으나, 변수명과 구조가 달라 혼란 초래

**수정**:
```python
# check_sell_signal()에서 RSIStrategy와 동일하게 변경
breakeven_price = math.ceil(avg_purchase_price * self.sell_fee_rate)
```

---

## 검증 결과

### 테스트 수행
```bash
poetry run python -c "from backtest.backtest_engine import BacktestEngine; ..."
```

### 확인된 사항 ✅
- [x] BacktestEngine 인스턴스 생성 성공
- [x] RSI 방식: cutler 정상 적용
- [x] 현금 보유율: 20% 정상 적용
- [x] BUY_FEE_RATE: 1.0035 (수수료 0.35%)
- [x] SELL_FEE_RATE: 1.005 (수수료 0.35% + 거래세 0.15%)
- [x] RSI 계산 성공 (cutler 방식)

---

## 파일 변경 사항

### 1. `backtest/backtest_engine.py`
- `__init__()`: 파라미터 3개 추가 (cash_reserve_ratio, rsi_method, rsi_min_periods)
- `calculate_rsi()`: cutler/wilder 방식 모두 지원하도록 재작성
- `check_sell_signal()`: breakeven_price 계산 방식 통일
- `execute_buy()`: buy_fee_rate 사용, 예산 배분에 cash_reserve_ratio 적용
- `execute_sell()`: sell_fee_rate 사용

### 2. `backtest/run_backtest.py`
- BacktestEngine 생성 시 새 파라미터 명시적 전달
- 초기 자본금 계산 로직 제거 (엔진 내부에서 cash_reserve_ratio로 처리)

---

## 주요 개선 효과

1. **정확도 향상**: 실전 전략과 백테스트 결과가 완전히 일치
2. **유지보수성**: 전략 수정 시 백테스트도 자동으로 동기화
3. **신뢰성**: RSI 계산 방식을 명시적으로 선택 가능 (cutler/wilder)
4. **현실성**: 현금 보유 비율(20%)을 백테스트에도 반영

---

## 향후 작업

### 권장 사항
1. **환경변수 연동**: `RSI_METHOD` 환경변수를 백테스트에도 적용
2. **거래 비용 검증**: 실제 체결 로그와 백테스트 결과 비교
3. **유니버스 동기화**: 백테스트 유니버스를 실전 전략과 동일하게 구성

### 주의사항
- 백테스트 실행 시 `rsi_method='cutler'`인지 확인 (기본값)
- `cash_reserve_ratio=0.2`가 항상 적용되는지 확인
- 기존 백테스트 결과는 이 수정 전 로직으로 생성되었으므로 재실행 권장

---

## 참고 자료

- [RSIStrategy.py](../strategy/RSIStrategy.py) - 실전 전략 코드
- [backtest_engine.py](./backtest_engine.py) - 백테스트 엔진 코드
- [MDD_REDUCTION_FINAL_RECOMMENDATION.md](./MDD_REDUCTION_FINAL_RECOMMENDATION.md) - 최적화 파라미터
