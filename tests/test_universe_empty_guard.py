"""유니버스 빈 결과 가드 테스트.

주말/휴장 중에 유니버스를 만들면 데이터 수집이 실패해 빈 결과가 나오는데,
이를 정상 결과로 취급하면 DB의 기존 유니버스와 canonical 캐시가 빈 값으로
덮어써져 봇이 신호를 전혀 만들지 못하게 된다. 이 동작을 방지하는 가드를 검증한다.
"""

import pandas as pd
import pytest

from strategy.RSIStrategy import RSIStrategy
from util.make_up_universe import (
    MIN_UNIVERSE_SIZE,
    _filter_and_create_universe,
)


def _empty_all_stocks_df():
    """필터링에 필요한 컬럼만 가진 0행 DataFrame"""
    return pd.DataFrame(
        {
            '종목코드': pd.Series(dtype='object'),
            '종목명': pd.Series(dtype='object'),
            '시장구분': pd.Series(dtype='object'),
            '거래량': pd.Series(dtype='float64'),
            '거래대금': pd.Series(dtype='float64'),
            '시가총액': pd.Series(dtype='float64'),
            '등락률': pd.Series(dtype='float64'),
            '외국인비율': pd.Series(dtype='float64'),
        }
    )


def test_filter_and_create_universe_raises_on_empty_result():
    """빈 결과는 정상 반환이 아니라 예외여야 한다 (기존 유니버스 보존 유도)"""
    with pytest.raises(Exception) as exc_info:
        _filter_and_create_universe(_empty_all_stocks_df())

    assert "비어" in str(exc_info.value) or "없습니다" in str(exc_info.value)


def test_normalize_units_skips_canonical_save_for_empty_data(monkeypatch):
    """빈/희소 데이터는 canonical로 저장하지 않는다 (30일간 빈 캐시 재사용 방지)"""
    import util.make_up_universe as mu

    saved = []

    class _Stop(Exception):
        pass

    def fake_to_parquet(self, path, index=True):
        saved.append(path)
        raise _Stop()

    monkeypatch.setattr(pd.DataFrame, 'to_parquet', fake_to_parquet, raising=False)

    # _Stop이 발생하지 않아야 정상 (저장 시도 자체를 하지 않음)
    mu._normalize_units(_empty_all_stocks_df(), save_canonical=True)

    assert saved == []


def test_is_parquet_empty_detects_empty_file(tmp_path):
    """mtime이 신선해도 0행 canonical은 캐시로 인정하지 않는다"""
    strategy = RSIStrategy.__new__(RSIStrategy)
    empty_path = tmp_path / 'empty.parquet'
    full_path = tmp_path / 'full.parquet'

    pd.DataFrame({'종목코드': []}).to_parquet(empty_path)
    pd.DataFrame({'종목코드': ['005930'] * MIN_UNIVERSE_SIZE}).to_parquet(full_path)

    assert strategy._is_parquet_empty(str(empty_path)) is True
    assert strategy._is_parquet_empty(str(full_path)) is False


def test_is_parquet_empty_returns_false_on_read_error(tmp_path):
    """읽기 실패 시에는 기존 동작(캐시 사용)을 유지한다"""
    strategy = RSIStrategy.__new__(RSIStrategy)
    broken = tmp_path / 'broken.parquet'
    broken.write_text('not a parquet', encoding='utf-8')

    assert strategy._is_parquet_empty(str(broken)) is False
