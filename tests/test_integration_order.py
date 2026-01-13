import os
import pytest


@pytest.mark.integration
def test_send_order_and_cancel(kiwoom_client):
    # 이 테스트는 실주문을 발생시킵니다(파괴적).
    # RUN_REAL_ORDERS=1로 명시적으로 설정하지 않으면 스킵됩니다. 테스트용(모의) 계정만 사용하세요.
    if os.environ.get("RUN_REAL_ORDERS") != "1":
        pytest.skip("Real-order tests disabled; set RUN_REAL_ORDERS=1 to enable (use test account only)")

    # 위험을 최소화하기 위해 소량 주문(수량 1)을 진행합니다. 필요시 필드를 조정하세요.
    ord_no = kiwoom_client.send_order("rq", "sc1", 0, "MOCK", 1, 1, "00")
    assert ord_no is not None
    print("Placed order:", ord_no)

    # 클라이언트가 취소 API를 제공하면 정리 차원에서 주문 취소를 시도합니다.
    if hasattr(kiwoom_client, "cancel_order"):
        try:
            kiwoom_client.cancel_order(ord_no)
        except Exception as e:
            print("Cancel attempt failed:", e)
