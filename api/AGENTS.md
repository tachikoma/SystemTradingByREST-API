# API KNOWLEDGE BASE

**대상:** `api/Kiwoom.py` (1071라인)

## OVERVIEW
Kiwoom OpenAPI REST + WebSocket 클라이언트. 모든 외부 API 통신과 인증, 실시간 시세 처리를 담당.

## KEY METHODS

| 메서드 | 라인 | 역할 |
|--------|------|------|
| `_authenticate()` | 79 | access token 발급 (OAuth2 client_credentials) |
| `_request()` | 118 | 모든 API 호출의 공통 래퍼 (인증/재시도 포함) |
| `get_price_data()` | 327 | 일별 OHLCV 히스토리 조회 (페이징 처리) |
| `get_deposit()` | 434 | 주문 가능 예수금 조회 |
| `send_order()` | 473 | 매수/매도 주문 전송 |
| `get_order()` | 582 | 미체결 주문 목록 조회 |
| `get_balance()` | 668 | 계좌 잔액/보유 종목 조회 (매수일 보존 로직 포함) |
| `get_executions_for_code()` | 757 | 주문/체결 이력 조회 |
| `set_real_reg()` | 892 | WebSocket 실시간 구독 등록 |
| `_on_receive_real_data()` | 936 | 실시간 체결/주문 데이터 처리 |
| `_handle_order_execution()` | 967 | 주문체결 실시간 데이터 → balance/order 동기화 |

## CONVENTIONS
- `_request()` 래퍼로 모든 API 호출 통일. 우회 금지.
- retry는 `return_code == 5` (rate-limit) 응답에서만 수행. 다른 오류는 즉시 중단.
- 페이징 처리: `cont-yn`/`next-key` 헤더 기반 do-while 패턴.
- 모의(`mockapi.kiwoom.com`) / 실전(`api.kiwoom.com`) URL을 `mock` 플래그로 분기.
- WebSocket 재연결시 `_real_reg_info` 저장값으로 구독 자동 재등록.
- 중복 LOGIN 방지: `asyncio.Lock` + `_websocket_logged_in`/`_websocket_login_sent` 플래그.

## ANTI-PATTERNS
- `_request()`를 거치지 않은 직접 `self.session.post()` 호출 금지.
- rate-limit 응답 외의 오류에서 무분별한 재시도 금지.
- `main.py` 외에서 API 키·`KIWOOM_MODE` 직접 변경 금지.
