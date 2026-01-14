#!/usr/bin/env python3
"""
Universe 생성 및 캐싱 기능 테스트 스크립트

실행 방법:
    # Mock 데이터로 빠른 테스트 (추천, 1초 이내)
    poetry run python test_universe_cache.py
    
    # 실제 API 빠른 검증 (10개 종목, 약 1초)
    poetry run python test_universe_cache.py --quick
    
    # 실제 API 전체 테스트 (실전투자 키 필요, 약 7분 소요)
    poetry run python test_universe_cache.py --real-api
    
    # 확인 없이 바로 실행 (백그라운드 실행용)
    poetry run python test_universe_cache.py --real-api -y
    nohup poetry run python test_universe_cache.py --real-api -y > test_output.log 2>&1 &
"""

import os
import sys
import argparse
import time
from pathlib import Path
from dotenv import load_dotenv

# 환경변수 로드
load_dotenv(dotenv_path=Path(__file__).parent / '.env')

# 로깅 초기화
from util.logging_config import configure_logging
configure_logging()

from util.make_up_universe import cache_daily_data, get_universe
from api.Kiwoom import Kiwoom
import logging
import pandas as pd

logger = logging.getLogger(__name__)


class MockKiwoomClient:
    """테스트용 Mock Kiwoom 클라이언트"""
    
    def __init__(self):
        self.mock = True
        logger.info("MockKiwoomClient 초기화")
    
    def get_code_list_by_market(self, market_type):
        """Mock 종목 리스트 반환"""
        logger.info(f"Mock: get_code_list_by_market({market_type})")
        
        if market_type == "0":  # 코스피
            return [
                {'code': '005930', 'name': '삼성전자'},
                {'code': '000660', 'name': 'SK하이닉스'},
                {'code': '035420', 'name': 'NAVER'},
                {'code': '051910', 'name': 'LG화학'},
                {'code': '006400', 'name': '삼성SDI'},
                {'code': '005380', 'name': '현대차'},
                {'code': '000270', 'name': '기아'},
                {'code': '068270', 'name': '셀트리온'},
                {'code': '207940', 'name': '삼성바이오로직스'},
                {'code': '005490', 'name': 'POSCO홀딩스'},
            ]
        else:  # 코스닥
            return [
                {'code': '035720', 'name': '카카오'},
                {'code': '247540', 'name': '에코프로비엠'},
                {'code': '086520', 'name': '에코프로'},
                {'code': '091990', 'name': '셀트리온헬스케어'},
                {'code': '066570', 'name': 'LG전자'},
            ]
    
    def get_stock_info(self, code):
        """Mock 종목 상세 정보 반환"""
        logger.debug(f"Mock: get_stock_info({code})")
        
        # Mock 데이터
        mock_data = {
            '005930': {'name': '삼성전자', 'cur_prc': '70000', 'trde_qty': '12345678', 
                      'trde_amt': '86400', 'mrkt_cap': '4180000', 'flu_rt': '1.5', 
                      'for_exh_rt': '56.7', 'list_cnt': '5969783'},
            '000660': {'name': 'SK하이닉스', 'cur_prc': '135000', 'trde_qty': '5678901',
                      'trde_amt': '76500', 'mrkt_cap': '982000', 'flu_rt': '2.1',
                      'for_exh_rt': '52.3', 'list_cnt': '728002'},
            '035420': {'name': 'NAVER', 'cur_prc': '180000', 'trde_qty': '890123',
                      'trde_amt': '16020', 'mrkt_cap': '295000', 'flu_rt': '-0.8',
                      'for_exh_rt': '48.9', 'list_cnt': '164263'},
            '051910': {'name': 'LG화학', 'cur_prc': '380000', 'trde_qty': '456789',
                      'trde_amt': '17358', 'mrkt_cap': '268000', 'flu_rt': '0.5',
                      'for_exh_rt': '35.2', 'list_cnt': '70592'},
            '006400': {'name': '삼성SDI', 'cur_prc': '450000', 'trde_qty': '345678',
                      'trde_amt': '15555', 'mrkt_cap': '302000', 'flu_rt': '3.2',
                      'for_exh_rt': '41.8', 'list_cnt': '67171'},
            '005380': {'name': '현대차', 'cur_prc': '220000', 'trde_qty': '678901',
                      'trde_amt': '14938', 'mrkt_cap': '472000', 'flu_rt': '0.9',
                      'for_exh_rt': '33.2', 'list_cnt': '214574'},
            '000270': {'name': '기아', 'cur_prc': '95000', 'trde_qty': '789012',
                      'trde_amt': '7496', 'mrkt_cap': '387000', 'flu_rt': '1.2',
                      'for_exh_rt': '29.8', 'list_cnt': '407585'},
            '068270': {'name': '셀트리온', 'cur_prc': '170000', 'trde_qty': '456789',
                      'trde_amt': '7765', 'mrkt_cap': '238000', 'flu_rt': '-0.6',
                      'for_exh_rt': '45.6', 'list_cnt': '140000'},
            '207940': {'name': '삼성바이오로직스', 'cur_prc': '880000', 'trde_qty': '123456',
                      'trde_amt': '10868', 'mrkt_cap': '629000', 'flu_rt': '2.3',
                      'for_exh_rt': '51.2', 'list_cnt': '71500'},
            '005490': {'name': 'POSCO홀딩스', 'cur_prc': '420000', 'trde_qty': '234567',
                      'trde_amt': '9852', 'mrkt_cap': '365000', 'flu_rt': '1.8',
                      'for_exh_rt': '40.5', 'list_cnt': '86956'},
            '035720': {'name': '카카오', 'cur_prc': '45000', 'trde_qty': '2345678',
                      'trde_amt': '10555', 'mrkt_cap': '195000', 'flu_rt': '-1.2',
                      'for_exh_rt': '42.1', 'list_cnt': '433457'},
            '247540': {'name': '에코프로비엠', 'cur_prc': '280000', 'trde_qty': '567890',
                      'trde_amt': '15912', 'mrkt_cap': '185000', 'flu_rt': '5.6',
                      'for_exh_rt': '28.4', 'list_cnt': '66071'},
            '086520': {'name': '에코프로', 'cur_prc': '520000', 'trde_qty': '234567',
                      'trde_amt': '12198', 'mrkt_cap': '172000', 'flu_rt': '7.8',
                      'for_exh_rt': '31.2', 'list_cnt': '33086'},
            '091990': {'name': '셀트리온헬스케어', 'cur_prc': '85000', 'trde_qty': '345678',
                      'trde_amt': '2938', 'mrkt_cap': '115000', 'flu_rt': '2.4',
                      'for_exh_rt': '38.9', 'list_cnt': '135294'},
            '066570': {'name': 'LG전자', 'cur_prc': '120000', 'trde_qty': '456789',
                      'trde_amt': '5481', 'mrkt_cap': '201000', 'flu_rt': '1.5',
                      'for_exh_rt': '36.7', 'list_cnt': '167500'},
        }
        
        return mock_data.get(code, None)


def test_with_mock():
    """Mock 데이터로 빠른 테스트"""
    logger.info("=" * 60)
    logger.info("Mock 데이터로 테스트 시작")
    logger.info("=" * 60)
    
    mock_client = MockKiwoomClient()
    
    # 1. cache_daily_data 테스트
    logger.info("\n[테스트 1] cache_daily_data() 테스트")
    try:
        df = cache_daily_data(mock_client)
        logger.info(f"✅ 성공: {len(df)}개 종목 캐싱 완료")
        logger.info(f"컬럼: {list(df.columns)}")
        logger.info(f"샘플 데이터:\n{df.head(3)}")
        
        # 파일 생성 확인
        cache_file = 'all_stocks_kiwoom.xlsx'
        if os.path.exists(cache_file):
            logger.info(f"✅ 캐시 파일 생성 확인: {cache_file}")
        else:
            logger.warning(f"⚠️ 캐시 파일 미생성: {cache_file}")
    except Exception as e:
        logger.error(f"❌ 실패: {e}", exc_info=True)
        return False
    
    # 2. get_universe 테스트
    logger.info("\n[테스트 2] get_universe() 테스트 (Mock 우선)")
    try:
        universe_list = get_universe(kiwoom_client=mock_client, use_kiwoom_api=False)
        logger.info(f"✅ 성공: {len(universe_list)}개 종목 선정")
        logger.info(f"선정 종목: {universe_list[:5]}...")
        
        # universe.xlsx 파일 확인
        universe_file = 'universe.xlsx'
        if os.path.exists(universe_file):
            logger.info(f"✅ Universe 파일 생성 확인: {universe_file}")
        else:
            logger.warning(f"⚠️ Universe 파일 미생성: {universe_file}")
    except Exception as e:
        logger.error(f"❌ 실패: {e}", exc_info=True)
        return False
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ Mock 테스트 모두 통과!")
    logger.info("=" * 60)
    return True


def test_with_real_api(quick_mode=False):
    """실제 API로 통합 테스트"""
    logger.info("=" * 60)
    if quick_mode:
        logger.info("실제 API 빠른 검증 테스트 시작 (10개 종목만)")
        logger.info("⚠️  약 1초 소요")
    else:
        logger.info("실제 API로 통합 테스트 시작")
        logger.info("⚠️  약 7분 소요 (4234개 종목)")
    logger.info("=" * 60)
    
    # 실전투자 키 확인
    appkey = os.environ.get('KIWOOM_REAL_APPKEY')
    secretkey = os.environ.get('KIWOOM_REAL_SECRETKEY')
    
    if not appkey or not secretkey:
        logger.error("❌ KIWOOM_REAL_APPKEY와 KIWOOM_REAL_SECRETKEY가 필요합니다")
        logger.error("백테스트 데이터 수집은 실전투자 키를 사용합니다")
        return False
    
    logger.info("키움 API 클라이언트 초기화 중...")
    kiwoom = Kiwoom(appkey=appkey, secretkey=secretkey, mock=False)
    
    if quick_mode:
        # 빠른 테스트: 코스피 10개만
        logger.info("\n[빠른 검증] 코스피 10개 종목만 수집")
        try:
            # 전체 종목 리스트 가져오기
            kospi_list = kiwoom.get_code_list_by_market("0")  # 코스피
            logger.info(f"코스피 전체: {len(kospi_list)}개 종목")
            
            # 첫 10개만 테스트
            test_stocks = kospi_list[:10]
            logger.info(f"테스트 대상: {[s['name'] for s in test_stocks]}")
            
            data = []
            for i, stock in enumerate(test_stocks):
                code = stock['code']
                name = stock['name']
                
                try:
                    info = kiwoom.get_stock_info(code)
                    if info:
                        data.append({
                            '종목코드': code,
                            '종목명': name,
                            '시장구분': '코스피',
                            '현재가': info.get('cur_prc', 0),
                            '거래량': info.get('trde_qty', 0),
                            '거래대금': info.get('trde_amt', 0),
                            '시가총액': info.get('mrkt_cap', 0),
                            '등락률': info.get('flu_rt', 0),
                            '외국인비율': info.get('for_exh_rt', 0),
                            '상장주식수': info.get('list_cnt', 0)
                        })
                        logger.info(f"  [{i+1}/10] {name} - 성공")
                    time.sleep(0.1)  # Rate limit
                except Exception as e:
                    logger.warning(f"  [{i+1}/10] {name} - 실패: {e}")
            
            if data:
                df = pd.DataFrame(data)
                logger.info(f"\n✅ 성공: {len(df)}개 종목 수집 완료")
                logger.info(f"샘플 데이터:\n{df}")
                logger.info("\n💡 전체 테스트를 실행하려면:")
                logger.info("   poetry run python test_universe_cache.py --real-api -y")
            else:
                logger.error("❌ 데이터 수집 실패")
                return False
                
        except Exception as e:
            logger.error(f"❌ 실패: {e}", exc_info=True)
            return False
    else:
        # 전체 테스트
        logger.info("\n[통합 테스트] cache_daily_data() - 전체 종목 수집")
        logger.info("진행 상황을 확인하세요 (100개마다 로그 출력)")
        
        try:
            df = cache_daily_data(kiwoom)
            logger.info(f"✅ 성공: {len(df)}개 종목 캐싱 완료")
            logger.info(f"파일: all_stocks_kiwoom.xlsx")
            logger.info(f"샘플 데이터:\n{df.head()}")
        except Exception as e:
            logger.error(f"❌ 실패: {e}", exc_info=True)
            return False
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ 실제 API 테스트 완료!")
    logger.info("=" * 60)
    return True


def main():
    parser = argparse.ArgumentParser(description='Universe 캐싱 기능 테스트')
    parser.add_argument('--real-api', action='store_true', 
                       help='실제 API로 전체 테스트 (시간 소요, 실전투자 키 필요)')
    parser.add_argument('--quick', action='store_true',
                       help='빠른 검증 (10개 종목만, 1초 소요)')
    parser.add_argument('-y', '--yes', action='store_true',
                       help='확인 없이 바로 실행')
    
    args = parser.parse_args()
    
    if args.quick:
        # 빠른 검증
        print("🚀 빠른 검증 모드: 10개 종목만 테스트합니다 (약 1초)")
        success = test_with_real_api(quick_mode=True)
    elif args.real_api:
        # 전체 테스트
        if not args.yes:
            confirm = input("⚠️  실제 API 전체 테스트는 약 7분 소요됩니다. 계속하시겠습니까? (yes/no): ")
            if confirm.lower() != 'yes':
                print("테스트 취소됨")
                return
        else:
            print("🚀 실제 API 전체 테스트를 시작합니다 (약 7분 소요)")
        success = test_with_real_api(quick_mode=False)
    else:
        # Mock 테스트 (기본값)
        success = test_with_mock()
    
    if success:
        print("\n✅ 모든 테스트 통과!")
        sys.exit(0)
    else:
        print("\n❌ 테스트 실패")
        sys.exit(1)


if __name__ == '__main__':
    main()
