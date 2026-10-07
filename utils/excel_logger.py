"""
utils/excel_logger.py
openpyxl 기반 trade_history.xlsx 거래 및 더미 내역 누적 기록 및 손익차트(종합, V3+, V3-) 시각화 모듈 (PRD 섹션 5)
"""

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Union, Dict, Any, List

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.chart.axis import ChartLines
from openpyxl.chart.text import RichText
from openpyxl.drawing.text import (
    CharacterProperties,
    Paragraph,
    ParagraphProperties,
    RichTextProperties,
)

from config import EXCEL_FILE, STATE_FILE
from utils.logger import logger

KST = timezone(timedelta(hours=9))

PNL_SHEET_NAME = "손익차트"
TRADE_SHEET_NAME = "TradeHistory"

COLUMNS = [
    "날짜/시간",
    "전략 구분",
    "이벤트 유형",
    "체결가 ($)",
    "수량 (Qty)",
    "실현손익 ($)",
    "수익률 (%)",
    "유닛 번호",
    "더미 소진 현황",
    "총 평가 잔고 ($)",
    "비고"
]


class ExcelLogger:
    """
    trade_history.xlsx 파일 생성, 행 누적 기록 및 손익차트 시각화 관리자
    """
    def __init__(self, file_path: Union[Path, str] = EXCEL_FILE):
        self.file_path = Path(file_path)
        self._ensure_workbook()

    def _ensure_workbook(self):
        """
        엑셀 파일이 존재하지 않거나 구버전 헤더인 경우 워크북을 생성/보정하고 스타일이 적용된 헤더를 작성합니다.
        """
        need_header_write = False
        if not self.file_path.exists():
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = TRADE_SHEET_NAME
            need_header_write = True
        else:
            wb = openpyxl.load_workbook(self.file_path)
            ws = wb[TRADE_SHEET_NAME] if TRADE_SHEET_NAME in wb.sheetnames else wb.active
            if ws.max_row <= 1:
                # 데이터가 없고 헤더만 있거나 빈 시트인 경우 최신 11컬럼 헤더로 재작성
                ws.delete_rows(1, ws.max_row)
                need_header_write = True

        if need_header_write:
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
            col_widths = [18, 14, 14, 14, 14, 16, 14, 18, 14, 18, 32]
            for idx, width in enumerate(col_widths, start=1):
                col_letter = openpyxl.utils.get_column_letter(idx)
                ws.column_dimensions[col_letter].width = width

            wb.save(self.file_path)
            logger.info(f"[ExcelLogger] 거래 기록 엑셀 파일 헤더 초기화 완료: {self.file_path.name}")

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
        note: str = "",
        realized_pnl: Optional[float] = None,
        pnl_pct: Optional[float] = None,
        auto_update_chart: bool = True
    ):
        """
        매매 집행(BUY_UNIT, SELL_CLEAR) 또는 더미 소진(BUY_DUMMY) 이벤트를 기록합니다.
        SELL_CLEAR 시 실현손익($)과 수익률(%)을 기록하고 손익차트를 자동 갱신합니다.
        """
        try:
            self._ensure_workbook()
            wb = openpyxl.load_workbook(self.file_path)
            ws = wb[TRADE_SHEET_NAME] if TRADE_SHEET_NAME in wb.sheetnames else wb.active

            price_str = f"${price:,.2f}" if price and price > 0 else "-"
            qty_str = f"{qty:,.4f}" if qty and qty > 0 else "-"
            pnl_str = f"${realized_pnl:+,.2f}" if realized_pnl is not None else "-"
            pct_str = f"{pnl_pct:+.2f}%" if pnl_pct is not None else "-"
            balance_str = f"${total_balance:,.2f}"

            # 현재 워크시트의 컬럼 헤더 수 확인 (11컬럼 vs 9컬럼)
            first_row_vals = [cell.value for cell in ws[1]] if ws.max_row >= 1 else []
            has_pnl_cols = "실현손익 ($)" in first_row_vals

            if has_pnl_cols:
                row_data = [
                    date_str,
                    strategy_name,
                    event_type,
                    price_str,
                    qty_str,
                    pnl_str,
                    pct_str,
                    unit_label,
                    dummy_status,
                    balance_str,
                    note
                ]
            else:
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

            note_col_idx = len(row_data)
            for col_idx in range(1, len(row_data) + 1):
                cell = ws.cell(row=last_row, column=col_idx)
                cell.font = Font(name="맑은 고딕", size=10)
                cell.border = thin_border
                cell.alignment = Alignment(horizontal="center" if col_idx != note_col_idx else "left", vertical="center")
                if fill_style:
                    cell.fill = fill_style

            wb.save(self.file_path)
            logger.info(f"[ExcelLogger] 이력 기록 완료: [{strategy_name}] {event_type} - {unit_label} ({note})")

            # 청산 이벤트이거나 자동 갱신 요청 시 손익차트 업데이트
            if auto_update_chart and event_type == "SELL_CLEAR":
                self.update_pnl_chart()

        except Exception as e:
            logger.error(f"[ExcelLogger] 엑셀 기록 중 에러 발생: {e}")

    def update_pnl_chart(self) -> None:
        """
        TradeHistory 기록과 state.json의 상태를 기반으로
        '손익차트' 시트(종합 / V3+ / V3- 실현손익 추이 및 통계)를 생성 및 갱신합니다.
        """
        try:
            self._ensure_workbook()
            wb = openpyxl.load_workbook(self.file_path)
            ws_trade = wb[TRADE_SHEET_NAME] if TRADE_SHEET_NAME in wb.sheetnames else wb.active

            # 1. TradeHistory 데이터 로드 및 맵핑
            rows = list(ws_trade.iter_rows(values_only=True))
            if not rows or len(rows) <= 1:
                trade_records = []
            else:
                headers = [str(h).strip() if h is not None else "" for h in rows[0]]
                col_map = {name: idx for idx, name in enumerate(headers)}
                trade_records = []
                for row in rows[1:]:
                    if not any(row):
                        continue
                    trade_records.append({name: row[idx] if idx < len(row) else None for name, idx in col_map.items()})

            # 2. 청산(SELL_CLEAR) 이벤트 파싱
            sell_records = []
            for rec in trade_records:
                event_type = str(rec.get("이벤트 유형", "")).strip()
                if "SELL_CLEAR" not in event_type:
                    continue

                date_val = str(rec.get("날짜/시간", "")).strip()
                date_str = date_val[:10] if len(date_val) >= 10 else date_val

                strat_raw = str(rec.get("전략 구분", "")).strip().lower()
                if "strategy_a" in strat_raw or "v3+" in strat_raw or "long" in strat_raw:
                    strat_type = "v3_plus"
                elif "strategy_b" in strat_raw or "v3-" in strat_raw or "short" in strat_raw:
                    strat_type = "v3_minus"
                else:
                    strat_type = "other"

                # 실현손익 추출
                pnl_usd = 0.0
                pnl_found = False
                if "실현손익 ($)" in rec and rec["실현손익 ($)"] not in (None, "-", ""):
                    try:
                        pnl_clean = str(rec["실현손익 ($)"]).replace("$", "").replace(",", "").strip()
                        pnl_usd = float(pnl_clean)
                        pnl_found = True
                    except Exception:
                        pnl_found = False

                if not pnl_found:
                    note = str(rec.get("비고", ""))
                    match = re.search(r"손익:\s*([+-]?[\d,]+(?:\.\d+)?)\s*USD", note)
                    if match:
                        try:
                            pnl_usd = float(match.group(1).replace(",", ""))
                            pnl_found = True
                        except Exception:
                            pnl_usd = 0.0

                sell_records.append({
                    "date": date_str,
                    "strat_type": strat_type,
                    "pnl": pnl_usd
                })

            # 3. 날짜별 일일/누적 실현손익 집계
            all_dates = sorted(list(set(r["date"] for r in sell_records if r["date"])))
            daily_stats = []
            cum_total = 0.0
            cum_v3_plus = 0.0
            cum_v3_minus = 0.0

            for d in all_dates:
                pnl_plus_day = sum(r["pnl"] for r in sell_records if r["date"] == d and r["strat_type"] == "v3_plus")
                pnl_minus_day = sum(r["pnl"] for r in sell_records if r["date"] == d and r["strat_type"] == "v3_minus")
                pnl_total_day = pnl_plus_day + pnl_minus_day

                # 일일 손익이 0인 날짜는 차트 가독성을 위해 제외
                if abs(pnl_total_day) < 1e-6 and abs(pnl_plus_day) < 1e-6 and abs(pnl_minus_day) < 1e-6:
                    continue

                cum_total += pnl_total_day
                cum_v3_plus += pnl_plus_day
                cum_v3_minus += pnl_minus_day

                daily_stats.append({
                    "date": d,
                    "pnl_total": pnl_total_day,
                    "cum_total": cum_total,
                    "pnl_v3_plus": pnl_plus_day,
                    "cum_v3_plus": cum_v3_plus,
                    "pnl_v3_minus": pnl_minus_day,
                    "cum_v3_minus": cum_v3_minus
                })

            # 4. state.json 조회하여 미청산 상태 파악
            state_data = {}
            if Path(STATE_FILE).exists():
                try:
                    with open(STATE_FILE, "r", encoding="utf-8") as f:
                        state_data = json.load(f)
                except Exception:
                    state_data = {}

            state_a = state_data.get("strategy_A", {})
            state_b = state_data.get("strategy_B", {})

            v3p_qty = float(state_a.get("total_qty", 0.0))
            v3p_avg = float(state_a.get("avg_price", 0.0))
            v3p_open_desc = f"{v3p_qty:,.4f} Qty (@${v3p_avg:,.2f})" if v3p_qty > 0 else "미보유 (0 Qty)"

            v3m_qty = float(state_b.get("total_qty", 0.0))
            v3m_avg = float(state_b.get("avg_price", 0.0))
            v3m_open_desc = f"{v3m_qty:,.4f} Qty (@${v3m_avg:,.2f})" if v3m_qty > 0 else "미보유 (0 Qty)"

            if v3p_qty > 0 and v3m_qty > 0:
                total_open_desc = "양방향 동시 보유 (롱 & 숏)"
            elif v3p_qty > 0:
                total_open_desc = "롱 포지션 보유중 (V3+)"
            elif v3m_qty > 0:
                total_open_desc = "숏 포지션 보유중 (V3-)"
            else:
                total_open_desc = "전략 전체 미보유 (현금 대기)"

            v3p_clear_cnt = sum(1 for r in sell_records if r["strat_type"] == "v3_plus")
            v3m_clear_cnt = sum(1 for r in sell_records if r["strat_type"] == "v3_minus")
            total_clear_cnt = len(sell_records)

            total_pnl = cum_total
            total_pnl_v3p = cum_v3_plus
            total_pnl_v3m = cum_v3_minus

            # 5. '손익차트' 시트 생성 (기존 시트 교체)
            if PNL_SHEET_NAME in wb.sheetnames:
                del wb[PNL_SHEET_NAME]
            ws = wb.create_sheet(PNL_SHEET_NAME)

            # 스타일 정의
            font_title = Font(name="맑은 고딕", size=14, bold=True, color="595959")
            font_kpi_head = Font(name="맑은 고딕", size=10, bold=True, color="FFFFFF")
            fill_kpi_head = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
            thin_border = Border(
                left=Side(style="thin", color="D9D9D9"),
                right=Side(style="thin", color="D9D9D9"),
                top=Side(style="thin", color="D9D9D9"),
                bottom=Side(style="thin", color="D9D9D9"),
            )
            align_center = Alignment(horizontal="center", vertical="center")
            align_right = Alignment(horizontal="right", vertical="center")
            align_left = Alignment(horizontal="left", vertical="center")

            # 5-1. 상단 KPI 요약 카드 작성
            ws["A1"] = "실현 손익 추이 (V3+ & V3- 종합 대시보드)"
            ws["A1"].font = font_title

            kpi_rows = [
                ("지표 구분", "종합 성과 (Total)", "V3+ 전략 (Long DCA)", "V3- 전략 (Short DCA)"),
                ("지금까지 누적 실현손익(USDT)", total_pnl, total_pnl_v3p, total_pnl_v3m),
                ("청산 완료 건수", f"{total_clear_cnt} 건", f"{v3p_clear_cnt} 건", f"{v3m_clear_cnt} 건"),
                ("현재 미청산(보유) 현황", total_open_desc, v3p_open_desc, v3m_open_desc),
                ("기준시각 (KST)", datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"), "-", "-"),
            ]

            for r_offset, row_vals in enumerate(kpi_rows, start=2):
                for c_offset, val in enumerate(row_vals, start=1):
                    cell = ws.cell(row=r_offset, column=c_offset, value=val)
                    cell.border = thin_border
                    if r_offset == 2:
                        cell.fill = fill_kpi_head
                        cell.font = font_kpi_head
                        cell.alignment = align_center
                    elif r_offset == 3:  # 누적 실현손익
                        if c_offset == 1:
                            cell.font = Font(name="맑은 고딕", size=10, bold=True)
                            cell.alignment = align_left
                        else:
                            color_code = "C00000" if c_offset == 2 else ("1F497D" if c_offset == 3 else "ED7D31")
                            cell.font = Font(name="맑은 고딕", size=12, bold=True, color=color_code)
                            cell.alignment = align_right
                            cell.number_format = "$#,##0.00;($#,##0.00);$0.00"
                    else:
                        cell.font = Font(name="맑은 고딕", size=10)
                        cell.alignment = align_center if c_offset > 1 else align_left

            # 5-2. 일자별 상세 집계 데이터 테이블 작성 (Row 8부터 시작)
            table_headers = [
                "기간",
                "종합 일일손익(USDT)",
                "종합 누적손익(USDT)",
                "V3+ 일일손익(USDT)",
                "V3+ 누적손익(USDT)",
                "V3- 일일손익(USDT)",
                "V3- 누적손익(USDT)",
            ]
            start_row = 8
            fill_tbl_head = PatternFill(start_color="F2F2F2", end_color="F2F2F2", fill_type="solid")
            font_tbl_head = Font(name="맑은 고딕", size=10, bold=True, color="333333")

            for c_idx, h_text in enumerate(table_headers, start=1):
                cell = ws.cell(row=start_row, column=c_idx, value=h_text)
                cell.fill = fill_tbl_head
                cell.font = font_tbl_head
                cell.alignment = align_center
                cell.border = thin_border

            data_row_count = len(daily_stats)
            for r_idx, stat in enumerate(daily_stats, start=start_row + 1):
                row_vals = [
                    stat["date"],
                    stat["pnl_total"],
                    stat["cum_total"],
                    stat["pnl_v3_plus"],
                    stat["cum_v3_plus"],
                    stat["pnl_v3_minus"],
                    stat["cum_v3_minus"],
                ]
                for c_idx, val in enumerate(row_vals, start=1):
                    cell = ws.cell(row=r_idx, column=c_idx, value=val)
                    cell.border = thin_border
                    cell.font = Font(name="맑은 고딕", size=10)
                    if c_idx == 1:
                        cell.alignment = align_center
                    else:
                        cell.alignment = align_right
                        cell.number_format = "#,##0.00;(#,##0.00);0.00"

            # 열 너비 설정
            ws.column_dimensions["A"].width = 16
            ws.column_dimensions["B"].width = 22
            ws.column_dimensions["C"].width = 22
            ws.column_dimensions["D"].width = 20
            ws.column_dimensions["E"].width = 20
            ws.column_dimensions["F"].width = 20
            ws.column_dimensions["G"].width = 20
            ws.column_dimensions["H"].width = 4

            # 5-3. 콤보 차트 3종 생성 (데이터가 1건 이상일 때)
            if data_row_count > 0:
                cats = Reference(ws, min_col=1, min_row=start_row + 1, max_row=start_row + data_row_count)

                def _build_chart(title, b_col, l_col, b_color, l_color, marker):
                    b_ref = Reference(ws, min_col=b_col, min_row=start_row, max_row=start_row + data_row_count)
                    l_ref = Reference(ws, min_col=l_col, min_row=start_row, max_row=start_row + data_row_count)

                    bar = BarChart()
                    bar.type = "col"
                    bar.grouping = "clustered"
                    bar.title = title
                    bar.style = 10
                    bar.width = 17
                    bar.height = 11

                    bar.x_axis.delete = False
                    bar.x_axis.axPos = "b"
                    bar.x_axis.tickLblPos = "nextTo"
                    bar.x_axis.majorTickMark = "out"
                    bar.x_axis.txPr = RichText(
                        bodyPr=RichTextProperties(
                            anchor="ctr",
                            anchorCtr="1",
                            rot="-5400000",
                            spcFirstLastPara="1",
                            vertOverflow="ellipsis",
                            wrap="square",
                        ),
                        p=[Paragraph(pPr=ParagraphProperties(defRPr=CharacterProperties()), endParaRPr=CharacterProperties())],
                    )

                    bar.y_axis.delete = False
                    bar.y_axis.axPos = "l"
                    bar.y_axis.tickLblPos = "nextTo"
                    bar.y_axis.majorTickMark = "out"
                    bar.y_axis.number_format = "#,##0"
                    bar.y_axis.majorGridlines = ChartLines()

                    bar.add_data(b_ref, titles_from_data=True)
                    bar.set_categories(cats)
                    bar.shape = 4
                    bar.gapWidth = 80
                    bar.legend.position = "r"

                    if bar.series:
                        bar.series[0].graphicalProperties.solidFill = b_color

                    line = LineChart()
                    line.add_data(l_ref, titles_from_data=True)
                    line.set_categories(cats)
                    if line.series:
                        s = line.series[0]
                        s.graphicalProperties.line.solidFill = l_color
                        s.graphicalProperties.line.width = 25000
                        s.marker.symbol = marker
                        s.marker.size = 5

                    bar += line
                    return bar

                ws.add_chart(_build_chart("종합 실현 손익 추이 (단위: USDT)", 2, 3, "4472C4", "C00000", "circle"), "I2")
                ws.add_chart(_build_chart("V3+ 전략 (Long DCA) 실현 손익 추이", 4, 5, "5B9BD5", "1F497D", "square"), "I18")
                ws.add_chart(_build_chart("V3- 전략 (Short DCA) 실현 손익 추이", 6, 7, "ED7D31", "A5531B", "triangle"), "I34")

            wb.save(self.file_path)
            logger.info(f"[ExcelLogger] '손익차트' 시트 갱신 완료 (총 {data_row_count}일 거래 집계)")
        except Exception as e:
            logger.error(f"[ExcelLogger] 손익차트 시트 갱신 중 에러 발생: {e}")


excel_logger = ExcelLogger()

