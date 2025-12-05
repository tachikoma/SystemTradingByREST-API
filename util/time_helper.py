from datetime import datetime
from zoneinfo import ZoneInfo


# 한국 시간대 (KST - Korea Standard Time)
KST = ZoneInfo("Asia/Seoul")


def get_korea_time():
    """한국 시간을 반환하는 함수 (서버 위치와 무관)"""
    return datetime.now(KST)


def check_transaction_open():
    """현재 시간이 장 중인지 확인하는 함수 (한국 시간 기준)"""
    now = get_korea_time()
    start_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
    end_time = now.replace(hour=15, minute=20, second=0, microsecond=0)
    return start_time <= now <= end_time


def check_transaction_closed():
    """현재 시간이 장이 끝난 시간인지 확인하는 함수 (한국 시간 기준)"""
    now = get_korea_time()
    end_time = now.replace(hour=15, minute=20, second=0, microsecond=0)
    return end_time < now


def check_adjacent_transaction_closed():
    """현재 시간이 장 종료 부근인지 확인하는 함수(매수 시간 확인용, 한국 시간 기준)"""
    now = get_korea_time()
    base_time = now.replace(hour=15, minute=0, second=0, microsecond=0)
    end_time = now.replace(hour=15, minute=20, second=0, microsecond=0)
    return base_time <= now < end_time

