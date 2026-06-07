import requests
import numpy as np
import pandas as pd
from datetime import datetime, time as datetime_time
from zoneinfo import ZoneInfo
import os
import gc
import time
from typing import Optional
from util.time_helper import check_transaction_closed
from util.logging_config import get_logger
from pathlib import Path


# Directory for cache/data files (Excel, universe outputs)
DB_DIR = os.getenv("DB_DIR", "./data")
Path(DB_DIR).mkdir(parents=True, exist_ok=True)

BASE_URL = 'https://finance.naver.com/sise/sise_market_sum.nhn?sosok='
CODES = [0, 1]  # KOSPI:0, KOSDAQ:1
START_PAGE = 1
now = datetime.now(ZoneInfo("Asia/Seoul"))
formattedDate = now.strftime("%Y%m%d")

# 모의투자 매매제한 종목 코드 리스트
MOCK_TRADE_BLACKLIST_CODES = [
    '023760',  # 한국캐피탈
    # 추가 제한 종목은 여기에 추가
]

logger = get_logger(__name__)


ETF_NAME_PREFIXES = (
    '1Q',
    'KODEX',
    'KoAct'
    'TIGER',
    'ARIRANG',
    'KOSEF',
    'KBSTAR',
    'HK',
    'HANARO',
    'SOL',
    'ACE',
    'TIME',
    'PLUS',
    'RISE',
    'TREX',
    'FOCUS',
    'KIWOOM',
    'WON',
)
_FDR_ETF_SYMBOLS = None

def _normalize_code(code):
    """종목코드 정규화: 숫자만 추출하여 선행 0까지 포함한 문자열 반환"""
    try:
        return ''.join(ch for ch in str(code) if ch.isdigit())
    except Exception:
        return str(code)
def _parse_env_csv_set(value):
    if not value:
        return set()
    return {item.strip() for item in str(value).split(',') if item and item.strip()}


def _is_etf_name(name):
    """이름 기반 ETF 판별(기존 로직 유지)"""
    if not isinstance(name, str):
        return False
    s = name.strip().upper()
    return s.startswith(ETF_NAME_PREFIXES) or (' ETF' in s) or s.endswith('ETF')


def _load_fdr_etf_symbols():
    """FinanceDataReader로 ETF 종목 코드 집합을 로드(캐시). 실패하면 None 반환."""
    global _FDR_ETF_SYMBOLS
    if _FDR_ETF_SYMBOLS is not None:
        return _FDR_ETF_SYMBOLS
    try:
        import FinanceDataReader as fdr
    except Exception as e:
        logger.debug("FinanceDataReader import 실패: %s", e)
        _FDR_ETF_SYMBOLS = None
        return None
    try:
        # FinanceDataReader 예시: fdr.StockListing('ETF/KR')
        df_etf = fdr.StockListing('ETF/KR')
        if df_etf is None or df_etf.empty:
            _FDR_ETF_SYMBOLS = set()
            return _FDR_ETF_SYMBOLS
        # 컬럼명은 환경에 따라 다를 수 있으므로 'Symbol' 또는 'Code' 등을 탐색
        sym_col = next((c for c in df_etf.columns if c.lower() in ('symbol', 'code')), df_etf.columns[0])
        syms = set(_normalize_code(s) for s in df_etf[sym_col].astype(str).values if s)
        _FDR_ETF_SYMBOLS = syms
        logger.info("FinanceDataReader ETF 목록 로드: %d개", len(syms))
        return _FDR_ETF_SYMBOLS
    except Exception as e:
        logger.warning("FinanceDataReader ETF 목록 로드 실패: %s", e)
        _FDR_ETF_SYMBOLS = None
        return None


def _compute_etf_mask(df):
    """
    DataFrame에서 ETF 여부 마스크를 생성한다.
    우선순위:
    1) 'instt_tp_nm' 컬럼 존재 시 해당 컬럼에 'ETF' 포함 여부 사용 (KRX API)
    2) '종목코드' 존재 시 FinanceDataReader로 ETF 코드 여부 확인
    3) 위 모두 실패하면 이름 기반 판별(_is_etf_name)으로 폴백
    """
    if 'instt_tp_nm' in df.columns:
        try:
            return df['instt_tp_nm'].astype(str).str.upper().str.contains('ETF').fillna(False)
        except Exception:
            pass

    # 코드 기반 판별 시도
    if '종목코드' in df.columns:
        try:
            codes_norm = df['종목코드'].astype(str).map(_normalize_code)
            fdr_syms = _load_fdr_etf_symbols()
            if fdr_syms:
                mask_code = codes_norm.isin(fdr_syms)
                # 이름 기반과 병합: 코드 판별에 실패한 항목은 이름 판별로 보완
                if '종목명' in df.columns:
                    mask_name = df['종목명'].astype(str).map(_is_etf_name)
                    return (mask_code | mask_name).fillna(False)
                return mask_code.fillna(False)
        except Exception:
            pass

    # 마지막 폴백: 이름 기반 판별
    if '종목명' in df.columns:
        try:
            return df['종목명'].astype(str).map(_is_etf_name).fillna(False)
        except Exception:
            pass

    return pd.Series([False] * len(df), index=df.index)


def _normalize_units(df, source_hint: Optional[str] = None, save_canonical: bool = False):
    """
    입력 DataFrame의 주요 수치 컬럼을 표준 단위(백만원)로 정규화합니다.

    - 보존: 원본 컬럼은 '<col>_raw'로 보존합니다.
    - heuristics:
      * 시가총액 중앙값 < 10000 -> '억원' 단위로 간주하여 ×100
      * 시가총액 중앙값 > 1_000_000 -> '원' 단위로 간주하여 /1_000_000
      * 거래대금에 대해서도 유사 규칙을 적용

    반환값: 정규화된 DataFrame (원본을 변경하지 않음)
    """
    try:
        df_norm = df.copy()
    except Exception:
        df_norm = df

    def _parse_numeric_series(srs):
        if srs is None:
            return pd.Series(dtype=float)
        s = srs.astype(str).str.replace(',', '', regex=False).str.strip()
        s = s.str.replace('%', '', regex=False)
        # 숫자 외 문자는 제거하여 숫자로 변환 시도
        s_clean = s.str.replace(r'[^0-9\.\-]', '', regex=True)
        return pd.to_numeric(s_clean, errors='coerce')

    numeric_cols = ['시가총액', '거래대금', '거래량', '등락률', '외국인비율']
    for col in numeric_cols:
        if col in df_norm.columns and f'{col}_raw' not in df_norm.columns:
            try:
                df_norm[f'{col}_raw'] = df_norm[col]
            except Exception:
                pass

    # --- 시가총액 정규화(백만원 기준) ---
    if '시가총액' in df_norm.columns:
        cap_raw_str = df_norm['시가총액'].astype(str)
        cap = _parse_numeric_series(df_norm['시가총액'])
        # 명시적 '억' 표기가 있으면 해당 행만 ×100
        try:
            has_eok = cap_raw_str.str.contains('억', na=False)
            if has_eok.any():
                cap.loc[has_eok] = cap.loc[has_eok] * 100
        except Exception:
            pass

        try:
            median_cap = float(cap.dropna().median()) if not cap.dropna().empty else float('nan')
        except Exception:
            median_cap = float('nan')

        # 단위 추정 및 보정
        try:
            if not np.isnan(median_cap):
                # 가격 * 상장주식수와 비교하여 '천원' 단위로 저장된 경우를 검사
                if '현재가' in df_norm.columns and '상장주식수' in df_norm.columns:
                    price = pd.to_numeric(df_norm.get('현재가', pd.Series([0] * len(df_norm))), errors='coerce').fillna(0).astype(float)
                    shares = pd.to_numeric(df_norm.get('상장주식수', pd.Series([0] * len(df_norm))), errors='coerce').fillna(0).astype(float)
                    # 계산된 시가총액(원 단위)의 중앙값
                    calc_cap_won_median = (price * shares).replace(0, float('nan')).median(skipna=True)

                    if not pd.isna(calc_cap_won_median) and calc_cap_won_median > 0 and median_cap > 0:
                        approx_ratio = calc_cap_won_median / median_cap
                        # approx_ratio ~1000인 경우: raw cap이 '천원' 단위로 저장되어 있음
                        if 100 < approx_ratio < 10000:
                            # '천원' -> '백만원'으로 변환: raw(천원) * 1_000(원) / 1_000_000(백만원) = raw / 1_000
                            cap = cap / 1000.0
                        else:
                            # 기존 휴리스틱 적용
                            if median_cap < 10000:
                                cap = cap * 100
                            elif median_cap > 1_000_000:
                                cap = cap / 1_000_000
                    else:
                        # price/상장주식수 정보 없거나 계산 불가 시 기존 휴리스틱 사용
                        if median_cap < 10000:
                            cap = cap * 100
                        elif median_cap > 1_000_000:
                            cap = cap / 1_000_000
                else:
                    # 가격 또는 상장주식수 정보가 없으면 기존 규칙 적용
                    if median_cap < 10000:
                        cap = cap * 100
                    elif median_cap > 1_000_000:
                        cap = cap / 1_000_000
        except Exception:
            # 보정 실패 시 원본 유지
            pass

        df_norm['시가총액'] = cap

        # --- 상장주식수 단위 자동 보정 ---
        # 일부 ETN/선물/인버스 등에서는 상장주식수 단위가 원래 기대값보다 1/1000으로 들어오는 경우가 관찰됩니다.
        # 가격 * 상장주식수로 계산한 시가총액(원) / 컬럼 시가총액(원) 비율이 약 0.001 근처인 경우 상장주식수에 *1000 보정을 적용합니다.
        try:
            if '상장주식수' in df_norm.columns and '현재가' in df_norm.columns:
                if '상장주식수_raw' not in df_norm.columns:
                    try:
                        df_norm['상장주식수_raw'] = df_norm['상장주식수']
                    except Exception:
                        pass

                price_n = pd.to_numeric(df_norm['현재가'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                shares_n = pd.to_numeric(df_norm['상장주식수'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                cap_won = pd.to_numeric(df_norm['시가총액'].astype(float), errors='coerce').fillna(0) * 1_000_000

                # 안전하게 비율 계산 (분모 0 방지)
                with np.errstate(divide='ignore', invalid='ignore'):
                    ratio = (price_n * shares_n) / cap_won.replace({0: np.nan})

                # 비율이 0.0005~0.002 범위면 상장주식수 단위를 보정
                mask = ratio.between(0.0005, 0.002)
                if mask.any():
                    try:
                        shares_n.loc[mask] = shares_n.loc[mask] * 1000.0
                        df_norm['상장주식수'] = shares_n
                        logger.info("상장주식수 단위 보정 적용: %d개 행에 대해 *1000", int(mask.sum()))
                    except Exception:
                        logger.debug("상장주식수 보정 시도 중 오류 발생")
        except Exception:
            # 보정 로직 실패 시 무시
            pass

    # --- 거래대금 정규화(백만원 기준) ---
    if '거래대금' in df_norm.columns:
        amt_raw_str = df_norm['거래대금'].astype(str)
        amt = _parse_numeric_series(df_norm['거래대금'])
        try:
            has_eok_amt = amt_raw_str.str.contains('억', na=False)
            if has_eok_amt.any():
                amt.loc[has_eok_amt] = amt.loc[has_eok_amt] * 100
        except Exception:
            pass

        try:
            median_amt = float(amt.dropna().median()) if not amt.dropna().empty else float('nan')
        except Exception:
            median_amt = float('nan')

        if not np.isnan(median_amt):
            if median_amt > 1_000_000:
                amt = amt / 1_000_000
            elif median_amt < 1:
                # 아주 작은 값은 억 단위로 간주(드문 경우)
                amt = amt * 100

        df_norm['거래대금'] = amt

    # 기타 숫자 컬럼 정리
    if '거래량' in df_norm.columns:
        try:
            df_norm['거래량'] = pd.to_numeric(df_norm['거래량'].astype(str).str.replace(',', '', regex=False), errors='coerce').fillna(0)
        except Exception:
            pass
    if '등락률' in df_norm.columns:
        try:
            df_norm['등락률'] = pd.to_numeric(df_norm['등락률'].astype(str).str.replace('%', '', regex=False).str.replace('+', '', regex=False), errors='coerce')
        except Exception:
            pass
    if '외국인비율' in df_norm.columns:
        try:
            df_norm['외국인비율'] = pd.to_numeric(df_norm['외국인비율'].astype(str).str.replace('%', '', regex=False).str.replace('+', '', regex=False), errors='coerce')
        except Exception:
            pass

    try:
        df_norm['_units_normalized'] = True
    except Exception:
        pass

    # 거래대금 표준화 계산: 기본값은 소스 제공값을 사용하되
    # 환경변수 `UNIVERSE_TRADE_VALUE_METHOD`로 재계산 여부를 제어합니다.
    # 지원값:
    # - 'reported' (기본): 수집된 `거래대금` 값을 그대로 사용
    # - 'volume_price' 또는 'calc': `거래량 * 현재가 / 1_000_000`으로 계산
    try:
        method = str(os.getenv('UNIVERSE_TRADE_VALUE_METHOD', 'reported')).strip().lower()
    except Exception:
        method = 'reported'
    try:
        if '거래대금' in df_norm.columns and method in ('volume_price', 'calc', 'volume*price'):
            price = pd.to_numeric(df_norm.get('현재가', pd.Series([0] * len(df_norm))), errors='coerce').fillna(0)
            vol = pd.to_numeric(df_norm.get('거래량', pd.Series([0] * len(df_norm))), errors='coerce').fillna(0)
            # 가격(원) * 거래량 -> 백만원 단위로 변환
            df_norm['거래대금_standard'] = (vol * price) / 1_000_000.0
        else:
            # reported(또는 컬럼 없음)인 경우 원본 정규화값을 그대로 사용
            if '거래대금' in df_norm.columns:
                df_norm['거래대금_standard'] = df_norm['거래대금']
            else:
                df_norm['거래대금_standard'] = pd.Series([0] * len(df_norm))
    except Exception:
        try:
            df_norm['거래대금_standard'] = df_norm['거래대금'] if '거래대금' in df_norm.columns else pd.Series([0] * len(df_norm))
        except Exception:
            pass

    if save_canonical:
        try:
            canonical_path = os.path.join(DB_DIR, 'all_stocks_canonical.parquet')
            df_norm.to_parquet(canonical_path, index=True)
            logger.info(f"Canonical Parquet saved: {Path(canonical_path).resolve()}")
        except Exception as e:
            logger.warning(f"Canonical save failed: {e}")

    return df_norm


def _log_etf_mix(df, label=''):
    """유니버스 데이터의 ETF/비ETF 구성 비중을 로그로 남긴다."""
    if '종목명' not in df.columns:
        return

    total = len(df)
    if total == 0:
        logger.info("Universe ETF 구성[%s]: total=0", label)
        return

    etf_mask = _compute_etf_mask(df)
    etf_count = int(etf_mask.sum())
    non_etf_count = int(total - etf_count)
    etf_ratio = (etf_count / total) * 100.0
    logger.info(
        "Universe ETF 구성[%s]: total=%d, etf=%d(%.2f%%), non_etf=%d",
        label,
        total,
        etf_count,
        etf_ratio,
        non_etf_count,
    )


def _apply_etf_policy(df, policy_overrides=None):
    """
    환경변수 기반 ETF 정책을 적용한다.

    - UNIVERSE_ETF_MODE: all | exclude | only | auto (기본 all)
    - UNIVERSE_ETF_WHITELIST_CODES: auto 모드에서 유지할 ETF 코드 CSV
    - UNIVERSE_ETF_WHITELIST_NAMES: auto 모드에서 유지할 ETF 종목명 CSV

    auto 모드: ETF는 기본 제외하고 whitelist ETF만 유지.
    """
    policy_overrides = policy_overrides or {}

    # 정책 오버라이드 'mode'가 명시적으로 None일 수 있으므로
    # None 또는 빈 문자열일 경우 환경변수로 폴백하도록 처리합니다.
    override_mode = policy_overrides.get('mode', None)
    if override_mode is None or (isinstance(override_mode, str) and override_mode.strip() == ''):
        env_mode = os.getenv('UNIVERSE_ETF_MODE', 'all')
        mode = str(env_mode).strip().lower() if env_mode is not None else 'all'
    else:
        mode = str(override_mode).strip().lower()

    if not mode:
        mode = 'all'

    if mode not in {'all', 'exclude', 'only', 'auto'}:
        logger.warning("알 수 없는 UNIVERSE_ETF_MODE=%s, all로 처리합니다.", mode)
        mode = 'all'

    if '종목명' not in df.columns:
        logger.warning("ETF 정책 적용 건너뜀: 종목명 컬럼이 없습니다.")
        return df

    out = df.copy()
    etf_mask = _compute_etf_mask(out)

    # 디버그: 호출시 전달된 모드/파라미터와 DataFrame 샘플 정보 로깅
    try:
        sample_codes = out['종목코드'].astype(str).head(10).tolist() if '종목코드' in out.columns else None
    except Exception:
        sample_codes = None
    try:
        logger.info(
            "DEBUG _apply_etf_policy 호출: mode=%s, policy_overrides=%s, df_rows=%d, df_cols=%d, sample_codes=%s",
            mode,
            policy_overrides,
            len(out),
            len(out.columns),
            sample_codes,
        )
    except Exception:
        # 로깅 실패 시 무시
        pass

    if mode == 'all':
        logger.info("ETF 정책(all): ETF 포함 유지 (ETF %d개)", int(etf_mask.sum()))
        _log_etf_mix(out, label='all')
        return out

    if mode == 'exclude':
        filtered = out.loc[~etf_mask].copy()
        logger.info("ETF 정책(exclude): ETF %d개 제외, 결과 %d개", int(etf_mask.sum()), len(filtered))
        _log_etf_mix(filtered, label='exclude')
        return filtered

    if mode == 'only':
        filtered = out.loc[etf_mask].copy()
        logger.info("ETF 정책(only): ETF %d개만 유지", len(filtered))
        _log_etf_mix(filtered, label='only')
        return filtered

    # mode == auto
    # whitelist override가 None 또는 빈 문자열이면 .env 값으로 폴백
    override_codes = policy_overrides.get('whitelist_codes', None)
    if override_codes is None or (isinstance(override_codes, str) and override_codes.strip() == ''):
        env_codes = os.getenv('UNIVERSE_ETF_WHITELIST_CODES', '')
        whitelist_codes = _parse_env_csv_set(env_codes)
    else:
        whitelist_codes = _parse_env_csv_set(override_codes)

    override_names = policy_overrides.get('whitelist_names', None)
    if override_names is None or (isinstance(override_names, str) and override_names.strip() == ''):
        env_names = os.getenv('UNIVERSE_ETF_WHITELIST_NAMES', '')
        whitelist_names = _parse_env_csv_set(env_names)
    else:
        whitelist_names = _parse_env_csv_set(override_names)

    # 코드 비교는 형식(선행 0 포함/미포함) 차이로 매칭 실패가 나는 것을 방지하기 위해
    # 숫자 기반으로 정규화하여 비교합니다. (예: '001200' vs '1200' 일치)
    code_keep_mask = np.zeros(len(out), dtype=bool)
    whitelist_codes_int = set()
    if whitelist_codes:
        for c in whitelist_codes:
            digits = ''.join(ch for ch in str(c) if ch.isdigit())
            try:
                if digits:
                    whitelist_codes_int.add(int(digits))
            except Exception:
                continue

    # 디버그: 화이트리스트 파싱 결과와 캐시 내 매칭 개수 확인
    try:
        logger.info(
            "DEBUG whitelist parsed: raw=%s, normalized_int=%s, names=%s",
            whitelist_codes,
            sorted(list(whitelist_codes_int)) if whitelist_codes_int else [],
            whitelist_names,
        )
    except Exception:
        pass
    try:
        matches_int = 0
        matches_str = 0
        if '종목코드' in out.columns and whitelist_codes_int:
            out_code_digits = out['종목코드'].astype(str).str.replace(r'\D+', '', regex=True)
            out_code_ints = pd.to_numeric(out_code_digits, errors='coerce').fillna(-1).astype(int)
            matches_int = int(out_code_ints.isin(list(whitelist_codes_int)).sum())
        if '종목코드' in out.columns and whitelist_codes:
            matches_str = int(out['종목코드'].astype(str).isin(whitelist_codes).sum())
        logger.info("DEBUG whitelist match counts: by_int=%d, by_str=%d", matches_int, matches_str)
    except Exception:
        pass

    if whitelist_codes_int and '종목코드' in out.columns:
        # df의 종목코드를 숫자형으로 정규화
        try:
            out_code_digits = out['종목코드'].astype(str).str.replace(r'\D+', '', regex=True)
            out_code_ints = pd.to_numeric(out_code_digits, errors='coerce').fillna(-1).astype(int)
            code_keep_mask = out_code_ints.isin(list(whitelist_codes_int)).to_numpy()
        except Exception:
            # 폴백: 기존 문자열 매칭 사용
            code_keep_mask = out['종목코드'].astype(str).isin(whitelist_codes).to_numpy()

    name_keep_mask = np.zeros(len(out), dtype=bool)
    if whitelist_names and '종목명' in out.columns:
        name_keep_mask = out['종목명'].astype(str).isin(whitelist_names).to_numpy()

    # upstream에서 전달된 보호 표식을 반영할 수 있도록 준비합니다.
    pre_keep_mask = None
    pre_keep_series = None
    if '_pre_whitelist' in out.columns:
        try:
            pre_keep_series = out['_pre_whitelist'].astype(bool)
            pre_keep_mask = pre_keep_series.to_numpy()
            try:
                logger.info("DEBUG _apply_etf_policy: detected _pre_whitelist, preserve_count=%d", int(pre_keep_series.sum()))
            except Exception:
                pass
        except Exception:
            pre_keep_mask = None

    keep_whitelist_mask = code_keep_mask | name_keep_mask
    if pre_keep_mask is not None:
        try:
            keep_whitelist_mask = keep_whitelist_mask | pre_keep_mask
        except Exception:
            try:
                keep_whitelist_mask = (keep_whitelist_mask.astype(bool)) | (np.array(pre_keep_mask, dtype=bool))
            except Exception:
                pass

    final_keep = (~etf_mask.to_numpy()) | keep_whitelist_mask
    filtered = out.loc[final_keep].copy()

    # Mark whitelist rows present in the filtered set
    try:
        # _is_whitelist 표시는 숫자 정규화 기반으로 처리
        if '종목코드' in filtered.columns:
            try:
                filt_code_digits = filtered['종목코드'].astype(str).str.replace(r'\D+', '', regex=True)
                filt_code_ints = pd.to_numeric(filt_code_digits, errors='coerce').fillna(-1).astype(int)
                filtered['_is_whitelist'] = filt_code_ints.isin(list(whitelist_codes_int))
            except Exception:
                filtered['_is_whitelist'] = filtered['종목코드'].astype(str).isin(whitelist_codes)
        else:
            filtered['_is_whitelist'] = False
        if whitelist_names and '종목명' in filtered.columns:
            filtered['_is_whitelist'] = filtered['_is_whitelist'] | filtered['종목명'].astype(str).isin(whitelist_names)

        # upstream에서 보호 표식이 있으면 _is_whitelist에 합칩니다.
        if pre_keep_series is not None:
            try:
                pre_filtered = pre_keep_series.loc[filtered.index].astype(bool)
                filtered['_is_whitelist'] = filtered['_is_whitelist'].astype(bool) | pre_filtered.values
            except Exception:
                try:
                    filtered['_is_whitelist'] = filtered['_is_whitelist'].astype(bool) | pre_keep_mask
                except Exception:
                    pass
    except Exception:
        filtered['_is_whitelist'] = False

    # If some whitelist codes/names were removed by prior filters, try to fetch them from cache and append.
    # Compute missing sets in both string and normalized-int forms for robust matching.
    missing_codes = set()
    missing_codes_int = set()
    missing_names = set()
    if whitelist_codes and '종목코드' in filtered.columns:
        try:
            filt_code_digits = filtered['종목코드'].astype(str).str.replace(r'\D+', '', regex=True)
            filt_code_ints = pd.to_numeric(filt_code_digits, errors='coerce').dropna().astype(int).tolist()
            missing_codes_int = set(whitelist_codes_int) - set(filt_code_ints)
        except Exception:
            missing_codes_int = set()
        try:
            missing_codes = set(whitelist_codes) - set(filtered['종목코드'].astype(str))
        except Exception:
            missing_codes = set(whitelist_codes)
    if whitelist_names and '종목명' in filtered.columns:
        try:
            missing_names = set(whitelist_names) - set(filtered['종목명'].astype(str))
        except Exception:
            missing_names = set(whitelist_names)

    if missing_codes or missing_names:
        try:
            cache_df = _try_load_cache()
            appended = None
            if cache_df is not None:
                parts = []
                if (missing_codes_int or missing_codes) and '종목코드' in cache_df.columns:
                    # cache의 종목코드도 정규화하여 비교 (숫자 우선, 실패 시 문자열 매칭)
                    try:
                        cache_code_digits = cache_df['종목코드'].astype(str).str.replace(r'\D+', '', regex=True)
                        cache_code_ints = pd.to_numeric(cache_code_digits, errors='coerce').fillna(-1).astype(int)
                        part = None
                        if missing_codes_int:
                            part = cache_df[cache_code_ints.isin(list(missing_codes_int))].copy()
                        if (part is None or part.empty) and missing_codes:
                            part = cache_df[cache_df['종목코드'].astype(str).isin(missing_codes)].copy()
                        if part is not None and not part.empty:
                            parts.append(part)
                    except Exception:
                        try:
                            part = cache_df[cache_df['종목코드'].astype(str).isin(missing_codes)].copy()
                            if not part.empty:
                                parts.append(part)
                        except Exception:
                            pass
                if missing_names and '종목명' in cache_df.columns:
                    part = cache_df[cache_df['종목명'].astype(str).isin(missing_names)].copy()
                    if not part.empty:
                        parts.append(part)
                if parts:
                    appended = pd.concat(parts, ignore_index=True, sort=False)
                    # ensure columns align with filtered
                    for col in filtered.columns:
                        if col not in appended.columns:
                            appended[col] = ''
                    appended = appended[filtered.columns]
                    appended['_is_whitelist'] = True
                    filtered = pd.concat([filtered, appended], ignore_index=True, sort=False)
                    # dedupe by code if present
                    if '종목코드' in filtered.columns:
                        filtered = filtered.drop_duplicates(subset=['종목코드'], keep='first').reset_index(drop=True)
        except Exception as e:
            logger.warning(f"auto whitelist 병합 중 오류 발생: {e}")

    kept_etf = 0
    try:
        # recompute kept_etf based on ETF mask and final filtered set
        if '종목코드' in filtered.columns:
            etf_mask_filtered = _compute_etf_mask(filtered)
            kept_etf = int(etf_mask_filtered.sum())
        else:
            kept_etf = 0
    except Exception:
        kept_etf = 0

    logger.info(
        "ETF 정책(auto): ETF %d개 중 whitelist %d개 유지, 결과 %d개",
        int(etf_mask.sum()),
        kept_etf,
        len(filtered),
    )
    _log_etf_mix(filtered, label='auto')
    return filtered


def universe_cache_exists(db_dir=None, max_age_days=None, strategy_name=None):
    """
    all_stocks_kiwoom.parquet 캐시 또는 전략 DB의 `universe` 테이블 존재/신선도 확인.

    Returns: (exists_bool, days_old_or_None, modified_datetime_or_None)
    - parquet 파일이 있으면 수정시간으로 days_old 계산.
    - parquet가 없고 strategy_name이 주어지면 DB의 `universe` 테이블 존재 여부를 확인.
    - max_age_days가 주어지면 exists_bool은 days_old < max_age_days 조건을 사용해 판단.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from util.time_helper import get_korea_time
    from util.db_helper import check_table_exist

    db_dir = db_dir or DB_DIR
    cache_file = os.path.join(db_dir, 'all_stocks_kiwoom.parquet')

    if os.path.exists(cache_file):
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(cache_file), tz=ZoneInfo("Asia/Seoul"))
            try:
                days_old = (get_korea_time().date() - mtime.date()).days
            except Exception:
                days_old = None
            if max_age_days is None:
                return True, days_old, mtime
            return (days_old is not None and days_old < int(max_age_days)), days_old, mtime
        except Exception:
            return True, None, None

    # parquet 파일이 없으면 DB 테이블 존재 여부로 판단 (전략 DB가 주어진 경우)
    if strategy_name:
        try:
            table_exists = check_table_exist(strategy_name, 'universe')
            if table_exists:
                return True, None, None
        except Exception:
            pass

    return False, None, None

# 기본 필드 아이디(네이버 필드 id 목록이 변경되었을 때의 폴백)
# 실제 네이버 필드 id는 사이트 변경에 따라 달라질 수 있으므로 최소한의 주요 항목을 포함
DEFAULT_FIELD_IDS = ['open', 'high', 'low', 'market_sum', 'trd_amt', 'cur_prc']


def cache_daily_data(kiwoom_client):
    """
    매일 장 종료 후 키움 API로 당일 데이터를 수집하여 캐싱하는 함수
    (Universe 재구성과 별개로 데이터만 갱신)
    
    Args:
        kiwoom_client: Kiwoom API 클라이언트 인스턴스
    
    Note:
        - Universe 재구성 (30일 주기): 종목 리스트 변경
        - 데이터 캐싱 (매일): 기존 종목들의 최신 데이터만 갱신
    """
    logger.info("📊 키움 API로 당일 데이터 캐싱 시작...")
    
    try:
        # 캐시를 읽지 않고 새로 생성하되, 수집 후에는 저장 (use_cache=False, save_cache=True)
        df = fetch_all_stocks_from_kiwoom(kiwoom_client, use_cache=False, save_cache=True)
        logger.info(f"✅ 당일 데이터 캐싱 완료: {len(df)}개 종목")
        return df
    except Exception as e:
        logger.error(f"❌ 데이터 캐싱 실패: {e}")
        raise


def fetch_all_stocks_from_kiwoom(kiwoom_client, use_cache=True, save_cache=True, cache_file='all_stocks_kiwoom.parquet'):
    """
    키움 API를 활용하여 전체 종목 리스트를 수집하는 함수
    (유니버스 생성용 기초 데이터)
    
    Args:
        kiwoom_client: Kiwoom API 클라이언트 인스턴스
        use_cache: 캐시 읽기 여부 (기본값: True)
        save_cache: 캐시 저장 여부 (기본값: True)
        cache_file: 캐시 파일 경로 (기본값: 'all_stocks_kiwoom.parquet')
    
    Returns:
        DataFrame: 전체 종목 정보가 담긴 데이터프레임
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import time
    
    today_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    
    # 캐시 파일 확인 (30일 이내 파일 사용 가능)
    cache_path = cache_file if os.path.isabs(cache_file) else os.path.join(DB_DIR, cache_file)
    if use_cache and os.path.exists(cache_path):
        file_mod_time = datetime.fromtimestamp(os.path.getmtime(cache_path), tz=ZoneInfo("Asia/Seoul"))
        file_date_str = file_mod_time.strftime("%Y%m%d")
        days_old = (datetime.now(ZoneInfo("Asia/Seoul")).date() - file_mod_time.date()).days
        
        # 30일 이내 캐시 파일은 사용 가능
        if days_old < 30:
            logger.info(f"캐시 파일 사용: {Path(cache_path).resolve()} ({days_old}일 전 데이터, 30일 이내)")
            try:
                return pd.read_parquet(cache_path)
            except Exception as e:
                logger.warning(f"캐시 Parquet 읽기 실패: {e}. API로 새로 조회합니다.")
        else:
            logger.warning(f"캐시 파일이 너무 오래됨: {days_old}일 전. API로 새로 조회합니다.")
    
    logger.info("키움 API로 종목 정보를 수집합니다...")
    
    # 1단계: 전체 종목 리스트 가져오기 (ka10099)
    logger.info("1/2: 종목 리스트 조회 중 (ka10099)...")
    kospi_list = kiwoom_client.get_code_list_by_market("0")  # 코스피
    kosdaq_list = kiwoom_client.get_code_list_by_market("10")  # 코스닥
    
    all_stocks = []
    for stock in kospi_list:
        all_stocks.append({**stock, 'market': '코스피'})
    for stock in kosdaq_list:
        all_stocks.append({**stock, 'market': '코스닥'})
    
    logger.info(f"총 {len(all_stocks)}개 종목 발견 (코스피: {len(kospi_list)}, 코스닥: {len(kosdaq_list)})")
    
    # 2단계: 각 종목의 상세 정보 가져오기 (ka10001)
    logger.info("2/2: 종목별 상세 정보 조회 중 (ka10001)... (시간이 소요될 수 있습니다)")
    
    # Rate limit 설정 (환경변수에서 읽기, 없으면 기본값 사용)
    # 모의투자는 rate limit이 더 엄격 (0.2초), 실전투자는 0.1초
    sleep_interval = float(
        os.getenv(
            'KIWOOM_API_SLEEP_MOCK' if kiwoom_client.mock else 'KIWOOM_API_SLEEP_REAL',
            '0.2' if kiwoom_client.mock else '0.1'
        )
    )
    logger.info(f"API 호출 간격: {sleep_interval}초 ({'모의투자' if kiwoom_client.mock else '실전투자'} 모드)")
    
    stock_data = []
    failed_count = 0
    
    for idx, stock in enumerate(all_stocks, 1):
        if idx % 100 == 0:
            logger.info(f"진행 상황: {idx}/{len(all_stocks)}...")
        
        info = kiwoom_client.get_stock_info(stock['code'])
        
        if info:
            try:
                # 키움 API: 시가총액은 억원 단위, 거래대금은 백만원 단위
                stock_data.append({
                    '종목코드': stock['code'],
                    '종목명': info.get('name', stock['name']),
                    '시장구분': stock['market'],
                    '현재가': int(info.get('cur_prc', 0)),
                    '거래량': int(info.get('trde_qty', 0)),
                    '거래대금': int(info.get('trde_amt', 0)),  # 백만원 단위 (그대로 사용)
                    '시가총액': int(info.get('mrkt_cap', 0)) * 100,  # 억원 → 백만원 (×100)
                    '등락률': float(info.get('flu_rt', 0)),
                    '외국인비율': float(info.get('for_exh_rt', 0)),
                    '상장주식수': int(info.get('list_cnt', 0)),
                })
            except (ValueError, TypeError) as e:
                logger.warning(f"종목 {stock['code']} 데이터 파싱 실패: {e}")
                failed_count += 1
        else:
            failed_count += 1
        
        # Rate limit 방지
        time.sleep(sleep_interval)
    
    logger.info(f"데이터 수집 완료: {len(stock_data)}개 성공, {failed_count}개 실패")
    
    # DataFrame 생성
    df = pd.DataFrame(stock_data)
    # --- master_list DB에 코드/종목명 저장(캐시 초기화용) ---
    try:
        from util.db_helper import upsert_stock_name
        for row in stock_data:
            try:
                code = str(row.get('종목코드') or row.get('종목_code') or row.get('code'))
                name = str(row.get('종목명') or row.get('종목명', None) or row.get('종목_name') or row.get('name') or '')
                if code and name:
                    upsert_stock_name('master_list', code, name)
            except Exception:
                continue
    except Exception:
        # DB 연동 실패 시에도 전체 기능은 유지되도록 무시
        logger.debug("master_list 업서트 스텝 건너뜀 (DB 오류)")
    
    # 캐시 저장
    if save_cache:
        try:
            # Prefer Parquet for compactness and speed
            df.to_parquet(cache_path, index=True)
            logger.info(f"캐시 파일 저장: {Path(cache_path).resolve()}")
        except Exception as e:
            logger.error(f"캐시 Parquet 저장 실패: {e}")
        # canonical 저장 옵션 활성화 시 정규화된 canonical 파일도 저장
        try:
            save_canonical_env = str(os.getenv('SAVE_CANONICAL', '1')).lower() in ('1', 'true', 'yes')
            if save_canonical_env:
                try:
                    _normalize_units(df, save_canonical=True)
                except Exception as e:
                    logger.warning(f"캐논컬 저장 시도 중 오류: {e}")
        except Exception:
            pass
    
    return df


def is_market_hours():
    """
    장시간인지 확인하는 함수 (크롤링 가능 시간 체크용)
    평일 09:00 ~ 15:30 사이를 장시간으로 판단 (휴장일 제외)
    
    주의: 15:30까지 포함하는 이유는 동시호가 시간대에도 크롤링 가능하기 때문
          실제 매매는 15:20까지만 가능 (check_transaction_open 참고)
    """
    from util.time_helper import is_market_closed_day
    
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    
    # 휴장일 체크 (주말 + 공휴일)
    if is_market_closed_day():
        return False
    
    # 장시작: 09:00, 장마감: 15:30 (공식 마감 시간)
    market_open = datetime_time(9, 0)
    market_close = datetime_time(15, 30)
    
    current_time = now.time()
    
    return market_open <= current_time <= market_close


def execute_crawler(output_file='all_stocks_naver.parquet'):
    # KOSPI, KOSDAQ 종목을 하나로 합치는데 사용할 변수
    df_total = []

    # CODES에 담긴 KOSPI, KOSDAQ 종목 모두를 크롤링하기 위해 for문을 사용
    for code in CODES:

        # 전체 페이지 개수를 가져오기 위한 코드 (마켓별로 `code` 사용)
        # lazy import BeautifulSoup to avoid requiring bs4 unless crawling
        from bs4 import BeautifulSoup
        res = requests.get(BASE_URL + str(code))
        page_soup = BeautifulSoup(res.text, 'lxml')

        # '맨뒤'에 해당하는 태그를 기준으로 전체 페이지 개수 추출하기
        total_page_elem = page_soup.select_one('td.pgRR > a')
        if total_page_elem is None:
            logger.warning(f"전체 페이지 정보를 찾을 수 없어 market={code}을(를) 1페이지만 처리합니다.")
            total_page_num = 1
        else:
            try:
                total_page_num = int(total_page_elem.get('href').split('=')[-1])
            except Exception as e:
                logger.warning(f"전체 페이지 수 파싱 실패 (href={total_page_elem.get('href')}): {e}. 1로 처리합니다.")
                total_page_num = 1

        # 조회할 수 있는 항목정보들 추출
        ipt_html = page_soup.select_one('div.subcnt_sise_item_top')

        # 페이지에서 조회할 항목정보들 추출 (로컬 변수로 관리)
        if ipt_html is None:
            logger.warning(f"항목 정보(div.subcnt_sise_item_top)를 찾을 수 없습니다. 기본 필드로 폴백합니다. (market={code})")
            fields = DEFAULT_FIELD_IDS
        else:
            fields = [item.get('value') for item in ipt_html.select('input')]

        # page마다 존재하는 모든 종목들의 항목정보를 크롤링해서 result에 저장
        result = []
        for page in range(1, total_page_num + 1):
            try:
                page_df = crawler(code, str(page), fields)
                if page_df is not None and not page_df.empty:
                    result.append(page_df)
            except Exception as e:
                logger.warning(f"페이지 크롤링 실패 (market={code}, page={page}): {e}")

        # 전체 페이지를 저장한 result를 하나의 데이터프레임으로 만듬
        if result:
            df = pd.concat(result, axis=0, ignore_index=True)
        else:
            df = pd.DataFrame()
        
        # 시장구분 컬럼 추가 (0=코스피, 1=코스닥)
        df['시장구분'] = '코스피' if code == 0 else '코스닥'

        # 변수 df는 KOSPI, KOSDAQ별로 크롤링한 종목 정보이고 이를 하나로 합치기 위해 df_total에 추가
        df_total.append(df)

    # df_total를 하나의 데이터프레임으로 만듬
    df_total = pd.concat(df_total)

    # 합친 데이터프레임의 index 번호를 새로 매김
    df_total.reset_index(inplace=True, drop=True)

    # 전체 크롤링 결과를 Parquet로 저장
    out_path = output_file if os.path.isabs(output_file) else os.path.join(DB_DIR, output_file)
    try:
        df_total.to_parquet(out_path, index=True)
        try:
            logger.info(f"크롤링 결과 저장: {Path(out_path).resolve()}")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"크롤링 결과 Parquet 저장 실패: {e}")

    # 크롤링 결과를 반환
    return df_total


def crawler(code, page, fields):
    """Parse a single page using explicit `fields` (stateless).

    `fields` is now required to make the function pure and deterministic.
    """

    # Naver finance에 전달할 값들 세팅(요청을 보낼 때는 menu, fieldIds, returnUrl을 지정해서 보내야 함)
    data = {'menu': 'market_sum',
            'fieldIds': fields,
            'returnUrl': BASE_URL + str(code) + "&page=" + str(page)}

    # lazy import BeautifulSoup only when crawler runs
    from bs4 import BeautifulSoup
    # 네이버로 요청을 전달(post방식)
    res = requests.post('https://finance.naver.com/sise/field_submit.nhn', data=data)

    page_soup = BeautifulSoup(res.text, 'lxml')

    # 크롤링할 table의 html 가져오는 코드(크롤링 대상 요소의 클래스는 브라우저에서 확인)
    table_html = page_soup.select_one('div.box_type_l')

    # column명을 가공
    header_data = [item.get_text().strip() for item in table_html.select('thead th')][1:-1]

    # 종목코드 추출 (a.title 태그의 href에서 추출)
    code_data = []
    for item in table_html.select('a.tltle'):
        href = item.get('href', '')
        if 'code=' in href:
            code = href.split('code=')[1].split('&')[0]
            code_data.append(code)
        else:
            code_data.append('')

    # 종목명 + 수치 추출 (a.title = 종목명, td.number = 기타 수치)
    inner_data = [item.get_text().strip() for item in table_html.find_all(lambda x:
                                                                          (x.name == 'a' and
                                                                           'tltle' in x.get('class', [])) or
                                                                          (x.name == 'td' and
                                                                           'number' in x.get('class', []))
                                                                          )]

    # page마다 있는 종목의 순번 가져오기
    no_data = [item.get_text().strip() for item in table_html.select('td.no')]
    number_data = np.array(inner_data)

    # 가로 x 세로 크기에 맞게 행렬화
    number_data.resize(len(no_data), len(header_data))

    # 한 페이지에서 얻은 정보를 모아 DataFrame로 만들어 반환
    df = pd.DataFrame(data=number_data, columns=header_data)
    
    # 종목코드 컬럼 추가
    if len(code_data) == len(df):
        df.insert(0, '종목코드', code_data)
    
    return df


def get_universe(
    kiwoom_client=None,
    use_kiwoom_api=False,
    max_codes=200,
    universe_output_file: Optional[str] = 'universe.parquet',
    etf_policy_overrides: Optional[dict] = None,
):
    """
    유니버스를 생성하는 함수 (스마트 캐싱 전략)
    
    Args:
        kiwoom_client: Kiwoom API 클라이언트 (장 종료 후 자동으로 사용)
        use_kiwoom_api: 키움 API 강제 사용 (기본값: False, 자동 판단)
        max_codes: 유니버스 최대 종목 수 (기본값: 200)
    
    Returns:
        list: 종목명 리스트
    
    동작 방식 (스마트 전략):
    1. 장 종료 후 (15:30 이후) → 키움 API로 당일 데이터 수집하여 캐싱
    2. 장 중 → 네이버 크롤링 시도 (빠름) → 실패 시 캐시 사용
    3. 수동으로 use_kiwoom_api=True 지정 시 → 항상 API 사용
    """
    # 장 종료 후면 키움 API로 당일 데이터 갱신 (kiwoom_client가 있는 경우)
    if kiwoom_client and (use_kiwoom_api or check_transaction_closed()):
        mode = "강제 모드" if use_kiwoom_api else "장 종료 후 자동 갱신"
        logger.info(f"키움 API로 유니버스 생성을 시도합니다... ({mode})")
        try:
            df = fetch_all_stocks_from_kiwoom(kiwoom_client)
            logger.info(f"✅ 키움 API로 {len(df)}개 종목 정보 획득 및 캐싱 완료")
            universe = _filter_and_create_universe(
                df,
                max_codes=max_codes,
                universe_output_file=universe_output_file,
                etf_policy_overrides=etf_policy_overrides,
            )
            try:
                del df
                gc.collect()
            except Exception:
                pass
            return universe
        except Exception as e:
            logger.error(f"키움 API 유니버스 생성 실패: {e}")
            if use_kiwoom_api:  # 강제 모드였다면 fallback
                logger.info("네이버 크롤링으로 fallback합니다...")
            else:  # 장 종료 후 자동 모드였다면 캐시 우선 시도
                logger.info("캐시 파일 확인합니다...")
                cached_df = _try_load_cache()
                if cached_df is not None:
                    logger.info(f"✅ 캐시 파일 사용: {len(cached_df)}개 종목")
                    universe = _filter_and_create_universe(
                        cached_df,
                        max_codes=max_codes,
                        universe_output_file=universe_output_file,
                        etf_policy_overrides=etf_policy_overrides,
                    )
                    try:
                        del cached_df
                        gc.collect()
                    except Exception:
                        pass
                    return universe
            # 아래 네이버 크롤링 로직으로 계속 진행
    all_stock_cache_file = 'all_stocks_naver.parquet'
    today_str = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    all_stock_cache_path = all_stock_cache_file if os.path.isabs(all_stock_cache_file) else os.path.join(DB_DIR, all_stock_cache_file)
    
    # 오늘 날짜 파일이 있는지 확인
    file_is_today = False
    if os.path.exists(all_stock_cache_path):
        file_mod_time = datetime.fromtimestamp(os.path.getmtime(all_stock_cache_path), tz=ZoneInfo("Asia/Seoul"))
        file_date_str = file_mod_time.strftime("%Y%m%d")
        file_is_today = (file_date_str == today_str)
        
        if file_is_today:
            logger.info(f"오늘 생성된 {all_stock_cache_path} 파일을 사용합니다. (생성 시간: {file_mod_time.strftime('%H:%M:%S')})")
            print(f"오늘 생성된 {all_stock_cache_path} 파일을 사용합니다.")
            try:
                df = pd.read_parquet(all_stock_cache_path)
                # 읽어온 Parquet에 필수 컬럼이 없는 경우 NaN 컬럼으로 보완
                required_cols = ['거래량', '거래대금', '시가총액', '등락률', '외국인비율', '종목명', '종목코드', '시장구분']
                for rc in required_cols:
                    if rc not in df.columns:
                        df[rc] = np.nan
                # 네이버 캐시로부터 읽은 데이터는 정규화 후(옵션) canonical로 저장할 수 있습니다.
                try:
                    save_canonical = str(os.getenv('SAVE_CANONICAL', '1')).lower() in ('1', 'true', 'yes')
                    if '_units_normalized' not in df.columns:
                        df = _normalize_units(df, source_hint='naver', save_canonical=save_canonical)
                except Exception as e:
                    logger.warning(f"네이버 캐시 정규화 실패: {e}")
            except Exception as e:
                logger.error(f"Parquet 파일 읽기 실패: {e}. 크롤링을 시도합니다.")
                file_is_today = False  # 파일 읽기 실패하면 크롤링 시도
    
    # 크롤링 스킵: 평일 08:00-09:00에는 네이버 크롤링 데이터가 신뢰 불가
    from util.time_helper import is_market_closed_day
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    if not use_kiwoom_api and (not is_market_closed_day()):
        if datetime_time(8, 0) <= now_kst.time() < datetime_time(9, 0):
            logger.info("평일 08:00-09:00: 네이버 크롤링을 스킵합니다. 캐시 사용을 시도합니다.")
            cached_df = _try_load_cache()
            if cached_df is not None:
                logger.info(f"✅ 캐시 파일 사용: {len(cached_df)}개 종목 (크롤링 스킵)")
                df = cached_df
                file_is_today = True
            else:
                raise Exception("평일 08:00-09:00 이므로 크롤링을 스킵합니다. 사용 가능한 캐시가 없습니다.")

    # 오늘 파일이 없거나 읽기 실패 시 크롤링 시도
    if not file_is_today:
        logger.info(f"크롤링을 실행합니다. (파일 존재: {os.path.exists(all_stock_cache_path)}, 오늘 파일: {file_is_today}, path={Path(all_stock_cache_path).resolve()})")
        print(f"크롤링을 실행합니다...")
        
        try:
            df = execute_crawler(all_stock_cache_file)
            # 크롤링으로 수집한 결과는 정규화(및 SAVE_CANONICAL 옵션에 따라 canonical 저장)를 수행합니다.
            try:
                save_canonical = str(os.getenv('SAVE_CANONICAL', '1')).lower() in ('1', 'true', 'yes')
                if '_units_normalized' not in df.columns:
                    df = _normalize_units(df, source_hint='naver', save_canonical=save_canonical)
            except Exception as e:
                logger.warning(f"네이버 크롤링 정규화 실패: {e}")
        except Exception as e:
            # 캐시 파일 사용 (네이버 + 키움 API 캐시 모두 시도)
            logger.warning(f"크롤링 실패: {e}. 캐시 파일을 확인합니다...")
            cached_df = _try_load_cache()
            if cached_df is not None:
                logger.info(f"✅ 캐시 파일 사용: {len(cached_df)}개 종목")
                df = cached_df
            else:
                logger.error(f"크롤링 실패이고 사용 가능한 캐시도 없습니다.")
                raise Exception(f"크롤링 실패이고 사용 가능한 캐시도 없습니다: {e}")

    universe = _filter_and_create_universe(
        df,
        max_codes=max_codes,
        universe_output_file=universe_output_file,
        etf_policy_overrides=etf_policy_overrides,
    )
    try:
        del df
        gc.collect()
    except Exception:
        pass
    return universe


def _try_load_cache():
    """
    캐시 파일을 로드하는 내부 함수 (우선순위: 키움 API 캐시 → 네이버 크롤링 캐시)
    
    Returns:
        DataFrame or None: 캐시 데이터 또는 None (실패 시)
    """
    cache_files = [
        'all_stocks_kiwoom.parquet',  # 키움 API 전체 종목 (우선)
        'all_stocks_naver.parquet'     # 네이버 크롤링 전체 종목
    ]

    for cache_file in cache_files:
        cache_path = cache_file if os.path.isabs(cache_file) else os.path.join(DB_DIR, cache_file)
        if os.path.exists(cache_path):
            try:
                try:
                    df = pd.read_parquet(cache_path)
                except Exception as e:
                    logger.warning(f"Parquet 읽기 실패: {e}")
                    raise
                # 파일 수정시간 조회는 실패할 수 있으므로 개별로 처리
                try:
                    file_mod_time = datetime.fromtimestamp(
                        os.path.getmtime(cache_path), 
                        tz=ZoneInfo("Asia/Seoul")
                    )
                    logger.info(f"캐시 파일 발견: {cache_file} (생성: {file_mod_time.strftime('%Y-%m-%d %H:%M:%S')})")
                except Exception:
                    logger.info(f"캐시 파일 읽음: {cache_file} (수정시간 없음)")
                    logger.info(f"⚠️  캐시 파일 사용: {cache_file}")
                    # 보장: 필터링에서 기대하는 컬럼들이 없으면 NaN 컬럼을 추가하여 KeyError 방지
                    required_cols = ['거래량', '거래대금', '시가총액', '등락률', '외국인비율', '종목명', '종목코드', '시장구분']
                    for rc in required_cols:
                        if rc not in df.columns:
                            df[rc] = np.nan
                    # 캐시에서 읽은 데이터는 단위 정규화 옵션에 따라 canonical로 저장될 수 있음
                    try:
                        save_canonical = str(os.getenv('SAVE_CANONICAL', '1')).lower() in ('1', 'true', 'yes')
                        df = _normalize_units(df, save_canonical=save_canonical)
                    except Exception as e:
                        logger.warning(f"캐시 정규화 실패: {e}")
                    return df
            except Exception as e:
                logger.warning(f"{cache_path} 읽기 실패: {e}")
                continue
    
    return None


def _filter_and_create_universe(
    df,
    kiwoom_client=None,
    max_codes=200,
    universe_output_file: Optional[str] = 'universe.parquet',
    etf_policy_overrides: Optional[dict] = None,
):
    """
    DataFrame을 받아서 필터링하고 유니버스를 생성하는 내부 함수
    네이버 크롤링과 키움 API 모두에서 공통으로 사용

    Args:
        df: 필터링할 종목 데이터 DataFrame
        kiwoom_client: Kiwoom API 클라이언트 (보유/주문 종목 병합용)
        max_codes: 선택할 유니버스 종목 수 (기본 200, 환경변수 REALTIME_MAX_CODES + POLLING_MAX_CODES로 오버라이드 가능)
        universe_output_file: Parquet 출력 파일 경로
        etf_policy_overrides: ETF 정책 오버라이드 딕셔너리
    """
    # 데이터 정제
    mapping = {',': '', 'N/A': '0', '%': ''}
    df.replace(mapping, regex=True, inplace=True)

    # 단위 정규화: 서로 다른 소스(네이버/키움)에서 온 수치 단위를 백만원 기준으로 통일
    try:
        df = _normalize_units(df)
    except Exception as e:
        logger.warning(f"단위 정규화 실패: {e}. 기존 데이터로 계속 진행합니다.")

    # 사용할 column들 설정 (RSI 전략에 최적화)
    cols = ['거래량', '거래대금', '시가총액', '등락률', '외국인비율']

    # column들을 숫자타입으로 변환(Naver Finance를 크롤링해온 데이터는 str 형태)
    for col in cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    
    # NaN이 생긴 행 제거
    df = df.dropna(subset=cols)
    
    # 음수 등락률 절대값 처리 필요 (등락률은 이미 숫자)
    if len(df) == 0:
        logger.warning("필터링 후 데이터가 없습니다.")
        return []
    
    # 종목코드가 있는 경우 모의투자 제한 종목 제외
    if '종목코드' in df.columns:
        before_count = len(df)
        df = df[~df['종목코드'].isin(MOCK_TRADE_BLACKLIST_CODES)]
        removed = before_count - len(df)
        if removed > 0:
            logger.info(f"모의투자 제한 종목 {removed}개 제외")

    # 수동 제외 리스트 적용 (환경변수 기반)
    # - UNIVERSE_EXCLUDE_NAMES: 종목명 부분매치로 제외할 문자열 CSV
    # - UNIVERSE_EXCLUDE_CODES: 정확히 제외할 종목코드 CSV
    try:
        exclude_names = _parse_env_csv_set(os.getenv('UNIVERSE_EXCLUDE_NAMES', ''))
        exclude_codes = _parse_env_csv_set(os.getenv('UNIVERSE_EXCLUDE_CODES', ''))
    except Exception:
        exclude_names = set()
        exclude_codes = set()

    if exclude_names or exclude_codes:
        before_exclude = len(df)
        if exclude_codes and '종목코드' in df.columns:
            df = df.loc[~df['종목코드'].astype(str).isin(exclude_codes)]
        if exclude_names and '종목명' in df.columns:
            # 부분일치(대소문자 무관)로 제외
            try:
                name_series = df['종목명'].astype(str).str.lower()
                mask = pd.Series([False] * len(df), index=df.index)
                for ex in exclude_names:
                    ex_low = ex.lower()
                    mask = mask | name_series.str.contains(ex_low, na=False)
                df = df.loc[~mask]
            except Exception as e:
                logger.warning(f"유니버스 제외 이름 필터 적용 실패: {e}")

        removed_excl = before_exclude - len(df)
        if removed_excl > 0:
            logger.info(
                f"환경변수 기반 유니버스 제외 적용: {removed_excl}개 종목 제외 (names={len(exclude_names)}, codes={len(exclude_codes)})"
            )

    # ===== RSI(2) 전략에 최적화된 Universe 구성 =====
    # 1. 기본 필터링: 유동성 + 적절한 시가총액 범위
    # 거래대금/시가총액 단위: 백만원

    # 선행 필터 적용 전, 환경변수 기반 화이트리스트를 계산하여 보호 마스크를 만듭니다.
    # etf_policy_overrides가 전달된 경우 우선 사용, 그렇지 않으면 .env에서 읽습니다.
    try:
        overrides = etf_policy_overrides or {}
        override_codes = overrides.get('whitelist_codes', None)
        if override_codes is None or (isinstance(override_codes, str) and override_codes.strip() == ''):
            env_codes = os.getenv('UNIVERSE_ETF_WHITELIST_CODES', '')
            whitelist_codes = _parse_env_csv_set(env_codes)
        else:
            whitelist_codes = _parse_env_csv_set(override_codes)

        override_names = overrides.get('whitelist_names', None)
        if override_names is None or (isinstance(override_names, str) and override_names.strip() == ''):
            env_names = os.getenv('UNIVERSE_ETF_WHITELIST_NAMES', '')
            whitelist_names = _parse_env_csv_set(env_names)
        else:
            whitelist_names = _parse_env_csv_set(override_names)
    except Exception:
        whitelist_codes = set()
        whitelist_names = set()

    whitelist_codes_int = set()
    if whitelist_codes:
        for c in whitelist_codes:
            digits = ''.join(ch for ch in str(c) if ch.isdigit())
            try:
                if digits:
                    whitelist_codes_int.add(int(digits))
            except Exception:
                continue

    # 기본적으로 보호 마스크는 False로 초기화
    code_protect_mask = np.zeros(len(df), dtype=bool)
    name_protect_mask = np.zeros(len(df), dtype=bool)

    if '종목코드' in df.columns and (whitelist_codes_int or whitelist_codes):
        try:
            code_digits = df['종목코드'].astype(str).str.replace(r'\D+', '', regex=True)
            code_ints = pd.to_numeric(code_digits, errors='coerce').fillna(-1).astype(int)
            if whitelist_codes_int:
                code_protect_mask = code_ints.isin(list(whitelist_codes_int)).to_numpy()
            else:
                code_protect_mask = df['종목코드'].astype(str).isin(whitelist_codes).to_numpy()
        except Exception:
            try:
                code_protect_mask = df['종목코드'].astype(str).isin(whitelist_codes).to_numpy()
            except Exception:
                code_protect_mask = np.zeros(len(df), dtype=bool)

    if whitelist_names and '종목명' in df.columns:
        try:
            name_protect_mask = df['종목명'].astype(str).isin(whitelist_names).to_numpy()
        except Exception:
            name_protect_mask = np.zeros(len(df), dtype=bool)

    whitelist_protect_mask = code_protect_mask | name_protect_mask
    try:
        preserved_by_whitelist = int(np.sum(whitelist_protect_mask))
        logger.info("DEBUG pre-filter whitelist protect: preserved=%d", preserved_by_whitelist)
    except Exception:
        pass

    # _apply_etf_policy 호출 시 upstream에서 보호한 항목을 인식할 수 있도록
    # 표식 컬럼을 남겨둡니다. (이 컬럼은 이후 안전하게 삭제됩니다)
    try:
        df['_pre_whitelist'] = whitelist_protect_mask
    except Exception:
        try:
            df.loc[:, '_pre_whitelist'] = whitelist_protect_mask
        except Exception:
            pass

    # 크롤링 시 저장된 시장구분 정보 활용 (종목코드 기반 추측보다 정확)
    kosdaq_mask = df['시장구분'] == '코스닥'

    # 시장별 차등 시가총액 필터 (코스피: 500억, 코스닥: 200억)
    market_cap_filter = (
        (~kosdaq_mask & (df['시가총액'] > 50000)) |  # 코스피: 500억 이상
        (kosdaq_mask & (df['시가총액'] > 20000))      # 코스닥: 200억 이상
    )

    # 거래대금 필드 우선순위: 재계산된 표준 컬럼이 있으면 우선 사용
    trade_col = '거래대금_standard' if '거래대금_standard' in df.columns else '거래대금'

    # 기존 필터들을 하나의 마스크로 결합한 뒤 화이트리스트 보호 마스크를 OR 연산으로 합칩니다.
    mask_combined = (
        market_cap_filter &                        # 시장별 차등 시가총액 조건
        (df[trade_col] > 3000) &                   # 30억 이상 (유동성 확보) - 표준 컬럼 우선
        (df['시가총액'] < 5000000) &               # 5조 미만 (대형 우량주 제외)
        (df['거래량'] > 0) &                       # 거래량 있는 종목
        (~df.종목명.str.contains("지주", na=False)) &    # 지주회사 제외
        (~df.종목명.str.contains("홀딩스", na=False)) &  # 홀딩스 제외
        (~df.종목명.str.contains("스팩", na=False)) &    # 스팩 제외
        (~df.종목명.str.contains("리츠", na=False)) &    # 리츠 제외
        (~df.종목명.str.contains("캐피탈", na=False)) &    # 캐피탈 제외 (모의투자 제한 많음)
        (~df.종목명.str.contains("CD금리", na=False)) &    # CD금리 제외
        (~df.종목명.str.contains("KOFR금리", na=False)) &    # KOFR금리 제외
        (~df.종목명.str.contains("머니마켓", na=False))    # 머니마켓 제외
    )

    final_mask = mask_combined | whitelist_protect_mask
    df = df.loc[final_mask]
    try:
        # 디버그: pre-filter 단계에서 화이트리스트 보존 여부 확인
        if '종목코드' in df.columns:
            logger.info(
                "DEBUG post-prefilter: rows=%d, whitelist_preserved=%d",
                len(df),
                int(df['종목코드'].astype(str).isin(list(whitelist_codes_int) if whitelist_codes_int else list(whitelist_codes)).sum()),
            )
    except Exception:
        pass

    # ETF 정책 적용 (all/exclude/only/auto)
    df = _apply_etf_policy(df, policy_overrides=etf_policy_overrides)
    try:
        if '종목코드' in df.columns:
            logger.info("DEBUG post-apply_etf_policy: rows=%d, whitelist_preserved=%d", len(df), int(df['종목코드'].astype(str).isin(list(whitelist_codes_int) if whitelist_codes_int else list(whitelist_codes)).sum()))
    except Exception:
        pass
    # (디버그) 콘솔 출력 제거: logger로 대체됨

    # 우선주 필터링: 종목명에 단순히 '우'가 포함된다고 제거하면
    # '우진', '우리금융지주' 등 일반 종목이 잘못 제외될 수 있음.
    # 보통주는 종목코드가 '0'으로 끝나는 경우가 대부분이므로
    # 종목코드가 있으면 코드 끝자리가 '0'인 종목만 남기고, 없으면 그대로 유지
    try:
        if '종목코드' in df.columns:
            # 우선주 필터: 기본은 코드 끝자리 '0'인 종목만 유지
            # 단, 화이트리스트(_is_whitelist)가 있을 경우에는 해당 종목은 보호
            if '_is_whitelist' in df.columns:
                df = df[df['종목코드'].astype(str).str.endswith('0') | df['_is_whitelist'].astype(bool)]
            else:
                df = df[df['종목코드'].astype(str).str.endswith('0')]
        else:
            pass  # 종목코드가 없으면 그대로 유지
    except Exception:
        # 필터 적용 중 예외가 발생하면 원래 데이터프레임을 유지
        logger.warning("우선주 필터 적용 중 오류 발생: 종목코드 기반 필터링을 건너뜁니다.")
    try:
        if '종목코드' in df.columns:
            logger.info("DEBUG post-priority-share: rows=%d, whitelist_preserved=%d", len(df), int(df['종목코드'].astype(str).isin(list(whitelist_codes_int) if whitelist_codes_int else list(whitelist_codes)).sum()))
    except Exception:
        pass

    # 2. 거래대금(1순위) + 시가총액(2순위) 기준 정렬
    # 당일 등락률은 일일 이벤트에 민감하게 반응하므로 제외.
    # 거래대금과 시가총액은 단기 변동이 적어 유니버스 안정성이 높아짐.
    # 거래대금(우선 표준 컬럼) + 시가총액 기준 정렬
    df = df.sort_values(by=[trade_col, '시가총액'], ascending=[False, False])

    # 필터링한 데이터프레임의 index 번호를 새로 매김
    df = df.reset_index(drop=True)

    # 안전한 DataFrame 조작을 위해 복사본 사용
    df = df.copy()

    try:
        if '종목코드' in df.columns:
            logger.info("DEBUG after-sort-before-top: rows=%d, whitelist_preserved=%d", len(df), int(df['종목코드'].astype(str).isin(list(whitelist_codes_int) if whitelist_codes_int else list(whitelist_codes)).sum()))
    except Exception:
        pass

    # 캐시에서 읽을 때 Parquet를 index_col=0으로 읽는 케이스를 지원
    # index에 종목코드가 들어있다면 이를 명시적 컬럼으로 복원
    if '종목코드' not in df.columns:
        df = df.reset_index()
        # reset_index로 생성된 첫 컬럼을 `종목코드`로 표준화
        first_col = df.columns[0]
        if first_col != '종목코드':
            df = df.rename(columns={first_col: '종목코드'})

    # 상위 max_codes개만 추출 (화이트리스트 보호 적용)
    try:
        df_top = df.loc[: max_codes - 1].copy()
        # 보호 대상(화이트리스트 표식 또는 etf whitelist 명시)을 수집
        protected_norm = set()
        def _norm_code(s):
            try:
                return ''.join(ch for ch in str(s) if ch.isdigit())
            except Exception:
                return str(s)

        if '종목코드' in df.columns:
            if '_is_whitelist' in df.columns:
                protected_norm.update(df.loc[df['_is_whitelist'].astype(bool), '종목코드'].astype(str).map(_norm_code).tolist())
            if '_pre_whitelist' in df.columns:
                try:
                    protected_norm.update(df.loc[df['_pre_whitelist'].astype(bool), '종목코드'].astype(str).map(_norm_code).tolist())
                except Exception:
                    pass

        # 환경변수/오버라이드로 지정된 화이트리스트 코드도 보호 대상으로 포함
        try:
            for c in whitelist_codes:
                protected_norm.add(_norm_code(c))
        except Exception:
            pass
        try:
            for c in whitelist_codes_int:
                protected_norm.add(str(int(c)))
        except Exception:
            pass

        # 현재 top에 없는 보호종목이 있으면 df에서 찾아 append (최종 크기는 max_codes를 초과할 수 있음)
        if protected_norm and '종목코드' in df.columns:
            top_norm = set(df_top['종목코드'].astype(str).map(_norm_code).tolist())
            missing_norm = protected_norm - top_norm
            if missing_norm:
                missing_rows = df.loc[df['종목코드'].astype(str).str.replace(r'\D+', '', regex=True).isin(list(missing_norm))].copy()
                if not missing_rows.empty:
                    # append and dedupe by 코드
                    combined = pd.concat([df_top, missing_rows], ignore_index=True, sort=False)
                    if '종목코드' in combined.columns:
                        combined = combined.drop_duplicates(subset=['종목코드'], keep='first').reset_index(drop=True)
                    df = combined
                else:
                    df = df_top
            else:
                df = df_top
        else:
            df = df_top
    except Exception:
        # 실패 시 기존 행동(슬라이싱)으로 폴백
        df = df.loc[: max_codes - 1]
    
    # Universe 최소 개수 검증 (비정상 데이터 방지)
    MIN_UNIVERSE_SIZE = 10
    if len(df) < MIN_UNIVERSE_SIZE:
        error_msg = f"Universe 크기가 너무 작습니다 ({len(df)}개). 최소 {MIN_UNIVERSE_SIZE}개 필요."
        logger.error(error_msg)
        raise Exception(error_msg)

    # 유니버스 생성 결과를 Parquet 출력
    # 우선 df는 필터링 및 정렬을 마친 상위 max_codes개(기본 200개, env로 오버라이드 가능) 후보입니다.
    # 추가 조치: 현재 보유/주문 종목(kiwoom_client)을 병합하여 보유종목이 누락되지 않도록 함
    try:
        if kiwoom_client is not None:
            held_codes = set()
            order_codes = set()
            try:
                held_codes = set(getattr(kiwoom_client, 'balance', {}).keys())
            except Exception:
                held_codes = set()
            try:
                order_codes = set(getattr(kiwoom_client, 'order', {}).keys())
            except Exception:
                order_codes = set()

            # 코드 -> 종목명 맵을 빠르게 조회
            existing_codes = set(df['종목코드'].astype(str).tolist()) if '종목코드' in df.columns else set()

            # 보유/주문 중 df에 없는 종목을 df에 추가(간단한 레코드로 추가)
            missing_codes = (held_codes | order_codes) - existing_codes
            added_rows = []
            for code in missing_codes:
                # 모의투자 블랙리스트 처리는 호출측에서 하도록 함
                code_name = None
                # 우선 Kiwoom client의 balance에서 종목명 사용
                try:
                    code_name = kiwoom_client.balance.get(code, {}).get('종목명')
                except Exception:
                    code_name = None
                # 필요 시 API로 종목명 조회(안정성: 예외 처리)
                if not code_name:
                    try:
                        code_name = kiwoom_client.get_master_code_name(code) or f"{code}"
                    except Exception:
                        code_name = f"{code}"

                # 최소한의 행을 추가 (필요 컬럼에 NAs)
                new_row = {col: None for col in df.columns}
                if '종목코드' in df.columns:
                    new_row['종목코드'] = code
                # 종목명 컬럼이 존재하면 채움
                if '종목명' in df.columns:
                    new_row['종목명'] = code_name
                added_rows.append(new_row)

            if added_rows:
                # concat으로 인한 FutureWarning 회피: 행 단위로 안전하게 추가
                # 컬럼 타입에 맞는 기본값으로 채워 삽입 (all-NA 컬럼 생성 방지)
                col_kinds = {col: df[col].dtype.kind for col in df.columns}
                for new_row in added_rows:
                    row_values = []
                    for col in df.columns:
                        if col in new_row and new_row[col] is not None:
                            row_values.append(new_row[col])
                        else:
                            kind = col_kinds.get(col, 'O')
                            if kind in ('i', 'u', 'f', 'c'):  # numeric types
                                row_values.append(0)
                            elif kind == 'b':
                                row_values.append(False)
                            else:
                                row_values.append('')
                    df.loc[len(df)] = row_values

            # 이제 전체 후보에서 보유/주문을 우선 보존하되, max_codes를 초과하면
            # 보유/주문이 아닌 기존 후보 중 거래량이 작은 순으로 제거
            # 거래량 컬럼이름 다양성 고려
            vol_col = None
            for c in ['거래량', 'volume', '누적거래량']:
                if c in df.columns:
                    vol_col = c
                    break

            # mark held/order rows and protect whitelist rows as well
            if '종목코드' in df.columns:
                protected_codes = held_codes | order_codes
                if '_is_whitelist' in df.columns:
                    df['_is_held_or_order'] = df['종목코드'].astype(str).isin(protected_codes) | df['_is_whitelist'].astype(bool)
                else:
                    df['_is_held_or_order'] = df['종목코드'].astype(str).isin(protected_codes)
            else:
                df['_is_held_or_order'] = False

            # 만약 후보수가 초과하면 제거 수행
            if len(df) > max_codes:
                excess = len(df) - max_codes
                # 제거 후보: 보유/주문이 아닌 행
                # 제거 후보: 보유/주문이 아닌 행 (명시적 복사)
                removable_df = df.loc[~df['_is_held_or_order']].copy()
                if vol_col:
                    # NaN을 0으로 대체한 별도 열을 생성하여 원본을 건드리지 않음
                    vol_series = pd.to_numeric(removable_df[vol_col], errors='coerce').fillna(0)
                    removable_sorted = removable_df.assign(_vol_numeric=vol_series).sort_values(by='_vol_numeric', ascending=True)
                else:
                    removable_sorted = removable_df

                # 실제로 제거할 인덱스
                to_remove_idx = removable_sorted.index.tolist()[:excess]
                if len(to_remove_idx) < excess:
                    logger.warning("병합 후 슬롯 부족: 제거 후보 부족 (필요:%d, 가능:%d)", excess, len(to_remove_idx))

                # 제거
                if to_remove_idx:
                    df = df.drop(index=to_remove_idx).reset_index(drop=True)

            # 최종적으로 max_codes까지 자름(안전망)
            df = df.head(max_codes)

            # cleanup: drop protection/marker columns before saving
            if '_is_held_or_order' in df.columns:
                df = df.drop(columns=['_is_held_or_order'])
            if '_is_whitelist' in df.columns:
                df = df.drop(columns=['_is_whitelist'])

    except Exception as e:
        logger.warning(f"보유/주문 병합 중 경고 발생: {e}")

    if universe_output_file:
        try:
            out_universe = (
                universe_output_file
                if os.path.isabs(universe_output_file)
                else os.path.join(DB_DIR, universe_output_file)
            )
            try:
                df.to_parquet(out_universe, index=True)
                try:
                    logger.info(f"Universe 저장: {Path(out_universe).resolve()}")
                except Exception:
                    logger.info(f"Universe 저장: {out_universe}")
            except Exception as e:
                logger.error(f"Universe Parquet 저장 실패: {e}")
        except Exception as e:
            logger.warning(f"universe 저장 실패: {e}")

    # 임시로 생성된 대용량 컬럼들을 삭제하여 메모리 사용을 줄입니다.
    try:
        tmp_cols = ['_vol_numeric', '_is_whitelist']
        for c in tmp_cols:
            if c in df.columns:
                try:
                    df.drop(columns=[c], inplace=True)
                except Exception:
                    pass
    except Exception:
        pass

    # (주의) 원본 DataFrame 레퍼런스 제거는 호출자에서 처리합니다.
    # 내부에서는 임시 컬럼만 제거하여 피크 메모리를 낮춥니다.

    universe_list = df['종목명'].tolist() if '종목명' in df.columns else df.iloc[:, 0].astype(str).tolist()
    logger.info(f"Universe 생성 완료: {len(universe_list)}개 종목 (병합 후)")
    return universe_list


if __name__ == "__main__":
    import sys
    import os

    # 권장 실행 방식 안내: 패키지 모드로 실행하는 것이 import 경로 문제를 방지합니다.
    if not (__package__):
        sys.stderr.write(
            "권장: 패키지 모드로 실행하세요 — `poetry run python -m util.make_up_universe`\n"
            "직접 실행 중입니다. 일부 상대/절대 import가 실패할 수 있습니다.\n"
        )
        # 자동 재실행 시도 (옵션: --no-reexec 또는 환경변수 SKIP_REEXEC로 건너뜀)
        if "--no-reexec" not in sys.argv and not os.getenv("SKIP_REEXEC"):
            sys.stderr.write("모듈 모드로 재실행합니다...\n")
            args = [sys.executable, "-m", "util.make_up_universe"] + sys.argv[1:]
            os.execv(sys.executable, args)
        else:
            sys.stderr.write("재실행 건너뜀 (--no-reexec 또는 SKIP_REEXEC 감지). 계속 진행합니다.\n")

    # 실제 동작 시작
    print('Start!')
    universe = get_universe()
    print(universe)
    print('End')