import os
import sys
import time
import threading
from api.Kiwoom import Kiwoom
from strategy.RSIStrategy import RSIStrategy

if __name__ == '__main__':
    # For security, it's recommended to use environment variables for API keys.
    # Please set the following environment variables:
    # export KIWOOM_APPKEY=your_app_key
    # export KIWOOM_SECRETKEY=your_secret_key
    appkey = os.environ.get('KIWOOM_APPKEY')
    secretkey = os.environ.get('KIWOOM_SECRETKEY')

    if not appkey or not secretkey:
        print("Error: KIWOOM_APPKEY and KIWOOM_SECRETKEY environment variables are not set.")
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