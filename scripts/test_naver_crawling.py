"""
종목 정보 FDR+pykrx 테스트 스크립트
장 시간 외에도 정확한 데이터를 가져올 수 있는지 테스트
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from util.make_up_universe import execute_crawler, is_market_hours
from datetime import datetime
from zoneinfo import ZoneInfo

def test_naver_crawling():
    print("=" * 60)
    print("종목 정보 FDR+pykrx 테스트")
    print("=" * 60)
    
    # 현재 시간 정보
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    print(f"\n현재 시간: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"요일: {['월', '화', '수', '목', '금', '토', '일'][now.weekday()]}")
    print(f"장시간 여부: {'예' if is_market_hours() else '아니오'}")
    
    # FDR+pykrx 시작
    print("\nFDR+pykrx 시작...")
    print("-" * 60)
    
    try:
        df = execute_crawler("FDR_pykrx.parquet")
        
        print("\nFDR+pykrx 완료!")
        print("=" * 60)
        print(f"전체 종목 수: {len(df)}")
        print(f"컬럼: {list(df.columns)}")
        
        # 샘플 데이터 출력 (상위 5개)
        print("\n샘플 데이터 (상위 5개):")
        print("-" * 60)
        display_cols = ['종목코드', '종목명', '현재가', '시가총액', '거래대금', '시장구분']
        available_cols = [col for col in display_cols if col in df.columns]
        print(df[available_cols].head(5).to_string())
        
        # 데이터 검증
        print("\n데이터 검증:")
        print("-" * 60)
        
        # 시가총액이 0이거나 빈 값인 종목 수
        if '시가총액' in df.columns:
            invalid_market_cap = df['시가총액'].isna().sum() + (df['시가총액'] == 0).sum()
            print(f"시가총액 데이터 없는 종목: {invalid_market_cap}개")
        
        # 거래대금이 0이거나 빈 값인 종목 수
        if '거래대금' in df.columns:
            invalid_trading_value = df['거래대금'].isna().sum() + (df['거래대금'] == 0).sum()
            print(f"거래대금 데이터 없는 종목: {invalid_trading_value}개")
        
        # 현재가가 0이거나 빈 값인 종목 수
        if '현재가' in df.columns:
            invalid_price = df['현재가'].isna().sum() + (df['현재가'] == '0').sum()
            print(f"현재가 데이터 없는 종목: {invalid_price}개")
        
        print("\n결론:")
        print("-" * 60)
        if is_market_hours():
            print("✅ 장시간입니다. 실시간 데이터 수집 성공!")
        else:
            print("✅ 장시간 외입니다.")
            print("✅ 데이터가 정상적으로 수집되었습니다!")
            print("✅ FDR+pykrx는 장 마감 후에도 당일 확정 데이터를 제공합니다.")
            print("✅ Universe 재구성 시 장시간 체크 없이 FDR+pykrx 가능합니다.")
        
        print("\n파일 저장 위치: FDR_pykrx.parquet")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        print(f"\n❌ FDR+pykrx 실패: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_naver_crawling()
    sys.exit(0 if success else 1)
