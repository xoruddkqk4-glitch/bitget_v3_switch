"""
utils/excel_logger.py
openpyxl 기반 trade_history.xlsx 거래 및 더미 내역 누적 기록 모듈 (PRD 섹션 5)
"""

from pathlib import Path
from typing import Optional, Union
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from config import EXCEL_FILE
from utils.logger import logger

COLUMNS = [
    "날짜/시간",
    "전략 구분",
    "이벤트 유형",
    "체결가 ($)",
    "수량 (Qty)",
    "유닛 번호",
    "더미 소진 현황",
    "총 평가 잔고 ($)",
    "비고"
]


class ExcelLogger:
    """
    trade_history.xlsx 파일 생성 및 행 누적 기록기
    """
    def __init__(self, file_path: Union[Path, str] = EXCEL_FILE):
        self.file_path = Path(file_path)
        self._ensure_workbook()

    def _ensure_workbook(self):
        """
        엑셀 파일이 존재하지 않는 경우 신규 워크북을 생성하고 스타일이 적용된 헤더를 작성합니다.
        """
        if not self.file_path.exists():
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "TradeHistory"

            # 헤더 스타일 설정
            header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
            header_font = Font(name="맑은 고딕", size=11, bold=True, color="FFFFFF")
            thin_border = Border(
                left=Side(style='thin', color='D9D9D9'),
                right=Side(style='thin', color='D9D9D9'),
                top=Side(style='thin', color='D9D9D9'),
                bottom=Side(style='thin', color='D9D9D9')
            )
            alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

            ws.append(COLUMNS)

            for col_idx in range(1, len(COLUMNS) + 1):
                cell = ws.cell(row=1, column=col_idx)
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = alignment
                cell.border = thin_border

            # 열 너비 자동 조정
            col_widths = [18, 14, 14, 14, 14, 18, 14, 18, 32]
            for idx, width in enumerate(col_widths, start=1):
                col_letter = openpyxl.utils.get_column_letter(idx)
                ws.column_dimensions[col_letter].width = width

            wb.save(self.file_path)
            logger.info(f"[ExcelLogger] 신규 기록용 엑셀 파일 생성: {self.file_path.name}")

    def append_record(
        self,
        date_str: str,
        strategy_name: str,
        event_type: str,
        price: Optional[float],
        qty: Optional[float],
        unit_label: str,
        dummy_status: str,
        total_balance: float,
        note: str = ""
    ):
        """
        매매 집행(BUY_UNIT, SELL_CLEAR) 또는 더미 소진(BUY_DUMMY) 이벤트를 기록합니다.
        """
        try:
            self._ensure_workbook()
            wb = openpyxl.load_workbook(self.file_path)
            ws = wb["TradeHistory"] if "TradeHistory" in wb.sheetnames else wb.active

            price_str = f"${price:,.2f}" if price and price > 0 else "-"
            qty_str = f"{qty:,.4f}" if qty and qty > 0 else "-"
            balance_str = f"${total_balance:,.2f}"

            row_data = [
                date_str,
                strategy_name,
                event_type,
                price_str,
                qty_str,
                unit_label,
                dummy_status,
                balance_str,
                note
            ]
            ws.append(row_data)

            # 추가된 행 스타일 적용
            last_row = ws.max_row
            thin_border = Border(
                left=Side(style='thin', color='E0E0E0'),
                right=Side(style='thin', color='E0E0E0'),
                top=Side(style='thin', color='E0E0E0'),
                bottom=Side(style='thin', color='E0E0E0')
            )

            # 이벤트 유형별 배경색 하이라이트
            event_fills = {
                "BUY_UNIT": PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid"),   # 연초록
                "SELL_CLEAR": PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid"), # 연주황
                "BUY_DUMMY": PatternFill(start_color="EDEDED", end_color="EDEDED", fill_type="solid"),  # 연회색
            }
            fill_style = event_fills.get(event_type, None)

            for col_idx in range(1, len(row_data) + 1):
                cell = ws.cell(row=last_row, column=col_idx)
                cell.font = Font(name="맑은 고딕", size=10)
                cell.border = thin_border
                cell.alignment = Alignment(horizontal="center" if col_idx != 9 else "left", vertical="center")
                if fill_style:
                    cell.fill = fill_style

            wb.save(self.file_path)
            logger.info(f"[ExcelLogger] 이력 기록 완료: [{strategy_name}] {event_type} - {unit_label} ({note})")
        except Exception as e:
            logger.error(f"[ExcelLogger] 엑셀 기록 중 에러 발생: {e}")


excel_logger = ExcelLogger()
