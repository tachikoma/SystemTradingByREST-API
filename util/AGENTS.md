# UTIL KNOWLEDGE BASE

생성일: 2026-06-20
스택: Python 3.11+, SQLite, ZoneInfo

## OVERVIEW
공용 유틸리티로 DB, 시간, 로깅, 알림, 종목 선정, RSI 계산을 수행하는 핵심 모듈 모음이다.

## WHERE TO LOOK
| 모듈 | 역할 |
| :--- | :--- |
| time_helper.py | 한국 시간대 처리와 휴장일 판단, 장 운영 시간 여부를 확인한다. |
| db_helper.py | SQLite CRUD와 구매일 업데이트(upsert_purchase_date)를 담당한다. |
| logging_config.py | RotatingFileHandler를 설정하고 환경변수에 따른 로그 정책을 관리한다. |
| shutdown.py | 시그널 핸들러를 통해 안전한 종료와 클린업 로직, 종료 알림을 처리한다. |
| notifier.py | 텔레그램 메시지 전송과 예외 알림용 데코레이터를 제공한다. |
| make_up_universe.py | 키움 API와 네이버 크롤링을 결합해 매매 대상 유니버스를 구성한다. |
| rsi_calc.py | Wilder 방식을 사용하는 RSI 계산 함수(compute_rsi)를 포함한다. |
| const.py | 빈 파일이며 기존 시스템 구성과의 호환성 유지를 위해 남겨두었다. |
| env_reloader.py | .env 파일의 변경 사항을 실시간으로 감시하고 동적으로 재로드한다. |
| env_approver.py | 민감한 환경변수가 변경될 때 사용자 승인 절차를 수행한다. |
| price_fetcher.py | 전략 실행에 필요한 가격 데이터를 효율적으로 가져온다. |
| trade_logger.py | 실제 발생한 매매 이력을 별도의 로그 파일이나 DB에 기록한다. |
| practice_crawling.py | 크롤링 로직 테스트와 연습을 위한 보조 스크립트다. |
| telegram_test.py | 텔레그램 알림 기능이 정상적으로 작동하는지 확인하는 테스트 도구다. |

## CONVENTIONS
* 로깅 설정. 모든 모듈은 `util.logging_config.get_logger(__name__)`를 호출해 로거를 생성한다.
* 시간대 통일. 한국 시간 기준인 `ZoneInfo("Asia/Seoul")`를 사용해 모든 시점 데이터를 관리한다.
* 에러 처리. `notifier.py`의 `notify_on_exception`을 활용해 치명적 오류를 즉시 전송한다.
* 모듈 의존성. 유틸리티끼리 서로 참조할 때는 순환 참조가 발생하지 않도록 주의한다.

## ANTI-PATTERNS
* `datetime.now()` 사용 금지. 시스템 로컬 시간이 아닌 `time_helper.get_korea_time()`을 써야 한다.
* 직접 DB 연결 금지. 개별 모듈에서 `sqlite3.connect`를 쓰지 않고 `db_helper`를 통해 작업한다.
* 하드코딩 금지. 파일 경로나 디렉터리 설정은 환경변수나 중앙 설정 파일을 이용해야 한다.
* 전역 상태 지양. 유틸리티 함수는 가급적 순수 함수 형태로 작성해 예측 가능성을 높인다.
