import requests
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
from util.logging_config import configure_logging, get_logger
import logging

# Rate limit message keyword used by Kiwoom API responses
RATE_LIMIT_MSG = "허용된 요청 개수를 초과하였습니다"

configure_logging()
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
        # websocket login retry counter
        self._websocket_login_retries = 0
        self._websocket_max_login_retries = 3
        # websocket login state guards to avoid duplicate LOGIN packets
        self._websocket_logged_in = False
        self._websocket_login_sent = False
        # `asyncio.Lock` will be created within the websocket event loop thread
        self._login_send_lock = None

    def _authenticate(self):
        """
        Get access token.
        """
        url = f"{self.base_url}/oauth2/token"
        headers = {"content-type": "application/json"}
        data = {
            "grant_type": "client_credentials",
            "appkey": self.appkey,
            "secretkey": self.secretkey
        }
        res = requests.post(url, headers=headers, data=json.dumps(data))
        if res.status_code == 200:
            response_data = res.json()
            if return_code := response_data.get("return_code") != 0:
                logger.warning(response_data.get("return_msg"))
            self.access_token = response_data["token"]
            self.token_expires_in = datetime.datetime.strptime(response_data["expires_dt"], '%Y%m%d%H%M%S')
            logger.info("Authentication successful.")
        else:
            logger.error(f"Authentication failed: {res.text}")
            self.access_token = None

    def _request(self, path, api_id, params, method="POST", extra_headers=None):
        """
        A wrapper for making API requests.
        """
        # TODO: Add token refresh logic
        # Use Korea timezone helper consistently
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

        if method == "POST":
            res = requests.post(url, headers=headers, data=json.dumps(params))
        else: # GET
            res = requests.get(url, headers=headers, params=params)

        if res.status_code == 200:
            return res.json(), res.headers # Return headers for pagination
        else:
            logger.error(f"Request failed: {res.text}, {res.headers}")
            return res.json(), res.headers



    def get_code_list_by_market(self, market_type):
        """
        Retrieves a list of stock codes and names for a given market type.
        market_type: '0' (KOSPI), '10' (KOSDAQ), etc.
        """
        path = "/api/dostk/stkinfo"
        api_id = "ka10099"
        params = {"mrkt_tp": market_type}

        res_data, res_headers = self._request(path=path, api_id=api_id, params=params, method="POST")

        code_list = []
        if res_data and isinstance(res_data, dict) and "list" in res_data:
            for item in res_data["list"]:
                code_list.append({"code": item["code"], "name": item["name"]})
        else:
            logger.warning(f"Failed to retrieve code list for market {market_type} or unexpected response format: {res_data}")

        return code_list

    def get_master_code_name(self, code, max_retries=3, delay=1):
        """
        Retrieves the name of a stock given its code, with retry on API rate limit.
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
                logger.warning(f"API rate limit exceeded (attempt {attempt+1}), retrying after {delay}s...")
                time.sleep(delay)
                continue
            # 기타 실패는 즉시 종료
            logger.warning(f"Failed to retrieve stock name for code {code} or unexpected response format: {res_data}")
            break
        logger.error(f"All retries failed for code {code}.")
        return None

    def get_price_data(self, code, cont_yn='N', max_loops=1, max_retries=3, retry_delay=1):
        """
        Retrieves historical daily OHLCV data for a specific stock.

        Parameters:
        - code: stock code
        - cont_yn: continuation flag default
        - max_loops: maximum number of pages to retrieve
        - max_retries: per-page retry count when rate limit is hit
        - retry_delay: delay (seconds) between retries on rate limit

        The function retries an individual page request only when the API
        responds with the rate-limit error (return_code == 5 and
        return_msg contains the Korean rate-limit message). Other failures
        stop the retrieval.
        """
        path = "/api/dostk/chart"
        api_id = "ka10081"
        all_ohlcv_data = {
            'date': [], 'open': [], 'high': [], 'low': [], 'close': [], 'volume': []
        }

        next_key = ""
        first = True
        loop_count = 0
        while True:
            params = {
                "stk_cd": code,
                "base_dt": datetime.date.today().strftime("%Y%m%d"), # Start from today
                "upd_stkpc_tp": "1" # Adjusted stock price
            }
            extra_headers = {}
            if next_key:
                extra_headers["next-key"] = next_key

            # Per-page request with retries only on rate-limit responses
            page_res = None
            page_headers = None
            for attempt in range(max_retries):
                page_res, page_headers = self._request(path=path, api_id=api_id, params=params, method="POST", extra_headers=extra_headers)

                # Successful page response
                if page_res and isinstance(page_res, dict) and "stk_dt_pole_chart_qry" in page_res:
                    break

                # Check for rate-limit response to decide retry
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

                # Other failures: do not retry
                logger.error(f"Failed page request for code {code} (attempt {attempt+1}): {page_res}")
                page_res = None
                break

            # If page request ultimately failed, stop retrieval
            if not page_res or not isinstance(page_res, dict) or "stk_dt_pole_chart_qry" not in page_res:
                logger.warning(f"Stopping retrieval for code {code} due to page request failure.")
                break

            # Process received items
            for item in page_res["stk_dt_pole_chart_qry"]:
                all_ohlcv_data['date'].append(item['dt'])
                all_ohlcv_data['open'].append(int(item['open_pric']))
                all_ohlcv_data['high'].append(int(item['high_pric']))
                all_ohlcv_data['low'].append(int(item['low_pric']))
                all_ohlcv_data['close'].append(int(item['cur_prc']))
                all_ohlcv_data['volume'].append(int(item['trde_qty']))

            # Update continuation flags from headers
            if page_headers:
                cont_yn = page_headers.get("cont-yn", cont_yn)
                next_key = page_headers.get("next-key", "")

            # do-while 형태: 최소 1회 실행, cont_yn이 'Y'가 아니면 종료
            if not first and cont_yn != "Y":
                break
            loop_count += 1
            if loop_count >= max_loops:
                logger.info(f"Loop limit ({max_loops}) reached, breaking for code {code}")
                break
            first = False
            time.sleep(0.2) # To avoid rate limiting

        df = pd.DataFrame(all_ohlcv_data, columns=['open', 'high', 'low', 'close', 'volume'], index=all_ohlcv_data['date'])
        return df[::-1]

    def get_deposit(self):
        """
        Retrieves the orderable deposit amount using the Kiwoom REST API (kt00001).
        """
        path = "/api/dostk/acnt"
        api_id = "kt00001"
        params = {"qry_tp": "3"} # 3 for Estimated Inquiry

        res_data, _ = self._request(path=path, api_id=api_id, params=params, method="POST")

        deposit = 0
        if res_data and isinstance(res_data, dict) and "ord_alow_amt" in res_data:
            deposit = int(res_data["ord_alow_amt"])
        else:
            logger.warning(f"Failed to retrieve deposit or unexpected response format: {res_data}")

        return deposit

    def send_order(self, rqname, screen_no, order_type, code, order_quantity, order_price, order_classification, origin_order_number=""):
        """
        Sends a buy or sell order using the Kiwoom REST API.
        rqname, screen_no are not directly used in REST API, kept for compatibility.
        order_type: 0 for Buy, 1 for Sell (from original Kiwoom API)
        order_classification: '00' for limit order (지정가), '03' for market order (시장가)
        """
        path = "/api/dostk/ordr"
        
        api_id_map = {
            0: "kt10000", # Buy order
            1: "kt10001"  # Sell order
            # TODO: Add kt10002 for amend, kt10003 for cancel
        }
        api_id = api_id_map.get(order_type)
        if not api_id:
            logger.warning(f"Unsupported order_type: {order_type}")
            return None

        # Map order_classification to trde_tp
        trde_tp_map = {
            "00": "0", # 지정가 (Limit order) -> 보통
            "03": "3"  # 시장가 (Market order)
            # Add other mappings if needed
        }
        trde_tp = trde_tp_map.get(order_classification)
        if not trde_tp:
            logger.warning(f"Unsupported order_classification: {order_classification}")
            return None

        # ord_uv should be empty for market orders
        order_uv_param = str(order_price) if trde_tp != "3" else ""

        params = {
            "dmst_stex_tp": "KRX", # Hardcode to KRX for now
            "stk_cd": code,
            "ord_qty": str(order_quantity),
            "ord_uv": order_uv_param,
            "trde_tp": trde_tp,
            "cond_uv": "" # Not handled in original signature
        }

        res_data, _ = self._request(path=path, api_id=api_id, params=params, method="POST")

        if res_data and isinstance(res_data, dict) and "ord_no" in res_data:
            logger.info(f"Order successful. Order number: {res_data['ord_no']}")
            # Update self.order with the new order details (simplified for now)
            self.order[res_data['ord_no']] = {
                '종목코드': code,
                '주문수량': order_quantity,
                '주문가격': order_price,
                '주문구분': order_type,
                '주문번호': res_data['ord_no'],
                '주문상태': '접수' # Assuming initial state is '접수'
            }
            return res_data['ord_no']
        else:
            logger.error(f"Order failed for code {code} or unexpected response: {res_data}")
            return None

    def get_order(self, cont_yn='N', max_loops=10, max_retries=3, retry_delay=1):
        """
        Retrieves a list of unexecuted orders using the Kiwoom REST API (ka10075).
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
                    '현재가': int(item.get('cur_prc', '0').replace('+', '').replace('-', '')), # Remove signs
                    '주문구분': item.get('io_tp_nm', '').strip(),
                    '미체결수량': int(item.get('oso_qty', '0')),
                    '체결량': int(item.get('cntr_qty', '0')),
                    '주문시간': item.get('tm', '').strip(),
                    '당일매매수수료': int(item.get('tdy_trde_cmsn', '0')),
                    '당일매매세금': int(item.get('tdy_trde_tax', '0'))
                }
                all_unexecuted_orders.append(order_info)

            # Update continuation flags from headers
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
            time.sleep(0.2) # To avoid rate limiting
        self.order = {order['종목코드']: order for order in all_unexecuted_orders}
        return all_unexecuted_orders

    def get_balance(self, cont_yn='N', max_loops=10, max_retries=3, retry_delay=1):
        """
        Retrieves account balance and holdings using the Kiwoom REST API (kt00018).
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
            
            # Per-page request with retries only on rate-limit responses
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
                all_holdings.append((item.get('stk_cd', '').strip(), holding_info))

            # Update continuation flags from headers
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
            time.sleep(0.2) # To avoid rate limiting
        self.balance = {code: info for code, info in all_holdings}
        return self.balance

    def set_real_reg(self, str_code_list, str_opt_type='0'):
        """
        Registers for real-time data using WebSocket.
        
        Args:
            str_code_list: ';'로 구분된 종목코드 문자열 (예: '005930;000660')
            str_opt_type: '0' (기존 등록한 item/type은 해지 후 추가) 또는 '1' (기존 기존등록한 item/type은 유지 후 추가). 기본값은 '0'
        """
        codes = str_code_list.split(';')
        
        # 등록 정보 저장 (재연결 시 사용)
        self._real_reg_info = {
            'code_list': str_code_list,
            'opt_type': str_opt_type
        }
        
        # In REST API, we subscribe by stock code and type (e.g., '0B' for execution)
        # Let's assume we always want execution data, so type is '0B'.
        
        subscription_data = [{
            'item': codes,
            'type': ['0B'] # Assuming we always subscribe to '주식체결'
        }]

        message = {
            'trnm': 'REG',
            'grp_no': '1', # Using a default group number
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
        This method is now called from the WebSocket message handler.
        real_type is '0B' for execution data.
        real_data is a dictionary of FIDs and values.
        """
        if real_type == "0B": # 주식체결
            if s_code not in self.universe_realtime_transaction_info:
                self.universe_realtime_transaction_info[s_code] = {}
            
            # Map FIDs from string to integer keys for consistency with original const.py if needed,
            # but for now let's use string keys as received.
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
            # TODO logger.debug(f"Real-time update for {s_code}: {self.universe_realtime_transaction_info[s_code]}")
        # Add other real_type handlers if needed

    def _start_websocket_thread(self):
        """Starts the WebSocket connection in a separate thread, ensures only one thread runs."""
        if self.websocket_thread and self.websocket_thread.is_alive():
            logger.info("WebSocket thread already running.")
            return
        self._websocket_stop_event.clear()
        self.websocket_thread = threading.Thread(target=self._run_websocket_loop)
        self.websocket_thread.daemon = True
        self.websocket_thread.start()

    def _run_websocket_loop(self):
        """Runs the asyncio event loop for the WebSocket. Handles stop event and loop reuse."""
        self.asyncio_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.asyncio_loop)
        # create asyncio primitives tied to this loop
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

    async def _websocket_main_loop(self):
        """Main loop for WebSocket connection: handles connect, reconnect, and message processing."""
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
                    # Send LOGIN directly on the main loop's connection path to avoid
                    # racing with other code paths that may call `_send_websocket_message`.
                    try:
                        if self._login_send_lock is not None:
                            async with self._login_send_lock:
                                if not self._websocket_login_sent:
                                    await self.websocket.send(json.dumps({'trnm': 'LOGIN', 'token': self.access_token}))
                                    self._websocket_login_sent = True
                                    # logged_in will be set when LOGIN response arrives in _handle_websocket_message
                                    self._websocket_logged_in = False
                        else:
                            # fallback: send without lock
                            if not self._websocket_login_sent:
                                await self.websocket.send(json.dumps({'trnm': 'LOGIN', 'token': self.access_token}))
                                self._websocket_login_sent = True
                                self._websocket_logged_in = False
                    except Exception as e:
                        logger.exception(f"Failed to send LOGIN directly: {e}")
                    
                    # 재연결 시 이전에 등록했던 실시간 데이터 재등록
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
                logger.exception(f"WebSocket connection error: {e}")
                self.is_websocket_connected = False
                self._websocket_logged_in = False
                self._websocket_login_sent = False
                if not self._websocket_stop_event.is_set():
                    logger.info("Retrying WebSocket connection in 2 seconds...")
                    await asyncio.sleep(2)
        logger.info("WebSocket loop stopped.")

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

    async def _websocket_connect(self):
        try:
            # Ensure token is valid before connecting
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

    async def _ensure_valid_token_async(self):
        """Ensure `self.access_token` is valid; call blocking _authenticate in executor if expired.

        This avoids blocking the asyncio event loop by running the synchronous
        `_authenticate` in a threadpool.
        """
        # If token is missing or will expire in the next few seconds, refresh it.
        try:
            if self.token_expires_in is None or self.token_expires_in < (get_korea_time() + datetime.timedelta(seconds=5)):
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._authenticate)
        except Exception as e:
            logger.exception(f"Token refresh failed in _ensure_valid_token_async: {e}")

    def stop_websocket(self):
        """Stops the WebSocket thread and event loop safely."""
        self._websocket_stop_event.set()
        self.is_websocket_connected = False
        if self.asyncio_loop:
            self.asyncio_loop.call_soon_threadsafe(self.asyncio_loop.stop)
        if self.websocket_thread and self.websocket_thread.is_alive():
            self.websocket_thread.join(timeout=5)
            logger.info("WebSocket thread stopped.")

    async def _send_websocket_message(self, message):
        """Sends a message to the WebSocket server. Handles reconnection if needed."""
        # Ensure we have a JSON string and also keep a parsed object to inspect trnm
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
                    # If not logged in yet and this is not an explicit LOGIN message,
                    # send LOGIN first (once) to avoid server rejecting other messages.
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
                            # Wait up to ~1s for login to be confirmed
                            for _ in range(10):
                                if self._websocket_logged_in:
                                    break
                                await asyncio.sleep(0.1)
                        except Exception as e:
                            logger.warning(f"Pre-login send failed: {e}")

                    await self.websocket.send(message)
                    send_attempted = True
                    break
                except Exception as e:
                    logger.error(f"WebSocket send failed (attempt {attempt+1}): {e}")
                    self.is_websocket_connected = False
                    self.websocket = None
            else:
                logger.warning("WebSocket is still not connected after reconnect attempt.")
        if not send_attempted:
            logger.error("Failed to send message via WebSocket after reconnection attempts.")
            # Reset login_sent so next connect will attempt login again
            self._websocket_login_sent = False
    async def _handle_websocket_message(self, message):
        """Handles incoming WebSocket messages."""
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
                logger.debug(f"[DEBUG] Unknown message type '{trnm}': {data}")
                
        except json.JSONDecodeError:
            logger.warning(f"Failed to decode WebSocket message: {message}")
        except Exception as e:
            logger.exception(f"Error handling WebSocket message: {e}")

    def disconnect(self):
        """Disconnects from the WebSocket."""
        if self.is_websocket_connected and self.asyncio_loop:
            self.is_websocket_connected = False
            self.asyncio_loop.call_soon_threadsafe(asyncio.create_task, self.websocket.close())
        if self.websocket_thread:
            self.websocket_thread.join()
        # Reset login state
        self._websocket_logged_in = False
        self._websocket_login_sent = False