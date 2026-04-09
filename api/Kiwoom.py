import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import json
import datetime
from zoneinfo import ZoneInfo
import time
import pandas as pd
from util.const import *
from util.time_helper import get_korea_time
import asyncio
import websockets
import threading
from util.logging_config import get_logger
from util.notifier import notify_on_exception
from util.db_helper import upsert_purchase_date, delete_purchase_date

# Kiwoom API 응답에서 사용되는 요청 한도 초과 메시지 키워드
RATE_LIMIT_MSG = "허용된 요청 개수를 초과하였습니다"

# 참고: 로깅 설정은 애플리케이션(`main.py`)에서 환경변수 로드 후 초기화해야 합니다.
# 모듈 임포트 시점에 `configure_logging()`을 호출하면 초기화 순서가 불명확해질 수 있으므로 지양합니다.
logger = get_logger('kiwoom')


class Kiwoom:
    def __init__(self, appkey, secretkey, mock=False):
        self.mock = mock
        if mock:
            self.base_url = "https://mockapi.kiwoom.com"
            self.socket_url = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"
        else:
            self.base_url = "https://api.kiwoom.com"
            self.socket_url = "wss://api.kiwoom.com:10000/api/dostk/websocket"

        self.appkey = appkey
        self.secretkey = secretkey
        self.access_token = None
        self.token_expires_in = None

        # HTTP 세션과 재시도 정책 설정
        self.session = requests.Session()
        retry_strategy = Retry(
            total=5,
            backoff_factor=0.5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(["GET", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        self.order = {}
        self.balance = {}
        self.universe_realtime_transaction_info = {}

        self.websocket = None
        self.is_websocket_connected = False
        self.websocket_thread = None
        self.asyncio_loop = None
        self._websocket_stop_event = threading.Event()
        
        # 실시간 데이터 등록 정보 저장 (재연결 시 사용)
        self._real_reg_info = None

        self._authenticate()
        self._start_websocket_thread()
        # 웹소켓 로그인 재시도 카운터
        self._websocket_login_retries = 0
        self._websocket_max_login_retries = 3
        # 중복 LOGIN 전송을 방지하기 위한 웹소켓 로그인 상태 플래그
        self._websocket_logged_in = False
        self._websocket_login_sent = False
        # `asyncio.Lock`은 웹소켓 이벤트 루프 스레드 내에서 생성됩니다
        self._login_send_lock = None

    def _authenticate(self):
        """
        액세스 토큰을 요청하여 설정합니다.
        """
        url = f"{self.base_url}/oauth2/token"
        headers = {"content-type": "application/json"}
        data = {
            "grant_type": "client_credentials",
            "appkey": self.appkey,
            "secretkey": self.secretkey
        }
        try:
            res = self.session.post(url, headers=headers, data=json.dumps(data), timeout=15)
            if res.status_code == 200:
                response_data = res.json()
            else:
                logger.error(f"Authentication failed: {res.text}")
                self.access_token = None
                return
        except requests.exceptions.RequestException as e:
            logger.error(f"Authentication request failed: {e}")
            self.access_token = None
            return

        if response_data:
            if return_code := response_data.get("return_code") != 0:
                logger.warning(response_data.get("return_msg"))
            self.access_token = response_data["token"]
                # `token_expires_in`을 한국 시간대(tz-aware)로 설정하여 `get_korea_time()`과 일치시킵니다
            try:
                self.token_expires_in = datetime.datetime.strptime(response_data["expires_dt"], '%Y%m%d%H%M%S').replace(tzinfo=ZoneInfo("Asia/Seoul"))
            except Exception:
                # 폴백: 파싱에 실패하면 naive datetime으로 설정하되, 가능하면 timezone-aware를 사용합니다
                self.token_expires_in = datetime.datetime.strptime(response_data["expires_dt"], '%Y%m%d%H%M%S')
            logger.info("Authentication successful.")
        else:
            # handled in exception branch or above
            self.access_token = None

    def _request(self, path, api_id, params, method="POST", extra_headers=None):
        """
        API 요청을 수행하는 래퍼입니다.
        """
        # TODO: 토큰 갱신 로직 추가
        # 한국 시간 헬퍼(`get_korea_time()`)를 일관되게 사용
        if self.token_expires_in is None or self.token_expires_in < get_korea_time():
            self._authenticate()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "api-id": api_id
        }

        if extra_headers:
            headers.update(extra_headers)

        url = f"{self.base_url}{path}"
        try:
            if method == "POST":
                res = self.session.post(url, headers=headers, data=json.dumps(params), timeout=15)
            else:  # GET 요청 처리
                res = self.session.get(url, headers=headers, params=params, timeout=15)

            if res.status_code == 200:
                # 정상 응답
                try:
                    return res.json(), res.headers
                except Exception:
                    logger.error(f"Failed to parse JSON response from {url}")
                    return None, res.headers
            else:
                logger.error(f"Request failed: {res.text}, {res.headers}")
                try:
                    return res.json(), res.headers
                except Exception:
                    return None, res.headers
        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP request exception for {url}: {e}")
            return None, {}



    def get_code_list_by_market(self, market_type, max_retries=3, retry_delay=0.5):
        """
        지정한 마켓 유형에 대한 종목 코드와 이름 목록을 반환합니다.
        `market_type`: '0' (KOSPI), '10' (KOSDAQ) 등

        재시도 로직: API에서 요청 한도 초과 응답(return_code == 5 및
        return_msg에 RATE_LIMIT_MSG 포함)을 반환할 때만 재시도합니다.
        """
        path = "/api/dostk/stkinfo"
        api_id = "ka10099"
        params = {"mrkt_tp": market_type}

        code_list = []

        for attempt in range(max_retries):
            res_data, res_headers = self._request(path=path, api_id=api_id, params=params, method="POST")

            if res_data and isinstance(res_data, dict) and "list" in res_data:
                for item in res_data["list"]:
                    code_list.append({"code": item.get("code"), "name": item.get("name")})
                return code_list

            # 요청 한도 초과 응답일 경우에만 재시도
            if (
                res_data is not None and
                isinstance(res_data, dict) and
                "return_code" in res_data and
                res_data.get("return_code") == 5 and
                RATE_LIMIT_MSG in res_data.get("return_msg", "")
            ):
                logger.warning(f"API rate limit exceeded for market {market_type} (attempt {attempt+1}/{max_retries}), retrying after {retry_delay}s...")
                time.sleep(retry_delay)
                continue

            # 기타 실패는 재시도하지 않음
            logger.warning(f"Failed to retrieve code list for market {market_type} or unexpected response format: {res_data}")
            break

        logger.error(f"All retries failed for market {market_type}.")
        return code_list

    def get_master_code_name(self, code, max_retries=3, retry_delay=0.5):
        """
        종목 코드로부터 종목명을 조회합니다. API 요청 한도 초과 응답일 경우 재시도합니다.
        """
        path = "/api/dostk/stkinfo"
        api_id = "ka10100"
        params = {"stk_cd": code}

        for attempt in range(max_retries):
            res_data, res_headers = self._request(path=path, api_id=api_id, params=params, method="POST")
            if res_data and isinstance(res_data, dict) and "name" in res_data:
                return res_data["name"]
            # 리밋 초과 조건에만 재시도
            if (
                res_data is not None and
                isinstance(res_data, dict) and
                "return_code" in res_data and
                res_data["return_code"] == 5 and
                RATE_LIMIT_MSG in res_data.get("return_msg", "")
            ):
                logger.warning(f"API rate limit exceeded (attempt {attempt+1}), retrying after {retry_delay}s...")
                time.sleep(retry_delay)
                continue
            # 기타 실패는 즉시 종료
            logger.warning(f"Failed to retrieve stock name for code {code} or unexpected response format: {res_data}")
            break
        logger.error(f"All retries failed for code {code}.")
        return None

    def get_master_code_name_safe(self, code):
        """예외를 흘려보내지 않는 안전한 래퍼: 내부적으로 get_master_code_name을 호출하고
        실패 시 None을 반환합니다."""
        try:
            return self.get_master_code_name(code)
        except Exception as e:
            logger.exception("get_master_code_name_safe 실패: %s", e)
            return None

    def get_stock_info(self, code, max_retries=3, retry_delay=0.5):
        """
        종목의 상세 기본정보를 조회합니다 (ka10001).
        당일 거래량, 거래대금, 등락률, 현재가, 외국인비율 등을 포함합니다.
        
        Returns:
            dict: 종목 상세 정보
                - code: 종목코드
                - name: 종목명
                - cur_prc: 현재가 (String)
                - trde_qty: 거래량 (String)
                - trde_amt: 거래대금 (String, 백만원 단위 - 계산값)
                - flu_rt: 등락률 (String, %)
                - list_cnt: 상장주식수 (String)
                - mrkt_cap: 시가총액 (String, 억원 단위 - API 원본값)
                - for_exh_rt: 외국인비율 (String, %)
                또는 None (실패 시)
        
        주의: 시가총액은 억원 단위로 반환되므로, 백만원 단위 변환이 필요하면 ×100 해야 함
        """
        path = "/api/dostk/stkinfo"
        api_id = "ka10001"
        params = {"stk_cd": code}

        for attempt in range(max_retries):
            res_data, res_headers = self._request(path=path, api_id=api_id, params=params, method="POST")
            
            # 성공: stk_cd 키가 있으면 데이터 변환하여 반환
            if res_data and isinstance(res_data, dict) and "stk_cd" in res_data:
                # API 응답 형식을 표준 형식으로 변환
                # 어제 로그 기준: 'stk_cd', 'stk_nm', 'cur_prc', 'trde_qty', 'flu_rt', 'for_exh_rt', 'mac' 등
                try:
                    # 거래대금 계산: 거래량 * 현재가 / 1,000,000 (백만원 단위)
                    trde_qty = abs(float(res_data.get('trde_qty', 0)))
                    cur_prc = abs(float(res_data.get('cur_prc', '0').replace('+', '').replace('-', '')))
                    trde_amt = str(int((trde_qty * cur_prc) / 1_000_000))  # 백만원 단위
                    
                    # 시가총액은 'mac' 키 사용 (API는 억원 단위로 반환)
                    mrkt_cap = res_data.get('mac', '0')
                    
                    # 상장주식수는 'flo_stk' 또는 계산 (시가총액 / 현재가)
                    flo_stk = res_data.get('flo_stk', '')
                    if flo_stk and flo_stk.strip():
                        list_cnt = int(flo_stk) * 1000  # 천주 단위이므로 1,000 곱함
                    else:
                        # 계산: 시가총액(억원) * 100,000,000 / 현재가
                        if cur_prc > 0:
                            list_cnt = str(int(float(mrkt_cap) * 100_000_000 / cur_prc))
                        else:
                            list_cnt = '0'
                    
                    return {
                        'code': res_data.get('stk_cd'),
                        'name': res_data.get('stk_nm'),
                        'cur_prc': res_data.get('cur_prc', '0').replace('+', '').replace('-', ''),
                        'trde_qty': res_data.get('trde_qty', '0'),
                        'trde_amt': trde_amt,
                        'flu_rt': res_data.get('flu_rt', '0').replace('+', ''),
                        'list_cnt': list_cnt,
                        'mrkt_cap': mrkt_cap,
                        'for_exh_rt': res_data.get('for_exh_rt', '0').replace('+', '')
                    }
                except (ValueError, TypeError) as e:
                    logger.warning(f"Failed to parse stock info for {code}: {e}")
                    # 파싱 실패 시에도 원본 데이터 반환 시도
                    pass
            
            # Rate limit 체크 및 재시도
            if (
                res_data is not None and
                isinstance(res_data, dict) and
                "return_code" in res_data and
                res_data.get("return_code") == 5 and
                RATE_LIMIT_MSG in res_data.get("return_msg", "")
            ):
                logger.warning(f"API rate limit exceeded for {code} (attempt {attempt+1}), retrying after {retry_delay}s...")
                time.sleep(retry_delay)
                continue
            
            # 기타 실패
            logger.warning(f"Failed to retrieve stock info for {code}: {res_data}")
            break
        
        logger.error(f"All retries failed for code {code}")
        return None

    def get_price_data(self, code, cont_yn='N', max_loops=1, max_retries=3, retry_delay=0.5):
        """
        특정 종목의 일별 OHLCV(시가/고가/저가/종가/거래량) 히스토리 데이터를 조회합니다.

        매개변수:
        - `code`: 종목 코드
        - `cont_yn`: 연속 조회 플래그 기본값
        - `max_loops`: 최대 페이지 조회 횟수
        - `max_retries`: 페이지별 재시도 횟수(레이트 리밋 응답 시)
        - `retry_delay`: 레이트 리밋 발생 시 재시도 간 지연(초)

        각 페이지 요청은 API가 레이트 리밋 응답(return_code == 5 및
        return_msg에 레이트 리밋 문구 포함)을 반환할 때만 재시도합니다.
        그 외의 실패는 조회를 중단합니다.
        """
        path = "/api/dostk/chart"
        api_id = "ka10081"
        all_ohlcv_data = {
            'date': [], 'open': [], 'high': [], 'low': [], 'close': [], 'volume': []
        }

        next_key = ""
        loop_count = 0
        base_dt = datetime.date.today().strftime("%Y%m%d")  # 첫 조회는 오늘부터
        
        while loop_count < max_loops:
            loop_count += 1
            
            params = {
                "stk_cd": code,
                "base_dt": base_dt,
                "upd_stkpc_tp": "1"  # 수정된(조정된) 주가
            }
            extra_headers = {}
            if next_key:
                extra_headers["next-key"] = next_key

            # 페이지 단위 요청: 재시도는 요청 한도(rate-limit) 응답일 때만 수행
            page_res = None
            page_headers = None
            for attempt in range(max_retries):
                page_res, page_headers = self._request(path=path, api_id=api_id, params=params, method="POST", extra_headers=extra_headers)

                # 페이지 요청 성공
                if page_res and isinstance(page_res, dict) and "stk_dt_pole_chart_qry" in page_res:
                    break

                # 재시도 여부 판단: rate-limit 응답인지 확인
                if (
                    page_res is not None and
                    isinstance(page_res, dict) and
                    "return_code" in page_res and
                    page_res.get("return_code") == 5 and
                    RATE_LIMIT_MSG in page_res.get("return_msg", "")
                ):
                    logger.warning(f"API rate limit exceeded for code {code} (attempt {attempt+1}/{max_retries}), retrying after {retry_delay}s...")
                    time.sleep(retry_delay)
                    continue

                # 기타 실패: 재시도하지 않음
                logger.error(f"Failed page request for code {code} (attempt {attempt+1}): {page_res}")
                page_res = None
                break

            # 페이지 요청이 최종 실패하면 조회를 중단합니다
            if not page_res or not isinstance(page_res, dict) or "stk_dt_pole_chart_qry" not in page_res:
                logger.warning(f"Stopping retrieval for code {code} due to page request failure.")
                break

            # 수신된 항목을 처리합니다
            oldest_date = None
            for item in page_res["stk_dt_pole_chart_qry"]:
                all_ohlcv_data['date'].append(item['dt'])
                all_ohlcv_data['open'].append(int(item['open_pric']))
                all_ohlcv_data['high'].append(int(item['high_pric']))
                all_ohlcv_data['low'].append(int(item['low_pric']))
                all_ohlcv_data['close'].append(int(item['cur_prc']))
                all_ohlcv_data['volume'].append(int(item['trde_qty']))
                # 가장 오래된 날짜 추적 (리스트의 마지막 항목이 가장 오래된 날짜)
                oldest_date = item['dt']

            # 헤더에서 연속 조회 관련 플래그를 갱신합니다
            if page_headers:
                cont_yn = page_headers.get("cont-yn", cont_yn)
                next_key = page_headers.get("next-key", "")

            # cont_yn이 'Y'가 아니면 더 이상 데이터가 없으므로 종료
            if cont_yn != "Y":
                logger.info(f"No more data available for code {code} (loop {loop_count}/{max_loops})")
                break
            
            # 다음 조회를 위해 base_dt를 가장 오래된 날짜의 전날로 설정
            if oldest_date:
                try:
                    oldest_dt = datetime.datetime.strptime(oldest_date, "%Y%m%d")
                    prev_day = oldest_dt - datetime.timedelta(days=1)
                    base_dt = prev_day.strftime("%Y%m%d")
                    logger.debug(f"Next base_dt for {code}: {base_dt} (previous oldest: {oldest_date})")
                except Exception as e:
                    logger.warning(f"Failed to calculate next base_dt for {code}: {e}")
            
            # 다음 페이지 조회가 있으므로 레이트 리밋 방지를 위해 대기
            time.sleep(0.2)

        df = pd.DataFrame(all_ohlcv_data, columns=['open', 'high', 'low', 'close', 'volume'], index=all_ohlcv_data['date'])
        return df[::-1]

    def get_deposit(self, max_retries=3, retry_delay=0.5):
        """
        REST API(kt00001)를 사용해 주문 가능 예수금을 조회합니다.
        
        Args:
            max_retries: 최대 재시도 횟수 (기본값: 3)
            retry_delay: 재시도 대기 시간(초) (기본값: 1)
        """
        path = "/api/dostk/acnt"
        api_id = "kt00001"
        params = {"qry_tp": "3"}  # 3: 추정 조회

        res_data = None
        for attempt in range(max_retries):
            res_data, _ = self._request(path=path, api_id=api_id, params=params, method="POST")

            if res_data and isinstance(res_data, dict) and "ord_alow_amt" in res_data:
                deposit = int(res_data["ord_alow_amt"])
                return deposit

            # Rate limit 체크
            if (
                res_data is not None and
                isinstance(res_data, dict) and
                "return_code" in res_data and
                res_data.get("return_code") == 5 and
                RATE_LIMIT_MSG in res_data.get("return_msg", "")
            ):
                logger.warning(f"API rate limit exceeded for get_deposit (attempt {attempt+1}/{max_retries}), retrying after {retry_delay}s...")
                time.sleep(retry_delay)
                continue

            logger.error(f"Failed to retrieve deposit (attempt {attempt+1}/{max_retries}): {res_data}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)

        logger.warning(f"Failed to retrieve deposit after {max_retries} attempts: {res_data}")
        return 0

    def send_order(self, rqname, screen_no, order_type, code, order_quantity, order_price, order_classification, origin_order_number=""):
        """
        Kiwoom REST API를 사용해 매수/매도 주문을 전송합니다.
        `rqname`, `screen_no`는 REST API에서 직접 사용되지 않지만 호환성을 위해 유지합니다.
        `order_type`: 0=매수, 1=매도
        `order_classification`: '00'=지정가, '03'=시장가
        
        Returns:
            dict: 주문 결과 딕셔너리
                - success (bool): 주문 성공 여부
                - order_no (str): 주문번호 (성공 시)
                - code (str): 종목코드
                - order_type (int): 주문유형 (0=매수, 1=매도)
                - quantity (int): 주문수량
                - price (int): 주문가격
                - error_code (str): 에러코드 (실패 시)
                - error_message (str): 에러메시지 (실패 시)
                - raw_response (dict): API 원본 응답
        """
        path = "/api/dostk/ordr"
        
        # 기본 결과 딕셔너리
        result = {
            'success': False,
            'code': code,
            'order_type': order_type,
            'quantity': order_quantity,
            'price': order_price,
            'order_classification': order_classification
        }
        
        api_id_map = {
            0: "kt10000",  # 매수 주문
            1: "kt10001"   # 매도 주문
            # TODO: 수정용 kt10002, 취소용 kt10003 추가
        }
        api_id = api_id_map.get(order_type)
        if not api_id:
            error_msg = f"지원하지 않는 주문유형입니다: {order_type}"
            logger.warning(error_msg)
            result.update({
                'error_code': 'INVALID_ORDER_TYPE',
                'error_message': error_msg
            })
            return result

        # order_classification을 trde_tp로 매핑
        trde_tp_map = {
            "00": "0", # 지정가 (Limit order) -> 보통
            "03": "3"  # 시장가 (Market order)
            # Add other mappings if needed
        }
        trde_tp = trde_tp_map.get(order_classification)
        if not trde_tp:
            error_msg = f"지원하지 않는 주문구분입니다: {order_classification}"
            logger.warning(error_msg)
            result.update({
                'error_code': 'INVALID_ORDER_CLASSIFICATION',
                'error_message': error_msg
            })
            return result

        # 시장가 주문의 경우 ord_uv는 빈 문자열이어야 합니다
        order_uv_param = str(order_price) if trde_tp != "3" else ""

        params = {
            "dmst_stex_tp": "KRX",  # 현재는 KRX로 하드코딩
            "stk_cd": code,
            "ord_qty": str(order_quantity),
            "ord_uv": order_uv_param,
            "trde_tp": trde_tp,
            "cond_uv": ""  # 원래 시그니처에서 처리하지 않음
        }

        res_data, _ = self._request(path=path, api_id=api_id, params=params, method="POST")
        result['raw_response'] = res_data

        if res_data and isinstance(res_data, dict) and "ord_no" in res_data:
            logger.info(f"Order successful. Order number: {res_data['ord_no']}")
            # Update self.order with the new order details
            # 키를 종목코드로 사용하여 RSIStrategy 및 _handle_order_execution과 일관성 유지
            order_type_str = '매수' if order_type == 0 else '매도'
            self.order[code] = {
                '종목코드': code,
                '주문수량': order_quantity,
                '주문가격': order_price,
                '주문구분': order_type_str,  # 문자열로 통일
                '주문번호': res_data['ord_no'],
                '주문상태': '접수',
                '미체결수량': order_quantity,  # 초기에는 전량 미체결
                '체결량': 0
            }
            result.update({
                'success': True,
                'order_no': res_data['ord_no']
            })
            return result
        else:
            # 실패 시 상세 에러 정보 추출
            error_code = res_data.get('return_code', 'UNKNOWN') if res_data else 'NO_RESPONSE'
            error_message = res_data.get('return_msg', '알 수 없는 오류') if res_data else 'API 응답 없음'
            
            logger.error(f"Order failed for code {code}: {error_code} - {error_message}")
            result.update({
                'error_code': str(error_code),
                'error_message': error_message
            })
            return result

    def get_order(self, cont_yn='N', max_loops=200, max_retries=3, retry_delay=0.5):
        """
        REST API(ka10075)를 사용해 미체결 주문 목록을 조회합니다.
        """
        path = "/api/dostk/acnt"
        api_id = "ka10075"
        
        all_unexecuted_orders = []
        next_key = ""
        first = True
        loop_count = 0
        while True:
            params = {
                "all_stk_tp": "0", # 0: 전체, 1: 종목 (All stocks)
                "trde_tp": "0",    # 0: 전체, 1: 매도, 2: 매수 (All trade types)
                "stk_cd": "",      # Empty for all stocks
                "stex_tp": "0"     # 0: 통합, 1: KRX, 2: NXT (Integrated exchange)
            }
            extra_headers = {}
            if next_key:
                extra_headers["next-key"] = next_key
            
            # Per-page request with retries only on rate-limit responses
            page_res = None
            page_headers = None
            for attempt in range(max_retries):
                page_res, page_headers = self._request(path=path, api_id=api_id, params=params, method="POST", extra_headers=extra_headers)

                if page_res and isinstance(page_res, dict) and "oso" in page_res:
                    break

                if (
                    page_res is not None and
                    isinstance(page_res, dict) and
                    "return_code" in page_res and
                    page_res.get("return_code") == 5 and
                    RATE_LIMIT_MSG in page_res.get("return_msg", "")
                ):
                    logger.warning(f"API rate limit exceeded for get_order (attempt {attempt+1}/{max_retries}), retrying after {retry_delay}s...")
                    time.sleep(retry_delay)
                    continue

                logger.error(f"Failed page request for get_order (attempt {attempt+1}): {page_res}")
                page_res = None
                break

            if not page_res or not isinstance(page_res, dict) or "oso" not in page_res:
                logger.warning(f"Stopping retrieval of unexecuted orders due to page request failure.")
                break

            for item in page_res["oso"]:
                order_info = {
                    '종목코드': item.get('stk_cd', '').strip(),
                    '종목명': item.get('stk_nm', '').strip(),
                    '주문번호': item.get('ord_no', '').strip(),
                    '주문상태': item.get('ord_stt', '').strip(),
                    '주문수량': int(item.get('ord_qty', '0')),
                    '주문가격': int(item.get('ord_pric', '0')),
                    '현재가': int(item.get('cur_prc', '0').replace('+', '').replace('-', '')),  # 부호 제거
                    '주문구분': item.get('io_tp_nm', '').strip(),
                    '미체결수량': int(item.get('oso_qty', '0')),
                    '체결량': int(item.get('cntr_qty', '0')),
                    '주문시간': item.get('tm', '').strip(),
                    '당일매매수수료': int(item.get('tdy_trde_cmsn', '0')),
                    '당일매매세금': int(item.get('tdy_trde_tax', '0'))
                }
                all_unexecuted_orders.append(order_info)

            # 헤더에서 연속 조회 관련 플래그를 갱신합니다
            if page_headers:
                cont_yn = page_headers.get("cont-yn", cont_yn)
                next_key = page_headers.get("next-key", "")
            if not first and cont_yn != "Y":
                break
            loop_count += 1
            if loop_count >= max_loops:
                logger.info(f"Loop limit ({max_loops}) reached for get_order, breaking")
                break
            first = False
            
            # 다음 페이지 조회가 있으므로 레이트 리밋 방지를 위해 대기
            if cont_yn == "Y":
                time.sleep(0.2)
        self.order = {order['종목코드']: order for order in all_unexecuted_orders}
        return all_unexecuted_orders

    def get_balance(self, cont_yn='N', max_loops=200, max_retries=3, retry_delay=0.5):
        """
        REST API(kt00018)를 사용해 계좌 잔액과 보유 종목(포지션)을 조회합니다.
        """
        path = "/api/dostk/acnt"
        api_id = "kt00018"
        
        all_holdings = []
        next_key = ""
        first = True
        loop_count = 0
        while True:
            params = {
                "qry_tp": "1",        # 1: 합산 (Combined), 2: 개별 (Individual)
                "dmst_stex_tp": "KRX" # KRX: 한국거래소, NXT: 넥스트트레이드
            }
            extra_headers = {}
            if next_key:
                extra_headers["next-key"] = next_key
            
            # 페이지 단위 요청: 재시도는 요청 한도(rate-limit) 응답일 때만 수행
            page_res = None
            page_headers = None
            for attempt in range(max_retries):
                page_res, page_headers = self._request(path=path, api_id=api_id, params=params, method="POST", extra_headers=extra_headers)

                if page_res and isinstance(page_res, dict) and "acnt_evlt_remn_indv_tot" in page_res:
                    break

                if (
                    page_res is not None and
                    isinstance(page_res, dict) and
                    "return_code" in page_res and
                    page_res.get("return_code") == 5 and
                    RATE_LIMIT_MSG in page_res.get("return_msg", "")
                ):
                    logger.warning(f"API rate limit exceeded for get_balance (attempt {attempt+1}/{max_retries}), retrying after {retry_delay}s...")
                    time.sleep(retry_delay)
                    continue

                logger.error(f"Failed page request for get_balance (attempt {attempt+1}): {page_res}")
                page_res = None
                break

            if not page_res or not isinstance(page_res, dict) or "acnt_evlt_remn_indv_tot" not in page_res:
                logger.warning(f"Stopping retrieval of balance due to page request failure.")
                break

            for item in page_res["acnt_evlt_remn_indv_tot"]:
                holding_info = {
                    '종목명': item.get('stk_nm', '').strip(),
                    '보유수량': int(item.get('rmnd_qty', '0')),
                    '매입가': int(item.get('pur_pric', '0')),
                    '수익률': float(item.get('prft_rt', '0.0')),
                    '현재가': int(item.get('cur_prc', '0')),
                    '매입금액': int(item.get('pur_amt', '0')),
                    '매매가능수량': int(item.get('trde_able_qty', '0'))
                }
                all_holdings.append((item.get('stk_cd', '').lstrip('A').rstrip(), holding_info))

            # 헤더에서 연속 조회 관련 플래그를 갱신합니다
            if page_headers:
                cont_yn = page_headers.get("cont-yn", cont_yn)
                next_key = page_headers.get("next-key", "")
            if not first and cont_yn != "Y":
                break
            loop_count += 1
            if loop_count >= max_loops:
                logger.info(f"Loop limit ({max_loops}) reached for get_balance, breaking")
                break
            first = False
            
            # 다음 페이지 조회가 있으므로 레이트 리밋 방지를 위해 대기
            if cont_yn == "Y":
                time.sleep(0.2)
        # 기존 balance에서 매수일 정보 보존 (API 조회로 초기화되지 않도록)
        existing_dates = {code: info.get('매수일') for code, info in self.balance.items()}
        self.balance = {code: info for code, info in all_holdings}
        for code in self.balance:
            preserved = existing_dates.get(code)
            self.balance[code]['매수일'] = preserved if preserved else None
        return self.balance

    def set_real_reg(self, str_code_list, str_opt_type='0'):
        """
        WebSocket을 통해 실시간 데이터 구독을 등록합니다.

        인자:
            `str_code_list`: ';'로 구분된 종목코드 문자열 (예: '005930;000660')
            `str_opt_type`: '0' (기존 등록 항목 해지 후 추가), '1' (기존 등록 유지 후 추가). 기본값 '0'
        """
        codes = str_code_list.split(';')
        
        # 등록 정보 저장 (재연결 시 사용)
        self._real_reg_info = {
            'code_list': str_code_list,
            'opt_type': str_opt_type
        }
        
        # REST API에서는 종목 코드와 타입으로 구독합니다
        # '0B': 주식체결 (시세 정보)
        # '00': 주문체결 (주문/체결 정보)
        
        subscription_data = [
            {
                'item': codes,
                'type': ['0B']  # 주식 실시간 체결 데이터
            },
            {
                'item': [],  # 주문체결은 종목코드 불필요 (계좌 전체)
                'type': ['00']  # 주문체결 실시간 데이터
            }
        ]

        message = {
            'trnm': 'REG',
            'grp_no': '1',  # 기본 그룹 번호 사용
            'refresh': str_opt_type, # '0' or '1'
            'data': subscription_data
        }
        
        if self.is_websocket_connected and self.asyncio_loop:
            asyncio.run_coroutine_threadsafe(self._send_websocket_message(message), self.asyncio_loop)
            logger.info(f"Real-time registration sent for codes: {str_code_list}")
        else:
            logger.warning("WebSocket is not connected. Cannot register for real-time data.")

    def _on_receive_real_data(self, s_code, real_type, real_data):
        """
        이 메서드는 이제 WebSocket 메시지 핸들러에서 호출됩니다.
        `real_type`은 체결 데이터의 경우 '0B'입니다.
        `real_data`는 FID와 값의 딕셔너리 형식입니다.
        """
        if real_type == "0B": # 주식체결
            if s_code not in self.universe_realtime_transaction_info:
                self.universe_realtime_transaction_info[s_code] = {}
            
            # 필요 시 원래의 const.py와 일관성을 위해 FID 문자열을 정수 키로 매핑할 수 있으나,
            # 현재는 수신된 문자열 키를 그대로 사용합니다.
            self.universe_realtime_transaction_info[s_code].update({
                "체결시간": real_data.get('20'),
                "현재가": int(real_data.get('10', '0').replace('+', '').replace('-', '')),
                "고가": int(real_data.get('17', '0').replace('+', '').replace('-', '')),
                "시가": int(real_data.get('16', '0').replace('+', '').replace('-', '')),
                "저가": int(real_data.get('18', '0').replace('+', '').replace('-', '')),
                "(최우선)매도호가": int(real_data.get('27', '0').replace('+', '').replace('-', '')),
                "(최우선)매수호가": int(real_data.get('28', '0').replace('+', '').replace('-', '')),
                "누적거래량": int(real_data.get('13', '0'))
            })
            logger.debug(f"Real-time update for {s_code}: {self.universe_realtime_transaction_info[s_code]}")
        
        elif real_type == "00":  # 주문체결
            # 주문체결 실시간 데이터를 처리하여 order와 balance를 동기화
            self._handle_order_execution(s_code, real_data)
            logger.info(f"Order execution received for {s_code}")
        
        # Add other real_type handlers if needed

    def _handle_order_execution(self, s_code, real_data):
        """
        주문체결 실시간 데이터를 처리하여 order와 balance를 업데이트합니다.
        
        주요 FID:
        - 9201: 계좌번호
        - 9203: 주문번호
        - 9001: 종목코드
        - 913: 주문상태 (접수/확인/체결/취소/거부)
        - 900: 주문수량
        - 911: 체결수량
        - 902: 미체결수량
        - 905: 주문구분 (+매수/-매도)
        - 10: 현재가
        """
        def safe_int(value, default=0):
            """빈 문자열이나 None을 안전하게 int로 변환"""
            if not value or (isinstance(value, str) and value.strip() == ''):
                return default
            try:
                return int(str(value).replace('+', '').replace('-', '').strip())
            except (ValueError, AttributeError):
                return default
        
        try:
            order_no = real_data.get('9203', '').strip()
            code = real_data.get('9001', '').strip()
            order_status = real_data.get('913', '').strip()  # 접수/확인/체결
            order_qty = safe_int(real_data.get('900', '0'))
            exec_qty = safe_int(real_data.get('911', '0'))  # 체결량
            unexec_qty = safe_int(real_data.get('902', '0'))  # 미체결수량
            order_type = real_data.get('905', '').strip()  # +매수/-매도; 매도정정, 매수정정, 매수취소, 매도취소
            current_price = safe_int(real_data.get('10', '0'))
            
            # 주문구분 정규화
            if '+매수' in order_type:
                order_type_normalized = '매수'
            elif '-매도' in order_type:
                order_type_normalized = '매도'
            else:
                order_type_normalized = order_type
            
            logger.info(f"주문체결: [{code}] {order_type_normalized} {order_status} - 주문:{order_qty}, 체결:{exec_qty}, 미체결:{unexec_qty}")
            
            # order 딕셔너리 업데이트
            if unexec_qty > 0:
                # 미체결 수량이 있으면 order에 유지
                self.order[code] = {
                    '종목코드': code,
                    '주문번호': order_no,
                    '주문상태': order_status,
                    '주문수량': order_qty,
                    '현재가': current_price,
                    '주문구분': order_type_normalized,
                    '미체결수량': unexec_qty,
                    '체결량': exec_qty
                }
            else:
                # 완전 체결되면 order에서 제거
                if code in self.order:
                    del self.order[code]
                    logger.info(f"주문 완전 체결로 order에서 제거: {code}")
            
            # balance 업데이트 (체결된 경우)
            if exec_qty > 0:
                if order_type_normalized == '매수':
                    # 매수 체결: balance에 추가 또는 수량 증가
                    if code in self.balance:
                        # 기존 보유 종목에 추가 매수
                        old_qty = self.balance[code]['보유수량']
                        old_price = self.balance[code]['매입가']
                        new_qty = old_qty + exec_qty
                        # 평균 매입가 계산
                        new_avg_price = ((old_qty * old_price) + (exec_qty * current_price)) // new_qty
                        self.balance[code]['보유수량'] = new_qty
                        self.balance[code]['매입가'] = new_avg_price
                        self.balance[code]['현재가'] = current_price
                        logger.info(f"매수 체결로 balance 업데이트: {code} 수량 {old_qty}->{new_qty}, 평균가 {new_avg_price}")
                    else:
                        # 신규 매수
                        today_str = datetime.date.today().strftime('%Y%m%d')
                        self.balance[code] = {
                            '종목명': real_data.get('302', '').strip(),
                            '보유수량': exec_qty,
                            '매입가': current_price,
                            '수익률': 0.0,
                            '현재가': current_price,
                            '매입금액': exec_qty * current_price,
                            '매매가능수량': exec_qty,
                            '매수일': today_str
                        }
                        upsert_purchase_date(code, today_str)
                        logger.info(f"신규 매수 체결로 balance 추가: {code} 수량 {exec_qty}, 가격 {current_price}")
                
                elif order_type_normalized == '매도':
                    # 매도 체결: balance에서 수량 감소 또는 제거
                    if code in self.balance:
                        old_qty = self.balance[code]['보유수량']
                        new_qty = old_qty - exec_qty
                        if new_qty <= 0:
                            # 전량 매도
                            del self.balance[code]
                            delete_purchase_date(code)
                            logger.info(f"전량 매도 체결로 balance에서 제거: {code}")
                        else:
                            # 일부 매도
                            self.balance[code]['보유수량'] = new_qty
                            self.balance[code]['매매가능수량'] = new_qty
                            self.balance[code]['현재가'] = current_price
                            logger.info(f"일부 매도 체결로 balance 업데이트: {code} 수량 {old_qty}->{new_qty}")
                    
        except Exception as e:
            logger.error(f"주문체결 데이터 처리 중 오류: {e}")
            logger.debug(f"real_data: {real_data}")

    def _start_websocket_thread(self):
        """별도의 스레드에서 WebSocket 연결을 시작합니다. 중복 스레드 실행을 방지합니다."""
        if self.websocket_thread and self.websocket_thread.is_alive():
            logger.info("WebSocket thread already running.")
            return
        self._websocket_stop_event.clear()
        self.websocket_thread = threading.Thread(target=self._run_websocket_loop)
        self.websocket_thread.daemon = True
        self.websocket_thread.start()

    def _run_websocket_loop(self):
        """WebSocket용 asyncio 이벤트 루프를 실행합니다. 중지 이벤트와 루프 재사용을 처리합니다."""
        self.asyncio_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.asyncio_loop)
        # 이 루프에 바인딩된 asyncio 원시 객체들을 생성합니다
        try:
            self._login_send_lock = asyncio.Lock()
        except Exception:
            self._login_send_lock = None
        try:
            self.asyncio_loop.run_until_complete(self._websocket_main_loop())
        except Exception as e:
            logger.exception(f"WebSocket loop error: {e}")
        finally:
            self.asyncio_loop.close()

    @notify_on_exception(fallback_return=None)
    async def _websocket_main_loop(self):
        """WebSocket 연결의 메인 루프입니다: 연결/재연결 및 메시지 처리 로직을 담당합니다."""
        try:
            while not self._websocket_stop_event.is_set():
                try:
                    async with websockets.connect(self.socket_url) as websocket:
                        self.websocket = websocket
                        self.is_websocket_connected = True
                        logger.info("WebSocket connected.")
                        # Ensure token is valid before attempting LOGIN
                        try:
                            await self._ensure_valid_token_async()
                        except Exception as e:
                            logger.warning(f"Token refresh before websocket login failed: {e}")
                        # 다른 경로에서 `_send_websocket_message`를 호출하며 경쟁 상태가 발생하지 않도록
                        # 메인 루프의 연결 경로에서 직접 LOGIN을 전송합니다.
                        try:
                            if self._login_send_lock is not None:
                                async with self._login_send_lock:
                                    if not self._websocket_login_sent:
                                        await self.websocket.send(json.dumps({'trnm': 'LOGIN', 'token': self.access_token}))
                                        self._websocket_login_sent = True
                                            # LOGIN 응답이 `_handle_websocket_message`에서 도착하면 logged_in이 설정됩니다
                                        self._websocket_logged_in = False
                            else:
                                # fallback: send without lock
                                if not self._websocket_login_sent:
                                    await self.websocket.send(json.dumps({'trnm': 'LOGIN', 'token': self.access_token}))
                                    self._websocket_login_sent = True
                                    self._websocket_logged_in = False
                        except Exception as e:
                            logger.exception(f"Failed to send LOGIN directly: {e}")

                        # 재연결 시 이전에 등록했던 실시간 데이터를 재등록합니다
                        await self._reregister_real_data()

                        while self.is_websocket_connected and not self._websocket_stop_event.is_set():
                            try:
                                message = await self.websocket.recv()
                                await self._handle_websocket_message(message)
                            except websockets.ConnectionClosed:
                                logger.info("WebSocket connection closed.")
                                self.is_websocket_connected = False
                                # Reset login state on disconnect
                                self._websocket_logged_in = False
                                self._websocket_login_sent = False
                                break
                    # 연결이 끊어지면 재연결 대기 후 재시도
                    if not self._websocket_stop_event.is_set():
                        logger.info("Attempting to reconnect WebSocket in 2 seconds...")
                        await asyncio.sleep(2)
                except Exception as e:
                    # 일반적인 예외 처리: 로그 후 재시도
                    logger.exception(f"WebSocket connection error: {e}")
                    self.is_websocket_connected = False
                    self._websocket_logged_in = False
                    self._websocket_login_sent = False
                    if not self._websocket_stop_event.is_set():
                        logger.info("Retrying WebSocket connection in 2 seconds...")
                        await asyncio.sleep(2)
        except asyncio.CancelledError:
            # Task cancellation is used during shutdown; perform graceful cleanup and re-raise
            logger.info("WebSocket main loop cancelled: performing graceful shutdown.")
            try:
                if getattr(self, 'websocket', None):
                    try:
                        await self.websocket.close()
                    except Exception as _e:
                        logger.debug("Error closing websocket during cancellation: %s", _e)
            finally:
                self.is_websocket_connected = False
                self._websocket_logged_in = False
                self._websocket_login_sent = False
            raise
        finally:
            logger.info("WebSocket loop stopped.")

    @notify_on_exception(fallback_return=None)
    async def _reregister_real_data(self):
        """WebSocket 재연결 시 이전에 등록했던 실시간 데이터를 재등록합니다."""
        if self._real_reg_info:
            logger.info("Re-registering real-time data after reconnection...")
            await asyncio.sleep(0.5)  # LOGIN 완료 대기
            
            codes = self._real_reg_info['code_list'].split(';')
            subscription_data = [{
                'item': codes,
                'type': ['0B']
            }]
            
            message = {
                'trnm': 'REG',
                'grp_no': '1',
                'refresh': self._real_reg_info['opt_type'],
                'data': subscription_data
            }
            
            await self._send_websocket_message(message)
            logger.info(f"Real-time data re-registered for codes: {self._real_reg_info['code_list']}")

    @notify_on_exception(fallback_return=None)
    async def _websocket_connect(self):
        try:
            # 연결하기 전에 토큰이 유효한지 확인합니다
            try:
                await self._ensure_valid_token_async()
            except Exception as e:
                logger.warning(f"Token refresh before single websocket connect failed: {e}")
            self.websocket = await websockets.connect(self.socket_url)
            self.is_websocket_connected = True
            # Reset login flags - LOGIN must be performed after connect
            self._websocket_logged_in = False
            self._websocket_login_sent = False
            logger.info("WebSocket connected (single connect).")
        except Exception as e:
            logger.error(f"WebSocket single connect error: {e}")
            self.is_websocket_connected = False
            self.websocket = None

    @notify_on_exception(fallback_return=None)
    async def _ensure_valid_token_async(self):
        """`self.access_token`이 유효한지 확인합니다. 만료되었으면 블로킹 되는 `_authenticate`를
        쓰레드풀에서 실행하여 토큰을 갱신합니다.

        이렇게 하면 동기 `_authenticate`가 asyncio 이벤트 루프를 차단하지 않도록 합니다.
        """
        # 토큰이 없거나 짧은 시간 내에 만료될 예정이면 갱신합니다.
        try:
            if self.token_expires_in is None or self.token_expires_in < (get_korea_time() + datetime.timedelta(seconds=5)):
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._authenticate)
        except Exception as e:
            logger.exception(f"Token refresh failed in _ensure_valid_token_async: {e}")

    def stop_websocket(self):
        """WebSocket 스레드와 이벤트 루프를 안전하게 중지합니다."""
        self._websocket_stop_event.set()
        self.is_websocket_connected = False
        loop = getattr(self, 'asyncio_loop', None)
        # 안전 셧다운: 루프가 살아있다면 셧다운 코루틴을 해당 루프에서 실행하도록 요청
        if loop and not loop.is_closed():
            try:
                # _shutdown_websocket는 루프 내부에서 웹소켓을 닫고 관련 태스크를 정리합니다
                fut = asyncio.run_coroutine_threadsafe(self._shutdown_websocket(), loop)
                # 기본 대기시간은 5초
                try:
                    fut.result(timeout=5)
                except Exception as e:
                    logger.warning(f"Exception while waiting for websocket shutdown: {e}")
            except Exception as e:
                logger.warning(f"Failed to schedule websocket shutdown on loop: {e}")

        # 스레드 종료 대기
        if self.websocket_thread and self.websocket_thread.is_alive():
            self.websocket_thread.join(timeout=5)
            if self.websocket_thread.is_alive():
                logger.warning("WebSocket thread did not stop within timeout")
            else:
                logger.info("WebSocket thread stopped.")

    @notify_on_exception(fallback_return=None)
    async def _shutdown_websocket(self):
        """루프 내부에서 실행되는 안전한 웹소켓 셧다운 코루틴.

        - 열린 웹소켓을 닫음
        - 웹소켓 관련 대기중인 태스크들을 취소하고 대기
        - 내부 상태 정리 후 루프 중지 신호를 냄
        """
        try:
            # 1) 웹소켓이 열려있다면 닫기 (recv 대기 해제)
            if getattr(self, 'websocket', None):
                try:
                    await self.websocket.close()
                except Exception as e:
                    logger.warning(f"websocket.close() failed during shutdown: {e}")

            # 2) 현재 루프의 모든 태스크를 취소하되, 현재 셧다운 태스크는 제외
            try:
                current = asyncio.current_task()
                tasks = [t for t in asyncio.all_tasks() if t is not current]
                if tasks:
                    for t in tasks:
                        try:
                            t.cancel()
                        except Exception:
                            pass
                    await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as e:
                logger.debug(f"Error while cancelling tasks during websocket shutdown: {e}")

        finally:
            # 상태 정리
            try:
                self.websocket = None
                self.is_websocket_connected = False
                self._websocket_logged_in = False
                self._websocket_login_sent = False
            except Exception:
                pass
            # 루프 중지 요청 (실행 중인 루프에서 호출되므로 안전)
            try:
                loop = asyncio.get_running_loop()
                loop.stop()
            except Exception:
                pass

    @notify_on_exception(fallback_return=None)
    async def _send_websocket_message(self, message):
        """WebSocket 서버로 메시지를 전송합니다. 필요 시 재연결과 사전 LOGIN 처리를 합니다."""
        # 메시지가 문자열인지 확인하고, `trnm`을 검사하기 위해 파싱된 객체도 유지합니다
        if not isinstance(message, str):
            message = json.dumps(message)
        try:
            message_obj = json.loads(message)
        except Exception:
            message_obj = {}
        send_attempted = False
        for attempt in range(2):  # 최대 2회 시도(재연결 포함)
            if not self.is_websocket_connected or not self.websocket:
                logger.info("WebSocket is not connected. Reconnecting...")
                await self._websocket_connect()
            if self.is_websocket_connected and self.websocket:
                try:
                    # 아직 로그인되어 있지 않고, 요청 메시지가 명시적인 LOGIN이 아닌 경우,
                    # 서버가 다른 메시지를 거부하지 않도록 먼저 LOGIN을 한 번 전송합니다.
                    trnm = message_obj.get('trnm') if isinstance(message_obj, dict) else None
                    if trnm != 'LOGIN' and not self._websocket_logged_in:
                        try:
                            if self._login_send_lock is not None:
                                async with self._login_send_lock:
                                    if not self._websocket_login_sent:
                                        await self.websocket.send(json.dumps({'trnm': 'LOGIN', 'token': self.access_token}))
                                        self._websocket_login_sent = True
                            else:
                                if not self._websocket_login_sent:
                                    await self.websocket.send(json.dumps({'trnm': 'LOGIN', 'token': self.access_token}))
                                    self._websocket_login_sent = True
                            # 로그인 확인을 위해 최대 약 1초(0.1s * 10회) 까지 대기합니다
                            for _ in range(10):
                                if self._websocket_logged_in:
                                    break
                                await asyncio.sleep(0.1)
                        except Exception as e:
                            logger.warning(f"사전 LOGIN 전송 실패: {e}")

                    await self.websocket.send(message)
                    send_attempted = True
                    break
                except Exception as e:
                    logger.error(f"WebSocket send failed (attempt {attempt+1}): {e}")
                    self.is_websocket_connected = False
                    self.websocket = None
            else:
                logger.warning("재연결 시도 이후에도 WebSocket이 연결되지 않았습니다.")
        if not send_attempted:
            logger.error("재연결 시도 후에도 WebSocket으로 메시지 전송에 실패했습니다.")
            # 다음 연결에서 다시 LOGIN을 시도할 수 있도록 login_sent 플래그를 리셋합니다
            self._websocket_login_sent = False
    @notify_on_exception(fallback_return=None)
    async def _handle_websocket_message(self, message):
        """들어오는 WebSocket 메시지를 처리합니다."""
        try:
            data = json.loads(message)
            trnm = data.get('trnm')
            
            if trnm == 'LOGIN':
                if data.get('return_code') == 0:
                    logger.info("WebSocket login successful.")
                    # reset retry counter on success
                    self._websocket_login_retries = 0
                    # mark login success
                    self._websocket_logged_in = True
                    self._websocket_login_sent = True
                else:
                    msg = str(data.get('return_msg', ''))
                    logger.warning(f"WebSocket login failed: {msg}")
                    # detect token-related failures and try re-authenticate + relogin
                    lower = msg.lower()
                    token_issue = (
                        '토큰' in msg or '만료' in msg or '인증' in msg or 'expired' in lower or 'invalid token' in lower
                    )
                    if token_issue and self._websocket_login_retries < self._websocket_max_login_retries:
                        self._websocket_login_retries += 1
                        logger.info(f"Attempting to refresh token and re-login (attempt {self._websocket_login_retries})...")
                        try:
                            await self._ensure_valid_token_async()
                            await asyncio.sleep(0.2)
                            await self._send_websocket_message({'trnm': 'LOGIN', 'token': self.access_token})
                        except Exception as e:
                            logger.exception(f"Re-login attempt failed: {e}")
                    else:
                        # unrecoverable: disconnect
                        await self.disconnect()
                    
            elif trnm == 'PING':
                # PING 메시지에 대한 PONG 응답
                await self._send_websocket_message(data)

            elif trnm == 'REG':
                if data.get('return_code') == 0:
                    logger.info(f"Real-time registration successful.")
                else:
                    logger.warning(f"Real-time registration failed: {data}")
                    
            elif trnm == 'REMOVE':
                if data.get('return_code') == 0:
                    logger.info(f"Real-time removal successful.")
                else:
                    logger.warning(f"Real-time removal failed: {data}")
                    
            elif trnm == 'REAL':
                # 실시간 체결 데이터 처리
                real_data = data.get('data', [])
                for item in real_data:
                    self._on_receive_real_data(item.get('item'), item.get('type'), item.get('values'))
                    
            elif trnm == 'SYSTEM':
                # 시스템 공지/알림 메시지 처리 (거래정지, 시스템 점검 등)
                code = data.get('code', '')
                message_text = data.get('message', '')
                # 필요시 중요한 시스템 메시지만 출력하도록 필터링
                if code != 'R00000':  # R00000은 일반 공지
                    logger.warning(f"[SYSTEM] {code}: {message_text}")
                # else: 
                #     print(f"[SYSTEM-INFO] {message_text}")  # 디버깅이 필요한 경우 주석 해제
                    
            else:
                # 알 수 없는 메시지 타입 (디버깅용)
                logger.warning(f"Unknown message type '{trnm}': {data}")
                
        except json.JSONDecodeError:
            logger.warning(f"Failed to decode WebSocket message: {message}")
        except Exception as e:
            logger.exception(f"Error handling WebSocket message: {e}")

    def disconnect(self):
        """WebSocket 연결을 종료합니다."""
        if self.is_websocket_connected and self.asyncio_loop:
            self.is_websocket_connected = False
            self.asyncio_loop.call_soon_threadsafe(asyncio.create_task, self.websocket.close())
        if self.websocket_thread:
            self.websocket_thread.join()
        # Reset login state
        self._websocket_logged_in = False
        self._websocket_login_sent = False