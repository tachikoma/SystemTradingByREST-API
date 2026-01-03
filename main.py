import os
import sys
import time
import threading
from pathlib import Path
from dotenv import load_dotenv


if __name__ == '__main__':
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
        confirmation = input("Type 'YES' to confirm: ")
        if confirmation != 'YES':
            print("Aborted.")
            sys.exit(0)
    
    # Kiwoom 클라이언트 생성
    kiwoom = Kiwoom(appkey=appkey, secretkey=secretkey, mock=is_mock)

    # 전략 스레드를 시작합니다
    rsi_strategy = RSIStrategy(kiwoom)
    rsi_strategy.start()

    print("RSIStrategy started. Press Ctrl+C to stop.")

    try:
        while True:
            # 시그널 처리를 위해 메인 스레드를 계속 실행 상태로 유지합니다
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping strategy...")
        # 전략 스레드에 정지 신호를 보내거나 필요 시 정리 작업을 수행합니다
        # 현재는 웹소켓을 끊고 종료합니다.
        kiwoom.disconnect()
        print("Kiwoom disconnected.")
        # 전략 스레드가 종료될 때까지 대기합니다
        rsi_strategy.join()
        print("Strategy thread stopped.")
        sys.exit(0)