"""메모리 Universe 교체 회귀 테스트 (재구성 후 self.universe 누적 문제).

격리 DB/가짜 Kiwoom만 사용. 실주문·네트워크 없음.
"""
import pandas as pd
import pytest

import strategy.RSIStrategy as rs
from strategy.RSIStrategy import _parse_liquidation_mode
from test_universe_hotfix import (  # noqa: F401  (env는 pytest fixture로 재사용)
    ForbiddenKiwoom, db_codes, env, make_strategy,
)


@pytest.mark.parametrize("raw,expected", [
    (None, 'dry_run'), ('', 'dry_run'), ('dry_run', 'dry_run'), ('dry-run', 'dry_run'),
    ('typo', 'dry_run'), ('LIVE', 'live'), (' live ', 'live'), ('live', 'live'),
])
def test_liquidation_mode_defaults_to_dry_run(raw, expected):
    assert _parse_liquidation_mode(raw) == expected


def _new_names(lo, hi):
    return [f'종목{i}' for i in range(lo, hi)]  # make_strategy의 get_code_list: 종목0..299


def test_rebuild_replaces_memory_universe_and_keeps_price_data(env, monkeypatch):
    st = make_strategy(env, monkeypatch)  # 기존 260 (코드 001000..), 이름 OLDn
    st.universe['000010'] = {'code_name': '종목10', 'ma_latest': {'ma20': 1.0}}  # 새 Universe와 겹치는 종목 + 가격 데이터
    monkeypatch.setattr(rs, 'get_universe', lambda **k: _new_names(0, 250))
    assert st.check_and_get_universe(force_update=True) is True
    assert len(st.universe) == 250                                  # 누적(510개) 아님
    assert set(st.universe) == db_codes(st)                          # 메모리 == DB
    assert not any(c.startswith('001') and c in st.universe for c in [f'{i:06d}' for i in range(1000, 1260)])
    assert st.universe['000010']['ma_latest'] == {'ma20': 1.0}      # 기존 가격 데이터 보존


def test_excluded_holding_and_pending_order_stay_monitored_without_orders(env, monkeypatch):
    held = {'001005': {'종목명': 'HELD', '보유수량': 7, '매입가': 1000}}  # 기존 Universe(001000..)에만 있음
    kw = ForbiddenKiwoom(balance=held)
    kw.order = {'001006': {'미체결수량': 3}}                              # 기존 Universe에만 있는 미체결 주문
    st = make_strategy(env, monkeypatch, kw)
    st.universe_liquidation_mode = 'dry_run'
    monkeypatch.setattr(rs, 'get_universe', lambda **k: _new_names(0, 250))
    assert st.update_universe_with_holdings() is True
    assert kw.orders_sent == []                                      # 청산/기타 주문 0건
    assert '001005' in st.universe and '001006' in st.universe       # 감시 유지
    assert len(st.universe) == 252                                   # 신규 250 + 보유 1 + 미체결 1 (누적 아님)
    assert '001007' not in st.universe                               # 나머지 옛 종목은 제거


def test_diff_summary_notification_lists_excluded_holdings(env, monkeypatch):
    kw = ForbiddenKiwoom(balance={'001005': {'종목명': 'HELD', '보유수량': 7, '매입가': 1000}})
    st = make_strategy(env, monkeypatch, kw)
    monkeypatch.setattr(rs, 'get_universe', lambda **k: _new_names(0, 250))
    st.update_universe_with_holdings()
    summary = [m for m in st.messages if m.startswith('📊 Universe 재구성 비교')]
    assert len(summary) == 1
    assert '기존 260개 → 신규 250개' in summary[0] and '제외 보유종목' in summary[0] and '001005' in summary[0]
    assert 'dry_run' in summary[0] or 'live' in summary[0]


def test_live_mode_sends_order_only_for_excluded_holding(env, monkeypatch):
    """live를 명시한 경우에만 제외 보유종목 청산 주문이 (가짜 클라이언트로) 접수된다."""
    held = {'001005': {'종목명': 'HELD', '보유수량': 7, '매입가': 1000},
            '000010': {'종목명': 'KEEP', '보유수량': 3, '매입가': 1000}}  # 000010은 새 Universe에 포함
    kw = ForbiddenKiwoom(balance=held)
    st = make_strategy(env, monkeypatch, kw)
    st.universe_liquidation_mode = 'live'
    monkeypatch.setattr(rs, 'get_universe', lambda **k: _new_names(0, 250))
    st.update_universe_with_holdings()
    assert len(kw.orders_sent) == 1 and kw.orders_sent[0][0][3] == '001005'


def test_failed_rebuild_keeps_memory_unchanged(env, monkeypatch):
    st = make_strategy(env, monkeypatch)
    before = set(st.universe)
    monkeypatch.setattr(rs, 'get_universe', lambda **k: (_ for _ in ()).throw(Exception("x")))
    assert st.check_and_get_universe(force_update=True) is False
    assert set(st.universe) == before
