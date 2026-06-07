import unittest.mock
import numpy as np
import pandas as pd
import pytest

from backtest.backtest_engine import BacktestEngine


def _make_price_data(
    closes,
    opens=None,
    dates=None,
    code='AAA',
):
    if opens is None:
        opens = closes
    if dates is None:
        dates = [f'202601{2 + i:02d}' for i in range(len(closes))]

    n = len(closes)
    data = {
        'open': opens,
        'high': [c * 1.01 for c in closes],
        'low': [c * 0.99 for c in closes],
        'close': closes,
        'volume': [10000] * n,
    }
    df = pd.DataFrame(data, index=dates)
    df.index.name = 'index'
    return {code: df}


class TestBuyExecutionTPlusOne:

    def test_buy_scheduled_next_day(self):
        dates = ['20260102', '20260103', '20260104']
        price_data = _make_price_data(
            closes=[100, 102, 101],
            opens=[99, 101, 100],
            dates=dates,
        )

        engine = BacktestEngine(initial_capital=10_000_000, max_holdings=10)

        def mock_buy_signal(code, date, df, count):
            if date == '20260102':
                return True, float(df.loc[date, 'close'])
            return False, None

        with unittest.mock.patch.object(engine, 'check_buy_signal', side_effect=mock_buy_signal):
            result = engine.run_backtest(price_data)

        buy_trades = [t for t in engine.trades if t['type'] == 'buy']
        assert len(buy_trades) == 1, f"Expected 1 buy, got {len(buy_trades)}"
        assert buy_trades[0]['date'] == '20260103', (
            f"Buy should execute on T+1 (20260103), got {buy_trades[0]['date']}"
        )
        assert buy_trades[0]['price'] == 101, (
            f"Buy should execute at T+1 open (101), got price={buy_trades[0]['price']}"
        )

    def test_sell_scheduled_next_day(self):
        dates = ['20260102', '20260103', '20260104']
        price_data = _make_price_data(
            closes=[100, 101, 100],
            opens=[99, 100, 99],
            dates=dates,
        )

        engine = BacktestEngine(initial_capital=10_000_000, max_holdings=10)

        # Day 1: buy signal → T+1(day 2)에 매수 체결되어 보유 상태 생성
        # Day 2: 매도 신호 발생 → T+1(day 3)에 매도 체결
        def mock_buy_signal(code, date, df, count):
            if date == '20260102':
                return True, float(df.loc[date, 'close'])
            return False, None

        def mock_sell_signal(code, date, df, avg_price):
            if date == '20260103':
                return True, float(df.loc[date, 'close'])
            return False, None

        with unittest.mock.patch.object(engine, 'check_buy_signal', side_effect=mock_buy_signal), \
             unittest.mock.patch.object(engine, 'check_sell_signal', side_effect=mock_sell_signal):
            result = engine.run_backtest(price_data)

        sell_trades = [t for t in engine.trades if t['type'] == 'sell']
        assert len(sell_trades) == 1, f"Expected 1 sell, got {len(sell_trades)}"
        assert sell_trades[0]['date'] == '20260104', (
            f"Sell should execute on T+1 (20260104), got {sell_trades[0]['date']}"
        )

    def test_sell_same_day_mode(self, monkeypatch):
        monkeypatch.setenv('BACKTEST_SELL_EXECUTION_MODE', 'same_day_close')

        dates = ['20260102', '20260103', '20260104']
        price_data = _make_price_data(
            closes=[100, 101, 100],
            opens=[99, 100, 99],
            dates=dates,
        )

        engine = BacktestEngine(initial_capital=10_000_000, max_holdings=10)

        def mock_buy_signal(code, date, df, count):
            if date == '20260102':
                return True, float(df.loc[date, 'close'])
            return False, None

        def mock_sell_signal(code, date, df, avg_price):
            if date == '20260103':
                return True, float(df.loc[date, 'close'])
            return False, None

        with unittest.mock.patch.object(engine, 'check_buy_signal', side_effect=mock_buy_signal), \
             unittest.mock.patch.object(engine, 'check_sell_signal', side_effect=mock_sell_signal):
            result = engine.run_backtest(price_data)

        sell_trades = [t for t in engine.trades if t['type'] == 'sell']
        assert len(sell_trades) == 1
        assert sell_trades[0]['date'] == '20260103', (
            f"With same_day_close mode, sell should execute on T (20260103), got {sell_trades[0]['date']}"
        )

    def test_buy_same_day_mode(self, monkeypatch):
        monkeypatch.setenv('BACKTEST_BUY_EXECUTION_MODE', 'same_day_close')

        dates = ['20260102', '20260103']
        price_data = _make_price_data(
            closes=[100, 102],
            opens=[99, 101],
            dates=dates,
        )

        engine = BacktestEngine(initial_capital=10_000_000, max_holdings=10)

        def mock_buy_signal(code, date, df, count):
            if date == '20260102':
                return True, float(df.loc[date, 'close'])
            return False, None

        with unittest.mock.patch.object(engine, 'check_buy_signal', side_effect=mock_buy_signal):
            result = engine.run_backtest(price_data)

        buy_trades = [t for t in engine.trades if t['type'] == 'buy']
        assert len(buy_trades) == 1
        assert buy_trades[0]['date'] == '20260102', (
            f"With same_day_close mode, buy should execute on T (20260102), got {buy_trades[0]['date']}"
        )

    def test_tplus1_sell_frees_slot_for_new_signal(self):
        dates = ['20260102', '20260103', '20260104', '20260105']
        price_data = _make_price_data(
            closes=[100, 102, 103, 104],
            opens=[99, 101, 102, 103],
            dates=dates,
            code='AAA',
        )
        price_data['BBB'] = pd.DataFrame({
            'open': [50, 51, 52, 53],
            'high': [52, 53, 54, 55],
            'low': [49, 50, 51, 52],
            'close': [51, 52, 53, 54],
            'volume': [10000, 10000, 10000, 10000],
        }, index=dates)

        engine = BacktestEngine(initial_capital=10_000_000, max_holdings=1)

        # Day 1: AAA 매수 신호 -> Day 2 체결
        # Day 2: AAA 매도 신호 -> Day 3 체결
        # Day 3: 매도 체결로 슬롯 확보 후 BBB 매수 신호 -> Day 4 체결
        def mock_buy_signal(code, date, df, count):
            if date == '20260102' and code == 'AAA':
                return True, float(df.loc[date, 'close'])
            if date == '20260104' and code == 'BBB':
                return True, float(df.loc[date, 'close'])
            return False, None

        def mock_sell_signal(code, date, df, avg_price):
            if code == 'AAA' and date == '20260103':
                return True, float(df.loc[date, 'close'])
            return False, None

        with unittest.mock.patch.object(engine, 'check_buy_signal', side_effect=mock_buy_signal), \
             unittest.mock.patch.object(engine, 'check_sell_signal', side_effect=mock_sell_signal):
            result = engine.run_backtest(price_data)

        buy_trades = [t for t in engine.trades if t['type'] == 'buy']
        assert len(buy_trades) == 2, (
            f"Expected 2 buys (AAA+BBB), got {len(buy_trades)}"
        )

        sell_trades = [t for t in engine.trades if t['type'] == 'sell']
        assert len(sell_trades) == 1, (
            f"Expected 1 sell (AAA), got {len(sell_trades)}"
        )

        assert 'BBB' in engine.holdings, "BBB should be in holdings after all executions"
        assert 'AAA' not in engine.holdings, "AAA should have been sold"

    def test_monthly_snapshot_prev_month_alignment_default(self):
        dates = ['20260102', '20260103', '20260104']
        price_data = _make_price_data(
            closes=[100, 101, 102],
            opens=[99, 100, 101],
            dates=dates,
            code='AAA',
        )
        price_data['BBB'] = pd.DataFrame({
            'open': [200, 201, 202],
            'high': [202, 203, 204],
            'low': [198, 199, 200],
            'close': [201, 202, 203],
            'volume': [10000, 10000, 10000],
        }, index=dates)

        engine = BacktestEngine(initial_capital=10_000_000, max_holdings=1)

        def mock_buy_signal(code, date, df, count):
            return True, float(df.loc[date, 'close'])

        monthly_universe_map = {
            '202512': ['AAA'],
            '202601': ['BBB'],
        }

        with unittest.mock.patch.object(engine, 'check_buy_signal', side_effect=mock_buy_signal):
            result = engine.run_backtest(price_data, monthly_universe_map=monthly_universe_map)

        buy_trades = [t for t in engine.trades if t['type'] == 'buy']
        assert len(buy_trades) >= 1
        assert buy_trades[0]['code'] == 'AAA', (
            f"With default prev_month alignment, first buy should come from 202512 snapshot (AAA), got {buy_trades[0]['code']}"
        )

    def test_last_day_buy_fallback(self):
        dates = ['20260102']
        price_data = _make_price_data(
            closes=[100],
            opens=[99],
            dates=dates,
        )

        engine = BacktestEngine(initial_capital=10_000_000, max_holdings=10)

        def mock_buy_signal(code, date, df, count):
            return True, float(df.loc[date, 'close'])

        with unittest.mock.patch.object(engine, 'check_buy_signal', side_effect=mock_buy_signal):
            result = engine.run_backtest(price_data)

        buy_trades = [t for t in engine.trades if t['type'] == 'buy']
        assert len(buy_trades) == 1
        assert buy_trades[0]['date'] == '20260102', (
            f"Last day buy should execute on T (20260102), got {buy_trades[0]['date']}"
        )

    def test_buy_open_fallback_close(self):
        dates = ['20260102', '20260103']
        price_data = _make_price_data(
            closes=[100, 102],
            opens=[99, np.nan],
            dates=dates,
        )

        engine = BacktestEngine(initial_capital=10_000_000, max_holdings=10)

        def mock_buy_signal(code, date, df, count):
            if date == '20260102':
                return True, float(df.loc[date, 'close'])
            return False, None

        with unittest.mock.patch.object(engine, 'check_buy_signal', side_effect=mock_buy_signal):
            result = engine.run_backtest(price_data)

        buy_trades = [t for t in engine.trades if t['type'] == 'buy']
        assert len(buy_trades) == 1
        assert buy_trades[0]['date'] == '20260103'
        assert buy_trades[0]['price'] == 102, (
            f"Buy should fallback to close (102) when open is NaN, got price={buy_trades[0]['price']}"
        )
