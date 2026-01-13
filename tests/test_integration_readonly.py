import pytest


@pytest.mark.integration
def test_get_price_data_integration(kiwoom_client):
    # 읽기 전용 통합 테스트: 소량의 시세 데이터를 조회합니다
    df = kiwoom_client.get_price_data("005930", max_loops=2, max_retries=3, retry_delay=1)
    assert df is not None
    assert not df.empty
    assert "close" in df.columns
