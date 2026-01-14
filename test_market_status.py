"""장 운영 여부 통합 테스트"""
from util.time_helper import check_transaction_open, is_market_closed_day, get_korea_time
from datetime import datetime, time
from zoneinfo import ZoneInfo

now = get_korea_time()

print("=" * 80)
print("🏢 장 운영 여부 통합 테스트")
print("=" * 80)

print(f"\n📅 현재 시간: {now.strftime('%Y-%m-%d (%A) %H:%M:%S')}")
print(f"   요일: {['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일'][now.weekday()]}")

# 휴장일 체크
is_closed = is_market_closed_day()
print(f"\n🗓️  휴장일 여부: {'Yes (주말 또는 공휴일)' if is_closed else 'No (영업일)'}")

# 장 운영 체크
is_open = check_transaction_open()
print(f"🏪 장 운영 중: {'Yes (09:00-15:20)' if is_open else 'No'}")

# 시간대별 상태
current_time = now.time()
print(f"\n⏰ 시간대별 판정:")
print(f"   장 시작 전 (00:00-08:59): {current_time < time(9, 0)}")
print(f"   장 중 (09:00-15:20): {time(9, 0) <= current_time <= time(15, 20)}")
print(f"   장 종료 후 (15:21-23:59): {current_time > time(15, 20)}")

# 전략 실행 판정
print(f"\n{'='*80}")
if is_closed:
    print("⛔ 휴장일 - 전략 실행 안 함 (5분 대기)")
elif is_open:
    print("✅ 장 운영 중 - 전략 실행")
else:
    print("⏸️  장 외 시간 - 전략 실행 안 함 (5분 대기)")
print("=" * 80)

# 내일 상태 예측
from datetime import timedelta
tomorrow = now + timedelta(days=1)
tomorrow_date = tomorrow.date()

from util.time_helper import MARKET_HOLIDAYS_2026
is_tomorrow_weekend = tomorrow.weekday() >= 5
is_tomorrow_holiday = tomorrow_date in MARKET_HOLIDAYS_2026

print(f"\n📅 내일 ({tomorrow.strftime('%Y-%m-%d %A')}) 예상:")
if is_tomorrow_weekend:
    print("   ❌ 주말 - 휴장")
elif is_tomorrow_holiday:
    print("   ❌ 공휴일 - 휴장")
else:
    print("   ✅ 영업일 - 장 운영")
