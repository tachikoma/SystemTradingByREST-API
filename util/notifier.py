import requests
import os
import tempfile
import html
import functools
import time
import traceback
import asyncio
from util.logging_config import get_logger

logger = get_logger(__name__)

# 마지막 전송 시각을 저장해 동일한 함수의 예외가 짧은 시간에 반복 전송되는 것을 방지합니다.
_notify_last_sent = {}


def notify_on_exception(fallback_return=None, rethrow=False, throttle_seconds: int = 60):
    """데코레이터: 함수에서 예외가 발생하면 마스킹된 트레이스백을 텔레그램으로 전송하고,
    지정된 폴백 값을 반환합니다. 재전송은 `throttle_seconds`로 제한됩니다.

    Args:
        fallback_return: 예외 발생 시 반환할 값
        rethrow: True이면 예외를 다시 발생시킵니다
        throttle_seconds: 동일 함수에 대해 재전송을 제한할 초 단위
    """
    def decorator(func):
        @functools.wraps(func)
        def _handle_exception_sync():
            # placeholder to satisfy structure (not used)
            pass

        async def _handle_exception_async(key, tb):
            masked = mask_sensitive_info(tb)
            now = time.time()
            last = _notify_last_sent.get(key, 0)
            if now - last >= throttle_seconds:
                try:
                    send_telegram_traceback(masked)
                    _notify_last_sent[key] = now
                except Exception as send_err:
                    logger.warning("텔레그램 예외 알림 전송 실패 (%s): %s", key, send_err)
            else:
                logger.debug("예외 알림 스로틀링: %s (%.1fs 남음)", key, throttle_seconds - (now - last))
            logger.exception("Exception in %s: %s", key, masked)

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                try:
                    return await func(*args, **kwargs)
                except Exception:
                    tb = traceback.format_exc()
                    key = f"{func.__module__}.{func.__name__}"
                    await _handle_exception_async(key, tb)
                    if rethrow:
                        raise
                    return fallback_return
            return async_wrapper
        else:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                try:
                    return func(*args, **kwargs)
                except Exception:
                    tb = traceback.format_exc()
                    key = f"{func.__module__}.{func.__name__}"
                    # run async handler synchronously (fire-and-forget)
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.create_task(_handle_exception_async(key, tb))
                        else:
                            loop.run_until_complete(_handle_exception_async(key, tb))
                    except Exception:
                        # fallback: call sync path
                        masked = mask_sensitive_info(tb)
                        now = time.time()
                        last = _notify_last_sent.get(key, 0)
                        if now - last >= throttle_seconds:
                            try:
                                send_telegram_traceback(masked)
                                _notify_last_sent[key] = now
                            except Exception as send_err:
                                logger.warning("텔레그램 예외 알림 전송 실패 (%s): %s", key, send_err)
                        else:
                            logger.debug("예외 알림 스로틀링: %s (%.1fs 남음)", key, throttle_seconds - (now - last))
                        logger.exception("Exception in %s: %s", key, masked)

                    if rethrow:
                        raise
                    return fallback_return
            return wrapper
    return decorator


def send_telegram_message(message: str, bot_token: str = None, chat_id: str = None, parse_mode: str = 'HTML') -> bool:
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
        
        # 메시지 포맷 처리
        if parse_mode == 'HTML':
            safe_message = html.escape(message)
        else:
            # parse_mode이 None 또는 다른 값이면 원문을 그대로 보냄
            safe_message = message

        payload = {
            'chat_id': chat_id,
            'text': safe_message,
        }
        if parse_mode:
            payload['parse_mode'] = parse_mode

        response = requests.post(
            url,
            json=payload,
            timeout=10
        )
        
        # 응답 확인
        if response.status_code == 200:
            result = response.json()
            if result.get('ok'):
                try:
                    logger.debug(f"텔레그램 메시지 전송 성공: {mask_sensitive_info(message)[:50]}...")
                except Exception:
                    logger.debug("텔레그램 메시지 전송 성공")
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


def send_telegram_traceback(trace_text: str, bot_token: str = None, chat_id: str = None, max_inline_length: int = 1500) -> bool:
    """긴 트레이스백 전송: 길면 파일로 첨부(sendDocument), 짧으면 텍스트로 전송.

    Args:
        trace_text: 전송할 트레이스백 텍스트
        bot_token: 봇 토큰 (환경변수로 대체 가능)
        chat_id: 채팅 ID (환경변수로 대체 가능)
        max_inline_length: 이 길이보다 짧으면 텍스트로 전송
    """
    # 환경변수에서 토큰과 채팅방 ID 가져오기
    if bot_token is None:
        bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    if chat_id is None:
        chat_id = os.environ.get('TELEGRAM_CHAT_ID')

    if not bot_token or not chat_id:
        logger.warning("텔레그램 봇 토큰 또는 채팅방 ID가 설정되지 않았습니다. .env 파일에 TELEGRAM_BOT_TOKEN과 TELEGRAM_CHAT_ID를 설정하세요.")
        return False

    # 마스킹 적용
    try:
        masked_trace = mask_sensitive_info(trace_text)
    except Exception:
        masked_trace = trace_text

    # 짧은 경우: 텍스트로 전송 (parse_mode 없음 -> 평문)
    if len(masked_trace) <= max_inline_length:
        return send_telegram_message(masked_trace, bot_token=bot_token, chat_id=chat_id, parse_mode=None)

    # 긴 경우: 임시 파일로 저장 후 sendDocument로 전송
    url = f'https://api.telegram.org/bot{bot_token}/sendDocument'
    tmp = None
    try:
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
        tmp.write(masked_trace)
        tmp.flush()
        tmp.close()

        with open(tmp.name, 'rb') as f:
            files = {'document': (os.path.basename(tmp.name), f, 'text/plain')}
            data = {'chat_id': chat_id, 'caption': f'Traceback (length={len(trace_text)}). See attached file.'}
            resp = requests.post(url, data=data, files=files, timeout=30)

        if resp.status_code == 200:
            result = resp.json()
            if result.get('ok'):
                logger.debug('텔레그램에 트레이스백 파일 전송 성공')
                return True
            else:
                logger.error(f"텔레그램 트레이스백 전송 실패: {result}")
                return False
        else:
            logger.error(f"텔레그램 sendDocument 응답 오류: {resp.status_code} - {resp.text}")
            return False
    except requests.exceptions.RequestException as e:
        logger.error(f"텔레그램 트레이스백 전송 네트워크 오류: {e}")
        return False
    except Exception as e:
        logger.exception(f"트레이스백 전송 중 예외 발생: {e}")
        return False
    finally:
        if tmp is not None:
            try:
                os.remove(tmp.name)
            except Exception:
                pass


def mask_sensitive_info(text: str) -> str:
    """민감한 토큰, 시크릿, 패스워드 등을 텍스트에서 마스킹합니다.

    - 환경변수에 설정된 알려진 키 값을 우선 마스킹합니다.
    - JSON/키=값 형태의 token/secret/password 등도 정규식으로 마스킹합니다.
    - 아주 긴 연속 영숫자(>=20자)는 임의 마스킹합니다.
    """
    try:
        import re
        masked = text

        # 1) 환경변수에 있는 민감값 마스킹
        env_keys = [
            'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID',
            'KIWOOM_MOCK_APPKEY', 'KIWOOM_MOCK_SECRETKEY',
            'KIWOOM_REAL_APPKEY', 'KIWOOM_REAL_SECRETKEY',
            'APPKEY', 'SECRETKEY', 'SECRET', 'PASSWORD', 'API_KEY', 'APIKEY', 'TOKEN'
        ]
        for k in env_keys:
            v = os.environ.get(k)
            if v and v.strip():
                masked = masked.replace(v, '[REDACTED]')

        # 2) JSON-like 또는 key=value 형태의 민감키 마스킹
        patterns = [
            r'(?i)("|\')?(token|secret|password|api[_-]?key|appkey|secretkey)("|\')?\s*[:=]\s*("|\')?([A-Za-z0-9_\-\.=]{6,})',
        ]
        for p in patterns:
            masked = re.sub(p, lambda m: m.group(0).replace(m.group(5), '[REDACTED]'), masked)

        # 3) Bearer 토큰 마스킹
        masked = re.sub(r'(?i)Bearer\s+[A-Za-z0-9_\-\.]{8,}', 'Bearer [REDACTED]', masked)

        # 4) 아주 긴 연속 영숫자(20자 이상) 마스킹 - 일반 텍스트에서 실수로 노출되는 키 차단
        masked = re.sub(r'(?<![A-Za-z0-9])[A-Za-z0-9_\-]{20,}(?![A-Za-z0-9])', '[LONG_REDACTED]', masked)

        return masked
    except Exception:
        return text
