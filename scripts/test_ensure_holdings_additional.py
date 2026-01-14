import pandas as pd
from strategy.RSIStrategy import RSIStrategy
import time

class MockKiwoom:
    def __init__(self, balance_codes):
        self.mock = True
        self.balance = {c: {'종목명': f'Name_{c}'} for c in balance_codes}
        self.order = {}
        self.set_real_reg_calls = []
        self.universe_realtime_transaction_info = {}
    def get_master_code_name(self, code):
        return self.balance.get(code, {}).get('종목명', f'NAME_{code}')
    def set_real_reg(self, codes, arg):
        self.set_real_reg_calls.append((codes, arg))
    def get_price_data(self, code):
        # Return a minimal price_df with a volume column (two rows)
        import pandas as pd
        return pd.DataFrame({'volume': [1, 1]}, index=['20220101', '20220102'])


def build_strategy_with_universe(universe_size=100):
    rs = RSIStrategy.__new__(RSIStrategy)
    rs.strategy_name = 'RSIStrategy'
    rs.kiwoom = None
    rs.universe = {}
    rs.mock_trade_blacklist = set()
    rs.REALTIME_MAX_CODES = 100
    # create universe with ascending volumes
    for i in range(1, universe_size+1):
        code = f"{i:06d}"
        vol = i
        df = pd.DataFrame({'volume': [vol-1, vol]}, index=['20220101', '20220102'])
        rs.universe[code] = {'code_name': f'Name_{code}', 'price_df': df}
    return rs


def scenario_many_holdings():
    print('--- Scenario A: Many held codes (5) ---')
    # create strategy with 100 existing codes
    rs = build_strategy_with_universe(100)
    # held codes: 5 new codes not in existing universe
    held_codes = [f"9{900+i}" for i in range(5)]  # e.g., 9900..9904
    mk = MockKiwoom(held_codes)
    rs.kiwoom = mk

    # call ensure
    rs.ensure_holdings_in_universe()

    print('Universe count after:', len(rs.universe))
    print('Held codes present:', [c for c in held_codes if c in rs.universe])
    # removed candidates
    initial_codes = set(f"{i:06d}" for i in range(1,101))
    removed = initial_codes - set(rs.universe.keys())
    print('Number removed:', len(removed), 'sample:', sorted(list(removed))[:10])
    if mk.set_real_reg_calls:
        last = mk.set_real_reg_calls[-1][0]
        print('First registered codes:', last.split(';')[:10])
    print('')


def scenario_insufficient_removable():
    print('--- Scenario B: Insufficient removable candidates ---')
    # Make existing universe where almost all entries are also in held set. We'll mark 98 existing as held and add 10 new held codes -> excess
    rs = build_strategy_with_universe(100)
    # define held set including most existing universe codes
    held_existing = [f"{i:06d}" for i in range(1,99)]  # 98 existing held
    new_held = [f"8{800+i}" for i in range(10)]       # 10 new held codes
    held_codes = held_existing + new_held
    mk = MockKiwoom(held_codes)
    rs.kiwoom = mk

    rs.ensure_holdings_in_universe()

    print('Universe count after:', len(rs.universe))
    # Which existing were removed
    initial_codes = set(f"{i:06d}" for i in range(1,101))
    removed = initial_codes - set(rs.universe.keys())
    print('Removed count:', len(removed), 'sample:', sorted(list(removed))[:10])
    # Check logs: since removable less than excess, expect warning logged (we'll print real_reg calls)
    if mk.set_real_reg_calls:
        last = mk.set_real_reg_calls[-1][0]
        print('Registered count:', len(last.split(';')))
        print('First registered codes:', last.split(';')[:10])
    print('')


def main():
    scenario_many_holdings()
    scenario_insufficient_removable()

if __name__ == '__main__':
    main()
