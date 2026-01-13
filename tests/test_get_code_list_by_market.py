import pytest


def test_get_code_list_by_market(kiwoom_client):
    """통합(integration) 테스트: 운영 모드에서 종목 코드 리스트를 조회합니다.

    이 테스트는 `tests/conftest.py`의 `kiwoom_client` 픽스처를 사용하며,
    환경변수 `RUN_INTEGRATION=1`, `KIW_APPKEY`, `KIW_SECRET`이 설정되어 있어야 실제로 실행됩니다.
    """
    codes = kiwoom_client.get_code_list_by_market('0')  # KOSPI
    codes.append(kiwoom_client.get_code_list_by_market('10'))  # KOSDAQ

    assert isinstance(codes, list)
    # 실제 API 호출일 경우 적어도 한 종목 이상 반환되어야 함
    assert len(codes) > 0
