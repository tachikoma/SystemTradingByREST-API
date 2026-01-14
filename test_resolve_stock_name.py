#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
resolve_stock_name 기능 테스트

사용법:
    poetry run python test_resolve_stock_name.py
"""

import os
import sys
from unittest.mock import Mock

# 프로젝트 루트를 PYTHONPATH에 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategy.RSIStrategy import RSIStrategy
from util.db_helper import upsert_stock_name, get_stock_name


def test_resolve_stock_name():
    """resolve_stock_name 메서드 테스트"""
    
    # Mock Kiwoom 클라이언트 생성
    mock_kiwoom = Mock()
    mock_kiwoom.mock = True
    mock_kiwoom.get_master_code_name_safe = Mock(return_value="삼성전자")
    
    # RSIStrategy 인스턴스 생성 (초기화는 건너뜀)
    strategy = RSIStrategy.__new__(RSIStrategy)
    strategy.kiwoom = mock_kiwoom
    strategy.allow_kiwoom_calls = False  # 기본값: API 호출 비활성화
    strategy.universe_map = {}
    strategy.strategy_name = "TestStrategy"
    
    print("=" * 60)
    print("테스트 1: DB에 데이터 추가 및 조회")
    print("=" * 60)
    
    # 테스트 데이터 추가
    upsert_stock_name('master_list', '005930', '삼성전자')
    upsert_stock_name('master_list', '000660', 'SK하이닉스')
    
    # resolve_stock_name 테스트
    name1 = strategy.resolve_stock_name('005930')
    print(f"resolve_stock_name('005930') = {name1}")
    assert name1 == '삼성전자', f"Expected '삼성전자', got '{name1}'"
    
    name2 = strategy.resolve_stock_name('000660')
    print(f"resolve_stock_name('000660') = {name2}")
    assert name2 == 'SK하이닉스', f"Expected 'SK하이닉스', got '{name2}'"
    
    print("\n✅ 테스트 1 통과: DB 조회 성공")
    
    print("\n" + "=" * 60)
    print("테스트 2: 메모리 캐시 테스트")
    print("=" * 60)
    
    # 메모리 캐시에서 조회되는지 확인 (DB 호출 없이)
    strategy.universe_map['035720'] = '카카오'
    name3 = strategy.resolve_stock_name('035720')
    print(f"resolve_stock_name('035720') = {name3} (메모리 캐시)")
    assert name3 == '카카오', f"Expected '카카오', got '{name3}'"
    
    print("✅ 테스트 2 통과: 메모리 캐시 조회 성공")
    
    print("\n" + "=" * 60)
    print("테스트 3: 없는 종목 조회 (None 반환)")
    print("=" * 60)
    
    name4 = strategy.resolve_stock_name('999999')
    print(f"resolve_stock_name('999999') = {name4}")
    assert name4 is None, f"Expected None, got '{name4}'"
    
    print("✅ 테스트 3 통과: 없는 종목 None 반환")
    
    print("\n" + "=" * 60)
    print("테스트 4: 메시지 포맷 테스트")
    print("=" * 60)
    
    # 종목명이 있는 경우
    code1 = '005930'
    name = strategy.resolve_stock_name(code1)
    display1 = f"{name}({code1})" if name else code1
    print(f"종목코드 {code1} -> 표시: {display1}")
    assert display1 == "삼성전자(005930)", f"Expected '삼성전자(005930)', got '{display1}'"
    
    # 종목명이 없는 경우
    code2 = '999999'
    name = strategy.resolve_stock_name(code2)
    display2 = f"{name}({code2})" if name else code2
    print(f"종목코드 {code2} -> 표시: {display2}")
    assert display2 == "999999", f"Expected '999999', got '{display2}'"
    
    print("✅ 테스트 4 통과: 메시지 포맷 정상")
    
    print("\n" + "=" * 60)
    print("테스트 5: allow_kiwoom_calls 플래그 테스트")
    print("=" * 60)
    
    # API 호출 비활성화 상태에서 새 종목 조회
    strategy.allow_kiwoom_calls = False
    name5 = strategy.resolve_stock_name('012345')  # DB에도 없는 종목
    print(f"allow_kiwoom_calls=False, resolve_stock_name('012345') = {name5}")
    assert name5 is None, f"Expected None (API 호출 안 함), got '{name5}'"
    assert not mock_kiwoom.get_master_code_name_safe.called, "API가 호출되지 않아야 함"
    
    # API 호출 활성화 상태에서 새 종목 조회
    strategy.allow_kiwoom_calls = True
    name6 = strategy.resolve_stock_name('012345')
    print(f"allow_kiwoom_calls=True, resolve_stock_name('012345') = {name6}")
    assert name6 == '삼성전자', f"Expected '삼성전자' (Mock 반환값), got '{name6}'"
    assert mock_kiwoom.get_master_code_name_safe.called, "API가 호출되어야 함"
    
    print("✅ 테스트 5 통과: allow_kiwoom_calls 플래그 정상 동작")
    
    print("\n" + "=" * 60)
    print("🎉 모든 테스트 통과!")
    print("=" * 60)


if __name__ == "__main__":
    test_resolve_stock_name()
