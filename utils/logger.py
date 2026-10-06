"""
utils/logger.py
로깅 설정 모듈 (콘솔 및 로테이팅 파일 핸들러)
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from config import LOG_DIR, LOG_FILE


def setup_logger(name: str = "bitget_v3_switch") -> logging.Logger:
    """
    콘솔 및 파일 로깅을 지원하는 공용 Logger 인스턴스를 반환합니다.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # 이미 핸들러가 등록되어 있다면 중복 등록 방지
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 1. 콘솔 핸들러 (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 2. 파일 핸들러 (최대 10MB, 5개 백업 로테이션)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()
