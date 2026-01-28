import signal
import sys
import os
import time
import traceback
from typing import Callable, List

from util.logging_config import get_logger
from util.notifier import send_message, send_telegram_traceback, send_telegram_message

logger = get_logger(__name__)

# 등록된 정리 콜백 목록
_cleanup_funcs: List[Callable[[], None]] = []


def register_cleanup(func: Callable[[], None]):
    """종료 시 호출할 정리 함수를 등록합니다. 함수는 인자 없이 호출됩니다."""
    _cleanup_funcs.append(func)


def _run_cleanups() -> List[tuple]:
    """등록된 정리함수들을 실행하고, 실패한 항목들을 반환합니다."""
    errors = []
    for fn in _cleanup_funcs:
        try:
            fn()
        except Exception as e:
            logger.exception("Cleanup function failed: %s", e)
            errors.append((fn, e, traceback.format_exc()))
    return errors


def _build_shutdown_message(reason: str, signum: int = None, errors=None) -> str:
    pid = os.getpid()
    lines = [f"⚠️ Process shutdown: {reason}"]
    if signum is not None:
        try:
            import signal as _s
            signame = _s.Signals(signum).name
        except Exception:
            signame = str(signum)
        lines.append(f"Signal: {signame} ({signum})")
    lines.append(f"PID: {pid}")
    lines.append(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if errors:
        lines.append(f"Cleanup errors: {len(errors)}")
        for i, (fn, err, tb) in enumerate(errors[:3], start=1):
            try:
                name = getattr(fn, '__name__', repr(fn))
            except Exception:
                name = repr(fn)
            lines.append(f"{i}. {name}: {str(err)}")
    else:
        lines.append("Cleanup completed successfully.")
    return "\n".join(lines)


def _signal_handler(signum, frame):
    try:
        logger.warning("Shutdown signal received: %s", signum)
        errors = _run_cleanups()
        msg = _build_shutdown_message(f"SignalHandler", signum=signum, errors=errors)
        try:
            send_message(msg)
        except Exception:
            try:
                send_telegram_message(msg)
            except Exception:
                logger.exception("Failed to send shutdown telegram message")
    finally:
        # 강제 종료
        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)


def trigger_shutdown(reason: str = "manual"):
    """프로그램 내부에서 정리 및 텔레그램 알림을 트리거합니다."""
    logger.info("Triggering shutdown: %s", reason)
    errors = _run_cleanups()
    msg = _build_shutdown_message(reason, errors=errors)
    try:
        send_message(msg)
    except Exception:
        try:
            send_telegram_message(msg)
        except Exception:
            logger.exception("Failed to send shutdown telegram message")


def _uncaught_excepthook(exc_type, exc_value, tb):
    try:
        trace = ''.join(traceback.format_exception(exc_type, exc_value, tb))
        send_telegram_traceback(trace)
    except Exception:
        try:
            send_message("Unhandled exception occurred (trace send failed)")
        except Exception:
            logger.exception("Failed to notify unhandled exception")
    # 기본 훅으로 출력하고 종료
    try:
        sys.__excepthook__(exc_type, exc_value, tb)
    except Exception:
        pass
    try:
        sys.exit(1)
    except SystemExit:
        os._exit(1)


def setup_signal_handlers(ignore_signals=None):
    """기본 종료 시그널 및 예외 훅을 설정합니다.

    Args:
        ignore_signals: 설치를 건너뛸 시그널 목록 (예: [signal.SIGINT])
    """
    if ignore_signals is None:
        ignore_signals = []

    try:
        if signal.SIGTERM not in ignore_signals:
            signal.signal(signal.SIGTERM, _signal_handler)
    except Exception:
        logger.exception("Failed to set SIGTERM handler")

    try:
        if signal.SIGINT not in ignore_signals:
            signal.signal(signal.SIGINT, _signal_handler)
    except Exception:
        logger.exception("Failed to set SIGINT handler")

    try:
        # uncaught exception hook
        sys.excepthook = _uncaught_excepthook
    except Exception:
        logger.exception("Failed to set excepthook")
