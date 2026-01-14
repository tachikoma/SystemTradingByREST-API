목표: `RSIStrategy.py`의 주문 알림에 종목명(종목코드) 형식을 적용하고, DB 캐시 우선 조회 및 (옵션) Kiwoom API 호출로 종목명을 확보하여 메시지 포맷을 변경한다.

요구 요약
- DB 캐시 우선: `util/db_helper.py`에 `master_list(code TEXT PRIMARY KEY, name TEXT)` 테이블과 다음 함수 추가
  - `get_stock_name(db_name: str, code: str) -> Optional[str]`
  - `upsert_stock_name(db_name: str, code: str, name: str) -> None`
  - `load_all_stock_names(db_name: str) -> Dict[str, str]`
- 유니버스 수집 시 영속화: `util/make_up_universe.py`에서 수집한 (code,name)을 DB에 upsert
- 전략 초기화 및 런타임 로드: `strategy/RSIStrategy.py`에서
  - `self.allow_kiwoom_calls` 플래그(기본 False) 추가
  - `self.universe_map = load_all_stock_names(db_name)` 로 로컬 캐시 로드
  - `def resolve_stock_name(self, code):` 구현(메모리 캐시 → DB → Kiwoom API(플래그 허용) → DB 업서트 → 반환)
- 메시지 형식 변경: 모든 `send_message` 호출(매수/매도/청산 등) 직전에
  - `name = self.resolve_stock_name(code)`
  - `display = f"{name}({code})" if name else code`
  - 메시지에 `display` 사용
- Kiwoom 호출 제어: `api/Kiwoom.py`의 `get_master_code_name`(또는 safe 래퍼)를 사용하되, 호출은 `allow_kiwoom_calls`가 True인 경우에만 수행
- 테스트: 메시지 문자열 검사 단언이 존재하면 기대값을 완화하거나 새 테스트 추가

세부 작업 항목(우선순위)
1. `util/db_helper.py`에 테이블 보장 및 CRUD 헬퍼 추가
2. `util/make_up_universe.py`에 upsert 호출 추가
3. `strategy/RSIStrategy.py` 수정: 플래그, 캐시 로드, `resolve_stock_name`, `send_message` 포맷 변경
4. `api/Kiwoom.py` 안전 호출 확인/추가
5. 테스트 수정/추가 및 전체 테스트 실행
6. 변경 커밋 및 CI 실행

동작 원칙
- 네트워크 호출 최소화: DB 캐시 우선, `allow_kiwoom_calls=False` 기본
- 실패 대체: 종목명을 못 찾으면 기존 `code` 사용(주문 동작 불변)
- 안전성: DB 작업은 `with sqlite3.connect(...)` 패턴, 예외 로깅

파일명 규칙(권장)
- 전략별 DB 파일명: `{strategy_name}.db` (기존 규약 준수)
- 마스터 리스트 DB 파일 예시: `master_list.db` (유니버스 전체 저장용)

적용 방식
- 먼저 `util/db_helper.py`를 추가/수정하고, `strategy/RSIStrategy.py` 내 캐시 로드와 `resolve_stock_name` 구현을 적용
- 그 다음 `util/make_up_universe.py`에서 수집 시 DB 업서트를 추가하여 초기 데이터 확보
- 테스트를 실행해 메시지 포맷 관련 실패를 보정

비고
- 본 계획은 메시지 텍스트 변경만 수행하므로 런타임 리스크는 낮음. 다만 테스트 기대치만 조정하면 안전하게 배포 가능.

다음 단계: 원하시면 이미 적용되어 있는지 먼저 확인하고, 지금 패치를 실제 파일에 적용하고 테스트를 실행하겠습니다. (지금 적용할까요?)
