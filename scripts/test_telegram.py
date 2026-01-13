"""
텔레그램 메시지 전송 테스트 스크립트

사용법:
    1. .env 파일에 TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID 설정
    2. python scripts/test_telegram.py 실행
"""

import sys
from pathlib import Path

# 프로젝트 루트를 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from util.notifier import send_telegram_message

# .env 파일 로드
load_dotenv()


def test_telegram():
    """텔레그램 메시지 전송 테스트"""
    print("="*60)
    print("텔레그램 메시지 전송 테스트")
    print("="*60)
    
    # 테스트 메시지 목록
    test_messages = [
        "🤖 <b>테스트 메시지</b>\n텔레그램 봇이 정상적으로 작동합니다!",
        "📊 <b>매수 주문 체결</b>\n종목: 005930\n수량: 10주\n가격: 75,000원",
        "📈 <b>매도 주문 체결</b>\n종목: 005930\n수량: 10주\n가격: 78,000원\n수익: 30,000원 (+4.00%)",
        "⚠️ <b>경고</b>\n예수금 부족으로 매수 주문이 취소되었습니다.",
    ]
    
    print("\n텔레그램으로 테스트 메시지를 전송합니다...\n")
    
    for i, message in enumerate(test_messages, 1):
        print(f"[{i}/{len(test_messages)}] 전송 중...")
        print(f"메시지: {message[:50]}...")
        
        success = send_telegram_message(message)
        
        if success:
            print("✅ 전송 성공!\n")
        else:
            print("❌ 전송 실패!\n")
            print("\n문제 해결 방법:")
            print("1. .env 파일에 TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID가 설정되어 있는지 확인")
            print("2. 봇 토큰이 올바른지 확인")
            print("3. 봇과 대화를 시작했는지 확인 (메시지 1개 이상 전송)")
            print("4. chat_id가 올바른지 확인")
            print("\n설정 방법:")
            print("1. 텔레그램에서 @BotFather와 대화")
            print("2. /newbot 명령으로 새 봇 생성")
            print("3. 봇 토큰 획득")
            print("4. 봇과 대화 시작 (메시지 하나 보내기)")
            print("5. https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates 접속")
            print("6. 'chat':{'id':123456789} 부분에서 chat_id 확인")
            return
    
    print("="*60)
    print("✅ 모든 테스트 완료!")
    print("="*60)


if __name__ == '__main__':
    test_telegram()
