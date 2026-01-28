#!/usr/bin/env python3
"""Simple Linux (systemd-friendly) watchdog for SystemTrading

Features:
- Starts `main.py` from repository root using same Python interpreter
- Forwards SIGINT/SIGTERM to child and waits for graceful shutdown
- Detects exit reason (exit code or signal) and sends Telegram via `util.notifier`
- Optional automatic restart with delay and max restarts

Usage examples:
  python3 scripts/watchdog.py
  python3 scripts/watchdog.py --no-restart
  python3 scripts/watchdog.py --restart-delay 5 --max-restarts 10

When used with systemd, create unit file pointing ExecStart to this script.
"""
from __future__ import annotations
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
import logging
import atexit

try:
    # prefer package notifier if running inside project
    from util.notifier import send_telegram_message
except Exception:
    # fallback simple logger-only notifier
    def send_telegram_message(msg: str, *args, **kwargs):
        logging.getLogger('watchdog').warning('Telegram not available: %s', msg)


logger = logging.getLogger('watchdog')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def build_cmd(project_root: Path) -> list:
    # Use the same interpreter that's running the watcher
    py = sys.executable or 'python3'
    main_py = project_root / 'main.py'
    return [py, str(main_py), "--yes"]


class Watchdog:
    def __init__(self, project_root: Path, restart: bool = True, restart_delay: int = 5, max_restarts: int | None = None, notify: bool = True, name: str = 'SystemTrading'):
        self.project_root = project_root
        self.restart = restart
        self.restart_delay = max(0, int(restart_delay))
        self.max_restarts = None if max_restarts is None else int(max_restarts)
        self.notify = notify
        self.name = name
        self._stopping = False
        self.child: subprocess.Popen | None = None
        # signal handlers
        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

    def _on_signal(self, signum, frame):
        logger.info('Watchdog received signal %s', signum)
        self._stopping = True
        if self.child and self.child.poll() is None:
            try:
                logger.info('Forwarding signal %s to child PID %s', signum, self.child.pid)
                self.child.send_signal(signum)
            except Exception as e:
                logger.warning('Failed to forward signal to child: %s', e)

    def _notify(self, text: str):
        logger.info('Notify: %s', text)
        if not self.notify:
            return
        try:
            send_telegram_message(text)
        except Exception as e:
            logger.warning('send_telegram_message failed: %s', e)

    def run(self):
        cmd = build_cmd(self.project_root)
        restarts = 0
        while True:
            if self._stopping:
                logger.info('Watchdog stopping before launch')
                break

            logger.info('Starting child: %s', ' '.join(cmd))
            env = os.environ.copy()
            # ensure PYTHONPATH includes project root
            env.setdefault('PYTHONPATH', str(self.project_root))

            try:
                self.child = subprocess.Popen(cmd, cwd=str(self.project_root), env=env)
            except Exception as e:
                logger.exception('Failed to start child process: %s', e)
                self._notify(f'❌ {self.name} failed to start: {e}')
                break

            self._notify(f'▶ {self.name} started (pid={self.child.pid})')

            rc = None
            try:
                rc = self.child.wait()
            except KeyboardInterrupt:
                logger.info('KeyboardInterrupt in watcher; forwarding and waiting')
                try:
                    self.child.terminate()
                except Exception:
                    pass
                rc = self.child.wait()

            # If watchdog was instructed to stop, do not restart
            if self._stopping:
                reason = f'exit_code={rc}'
                if rc is not None and rc < 0:
                    reason = f'killed_by_signal={-rc}'
                self._notify(f'⏹ {self.name} stopped ({reason})')
                break

            # child exited but watchdog still active
            if rc == 0:
                self._notify(f'ℹ️ {self.name} exited normally (code=0)')
                if not self.restart:
                    break
            else:
                if rc < 0:
                    sig = -rc
                    try:
                        import signal as _s
                        sname = _s.Signals(sig).name
                    except Exception:
                        sname = str(sig)
                    self._notify(f'⚠️ {self.name} killed by signal {sig} ({sname})')
                else:
                    self._notify(f'⚠️ {self.name} exited with code {rc}')

            restarts += 1
            if self.max_restarts is not None and restarts > self.max_restarts:
                self._notify(f'❌ {self.name} reached max restarts ({self.max_restarts}), giving up')
                break

            if not self.restart:
                logger.info('Restart disabled, not restarting')
                break

            logger.info('Restarting after %s seconds (attempt %d)', self.restart_delay, restarts)
            time.sleep(self.restart_delay)


def parse_args():
    p = argparse.ArgumentParser(description='Watchdog for SystemTrading (Linux/systemd)')
    p.add_argument('--no-restart', dest='restart', action='store_false', help='Do not restart child on exit')
    p.add_argument('--restart-delay', type=int, default=5, help='Seconds to wait before restart')
    p.add_argument('--max-restarts', type=int, default=None, help='Maximum restarts (None=unlimited)')
    p.add_argument('--no-notify', dest='notify', action='store_false', help='Disable Telegram notifications')
    p.add_argument('--name', default='SystemTrading', help='Friendly name for notifications')
    return p.parse_args()


def main():
    args = parse_args()
    project_root = Path(__file__).resolve().parents[1]
    # PID file management
    run_dir = project_root / 'run'
    run_dir.mkdir(parents=True, exist_ok=True)
    pidfile = run_dir / 'watchdog.pid'
    try:
        pidfile.write_text(str(os.getpid()))
    except Exception:
        logger.exception('Failed to write PID file %s', pidfile)

    def _remove_pidfile():
        try:
            if pidfile.exists():
                pidfile.unlink()
        except Exception:
            logger.exception('Failed to remove PID file %s', pidfile)

    atexit.register(_remove_pidfile)
    wd = Watchdog(project_root=project_root, restart=args.restart, restart_delay=args.restart_delay, max_restarts=args.max_restarts, notify=args.notify, name=args.name)
    wd.run()


if __name__ == '__main__':
    main()
