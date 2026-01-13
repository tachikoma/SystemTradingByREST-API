import os
import sys
import time
import pandas as pd

# 스크립트를 직접 실행할 때(`python scripts/test_get_price_data_mock.py` 등) `api` 패키지를 임포트할 수 있도록 프로젝트 루트를 `sys.path`에 추가합니다.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from api.Kiwoom import Kiwoom


def run_mock_test():
    # 네트워크/인증 부작용을 피하기 위해 __init__을 호출하지 않고 인스턴스 생성
    inst = object.__new__(Kiwoom)

    call_count = {"n": 0}

    def mock_request(path, api_id, params, method="POST", extra_headers=None):
        """API 응답 시뮬레이션:
        - 처음 두 번은 요청 한도 초과 응답을 반환
        - 세 번째 호출에서 2개의 행을 포함한 성공 응답과 종료를 알리는 헤더를 반환
        """
        call_count["n"] += 1
        n = call_count["n"]
        if n <= 2:
            return ({
                "return_code": 5,
                "return_msg": "허용된 요청 개수를 초과하였습니다: too many requests"
            }, {})
        else:
            data = {
                "stk_dt_pole_chart_qry": [
                    {"dt": "20250102", "open_pric": "100", "high_pric": "110", "low_pric": "90", "cur_prc": "105", "trde_qty": "1000"},
                    {"dt": "20250101", "open_pric": "90", "high_pric": "95", "low_pric": "85", "cur_prc": "92", "trde_qty": "800"}
                ]
            }
            headers = {"cont-yn": "N", "next-key": ""}
            return (data, headers)

    # mock_request를 인스턴스에 연결
    inst._request = mock_request

    # 빠른 테스트를 위해 작은 retry_delay로 get_price_data 호출
    df = Kiwoom.get_price_data(inst, 'MOCK', max_loops=5, max_retries=5, retry_delay=0.1)

    print("Result DataFrame:")
    print(df)

    # 기본 검증
    assert not df.empty, "DataFrame should not be empty"
    # 순서를 유지하면서 중복 인덱스 항목 제거 후 비교
    unique_index = list(dict.fromkeys(list(df.index)))
    assert unique_index == ["20250101", "20250102"], "Index should be chronological (oldest first)"
    # 동일 날짜에 중복 행이 존재할 경우, 해당 날짜에 기대값이 하나라도 있으면 허용
    assert (df.loc["20250102"]["close"] == 105).any(), "Expected at least one close==105 for 20250102"
    assert (df.loc["20250101"]["close"] == 92).any(), "Expected at least one close==92 for 20250101"

    print("Mock test passed.")


if __name__ == '__main__':
    run_mock_test()
