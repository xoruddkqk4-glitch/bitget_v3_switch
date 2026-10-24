"""
sheets_logger.py - EC2 트레이딩 봇용 구글 스프레드시트 비동기 Webhook 로거
- 기존 로컬 엑셀 원장 저장 flow에 전혀 지장을 주지 않는 초경량 모듈
- 2~3초 타임아웃 및 try...except 예외 격리로 네트워크 지연 시에도 매매 루프 완벽 보장
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any

KST = timezone(timedelta(hours=9))

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger("SheetsLogger")

# 서버 공통 설정 파일 기본 경로 (환경변수 GSHEET_ENV_FILE 로 변경 가능)
DEFAULT_ENV_FILE = "/var/autobot/gsheet.env"
_ENV_LOADED = False


def _load_env_file(silent: bool = False) -> None:
    """
    공통 env 파일(KEY=VALUE 형식)에서 GSHEET_* 설정을 읽어 os.environ 에 채웁니다.
    - 이미 설정된 환경변수는 덮어쓰지 않습니다 (export 값이 항상 우선)
    - 파일이 없거나 읽을 수 없으면 조용히 건너뜁니다
    - 프로세스당 한 번만 실행합니다
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True

    path = os.getenv("GSHEET_ENV_FILE", DEFAULT_ENV_FILE)
    try:
        if not os.path.isfile(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].lstrip()
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as e:  # 설정 파일 문제로 매매 루프가 중단되면 안 됨
        if not silent:
            print(f"SheetsLogger: env 파일({path}) 로드 실패: {e}")


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
        :param auth_token: Apps Script Script Properties(SECRET_AUTH_TOKEN)에 등록된 인증 비밀키 (기본: 환경변수 GSHEET_AUTH_TOKEN)
        :param strategy_name: 전략명 ("그리드", "떨사오팔", "v3-switch")
        :param exchange: 거래소명 ("Bitget", "Binance" 등, 기본: Bitget)
        :param timeout: HTTP 요청 타임아웃 초 (기본: 3초)
        :param silent: True일 경우 터미널 에러 출력 음소거
        """
        _load_env_file(silent)
        self.webhook_url = webhook_url or os.getenv("GSHEET_WEBHOOK_URL", "")
        # 인증 토큰은 소스에 기본값을 두지 않으며 환경변수(GSHEET_AUTH_TOKEN)로만 주입합니다.
        self.auth_token = auth_token or os.getenv("GSHEET_AUTH_TOKEN", "")
        if self.webhook_url and not self.auth_token and not silent:
            print(f"[{strategy_name}] SheetsLogger: GSHEET_AUTH_TOKEN 환경변수가 없어 시트 전송이 인증 실패(401)로 거부됩니다.")
        self.strategy_name = strategy_name
        self.exchange = exchange
        self.timeout = timeout
        self.silent = silent
        self._session = requests.Session() if requests is not None else None

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
        timestamp: Optional[str] = None,
        trade_id: Optional[str] = None,
        event: str = "",
        side: str = "",
        is_dummy: bool = False,
        close_all: Optional[bool] = None
    ) -> bool:
        """
        매수/매도/더미 체결 기록을 구글 시트 [통합_체결원장]으로 전송
        - 기존 엑셀 원장 저장 직후에 호출하세요.
        - level: 매수/청산 차수 (예: '1차', '2차', '더미 1차', '본진 3차' 등)
        - 실패하더라도 False만 반환하고 메인 매매 루프는 절대 중단되지 않습니다.

        선택 인자 (생략하면 기존과 동일하게 동작):
        - trade_id: 같은 체결의 재전송을 서버가 걸러내는 ID. 생략하면 서버 중복 검사를 하지 않습니다.
        - event: 봇 원본 이벤트명 (예: 'SELL_CLEAR_PARTIAL')
        - side: 'LONG' / 'SHORT' / '' (양방향 전략용)
        - is_dummy: 가상(더미) 체결 여부
        - close_all: 전량 청산이면 True, 부분 청산이면 False, 해당 없으면 None
        서버가 'duplicate' 로 응답하면 이미 기록된 체결이므로 성공(True)으로 취급합니다.
        """
        if not self.webhook_url:
            if not self.silent:
                print(f"[{self.strategy_name}] SheetsLogger: webhook_url이 지정되지 않아 구글 시트 전송을 건너뜁니다.")
            return False

        if requests is None:
            if not self.silent:
                print(f"[{self.strategy_name}] SheetsLogger: requests 라이브러리가 설치되어 있지 않습니다. (pip install requests)")
            return False

        try:
            now_str = timestamp or datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

            data = {
                "timestamp": now_str,
                "strategy_name": self.strategy_name,
                "exchange": exchange or self.exchange,
                "symbol": symbol,
                "action": str(action).upper(),
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
            # 신규 선택 필드는 값이 있을 때만 포함 (구버전 서버와도 호환)
            if trade_id:
                data["trade_id"] = str(trade_id)
            if event:
                data["event"] = str(event)
            if side:
                data["side"] = str(side).upper()
            if is_dummy:
                data["is_dummy"] = True
            if close_all is not None:
                data["close_all"] = bool(close_all)

            payload = {
                "token": self.auth_token,
                "type": "TRADE",
                "data": data
            }

            http_client = self._session or requests
            resp = http_client.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout
            )
            if resp.status_code == 200:
                result = resp.json()
                if result.get("status") in ("success", "duplicate"):
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
            http_client = self._session or requests
            resp = http_client.post(
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

    def log_bot_state(self, state_data: Dict[str, Any], strategy_name: Optional[str] = None) -> bool:
        """
        EC2 봇의 내부 상태(쿨링다운, 공백 예약, 잔고부족 차수 등)를 구글 시트/대시보드로 비동기 전송
        - 타임아웃 2초 및 예외 격리로 메인 매매 루프 완벽 보장
        """
        if not self.webhook_url or requests is None:
            return False

        payload = {
            "token": self.auth_token,
            "type": "STATE_SYNC",
            "strategy_name": strategy_name or self.strategy_name,
            "timestamp": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            "data": state_data
        }

        try:
            http_client = self._session or requests
            resp = http_client.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=self.timeout
            )
            if resp.status_code != 200:
                return False
            # Apps Script 는 인증 실패에도 HTTP 200 을 돌려주므로 본문의 status 로 성공 여부를 판단
            try:
                return resp.json().get("status") == "success"
            except Exception:
                return False
        except Exception as e:
            if not self.silent:
                print(f"[{self.strategy_name}] 봇 상태 전송 지연/오류 (매매 정상 지속): {e}")
            return False

    def trigger_monthly_archive(self, min_retain_rows: int = 500, target_month: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        [옵션 1] 원장 월별 탭 자동 아카이브 원격 실행
        """
        if not self.webhook_url or requests is None:
            return None
        payload = {
            "token": self.auth_token,
            "type": "ARCHIVE_MONTHLY",
            "data": {
                "options": {
                    "minRetainRows": min_retain_rows,
                    "targetMonth": target_month
                }
            }
        }
        try:
            http_client = self._session or requests
            resp = http_client.post(self.webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            if not self.silent:
                print(f"[{self.strategy_name}] 월별 아카이브 요청 실패: {e}")
            return None

    def trigger_yearly_sharding(self, target_year: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        [옵션 4] 원장 연도별 파일 분리 (샤딩) 원격 실행
        """
        if not self.webhook_url or requests is None:
            return None
        payload = {
            "token": self.auth_token,
            "type": "SHARD_YEARLY",
            "data": {
                "year": target_year
            }
        }
        try:
            http_client = self._session or requests
            resp = http_client.post(self.webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            if not self.silent:
                print(f"[{self.strategy_name}] 연도별 샤딩 요청 실패: {e}")
            return None

    def diagnose_capacity(self) -> Optional[Dict[str, Any]]:
        """
        원장 용량 및 건강도 진단 원격 조회
        """
        if not self.webhook_url or requests is None:
            return None
        payload = {
            "token": self.auth_token,
            "type": "DIAGNOSE",
            "data": {}
        }
        try:
            http_client = self._session or requests
            resp = http_client.post(self.webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e:
            if not self.silent:
                print(f"[{self.strategy_name}] 원장 용량 진단 실패: {e}")
            return None


if __name__ == "__main__":
    print("SheetsLogger 모듈 정상 로드 완료.")
    print("사용법: from sheets_logger import SheetsLogger")

