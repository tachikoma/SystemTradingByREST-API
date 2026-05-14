"""Runtime .env watcher and safe reloader.

- 감시 대상: repository root의 .env 파일(경로를 전달 가능)
- 민감한 키(API keys, secrets, KIWOOM_MODE 등)는 자동 적용하지 않고 콜백으로 알립니다.
- 일반 옵션은 `os.environ`에 적용하고 등록된 콜백을 호출합니다.

사용 예:
    watcher = EnvWatcher('/path/to/.env')
    watcher.add_callback(non_sensitive_cb)
    watcher.set_sensitive_callback(sensitive_cb)
    watcher.start()

"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable, Dict, Tuple

from dotenv import dotenv_values

from util.logging_config import get_logger
from util.notifier import send_message

logger = get_logger(__name__)


def _is_sensitive_key(key: str) -> bool:
    if not key:
        return False
    k = key.upper()
    # 명시적 민감 키
    if k == 'KIWOOM_MODE':
        return True
    # 전략에서 보호하는 런타임 파라미터는 자동 적용 금지(수동 승인 필요)
    # RSIStrategy.apply_env_updates에서 보호(protected)로 정의된 키들과 일치시킨다.
    protected_strategy_keys = (
        'RSI_BUY_THRESHOLD',
        'PRICE_DROP_THRESHOLD',
        'CASH_RESERVE_RATIO',
        'ENABLE_STOP_LOSS',
    )
    if k in protected_strategy_keys:
        return True
    # APPKEY / SECRET / SECRETKEY / PASSWORD 등은 민감하다고 판단
    sensitive_tokens = ('APPKEY', 'SECRET', 'SECRETKEY', 'PASSWORD', 'PRIVATE_KEY', 'TOKEN')
    for t in sensitive_tokens:
        if t in k:
            return True
    return False


class EnvWatcher(threading.Thread):
    """파일 변경을 감지해 .env 값을 안전하게 재적용합니다.

    - non-sensitive 변경값은 `os.environ`에 적용한 뒤 등록된 콜백들을 호출합니다.
    - sensitive 변경은 자동 적용하지 않고 별도 콜백(`set_sensitive_callback`)으로 알립니다.
    """

    def __init__(self, dotenv_path: str, interval: float = 5.0):
        super().__init__(daemon=True)
        self.dotenv_path = str(dotenv_path)
        self.interval = float(interval)
        self._stop_event = threading.Event()
        self._callbacks: list[Callable[[Dict[str, Tuple[str, str]]], None]] = []
        self._sensitive_callback: Callable[[Dict[str, Tuple[str, str]]], None] | None = None

        try:
            self._last_values = dict(dotenv_values(self.dotenv_path) or {})
        except Exception:
            self._last_values = {}

        try:
            p = Path(self.dotenv_path)
            self._last_mtime = p.stat().st_mtime if p.exists() else None
        except Exception:
            self._last_mtime = None

    def add_callback(self, cb: Callable[[Dict[str, Tuple[str, str]]], None]):
        self._callbacks.append(cb)

    def set_sensitive_callback(self, cb: Callable[[Dict[str, Tuple[str, str]]], None]):
        self._sensitive_callback = cb

    def stop(self):
        self._stop_event.set()

    def reload_now(self):
        """즉시 스캔을 수행합니다 (SIGHUP 등에서 호출 가능)."""
        try:
            self._scan_and_apply()
        except Exception as e:
            logger.exception("EnvWatcher reload_now failed: %s", e)

    def run(self):
        while not self._stop_event.wait(self.interval):
            try:
                self._scan_and_apply()
            except Exception as e:
                logger.exception("EnvWatcher scan error: %s", e)

    def _scan_and_apply(self):
        p = Path(self.dotenv_path)
        try:
            mtime = p.stat().st_mtime
        except Exception:
            mtime = None

        try:
            new_values = dict(dotenv_values(self.dotenv_path) or {})
        except Exception as e:
            logger.warning("EnvWatcher failed to parse .env: %s", e)
            new_values = {}

        # 변경이 없으면 종료
        if mtime == self._last_mtime and new_values == self._last_values:
            return

        changed: Dict[str, Tuple[str | None, str | None]] = {}
        all_keys = set(self._last_values.keys()) | set(new_values.keys())
        for k in all_keys:
            old = self._last_values.get(k)
            new = new_values.get(k)
            if old != new:
                changed[k] = (old, new)

        if not changed:
            self._last_values = new_values
            self._last_mtime = mtime
            return

        sensitive = {k: v for k, v in changed.items() if _is_sensitive_key(k)}
        non_sensitive = {k: v for k, v in changed.items() if not _is_sensitive_key(k)}

        # non-sensitive는 즉시 os.environ에 적용
        for k, (_old, new) in non_sensitive.items():
            if new is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = str(new)

        # 상태 갱신
        self._last_values = new_values
        self._last_mtime = mtime

        if non_sensitive:
            for cb in self._callbacks:
                try:
                    cb(non_sensitive)
                except Exception:
                    logger.exception("EnvWatcher non-sensitive callback failed")

        if sensitive:
            if self._sensitive_callback:
                try:
                    self._sensitive_callback(sensitive)
                except Exception:
                    logger.exception("EnvWatcher sensitive callback failed")
            else:
                msg = "민감한 .env 변경 감지됨: " + ", ".join(sensitive.keys())
                logger.warning(msg)
                try:
                    send_message(msg)
                except Exception:
                    pass
