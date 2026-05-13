import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# 스크립트 직접 실행 시 프로젝트 루트를 import 경로에 추가
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='실시간 유니버스 즉시 강제 갱신')
    parser.add_argument('-y', '--yes', action='store_true', help='실전투자 확인 프롬프트 없이 실행 (위험)')
    parser.add_argument('--force-api', action='store_true', help='키움 API로 강제 전체 갱신(장중에도)')
    args = parser.parse_args()

    # 1) .env 로드
    env_path = PROJECT_ROOT / '.env'
    load_dotenv(dotenv_path=env_path)

    # 2) 로깅 초기화
    from util.logging_config import configure_logging, get_logger

    configure_logging(file_name='refresh_universe_now.log')
    logger = get_logger('refresh_universe_now')

    # 3) 나머지 모듈 import
    from api.Kiwoom import Kiwoom
    from strategy.RSIStrategy import RSIStrategy

    mode = os.environ.get('KIWOOM_MODE', 'mock').lower()
    is_mock = mode == 'mock'

    if is_mock:
        appkey = os.environ.get('KIWOOM_MOCK_APPKEY') or os.environ.get('KIWOOM_APPKEY')
        secretkey = os.environ.get('KIWOOM_MOCK_SECRETKEY') or os.environ.get('KIWOOM_SECRETKEY')
        mode_name = '모의투자'
    else:
        appkey = os.environ.get('KIWOOM_REAL_APPKEY')
        secretkey = os.environ.get('KIWOOM_REAL_SECRETKEY')
        mode_name = '실전투자'

    if not appkey or not secretkey:
        logger.error('API 키가 설정되지 않았습니다. .env를 확인하세요.')
        sys.exit(1)

    print(f'유니버스 즉시 갱신 시작 ({mode_name})')

    if not is_mock and not args.yes:
        confirmation = input("실전투자 모드입니다. 즉시 유니버스 갱신을 진행하려면 'YES'를 입력하세요: ").strip()
        if confirmation != 'YES':
            print('취소되었습니다.')
            sys.exit(0)

    kiwoom = None
    strategy = None

    try:
        kiwoom = Kiwoom(appkey=appkey, secretkey=secretkey, mock=is_mock)

        # --force-api 옵션이 주어지면 장중이라도 키움 API로 전체 종목을 강제 갱신합니다.
        if args.force_api:
            from util.make_up_universe import fetch_all_stocks_from_kiwoom
            logger.info('강제 API 갱신 요청: 키움 API로 전체 종목 수집 시작 (force-api)')
            print('키움 API 강제 전체 갱신 시작... (시간이 소요될 수 있습니다)')
            try:
                df = fetch_all_stocks_from_kiwoom(kiwoom, use_cache=False, save_cache=True)
                logger.info('강제 API 갱신 완료: %d개 종목 수집 및 캐시/Canonical 저장', len(df))
                print(f'완료: {len(df)}개 종목 수집 및 캐시/Canonical 저장')
            except Exception as e:
                logger.exception('강제 API 전체 수집 실패: %s', e)
                print(f'강제 API 실패: {e}')

        # on_demand 모드로 초기화 후 강제 갱신 메서드 실행
        strategy = RSIStrategy(kiwoom, universe_cache_mode='on_demand')

        logger.info('즉시 유니버스 강제 갱신 수행 시작')
        strategy.update_universe_with_holdings()
        logger.info('즉시 유니버스 강제 갱신 수행 완료 (in-memory 종목 수: %d)', len(strategy.universe))

        print(f'완료: universe 갱신 및 실시간 재등록 완료 (종목 수: {len(strategy.universe)})')
    except Exception as e:
        logger.exception('즉시 유니버스 강제 갱신 실패: %s', e)
        print(f'실패: {e}')
        sys.exit(1)
    finally:
        try:
            if strategy is not None:
                strategy.stop()
        except Exception:
            pass
        try:
            if kiwoom is not None:
                kiwoom.stop_websocket()
        except Exception:
            pass
        try:
            if kiwoom is not None:
                kiwoom.disconnect()
        except Exception:
            pass
