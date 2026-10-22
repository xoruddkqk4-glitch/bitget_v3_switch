"""
sheets_logger.py - EC2 트레이딩 봇용 구글 스프레드시트 비동기 Webhook 로거
- 기존 로컬 엑셀 원장 저장 flow에 전혀 지장을 주지 않는 초경량 모듈
- 2~3초 타임아웃 및 try...except 예외 격리로 네트워크 지연 시에도 매매 루프 완벽 보장
"""

import os
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger("SheetsLogger")


class SheetsLogger:
    def __init__(
        self,
        webhook_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        strategy_name: str = "미지정",
        exchange: str = "Bitget",
        timeout: int = 3,
        silent: bool = False
    ):
        """
        :param webhook_url: Apps Script 웹 앱 배포 URL (https://script.google.com/macros/s/.../exec)
        :param auth_token: Config 시트에 등록된 인증 비밀키 (기본: trading_secret_2026)
        :param strategy_name: 전략명 ("그리드", "떨사오팔", "v3-switch")
        :param exchange: 거래소명 ("Bitget", "Binance" 등, 기본: Bitget)
        :param timeout: HTTP 요청 타임아웃 초 (기본: 3초)
        :param silent: True일 경우 터미널 에러 출력 음소거
        """
        self.webhook_url = webhook_url or os.getenv("GSHEET_WEBHOOK_URL", "")
        self.auth_token = auth_token or os.getenv("GSHEET_AUTH_TOKEN", "trading_secret_2026")
        self.strategy_name = strategy_name
        self.exchange = exchange
        self.timeout = timeout
        self.silent = silent

    def log_trade(
        self,
        action: str,
        symbol: str = "SOXL/USDT",
        price: float = 0.0,
        amount: float = 0.0,
        fee: float = 0.0,
        pnl: float = 0.0,
        pnl_pct: float = 0.0,
        level: str = "",
        hold_time: str = "",
        note: str = "",
        exchange: Optional[str] = None,
        currency: str = "USDT",
        timestamp: Optional[str] = None
    ) -> bool:
        """
        매수/매도/더미 체결 기록을 구글 시트 [통합_체결원장]으로 비동기 전송
        - 기존 엑셀 원장 저장 직후에 호출하세요.
        - level: 매수/청산 차수 (예: '1차', '2차', '더미 1차', '본진 3차' 등)
        - 실패하더라도 False만 반환하고 메인 매매 루프는 절대 중단되지 않습니다.
        """
        if not self.webhook_url:
            if not self.silent:
                print(f"[{self.strategy_name}] SheetsLogger: webhook_url이 지정되지 않아 구글 시트 전송을 건너뜁니다.")
            return False

        if requests is None:
            if not self.silent:
                print(f"[{self.strategy_name}] SheetsLogger: requests 라이브러리가 설치되어 있지 않습니다. (pip install requests)")
            return False

        now_str = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        payload = {
            "token": self.auth_token,
            "type": "TRADE",
            "data": {
                "timestamp": now_str,
                "strategy_name": self.strategy_name,
                "exchange": exchange or self.exchange,
                "symbol": symbol,
                "action": action.upper(),
                "level": str(level),
                "price": float(price),
                "amount": float(amount),
                "fee": float(fee),
                "pnl": float(pnl),
                "pnl_pct": float(pnl_pct),
                "currency": currency,
                "hold_time": str(hold_time),
                "note": str(note)
            }
        }

        try:
            resp = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout
            )
            if resp.status_code == 200:
                result = resp.json()
                if result.get("status") == "success":
                    return True
                else:
                    if not self.silent:
                        print(f"[{self.strategy_name}] 시트 응답 에러: {result.get('message')}")
                    return False
            else:
                if not self.silent:
                    print(f"[{self.strategy_name}] HTTP {resp.status_code} 오류: {resp.text}")
                return False
        except Exception as e:
            if not self.silent:
                # 엑셀 원장 저장은 이미 완료되었으므로 안전 안내만 출력
                print(f"[{self.strategy_name}] 구글 시트 전송 지연/오류 (기존 매매 정상 유지): {e}")
            return False

    def log_snapshot(
        self,
        total_equity: float,
        cash: float,
        holding_value: float,
        daily_pnl: float = 0.0,
        cumulative_pnl: float = 0.0,
        win_rate: float = 0.0,
        trades_count: int = 0,
        date_str: Optional[str] = None
    ) -> bool:
        """
        일별 또는 특정 시점의 전략 자산 스냅샷을 [일별_자산스냅샷] 시트로 전송
        """
        if not self.webhook_url or requests is None:
            return False

        today_str = date_str or datetime.now().strftime("%Y-%m-%d")

        payload = {
            "token": self.auth_token,
            "type": "SNAPSHOT",
            "data": {
                "date": today_str,
                "strategy_name": self.strategy_name,
                "total_equity": float(total_equity),
                "cash": float(cash),
                "holding_value": float(holding_value),
                "daily_pnl": float(daily_pnl),
                "cumulative_pnl": float(cumulative_pnl),
                "win_rate": float(win_rate),
                "trades_count": int(trades_count)
            }
        }

        try:
            resp = requests.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout
            )
            return resp.status_code == 200
        except Exception as e:
            if not self.silent:
                print(f"[{self.strategy_name}] 스냅샷 전송 실패: {e}")
            return False


if __name__ == "__main__":
    print("SheetsLogger 모듈 정상 로드 완료.")
    print("사용법: from sheets_logger import SheetsLogger")
