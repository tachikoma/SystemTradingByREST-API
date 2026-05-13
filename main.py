import os
import sys
import time
import argparse
from pathlib import Path
from dotenv import load_dotenv


if __name__ == '__main__':
    # 커맨드라인 인자 파싱
    parser = argparse.ArgumentParser(description='System Trading Bot')
    parser.add_argument('-y', '--yes', action='store_true',
                       help='실전투자 확인 프롬프트 없이 바로 실행 (위험!)')
    args = parser.parse_args()
    
    # 모듈을 임포트하기 전에 .env를 먼저 로드합니다.
    # 이렇게 하면 로깅 설정 등 초기화 과정에서 .env 값을 사용할 수 있습니다.
    env_path = Path(__file__).parent / '.env'
    load_dotenv(dotenv_path=env_path)

    # .env 로드 후 로깅을 초기화하여 KIW_LOG_LEVEL 등 로깅 관련 환경변수가 반영되도록 합니다.
    from util.logging_config import configure_logging
    configure_logging()

    # 이제 import 시점에 로거를 얻는 모듈들을 임포트합니다
    from api.Kiwoom import Kiwoom
    from strategy.RSIStrategy import RSIStrategy

    # 환경변수에서 거래 모드를 가져옵니다 (기본값: mock)
    mode = os.environ.get('KIWOOM_MODE', 'mock').lower()
    is_mock = mode == 'mock'
    
    # 거래 모드에 따라 적절한 API 키를 가져옵니다
    if is_mock:
        appkey = os.environ.get('KIWOOM_MOCK_APPKEY') or os.environ.get('KIWOOM_APPKEY')
        secretkey = os.environ.get('KIWOOM_MOCK_SECRETKEY') or os.environ.get('KIWOOM_SECRETKEY')
        mode_name = "모의투자"
    else:
        appkey = os.environ.get('KIWOOM_REAL_APPKEY')
        secretkey = os.environ.get('KIWOOM_REAL_SECRETKEY')
        mode_name = "실전투자"
    
    if not appkey or not secretkey:
        print(f"Error: API keys for {mode_name} mode are not set.")
        print("Please create a .env file with the following content:")
        if is_mock:
            print("  KIWOOM_MODE=mock")
            print("  KIWOOM_MOCK_APPKEY=your_mock_app_key")
            print("  KIWOOM_MOCK_SECRETKEY=your_mock_secret_key")
        else:
            print("  KIWOOM_MODE=real")
            print("  KIWOOM_REAL_APPKEY=your_real_app_key")
            print("  KIWOOM_REAL_SECRETKEY=your_real_secret_key")
        sys.exit(1)
    
    print(f"🚀 Starting System Trading in {mode_name} mode...")
    if not is_mock:
        print("⚠️  WARNING: Running in REAL trading mode! Real money is at risk.")
        if not args.yes:
            confirmation = input("Type 'YES' to confirm: ")
            if confirmation != 'YES':
                print("Aborted.")
                sys.exit(0)
        else:
            print("⚠️  Auto-confirmed with -y option. Starting real trading...")
    
    # Kiwoom 클라이언트 생성
    kiwoom = Kiwoom(appkey=appkey, secretkey=secretkey, mock=is_mock)

    # 전략 스레드를 시작합니다 (기본 모드: env UNIVERSE_CACHE_MODE, default: 'eod')
    universe_cache_mode = os.environ.get('UNIVERSE_CACHE_MODE', 'eod').strip().lower()
    rsi_strategy = RSIStrategy(kiwoom, universe_cache_mode=universe_cache_mode)
    rsi_strategy.start()

    # 종료 시 수행할 정리 작업들을 등록합니다 (텔레그램 알림 포함)
    from util import shutdown

    # Kiwoom 클라이언트 정리: 안전한 웹소켓 셧다운 호출
    # (disconnect()는 내부적으로 재연결 루프를 멈추지 못할 수 있으므로 stop_websocket을 사용)
    shutdown.register_cleanup(lambda: kiwoom.stop_websocket())
    # 추가로 필요하면 disconnect도 호출
    shutdown.register_cleanup(lambda: kiwoom.disconnect())

    # 전략 스레드 정리: 플래그를 내려 스레드 루프가 종료되도록 한 뒤 조인
    def _stop_strategy():
        try:
            # use the strategy's stop() to wake interruptible waits
            try:
                rsi_strategy.stop()
            except Exception:
                rsi_strategy.is_init_success = False
            rsi_strategy.join(timeout=10)
        except Exception:
            pass

    shutdown.register_cleanup(_stop_strategy)

    # 시그널 핸들러 및 예외 훅 설정 (SIGINT, SIGTERM, uncaught exceptions)
    shutdown.setup_signal_handlers()

    print("RSIStrategy started. Press Ctrl+C to stop.")

    try:
        while True:
            # 시그널 처리를 위해 메인 스레드를 계속 실행 상태로 유지합니다
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping strategy (KeyboardInterrupt)...")
        try:
            # trigger_shutdown will run registered cleanup funcs and send telegram
            shutdown.trigger_shutdown("KeyboardInterrupt")
        except Exception:
            try:
                kiwoom.stop_websocket()
                kiwoom.disconnect()
            except Exception:
                pass
        sys.exit(0)