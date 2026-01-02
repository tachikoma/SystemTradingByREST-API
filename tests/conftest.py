import os
import pytest
from api.Kiwoom import Kiwoom


@pytest.fixture(scope="session")
def kiwoom_client():
    """통합 테스트용 실제 Kiwoom 클라이언트를 생성합니다.

    이 픽스처는 환경변수 `RUN_INTEGRATION`이 "1"로 설정되지 않으면 테스트를 건너뜁니다.
    자격증명은 `KIW_APPKEY`와 `KIW_SECRET` 환경변수로 제공되어야 합니다.
    """
    if os.environ.get("RUN_INTEGRATION") != "1":
        pytest.skip("Integration tests disabled (set RUN_INTEGRATION=1 to enable)")

    appkey = os.environ.get("KIW_APPKEY")
    secret = os.environ.get("KIW_SECRET")
    if not appkey or not secret:
        pytest.skip("Kiwoom credentials not provided in env vars")

    # 실제 API 사용(mock=False). 생성자에서 인증을 수행하고 웹소켓 스레드를 시작합니다.
    # 픽스처는 종료 시점에 웹소켓을 중지하려고 시도합니다.
    client = Kiwoom(appkey, secret, mock=False)
    yield client

    # Teardown: try to stop websocket and clean up
    try:
        client.stop_websocket()
    except Exception:
        pass
