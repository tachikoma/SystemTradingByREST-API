## Plan: `is_naver_crawl_allowed`로 리팩터링 및 샘플검사 기반 허용 정책

TL;DR: 기존 `is_market_hours`를 의미에 맞게 `is_naver_crawl_allowed()`로 이름 변경하고, 기본 허용시간(한국시간 09:30~익일 06:00) 외 시간(휴장일 포함)은 로컬 캐시의 샘플검증을 통과한 경우에만 크롤링을 허용합니다. 시간 판정은 `util.time_helper.get_korea_time()`을 사용합니다.

**Steps**
1. `util/time_helper.py` 개선 (*선행*)
   - **추가**: `previous_trading_day(ref_date=None) -> date` — 주말/공휴일을 건너뛴 마지막 영업일 반환.
   - **변경**: `is_market_closed_day(ref_date=None)` (옵션 `ref_date`)으로 확장하여 특정 날짜에 대해 휴장 여부 판단 가능하도록 함(하위호환 유지).

2. `util/make_up_universe.py` 리팩터링 (*depends on step 1*)
   - **함수명 변경**: `is_market_hours()` → `is_naver_crawl_allowed()`
   - **동작 요약**:
     - `now = get_korea_time()` 사용
     - 시간 기준 빠패스: 09:30 <= time or time <= 06:00 → 즉시 `True`
     - 그 외(즉 비허용 시간 또는 휴장일)일 경우 `validate_cache_for_crawling()` 호출하여 통과하면 `True`, 아니면 `False` 반환
   - **샘플검증 헬퍼**: `validate_cache_for_crawling(db_dir=DB_DIR, sample_size=..., select='top', min_trade_value=..., pass_ratio=..., cache_priority=[...])` 추가
     - 캐시 우선순위(`all_stocks_kiwoom.parquet` → `all_stocks_naver.parquet`)에 따라 존재하는 파일을 검사
     - 필요한 컬럼(`종목코드`,`종목명`,`시가총액`,`거래대금_standard` 우선, 없으면 `거래대금`,`거래량`)만 Parquet에서 읽어 샘플 선택
     - 샘플 선택: 시가총액 상위 N개(또는 화이트리스트/무작위)
     - 각 샘플에 대해 `trade_val >= min_trade_value` 및 `거래량 > 0` 검사; 성공 개수 >= ceil(N*pass_ratio)이면 통과
     - 통과 시 추가 안전성: 캐시 파일의 `mtime.date()`가 `previous_trading_day()` 이상인지 확인(옵션)
   - **하위호환**: 당분간 `is_market_hours()`라는 얇은 shim을 만들어 `is_naver_crawl_allowed()`를 호출하고 경고 로그를 남김(즉시 변경이 부담스러운 호출부를 보호).
   - **`get_universe()` 통합**: 네이버 크롤링을 바로 호출하기 전 허용 여부 확인 로직을 `is_naver_crawl_allowed()`로 교체. 기존의 `08:00-09:00` 스킵 로직은 유지하되, 새 허용 정책과 충돌하지 않도록 조정.

3. 스크립트/테스트 정비 (병행 가능)
   - `scripts/test_naver_crawling.py` 등 호출부에서 함수명 교체(또는 shim 사용으로 최소 변경)
   - 신규 단위테스트: `tests/test_crawl_sample_check.py` 추가
     - 케이스: 평일 허용시간(예: 10:00) → True
     - 평일 심야(예: 07:00) → 샘플검사 결과에 따라 True/False
     - 휴장일 + 캐시(전 거래일) + 샘플충족 → True
     - 휴장일 + 캐시없음/샘플불충족 → False
     - 컬럼누락/파싱오류 → False(로그 확인)

4. 문서화 및 환경변수
   - 새 환경변수(기본값 권장):
     - `NAVER_CRAWL_SAMPLE_SIZE=10`
     - `NAVER_CRAWL_SAMPLE_SELECT=top`  # top|whitelist|random
     - `NAVER_CRAWL_MIN_TRADE_VALUE=1000`  # 단위: 백만원
     - `NAVER_CRAWL_MIN_PASS_RATIO=0.7`
     - `NAVER_CRAWL_CACHE_PRIORITY=kiwoom,naver`
   - `readme.md` 또는 `docs/`에 크롤링 허용정책 명시

**Relevant files**
- [util/time_helper.py](util/time_helper.py) — `previous_trading_day()` 추가, `is_market_closed_day(ref_date=None)` 확장
- [util/make_up_universe.py](util/make_up_universe.py) — `is_naver_crawl_allowed()` 구현(기존 `is_market_hours` 대체), `validate_cache_for_crawling()` 추가, `get_universe()` 게이트 보강
- [scripts/test_naver_crawling.py](scripts/test_naver_crawling.py) — 호출부 조정
- [tests/test_crawl_sample_check.py](tests/test_crawl_sample_check.py) — 신규 테스트

**Verification**
1. 유닛 테스트: `pytest tests/test_crawl_sample_check.py -q`
2. 통합 테스트: `python -m util.make_up_universe`로 수동 시나리오(캐시 존재/부재)에 대한 경로 확인
3. 전체 관련 테스트: `pytest test_market_status.py test_market_holidays.py -q`

**Decisions**
- 함수명은 `is_naver_crawl_allowed()`로 변경(명확성 우선).
- 하위호환성 고려: 당분간 `is_market_hours()` shim을 유지해 급격한 호출부 변경을 완화.
- "이전 거래일 데이터" 판정은 샘플검증(시가총액 상위 N개 중 거래대금·거래량 임계치 충족) + 캐시 `mtime` 검증을 결합하여 판단.

**Further Considerations**
1. Parquet 파일이 매우 클 경우 상위 N개 추출이 비용이 큼 — 운영에서는 별도 요약 메타데이터(일일 top-N 요약 파일) 생성 권장.
2. 더 엄밀한 검증을 원하면 Parquet 내부의 날짜/타임스탬프 컬럼을 직접 확인하여 "전 거래일 데이터 포함 여부"를 판별하도록 개선.
3. 운영 로그 및 알람: 샘플검증 실패 원인을 추적하도록 모니터링 지표/로그 레벨 설계 필요.

**Next**
- 지금 구현(코드 변경 + 테스트 작성)으로 진행할까요, 아니면 우선 PR 텍스트/패치 초안을 보여드릴까요?
