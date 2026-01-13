import os
import sys

# 스크립트를 직접 실행할 때 `api` 패키지를 임포트할 수 있도록 프로젝트 루트를 `sys.path`에 추가합니다.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from api.Kiwoom import Kiwoom


def test_limit_buy():
    inst = object.__new__(Kiwoom)
    inst.order = {}

    def mock_request(path, api_id, params, method="POST", extra_headers=None):
        # 매수 주문의 API 경로와 api_id를 기대
        assert path == "/api/dostk/ordr"
        assert api_id == "kt10000"
        # 성공 시 REST 클라이언트는 주문번호를 반환해야 함
        return ({"ord_no": "ORD12345"}, {})

    inst._request = mock_request

    ord_no = inst.send_order("rqname", "screen1", 0, "MOCK", 10, 1000, "00")

    assert ord_no == "ORD12345", "send_order should return the order number"
    assert "ORD12345" in inst.order, "Order dict should be updated with the new order"
    o = inst.order["ORD12345"]
    assert o['종목코드'] == "MOCK"
    assert o['주문수량'] == 10
    assert o['주문가격'] == 1000
    assert o['주문구분'] == 0

    print("test_limit_buy passed")


def test_market_sell():
    inst = object.__new__(Kiwoom)
    inst.order = {}

    def mock_request(path, api_id, params, method="POST", extra_headers=None):
        # 매도 주문의 API 경로와 api_id를 기대
        assert path == "/api/dostk/ordr"
        assert api_id == "kt10001"
        # 시장가 주문(order_classification '03')의 경우 ord_uv는 빈 문자열이어야 함
        assert params.get('ord_uv', None) == ""
        return ({"ord_no": "ORD6789"}, {})

    inst._request = mock_request

    ord_no = inst.send_order("rqname", "screen2", 1, "MOCK2", 5, 0, "03")

    assert ord_no == "ORD6789", "send_order should return the order number for market sell"
    assert "ORD6789" in inst.order

    print("test_market_sell passed")


if __name__ == '__main__':
    test_limit_buy()
    test_market_sell()
    print("All send_order tests passed.")
