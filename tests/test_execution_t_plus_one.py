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

    def test_sell_executed_same_day(self):
        dates = ['20260102', '20260103', '20260104']
        price_data = _make_price_data(
            closes=[100, 101, 100],
            opens=[99, 100, 99],
            dates=dates,
        )

        engine = BacktestEngine(initial_capital=10_000_000, max_holdings=10)

        # Day 1: buy signal → T+1(day 2)에 매수 체결되어 보유 상태 생성
        # Day 2: 매수 실행(happens in Step 0) 후, 매도 신호 → 같은 날 매도
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
        assert sell_trades[0]['date'] == '20260103', (
            f"Sell should execute on T (20260103), got {sell_trades[0]['date']}"
        )

    def test_sell_frees_slot_for_buy_same_day(self):
        dates = ['20260102', '20260103', '20260104']
        price_data = _make_price_data(
            closes=[100, 102, 103],
            opens=[99, 101, 102],
            dates=dates,
            code='AAA',
        )
        price_data['BBB'] = pd.DataFrame({
            'open': [50, 51, 52],
            'high': [52, 53, 54],
            'low': [49, 50, 51],
            'close': [51, 52, 53],
            'volume': [10000, 10000, 10000],
        }, index=dates)

        engine = BacktestEngine(initial_capital=10_000_000, max_holdings=1)

        # Day 1 (20260102): buy AAA 신호 → T+1 예약
        # Day 2 (20260103): AAA 매수 실행(Step0) → AAA 매도 신호(Step1, same-day) → BBB 매수 신호(Step2, T+1 예약)
        # Day 3 (20260104): BBB 매수 실행(Step0)
        def mock_buy_signal(code, date, df, count):
            if date == '20260102' and code == 'AAA':
                return True, float(df.loc[date, 'close'])
            if date == '20260103' and code == 'BBB':
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
