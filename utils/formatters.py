"""
utils/formatters.py
차수 진행 상태 및 텍스트 시각화 포맷팅 유틸리티
"""

from typing import Optional
from config import PROGRESS_FILLED_ICON, PROGRESS_EMPTY_ICON, DUMMY_TARGET, UNIT_DIVISOR


def format_progress_single_track(
    dummy_count: int,
    executed_units: int,
    max_dummy: int = int(DUMMY_TARGET),
    max_units: int = int(UNIT_DIVISOR),
    filled_char: Optional[str] = None,
    empty_char: Optional[str] = None,
    include_counts: bool = True
) -> str:
    """
    더미 차수(기본 2차)와 실제 매수 차수(기본 10차)를 단일 트랙 구분 바로 시각화합니다.
    
    예시:
    - [더미: 🟨⬛ ┆ 실제: ⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛] (더미 1/2, 실제 0/10)
    - [더미: 🟨🟨 ┆ 실제: 🟨🟨🟨⬛⬛⬛⬛⬛⬛⬛] (더미 2/2, 실제 3/10)
    
    :param dummy_count: 소진된 더미 횟수 (0~max_dummy)
    :param executed_units: 실제 체결된 매수 유닛 수 (0~max_units)
    :param max_dummy: 최대 더미 차수 (기본 2)
    :param max_units: 최대 실제 매수 차수 (기본 10)
    :param filled_char: 체결 완료/소진 표시 아이콘 (기본 노란색 사각 블록 '🟨')
    :param empty_char: 미체결/빈 슬롯 표시 아이콘 (기본 검정색 사각 블록 '⬛')
    :param include_counts: 우측에 수치 카운트 '(더미 X/2, 실제 Y/10)' 병기 여부
    :return: 시각화된 진행 상태 문자열
    """
    f_char = filled_char if filled_char is not None else PROGRESS_FILLED_ICON
    e_char = empty_char if empty_char is not None else PROGRESS_EMPTY_ICON

    d_count = min(max(int(dummy_count or 0), 0), max_dummy)
    u_count = min(max(int(executed_units or 0), 0), max_units)

    dummy_boxes = (f_char * d_count) + (e_char * (max_dummy - d_count))
    unit_boxes = (f_char * u_count) + (e_char * (max_units - u_count))

    bar = f"[더미: {dummy_boxes} ┆ 실제: {unit_boxes}]"
    if include_counts:
        return f"{bar} (더미 {d_count}/{max_dummy}, 실제 {u_count}/{max_units})"
    return bar
