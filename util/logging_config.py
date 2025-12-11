import logging
import os
from logging.handlers import RotatingFileHandler
import tomllib
from typing import Optional

# 선택적 dotenv 지원: `python-dotenv`가 설치된 경우 .env를 자동으로 로드합니다
try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


def configure_logging(log_dir: Optional[str] = None, file_name: str = 'kiwoom.log', level: int = logging.INFO):
    """Configure root logging with a rotating file handler and a stderr stream handler.

    - If logging is already configured (root logger has handlers), this is a no-op to avoid double configuration.
    - log_dir defaults to '<project_root>/logs' (project root is one level up from util/)
    """
    # .env 파일이 존재하면 로드하여 조기 임포트하는 모듈들도 환경변수 예시를 인식하도록 합니다
    if load_dotenv is not None:
        try:
            project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
            dotenv_path = os.path.join(project_root, '.env')
            if os.path.exists(dotenv_path):
                # 이미 설정된 환경변수를 덮어쓰지 않도록 합니다
                load_dotenv(dotenv_path, override=False)
        except Exception:
            # dotenv 로드 실패는 무시합니다
            pass

    # 우선순위: 명시적 인자 > 환경변수 > pyproject.toml > 기본값
    env_log_dir = os.environ.get('KIW_LOG_DIR')
    env_log_level = os.environ.get('KIW_LOG_LEVEL')

    if log_dir is None:
        if env_log_dir:
            log_dir = env_log_dir
        else:
            # `pyproject.toml`의 `tool.systemtrading.logging` 설정을 시도합니다
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
                    # toml 파싱 오류는 무시합니다
                pass
        if log_dir is None:
            here = os.path.dirname(__file__)
            log_dir = os.path.normpath(os.path.join(here, '..', 'logs'))

    # 환경변수로 지정된 로그 레벨이 있으면 덮어씁니다
    if env_log_level:
        try:
            level = getattr(logging, env_log_level.upper())
        except Exception:
            pass
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, file_name)

    root = logging.getLogger()
    if root.handlers:
        # 이미 루트 로거에 핸들러가 존재하면 재설정하지 않습니다
        return

    root.setLevel(level)

    # 회전 관련 환경변수(KIW_LOG_ROTATION_MAX_BYTES, KIW_LOG_BACKUP_COUNT)를 반영합니다
    rotation_bytes = 10 * 1024 * 1024
    backup_count = 5
    try:
        if os.environ.get('KIW_LOG_ROTATION_MAX_BYTES'):
            rotation_bytes = int(os.environ.get('KIW_LOG_ROTATION_MAX_BYTES'))
    except Exception:
        pass
    try:
        if os.environ.get('KIW_LOG_BACKUP_COUNT'):
            backup_count = int(os.environ.get('KIW_LOG_BACKUP_COUNT'))
    except Exception:
        pass

    fh = RotatingFileHandler(log_file, maxBytes=rotation_bytes, backupCount=backup_count, encoding='utf-8')
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
