"""휴장일 처리 테스트 스크립트"""
from util.time_helper import is_market_closed_day, check_transaction_open, get_korea_time
from datetime import date

print("=" * 80)
print("🗓️  2026년 휴장일 처리 테스트")
print("=" * 80)

# 현재 상태
now = get_korea_time()
print(f"\n현재 시간: {now.strftime('%Y-%m-%d %H:%M:%S %A')}")
print(f"휴장일 여부: {'Yes ❌' if is_market_closed_day() else 'No ✅'}")
print(f"장 운영 중: {'Yes ✅' if check_transaction_open() else 'No ❌'}")

# 테스트 케이스
test_cases = [
    (date(2026, 1, 1), "신정"),
    (date(2026, 1, 2), "평일 (금)"),
    (date(2026, 1, 4), "토요일"),
    (date(2026, 1, 5), "일요일"),
    (date(2026, 2, 16), "설날 (월)"),
    (date(2026, 2, 17), "설날 (화)"),
    (date(2026, 2, 18), "설날 (수)"),
    (date(2026, 3, 1), "삼일절 (일, 주말)"),
    (date(2026, 3, 2), "삼일절 대체휴일 (월)"),
    (date(2026, 5, 1), "근로자의날 (금)"),
    (date(2026, 5, 5), "어린이날 (화)"),
    (date(2026, 5, 25), "석가탄신일 대체휴일 (월)"),
    (date(2026, 6, 6), "현충일 (토, 주말)"),
    (date(2026, 8, 15), "광복절 (토, 주말)"),
    (date(2026, 8, 17), "광복절 대체휴일 (월)"),
    (date(2026, 9, 24), "추석 (목)"),
    (date(2026, 9, 25), "추석 (금)"),
    (date(2026, 10, 3), "개천절 (토, 주말)"),
    (date(2026, 10, 5), "개천절 대체휴일 (월)"),
    (date(2026, 10, 9), "한글날 (금)"),
    (date(2026, 12, 25), "성탄절 (금)"),
    (date(2026, 12, 31), "연말휴장일 (목)"),
]

print(f"\n{'='*80}")
print("📅 2026년 주요 날짜 휴장일 체크")
print(f"{'='*80}")

for test_date, desc in test_cases:
    # 임시로 날짜 체크
    weekday = test_date.weekday()
    weekday_name = ['월', '화', '수', '목', '금', '토', '일'][weekday]
    
    # is_market_closed_day는 현재 날짜만 체크하므로 직접 로직 구현
    from util.time_helper import MARKET_HOLIDAYS_2026
    is_weekend = weekday >= 5
    is_holiday = test_date in MARKET_HOLIDAYS_2026
    is_closed = is_weekend or is_holiday
    
    status = "❌ 휴장" if is_closed else "✅ 영업"
    reason = ""
    if is_weekend:
        reason = " (주말)"
    elif is_holiday:
        reason = " (공휴일)"
    
    print(f"{test_date.strftime('%Y-%m-%d')} ({weekday_name}) {desc:12s} : {status}{reason}")

print(f"\n{'='*80}")
print("✅ 테스트 완료")
print(f"{'='*80}")
