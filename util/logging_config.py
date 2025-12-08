import logging
import os
from logging.handlers import RotatingFileHandler
import tomllib
from typing import Optional


def configure_logging(log_dir: Optional[str] = None, file_name: str = 'kiwoom.log', level: int = logging.INFO):
    """Configure root logging with a rotating file handler and a stderr stream handler.

    - If logging is already configured (root logger has handlers), this is a no-op to avoid double configuration.
    - log_dir defaults to '<project_root>/logs' (project root is one level up from util/)
    """
    # Priority: explicit arg > environment variables > pyproject.toml > default
    env_log_dir = os.environ.get('KIW_LOG_DIR')
    env_log_level = os.environ.get('KIW_LOG_LEVEL')

    if log_dir is None:
        if env_log_dir:
            log_dir = env_log_dir
        else:
            # try pyproject.toml for tool.systemtrading.logging
            try:
                pyproject_path = os.path.join(os.path.dirname(__file__), '..', 'pyproject.toml')
                if os.path.exists(pyproject_path):
                    with open(pyproject_path, 'rb') as f:
                        data = tomllib.load(f)
                        cfg = data.get('tool', {}).get('systemtrading', {}).get('logging', {})
                        if cfg:
                            log_dir = cfg.get('log_dir', None) or cfg.get('dir', None)
                            level_name = cfg.get('level', None)
                            if level_name:
                                try:
                                    level = getattr(logging, level_name.upper())
                                except Exception:
                                    pass
            except Exception:
                # ignore toml errors
                pass
        if log_dir is None:
            here = os.path.dirname(__file__)
            log_dir = os.path.normpath(os.path.join(here, '..', 'logs'))

    # env log level overrides
    if env_log_level:
        try:
            level = getattr(logging, env_log_level.upper())
        except Exception:
            pass
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, file_name)

    root = logging.getLogger()
    if root.handlers:
        # already configured
        return

    root.setLevel(level)

    fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8')
    fh.setLevel(level)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setLevel(logging.WARNING)
    sh.setFormatter(fmt)
    root.addHandler(sh)


def get_logger(name: str = 'kiwoom') -> logging.Logger:
    return logging.getLogger(name)
