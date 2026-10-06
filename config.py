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
LEVERAGE = 1                   # 1배율
MARGIN_MODE = "cross"          # 교차 마진 (Cross Margin)
POSITION_MODE = "hedge"        # 헤지 모드 (Long/Short 동시 독립 보유)

# ------------------------------------------------------------------------------
# 3. 자금 관리 및 유닛 가중치 설정 (PRD 섹션 2)
# ------------------------------------------------------------------------------
UNIT_DIVISOR = 10.0            # 1.0 Unit = 총 평가 잔고 / 10
DUMMY_TARGET = 2               # 1~2회차 신호는 더미(0원) 소진 처리
WEIGHTED_UNIT_THRESHOLD = 4    # 실제 매수 1~4회차(executed_units 0~3): 1.0x, 5회차 이상: 1.25x
WEIGHT_NORMAL = 1.0
WEIGHT_BOOST = 1.25

# ------------------------------------------------------------------------------
# 4. A 전략: Long DCA 조건 (PRD 섹션 3)
# ------------------------------------------------------------------------------
LONG_BEARISH_CANDLE_PCT = -0.015       # 당일 음봉 <= -1.5% (종가/시가 - 1)
LONG_DAILY_DROP_PCT = -0.030           # 전일 대비 하락률 <= -3.0% (종가/전일종가 - 1)
DRAWDOWN_THRESHOLD = -0.30             # 낙폭률 임계치 -30%

# A 전략 청산 계수 (SMA5 배수)
LONG_EXIT_RISING_SMA = 1.02            # 60일선 상승 시 Target = SMA5 * 1.02 이상
LONG_EXIT_FALLING_SMA_MILD = 0.99      # 60일선 하락 & 낙폭 > -30% Target = SMA5 * 0.99 이하 (본전탈출)
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
