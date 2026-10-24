"""
sheets_logger.py - EC2 트레이딩 봇용 구글 스프레드시트 비동기 Webhook 로거
- 기존 로컬 엑셀 원장 저장 flow에 전혀 지장을 주지 않는 초경량 모듈
- 전송은 백그라운드 스레드 큐로 처리하므로 네트워크 지연이 매매 루프를 막지 않습니다.
- 전송 실패한 체결은 로컬 outbox 파일(sheets_outbox_*.jsonl)에 보관했다가 재전송합니다.
  (서버가 trade_id 로 중복을 걸러내므로 재전송해도 같은 체결이 두 번 기록되지 않습니다)
"""

import os
import json
import atexit
import hashlib
import logging
import queue
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

KST = timezone(timedelta(hours=9))

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger("SheetsLogger")

# 서버 공통 설정 파일 기본 경로 (환경변수 GSHEET_ENV_FILE 로 변경 가능)
DEFAULT_ENV_FILE = "/var/autobot/gsheet.env"
_ENV_LOADED = False

# 전송 큐 / outbox 동작 상수
OUTBOX_RETRY_SEC = 60            # outbox 주기적 재전송 간격
NETWORK_DOWN_BACKOFF_SEC = 30    # 네트워크 오류 직후 이 시간 동안은 바로 outbox 로 보관
OUTBOX_MAX_BYTES = 20 * 1024 * 1024  # outbox 파일 최대 크기(초과 시 새 항목은 보관하지 않음)
MAX_REJECT_ATTEMPTS = 50         # 서버가 거절(인증/검증 오류)한 항목의 재시도 한도 (초과 시 .dead 파일로 이동)
EXIT_FLUSH_SEC = 5.0             # 프로세스 종료 시 큐를 비우는 최대 대기 시간


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
        silent: bool = False,
        async_mode: Optional[bool] = None,
        outbox_dir: Optional[str] = None
    ):
        """
        :param webhook_url: Apps Script 웹 앱 배포 URL (https://script.google.com/macros/s/.../exec)
        :param auth_token: Apps Script Script Properties(SECRET_AUTH_TOKEN)에 등록된 인증 비밀키 (기본: 환경변수 GSHEET_AUTH_TOKEN)
        :param strategy_name: 전략명 ("그리드", "떨사오팔", "v3-switch")
        :param exchange: 거래소명 ("Bitget", "Binance" 등, 기본: Bitget)
        :param timeout: HTTP 요청 타임아웃 초 (기본: 3초, 백그라운드 전송은 최소 10초까지 허용)
        :param silent: True일 경우 터미널 에러 출력 음소거
        :param async_mode: True(기본)면 백그라운드 큐 전송, False면 호출 스레드에서 즉시 전송하고 결과를 반환.
                           환경변수 GSHEET_ASYNC=0 으로도 끌 수 있습니다. (모의 테스트용)
        :param outbox_dir: 실패 보관 파일 폴더 (기본: 환경변수 GSHEET_OUTBOX_DIR, 없으면 이 파일이 있는 폴더)
        """
        _load_env_file(silent)
        raw_url = (webhook_url or os.getenv("GSHEET_WEBHOOK_URL", "")).strip()
        if raw_url and not raw_url.startswith("http://") and not raw_url.startswith("https://"):
            raw_url = f"https://script.google.com/macros/s/{raw_url}/exec"
        self.webhook_url = raw_url
        # 인증 토큰은 소스에 기본값을 두지 않으며 환경변수(GSHEET_AUTH_TOKEN)로만 주입합니다.
        self.auth_token = (auth_token or os.getenv("GSHEET_AUTH_TOKEN", "")).strip()
        if self.webhook_url and not self.auth_token and not silent:
            print(f"[{strategy_name}] SheetsLogger: GSHEET_AUTH_TOKEN 환경변수가 없어 시트 전송이 인증 실패(401)로 거부됩니다.")
        self.strategy_name = strategy_name
        self.exchange = exchange
        self.timeout = timeout
        self.silent = silent
        self._session = requests.Session() if requests is not None else None

        if async_mode is None:
            async_mode = os.getenv("GSHEET_ASYNC", "1").strip() not in ("0", "false", "False", "no")
        self.async_mode = bool(async_mode)

        # outbox 파일 경로 (전략별로 분리: 같은 폴더를 공유해도 충돌하지 않도록 이름 해시 사용)
        base_dir = outbox_dir or os.getenv("GSHEET_OUTBOX_DIR") or os.path.dirname(os.path.abspath(__file__))
        tag = hashlib.md5(strategy_name.encode("utf-8")).hexdigest()[:8]
        self.outbox_path = os.path.join(base_dir, f"sheets_outbox_{tag}.jsonl")
        self.dead_path = self.outbox_path[:-len(".jsonl")] + ".dead.jsonl"

        # 큐/스레드 상태 (스레드는 첫 전송 시 지연 시작)
        self._q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._file_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending_state: Optional[Dict[str, Any]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._busy = False
        self._down_until = 0.0
        self._warned_no_url = False
        self._last_sync_flush = 0.0
        atexit.register(self._shutdown)

    # ------------------------------------------------------------------
    # 내부 유틸
    # ------------------------------------------------------------------
    def _log(self, msg: str) -> None:
        if not self.silent:
            print(f"[{self.strategy_name}] {msg}")

    def _ensure_worker(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._worker, name=f"SheetsLogger-{self.strategy_name}", daemon=True)
            self._thread.start()

    def _send(self, item: Dict[str, Any]) -> str:
        """
        항목 1건을 서버로 전송.
        반환: 'ok'(성공 또는 서버가 이미 기록함) / 'retry'(네트워크·서버 일시 오류) / 'reject'(서버가 거절: 인증·검증 오류)
        Apps Script 는 오류에도 HTTP 200 을 돌려주므로 응답 본문의 status 로 판정합니다.
        """
        payload = {"token": self.auth_token, "type": item["type"], "data": item["data"]}
        payload.update(item.get("extra") or {})
        try:
            http_client = self._session or requests
            resp = http_client.post(
                self.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=max(self.timeout, 10)
            )
        except Exception as e:
            self._log(f"시트 전송 지연/오류 (매매 정상 유지, 나중에 재전송): {e}")
            return "retry"

        if resp.status_code != 200:
            self._log(f"HTTP {resp.status_code} 오류")
            return "retry" if (resp.status_code >= 500 or resp.status_code == 429) else "reject"
        try:
            body = resp.json()
        except Exception:
            self._log("시트 응답을 해석할 수 없습니다 (JSON 아님)")
            return "retry"
        if body.get("status") in ("success", "duplicate"):
            return "ok"
        self._log(f"시트 응답 에러: {body.get('message')}")
        return "reject"

    def _store(self, item: Dict[str, Any]) -> None:
        """전송하지 못한 항목을 outbox 에 보관 (거절 횟수 초과 항목은 .dead 파일로 이동)."""
        try:
            line = json.dumps(item, ensure_ascii=False)
            with self._file_lock:
                if item.get("attempts", 0) >= MAX_REJECT_ATTEMPTS:
                    with open(self.dead_path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                    return
                if os.path.isfile(self.outbox_path) and os.path.getsize(self.outbox_path) > OUTBOX_MAX_BYTES:
                    self._log("outbox 파일이 너무 커서 새 항목을 보관하지 못했습니다.")
                    return
                with open(self.outbox_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as e:
            self._log(f"outbox 보관 실패: {e}")

    def _process(self, item: Dict[str, Any]) -> bool:
        """항목 전송 시도. 성공하면 True, 실패하면 outbox 에 보관하고 False."""
        if not self.webhook_url:
            if not self._warned_no_url:
                self._warned_no_url = True
                self._log("webhook_url이 지정되지 않아 체결을 outbox 에 보관합니다 (URL 설정 후 재전송).")
            self._store(item)
            return False
        if time.time() < self._down_until:
            self._store(item)
            return False
        result = self._send(item)
        if result == "ok":
            return True
        if result == "retry":
            self._down_until = time.time() + NETWORK_DOWN_BACKOFF_SEC
        else:
            item["attempts"] = int(item.get("attempts", 0)) + 1
        self._store(item)
        return False

    def _flush_outbox(self) -> None:
        """outbox 에 쌓인 항목을 순서대로 재전송. 네트워크 오류가 나면 나머지는 그대로 둔다."""
        if not self.webhook_url or time.time() < self._down_until:
            return
        with self._file_lock:
            if not os.path.isfile(self.outbox_path):
                return
            try:
                with open(self.outbox_path, "r", encoding="utf-8") as f:
                    lines = [ln for ln in f.read().split("\n") if ln.strip()]
            except Exception as e:
                self._log(f"outbox 읽기 실패: {e}")
                return
        if not lines:
            return

        remaining: List[str] = []
        dead: List[str] = []
        stopped_at = len(lines)
        for i, line in enumerate(lines):
            try:
                item = json.loads(line)
            except Exception:
                dead.append(line)
                continue
            result = self._send(item)
            if result == "ok":
                continue
            if result == "retry":
                self._down_until = time.time() + NETWORK_DOWN_BACKOFF_SEC
                stopped_at = i
                break
            item["attempts"] = int(item.get("attempts", 0)) + 1
            if item["attempts"] >= MAX_REJECT_ATTEMPTS:
                dead.append(json.dumps(item, ensure_ascii=False))
            else:
                remaining.append(json.dumps(item, ensure_ascii=False))

        tail = lines[stopped_at:]
        with self._file_lock:
            try:
                # 재전송하는 동안 새로 보관된 항목(파일 끝에 추가분)도 보존
                with open(self.outbox_path, "r", encoding="utf-8") as f:
                    current = [ln for ln in f.read().split("\n") if ln.strip()]
                added = current[len(lines):]
                new_lines = remaining + tail + added
                if new_lines:
                    with open(self.outbox_path, "w", encoding="utf-8") as f:
                        f.write("\n".join(new_lines) + "\n")
                else:
                    os.remove(self.outbox_path)
                if dead:
                    with open(self.dead_path, "a", encoding="utf-8") as f:
                        f.write("\n".join(dead) + "\n")
            except Exception as e:
                self._log(f"outbox 갱신 실패: {e}")

    def _send_pending_state(self) -> None:
        """대기 중인 최신 봇 상태 1건 전송 (실패해도 다음 상태가 덮어쓰므로 보관하지 않음)."""
        with self._state_lock:
            item = self._pending_state
            self._pending_state = None
        if item is None or not self.webhook_url or time.time() < self._down_until:
            return
        if self._send(item) == "retry":
            self._down_until = time.time() + NETWORK_DOWN_BACKOFF_SEC

    def _worker(self) -> None:
        last_flush = 0.0
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=1.0)
            except queue.Empty:
                self._busy = True
                try:
                    self._send_pending_state()
                    if time.time() - last_flush >= OUTBOX_RETRY_SEC:
                        self._flush_outbox()
                        last_flush = time.time()
                except Exception as e:
                    self._log(f"백그라운드 전송 처리 오류: {e}")
                finally:
                    self._busy = False
                continue
            self._busy = True
            try:
                self._process(item)
            except Exception as e:
                self._log(f"백그라운드 전송 처리 오류: {e}")
            finally:
                self._busy = False
                self._q.task_done()

    def _enqueue(self, item: Dict[str, Any]) -> bool:
        """비동기: 큐에 넣고 즉시 True 반환 / 동기: 즉시 전송하고 성공 여부 반환."""
        if not self.async_mode:
            if time.time() - self._last_sync_flush >= OUTBOX_RETRY_SEC:
                self._last_sync_flush = time.time()
                try:
                    self._flush_outbox()
                except Exception:
                    pass
            return self._process(item)
        self._ensure_worker()
        self._q.put(item)
        return True

    def pending_count(self) -> int:
        """아직 전송되지 않은 항목 수 (메모리 큐 + 대기 상태)."""
        return self._q.unfinished_tasks + (1 if self._pending_state is not None else 0)

    def flush(self, timeout: float = EXIT_FLUSH_SEC) -> bool:
        """큐가 빌 때까지 최대 timeout 초 대기. 모두 처리되면 True."""
        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            if self._q.unfinished_tasks == 0 and self._pending_state is None and not self._busy:
                return True
            time.sleep(0.05)
        return self._q.unfinished_tasks == 0 and self._pending_state is None and not self._busy

    def _shutdown(self) -> None:
        """프로세스 종료 시: 큐를 최대 5초 비우고, 남은 체결은 outbox 에 보관."""
        try:
            if self._thread is not None and self._thread.is_alive():
                self.flush(EXIT_FLUSH_SEC)
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=1.0)
            # 아직 못 보낸 항목은 다음 기동 때 재전송되도록 보관
            while True:
                try:
                    item = self._q.get_nowait()
                except queue.Empty:
                    break
                self._store(item)
                self._q.task_done()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 체결 기록
    # ------------------------------------------------------------------
    def make_trade_record(
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
    ) -> Dict[str, Any]:
        """체결 1건을 서버 전송용 dict 로 만든다 (log_trade / log_trades_batch 공용)."""
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
        return data

    def _auto_trade_id(self) -> str:
        """trade_id 가 없는 체결에 부여하는 고유 ID (재전송해도 서버가 중복으로 걸러냄)."""
        return f"{hashlib.md5(self.strategy_name.encode('utf-8')).hexdigest()[:6]}-{uuid.uuid4().hex[:20]}"

    def log_trades_batch(self, trades: list) -> bool:
        """
        make_trade_record() 결과 여러 건을 BATCH_TRADES 로 한 번에 전송한다.
        - 전체 청산처럼 한 순간에 여러 건이 생길 때 사용 (HTTP 1회, 서버가 중복 건너뜀)
        - 비동기 모드에서는 큐에 넣고 바로 True 를 반환한다. 실패해도 예외를 내지 않는다.
        """
        if not trades:
            return True
        try:
            records = []
            for t in trades:
                rec = dict(t)
                if not rec.get("trade_id"):
                    rec["trade_id"] = self._auto_trade_id()
                records.append(rec)
            return self._enqueue({"type": "BATCH_TRADES", "data": {"trades": records}, "attempts": 0})
        except Exception as e:
            self._log(f"일괄 체결 전송 준비 오류 (매매 정상 유지): {e}")
            return False

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
        - 비동기 모드(기본)에서는 큐에 넣고 즉시 True 를 반환합니다. 전송 실패분은 outbox 에서 재전송됩니다.
        - 동기 모드(async_mode=False)에서는 실제 전송 결과(True/False)를 반환합니다.
        - 어떤 경우에도 예외를 내지 않아 메인 매매 루프는 중단되지 않습니다.

        선택 인자 (생략하면 기존과 동일하게 동작):
        - trade_id: 같은 체결의 재전송을 서버가 걸러내는 ID. 생략하면 자동으로 부여합니다.
        - event: 봇 원본 이벤트명 (예: 'SELL_CLEAR_PARTIAL')
        - side: 'LONG' / 'SHORT' / '' (양방향 전략용)
        - is_dummy: 가상(더미) 체결 여부
        - close_all: 전량 청산이면 True, 부분 청산이면 False, 해당 없으면 None
        서버가 'duplicate' 로 응답하면 이미 기록된 체결이므로 성공으로 취급합니다.
        """
        try:
            data = self.make_trade_record(
                action=action, symbol=symbol, price=price, amount=amount, fee=fee,
                pnl=pnl, pnl_pct=pnl_pct, level=level, hold_time=hold_time, note=note,
                exchange=exchange, currency=currency, timestamp=timestamp,
                trade_id=trade_id or self._auto_trade_id(), event=event, side=side,
                is_dummy=is_dummy, close_all=close_all,
            )
            return self._enqueue({"type": "TRADE", "data": data, "attempts": 0})
        except Exception as e:
            self._log(f"체결 전송 준비 오류 (기존 매매 정상 유지): {e}")
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
        일별 또는 특정 시점의 전략 자산 스냅샷을 [일별_자산스냅샷] 시트로 전송 (날짜 기본값은 KST 기준)
        """
        try:
            today_str = date_str or datetime.now(KST).strftime("%Y-%m-%d")
            data = {
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
            return self._enqueue({"type": "SNAPSHOT", "data": data, "attempts": 0})
        except Exception as e:
            self._log(f"스냅샷 전송 준비 오류: {e}")
            return False

    def log_bot_state(self, state_data: Dict[str, Any], strategy_name: Optional[str] = None) -> bool:
        """
        EC2 봇의 내부 상태(쿨링다운, 공백 예약, 잔고부족 차수 등)를 구글 시트/대시보드로 전송
        - 비동기 모드에서는 '최신 상태 1건만' 유지합니다 (대기 중 새 상태가 오면 이전 상태를 덮어씀).
        - 상태는 오래되면 의미가 없으므로 실패해도 outbox 에 보관하지 않습니다.
        """
        try:
            item = {
                "type": "STATE_SYNC",
                "data": state_data,
                "extra": {
                    "strategy_name": strategy_name or self.strategy_name,
                    "timestamp": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
                },
                "attempts": 0,
            }
            if not self.webhook_url:
                return False
            if not self.async_mode:
                if time.time() < self._down_until:
                    return False
                result = self._send(item)
                if result == "retry":
                    self._down_until = time.time() + NETWORK_DOWN_BACKOFF_SEC
                return result == "ok"
            with self._state_lock:
                self._pending_state = item
            self._ensure_worker()
            return True
        except Exception as e:
            self._log(f"봇 상태 전송 준비 오류 (매매 정상 지속): {e}")
            return False

    # ------------------------------------------------------------------
    # 관리용 원격 호출 (동기, 수동 실행용)
    # ------------------------------------------------------------------
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
