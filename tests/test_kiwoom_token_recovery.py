"""서버 측 토큰 무효화(8005) 시 Kiwoom 클라이언트의 재인증/재시도 동작 테스트.

실제 API 호출·웹소켓 없이 `Kiwoom.__new__`로 객체를 만들고 session을 mock 한다.
"""
import asyncio
import datetime
import threading
from unittest.mock import MagicMock

import pytest

from api import Kiwoom as kiwoom_module
from api.Kiwoom import Kiwoom
from util.time_helper import get_korea_time

TOKEN_INVALID = {'return_code': 3, 'return_msg': '인증에 실패했습니다[8005:Token이 유효하지 않습니다]'}
OK = {'return_code': 0, 'return_msg': '정상', 'data': 1}


class Resp:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.headers = {}
        self.text = str(body)

    def json(self):
        return self._body


def make_client(tokens):
    """tokens: _authenticate 호출마다 발급할 토큰 목록."""
    c = Kiwoom.__new__(Kiwoom)
    c.base_url = 'https://example.invalid'
    c.access_token = 'old'
    c.token_expires_in = get_korea_time() + datetime.timedelta(hours=1)
    c._auth_lock = threading.Lock()
    c._last_forced_auth_ts = float('-inf')
    c._last_auth_alert_ts = float('-inf')
    c.session = MagicMock()
    issued = list(tokens)
    calls = {'auth': 0}

    def fake_auth():
        calls['auth'] += 1
        if not issued:
            c.access_token = None
            return
        c.access_token = issued.pop(0)
        c.token_expires_in = get_korea_time() + datetime.timedelta(hours=24)

    c._authenticate = fake_auth
    c._alerts = []
    c._alert_auth_problem = lambda msg: c._alerts.append(msg)
    return c, calls


def sent_tokens(c):
    return [call.kwargs['headers']['Authorization'] for call in c.session.post.call_args_list]


def test_is_token_invalid_response():
    f = Kiwoom._is_token_invalid_response
    assert f(TOKEN_INVALID)
    assert f({'return_code': 3, 'return_msg': '토큰이 만료되었습니다'})
    assert not f(OK)
    assert not f({'return_code': 5, 'return_msg': '허용된 요청 개수를 초과하였습니다'})
    assert not f({'return_code': 3, 'return_msg': '잘못된 요청입니다'})
    assert not f(None)
    assert not f('x')


def test_invalid_token_triggers_reauth_and_single_retry():
    c, calls = make_client(['new'])
    c.session.post.side_effect = [Resp(TOKEN_INVALID), Resp(OK)]
    res, _ = c._request('/p', 'ka1', {})
    assert res == OK
    assert calls['auth'] == 1
    assert sent_tokens(c) == ['Bearer old', 'Bearer new']


def test_http_error_status_with_8005_body_also_recovers():
    c, calls = make_client(['new'])
    c.session.post.side_effect = [Resp(TOKEN_INVALID, status=401), Resp(OK)]
    res, _ = c._request('/p', 'ka1', {})
    assert res == OK
    assert calls['auth'] == 1


def test_retry_only_once_when_still_invalid():
    c, calls = make_client(['new', 'new2'])
    c.session.post.side_effect = [Resp(TOKEN_INVALID), Resp(TOKEN_INVALID), Resp(OK)]
    res, _ = c._request('/p', 'ka1', {})
    assert res == TOKEN_INVALID
    assert c.session.post.call_count == 2  # 최초 + 재시도 1회 뿐
    assert calls['auth'] == 1
    assert c._alerts, "재인증 후에도 실패하면 알림을 보내야 한다"


def test_forced_reauth_is_throttled():
    c, calls = make_client(['new', 'new2'])
    c.session.post.side_effect = [Resp(TOKEN_INVALID), Resp(TOKEN_INVALID), Resp(TOKEN_INVALID)]
    c._request('/p', 'ka1', {})            # 1차: 재인증 + 재시도(실패)
    n = c.session.post.call_count
    res, _ = c._request('/p', 'ka1', {})   # 2차: 30초 이내이므로 재인증하지 않음
    assert calls['auth'] == 1
    assert c.session.post.call_count == n + 1
    assert res == TOKEN_INVALID


def test_reauth_failure_does_not_retry_and_alerts():
    c, calls = make_client([])  # 인증 실패(토큰 없음)
    c.session.post.side_effect = [Resp(TOKEN_INVALID)]
    res, _ = c._request('/p', 'ka1', {})
    assert res == TOKEN_INVALID
    assert c.session.post.call_count == 1
    assert c._alerts


def test_other_errors_do_not_reauth():
    c, calls = make_client(['new'])
    c.session.post.side_effect = [Resp({'return_code': 5, 'return_msg': '허용된 요청 개수를 초과하였습니다'})]
    res, _ = c._request('/p', 'ka1', {})
    assert res['return_code'] == 5
    assert calls['auth'] == 0


def test_normal_response_unchanged():
    c, calls = make_client(['new'])
    c.session.post.side_effect = [Resp(OK)]
    res, _ = c._request('/p', 'ka1', {})
    assert res == OK and calls['auth'] == 0


def test_stale_token_skips_reauth_when_another_thread_refreshed():
    c, calls = make_client(['new'])
    c.access_token = 'already-new'   # 다른 스레드가 이미 갱신
    assert c._force_reauthenticate('old') is True
    assert calls['auth'] == 0


def test_missing_token_reauths_with_throttle():
    c, calls = make_client(['fresh'])
    c.access_token = None  # 이전 인증 실패 상태, 만료 시각은 미래
    c.session.post.side_effect = [Resp(OK)]
    c._request('/p', 'ka1', {})
    assert calls['auth'] == 1
    assert sent_tokens(c) == ['Bearer fresh']


def test_expired_token_still_refreshes_by_time():
    c, calls = make_client(['new'])
    c.token_expires_in = get_korea_time() - datetime.timedelta(seconds=1)
    c.session.post.side_effect = [Resp(OK)]
    c._request('/p', 'ka1', {})
    assert calls['auth'] == 1
    assert sent_tokens(c) == ['Bearer new']


def test_websocket_force_ensure_reauths_regardless_of_expiry():
    c, calls = make_client(['ws-new'])
    asyncio.run(c._ensure_valid_token_async(force=True))
    assert calls['auth'] == 1 and c.access_token == 'ws-new'


def test_websocket_normal_ensure_does_not_reauth_when_valid():
    c, calls = make_client(['ws-new'])
    asyncio.run(c._ensure_valid_token_async())
    assert calls['auth'] == 0 and c.access_token == 'old'


def test_alert_throttle():
    c = Kiwoom.__new__(Kiwoom)
    c._last_auth_alert_ts = float('-inf')
    sent = []
    import util.notifier as notifier
    orig = notifier.send_telegram_message
    notifier.send_telegram_message = lambda m, *a, **k: sent.append(m) or True
    try:
        c._alert_auth_problem('a')
        c._alert_auth_problem('b')
    finally:
        notifier.send_telegram_message = orig
    assert len(sent) == 1
