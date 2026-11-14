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


class FakeClock:
    """time.monotonic / asyncio.sleep 을 가짜 시계로 대체: sleep(d) 는 시계를 d 만큼 진행하고 루프에 양보한다."""

    def __init__(self, monkeypatch):
        self.now = 1000.0
        self.sleeps = []
        self.real_sleep = asyncio.sleep
        clock = self

        async def fake_sleep(d):
            clock.sleeps.append(d)
            clock.now += d
            await clock.real_sleep(0)

        monkeypatch.setattr(kiwoom_module.time, 'monotonic', lambda: clock.now)
        monkeypatch.setattr(kiwoom_module.asyncio, 'sleep', fake_sleep)

    def total(self):
        return sum(self.sleeps)


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
    c._reg_backoff_until = 0.0
    c._reg_backoff_key = None
    c._reg_last_key = None
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
    c._reg_last_key = ('005930;000660', '0')    # 실패 응답은 직전에 전송한 구독에 대한 것

    clock = FakeClock(monkeypatch)
    real_sleep = clock.real_sleep

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

    clock = FakeClock(monkeypatch)
    real_sleep = clock.real_sleep
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
        c._schedule_reregister()                             # 실행 중에 들어온 추가 요청
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


def test_failure_chain_inside_one_worker_respects_retry_budget(monkeypatch):
    """전송 중 REG 실패 응답이 연속으로 도착해도 최초 1회 + 재시도 한도(3회)를 넘기지 않는다."""
    c = make_client()
    c._websocket_logged_in = True
    clock = FakeClock(monkeypatch)
    sleeps = clock.sleeps
    real_sleep = clock.real_sleep
    sent = []

    async def send(m):
        sent.append(m)
        await c._handle_websocket_message(REG_FAIL)     # 전송 직후(워커 실행 중) 실패 응답 도착

    c._send_websocket_message = send

    async def scenario():
        c._schedule_reregister()
        await c._reregister_task

    asyncio.run(scenario())
    assert len(sent) == 1 + kiwoom_module.REG_MAX_RETRIES
    assert clock.total() >= kiwoom_module.REG_RETRY_DELAY_SEC * kiwoom_module.REG_MAX_RETRIES - 0.5   # 재시도 사이 백오프 적용


def test_retry_backoff_not_erased_by_concurrent_same_subscription_request(monkeypatch):
    c = make_client()
    c._websocket_logged_in = True
    clock = FakeClock(monkeypatch)
    sleeps = clock.sleeps
    real_sleep = clock.real_sleep
    gate = asyncio.Event()
    sent = []

    async def send(m):
        sent.append(m)
        if len(sent) == 1:
            await gate.wait()

    c._send_websocket_message = send

    async def scenario():
        c._schedule_reregister()
        await real_sleep(0)
        await real_sleep(0)
        c._schedule_reregister()                          # 같은 구독의 set_real_reg 가 전송 중에 들어옴
        await c._handle_websocket_message(REG_FAIL)       # 이어서 REG 실패 → 백오프 2초
        gate.set()
        await c._reregister_task

    asyncio.run(scenario())
    assert len(sent) == 2
    assert clock.total() >= kiwoom_module.REG_RETRY_DELAY_SEC - 0.01   # 즉시 재전송되지 않고 백오프를 지킴


def test_many_subscription_changes_during_sends_end_with_latest(monkeypatch):
    """전송 중 구독이 계속 바뀌어도 (임의의 횟수 상한 없이) 마지막 구독까지 전송된다."""
    c = make_client(real_reg=False)
    c._websocket_logged_in = True
    c._real_reg_info = {'code_list': 'c0', 'opt_type': '0'}
    sent = []

    async def send(m):
        sent.append(m['data'][0]['item'][0])
        n = len(sent)
        if n <= 8:                                         # 전송할 때마다 구독 목록이 바뀜
            c._real_reg_info = {'code_list': f'c{n}', 'opt_type': '0'}
            c._schedule_reregister()

    c._send_websocket_message = send

    async def scenario():
        c._schedule_reregister()
        await c._reregister_task

    asyncio.run(scenario())
    assert sent[-1] == 'c8' and len(sent) == 9
    assert c._reg_sent == (c._login_gen, ('c8', '0'))


def test_give_up_marker_applies_to_failed_key_not_newer_subscription():
    """재시도 한도가 소진된 A 의 실패가, 아직 전송하지 않은 최신 구독 B 를 포기 처리하지 않는다."""
    c = make_client(real_reg=False)
    c._websocket_logged_in = True
    c._real_reg_info = {'code_list': 'A', 'opt_type': '0'}
    c._reg_retry_count = kiwoom_module.REG_MAX_RETRIES      # A 의 재시도 예산은 이미 소진
    gate = asyncio.Event()
    sent = []

    async def send(m):
        sent.append(m['data'][0]['item'][0])
        if len(sent) == 1:
            await gate.wait()

    c._send_websocket_message = send

    async def scenario():
        c._schedule_reregister()
        for _ in range(3):
            await asyncio.sleep(0)
        c._real_reg_info = {'code_list': 'B', 'opt_type': '0'}   # A 전송 중 구독이 B 로 변경
        c._schedule_reregister()
        await c._handle_websocket_message(REG_FAIL)              # A 의 실패 응답 → A 포기
        gate.set()
        await c._reregister_task

    asyncio.run(scenario())
    assert sent == ['A', 'B']                                    # B 는 포기되지 않고 전송됨
    assert c._reg_sent == (c._login_gen, ('B', '0'))


def test_new_login_resets_backoff_while_worker_is_waiting(monkeypatch):
    c = make_client()
    c._websocket_logged_in = True
    clock = FakeClock(monkeypatch)
    sent = []

    async def send(m):
        sent.append(m)

    c._send_websocket_message = send

    async def scenario():
        c._reg_last_key = ('005930;000660', '0')
        await c._handle_websocket_message(REG_FAIL)        # 백오프 2초 + 워커 시작(대기)
        await clock.real_sleep(0)
        await c._handle_websocket_message(LOGIN_OK)        # 새 로그인 세션: 백오프 해제
        await c._reregister_task

    asyncio.run(scenario())
    assert len(sent) == 1
    assert clock.total() < kiwoom_module.REG_RETRY_DELAY_SEC    # 남은 백오프 전체를 기다리지 않음


def test_backoff_extended_while_waiting_is_respected(monkeypatch):
    c = make_client()
    c._websocket_logged_in = True
    clock = FakeClock(monkeypatch)
    sent_at = []

    async def send(m):
        sent_at.append(clock.now)

    c._send_websocket_message = send

    async def scenario():
        c._reg_last_key = ('005930;000660', '0')
        start = clock.now
        await c._handle_websocket_message(REG_FAIL)        # 기한 = start + 2
        await clock.real_sleep(0)
        clock.now += 0.5                                   # 대기 중 시간이 흐른 뒤
        await c._handle_websocket_message(REG_FAIL)        # 다음 실패가 기한을 연장 (= start + 2.5)
        await c._reregister_task
        return start

    start = asyncio.run(scenario())
    assert sent_at[0] >= start + 2.5 - 1e-6                # 연장된 기한 이전에 전송하지 않음


def test_backoff_does_not_delay_a_different_subscription(monkeypatch):
    c = make_client(real_reg=False)
    c._websocket_logged_in = True
    clock = FakeClock(monkeypatch)
    sent = []

    async def send(m):
        sent.append(m['data'][0]['item'][0])

    c._send_websocket_message = send

    async def scenario():
        c._real_reg_info = {'code_list': 'A', 'opt_type': '0'}
        c._reg_last_key = ('A', '0')
        await c._handle_websocket_message(REG_FAIL)        # A 에 백오프 (재시도 예약)
        c._real_reg_info = {'code_list': 'B', 'opt_type': '0'}   # 구독 변경: 새 구독은 백오프 대상이 아님
        c._schedule_reregister()
        await c._reregister_task

    asyncio.run(scenario())
    assert sent == ['B']
    assert clock.total() == 0


def test_new_subscription_gets_its_own_retry_budget(monkeypatch):
    """A 가 재시도 예산을 소진한 뒤 B 로 바뀌면, B 의 첫 실패는 재시도 대상이 된다."""
    c = make_client(real_reg=False)
    c._websocket_logged_in = True
    FakeClock(monkeypatch)
    sent = []

    async def send(m):
        sent.append(m['data'][0]['item'][0])

    c._send_websocket_message = send

    async def scenario():
        c._real_reg_info = {'code_list': 'A', 'opt_type': '0'}
        c._schedule_reregister()
        await c._reregister_task
        for _ in range(kiwoom_module.REG_MAX_RETRIES + 1):       # A: 재시도 예산 소진
            await c._handle_websocket_message(REG_FAIL)
            if c._reregister_task is not None:
                await c._reregister_task
        n_a = len(sent)
        c._real_reg_info = {'code_list': 'B', 'opt_type': '0'}   # 구독 변경
        c._schedule_reregister()
        await c._reregister_task
        await c._handle_websocket_message(REG_FAIL)              # B 의 일시적 실패
        await c._reregister_task
        return n_a

    n_a = asyncio.run(scenario())
    assert sent[n_a:] == ['B', 'B']                              # B 최초 전송 + 재시도 1회
