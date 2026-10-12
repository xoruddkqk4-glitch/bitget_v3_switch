"""
utils/alert_throttle.py
동일 경보의 반복 발송을 막는 쿨다운 관리 모듈
- state.json 손상 시에도 동작하도록 logs/alert_throttle.json에 별도 저장합니다.
"""

import json
import time

from config import ALERT_THROTTLE_FILE, ALERT_COOLDOWN_SEC
from utils.logger import logger


def should_send_alert(key: str, cooldown_sec: int = ALERT_COOLDOWN_SEC) -> bool:
    """
    key 경보를 지금 보내도 되는지 판단합니다.
    마지막 발송 후 cooldown_sec가 지났으면 True를 반환하고 발송 시각을 기록합니다.
    """
    now = time.time()
    data = {}
    try:
        if ALERT_THROTTLE_FILE.exists():
            with open(ALERT_THROTTLE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
    except Exception:
        data = {}

    last_sent = float(data.get(key, 0.0) or 0.0)
    if now - last_sent < cooldown_sec:
        logger.info(f"[AlertThrottle] '{key}' 경보 쿨다운 중 (재발송 생략)")
        return False

    data[key] = now
    try:
        ALERT_THROTTLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERT_THROTTLE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[AlertThrottle] 경보 쿨다운 기록 저장 실패: {e}")
    return True
