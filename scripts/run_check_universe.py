from util import make_up_universe as mu
import pandas as pd
from strategy.RSIStrategy import RSIStrategy

class MockKiwoom:
    def __init__(self, code_list_records):
        self.mock = True
        self.balance = {}
        self.order = {}
        self._code_list_records = code_list_records

    def get_code_list_by_market(self, market_type):
        if market_type == '0':
            market = '코스피'
        else:
            market = '코스닥'
        return [ {'code': str(r['종목코드']), 'name': r['종목명']} for r in self._code_list_records if r.get('시장구분') == market ]

    def get_master_code_name(self, code):
        for r in self._code_list_records:
            if str(r.get('종목코드')) == str(code):
                return r.get('종목명')
        return None


def main():
    print('Loading cached all_stocks_kiwoom.parquet...')
    df = pd.read_parquet('all_stocks_kiwoom.parquet')
    print('rows:', len(df))
    code_list_records = df.to_dict(orient='records')
    mk = MockKiwoom(code_list_records)
    # Simulate a held position for 237690 to ensure merging behavior
    mk.balance = {'237690': {'종목명': '에스티팜'}}

    def get_universe_stub(kiwoom_client=None, use_kiwoom_api=False):
        return mu._filter_and_create_universe(df, kiwoom_client=kiwoom_client, max_codes=100)

    mu.get_universe = get_universe_stub
    # RSIStrategy imported get_universe at module load; patch it there as well
    import strategy.RSIStrategy as srs
    srs.get_universe = get_universe_stub

    rs = RSIStrategy.__new__(RSIStrategy)
    rs.strategy_name = 'RSIStrategy'
    rs.kiwoom = mk
    rs.universe = {}
    rs.mock_trade_blacklist = set()
    rs.last_universe_update = None

    print('Calling check_and_get_universe(force_update=True)')
    rs.check_and_get_universe(force_update=True)
    print('Universe in memory count:', len(rs.universe))
    print('Contains 237690 in memory:', '237690' in rs.universe)

    from util.db_helper import execute_sql
    cur = execute_sql('RSIStrategy', "select code from universe where code='237690'")
    found = cur.fetchall()
    print('DB contains 237690:', len(found) > 0)
    if found:
        print('DB row sample:', found[:3])

if __name__ == '__main__':
    main()
