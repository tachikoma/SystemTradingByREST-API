"""
가격 데이터 페칭 헬퍼
- Kiwoom 클라이언트의 `get_price_data` 호출을 중앙에서 관리합니다.
- 얕은 조회만을 제공하며
  DB 저장 옵션을 지원합니다.

사용법 예시:
from util.price_fetcher import fetch_price_data
price_df = fetch_price_data(kiwoom, code)
"""

import os
import time
import logging
from typing import Optional

import pandas as pd

from util.db_helper import insert_df_to_db
from util.time_helper import get_korea_time

logger = logging.getLogger(__name__)


def fetch_price_data(kiwoom, code: str, save_to_db: bool = False,
                     strategy_name: Optional[str] = None, rate_limit_delay: float = 0.3):
    """
    최신 가격 데이터(얕은 조회, 최신 1페이지)만 조회하는 간단한 헬퍼 함수

    Args:
        kiwoom: Kiwoom API 클라이언트 인스턴스
        code: 조회할 종목 코드
        save_to_db: True면 결과를 DB에 저장 (strategy_name 필요)
        strategy_name: DB 저장 시 사용할 DB 이름(테이블 네임은 종목 코드)
        rate_limit_delay: (미래 확장용) API 호출 사이 대기 시간(초)

    Returns:
        pandas.DataFrame 또는 None
    """
    try:
        def call_get():
            # 최신 1페이지만 요청
            try:
                df = kiwoom.get_price_data(code, max_loops=1)
            except TypeError:
                # 일부 구현은 max_loops 파라미터를 받지 않을 수 있음
                df = kiwoom.get_price_data(code)
            except Exception as e:
                logger.warning("get_price_data 호출 실패 %s: %s", code, e)
                return None
            return df

        # 얕은 조회만 수행
        df = call_get()

        # DB 저장 옵션
        if save_to_db and strategy_name and df is not None and len(df) > 0:
            try:
                insert_df_to_db(strategy_name, code, df)
                logger.info("Saved fetched price data to DB %s.%s (rows=%d)", strategy_name, code, len(df))
            except Exception as e:
                logger.warning("DB 저장 실패 %s.%s: %s", strategy_name, code, e)

        # 상세 로깅
        try:
            rows = len(df) if df is not None else 0
            first = df.index[0] if (df is not None and rows > 0) else None
            last = df.index[-1] if (df is not None and rows > 0) else None
            logger.debug("fetch_price_data result for %s rows=%s first=%s last=%s",
                         code, rows, first, last)
        except Exception:
            pass

        return df
    except Exception as e:
        logger.exception("fetch_price_data 예외 %s: %s", code, e)
        return None
