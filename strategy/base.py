"""
strategy/base.py
전략 기본 추상 클래스 (BaseStrategy)
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Tuple


class BaseStrategy(ABC):
    """
    모든 매매 전략의 기본 인터페이스를 정의하는 추상 클래스
    """
    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def check_entry_signal(
        self,
        current_candle: Dict[str, Any],
        prev_candle: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        진입 조건 충족 여부를 판단합니다.
        Returns:
            Tuple[bool, str]: (진입 조건 충족 여부, 신호 사유/설명)
        """
        pass

    @abstractmethod
    def check_exit_signal(
        self,
        current_price: float,
        indicators: Dict[str, Any],
        cycle_info: Dict[str, Any]
    ) -> Tuple[bool, str, float]:
        """
        청산(익절) 조건 충족 여부 및 목표가를 산출합니다.
        Returns:
            Tuple[bool, str, float]: (청산 충족 여부, 청산 사유, 목표 청산가)
        """
        pass
