"""
api/exceptions.py
자동매매 시스템 커스텀 예외 정의
"""


class TradingError(Exception):
    """자동매매 시스템 공통 예외 베이스 (실전 매매 중단 경보 대상)"""


class ExchangeConfigError(TradingError):
    """거래소 계정 설정(포지션 모드/마진 모드/레버리지/계약 단위)이 기대값과 다를 때"""


class DataUnavailableError(TradingError):
    """실전 모드에서 시세/잔고/포지션 데이터를 신뢰할 수 없을 때 (모의 데이터 대체 금지)"""


class StateCorruptedError(TradingError):
    """state.json 손상으로 실전 매매를 계속할 수 없을 때"""


class OrderSkipError(TradingError):
    """최소 수량 미달 등으로 주문을 정상적으로 생략해야 할 때 (경고 수준)"""


class OrderNotConfirmedError(TradingError):
    """주문 발송 후 체결 여부를 확인하지 못했을 때 (상태 기록 금지, 다음 실행의 포지션 대조로 감지)"""
