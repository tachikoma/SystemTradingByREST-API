"""섀도우 모드 테스트: SHADOW_MODE=1이면 send_order가 절대 호출되지 않아야 한다.

이 환경에는 numpy/pandas 휠이 없으므로, RSIStrategy가 사용하는
최소 API(isfinite/isnan/isinf)만 스텁으로 제공하고 pandas는 빈 모듈로 대체한다.
테스트 대상인 섀도우 분기 로직에는 수치 연산이 포함되지 않으므로 유효하다.
"""

import math
import os
import sys
import pathlib
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

# --- 최소 스텁 (RSIStrategy import 전에 등록) ---
_np_stub = types.ModuleType("numpy")
_np_stub.nan = float("nan")
_np_stub.inf = float("inf")


def _to_float(x):
    try:
        return float(x)
    except Exception:
        return None


_np_stub.isnan = lambda x: (_to_float(x) is not None and math.isnan(_to_float(x)))  # noqa: E731
_np_stub.isinf = lambda x: (_to_float(x) is not None and math.isinf(_to_float(x)))  # noqa: E731
_np_stub.isfinite = lambda x: (_to_float(x) is not None and math.isfinite(_to_float(x)))  # noqa: E731
sys.modules.setdefault("numpy", _np_stub)
sys.modules.setdefault("pandas", types.ModuleType("pandas"))

from strategy.RSIStrategy import RSIStrategy  # noqa: E402
from util import trade_logger  # noqa: E402


class _FakeILoc:
    def __init__(self, rows):
        self._rows = rows

    def __getitem__(self, key):
        if isinstance(key, slice):
            return _FakeFrame(self._rows[key])
        return self._rows[key]


class _FakeCol:
    def __init__(self, values):
        self._values = values

    @property
    def iloc(self):
        return _FakeILoc(self._values)

    @property
    def values(self):
        return list(self._values)


class _FakeFrame:
    """calculate_rsi 반환 DataFrame 대역. close/RSI 컬럼과 iloc/len만 지원."""

    def __init__(self, rows):
        self._rows = rows

    def __len__(self):
        return len(self._rows)

    def __getitem__(self, col):
        if isinstance(col, str):
            return _FakeCol([r[col] for r in self._rows])
        raise KeyError(col)

    @property
    def iloc(self):
        return _FakeILoc(self._rows)


class FakeKiwoom:
    mock = False
    balance = {}
    order = {}
    universe_realtime_transaction_info = {}

    def send_order(self, *args, **kwargs):
        raise AssertionError("send_order must not be called in shadow mode")


def make_shadow_strategy(**overrides):
    """__init__의 무거운 초기화 없이 섀도우 분기에 필요한 속성만 갖춘 인스턴스 생성."""
    s = RSIStrategy.__new__(RSIStrategy)
    s.kiwoom = FakeKiwoom()
    s.shadow_mode = True
    s.pause_new_buys = False
    s.universe_map = {}
    s.universe = {}
    s._last_sell_reason = ""
    s.allow_kiwoom_calls = False
    s.BUY_FEE_RATE = 1 + (0.015 / 100)
    s.SELL_FEE_RATE = 1 + ((0.015 + 0.20) / 100)
    s.RSI_BUY_THRESHOLD = 3
    s.PRICE_DROP_THRESHOLD = -5.0
    s.CASH_RESERVE_RATIO = 0.2
    s.MAX_HOLDINGS = 10
    s.MAX_POSITION_RATIO = 0.20
    s.USE_MA20_FILTER = False
    s.RSI_PERIOD = 2
    s.MA_SHORT = 20
    s.MA_LONG = 60
    s.MA_TREND = 200
    s.MORNING_FALLBACK_GAP_UP_THRESHOLD = 3.0
    s.buy_window_done_today = False
    s.deposit = 10_000_000
    s.mock_trade_blacklist = set()
    s._buy_pause_log_date = None
    s.sent_messages = []
    s._queue_or_send = lambda msg: s.sent_messages.append(msg)  # noqa: E731
    s.resolve_stock_name = lambda code: "테스트종목"  # noqa: E731
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def test_shadow_sell_does_not_call_send_order(monkeypatch):
    s = make_shadow_strategy()
    s.kiwoom.balance = {"005930": {"보유수량": 10, "매매가능수량": 10, "매입가": 70000}}
    s.kiwoom.universe_realtime_transaction_info = {
        "005930": {"(최우선)매도호가": 71000, "현재가": 71000}
    }

    def _fail_send_order(*args, **kwargs):
        raise AssertionError("send_order must not be called in shadow mode")

    monkeypatch.setattr(s.kiwoom, "send_order", _fail_send_order)
    logged = []
    monkeypatch.setattr(trade_logger, "log_trade", lambda **kw: logged.append(kw))

    s.order_sell("005930", sell_reason="TEST")

    assert logged and logged[0]["order_no"] == "SHADOW"
    assert logged[0]["action"] == "SELL"
    assert any("SHADOW" in m for m in s.sent_messages)


def test_shadow_buy_does_not_call_send_order(monkeypatch):
    import util.time_helper as time_helper

    s = make_shadow_strategy()
    s.kiwoom.universe_realtime_transaction_info = {
        "005930": {"(최우선)매수호가": 70000, "현재가": 70000}
    }
    s.universe = {
        "005930": {
            "code_name": "테스트종목",
            "close_count": 250,
            "ma_latest": {"ma20": 100.0, "ma60": 100.0, "ma200": 80.0},
        }
    }

    # RSI(2)=2, 2거래일간 -6% 급락, 종가 > MA200 → 매수 신호 조건 충족
    df = _FakeFrame(
        [
            {"close": 100.0, "RSI(2)": 50.0},
            {"close": 100.0, "RSI(2)": 50.0},
            {"close": 94.0, "RSI(2)": 2.0},
        ]
    )
    monkeypatch.setattr(s, "calculate_rsi", lambda code: (df, 94.0))
    monkeypatch.setattr(time_helper, "is_buy_window_open", lambda: True)
    monkeypatch.setattr(time_helper, "is_morning_buy_fallback_window", lambda: False)

    def _fail_send_order(*args, **kwargs):
        raise AssertionError("send_order must not be called in shadow mode")

    monkeypatch.setattr(s.kiwoom, "send_order", _fail_send_order)
    logged = []
    monkeypatch.setattr(trade_logger, "log_trade", lambda **kw: logged.append(kw))

    result = s.check_buy_signal_and_order("005930")

    assert result is True
    assert logged and logged[0]["order_no"] == "SHADOW"
    assert logged[0]["action"] == "BUY"
    # 섀도우 매수는 예수금을 차감하지 않는다
    assert s.deposit == 10_000_000
    assert any("SHADOW" in m for m in s.sent_messages)


def test_shadow_flag_env_parsing(monkeypatch):
    monkeypatch.setenv("SHADOW_MODE", "1")
    monkeypatch.delenv("DRY_RUN", raising=False)
    flag = os.getenv("SHADOW_MODE", os.getenv("DRY_RUN", "0"))
    assert str(flag).lower() in ("1", "true", "yes")

    monkeypatch.setenv("SHADOW_MODE", "0")
    monkeypatch.setenv("DRY_RUN", "yes")
    flag = os.getenv("SHADOW_MODE", os.getenv("DRY_RUN", "0"))
    # SHADOW_MODE가 우선하므로 0 → 비활성
    assert str(flag).lower() not in ("1", "true", "yes")
