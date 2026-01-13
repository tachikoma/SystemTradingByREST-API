import pytest
import time


@pytest.mark.integration
def test_get_balance_and_orders(kiwoom_client):
    # 잔고 및 주문 조회는 읽기 전용 작업입니다; max_loops를 작게 설정하여 실행하세요
    balance = kiwoom_client.get_balance(max_loops=3)
    assert isinstance(balance, dict)

    orders = kiwoom_client.get_order(max_loops=3)
    assert isinstance(orders, list)

    # 호출 간 짧은 대기
    time.sleep(0.5)
