# AGENTS.md — feature/strategy-research 브랜치 지식 베이스

**생성:** 2026-08-16 (브랜치 작업 기준)

## OVERVIEW

신호 패밀리 교체 후보(변동성 돌파 vb/vb_daily, 추세추종 trend_follow, 밸류 value)를
개발하고 검증하는 리서치 브랜치. develop의 `RSIStrategy` / `backtest_engine` 은
**수정 금지** — 이 브랜치에서만 독립 엔진을 다룬다.

> **⚠️ 신규 엔진 3종 검증 결과 (2026-08-16, rev.2): 전부 실사용 불가.**
> 자세한 판정은 `backtest/reports/20260816_engine_validation/validation_report_20260816.md`.

## VALIDATION RESULT (rev.2 — 룩어헤드 편향 수정 후)

**유니버스 스냅샷은 반드시 `snapshot_alignment='prev_month'` (룩어헤드 방지).**
당월(same-month) 스냅샷은 당월 전체 거래대금 랭킹이라, 매수 시점에 존재하지 않는
정보를 사전에 사용하는 룩어헤드다. **rev.1의 모든 양호 결과는 이 편향의 아티팩트였다.**

| 엔진 | baseline 연 (prev_month) | MC-D 판정 |
|------|--------------------------|-----------|
| vb | -58.6% (MDD -99.99%) | **NO_INFO** |
| vb_daily | -57.9% (MDD -99.99%) | **NEGATIVE_INFO** |
| trend_follow | -35.7% (MDD -98.99%) | **NEGATIVE_INFO** |
| value | — | 보류 (pykrx 미설치 + 과거 PER/PBR 부재) |

- vb의 WFA/파라미터 표면(+148%/yr at k0.9/MA5)도 전부 룩어헤드 편향 기반 → prev_month에서 소멸.
- vb MC-D: same-month POSITIVE_INFO(아티팩트) → prev_month NO_INFO.

## ENGINES

| 엔진 | 파일 | 시그니처 | 비고 |
|------|------|----------|------|
| vb | `backtest/vb_engine.py` | `simulate_vb_backtest(...)` | k/MA/stop/hold, `buy_patch`·`selection_patch` 훅 |
| vb_daily | `backtest/vb_daily_engine.py` | `simulate_vb_daily(...)` | T+1, `buy_patch` 훅 |
| trend_follow | `backtest/trend_follow_engine.py` | `simulate_trend_follow(...)` | 069500 200MA 시장필터 + RS, `selection_patch` 훅 |
| value | `backtest/value_engine.py` | `simulate_value_strategy(...)` | **pykrx 의존 → 미사용** |

공통: `snapshot_alignment` 파라미터(`prev_month` 기본) 추가됨.
`daily_values`(date/portfolio_value) 반환. `validation_strategy_adapter.py`를 통해
gate 표준 포맷(Sharpe 포함)으로 정규화됨.

## VALIDATION TOOLS (이 브랜치 전용)

| 스크립트 | 역할 |
|----------|------|
| `backtest/validation_strategy_adapter.py` | 엔진 레지스트리, `run_strategy`, `normalize_results`, MC-D null(selection/buy_patch), `run_mc_d_engine`/`run_mc_d_selection` |
| `backtest/run_strategy_baseline.py` | 전체 유니버스 baseline 러너 |
| `backtest/run_strategy_mc_d.py` | 엔진별 MC-D (vb/trend_follow=selection null, vb_daily=buy_patch null) |
| `backtest/run_strategy_wfa.py` | vb WFA (룩어헤드 제거 후 무의미 → 폐기 판정에 기여) |
| `backtest/run_strategy_param_stability.py` | vb 파라미터 표면 (동일) |

## CONVENTIONS

- **유니버스 스냅샷 정렬**: `prev_month` 필수 (실전 `UNIVERSE_SNAPSHOT_ALIGNMENT=prev_month`와 동일).
- 모든 실행: `.venv/bin/python` (poetry 미사용). 데이터는 develop 워크트리와 심링크 공유.
- 069500(KODEX200): DB 테이블 `069500` (2,852행, 2015~2026) — trend_follow 시 `_load_single_stock('069500')`로 주입.
- gate 스크립트(`run_wfa_validation.py` 등 BacktestEngine 계열)는 develop 소유 — 이 브랜치에서 수정 금지.
- **검증 결과는 반드시 룩어헤드 제거(prev_month) 상태에서만 신뢰할 것.**

## COMMANDS

```bash
# baseline (전체 유니버스)
.venv/bin/python backtest/run_strategy_baseline.py

# MC-D (엔진별, n=30)
.venv/bin/python backtest/run_strategy_mc_d.py --engine vb --mc-d-iters 30
.venv/bin/python backtest/run_strategy_mc_d.py --engine vb_daily --mc-d-iters 30
.venv/bin/python backtest/run_strategy_mc_d.py --engine trend_follow --mc-d-iters 30
```

## NOTE

- develop 브랜치의 AGENTS.md는 프로젝트 전체 기준. 본 문서는 리서치 브랜치 전용.
- 모든 결과는 `backtest/reports/<날짜>_<전략>/` 아카이브로 트래킹 (output/은 gitignored).
- git identity 미설정 → 커밋 시 환경변수 사용:
  `GIT_AUTHOR_NAME="Durk-jae Yun" GIT_AUTHOR_EMAIL="iam00th@hanmail.net"`.