"""
config.py
bitget_v3_switch 자동매매 시스템 전역 설정 모듈
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 기본 디렉터리 경로 설정
BASE_DIR = Path(__file__).resolve().parent

# .env 로드 (존재할 경우)
load_dotenv(BASE_DIR / ".env")

# ------------------------------------------------------------------------------
# 1. API 및 외부 서비스 인증 설정
# ------------------------------------------------------------------------------
BITGET_API_KEY = os.getenv("BITGET_API_KEY", "")
BITGET_SECRET = os.getenv("BITGET_SECRET", "")
BITGET_PASSPHRASE = os.getenv("BITGET_PASSPHRASE", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 모의 매매 (Paper Trading) 모드 (기본값 True - API 키 미등록 시 안전 실행)
PAPER_TRADING = os.getenv("PAPER_TRADING", "true").lower() in ("true", "1", "yes")
MOCK_BALANCE = float(os.getenv("MOCK_BALANCE", "10000.0"))

# ------------------------------------------------------------------------------
# 2. 거래소 및 마켓 세부 설정 (PRD 섹션 1)
# ------------------------------------------------------------------------------
SYMBOL = "SOXL/USDT:USDT"      # Bitget 선물 마켓 티커
TIMEFRAME = "1d"               # 일봉 기준 실행
CANDLE_LIMIT = 100             # 60일선 및 전고점/전저점 산출을 위한 일봉 수집 개수
TIMEFRAME_4H = "4h"            # 2차 추세 감시용 4시간봉
CANDLE_LIMIT_4H = 30           # 4시간봉 수집 개수
SMA_4H_PERIOD = 5              # 4시간봉 5MA 기간
LEVERAGE = 1                   # 1배율
MARGIN_MODE = "cross"          # 교차 마진 (Cross Margin)
POSITION_MODE = "hedge"        # 헤지 모드 (Long/Short 동시 독립 보유)

# 2단계 트레일링 매도 및 스케줄링 설정
DAILY_EVAL_HOUR_KST = 9        # 매일 일봉 마감 평가 시간(KST)
DAILY_EVAL_START_MIN = 10      # 일봉 마감 평가 시작 분 (09:10 KST)
DAILY_EVAL_END_MIN = 30        # 일봉 마감 평가 종료 분 (09:30 KST, 실패 시 재시도 허용 / 중복은 last_daily_eval_candle 가드가 차단)
PRESERVATION_BUFFER_PCT = 0.002 # 최소 이익 보존선 슬리피지 허용 버퍼 (0.2%)
STAGE2_SIGNAL_MODE = "closed_candle"  # 2단계 4H 5MA 이탈 판정: "closed_candle"(마감된 4H 완성봉 종가, 권장) | "realtime"(진행 중 캔들 실시간가)
HYBRID_TRAILING_MIN_PNL_PCT = 5.0     # 완만한 장세 본탈 조건 도달 시 수익률(%) 이상이면 2단계 트레일링 익절로 승격 (+5.0%)

TAKER_FEE_RATE = 0.0006
ORDER_CONFIRM_RETRY_DELAYS = (0.5, 1.0, 2.0)
PARTIAL_FILL_RATIO = 0.99
RECONCILE_TOLERANCE_PCT = 0.01
RECONCILE_AUTO_SYNC = True
ALERT_COOLDOWN_SEC = 3600


# ------------------------------------------------------------------------------
# 3. 자금 관리 및 유닛 가중치 설정 (PRD 섹션 2)
# ------------------------------------------------------------------------------
UNIT_DIVISOR = 16.0            # 1.0 Unit = 기준 잔고 / 16 (13회차 소요량 1.0x4 + 1.25x9 = 15.25 Unit + 0.75 Unit 여유)
MAX_EXECUTED_UNITS = 13        # 싸이클당 실제 매수 최대 회차 (UNIT_DIVISOR와 분리된 독립 상한)
MARGIN_SAFETY_RATIO = 0.95     # 1회 주문금액 상한 = 가용 잔고 × 레버리지 × 0.95 (증거금 부족 주문 거절 방지)
MIN_ORDER_USD = 5.0            # Bitget 선물 최소 주문 가능 금액 ($5.0)
DUMMY_TARGET = 2               # 1~2회차 신호는 더미(0원) 소진 처리
WEIGHTED_UNIT_THRESHOLD = 4    # 실제 매수 1~4회차(executed_units 0~3): 1.0x, 5회차 이상: 1.25x
WEIGHT_NORMAL = 1.0
WEIGHT_BOOST = 1.25

# 차수 시각화 인디케이터 이모지 설정
PROGRESS_FILLED_ICON = "🟨"    # 체결 완료/소진 차수 아이콘 (노란색 사각 블록 '🟨', 또는 체크 박스 '☑️', '✅')
PROGRESS_EMPTY_ICON = "⬛"     # 미체결/빈 슬롯 차수 아이콘 (검정색 사각 블록 '⬛', 또는 빈 네모 '⬜')

# ------------------------------------------------------------------------------
# 4. A 전략: Long DCA 조건 (PRD 섹션 3)
# ------------------------------------------------------------------------------
LONG_BEARISH_CANDLE_PCT = -0.015       # 당일 음봉 <= -1.5% (종가/시가 - 1)
LONG_DAILY_DROP_PCT = -0.030           # 전일 대비 하락률 <= -3.0% (종가/전일종가 - 1)
DRAWDOWN_THRESHOLD = -0.30             # 낙폭률 임계치 -30%

# A 전략 청산 계수 (SMA5 배수)
LONG_EXIT_RISING_SMA = 1.02            # 60일선 상승 시 Target = SMA5 * 1.02 이상
LONG_EXIT_FALLING_SMA_MILD = 0.99      # 60일선 하락 & 낙폭 > -30% Target = SMA5 * 0.99 이상 회복 시 즉시 청산 (본전탈출)
BREAKEVEN_GUARD_ENABLED = False        # (선택) 본전탈출 시 현재가 >= 평단 × (1 + BREAKEVEN_GUARD_MIN_PCT) 일 때만 청산
BREAKEVEN_GUARD_MIN_PCT = 0.002        # 본전 가드 최소 수익률 (0.2% ≈ 왕복 수수료)
LONG_EXIT_FALLING_SMA_DEEP = 1.03      # 60일선 하락 & 낙폭 <= -30% Target = SMA5 * 1.03 이상 (반등익절)

# ------------------------------------------------------------------------------
# 5. B 전략: Short DCA 조건 (PRD 섹션 3)
# ------------------------------------------------------------------------------
SHORT_BULLISH_CANDLE_PCT = 0.015       # 당일 양봉 >= +1.5% (종가/시가 - 1)
SHORT_DAILY_PUMP_PCT = 0.030           # 전일 대비 상승률 >= +3.0% (종가/전일종가 - 1)
REBOUND_THRESHOLD = 0.30               # 반등폭 임계치 +30%

# B 전략 청산 계수 (SMA5 배수)
SHORT_EXIT_RISING_SMA = 0.98           # 60일선 상승 시 Target = SMA5 * 0.98 이하
SHORT_EXIT_FALLING_SMA_MILD = 1.01     # 60일선 하락 & 반등 < +30% Target = SMA5 * 1.01 이하 (본전탈출)
SHORT_EXIT_FALLING_SMA_DEEP = 0.97     # 60일선 하락 & 반등 >= +30% Target = SMA5 * 0.97 이하 (눌림익절)

# ------------------------------------------------------------------------------
# 6. 파일 저장 경로 설정 (PRD 섹션 4 & 5)
# ------------------------------------------------------------------------------
STATE_FILE = BASE_DIR / "state.json"
EXCEL_FILE = BASE_DIR / "trade_history.xlsx"
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "trading.log"
ALERT_THROTTLE_FILE = LOG_DIR / "alert_throttle.json"


# ------------------------------------------------------------------------------
# 7. 실전 모드 설정 검증
# ------------------------------------------------------------------------------
def validate_live_config() -> list:
    """
    실전 모드(PAPER_TRADING=False)에서 필수 API 설정 누락 항목 이름 리스트를 반환합니다.
    빈 리스트면 정상입니다. 모의 모드에서는 항상 빈 리스트를 반환합니다.
    """
    if PAPER_TRADING:
        return []
    required = (
        ("BITGET_API_KEY", BITGET_API_KEY),
        ("BITGET_SECRET", BITGET_SECRET),
        ("BITGET_PASSPHRASE", BITGET_PASSPHRASE),
    )
    return [name for name, value in required if not str(value or "").strip()]
