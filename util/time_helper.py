from datetime import datetime, date
from zoneinfo import ZoneInfo


# 한국 시간대 (KST - Korea Standard Time)
KST = ZoneInfo("Asia/Seoul")

# 2026년 한국 주식시장 휴장일 (매년 초 업데이트 필요)
# 참고: 한국거래소 영업일 달력 https://www.krx.co.kr/main/main.jsp
# 최종 업데이트: 2026-01-05 (KRX 공식 휴장일 기준)
MARKET_HOLIDAYS_2026 = [
    date(2026, 1, 1),   # 신정
    date(2026, 2, 16),  # 설날
    date(2026, 2, 17),  # 설날
    date(2026, 2, 18),  # 설날
    date(2026, 3, 2),   # 삼일절 대체휴일
    date(2026, 5, 1),   # 근로자의날
    date(2026, 5, 5),   # 어린이날
    date(2026, 5, 25),  # 석가탄신일 대체휴일
    date(2026, 8, 17),  # 광복절 대체휴일
    date(2026, 9, 24),  # 추석
    date(2026, 9, 25),  # 추석
    date(2026, 10, 5),  # 개천절 대체휴일
    date(2026, 10, 9),  # 한글날
    date(2026, 12, 25), # 성탄절
    date(2026, 12, 31), # 연말휴장일
]

# TODO: 2027년 초에 MARKET_HOLIDAYS_2027 리스트 추가 및 is_market_closed_day() 함수 업데이트


def get_korea_time():
    """한국 시간을 반환하는 함수 (서버 위치와 무관)"""
    return datetime.now(KST)


def is_market_closed_day():
    """휴장일 여부 확인 (주말 + 공휴일)
    
    Returns:
        bool: 휴장일이면 True, 영업일이면 False
    """
    now = get_korea_time()
    today = now.date()
    
    # 주말 체크 (토요일=5, 일요일=6)
    if now.weekday() >= 5:
        return True
    
    # 공휴일 체크
    return today in MARKET_HOLIDAYS_2026


def check_transaction_open():
    """현재 시간이 장 중인지 확인하는 함수 (한국 시간 기준, 휴장일 포함)
    
    정규시장 거래 시간: 09:00 ~ 15:20
    - 15:20~15:30: 장 종료 동시호가 (신규 주문 불가, 가격 조정만 가능)
    - 15:30: 공식 장 마감
    """
    # 휴장일 체크
    if is_market_closed_day():
        return False
    
    now = get_korea_time()
    start_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
    end_time = now.replace(hour=15, minute=20, second=0, microsecond=0)  # 실제 매매 가능 마지막 시간
    return start_time <= now <= end_time


def check_transaction_closed():
    """현재 시간이 장이 끝난 시간인지 확인하는 함수 (한국 시간 기준)
    
    15:20 이후를 장 종료로 판단 (동시호가 포함)
    """
    now = get_korea_time()
    end_time = now.replace(hour=15, minute=20, second=0, microsecond=0)
    return end_time < now


def check_adjacent_transaction_closed():
    """현재 시간이 장 종료 부근인지 확인하는 함수(매수 시간 확인용, 한국 시간 기준)
    
    15:00~15:20: 장 마감 임박 구간 (신규 매수 금지)
    - 당일 급매수로 인한 리스크 방지
    """
    now = get_korea_time()
    base_time = now.replace(hour=15, minute=0, second=0, microsecond=0)
    end_time = now.replace(hour=15, minute=20, second=0, microsecond=0)
    return base_time <= now < end_time

