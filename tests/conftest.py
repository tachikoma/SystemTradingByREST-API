import os
import time
import sys
import pathlib
import pytest

# pytest 실행 시 프로젝트 루트를 import 경로에 추가하여
# `from api.Kiwoom import Kiwoom` 같은 최상위 import가 동작하도록 함
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from api.Kiwoom import Kiwoom


@pytest.fixture(scope="session")
def kiwoom_client():
    """통합 테스트용 실제 Kiwoom 클라이언트를 생성합니다.

    이 픽스처는 환경변수 `RUN_INTEGRATION`이 "1"로 설정되지 않으면 테스트를 건너뜁니다.
    자격증명은 `KIW_APPKEY`와 `KIW_SECRET` 환경변수로 제공되어야 합니다.
    """
    if os.environ.get("RUN_INTEGRATION") != "1":
        pytest.skip("Integration tests disabled (set RUN_INTEGRATION=1 to enable)")

    # mock 모드 여부 결정: 여러 환경변수명 호환
    mode_env = os.environ.get("KIW_MODE") or os.environ.get("KIWOOM_MODE") or os.environ.get("KIW_MOCK")
    mock = False
    if mode_env:
        m = mode_env.lower()
        if m in ("mock", "m", "1", "true", "yes"):
            mock = True

    # 앱키/시크릿 선택: mock 전용 키 이름 또는 기본 키를 허용
    if mock:
        appkey = os.environ.get("KIW_MOCK_APPKEY") or os.environ.get("KIWOOM_MOCK_APPKEY") or os.environ.get("KIW_APPKEY")
        secret = os.environ.get("KIW_MOCK_SECRET") or os.environ.get("KIWOOM_MOCK_SECRETKEY") or os.environ.get("KIW_SECRET")
    else:
        appkey = os.environ.get("KIW_APPKEY") or os.environ.get("KIWOOM_REAL_APPKEY")
        secret = os.environ.get("KIW_SECRET") or os.environ.get("KIWOOM_REAL_SECRETKEY")

    if not appkey or not secret:
        pytest.skip("Kiwoom credentials not provided in env vars for selected mode")

    # Kiwoom 클라이언트 생성 (mock 플래그 전달)
    client = Kiwoom(appkey, secret, mock=mock)
    yield client

    # Teardown: stop websocket and wait for clean shutdown
    try:
        client.stop_websocket()
        # 최대 대기 시간 (초) - 환경변수로 조정 가능
        timeout = float(os.environ.get("KIW_WS_SHUTDOWN_TIMEOUT", 5))
        deadline = time.time() + timeout
        # websocket_thread가 존재하고 살아있다면 최대 timeout까지 짧게 폴링하여 종료를 기다립니다
        while getattr(client, 'websocket_thread', None) and client.websocket_thread.is_alive() and time.time() < deadline:
            time.sleep(0.1)
    except Exception:
        pass


# 각 테스트 함수 실행 전후에 master_list.db를 제거하여 테스트 간 상태 오염을 방지합니다.
@pytest.fixture(autouse=True)
def clean_master_list_db():
    files_to_remove = ['master_list.db', 'all_stocks_kiwoom.parquet', 'all_stocks_naver.parquet', 'universe.parquet']
    try:
        for f in files_to_remove:
            if os.path.exists(f):
                os.remove(f)
    except Exception:
        pass
    yield
    try:
        for f in files_to_remove:
            if os.path.exists(f):
                os.remove(f)
    except Exception:
        pass
