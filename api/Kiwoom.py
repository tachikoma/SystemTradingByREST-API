import requests
import json
import datetime
import time
import pandas as pd
from util.const import *
import asyncio
import websockets
import threading


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

        # WebSocket attributes
        self.websocket = None
        self.is_websocket_connected = False
        self.websocket_thread = None
        self.asyncio_loop = None

        self._authenticate()
        self._start_websocket_thread()

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
            self.access_token = res.json()["access_token"]
            self.token_expires_in = datetime.datetime.now() + datetime.timedelta(seconds=res.json()["expires_in"])
            print("Authentication successful.")
        else:
            print(f"Authentication failed: {res.text}")
            self.access_token = None

    def _request(self, path, tr_id, params, method="POST", extra_headers=None):
        """
        A wrapper for making API requests.
        """
        # TODO: Add token refresh logic
        # if self.token_expires_in is None or self.token_expires_in < datetime.datetime.now():
        #     self._authenticate()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "appkey": self.appkey,
            "secretkey": self.secretkey,
            "tr_id": tr_id
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
            print(f"Request failed: {res.text}")
            return None, None



    def get_code_list_by_market(self, market_type):
        """
        Retrieves a list of stock codes and names for a given market type.
        market_type: '0' (KOSPI), '10' (KOSDAQ), etc.
        """
        path = "/api/dostk/stkinfo"
        tr_id = "ka10099"
        params = {"mrkt_tp": market_type}

        res_data = self._request(path=path, tr_id=tr_id, params=params, method="POST")

        code_list = []
        if res_data and isinstance(res_data, dict) and "list" in res_data:
            for item in res_data["list"]:
                code_list.append({"code": item["code"], "name": item["name"]})
        else:
            print(f"Failed to retrieve code list for market {market_type} or unexpected response format: {res_data}")

        return code_list

    def get_master_code_name(self, code):
        """
        Retrieves the name of a stock given its code.
        """
        path = "/api/dostk/stkinfo"
        tr_id = "ka10100"
        params = {"stk_cd": code}

        res_data = self._request(path=path, tr_id=tr_id, params=params, method="POST")

        if res_data and isinstance(res_data, dict) and "name" in res_data:
            return res_data["name"]
        else:
            print(f"Failed to retrieve stock name for code {code} or unexpected response format: {res_data}")
            return None

    def get_price_data(self, code):
        """
        Retrieves historical daily OHLCV data for a specific stock.
        """
        path = "/api/dostk/chart"
        tr_id = "ka10081"
        all_ohlcv_data = {
            'date': [], 'open': [], 'high': [], 'low': [], 'close': [], 'volume': []
        }
        
        cont_yn = "Y"
        next_key = ""

        while cont_yn == "Y":
            params = {
                "stk_cd": code,
                "base_dt": datetime.date.today().strftime("%Y%m%d"), # Start from today
                "upd_stkpc_tp": "1" # Adjusted stock price
            }
            extra_headers = {}
            if next_key:
                extra_headers["next-key"] = next_key
            
            res_data, res_headers = self._request(path=path, tr_id=tr_id, params=params, method="POST", extra_headers=extra_headers)

            if res_data and isinstance(res_data, dict) and "stk_dt_pole_chart_qry" in res_data:
                for item in res_data["stk_dt_pole_chart_qry"]:
                    all_ohlcv_data['date'].append(item['dt'])
                    all_ohlcv_data['open'].append(int(item['open_pric']))
                    all_ohlcv_data['high'].append(int(item['high_pric']))
                    all_ohlcv_data['low'].append(int(item['low_pric']))
                    all_ohlcv_data['close'].append(int(item['cur_prc']))
                    all_ohlcv_data['volume'].append(int(item['trde_qty']))
                
                cont_yn = res_headers.get("cont-yn", "N")
                next_key = res_headers.get("next-key", "")
            else:
                print(f"Failed to retrieve price data for code {code} or unexpected response format: {res_data}")
                break
        
        df = pd.DataFrame(all_ohlcv_data, columns=['open', 'high', 'low', 'close', 'volume'], index=all_ohlcv_data['date'])
        
        return df[::-1]

    def get_deposit(self):
        """
        Retrieves the orderable deposit amount using the Kiwoom REST API (kt00001).
        """
        path = "/api/dostk/acnt"
        tr_id = "kt00001"
        params = {"qry_tp": "3"} # 3 for Estimated Inquiry

        res_data, _ = self._request(path=path, tr_id=tr_id, params=params, method="POST")

        deposit = 0
        if res_data and isinstance(res_data, dict) and "ord_alow_amt" in res_data:
            deposit = int(res_data["ord_alow_amt"])
        else:
            print(f"Failed to retrieve deposit or unexpected response format: {res_data}")

        return deposit

    def send_order(self, rqname, screen_no, order_type, code, order_quantity, order_price, order_classification, origin_order_number=""):
        """
        Sends a buy or sell order using the Kiwoom REST API.
        rqname, screen_no are not directly used in REST API, kept for compatibility.
        order_type: 0 for Buy, 1 for Sell (from original Kiwoom API)
        order_classification: '00' for limit order (지정가), '03' for market order (시장가)
        """
        path = "/api/dostk/ordr"
        
        tr_id_map = {
            0: "kt10000", # Buy order
            1: "kt10001"  # Sell order
            # TODO: Add kt10002 for amend, kt10003 for cancel
        }
        tr_id = tr_id_map.get(order_type)
        if not tr_id:
            print(f"Unsupported order_type: {order_type}")
            return None

        # Map order_classification to trde_tp
        trde_tp_map = {
            "00": "0", # 지정가 (Limit order) -> 보통
            "03": "3"  # 시장가 (Market order)
            # Add other mappings if needed
        }
        trde_tp = trde_tp_map.get(order_classification)
        if not trde_tp:
            print(f"Unsupported order_classification: {order_classification}")
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

        res_data, _ = self._request(path=path, tr_id=tr_id, params=params, method="POST")

        if res_data and isinstance(res_data, dict) and "ord_no" in res_data:
            print(f"Order successful. Order number: {res_data['ord_no']}")
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
            print(f"Order failed for code {code} or unexpected response: {res_data}")
            return None

    def get_order(self):
        """
        Retrieves a list of unexecuted orders using the Kiwoom REST API (ka10075).
        """
        path = "/api/dostk/acnt"
        tr_id = "ka10075"
        
        all_unexecuted_orders = []
        cont_yn = "Y"
        next_key = ""

        while cont_yn == "Y":
            params = {
                "all_stk_tp": "0", # 0: 전체, 1: 종목 (All stocks)
                "trde_tp": "0",    # 0: 전체, 1: 매도, 2: 매수 (All trade types)
                "stk_cd": "",      # Empty for all stocks
                "stex_tp": "0"     # 0: 통합, 1: KRX, 2: NXT (Integrated exchange)
            }
            extra_headers = {}
            if next_key:
                extra_headers["next-key"] = next_key
            
            res_data, res_headers = self._request(path=path, tr_id=tr_id, params=params, method="POST", extra_headers=extra_headers)

            if res_data and isinstance(res_data, dict) and "oso" in res_data:
                for item in res_data["oso"]:
                    # Map relevant fields to a consistent structure, similar to original self.order
                    # This mapping needs careful verification against actual API response.
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
                
                cont_yn = res_headers.get("cont-yn", "N")
                next_key = res_headers.get("next-key", "")
            else:
                print(f"Failed to retrieve unexecuted orders or unexpected response format: {res_data}")
                break
        
        # Update self.order for consistency, though it's primarily for real-time updates
        # For now, this will just contain the last fetched unexecuted orders.
        self.order = {order['종목코드']: order for order in all_unexecuted_orders}

        return all_unexecuted_orders

    def get_balance(self):
        """
        Retrieves account balance and holdings using the Kiwoom REST API (kt00018).
        """
        path = "/api/dostk/acnt"
        tr_id = "kt00018"
        
        all_holdings = []
        cont_yn = "Y"
        next_key = ""

        while cont_yn == "Y":
            params = {
                "qry_tp": "1",        # 1: 합산 (Combined), 2: 개별 (Individual)
                "dmst_stex_tp": "KRX" # KRX: 한국거래소, NXT: 넥스트트레이드
            }
            extra_headers = {}
            if next_key:
                extra_headers["next-key"] = next_key
            
            res_data, res_headers = self._request(path=path, tr_id=tr_id, params=params, method="POST", extra_headers=extra_headers)

            if res_data and isinstance(res_data, dict) and "acnt_evlt_remn_indv_tot" in res_data:
                for item in res_data["acnt_evlt_remn_indv_tot"]:
                    # Map relevant fields to a consistent structure, similar to original self.balance
                    # This mapping needs careful verification against actual API response.
                    holding_info = {
                        '종목명': item.get('stk_nm', '').strip(),
                        '보유수량': int(item.get('rmnd_qty', '0')),
                        '매입가': int(item.get('pur_pric', '0')),
                        '수익률': float(item.get('prft_rt', '0.0')),
                        '현재가': int(item.get('cur_prc', '0')),
                        '매입금액': int(item.get('pur_amt', '0')),
                        '매매가능수량': int(item.get('trde_able_qty', '0'))
                    }
                    all_holdings.append((item.get('stk_cd', '').strip(), holding_info)) # Store with code for keying
                
                cont_yn = res_headers.get("cont-yn", "N")
                next_key = res_headers.get("next-key", "")
            else:
                print(f"Failed to retrieve balance or unexpected response format: {res_data}")
                break
        
        # Update self.balance for consistency
        self.balance = {code: info for code, info in all_holdings}

        return self.balance

    def set_real_reg(self, str_screen_no, str_code_list, str_fid_list, str_opt_type):
        """
        Registers for real-time data using WebSocket.
        str_screen_no, str_fid_list are not used, but kept for compatibility.
        str_opt_type maps to refresh.
        """
        codes = str_code_list.split(';')
        
        # In REST API, FID list is not needed, we subscribe by stock code and type (e.g., '0B' for execution)
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
            print(f"Real-time registration sent for codes: {str_code_list}")
        else:
            print("WebSocket is not connected. Cannot register for real-time data.")

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
            # print(f"Real-time update for {s_code}: {self.universe_realtime_transaction_info[s_code]}")
        # Add other real_type handlers if needed

    def _start_websocket_thread(self):
        """Starts the WebSocket connection in a separate thread."""
        self.websocket_thread = threading.Thread(target=self._run_websocket_loop)
        self.websocket_thread.daemon = True
        self.websocket_thread.start()

    def _run_websocket_loop(self):
        """Runs the asyncio event loop for the WebSocket."""
        self.asyncio_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.asyncio_loop)
        self.asyncio_loop.run_until_complete(self._connect_websocket())

    async def _connect_websocket(self):
        """Connects to the WebSocket and handles messages."""
        try:
            async with websockets.connect(self.socket_url) as websocket:
                self.websocket = websocket
                self.is_websocket_connected = True
                print("WebSocket connected.")
                await self._send_websocket_message({
                    'trnm': 'LOGIN',
                    'token': self.access_token
                })

                while self.is_websocket_connected:
                    try:
                        message = await self.websocket.recv()
                        await self._handle_websocket_message(message)
                    except websockets.ConnectionClosed:
                        print("WebSocket connection closed.")
                        self.is_websocket_connected = False
                        break
        except Exception as e:
            print(f"WebSocket connection error: {e}")
            self.is_websocket_connected = False

    async def _send_websocket_message(self, message):
        """Sends a message to the WebSocket server."""
        if self.is_websocket_connected and self.websocket:
            if not isinstance(message, str):
                message = json.dumps(message)
            await self.websocket.send(message)

    async def _handle_websocket_message(self, message):
        """Handles incoming WebSocket messages."""
        try:
            data = json.loads(message)
            if data.get('trnm') == 'LOGIN':
                if data.get('return_code') == 0:
                    print("WebSocket login successful.")
                else:
                    print(f"WebSocket login failed: {data.get('return_msg')}")
                    await self.disconnect()
            elif data.get('trnm') == 'PING':
                await self._send_websocket_message(data)
            elif data.get('trnm') == 'REAL':
                real_data = data.get('data', [])
                for item in real_data:
                    self._on_receive_real_data(item.get('item'), item.get('type'), item.get('values'))
            else:
                print(f"Received unknown WebSocket message: {data}")
        except json.JSONDecodeError:
            print(f"Failed to decode WebSocket message: {message}")
        except Exception as e:
            print(f"Error handling WebSocket message: {e}")

    def disconnect(self):
        """Disconnects from the WebSocket."""
        if self.is_websocket_connected and self.asyncio_loop:
            self.is_websocket_connected = False
            self.asyncio_loop.call_soon_threadsafe(asyncio.create_task, self.websocket.close())
        if self.websocket_thread:
            self.websocket_thread.join()