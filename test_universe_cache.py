#!/usr/bin/env python3
"""
Universe 생성 및 캐싱 기능 테스트 스크립트

사용법 예시:
    # 빠른 검증: 10개 종목만 수집하고 샘플 파일을 생성합니다
    poetry run python test_universe_cache.py --quick

    # 빠른 검증(dry-run): 파일 생성 없이 샘플 동작만 확인
    poetry run python test_universe_cache.py --quick --dry-run

    # 전체 테스트(실제 API, 약 7분 소요)
    poetry run python test_universe_cache.py --real-api -y

    # 전체 테스트(dry-run): 파일 생성 없이 전체 흐름을 실행하려면 -y 함께 사용
    poetry run python test_universe_cache.py --real-api --dry-run -y

주의:
    - `--dry-run`은 파일 쓰기를 무력화하여 캐시/유니버스 파일이 생성되지 않게 합니다.
    - `--dry-run`과 `--real-api`를 함께 사용하면 긴 실행(약 7분)이 발생할 수 있으므로
      명시적 승인(-y)이 없으면 자동으로 중단됩니다.
"""

import os
import sys
import argparse
import time
from pathlib import Path
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

# 로깅 초기화
from util.logging_config import configure_logging
configure_logging()

from util.make_up_universe import cache_daily_data
from api.Kiwoom import Kiwoom
import logging
import pandas as pd

logger = logging.getLogger(__name__)


def test_with_real_api(quick_mode=False):
    """실제 API로 통합 테스트

    quick_mode: True이면 코스피 상위 10개 종목만 빠르게 조회하고
    샘플 파일(`sample_stocks_kiwoom.parquet`)을 생성합니다.
    """
    logger.info("=" * 60)
    if quick_mode:
        logger.info("실제 API 빠른 검증 테스트 시작 (10개 종목만)")
        logger.info("⚠️  약 1초 소요")
    else:
        logger.info("실제 API로 통합 테스트 시작")
        logger.info("⚠️  약 7분 소요 (4234개 종목)")
    logger.info("=" * 60)

    # 실전투자 키 확인
    appkey = os.environ.get('KIWOOM_REAL_APPKEY')
    secretkey = os.environ.get('KIWOOM_REAL_SECRETKEY')

    if not appkey or not secretkey:
        logger.error("❌ KIWOOM_REAL_APPKEY와 KIWOOM_REAL_SECRETKEY가 필요합니다")
        logger.error("백테스트 데이터 수집은 실전투자 키를 사용합니다")
        return False

    logger.info("키움 API 클라이언트 초기화 중...")
    kiwoom = Kiwoom(appkey=appkey, secretkey=secretkey, mock=False)

    if quick_mode:
        # 빠른 테스트: 코스피 10개만
        logger.info("\n[빠른 검증] 코스피 10개 종목만 수집")
        try:
            kospi_list = kiwoom.get_code_list_by_market("0")  # 코스피
            logger.info(f"코스피 전체: {len(kospi_list)}개 종목")

            test_stocks = kospi_list[:10]
            logger.info(f"테스트 대상: {[s['name'] for s in test_stocks]}")

            data = []
            for i, stock in enumerate(test_stocks):
                code = stock['code']
                name = stock['name']
                try:
                    info = kiwoom.get_stock_info(code)
                    if info:
                        data.append({
                            '종목코드': code,
                            '종목명': name,
                            '시장구분': '코스피',
                            '현재가': info.get('cur_prc', 0),
                            '거래량': info.get('trde_qty', 0),
                            '거래대금': info.get('trde_amt', 0),
                            '시가총액': info.get('mrkt_cap', 0),
                            '등락률': info.get('flu_rt', 0),
                            '외국인비율': info.get('for_exh_rt', 0),
                            '상장주식수': info.get('list_cnt', 0),
                        })
                        logger.info(f"  [{i+1}/10] {name} - 성공")
                    time.sleep(0.1)  # Rate limit
                except Exception as e:
                    logger.warning(f"  [{i+1}/10] {name} - 실패: {e}")

            if data:
                df = pd.DataFrame(data)
                sample_file = 'sample_stocks_kiwoom.parquet'
                try:
                    df.to_parquet(sample_file)
                    logger.info(f"✅ 샘플 파일 생성: {sample_file}")
                except Exception as e:
                    logger.warning(f"샘플 파일 저장 실패: {e}")

                logger.info(f"\n✅ 성공: {len(df)}개 종목 수집 완료")
                logger.info(f"샘플 데이터:\n{df}")
                logger.info("\n💡 전체 테스트를 실행하려면:")
                logger.info("   poetry run python test_universe_cache.py --real-api -y")
            else:
                logger.error("❌ 데이터 수집 실패")
                return False
        except Exception as e:
            logger.error(f"❌ 실패: {e}", exc_info=True)
            return False
    else:
        # 전체 테스트
        logger.info("\n[통합 테스트] cache_daily_data() - 전체 종목 수집")
        logger.info("진행 상황을 확인하세요 (100개마다 로그 출력)")
        try:
            df = cache_daily_data(kiwoom)
            logger.info(f"✅ 성공: {len(df)}개 종목 캐싱 완료")
            logger.info(f"파일: all_stocks_kiwoom.parquet")
            logger.info(f"샘플 데이터:\n{df.head()}")
        except Exception as e:
            logger.error(f"❌ 실패: {e}", exc_info=True)
            return False

    logger.info("\n" + "=" * 60)
    logger.info("✅ 실제 API 테스트 완료!")
    logger.info("=" * 60)
    return True


def main():
    parser = argparse.ArgumentParser(description='Universe 캐싱 기능 테스트')
    parser.add_argument('--real-api', action='store_true', 
                       help='실제 API로 전체 테스트 (시간 소요, 실전투자 키 필요)')
    parser.add_argument('--quick', action='store_true',
                       help='빠른 검증 (10개 종목만, 1초 소요)')
    parser.add_argument('--dry-run', action='store_true', dest='dry_run',
                       help='파일 생성 방지: 실제 파일을 쓰지 않습니다')
    parser.add_argument('-y', '--yes', action='store_true',
                       help='확인 없이 바로 실행')

    args = parser.parse_args()

    success = False

    if args.quick:
        # 빠른 검증
        print("🚀 빠른 검증 모드: 10개 종목만 테스트합니다 (약 1초)")
        if args.dry_run:
            # 파일 쓰기 금지: pandas DataFrame의 주요 write 메서드를 무력화
            _pd = pd
            _orig_to_parquet = getattr(_pd.DataFrame, 'to_parquet', None)
            _orig_to_csv = getattr(_pd.DataFrame, 'to_csv', None)
            _orig_to_pickle = getattr(_pd.DataFrame, 'to_pickle', None)
            _orig_to_feather = getattr(_pd.DataFrame, 'to_feather', None)

            def _noop_write(self, *args, **kwargs):
                logger.info("DRY-RUN: prevented DataFrame write in --dry-run mode")
                return None

            _pd.DataFrame.to_parquet = _noop_write
            _pd.DataFrame.to_csv = _noop_write
            _pd.DataFrame.to_pickle = _noop_write
            if _orig_to_feather is not None:
                _pd.DataFrame.to_feather = _noop_write

            try:
                success = test_with_real_api(quick_mode=True)
            finally:
                # 원래 메서드 복원
                if _orig_to_parquet is not None:
                    _pd.DataFrame.to_parquet = _orig_to_parquet
                if _orig_to_csv is not None:
                    _pd.DataFrame.to_csv = _orig_to_csv
                if _orig_to_pickle is not None:
                    _pd.DataFrame.to_pickle = _orig_to_pickle
                if _orig_to_feather is not None:
                    _pd.DataFrame.to_feather = _orig_to_feather
        else:
            success = test_with_real_api(quick_mode=True)

    elif args.real_api:
        # 전체 테스트
        # 안전장치: --dry-run과 함께 전체(긴) 실행을 원치 않을 가능성이 높으므로
        # - --dry-run 인 경우에는 명시적 승인(-y)이 없으면 실행을 중단하고 안내합니다.
        if args.dry_run and not args.yes:
            print("⚠️ --real-api와 --dry-run을 함께 사용하면 긴(약 7분) 네트워크 호출이 발생하지만 파일은 저장되지 않습니다.")
            print("원하지 않으면 --quick을 사용하거나, 계속하려면 -y/--yes를 추가하세요.")
            parser.print_help()
            sys.exit(2)

        if not args.yes:
            confirm = input("⚠️  실제 API 전체 테스트는 약 7분 소요됩니다. 계속하시겠습니까? (yes/no): ")
            if confirm.lower() != 'yes':
                print("테스트 취소됨")
                return
        else:
            print("🚀 실제 API 전체 테스트를 시작합니다 (약 7분 소요)")

        if args.dry_run:
            # 전체 테스트에서 파일 쓰기 금지
            _pd = pd
            _orig_to_parquet = getattr(_pd.DataFrame, 'to_parquet', None)
            _orig_to_csv = getattr(_pd.DataFrame, 'to_csv', None)
            _orig_to_pickle = getattr(_pd.DataFrame, 'to_pickle', None)
            _orig_to_feather = getattr(_pd.DataFrame, 'to_feather', None)

            def _noop_write(self, *args, **kwargs):
                logger.info("DRY-RUN: prevented DataFrame write in --dry-run mode")
                return None

            _pd.DataFrame.to_parquet = _noop_write
            _pd.DataFrame.to_csv = _noop_write
            _pd.DataFrame.to_pickle = _noop_write
            if _orig_to_feather is not None:
                _pd.DataFrame.to_feather = _noop_write

            try:
                success = test_with_real_api(quick_mode=False)
            finally:
                if _orig_to_parquet is not None:
                    _pd.DataFrame.to_parquet = _orig_to_parquet
                if _orig_to_csv is not None:
                    _pd.DataFrame.to_csv = _orig_to_csv
                if _orig_to_pickle is not None:
                    _pd.DataFrame.to_pickle = _orig_to_pickle
                if _orig_to_feather is not None:
                    _pd.DataFrame.to_feather = _orig_to_feather
        else:
            success = test_with_real_api(quick_mode=False)
    else:
        print("⚠️ 실행 모드를 지정해주세요: --real-api 또는 --quick")
        parser.print_help()
        sys.exit(2)

    if success:
        print("\n✅ 모든 테스트 통과!")
        sys.exit(0)
    else:
        print("\n❌ 테스트 실패")
        sys.exit(1)


if __name__ == '__main__':
    main()
