"""
Universe 생성 및 캐싱 기능 단위 테스트

실행 방법:
    poetry run pytest tests/test_universe_functions.py -v
"""

import pytest
import pandas as pd
import os
from unittest.mock import Mock, patch, MagicMock
from util.make_up_universe import (
    cache_daily_data,
    fetch_all_stocks_from_kiwoom,
    get_universe,
    _filter_and_create_universe,
    _try_load_cache
)


@pytest.fixture
def mock_kiwoom_client():
    """Mock Kiwoom 클라이언트 fixture"""
    client = Mock()
    client.mock = True
    
    # get_code_list_by_market mock
    def get_code_list_side_effect(market_type):
        if market_type == "0":
            return [
                {'code': '005930', 'name': '삼성전자'},
                {'code': '000660', 'name': 'SK하이닉스'},
            ]
        else:
            return [
                {'code': '035720', 'name': '카카오'},
            ]
    
    client.get_code_list_by_market.side_effect = get_code_list_side_effect
    
    # get_stock_info mock
    def get_stock_info_side_effect(code):
        mock_data = {
            '005930': {
                'name': '삼성전자',
                'cur_prc': '70000',
                'trde_qty': '12345678',
                'trde_amt': '86400',
                'mrkt_cap': '4180000',
                'flu_rt': '1.5',
                'for_exh_rt': '56.7',
                'list_cnt': '5969783'
            },
            '000660': {
                'name': 'SK하이닉스',
                'cur_prc': '135000',
                'trde_qty': '5678901',
                'trde_amt': '76500',
                'mrkt_cap': '982000',
                'flu_rt': '2.1',
                'for_exh_rt': '52.3',
                'list_cnt': '728002'
            },
            '035720': {
                'name': '카카오',
                'cur_prc': '45000',
                'trde_qty': '2345678',
                'trde_amt': '10555',
                'mrkt_cap': '195000',
                'flu_rt': '-1.2',
                'for_exh_rt': '42.1',
                'list_cnt': '433457'
            }
        }
        return mock_data.get(code)
    
    client.get_stock_info.side_effect = get_stock_info_side_effect
    
    return client


@pytest.fixture
def sample_all_stocks_df():
    """전체 종목 샘플 DataFrame"""
    return pd.DataFrame({
        '종목코드': ['005930', '000660', '035720'],
        '종목명': ['삼성전자', 'SK하이닉스', '카카오'],
        '시장구분': ['코스피', '코스피', '코스닥'],
        '현재가': [70000, 135000, 45000],
        '거래량': [12345678, 5678901, 2345678],
        '거래대금': [86400, 76500, 10555],
        '시가총액': [4180000, 982000, 195000],
        '등락률': [1.5, 2.1, -1.2],
        '외국인비율': [56.7, 52.3, 42.1],
        '상장주식수': [5969783, 728002, 433457]
    })


class TestCacheDailyData:
    """cache_daily_data 함수 테스트"""
    
    @patch('util.make_up_universe.fetch_all_stocks_from_kiwoom')
    def test_cache_daily_data_success(self, mock_create, mock_kiwoom_client, sample_all_stocks_df):
        """정상 캠싱 테스트"""
        mock_create.return_value = sample_all_stocks_df
        
        result = cache_daily_data(mock_kiwoom_client)
        
        assert result is not None
        assert len(result) == 3
        mock_create.assert_called_once_with(mock_kiwoom_client, use_cache=False, save_cache=True)
    
    @patch('util.make_up_universe.fetch_all_stocks_from_kiwoom')
    def test_cache_daily_data_failure(self, mock_create, mock_kiwoom_client):
        """캐싱 실패 테스트"""
        mock_create.side_effect = Exception("API Error")
        
        with pytest.raises(Exception):
            cache_daily_data(mock_kiwoom_client)


class TestFetchAllStocksFromKiwoom:
    """fetch_all_stocks_from_kiwoom 함수 테스트"""
    
    @patch('util.make_up_universe.time.sleep')  # Rate limit 대기 제거
    @patch('util.make_up_universe.pd.DataFrame.to_parquet')
    def test_fetch_all_stocks_basic(self, mock_to_parquet, mock_sleep, mock_kiwoom_client):
        """기본 생성 테스트"""
        df = fetch_all_stocks_from_kiwoom(mock_kiwoom_client, use_cache=False)
        
        assert df is not None
        assert len(df) == 3  # 2개 코스피 + 1개 코스닥
        assert '종목코드' in df.columns
        assert '종목명' in df.columns
        assert '시장구분' in df.columns
        
        # 캐시 저장 확인
        mock_to_parquet.assert_called_once()
    
    @patch('util.make_up_universe.time.sleep')
    def test_fetch_all_stocks_market_separation(self, mock_sleep, mock_kiwoom_client):
        """시장 구분 테스트"""
        df = fetch_all_stocks_from_kiwoom(mock_kiwoom_client, use_cache=False)
        
        kospi_count = len(df[df['시장구분'] == '코스피'])
        kosdaq_count = len(df[df['시장구분'] == '코스닥'])
        
        assert kospi_count == 2
        assert kosdaq_count == 1


class TestFilterAndCreateUniverse:
    """_filter_and_create_universe 함수 테스트"""
    
    def test_filter_basic(self, sample_all_stocks_df):
        """기본 필터링 테스트"""
        universe_list = _filter_and_create_universe(sample_all_stocks_df)
        
        assert isinstance(universe_list, list)
        assert len(universe_list) > 0
        assert all(isinstance(name, str) for name in universe_list)
    
    def test_filter_removes_invalid_data(self):
        """잘못된 데이터 필터링 테스트"""
        df = pd.DataFrame({
            '종목코드': ['000001', '000002'],
            '종목명': ['테스트1', '테스트2'],
            '시장구분': ['코스피', '코스피'],
            '거래량': [0, 1000000],  # 첫 번째는 거래량 0
            '거래대금': [0, 5000],   # 첫 번째는 거래대금 0
            '시가총액': [1000, 100000],
            '등락률': [0, 1.5],
            '외국인비율': [0, 30.0],
        })
        
        universe_list = _filter_and_create_universe(df)
        
        # 거래량 0인 종목은 제외되어야 함
        assert '테스트1' not in universe_list


class TestTryLoadCache:
    """_try_load_cache 함수 테스트"""
    
    @patch('util.make_up_universe.os.path.exists')
    @patch('util.make_up_universe.pd.read_parquet')
    def test_load_kiwoom_cache_first(self, mock_read_parquet, mock_exists, sample_all_stocks_df):
        """키움 캐시 우선 로드 테스트"""
        mock_exists.return_value = True
        mock_read_parquet.return_value = sample_all_stocks_df
        
        result = _try_load_cache()
        
        assert result is not None
        assert len(result) == 3
        # 첫 번째 파일(all_stocks_kiwoom.parquet)을 시도했는지 확인
        mock_read_parquet.assert_called()
    
    @patch('util.make_up_universe.os.path.exists')
    def test_load_no_cache(self, mock_exists):
        """캐시 없을 때 테스트"""
        mock_exists.return_value = False
        
        result = _try_load_cache()
        
        assert result is None


class TestGetUniverse:
    """get_universe 함수 통합 테스트"""
    
    @patch('util.make_up_universe.execute_crawler')
    @patch('util.make_up_universe._filter_and_create_universe')
    def test_get_universe_crawler_success(self, mock_filter, mock_crawler, sample_all_stocks_df):
        """네이버 크롤링 성공 테스트"""
        mock_crawler.return_value = sample_all_stocks_df
        mock_filter.return_value = ['삼성전자', 'SK하이닉스', '카카오']
        
        result = get_universe()
        
        assert result == ['삼성전자', 'SK하이닉스', '카카오']
        mock_crawler.assert_called_once()
    
    @patch('util.make_up_universe.check_transaction_closed')
    @patch('util.make_up_universe.fetch_all_stocks_from_kiwoom')
    @patch('util.make_up_universe._filter_and_create_universe')
    def test_get_universe_api_after_market_close(
        self, mock_filter, mock_fetch_api, mock_check_closed, 
        mock_kiwoom_client, sample_all_stocks_df
    ):
        """장 종료 후 자동 API 사용 테스트"""
        mock_check_closed.return_value = True
        mock_fetch_api.return_value = sample_all_stocks_df
        mock_filter.return_value = ['삼성전자']
        
        result = get_universe(kiwoom_client=mock_kiwoom_client)
        
        assert result == ['삼성전자']
        mock_fetch_api.assert_called_once()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
