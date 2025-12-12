import requests
import os
from util.logging_config import get_logger

logger = get_logger(__name__)


def send_telegram_message(message: str, bot_token: str = None, chat_id: str = None) -> bool:
    """텔레그램 봇을 사용한 메시지 보내기
    
    Args:
        message: 전송할 메시지
        bot_token: 텔레그램 봇 토큰 (None이면 환경변수 TELEGRAM_BOT_TOKEN 사용)
        chat_id: 텔레그램 채팅방 ID (None이면 환경변수 TELEGRAM_CHAT_ID 사용)
        
    Returns:
        bool: 전송 성공 여부
        
    사용법:
        1. @BotFather에서 봇 생성 후 토큰 획득
        2. 봇과 대화 시작
        3. https://api.telegram.org/bot<TOKEN>/getUpdates 에서 chat_id 확인
        4. .env에 TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID 설정
    """
    try:
        # 환경변수에서 토큰과 채팅방 ID 가져오기
        if bot_token is None:
            bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
        if chat_id is None:
            chat_id = os.environ.get('TELEGRAM_CHAT_ID')
        
        # 필수 값 체크
        if not bot_token or not chat_id:
            logger.warning("텔레그램 봇 토큰 또는 채팅방 ID가 설정되지 않았습니다. "
                          ".env 파일에 TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID를 설정하세요.")
            return False
        
        # 텔레그램 API 호출
        url = f'https://api.telegram.org/bot{bot_token}/sendMessage'
        
        response = requests.post(
            url,
            json={
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'HTML'  # HTML 포맷 지원 (선택사항)
            },
            timeout=10
        )
        
        # 응답 확인
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                logger.debug(f"텔레그램 메시지 전송 성공: {message[:50]}...")
                return True
            else:
                logger.error(f"텔레그램 메시지 전송 실패: {result}")
                return False
        else:
            logger.error(f"텔레그램 API 응답 오류: {response.status_code} - {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        logger.error("텔레그램 메시지 전송 타임아웃")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"텔레그램 메시지 전송 네트워크 오류: {e}")
        return False
    except Exception as e:
        logger.exception(f"텔레그램 메시지 전송 중 예외 발생: {e}")
        return False


# 하위 호환성을 위한 별칭 (기존 send_message 함수 대체)
def send_message(message: str, token: str = None) -> bool:
    """메시지 전송 (텔레그램)
    
    Args:
        message: 전송할 메시지
        token: 사용하지 않음 (하위 호환성을 위해 유지)
        
    Returns:
        bool: 전송 성공 여부
    """
    return send_telegram_message(message)
