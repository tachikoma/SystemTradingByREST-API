import os
import sys
import time
import threading
from pathlib import Path
from dotenv import load_dotenv
from api.Kiwoom import Kiwoom
from strategy.RSIStrategy import RSIStrategy

if __name__ == '__main__':
    # Load environment variables from .env file
    # .env 파일에서 환경 변수를 로드합니다
    env_path = Path(__file__).parent / '.env'
    load_dotenv(dotenv_path=env_path)
    
    # Get API keys from environment variables
    # 환경 변수에서 API 키를 가져옵니다
    appkey = os.environ.get('KIWOOM_APPKEY')
    secretkey = os.environ.get('KIWOOM_SECRETKEY')

    if not appkey or not secretkey:
        print("Error: KIWOOM_APPKEY and KIWOOM_SECRETKEY are not set.")
        print("Please create a .env file with the following content:")
        print("  KIWOOM_APPKEY=your_app_key")
        print("  KIWOOM_SECRETKEY=your_secret_key")
        sys.exit(1)

    # Set mock=True for mock trading
    kiwoom = Kiwoom(appkey=appkey, secretkey=secretkey, mock=True)

    # Start the strategy thread
    rsi_strategy = RSIStrategy(kiwoom)
    rsi_strategy.start()

    print("RSIStrategy started. Press Ctrl+C to stop.")

    try:
        while True:
            # Keep the main thread alive to handle signals
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping strategy...")
        # Signal the strategy thread to stop (if it has a stop condition)
        # For now, we just disconnect the websocket and exit.
        kiwoom.disconnect()
        print("Kiwoom disconnected.")
        # Wait for the strategy thread to finish
        rsi_strategy.join()
        print("Strategy thread stopped.")
        sys.exit(0)