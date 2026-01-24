#!/usr/bin/env python3
"""네이버 크롤링과 키움 API 데이터 단위 비교"""
import pandas as pd

print("=== 네이버 크롤링 데이터 (NaverFinance.parquet) ===")
naver_df = pd.read_parquet('NaverFinance.parquet')
print(f"컬럼: {list(naver_df.columns)}")
print("\n삼성전자 데이터:")
samsung_naver = naver_df[naver_df['종목명'] == '삼성전자'].iloc[0] if '삼성전자' in naver_df['종목명'].values else None
if samsung_naver is not None:
    cap = samsung_naver['시가총액']
    cap_num = float(str(cap).replace(',', '')) if isinstance(cap, str) else float(cap)
    print(f"  시가총액: {cap_num:,.0f}")
    if '거래대금' in samsung_naver:
        amt = samsung_naver['거래대금']
        amt_num = float(str(amt).replace(',', '')) if isinstance(amt, str) else float(amt)
        print(f"  거래대금: {amt_num:,.0f}")

print("\n상위 3개 종목:")
print(naver_df[['종목명', '시가총액']].head(3))

print("\n" + "="*60)
print("=== 키움 API 데이터 (all_stocks_kiwoom.parquet) ===")
kiwoom_df = pd.read_parquet('all_stocks_kiwoom.parquet')
print(f"컬럼: {list(kiwoom_df.columns)}")
print("\n삼성전자 데이터:")
samsung_kiwoom = kiwoom_df[kiwoom_df['종목명'] == '삼성전자'].iloc[0] if '삼성전자' in kiwoom_df['종목명'].values else None
if samsung_kiwoom is not None:
    print(f"  시가총액: {samsung_kiwoom['시가총액']:,} (백만원)")
    print(f"  거래대금: {samsung_kiwoom['거래대금']:,} (백만원)")

print("\n상위 3개 종목:")
print(kiwoom_df[['종목명', '시가총액', '거래대금']].head(3))

print("\n" + "="*60)
print("=== 단위 비교 분석 ===")
if samsung_naver is not None and samsung_kiwoom is not None:
    naver_cap_raw = samsung_naver['시가총액']
    naver_cap = float(str(naver_cap_raw).replace(',', '')) if isinstance(naver_cap_raw, str) else float(naver_cap_raw)
    kiwoom_cap = float(samsung_kiwoom['시가총액'])
    
    print(f"삼성전자 시가총액:")
    print(f"  네이버: {naver_cap:,.0f}")
    print(f"  키움:   {kiwoom_cap:,.0f} (백만원)")
    
    ratio = naver_cap / kiwoom_cap if kiwoom_cap != 0 else 0
    print(f"  비율:   {ratio:.2f}")
    
    if ratio > 100:
        print(f"\n⚠️ 네이버 데이터는 '원' 단위로 보입니다 (키움의 {ratio:.0f}배)")
        print(f"   네이버를 백만원 단위로 변환: {naver_cap / 1_000_000:,.0f}")
    elif ratio < 0.01:
        print(f"\n⚠️ 네이버 데이터는 '억원' 단위로 보입니다")
        print(f"   네이버를 백만원 단위로 변환: {naver_cap * 100:,.0f}")
    else:
        print(f"\n✅ 네이버와 키움 모두 백만원 단위로 동일합니다")
