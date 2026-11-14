"""WebSocket 재연결 후 실시간 구독 복구: LOGIN 성공 뒤 REG, REG 실패 시 제한 재시도, 주문체결('00') 포함."""
import asyncio
import datetime
import json
import threading
from unittest.mock import AsyncMock, MagicMock

import websockets

from api import Kiwoom as kiwoom_module
from api.Kiwoom import Kiwoom
from util.time_helper import get_korea_time

LOGIN_OK = json.dumps({'trnm': 'LOGIN', 'return_code': 0, 'return_msg': ''})
REG_FAIL = json.dumps({'trnm': 'REG', 'return_code': 1, 'return_msg': '실패'})
REG_OK = json.dumps({'trnm': 'REG', 'return_code': 0})


def make_client(real_reg=True):
    c = Kiwoom.__new__(Kiwoom)
    c.access_token = 'tok'
    c.token_expires_in = get_korea_time() + datetime.timedelta(hours=1)
    c._auth_lock = threading.Lock()
    c._last_auth_attempt_ts = float('-inf')
    c._last_auth_ok = True
    c._last_login_token = None
    c._websocket_login_retries = 0
    c._websocket_max_login_retries = 3
    c._websocket_logged_in = False
    c._websocket_login_sent = True
    c.is_websocket_connected = True
    c.websocket = AsyncMock()
    c._reregister_task = None
    c._reg_retry_count = 0
    c._real_reg_info = {'code_list': '005930;000660', 'opt_type': '0'} if real_reg else None
    c._send_websocket_message = AsyncMock()
    return c


def test_build_reg_message_includes_quote_and_order_execution_subscriptions():
    m = Kiwoom._build_reg_message('005930;000660', '0')
    assert m['trnm'] == 'REG' and m['refresh'] == '0'
    types = {tuple(d['type']): d['item'] for d in m['data']}
    assert types[('0B',)] == ['005930', '000660']
    assert types[('00',)] == []          # 주문체결 구독(재연결 후에도 유지되어야 함)


def test_login_success_triggers_reregister_with_full_subscription():
    c = make_client()

    async def scenario():
        await c._handle_websocket_message(LOGIN_OK)
        assert c._reregister_task is not None
        await c._reregister_task

    asyncio.run(scenario())
    c._send_websocket_message.assert_awaited_once()
    msg = c._send_websocket_message.await_args.args[0]
    assert msg == Kiwoom._build_reg_message('005930;000660', '0')


def test_login_success_without_previous_subscription_sends_nothing():
    c = make_client(real_reg=False)

    async def scenario():
        await c._handle_websocket_message(LOGIN_OK)
        assert c._reregister_task is None

    asyncio.run(scenario())
    c._send_websocket_message.assert_not_awaited()


def test_reregister_not_duplicated_while_task_running():
    c = make_client()

    async def scenario():
        c._websocket_logged_in = True
        c._schedule_reregister()
        first = c._reregister_task
        c._schedule_reregister()
        assert c._reregister_task is first
        await first

    asyncio.run(scenario())
    assert c._send_websocket_message.await_count == 1


def test_reg_failure_retries_limited_times(monkeypatch):
    c = make_client()
    c._websocket_logged_in = True

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(kiwoom_module.asyncio, 'sleep', fast_sleep)

    async def scenario():
        for _ in range(kiwoom_module.REG_MAX_RETRIES + 2):
            await c._handle_websocket_message(REG_FAIL)
            if c._reregister_task is not None:
                await c._reregister_task

    asyncio.run(scenario())
    assert c._reg_retry_count == kiwoom_module.REG_MAX_RETRIES
    assert c._send_websocket_message.await_count == kiwoom_module.REG_MAX_RETRIES   # 한도 이후엔 재시도하지 않음


def test_reg_success_resets_retry_counter():
    c = make_client()
    c._reg_retry_count = 2
    asyncio.run(c._handle_websocket_message(REG_OK))
    assert c._reg_retry_count == 0


def test_reregister_skipped_when_not_logged_in():
    c = make_client()
    c._websocket_logged_in = False

    async def scenario():
        c._schedule_reregister()
        await c._reregister_task

    asyncio.run(scenario())
    c._send_websocket_message.assert_not_awaited()


def test_main_loop_does_not_send_reg_before_login_response(monkeypatch):
    """재연결 직후에는 LOGIN 만 보내고, REG 는 LOGIN 성공 응답 이후에만 보낸다."""
    c = make_client()
    c.socket_url = 'wss://example.invalid'
    c.websocket = None
    c.is_websocket_connected = False
    c._websocket_login_sent = False
    c._websocket_stop_event = threading.Event()
    c._login_send_lock = None
    c._reregister_real_data = AsyncMock()
    ws = AsyncMock()
    ws.recv.side_effect = websockets.ConnectionClosed(None, None)

    class FakeConnect:
        def __init__(self, url):
            pass

        async def __aenter__(self):
            return ws

        async def __aexit__(self, *a):
            return False

    async def fake_sleep(_):
        c._websocket_stop_event.set()

    monkeypatch.setattr(kiwoom_module.websockets, 'connect', FakeConnect)
    monkeypatch.setattr(kiwoom_module.asyncio, 'sleep', fake_sleep)
    asyncio.run(c._websocket_main_loop())
    sent = [json.loads(call.args[0]) for call in ws.send.await_args_list]
    assert [m['trnm'] for m in sent] == ['LOGIN']
    c._reregister_real_data.assert_not_awaited()
