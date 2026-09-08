# RSI→VALUE 전환 계획 폐기 기록 (2026-09-08)

## 결론
- `rsi-to-value-transition.md` (Wave 1, 2026-06-22 작성) 및 관련 OMO 아티팩트 10건을 폐기한다.
- 사유: 전제(VALUE 모드 전환)가 2026-08-16 rev.2 검증에서 소멸했기 때문.
- `docs/plans/rsi-to-value-transition.md` 복사본도 함께 제거한다. 본 기록이 유일한 정리가이드로 남는다.

## 폐기 대상 (본 커밋에서 D 처리)
- `.omo/plans/rsi-to-value-transition.md` (= `docs/plans/` 복사본과 identical 확인)
- `.omo/plans/value-strategy-bugfix.md`
- `.omo/plans/value-strategy-github-actions.md`
- `.omo/drafts/value-strategy-github-actions.md`
- `.omo/evidence/task-1-branch-diff-analysis.md`
- `.omo/evidence/task-2-merge-prep.md`
- `.omo/evidence/task-3-env-blueprint.md`
- `.omo/evidence/task-4-dry-run-output.txt`
- `.omo/evidence/task-4-vps-runbook.md`
- `.omo/evidence/value-strategy-bugfix-verification.txt`
- `.gitignore` OMO 추적 예외 블록 제거 (`.omo/` 관련 5줄)
- `docs/plans/rsi-to-value-transition.md` (untracked 복사본, 원본과 identical이므로 삭제)

## 폐기 근거
- `backtest/reports/20260816_engine_validation/validation_report_20260816.md` (rev.2):
  - vb baseline 연 -58.6%, MC-D **NO_INFO**
  - vb_daily baseline 연 -57.9%, MC-D **NEGATIVE_INFO**
  - trend_follow baseline 연 -35.7%, MC-D **NEGATIVE_INFO**
  - value: pykrx 미설치 + 과거 PER/PBR 부재로 **보류**
- rev.1의 양호 결과(WFA, 파라미터 표면 +148%/yr 등)는 same-month 스냅샷 룩어헤드 아티팩트로 판명,
  `snapshot_alignment='prev_month'` 정직 조건에서 소멸.
- 따라서 "feature/strategy-research → develop 머지 후 VALUE 전환" 실행 명분이 없음.
  Wave 2(트리거 기반 머지+배포)는 무기한 보류, Wave 1 산출물(.env 블루프린트, 런북)도 현행 유효성 없음.

## 복원 방법
- 폐기 전 최종 상태는 `3ac2a09` (`docs: feature/strategy-research 브랜치 AGENTS.md 추가`)에 보존됨.
- 예: `git show 3ac2a09:.omo/plans/rsi-to-value-transition.md`
- VALUE 전략을 재개하려면 rev.2 이후 신규 검증부터 다시 수행할 것.

## 확인
- `git status --porcelain` 상 `.omo` 전체 삭제 + `docs/plans/` 제거 확인 후 커밋.
- 본 폴더(`backtest/reports/20260908_plan_retirement/`) 외에 전환 계획 잔재 없음.
