"""
utils/notifier.py
텔레그램 봇 메시지 전송 모듈
"""

import requests
from typing import Optional
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from utils.logger import logger


class TelegramNotifier:
    """
    텔레그램 알림 전송기
    """
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token or TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or TELEGRAM_CHAT_ID
        self.api_url = f"https://api.telegram.org/bot{self.token}/sendMessage" if self.token else None

    def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """
        텔레그램 방으로 텍스트 메시지를 전송합니다.
        토큰이나 chat_id가 설정되지 않은 경우 에러를 일으키지 않고 경고 로그만 남깁니다.
        """
        if not self.token or not self.chat_id:
            logger.debug("[Notifier] 텔레그램 토큰 또는 CHAT_ID가 설정되지 않아 알림 전송을 건너뜁니다.")
            return False

        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(self.api_url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("[Notifier] 텔레그램 메시지 전송 성공")
                return True
            elif parse_mode == "HTML" and response.status_code == 400:
                logger.warning(f"[Notifier] 텔레그램 HTML 파싱 오류로 일반 텍스트 모드로 재전송합니다: {response.text}")
                import re
                plain_text = re.sub(r'<[^>]+>', '', message)
                fallback_payload = {
                    "chat_id": self.chat_id,
                    "text": plain_text,
                    "disable_web_page_preview": True,
                }
                fallback_resp = requests.post(self.api_url, json=fallback_payload, timeout=10)
                if fallback_resp.status_code == 200:
                    logger.info("[Notifier] 일반 텍스트 폴백 메시지 전송 성공")
                    return True
                else:
                    logger.warning(f"[Notifier] 폴백 전송도 실패 (상태코드: {fallback_resp.status_code}): {fallback_resp.text}")
                    return False
            else:
                logger.warning(f"[Notifier] 텔레그램 전송 실패 (상태코드: {response.status_code}): {response.text}")
                return False
        except Exception as e:
            logger.error(f"[Notifier] 텔레그램 API 요청 중 예외 발생: {e}")
            return False


notifier = TelegramNotifier()
