# 신규 전략 엔진 검증 보고서 (2026-08-16, rev.2)

**목적:** `feature/strategy-research` 브랜치의 독립 엔진(vb/vb_daily/trend_follow)을
검증 gate의 핵심 테스트(MC-D)로 검증. value 엔진은 pykrx 미설치로 보류.

**데이터:** FDR, 2,962종목, 2016-01-04~2026-08-14, 월별 유니버스 스냅샷(상위 250종목/월, 128개월).
trend_follow은 069500(KODEX200) 별도 주입(2,852행).

> ⚠️ **rev.1 → rev.2 변경**: 유니버스 룩어헤드 편향 수정. rev.1 결과는 전부 아티팩트였음.
> 상세는 아래 "2. 룩어헤드 편향 발견" 참조.

---

## 1. baseline (전체 유니버스, 신호 그대로)

**snapshot_alignment = prev_month** (실전 전략과 동일, 룩어헤드 방지).

| 엔진 | 총수익 | 연환산 | MDD | Sharpe | 매수 |
|------|--------|--------|------|--------|------|
| vb | -99.99% | -58.6% | -99.99% | -2.99 | 6,330 |
| vb_daily | -99.99% | -57.9% | -99.99% | -2.15 | 1,754 |
| trend_follow | -98.97% | -35.7% | -98.99% | -1.21 | 436 |
| value | — | — | — | — | 보류 |

---

## 2. 룩어헤드 편향 발견 (중대)

세 엔진 모두 스냅샷을 **당월(same-month)** 으로 적용했습니다.
스냅샷은 당월 전체 거래대금 랭킹으로 산정되므로, 당월 상반기 매수 시
이미 존재하지 않는 정보(당월 후반 거래대금)를 이용하는 룩어헤드가 발생합니다.

| 항목 | same-month (rev.1) | prev_month (rev.2, 정직) |
|------|-------------------|-------------------------|
| vb baseline 연 | -18.4% (편향) | **-58.6%** |
| vb_daily baseline 연 | -28.4% (편향) | **-57.9%** |
| trend_follow baseline 연 | -25.6% (편향) | **-35.7%** |
| vb MC-D 판정 | POSITIVE_INFO (아티팩트) | **NO_INFO** |

vb의 WFA(k∈{0.5..0.9}×MA{0..20}, 8개 윈도우)와 파라미터 안정성 표면(+148%/yr at k0.9/MA5)
역시 전부 이 편향에 기반한 것입니다. prev_month 정렬에서는 전부 동일 수준(-58%대)으로 수렴.

**근본 원인**: backtest_engine(RSI 실전)은 `UNIVERSE_SNAPSHOT_ALIGNMENT=prev_month` 기본값으로
이 편향을 이미 해결했으나, 신규 엔진들이 이를 도입하지 않았습니다. 3엔진 모두 수정 완료.

---

## 3. MC-D (신호 정보량 permutation) — 정직한 결과

**snapshot_alignment = prev_month** 적용 상태.

| 엔진 | null 연평균 ± std | P5 | P95 | baseline 연 | **판정** |
|------|-------------------|----|----|-------------|----------|
| **vb** | -57.8% ± 2.7 | -59.7 | -53.0 | -58.6% | **NO_INFO** |
| **vb_daily** | -49.7% ± 3.2 | -54.9 | -44.8 | -57.9% | **NEGATIVE_INFO** |
| **trend_follow** | -17.2% ± 4.1 | -25.3 | -12.1 | -35.8% | **NEGATIVE_INFO** |

- **vb**: 신호가 무작위 선정과 구분 불가(NO_INFO). baseline(-58.6%)이 null 90% CI(-59.7~-53.0) 안에 있음.
- **vb_daily**: 신호가 무작위보다 유의미하게 나쁨(NEGATIVE_INFO). baseline(-57.9%)이 null P5(-54.9) 아래.
- **trend_follow**: RS 선정이 무작위보다 적극적으로 해롭다(NEGATIVE_INFO). 무작위 월간 5종목(-17.2%)
  대비 RS 상위 5종목(-35.8%).

---

## 4. 종합 판정

| 엔진 | MC-D | 실사용 |
|------|------|--------|
| vb | NO_INFO | ❌ 불가 |
| vb_daily | NEGATIVE_INFO | ❌ 불가 |
| trend_follow | NEGATIVE_INFO | ❌ 불가 |
| value | — | 보류 (pykrx) |

**결론: 신규 엔진 3종 모두 실사용 불가.** 룩어헤드 편향 제거 후 신호 정보량은
vb는 NO_INFO, 나머지는 NEGATIVE_INFO. WFA/파라미터 안정성 추가 검증 불필요.

---

## 5. 교훈

1. 유니버스 스냅샷 정렬: `prev_month` 사용이 필수(당월 스냅샷은 룩어헤드).
2. 인샘플 파라미터 표면이 매끄럽더라도, 공정하지 않은 유니버스로 만들어진 결과는
   전적으로 무효하다(+148%/yr → -58%/yr 붕괴).
3. 신규 엔진이 실전 전략(backtest_engine)의 컨벤션을 물려받지 못한 설계가 근본 원인.

---

## 6. 산출물

```bash
# baseline
.venv/bin/python backtest/run_strategy_baseline.py
# MC-D
.venv/bin/python backtest/run_strategy_mc_d.py --engine vb --mc-d-iters 30
.venv/bin/python backtest/run_strategy_mc_d.py --engine vb_daily --mc-d-iters 30
.venv/bin/python backtest/run_strategy_mc_d.py --engine trend_follow --mc-d-iters 30
```

- `backtest/reports/20260816_engine_validation/mc_d_*.json` — MC-D raw 결과 (prev_month 기준)
- `backtest/reports/20260816_engine_validation/validation_report_20260816.md` — 본 보고서
