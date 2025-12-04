import os
import pytest
from api.Kiwoom import Kiwoom


@pytest.fixture(scope="session")
def kiwoom_client():
    """Create a real Kiwoom client for integration tests.

    This fixture will skip the tests unless the environment variable
    `RUN_INTEGRATION` is set to "1". Credentials must be provided via
    `KIW_APPKEY` and `KIW_SECRET` environment variables.
    """
    if os.environ.get("RUN_INTEGRATION") != "1":
        pytest.skip("Integration tests disabled (set RUN_INTEGRATION=1 to enable)")

    appkey = os.environ.get("KIW_APPKEY")
    secret = os.environ.get("KIW_SECRET")
    if not appkey or not secret:
        pytest.skip("Kiwoom credentials not provided in env vars")

    # Use the real API (mock=False). The constructor will authenticate and
    # start a websocket thread; the fixture will attempt to stop the websocket
    # in teardown.
    client = Kiwoom(appkey, secret, mock=False)
    yield client

    # Teardown: try to stop websocket and clean up
    try:
        client.stop_websocket()
    except Exception:
        pass
