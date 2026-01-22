"""
가격 데이터 페칭 헬퍼
- Kiwoom 클라이언트의 `get_price_data` 호출을 중앙에서 관리합니다.
- 얕은 조회(shallow), 심층 조회(deep), 자동(auto) 모드를 제공하며
  재시도 로직, 로깅, DB 저장 옵션을 지원합니다.

사용법 예시:
from util.price_fetcher import fetch_price_data
price_df = fetch_price_data(kiwoom, code, mode='auto', max_loops=None)
"""

import os
import time
import logging
from typing import Optional

import pandas as pd

from util.db_helper import insert_df_to_db
from util.time_helper import get_korea_time

logger = logging.getLogger(__name__)


def fetch_price_data(kiwoom, code: str, mode: str = 'auto', max_loops: Optional[int] = None,
                     save_to_db: bool = False, strategy_name: Optional[str] = None,
                     replace_db: bool = True, rate_limit_delay: float = 0.3):
    """
    가격 데이터를 일관된 정책으로 가져오는 헬퍼 함수

    Args:
        kiwoom: Kiwoom API 클라이언트 인스턴스
        code: 조회할 종목 코드
        mode: 'shallow'|'deep'|'auto' - 동작 모드
            - shallow: 최신 1페이지만 요청
            - deep: max_loops 만큼 요청(전체/심층)
            - auto: shallow 후 필요 시 deep 재시도
        max_loops: deep 모드에서 사용할 페이지 수. None이면 환경변수 `PRICE_FETCH_MAX_LOOPS` 사용
        save_to_db: True면 결과를 DB에 저장 (strategy_name 필요)
        strategy_name: DB 저장 시 사용할 DB 이름(테이블 네임은 종목 코드)
        replace_db: True면 기존 테이블을 덮어씀 (pandas.to_sql 'replace')
        rate_limit_delay: API 호출 사이 대기 시간(초)

    Returns:
        pandas.DataFrame 또는 None
    """
    try:
        if max_loops is None:
            try:
                max_loops = int(os.getenv('PRICE_FETCH_MAX_LOOPS', '1'))
            except Exception:
                max_loops = 1

        mode = str(mode).lower() if mode else 'auto'

        def call_get(loop_count: int):
            # 실제 Kiwoom 호출 래퍼
            try:
                df = kiwoom.get_price_data(code, max_loops=loop_count)
            except TypeError:
                # 일부 구간에서 get_price_data가 max_loops 파라미터 없이 동작할 수 있음
                df = kiwoom.get_price_data(code)
            except Exception as e:
                logger.warning("get_price_data 호출 실패 %s %s: %s", code, loop_count, e)
                return None
            return df

        df = None
        retried = False

        if mode == 'shallow':
            df = call_get(1)
        elif mode == 'deep':
            df = call_get(max_loops)
        else:  # auto
            # 1) 얕은 조회 시도
            df = call_get(1)
            time.sleep(rate_limit_delay)
            # 검사: 빈 결과 혹은 최신 날짜가 오늘보다 이전이면 심층 재시도
            need_deep = False
            if df is None or len(df) == 0:
                need_deep = True
            else:
                try:
                    latest = str(df.index[-1])
                    today = get_korea_time().strftime('%Y%m%d')
                    if latest < today:
                        need_deep = True
                except Exception:
                    need_deep = True

            if need_deep:
                retried = True
                logger.info("Shallow fetch insufficient for %s; performing deep fetch with max_loops=%d", code, max_loops)
                df = call_get(max_loops)

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
            logger.debug("fetch_price_data result for %s mode=%s retried=%s rows=%s first=%s last=%s",
                         code, mode, retried, rows, first, last)
        except Exception:
            pass

        return df
    except Exception as e:
        logger.exception("fetch_price_data 예외 %s: %s", code, e)
        return None
