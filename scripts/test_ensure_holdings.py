import pandas as pd
from strategy.RSIStrategy import RSIStrategy
import time

class MockKiwoom:
    def __init__(self):
        self.mock = True
        self.balance = {'237690': {'종목명': '에스티팜'}}
        self.order = {}
        self.set_real_reg_calls = []
        self.universe_realtime_transaction_info = {}
    def get_master_code_name(self, code):
        return '에스티팜' if str(code)=='237690' else f"NAME_{code}"
    def set_real_reg(self, codes, arg):
        self.set_real_reg_calls.append((codes, arg))
    def get_price_data(self, code):
        import pandas as pd
        return pd.DataFrame({'volume': [1, 1]}, index=['20220101', '20220102'])

# Prepare RSIStrategy instance skeleton
rs = RSIStrategy.__new__(RSIStrategy)
rs.strategy_name = 'RSIStrategy'
rs.kiwoom = MockKiwoom()
rs.universe = {}
rs.mock_trade_blacklist = set()
rs.REALTIME_MAX_CODES = 100

# Create 100 existing universe codes with varying last volumes
for i in range(1, 101):
    code = f"{i:06d}"
    # volume increases with i; make code '000050' low volume middle
    vol = i
    df = pd.DataFrame({'volume': [vol-1, vol]}, index=['20220101', '20220102'])
    rs.universe[code] = {'code_name': f'Name_{code}', 'price_df': df}

# Intentionally set some low-volume codes to be the smallest
rs.universe['000001']['price_df'].iloc[-1]['volume'] = 1
rs.universe['000002']['price_df'].iloc[-1]['volume'] = 2
rs.universe['000003']['price_df'].iloc[-1]['volume'] = 3

print('Initial universe count:', len(rs.universe))
print('Has 237690 before:', '237690' in rs.universe)

# Call ensure_holdings_in_universe
rs.ensure_holdings_in_universe()

print('Universe count after:', len(rs.universe))
print('Has 237690 after:', '237690' in rs.universe)

# Which codes were removed? find difference from initial
initial_codes = set(f"{i:06d}" for i in range(1,101))
current_codes = set(rs.universe.keys())
removed = initial_codes - current_codes
print('Removed codes (sample up to 10):', sorted(list(removed))[:10])

# Check real_reg calls
print('set_real_reg calls:', rs.kiwoom.set_real_reg_calls[:3])

# Verify registration order in last call
if rs.kiwoom.set_real_reg_calls:
    last_codes = rs.kiwoom.set_real_reg_calls[-1][0]
    print('Last real_reg length (split by ;) :', len(last_codes.split(';')))
    # show first 10 codes in registration order
    print('First 10 registered codes:', last_codes.split(';')[:10])

print('Done')
