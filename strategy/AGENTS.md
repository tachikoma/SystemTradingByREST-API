# STRATEGY KNOWLEDGE BASE

**대상:** `strategy/RSIStrategy.py` (2665라인)

## OVERVIEW
RSI(상대강도지수)와 이동평균선을 결합하여 과매도 종목을 발굴하고 자동 매매를 수행하는 핵심 엔진.

## WHERE TO LOOK
- `RSIStrategy` (L71): `threading.Thread`를 상속받은 메인 전략 클래스.
- `run()` (L1305): 메인 이벤트 루프. 1초 간격으로 장 상태 확인 및 로직 트리거.
- `check_and_get_price_data()` (L1171): 기술 지표 계산을 위한 과거/실시간 가격 데이터 수집.
- `calculate_rsi()` (L1968): Wilder/EWMA 방식을 사용한 RSI 지표 산출.
- `check_buy_signal_and_order()` (L2366): 매수 조건 검증 및 주문 실행.
- `check_sell_signal()` (L2078): 매도 조건(RSI 과열, 목표 수익률, 시간 손절) 검증.
- `order_sell()` (L2180): 시장가/지정가 매도 주문 처리 및 상태 갱신.
- `apply_env_updates()` (L443): `.env` 변경 사항 실시간 반영 (재시작 없이 설정 변경).

## KEY CLASSES & CONSTANTS
백테스트(2016-2025)를 통해 검증된 최적 파라미터이며 임의 변경을 엄격히 금지함.
- `MAX_HOLDINGS = 10`: 포트폴리오 최대 보유 종목 수.
- `RSI_BUY_THRESHOLD = 3`: 극심한 과매도 상태(RSI < 3)에서만 진입.
- `PRICE_DROP_THRESHOLD = -5.0`: 전일 대비 5% 이상 하락 시 진입 조건 강화.
- `CASH_RESERVE_RATIO = 0.2`: 총 예수금의 20%는 항상 현금으로 유지.
- `PROFIT_TARGET_PERCENT = 10.0`: 최소 목표 수익률.

## CONVENTIONS
- **Thread 상속:** `main.py`에서 별도 스레드로 기동되어 비동기적으로 시세를 모니터링함.
- **Star Import:** `api.Kiwoom`, `util.db_helper` 등에서 `*` 임포트 패턴 사용 (레거시 유지).
- **매매 윈도우:**
  - 오후 종가 매수: 14:50 ~ 15:20 (주력 진입 시간).
  - 오전 보정(Morning Fallback): 09:00 ~ 09:20 (전일 미체결분 처리).
- **에러 처리:** `notify_on_exception` 데코레이터를 사용하여 치명적 오류 시 텔레그램 알림.

## ANTI-PATTERNS
- **상수 수정 금지:** `RSI_BUY_THRESHOLD`, `CASH_RESERVE_RATIO` 등은 승인 없이 절대 변경 불가.
- **손절 비활성화:** `enable_stop_loss=False` 기본값 유지. 데이터상 손절보다 시간 손절(90일)이 유리함.
- **직접 시간 호출:** `datetime.now()` 대신 반드시 `util.time_helper.get_korea_time()` 사용.
- **API 직접 호출:** `self.kiwoom` 객체의 래퍼 메서드를 통하지 않은 로우 레벨 API 호출 금지.
