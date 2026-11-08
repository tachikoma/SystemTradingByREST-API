# TEST KNOWLEDGE BASE

**대상:** `tests/` (11개 파일, pytest)

## OVERVIEW
단위 테스트 + 통합 테스트 스위트. mock API 응답을 활용한 전략/유틸리티 검증.

## FILES

| 파일 | 역할 |
|------|------|
| conftest.py | pytest fixtures (Kiwoom mock 객체 등) |
| test_rsi_values.py | RSI 계산 결과 검증 |
| test_make_up_universe_crawler.py | 네이버 금융 크롤링 테스트 |
| test_universe_functions.py | 유니버스 생성/필터링 함수 검증 |
| test_universe_empty_guard.py | 빈 유니버스/빈 캐시가 기존 데이터를 덮어쓰지 않는지 검증 |
| test_shadow_mode.py | 섀도우 모드 주문 차단·신호 중복 억제·전역 킬스위치 검증 |
| test_execution_t_plus_one.py | T+1 결제일 처리 검증 |
| test_integration_account.py | 계좌 조회 통합 테스트 |
| test_integration_order.py | 주문 전송 통합 테스트 |
| test_integration_readonly.py | 읽기 전용 API 통합 테스트 (안전) |
| test_get_code_list_by_market.py | 종목코드 목록 조회 검증 |

## CONVENTIONS
- 통합 테스트는 `@pytest.mark.integration` 마커로 분리.
- `RUN_INTEGRATION=1` 환경변수 설정 시에만 통합 테스트 활성화.
- `pytest.ini`에서 `--ignore=scripts --ignore=backtest` 기본 설정.
- Mock 응답은 `conftest.py`에서 중앙 관리.
- 루트 `.gitignore`의 `test*.py` 패턴 때문에 신규 테스트 파일은 커밋되지 않는다.
  추가 시 `!tests/<파일명>.py` 예외를 반드시 함께 등록할 것.

## COMMANDS
```bash
poetry run pytest                          # 전체 (통합 제외)
poetry run pytest -m integration           # 통합 테스트만
RUN_INTEGRATION=1 poetry run pytest -v     # 통합 포함 전체
```
