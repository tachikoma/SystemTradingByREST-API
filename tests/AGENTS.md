# TEST KNOWLEDGE BASE

**대상:** `tests/` (12개 파일, pytest)

## OVERVIEW
단위 테스트 + 통합 테스트 스위트. mock API 응답을 활용한 전략/유틸리티 검증.

## FILES

| 파일 | 역할 |
|------|------|
| conftest.py | pytest fixtures (Kiwoom mock 객체 등) |
| test_rsi_values.py | RSI 계산 결과 검증 |
| test_backtest_rsi_parity.py | 백테스트/실전 전략 RSI 일치성 검증 |
| test_make_up_universe_crawler.py | 네이버 금융 크롤링 테스트 |
| test_universe_functions.py | 유니버스 생성/필터링 함수 검증 |
| test_execution_t_plus_one.py | T+1 결제일 처리 검증 |
| test_strategy_emergency_liquidation.py | 긴급 청산 조건 검증 |
| test_integration_account.py | 계좌 조회 통합 테스트 |
| test_integration_order.py | 주문 전송 통합 테스트 |
| test_integration_readonly.py | 읽기 전용 API 통합 테스트 (안전) |
| test_convert_parser.py | 데이터 변환/파싱 검증 |
| test_get_code_list_by_market.py | 종목코드 목록 조회 검증 |

## CONVENTIONS
- 통합 테스트는 `@pytest.mark.integration` 마커로 분리.
- `RUN_INTEGRATION=1` 환경변수 설정 시에만 통합 테스트 활성화.
- `pytest.ini`에서 `--ignore=scripts --ignore=backtest` 기본 설정.
- Mock 응답은 `conftest.py`에서 중앙 관리.

## COMMANDS
```bash
poetry run pytest                          # 전체 (통합 제외)
poetry run pytest -m integration           # 통합 테스트만
RUN_INTEGRATION=1 poetry run pytest -v     # 통합 포함 전체
```
