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
    c._reregister_delay = None
    c._login_gen = 0
    c._reg_sent = None
    c._reg_fail_gen = 0
    c._websocket_stop_event = threading.Event()
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

    real_sleep = asyncio.sleep

    async def fast_sleep(_):
        await real_sleep(0)       # 지연은 없애되 이벤트 루프에는 양보

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


def test_reg_failure_during_inflight_reregister_is_not_lost(monkeypatch):
    """전송 중(태스크 실행 중) 도착한 REG 실패의 재시도 요청을 보존했다가 종료 후 다시 예약한다."""
    c = make_client()
    c._websocket_logged_in = True

    real_sleep = asyncio.sleep

    async def fast_sleep(_):
        await real_sleep(0)       # 지연은 없애되 이벤트 루프에는 양보

    monkeypatch.setattr(kiwoom_module.asyncio, 'sleep', fast_sleep)
    gate = asyncio.Event()
    sends = []

    async def slow_send(msg):
        sends.append(msg)
        if len(sends) == 1:
            await gate.wait()       # 첫 전송이 흐름 제어 등으로 대기 중

    c._send_websocket_message = slow_send

    async def scenario():
        c._schedule_reregister()
        await asyncio.sleep(0)                      # 첫 태스크가 전송 대기 상태로 진입
        await c._handle_websocket_message(REG_FAIL)  # 전송 중 도착한 REG 실패
        assert c._reg_retry_count == 1
        gate.set()
        for _ in range(20):                          # 보존된 재시도가 새 태스크로 실행되도록 양보
            await asyncio.sleep(0)
        t = c._reregister_task
        if t is not None:
            await t

    asyncio.run(scenario())
    assert len(sends) == 2                           # 첫 전송 + 보존된 재시도


import contextlib
import time


@contextlib.contextmanager
def loop_thread():
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(2)
        loop.close()


def run_in_loop(loop, coro):
    return asyncio.run_coroutine_threadsafe(coro, loop).result(2)


def wait_until(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def settle(loop):
    """루프에 예약된 콜백/태스크가 모두 소화될 때까지 잠시 양보."""
    async def _spin():
        for _ in range(20):
            await asyncio.sleep(0)
    run_in_loop(loop, _spin())


def test_set_real_reg_before_login_defers_to_login_success_without_duplicate_reg():
    c = make_client(real_reg=False)
    c._websocket_logged_in = False
    sent = []

    async def send(m):
        sent.append(m)

    c._send_websocket_message = send
    with loop_thread() as loop:
        c.asyncio_loop = loop
        c.set_real_reg('005930;000660', '0')
        settle(loop)
        assert c._real_reg_info == {'code_list': '005930;000660', 'opt_type': '0'}
        assert sent == []                                   # 로그인 전에는 전송하지 않음
        run_in_loop(loop, c._handle_websocket_message(LOGIN_OK))   # LOGIN 성공 → 한 번만 전송
        assert wait_until(lambda: len(sent) == 1)
        settle(loop)
    assert sent == [Kiwoom._build_reg_message('005930;000660', '0')]


def test_set_real_reg_when_logged_in_sends_once():
    c = make_client(real_reg=False)
    c._websocket_logged_in = True
    sent = []

    async def send(m):
        sent.append(m)

    c._send_websocket_message = send
    with loop_thread() as loop:
        c.asyncio_loop = loop
        c.set_real_reg('005930', '0')
        assert wait_until(lambda: len(sent) == 1)
        settle(loop)
    assert sent == [Kiwoom._build_reg_message('005930', '0')]


def test_race_login_success_then_set_real_reg_sends_once():
    """LOGIN 성공 처리와 set_real_reg 가 어느 순서로 겹쳐도 같은 REG 는 한 번만 전송된다."""
    c = make_client(real_reg=False)
    c._websocket_logged_in = False
    sent = []

    async def send(m):
        sent.append(m)

    c._send_websocket_message = send
    with loop_thread() as loop:
        c.asyncio_loop = loop
        # 등록 정보가 저장된 직후(호출 스레드가 멈춘 상태)에 LOGIN 성공이 먼저 처리되는 순서를 재현
        c._real_reg_info = {'code_list': '005930', 'opt_type': '0'}
        run_in_loop(loop, c._handle_websocket_message(LOGIN_OK))
        assert wait_until(lambda: len(sent) == 1)
        settle(loop)
        # 이어서 set_real_reg 의 스케줄링 단계가 실행됨 (이미 로그인됨, 같은 정보)
        c.asyncio_loop.call_soon_threadsafe(c._schedule_reregister)
        settle(loop)
    assert len(sent) == 1


def test_changed_registration_is_sent_again_after_same_session():
    c = make_client(real_reg=False)
    c._websocket_logged_in = True
    sent = []

    async def send(m):
        sent.append(m)

    c._send_websocket_message = send
    with loop_thread() as loop:
        c.asyncio_loop = loop
        c.set_real_reg('005930', '0')
        assert wait_until(lambda: len(sent) == 1)
        settle(loop)
        c.set_real_reg('005930;000660', '0')                # 구독 목록이 바뀌면 다시 전송
        assert wait_until(lambda: len(sent) == 2)
    assert sent[1] == Kiwoom._build_reg_message('005930;000660', '0')


def test_cancelled_reregister_does_not_spawn_followup_task():
    c = make_client()
    c._websocket_logged_in = True

    async def scenario():
        started = asyncio.Event()

        async def hang(_):
            started.set()
            await asyncio.sleep(3600)

        c._send_websocket_message = hang
        c._schedule_reregister()
        task = c._reregister_task
        await started.wait()
        c._schedule_reregister(delay=0)                      # 실행 중에 들어온 추가 요청
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        for _ in range(5):
            await asyncio.sleep(0)
        assert c._reregister_task is task                    # 취소 후 새 태스크가 만들어지지 않음
        assert task.cancelled()

    asyncio.run(scenario())


def test_registration_changed_during_inflight_send_is_not_lost():
    """A 목록을 전송하는 동안 set_real_reg(B) 가 들어와도 B 가 이어서 전송된다."""
    c = make_client(real_reg=False)
    c._websocket_logged_in = True
    c._real_reg_info = {'code_list': '005930', 'opt_type': '0'}
    gate = asyncio.Event()
    sent = []

    async def slow_send(m):
        sent.append(m)
        if len(sent) == 1:
            await gate.wait()

    c._send_websocket_message = slow_send

    async def scenario():
        c._schedule_reregister()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # 전송 대기 중에 구독 목록 변경 (set_real_reg 의 저장 + 스케줄링 단계)
        c._real_reg_info = {'code_list': '005930;000660', 'opt_type': '0'}
        c._schedule_reregister()
        gate.set()
        await c._reregister_task

    asyncio.run(scenario())
    assert [m['data'][0]['item'] for m in sent] == [['005930'], ['005930', '000660']]


def test_new_login_session_during_inflight_send_is_not_masked_by_old_send():
    """이전 세션의 REG 전송이 끝나기 전에 새 LOGIN 이 성공해도, 새 세션의 구독이 복구된다."""
    c = make_client()
    c._websocket_logged_in = True
    gate = asyncio.Event()
    sent = []

    async def slow_send(m):
        sent.append(m)
        if len(sent) == 1:
            await gate.wait()

    c._send_websocket_message = slow_send

    async def scenario():
        c._schedule_reregister()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await c._handle_websocket_message(LOGIN_OK)         # 새 로그인 세션 시작 (이전 전송은 아직 진행 중)
        gate.set()
        await c._reregister_task

    asyncio.run(scenario())
    assert len(sent) == 2                                    # 새 세션용 REG 가 다시 전송됨
    assert c._reg_sent == (c._login_gen, ('005930;000660', '0'))
