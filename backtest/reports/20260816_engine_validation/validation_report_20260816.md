# 신규 전략 엔진 검증 보고서 (2026-08-16)

**목적:** `feature/strategy-research` 브랜치의 독립 엔진(vb/vb_daily/trend_follow)을
RSI(2) 검증 gate의 핵심 테스트인 **MC-D(신호 정보량 permutation)** 로 검증.
기존 RSI(2)이 SIGNAL_NO_INFO(무작위와 구분 불가)로 실사용 불가 판정된 뒤의 신호 패밀리 후보들이다.

**데이터:** FDR, 2,962종목, 2016-01-04~2026-08-14, 월별 유니버스 스냅샷(상위 250종목/월, 128개월).
trend_follow은 시장 필터용 069500(KODEX200)을 별도 주입(2,852행, 2015~2026).
수수료/세금/슬리피지: 모의 기준.

## 1. Baseline (전체 유니버스, 신호 그대로)

| 엔진 | 파라미터 | 총수익 | 연환산 | MDD | Sharpe | 매수 | 매도 | 소요 |
|------|----------|--------|--------|------|--------|------|------|------|
| vb | k=0.5, hold=1, SL=-5% | -87.7% | -18.38% | -92.45% | -0.44 | 12,960 | - | 22s |
| vb_daily | k=0.5, hold=5, SL=-8% | -96.8% | -28.43% | -97.76% | -0.68 | 3,301 | - | 13s |
| trend_follow | RS 상위5, 200MA 필터 | -95.3% | -25.56% | -95.67% | -0.67 | 451 | 448 | 4s |
| value | (pykrx 미설치) | - | - | - | - | - | - | 보류 |

> 3개 엔진 모두 10년 전체에서 -18%/yr 이하로 **절대 수익 자체가 실사용 불가 수준**이다.
> RSI(2) baseline(연 -3.34%)보다도 크게 나쁘다.

## 2. MC-D (신호 정보량 permutation)

### 방법론
- **vb / trend_follow**: 신호가 조밀(매일 5종목 풀, 월간 리밸런싱)해 Bernoulli null이 구조적으로
  성립 불가 → **균등무작위 선정(selection) null** 사용: 매일/매월 유니버스에서 같은 수를 무작위 추출.
- **vb_daily**: 신호가 희소(연 330회)해 RSI식 **후보일 C 기반 Bernoulli null** 사용
  (p = baseline 거래수 / C = 0.0101).
- null 30회, baseline은 90% 중심 구간(P5~P95)에 들어가면 NO_INFO.

### 결과

| 엔진 | null 연평균 ± std | P5 | P95 | baseline 연 | P(무작위≥baseline) | **판정** |
|------|-------------------|----|----|-------------|--------------------|----------|
| **vb** | -57.16% ± 2.45 | -59.22 | -52.68 | -18.38% | 1.0000 | **POSITIVE_INFO** |
| **vb_daily** | -20.09% ± 9.92 | -36.02 | -4.14 | -28.43% | 0.2000 | **NO_INFO** |
| **trend_follow** | +31.22% ± 10.62 | +18.15 | +48.11 | -25.56% | 0.0000 | **NEGATIVE_INFO** |

### 해석
- **vb**: 돌파 신호는 무작위 매수보다 연 +39pp 우월(P95 초과, 30/30). 신호에 **양의 정보량이 존재**한다.
  그러나 신호 자체가 -18%/yr, MDD -92%로 절대 성과가 참담 — 정보가 있어도 전략이 성립하지 않는다.
- **vb_daily**: baseline(-28.4%)은 null 90% 구간(-36.0~-4.1) 안 → 신호가 무작위와 **통계적으로 구분 불가**(NO_INFO).
- **trend_follow**: **RS 상위 5종목 선정이 적극적으로 해롭다**. 같은 캐던스로 무작위 5종목을 사면
  연 +31%인데 RS 상위 선정은 -25.6%. 상대강도 랭킹 상위(모멘텀 정점 추종)가 추세반전에 노출.

## 3. 종합 판정

| 엔진 | MC-D | 실사용 |
|------|------|--------|
| vb | POSITIVE_INFO | ❌ 불가 (연 -18%, MDD -92%) |
| vb_daily | NO_INFO | ❌ 불가 |
| trend_follow | NEGATIVE_INFO | ❌ 불가 |
| value | - | 보류 (pykrx 미설치) |

**결론: 신규 엔진 3종 모두 실사용 불가.** RSI(2)의 SIGNAL_NO_INFO와 달리 vb만 신호 정보가
존재하나 절대 성과가 워낙 나빠 파라미터 최적화 여지로만 남는다(단, 12,960회/10년의 고회전 구조
상 거래비용이 성과를 크게 잠식).

## 4. 권고

1. **vb의 신호 정보가 유일한 후속 연구 지점**: k/보유기간/회전율 제한 등 파라미터 재검토.
   단, 현재 구조(매일 5종목 풀 회전)로는 비용 문제가 본질적.
2. trend_follow의 RS 선정은 폐기 권장(역방향 정보). 무작위보다 나쁘다.
3. value 엔진은 pykrx 의존 + 과거 PER/PBR 데이터 확보 문제로 보류 유지.
4. RSI(2)와 동일하게, **신호 패밀리 교체만으로는 부족** — 거래비용·회전율을 통제하는
   구조 설계가 병행되어야 한다.

## 5. 명령어 / 산출물

```bash
# baseline (전체 유니버스)
.venv/bin/python backtest/run_strategy_baseline.py
# MC-D (엔진별)
.venv/bin/python backtest/run_strategy_mc_d.py --engine vb --mc-d-iters 30
.venv/bin/python backtest/run_strategy_mc_d.py --engine vb_daily --mc-d-iters 30
.venv/bin/python backtest/run_strategy_mc_d.py --engine trend_follow --mc-d-iters 30
```

- `backtest/run_strategy_baseline.py` — 엔진별 전체 유니버스 baseline 러너
- `backtest/run_strategy_mc_d.py` — 엔진별 MC-D 러너
- `backtest/validation_strategy_adapter.py` — 엔진 레지스트리/정규화/selection null/MC-D
- `backtest/reports/20260816_engine_validation/mc_d_*.json` — MC-D raw 결과 (본 보고서와 동일 디렉터리)
