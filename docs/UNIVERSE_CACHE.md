# 유니버스 캐시 및 Canonical 정책

이 문서는 유니버스(전체 종목) 캐시와 정규화(canonical) 동작, 그리고 운영자가 장중에도 전체 API 갱신을 강제할 수 있는 방법에 대해 설명합니다.

## 파일/캐시 개요

- 기본 저장 위치: `DB_DIR` 환경변수(기본: `./data`)
- 주요 파일:
  - `all_stocks_kiwoom.parquet` : 키움 API로 수집한 원본 캐시
  - `all_stocks_naver.parquet` : 네이버 금융 크롤링 결과
  - `all_stocks_canonical.parquet` : `_normalize_units()`로 정규화되어 저장된 canonical 파일 (백만원 단위)

## 단위 정규화 (Canonical)

- 파이프라인의 모든 유니버스 생성 경로(키움 API, 네이버 크롤링)는 필터링 직전에 `_normalize_units()`를 호출하여 단위를 백만원으로 통일합니다.
- 원본 값은 `<컬럼>_raw`로 보존됩니다(예: `시가총액_raw`, `거래대금_raw`).
- 환경변수 `SAVE_CANONICAL` (기본: `1`)이 켜져 있으면 정규화된 DataFrame이 `all_stocks_canonical.parquet`로 저장됩니다.

## 거래대금 표준화

- 기본 동작: 수집된 `거래대금` 값을 그대로 사용합니다 (`UNIVERSE_TRADE_VALUE_METHOD=reported`).
- 재계산 모드: `UNIVERSE_TRADE_VALUE_METHOD=volume_price`로 설정하면 `거래대금_standard = 거래량 * 현재가 / 1_000_000`으로 재계산합니다.

## 장중 강제 갱신 (강제 API 호출)

- 기본적으로 키움 API를 통한 전체 종목 수집은 장 종료 후 자동으로 수행되며, 장중에는 빠른 네이버 크롤링 결과나 기존 캐시를 우선 사용합니다.
- 운영자가 장중이라도 강제로 키움 API로 전체 갱신(캐시 + canonical 저장)을 수행하려면 다음 스크립트를 사용하세요:

```bash
# 장중 강제 전체 갱신 (캐시 + canonical 저장)
poetry run python scripts/refresh_universe_now.py --force-api -y
```

- 옵션 설명:
  - `--force-api` : 장중에도 `fetch_all_stocks_from_kiwoom()`를 호출하여 전체 종목을 수집합니다.
  - `-y` / `--yes` : 실전 모드에서 확인 프롬프트를 건너뜁니다(자동화 환경에서 필요).

- 주의사항:
  - 전체 종목 수집은 시간이 소요됩니다: 모의투자 환경에서는 수십 분, 실전환경에서는 약 10분 내외가 소요될 수 있습니다.
  - 자동화 환경에서 사용 시 시간을 충분히 확보하고, 실행 로그 및 실패 시의 폴백(캐시 사용)을 모니터링하세요.

## 운영 체크리스트 (재시작 전 권장)

1. `all_stocks_canonical.parquet` 존재 확인:

```bash
[ -f ./data/all_stocks_canonical.parquet ] && echo 'canonical 존재' || echo 'canonical 없음'
```

2. canonical이 없을 경우(자동화 전)
   - 실전: `poetry run python scripts/refresh_universe_now.py --force-api -y`
   - 모의: 동일 명령 실행(소요시간이 길 수 있음)

3. canonical 생성 후 서비스 재시작.

---

문제가 발생하면 운영 로그(`logs/`)와 `data/` 디렉터리의 Parquet 파일을 확인해 주세요.
