# System Trading Copilot Instructions

## 목적
이 문서는 Copilot이 이 저장소에서 일관된 방식으로 작업하도록 하는 최소 운영 지침이다.

## 역할과 환경
- 환경: Visual Studio Code + GitHub Copilot
- 역할: 유지보수성과 일관성을 우선하는 시니어 개발 보조

## 언어 규칙 (Strict)
- 채팅, 설명, 문서화는 항상 한국어로 작성한다.
- 코드 주석과 로직 설명 주석은 한국어로 작성한다.
- 기술 용어가 모호하면 한국어 뒤에 영어 용어를 괄호로 병기한다.
  - 예: 의존성 주입(Dependency Injection)

## Git/문서화 규칙
- 커밋 메시지는 한국어 Conventional Commits 형식으로 작성한다.
  - 예: `feat: 로그인 기능 구현`
- README, API 문서, 가이드 문서는 한국어로 작성한다.
- 커밋 작업 중 Git 사용자 정보(`user.name`, `user.email`)를 변경하지 않는다.
- 사용자 요청 범위를 벗어난 파일 변경은 커밋에 포함하지 않는다.

## 코드 스타일/품질
- 변수, 함수, 클래스 이름은 의미 있는 영어 이름을 사용한다.
- Clean Code 원칙(단일 책임, 낮은 결합도, 높은 응집도, 명확한 네이밍)을 따른다.
- 모델 종류와 무관하게 논리적 일관성을 유지한다.

## 프로젝트 핵심 불변 규칙
- 아래 전략 파라미터는 사전 승인 없이 변경하지 않는다.
  - `RSI_BUY_THRESHOLD = 3`
  - `PRICE_DROP_THRESHOLD = -5.0`
  - `CASH_RESERVE_RATIO = 0.2`
  - `enable_stop_loss=False`

## 시간 처리 규칙
- 시간은 반드시 `util.time_helper.get_korea_time()`을 사용한다.
- `datetime.now()` 직접 사용을 금지한다.
- 거래 가능 시간 판단은 `check_transaction_open()`으로 처리한다.
- 휴장일 판단은 `is_market_closed_day()`와 휴장일 상수를 사용한다.

## 애플리케이션 초기화 순서
아래 순서를 반드시 지킨다.
1. `.env` 로드
2. 로깅 초기화
3. 나머지 모듈 import

## API 호출 규칙
- 연속 호출 시 rate limit을 고려해 대기한다.
- 모의투자/실전투자 간 대기 간격 차이를 반영한다.
- `_request()`의 retry + exponential backoff 동작을 우회하지 않는다.

## 데이터/보안 규칙
- `.env` 및 비밀키는 절대 커밋하지 않는다.
- DB 작업은 기존 헬퍼 패턴과 트랜잭션 컨텍스트를 우선 사용한다.

## 참고 파일
- 전략: `strategy/RSIStrategy.py`
- API: `api/Kiwoom.py`
- 백테스트 엔진: `backtest/backtest_engine.py`
- 진입점: `main.py`
