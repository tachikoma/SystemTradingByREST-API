"""Telegram-based approval flow for sensitive .env changes.

Creates approval requests when `EnvWatcher` reports sensitive changes.
Polls Telegram `getUpdates` for `/approve <id>` or `/reject <id>` commands
from an authorized chat id and applies non-restart-sensitive keys via a
provided callback. Keys that require process restart (APPKEY/SECRET/ KIWOOM_MODE)
are not auto-applied even after approval; approver notifies operator to restart.

Usage:
    approver = EnvApprover(apply_callback=rsi_strategy.apply_sensitive_updates)
    approver.start()
    # On sensitive change detected:
    approver.create_request(changed_dict)

"""
from __future__ import annotations

import json
import os
import time
import threading
import uuid
from pathlib import Path
from typing import Callable, Dict, Any, Optional

import requests

from util.logging_config import get_logger
from util.notifier import send_telegram_message, send_message

logger = get_logger(__name__)


def _default_approvals_dir() -> Path:
    # place approvals under repository root .env_approvals
    root = Path(__file__).resolve().parents[1]
    d = root / '.env_approvals'
    d.mkdir(parents=True, exist_ok=True)
    return d


class EnvApprover(threading.Thread):
    def __init__(self,
                 bot_token: Optional[str] = None,
                 approver_chat_id: Optional[str] = None,
                 approvals_dir: Optional[str] = None,
                 poll_interval: float = 5.0,
                 apply_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        super().__init__(daemon=True)
        self.bot_token = bot_token or os.environ.get('TELEGRAM_BOT_TOKEN')
        self.approver_chat_id = str(approver_chat_id or os.environ.get('TELEGRAM_APPROVER_CHAT_ID') or os.environ.get('TELEGRAM_CHAT_ID') or '')
        self.poll_interval = float(os.environ.get('ENV_APPROVER_POLL_INTERVAL', poll_interval))
        self.apply_callback = apply_callback
        self.approvals_dir = Path(approvals_dir) if approvals_dir else _default_approvals_dir()
        self._stop_event = threading.Event()
        self._last_update_id: Optional[int] = None

    def create_request(self, changed: Dict[str, Any]) -> str:
        rid = uuid.uuid4().hex[:8]
        payload = {
            'id': rid,
            'changed': changed,
            'created_at': time.time(),
            'status': 'pending'
        }
        path = self.approvals_dir / f"{rid}.json"
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        except Exception:
            logger.exception('Failed to write approval request file')

        text = f"민감한 환경변수 변경 요청({rid})가 생성되었습니다: {', '.join(changed.keys())}\n" \
               + f"승인: /approve {rid}  거부: /reject {rid}"
        try:
            send_telegram_message(text)
        except Exception:
            send_message(text)
        return rid

    def stop(self):
        self._stop_event.set()

    def run(self):
        if not self.bot_token:
            logger.warning('EnvApprover: TELEGRAM_BOT_TOKEN not configured, approver not started')
            return

        # bootstrap last_update_id if stored
        last_file = self.approvals_dir / '.last_update_id'
        if last_file.exists():
            try:
                self._last_update_id = int(last_file.read_text().strip())
            except Exception:
                self._last_update_id = None

        while not self._stop_event.wait(self.poll_interval):
            try:
                updates = self._get_updates()
                for u in updates:
                    try:
                        self._handle_update(u)
                    except Exception:
                        logger.exception('Failed to handle update')
            except Exception:
                logger.exception('EnvApprover poll failed')

    def _get_updates(self):
        url = f'https://api.telegram.org/bot{self.bot_token}/getUpdates'
        params = {}
        if self._last_update_id is not None:
            params['offset'] = self._last_update_id + 1
        # Use Telegram long-polling to reduce frequent TLS handshakes and
        # add retries/backoff for transient network errors.
        try:
            try:
                lp_timeout = min(60, max(5, int(self.poll_interval)))
            except Exception:
                lp_timeout = 30
            params['timeout'] = lp_timeout

            # session with retry/backoff for idempotent GETs
            session = requests.Session()
            try:
                from requests.adapters import HTTPAdapter
                from urllib3.util.retry import Retry
                retries = Retry(total=3, backoff_factor=1, status_forcelist=(429, 500, 502, 503, 504))
                session.mount('https://', HTTPAdapter(max_retries=retries))
            except Exception:
                # best-effort: if Retry not available, continue without mounting
                pass

            # Use a tuple timeout (connect_timeout, read_timeout). Read timeout
            # should be slightly larger than lp_timeout to allow server to hold.
            r = session.get(url, params=params, timeout=(5, lp_timeout + 10))
            r.raise_for_status()
            j = r.json()
            if not j.get('ok'):
                return []
            res = j.get('result', [])
            if res:
                self._last_update_id = res[-1]['update_id']
                try:
                    (self.approvals_dir / '.last_update_id').write_text(str(self._last_update_id))
                except Exception:
                    pass
            return res
        except requests.exceptions.RequestException as e:
            # Network/timeouts are common transient issues; log as warning to
            # avoid noisy tracebacks while keeping the service resilient.
            logger.warning('Failed to fetch telegram updates: %s', e)
            return []
        except Exception:
            logger.exception('Failed to fetch telegram updates')
            return []

    def _handle_update(self, update: Dict[str, Any]):
        msg = update.get('message') or update.get('edited_message')
        if not msg:
            return
        chat = msg.get('chat', {})
        chat_id = str(chat.get('id', ''))
        # Only accept from configured approver chat id (if set)
        if self.approver_chat_id and self.approver_chat_id != '' and self.approver_chat_id != chat_id:
            return
        text = msg.get('text', '').strip()
        if not text:
            return
        parts = text.split()
        cmd = parts[0].lower()
        if cmd not in ('/approve', '/reject') or len(parts) < 2:
            return
        rid = parts[1].strip()
        req_file = self.approvals_dir / f"{rid}.json"
        if not req_file.exists():
            send_telegram_message(f'요청 {rid} 을(를) 찾을 수 없습니다.')
            return
        try:
            data = json.loads(req_file.read_text(encoding='utf-8'))
        except Exception:
            send_telegram_message(f'요청 파일 로드 실패: {rid}')
            return
        if data.get('status') != 'pending':
            send_telegram_message(f'요청 {rid} 은(는) 이미 처리됨: {data.get("status")}')
            return

        if cmd == '/reject':
            data['status'] = 'rejected'
            data['handled_by'] = chat_id
            data['handled_at'] = time.time()
            req_file.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            send_telegram_message(f'요청 {rid} 이(가) 거부되었습니다.')
            return

        # Approve: classify keys
        changed = data.get('changed', {})
        restart_keys = []
        apply_keys = {}
        for k, v in changed.items():
            ku = k.upper()
            if 'APPKEY' in ku or 'SECRET' in ku or ku == 'KIWOOM_MODE' or ku == 'KIWOOM_MODE':
                restart_keys.append(k)
            else:
                apply_keys[k] = v

        applied = False
        try:
            if apply_keys and self.apply_callback:
                try:
                    self.apply_callback(apply_keys)
                    applied = True
                except Exception:
                    logger.exception('apply_callback failed')
                    send_telegram_message(f'요청 {rid} 적용 중 오류가 발생했습니다. 로그를 확인하세요.')
                    return

            data['status'] = 'approved'
            data['handled_by'] = chat_id
            data['handled_at'] = time.time()
            data['applied'] = applied
            req_file.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

            parts = []
            if applied:
                parts.append(f'자동 적용된 키: {", ".join(apply_keys.keys())}')
            if restart_keys:
                parts.append(f'프로세스 재시작 필요 키: {", ".join(restart_keys)}')
            if not parts:
                parts.append('적용된 변경사항 없음')

            send_telegram_message(f'요청 {rid} 승인됨. ' + '; '.join(parts))

        except Exception:
            logger.exception('Approval handling failed for %s', rid)
            send_telegram_message(f'요청 {rid} 처리 중 오류가 발생했습니다.')
