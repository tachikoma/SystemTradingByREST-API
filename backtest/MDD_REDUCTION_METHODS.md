# MDD 감소 방법 연구

## 현재 상황
- **전략:** RSI(2) 역추세 전략
- **현재 MDD:** -58.25%
- **연수익률:** 20.05%
- **승률:** 100%
- **문제:** 승률은 완벽하지만 보유 기간 중 평가손실이 매우 큼

## 🎯 MDD 감소 방법 8가지

### 1. 포지션 크기 조정 (Position Sizing) ⭐⭐⭐⭐⭐
**개념:** 각 종목에 투자하는 금액 비율 조정

**현재 방식:**
```python
# 균등 분산 (Equal Weight)
position_size = available_cash / max_holdings
# 예: 1000만원 / 10종목 = 종목당 100만원
```

**개선 방안:**

#### A. Fixed Fractional (고정 비율)
```python
# 전체 자본의 5%만 한 종목에 투자
position_size = total_capital * 0.05
# 예: 1000만원 * 5% = 종목당 50만원
# 효과: 개별 종목 손실 영향 감소
```

#### B. Kelly Criterion (켈리 공식)
```python
# 최적 베팅 비율 = (승률 * 평균수익 - 패률 * 평균손실) / 평균수익
kelly = (win_rate * avg_win - lose_rate * avg_loss) / avg_win
position_size = total_capital * kelly * 0.5  # Half Kelly (안전)
```

#### C. ATR 기반 포지션 크기
```python
# 변동성이 클수록 포지션 작게
position_size = risk_per_trade / (atr * multiplier)
```

**예상 효과:** MDD -58% → -35~45%

---

### 2. 현금 비중 유지 (Cash Reserve) ⭐⭐⭐⭐
**개념:** 항상 일정 비율의 현금 보유

**구현:**
```python
# 80%만 투자, 20% 현금 보유
investable_capital = total_capital * 0.8
max_position_size = investable_capital / max_holdings
```

**효과:**
- 현금 20% 보유 → MDD는 자동으로 20% 감소
- 예: MDD -58% → -46% (58% * 0.8)
- 단, 수익률도 20% 감소

**최적 비율 찾기:**
| 현금 비중 | 투자 비중 | MDD 예상 | 수익률 예상 |
|---------|---------|---------|-----------|
| 0% | 100% | -58% | 20.05% |
| 20% | 80% | -46% | 16.04% |
| 30% | 70% | -41% | 14.04% |
| 40% | 60% | -35% | 12.03% |

**예상 효과:** MDD -58% → -35~46%

---

### 3. 최대 보유 종목 수 조정 ⭐⭐⭐⭐
**개념:** 동시 보유 종목 수를 제한

**현재:** max_holdings = 10

**옵션:**

#### A. 종목 수 감소 (집중 투자)
```python
max_holdings = 5
# 효과: 선별된 최고의 신호에만 집중
# 리스크: 분산 효과 감소
```

#### B. 종목 수 증가 (분산 투자)
```python
max_holdings = 20
# 효과: 개별 종목 비중 감소 → MDD 감소
# 리스크: 수익률도 감소 가능
```

**테스트 필요:**
- 5종목 vs 10종목 vs 20종목 비교

**예상 효과:** MDD -58% → -40~50%

---

### 4. 진입 조건 강화 ⭐⭐⭐⭐
**개념:** 더 좋은 신호에만 진입

**현재 조건:**
```python
# RSI < 5
# 가격 > MA20 > MA60
# MA20 > MA200 (상승 추세)
# 어제 종가 하락 > -2%
```

**강화 옵션:**

#### A. RSI 기준 강화
```python
rsi_buy_threshold = 3  # 5 → 3
# 효과: 더 깊은 과매도 상태만 진입
```

#### B. 가격 하락 기준 강화
```python
price_drop_threshold = -5.0  # -2% → -5%
# 효과: 더 큰 하락만 진입 (반등 여력 증가)
```

#### C. 거래량 필터 추가
```python
# 평균 거래량 대비 150% 이상
volume_condition = current_volume > avg_volume * 1.5
```

#### D. 변동성 필터 추가
```python
# ATR이 과도하게 높은 종목 제외
atr_filter = atr < atr_sma * 2.0
```

**예상 효과:** MDD -58% → -40~50%

---

### 5. 시장 상황 필터 (Market Regime Filter) ⭐⭐⭐
**개념:** 약세장에서는 포지션 축소

**구현:**

#### A. KOSPI 기준
```python
# KOSPI가 200일 이동평균 아래면 50%만 투자
if kospi < kospi_ma200:
    position_multiplier = 0.5
else:
    position_multiplier = 1.0
```

#### B. VIX 기준 (변동성 지수)
```python
# 변동성 높으면 포지션 축소
if vix > 25:  # 높은 변동성
    position_multiplier = 0.5
elif vix > 20:  # 중간 변동성
    position_multiplier = 0.7
else:
    position_multiplier = 1.0
```

**예상 효과:** MDD -58% → -35~45%

---

### 6. 분할 매수 (Pyramiding) ⭐⭐
**개념:** 한 번에 전량 매수하지 않고 분할

**구현:**
```python
# 1차 매수: 50%
# 추가 하락 시 2차 매수: 30%
# 더 하락 시 3차 매수: 20%

if rsi < 5:
    buy_quantity_1 = position_size * 0.5
elif rsi < 3:  # 더 하락
    buy_quantity_2 = position_size * 0.3
```

**효과:**
- 평균 매수가 낮춤
- 리스크 분산

**예상 효과:** MDD -58% → -45~52%

---

### 7. 동적 최대 보유 종목 수 ⭐⭐
**개념:** 시장 상황에 따라 종목 수 조정

**구현:**
```python
# 약세장: 종목 수 감소 (5종목)
# 보통: 10종목
# 강세장: 15종목

if market_trend == 'bear':
    max_holdings = 5
elif market_trend == 'bull':
    max_holdings = 15
else:
    max_holdings = 10
```

**예상 효과:** MDD -58% → -45~55%

---

### 8. 상관관계 기반 분산 ⭐⭐⭐
**개념:** 상관관계 높은 종목들을 동시 보유 제한

**구현:**
```python
# 같은 섹터 종목은 최대 2개만
# 예: 삼성전자, SK하이닉스 동시 보유 제한

def check_sector_limit(holdings, new_code, sector_data):
    sector = sector_data[new_code]
    sector_count = sum(1 for code in holdings if sector_data[code] == sector)
    return sector_count < 2
```

**효과:**
- 섹터 동반 하락 시 손실 제한
- 진정한 분산 효과

**예상 효과:** MDD -58% → -40~48%

---

## 📊 우선순위 및 구현 난이도

| 순위 | 방법 | 예상 효과 | 구현 난이도 | 추천도 |
|-----|-----|---------|----------|--------|
| 1 | 현금 비중 유지 | ⭐⭐⭐⭐⭐ | 매우 쉬움 | ⭐⭐⭐⭐⭐ |
| 2 | 포지션 크기 조정 | ⭐⭐⭐⭐⭐ | 쉬움 | ⭐⭐⭐⭐⭐ |
| 3 | 진입 조건 강화 | ⭐⭐⭐⭐ | 쉬움 | ⭐⭐⭐⭐ |
| 4 | 최대 보유 종목 수 | ⭐⭐⭐⭐ | 쉬움 | ⭐⭐⭐⭐ |
| 5 | 시장 상황 필터 | ⭐⭐⭐ | 중간 | ⭐⭐⭐ |
| 6 | 상관관계 분산 | ⭐⭐⭐ | 어려움 | ⭐⭐⭐ |
| 7 | 분할 매수 | ⭐⭐ | 중간 | ⭐⭐ |
| 8 | 동적 종목 수 | ⭐⭐ | 중간 | ⭐⭐ |

---

## 🚀 추천 실행 순서

### Phase 1: 간단하고 효과 큰 것부터
1. **현금 비중 유지 테스트** (20%, 30%, 40%)
2. **포지션 크기 조정** (Fixed Fractional 5%, 10%)
3. **최대 보유 종목 수** (5개, 15개, 20개)

### Phase 2: 전략 개선
4. **진입 조건 강화** (RSI < 3, 하락 -5%)
5. **시장 상황 필터** (KOSPI MA200 기준)

### Phase 3: 고급 기법
6. **상관관계 기반 분산**
7. **복합 전략** (위 방법들 조합)

---

## 💡 예상 최적 조합

```python
BacktestEngine(
    initial_capital=10_000_000,
    max_holdings=15,  # 10 → 15 (분산 강화)
    cash_reserve=0.2,  # 20% 현금 보유 (NEW)
    position_size_method='fixed_fraction',  # 5% 고정 (NEW)
    position_size_pct=0.05,  # (NEW)
    rsi_buy_threshold=3,  # 5 → 3 (진입 강화)
    price_drop_threshold=-5.0,  # -2 → -5 (진입 강화)
    market_filter=True,  # KOSPI MA200 필터 (NEW)
)
```

**예상 결과:**
- MDD: -58% → -35~40%
- 연수익률: 20.05% → 14~16%
- Sharpe: 0.87 → 1.0~1.2
- **위험 조정 수익률 대폭 개선**

---

## 📝 다음 단계

1. `test_mdd_reduction.py` 생성 - 각 방법 개별 테스트
2. `optimize_mdd_reduction.py` 생성 - 조합 최적화
3. 결과 분석 후 최적 설정 결정

---

*작성일: 2026-01-01*
*목표: MDD -58% → -35~40% (수익률 소폭 감소 허용)*
