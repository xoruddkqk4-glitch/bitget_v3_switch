# -*- coding: utf-8 -*-
"""
=============================================================================
LS_API_Helper_KR.py  —  LS증권 국내주식 헬퍼 (자체 구현본)

LS증권 OpenAPI 공식 명세를 참고하여 직접 작성했습니다.
KIS 모듈과 동일한 함수 인터페이스를 제공해, 봇이 증권사만 바꿔 쓸 수 있게 합니다.

[현재 구현 — 받은 시세 문서 기반]
  · GetCurrentPrice : 현재가            (t1102 주식현재가시세)
  · GetHoga         : 10호가             (t1101 주식현재가호가)
  · GetStockName    : 종목명             (t1102)
  · GetStockAmt     : 수량단위/증거금율   (t1102)
  · GetStockTickerList : 전체 종목 리스트 (t9945 주식마스터)
  · PriceAdjust     : 국내 호가단위 보정  (한국거래소 현행 규칙)
  · GetOhlcv        : 일봉 (LS_Common에 위임)
  · IsMarketOpen    : 장중 여부
  · GetOhlcvNew     : GetOhlcv 별칭(호환)

[미구현 — 주문/잔고/미체결 API 문서 수령 후 추가 예정]
  · GetBalance, GetMyStockList, MakeBuy/SellLimitOrder, MakeBuy/SellMarketOrder,
    CancelOrder, ModifyOrder, CancelAllOrders, GetOrderList, SellAllStock,
    CheckPossibleBuyInfo, AdjustPossibleAmt, GetMarketOrderPrice
  → 호출 시 NotImplementedError 안내 (문서 받으면 즉시 구현)

LS는 계좌번호가 필요 없습니다(토큰에 계좌 매핑). 주문/잔고도 동일.
=============================================================================
"""

import json
import requests
from datetime import datetime

import LS_Common_new as Common

# LS 시세/차트 공통 엔드포인트
_MARKET_URL = "/stock/market-data"


def _post(tr_cd, in_block_name, in_block, out_block_name):
    """
    LS 시세 API 공통 POST 헬퍼.
    tr_cd로 거래코드를 지정하고, 입력 블록을 담아 호출한 뒤 출력 블록을 반환합니다.
    """
    url = f"{Common.GetUrlBase(Common.GetNowDist())}{_MARKET_URL}"
    headers = Common.GetCommonHeader(tr_cd=tr_cd)
    body = {in_block_name: in_block}
    try:
        res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
        if res.status_code != 200:
            print(f"[{tr_cd}] HTTP {res.status_code}: {res.text[:150]}")
            return None
        j = res.json()
        # LS는 rsp_cd "00000"이 정상
        if str(j.get("rsp_cd", "")) not in ("00000", ""):
            print(f"[{tr_cd}] 조회 실패: {j.get('rsp_msg')}")
            return None
        return j.get(out_block_name)
    except Exception as e:
        print(f"[{tr_cd}] 오류: {e}")
        return None


# ---------------------------------------------------------------------------
# 현재가 / 시세 (t1102)
# ---------------------------------------------------------------------------
def GetCurrentPrice(stock_code):
    """현재가를 반환합니다. (t1102 주식현재가시세조회의 price)"""
    out = _post("t1102", "t1102InBlock", {"shcode": stock_code}, "t1102OutBlock")
    if not out:
        return None
    try:
        return float(out.get("price", 0) or 0)
    except Exception:
        return None


def GetStockName(stock_code):
    """종목명을 반환합니다. (t1102의 hname)"""
    out = _post("t1102", "t1102InBlock", {"shcode": stock_code}, "t1102OutBlock")
    if not out:
        return None
    return out.get("hname", "")


def GetStockAmt(stock_code):
    """
    종목의 수량단위·증거금율 등 거래 정보를 반환합니다. (t1102)
    반환 dict: price(현재가), memedan(수량단위), jkrate(증거금율),
              uplmtprice(상한가), dnlmtprice(하한가)
    """
    out = _post("t1102", "t1102InBlock", {"shcode": stock_code}, "t1102OutBlock")
    if not out:
        return None
    def _num(v):
        try:
            return float(v)
        except Exception:
            return 0.0
    return {
        "price": _num(out.get("price", 0)),
        "memedan": out.get("memedan", ""),       # 수량단위
        "jkrate": _num(out.get("jkrate", 0)),     # 증거금율
        "uplmtprice": _num(out.get("uplmtprice", 0)),
        "dnlmtprice": _num(out.get("dnlmtprice", 0)),
        "open": _num(out.get("open", 0)),
        "high": _num(out.get("high", 0)),
        "low": _num(out.get("low", 0)),
        "volume": _num(out.get("volume", 0)),
    }


# ---------------------------------------------------------------------------
# 호가 (t1101)
# ---------------------------------------------------------------------------
def GetHoga(stock_code):
    """
    10호가를 조회합니다. (t1101 주식현재가호가조회)
    반환: {'ask': [매도1~10], 'bid': [매수1~10],
           'ask_rem': [매도잔량...], 'bid_rem': [매수잔량...]}
    """
    out = _post("t1101", "t1101InBlock", {"shcode": stock_code}, "t1101OutBlock")
    if not out:
        return None
    ask, bid, ask_rem, bid_rem = [], [], [], []
    for i in range(1, 11):
        a = out.get(f"offerho{i}")     # 매도호가
        b = out.get(f"bidho{i}")       # 매수호가
        ar = out.get(f"offerrem{i}")   # 매도호가수량
        br = out.get(f"bidrem{i}")     # 매수호가수량
        if a not in (None, "", 0):
            ask.append(float(a))
        if b not in (None, "", 0):
            bid.append(float(b))
        if ar not in (None, ""):
            ask_rem.append(float(ar))
        if br not in (None, ""):
            bid_rem.append(float(br))
    return {"ask": ask, "bid": bid, "ask_rem": ask_rem, "bid_rem": bid_rem}


# ---------------------------------------------------------------------------
# 종목 마스터 (t9945)
# ---------------------------------------------------------------------------
def GetStockTickerList(market="KOSPI"):
    """
    전체 종목 리스트를 반환합니다. (t9945 주식마스터조회)
    market="KOSPI"(gubun=1) / "KOSDAQ"(gubun=2)
    반환: [{'code','name','etf'}, ...]
    """
    gubun = "1" if market.upper() in ("KOSPI", "KSP", "1") else "2"
    out = _post("t9945", "t9945InBlock", {"gubun": gubun}, "t9945OutBlock")
    if not out:
        return []
    result = []
    for row in out:
        result.append({
            "code": row.get("shcode", ""),
            "name": row.get("hname", ""),
            "etf": row.get("etfchk", "0") == "1",
        })
    return result


# ---------------------------------------------------------------------------
# 호가단위 보정 (한국거래소 현행 규칙 — KIS와 동일)
# ---------------------------------------------------------------------------
def PriceAdjust(price, up=False):
    """
    국내 주식 가격을 호가단위(틱)에 맞게 보정합니다.
    한국거래소 현행 호가단위(2023-01-25 개편):
      2,000 미만→1 / ~5,000→5 / ~20,000→10 / ~50,000→50 /
      ~200,000→100 / ~500,000→500 / 500,000~→1,000원
    up=False면 내림(매수 안전), up=True면 올림(매도 안전).
    """
    price = float(price)
    if price < 2000:
        tick = 1
    elif price < 5000:
        tick = 5
    elif price < 20000:
        tick = 10
    elif price < 50000:
        tick = 50
    elif price < 200000:
        tick = 100
    elif price < 500000:
        tick = 500
    else:
        tick = 1000
    if up:
        return int(-(-int(price) // tick) * tick)
    return int((int(price) // tick) * tick)


# ---------------------------------------------------------------------------
# 일봉 (LS_Common에 위임)
# ---------------------------------------------------------------------------
def GetOhlcv(area, stock_code, limit=500, adj_ok="1", source="ls"):
    """일봉 OHLCV. LS_Common.GetOhlcv로 위임합니다."""
    return Common.GetOhlcv(area, stock_code, limit, adj_ok, source)


def GetOhlcvNew(area, stock_code, limit=500, adj_ok="1", source="ls"):
    """GetOhlcv 별칭 (호환용)."""
    return Common.GetOhlcv(area, stock_code, limit, adj_ok, source)


# ---------------------------------------------------------------------------
# 장중 여부 — 평일 09:00~15:30 + 영업일
# ---------------------------------------------------------------------------
def IsMarketOpen(check_holiday=True):
    """국내 장중이면 True. 평일 09:00~15:30 + (옵션) 영업일 확인. (서버시간 기준)"""
    now = Common.GetNow("KR")
    if now.weekday() >= 5:   # 토(5), 일(6)
        return False
    t = now.hour * 60 + now.minute
    if not (9 * 60 <= t <= 15 * 60 + 30):
        return False
    if check_holiday:
        opened = Common.IsTradingDayByOhlcv("KR")
        if opened is False:
            return False
    return True


# ---------------------------------------------------------------------------
# 주문 공통 헬퍼 — /stock/order
# ---------------------------------------------------------------------------
_ORDER_URL = "/stock/order"
_ACCNO_URL = "/stock/accno"


def _normalize_isuno(stock_code):
    """LS 주문은 종목코드에 'A' 접두가 필요합니다. (이미 있으면 그대로)"""
    code = str(stock_code).strip()
    if code[:1].isalpha():     # 이미 A/J/Q 등 접두가 있으면 유지
        return code
    return "A" + code


def _order_post(url_path, tr_cd, in_block_name, in_block, out_block_names):
    """주문/계좌 API 공통 POST. 응답에서 지정한 out 블록들을 dict로 모아 반환."""
    url = f"{Common.GetUrlBase(Common.GetNowDist())}{url_path}"
    headers = Common.GetCommonHeader(tr_cd=tr_cd)
    body = {in_block_name: in_block}
    try:
        res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
        if res.status_code != 200:
            print(f"[{tr_cd}] HTTP {res.status_code}: {res.text[:150]}")
            return None
        j = res.json()
        result = {"rsp_cd": j.get("rsp_cd", ""), "rsp_msg": j.get("rsp_msg", "")}
        for name in out_block_names:
            result[name] = j.get(name)
        return result
    except Exception as e:
        print(f"[{tr_cd}] 오류: {e}")
        return None


def _do_order(stock_code, amount, price, bns_tp, ord_ptn, mbr="KRX"):
    """
    현물주문(CSPAT00601) 실행 공통.
      bns_tp  : "1"=매도, "2"=매수
      ord_ptn : "00"=지정가, "03"=시장가
    반환: {OrderNum, OrderTime, IsuNm} (실패 시 OrderNum=0)
    """
    in_block = {
        "IsuNo": _normalize_isuno(stock_code),
        "OrdQty": int(abs(amount)),
        "OrdPrc": float(price) if ord_ptn == "00" else 0,
        "BnsTpCode": bns_tp,
        "OrdprcPtnCode": ord_ptn,
        "MgntrnCode": "000",         # 보통(신용 아님)
        "LoanDt": "",
        "OrdCndiTpCode": "0",        # 조건없음
        "MbrNo": mbr,                # KRX
    }
    # 실패 응답에는 거부 사유(rsp_cd/rsp_msg)를 함께 담아 돌려준다.
    # -> 봇이 텔레그램으로 "왜 주문이 안 나갔는지"를 그대로 알릴 수 있다.
    #   (기존 키 OrderNum/OrderTime/IsuNm 은 그대로라 기존 봇 로직에는 영향 없음)
    fail = {"OrderNum": 0, "OrderTime": "", "IsuNm": "",
            "rsp_cd": "", "rsp_msg": "주문 응답 없음"}
    out = _order_post(_ORDER_URL, "CSPAT00601", "CSPAT00601InBlock1", in_block,
                      ["CSPAT00601OutBlock2"])
    if not out:
        return fail
    blk2 = out.get("CSPAT00601OutBlock2") or {}
    ord_no = blk2.get("OrdNo", 0)
    if not ord_no:
        print(f"[KR.order] 주문 실패: {out.get('rsp_msg')}")
        fail["rsp_cd"] = out.get("rsp_cd", "")
        fail["rsp_msg"] = out.get("rsp_msg", "") or "사유 미상"
        return fail
    return {
        "OrderNum": ord_no,
        "OrderTime": blk2.get("OrdTime", ""),
        "IsuNm": blk2.get("IsuNm", ""),
    }


# ---------------------------------------------------------------------------
# 주문 — 지정가 / 시장가 (매수·매도)
# ---------------------------------------------------------------------------
def MakeBuyLimitOrder(stock_code, amount, price):
    """지정가 매수."""
    return _do_order(stock_code, amount, price, bns_tp="2", ord_ptn="00")


def MakeSellLimitOrder(stock_code, amount, price):
    """지정가 매도."""
    return _do_order(stock_code, amount, price, bns_tp="1", ord_ptn="00")


def MakeBuyMarketOrder(stock_code, amount):
    """시장가 매수."""
    return _do_order(stock_code, amount, 0, bns_tp="2", ord_ptn="03")


def MakeSellMarketOrder(stock_code, amount):
    """시장가 매도."""
    return _do_order(stock_code, amount, 0, bns_tp="1", ord_ptn="03")


# ---------------------------------------------------------------------------
# 정정 / 취소
# ---------------------------------------------------------------------------
def ModifyOrder(org_order_no, stock_code, amount, price, ord_ptn="00"):
    """
    현물정정주문(CSPAT00701). 원주문번호의 가격/수량을 정정합니다.
    반환: 새 주문번호 (실패 시 0)
    """
    in_block = {
        "OrgOrdNo": int(org_order_no),
        "IsuNo": _normalize_isuno(stock_code),
        "OrdQty": int(abs(amount)),
        "OrdprcPtnCode": ord_ptn,
        "OrdCndiTpCode": "0",
        "OrdPrc": float(price),
    }
    out = _order_post(_ORDER_URL, "CSPAT00701", "CSPAT00701InBlock1", in_block,
                      ["CSPAT00701OutBlock2"])
    if not out:
        return 0
    blk2 = out.get("CSPAT00701OutBlock2") or {}
    return blk2.get("OrdNo", 0) or 0


def CancelOrder(org_order_no, stock_code, amount):
    """
    현물취소주문(CSPAT00801). 원주문번호를 취소합니다.
    반환: True/False
    """
    in_block = {
        "OrgOrdNo": int(org_order_no),
        "IsuNo": _normalize_isuno(stock_code),
        "OrdQty": int(abs(amount)),
    }
    out = _order_post(_ORDER_URL, "CSPAT00801", "CSPAT00801InBlock1", in_block,
                      ["CSPAT00801OutBlock2"])
    if not out:
        return False
    blk2 = out.get("CSPAT00801OutBlock2") or {}
    return bool(blk2.get("OrdNo", 0))


def CancelModifyOrder(org_order_no, stock_code, amount=0, price=0, mode="CANCEL"):
    """KIS 호환용. mode='CANCEL'이면 취소, 'MODIFY'면 정정."""
    if mode.upper() == "MODIFY":
        return ModifyOrder(org_order_no, stock_code, amount, price)
    return CancelOrder(org_order_no, stock_code, amount)


# ---------------------------------------------------------------------------
# 잔고 (t0424)
# ---------------------------------------------------------------------------
def _get_balance_raw():
    """주식잔고2(t0424) 원본 응답을 반환합니다."""
    in_block = {
        "prcgb": "1",      # 평균단가
        "chegb": "0",      # 결제기준잔고
        "dangb": "0",      # 정규장
        "charge": "1",     # 제비용 포함
        "cts_expcode": "",
    }
    return _order_post(_ACCNO_URL, "t0424", "t0424InBlock", in_block,
                       ["t0424OutBlock", "t0424OutBlock1"])


def GetBalance():
    """
    계좌 잔고 요약을 반환합니다. (t0424)
    반환 dict:
      TotalMoney   : 추정 순자산(sunamt)
      RemainMoney  : 추정 D2 예수금(sunamt1)
      StockMoney   : 평가금액(tappamt)
      StockRevenue : 평가손익(tdtsunik)
    """
    fail = {"TotalMoney": 0, "RemainMoney": 0, "StockMoney": 0, "StockRevenue": 0}
    out = _get_balance_raw()
    if not out:
        return fail
    blk = out.get("t0424OutBlock") or {}
    def _n(v):
        try:
            return float(v)
        except Exception:
            return 0.0
    return {
        "TotalMoney": _n(blk.get("sunamt", 0)),      # 추정순자산
        "RemainMoney": _n(blk.get("sunamt1", 0)),    # 추정D2예수금
        "StockMoney": _n(blk.get("tappamt", 0)),     # 평가금액
        "StockRevenue": _n(blk.get("tdtsunik", 0)),  # 평가손익
    }


def GetMyStockList():
    """
    보유 종목 리스트를 반환합니다. (t0424OutBlock1)
    반환: [{StockCode, StockName, StockAmt(보유수량), StockAvgPrice(평단),
            StockNowPrice(현재가), StockEvalMoney(평가금액),
            StockRevenue(평가손익), StockRevenueRate(수익률),
            StockSellAbleAmt(매도가능수량)}, ...]
    """
    out = _get_balance_raw()
    if not out:
        return []
    rows = out.get("t0424OutBlock1") or []
    def _n(v):
        try:
            return float(v)
        except Exception:
            return 0.0
    result = []
    for r in rows:
        qty = _n(r.get("janqty", 0))
        if qty <= 0:
            continue
        result.append({
            "StockCode": r.get("expcode", ""),
            "StockName": r.get("hname", ""),
            "StockAmt": qty,
            "StockAvgPrice": _n(r.get("pamt", 0)),
            "StockNowPrice": _n(r.get("price", 0)),
            "StockEvalMoney": _n(r.get("appamt", 0)),
            "StockRevenue": _n(r.get("dtsunik", 0)),
            "StockRevenueRate": _n(r.get("sunikrt", 0)),
            "StockSellAbleAmt": _n(r.get("mdposqt", 0)),
        })
    return result


# ---------------------------------------------------------------------------
# 미체결 / 주문 리스트 (t0425)
# ---------------------------------------------------------------------------
def GetOrderList(stock_code="", che_gb="2"):
    """
    주문 체결/미체결 내역. (t0425)
      che_gb : "0"=전체, "1"=체결, "2"=미체결(기본)
    반환: [{OrderNum, StockCode, OrderSide, OrderAmt, OrderPrice,
            ExecAmt, RemainAmt, OrgOrderNum, Status}, ...]
    """
    in_block = {
        "expcode": stock_code,
        "chegb": che_gb,
        "medosu": "0",       # 전체(매도+매수)
        "sortgb": "2",       # 주문번호 순
        "cts_ordno": " ",
    }
    out = _order_post(_ACCNO_URL, "t0425", "t0425InBlock", in_block, ["t0425OutBlock1"])
    if not out:
        return []
    rows = out.get("t0425OutBlock1") or []
    def _n(v):
        try:
            return float(v)
        except Exception:
            return 0.0
    result = []
    for r in rows:
        result.append({
            "OrderNum": r.get("ordno", 0),
            "StockCode": r.get("expcode", ""),
            "OrderSide": r.get("medosu", ""),         # 매수/매도(한글)
            "OrderAmt": _n(r.get("qty", 0)),
            "OrderPrice": _n(r.get("price", 0)),
            "ExecAmt": _n(r.get("cheqty", 0)),        # 체결수량
            "RemainAmt": _n(r.get("ordrem", 0)),      # 미체결잔량
            "OrgOrderNum": r.get("orgordno", 0),
            "Status": r.get("status", ""),
        })
    return result

def GetOrderExecInfo(stock_code, order_num, market=None):
    """
    특정 주문번호(order_num)의 '실제 체결' 정보를 조회한다. (CSPAQ13700)

    해외판 GetOrderExecInfo 와 동일한 인터페이스다.
      market : 국내는 사용하지 않는다(해외판과 시그니처를 맞추기 위한 자리).

    반환:
      {"price": 체결단가(float), "qty": 체결수량(int),
       "amt": 체결금액(float), "raw": 원본row(dict)}
      아직 체결이 확인되지 않으면(미체결/조회불가) None.

    ※ CSPAQ13700OutBlock3 의 ExecPrc(체결가) / ExecQty(체결수량) 를 사용한다.
      계정·버전에 따라 필드명이 다를 수 있어 후보 키를 순서대로 시도하고,
      그래도 못 찾으면 '체결금액 ÷ 체결수량' 으로 역산한다.
      첫 체결 확인 시 원본 row 를 [체결가확인] 로그로 남긴다.
    """
    try:
        order_num = int(order_num)
    except Exception:
        return None
    if order_num <= 0:
        return None

    def _f(v):
        try:
            return float(v)
        except Exception:
            return 0.0

    PRICE_KEYS = ["ExecPrc", "ExecPric", "ExecAvrPrc", "AvrPrc"]
    QTY_KEYS = ["ExecQty", "AllExecQty"]
    AMT_KEYS = ["ExecAmt", "BuyExecAmt", "SellExecAmt"]   # 역산용

    in_block = {
        "OrdMktCode": "00",      # 전체(거래소+코스닥)
        "BnsTpCode": "0",        # 전체(매도+매수)
        "IsuNo": _normalize_isuno(stock_code),
        "ExecYn": "1",           # 1 = 체결분만
        "OrdDt": Common.GetNowDateStr("KR"),
        "SrtOrdNo2": 999999999,  # 역순 조회 시작 주문번호
        "BkseqTpCode": "0",      # 0 = 역순(최근 주문부터)
        "OrdPtnCode": "00",      # 전체
    }
    out = _order_post(_ACCNO_URL, "CSPAQ13700", "CSPAQ13700InBlock1", in_block,
                      ["CSPAQ13700OutBlock3"])
    if not out:
        return None

    rows = out.get("CSPAQ13700OutBlock3") or []
    if isinstance(rows, dict):
        rows = [rows]

    # ★ 2026-08-12 — 부분체결 분할 행 합산(국내)
    #   같은 주문번호라도 체결이 나뉘면 CSPAQ13700 이 행을 여러 개 준다.
    #   예전에는 첫 행에서 바로 return 해서 일부 수량만 기록됐다.
    #   AllExecQty 는 행마다 같은 '주문 누적 체결량' 이라 합산하면 안 된다.
    #   그래서 행이 1개면 기존 로직 그대로, 2개 이상이면 ExecQty 로만 합산한다.
    _matched = []
    for r in rows:
        try:
            if int(r.get("OrdNo", 0) or 0) != order_num:
                continue
        except Exception:
            continue
        _matched.append(r)

    def _row_price(r, qty):
        price = 0.0
        for k in PRICE_KEYS:
            if r.get(k) not in (None, ""):
                price = _f(r.get(k))
                if price > 0:
                    break
        # 단가를 못 찾으면 체결금액 ÷ 체결수량으로 역산
        if price <= 0:
            amt = 0.0
            for k in AMT_KEYS:
                if r.get(k) not in (None, ""):
                    amt = _f(r.get(k))
                    if amt > 0:
                        break
            if amt > 0 and qty > 0:
                price = amt / qty
        return price

    if len(_matched) == 1:
        r = _matched[0]
        qty = 0
        for k in QTY_KEYS:
            if r.get(k) not in (None, ""):
                qty = int(_f(r.get(k)))
                if qty > 0:
                    break
        if qty <= 0:
            return None                    # 아직 체결 안 됨
        price = _row_price(r, qty)
        print(f"[체결가확인] {stock_code} 주문:{order_num} -> "
              f"단가:{price} 수량:{qty} | raw={r}")
        if price > 0:
            return {"price": round(price, 4), "qty": qty,
                    "amt": round(price * qty, 2), "raw": r}
        return None

    if len(_matched) > 1:
        합_수량 = 0
        합_금액 = 0.0
        마지막행 = None
        누적_체결 = 0
        for r in _matched:
            qty = 0
            if r.get("ExecQty") not in (None, ""):
                qty = int(_f(r.get("ExecQty")))
            if qty <= 0:
                continue
            price = _row_price(r, qty)
            if price <= 0:
                continue
            합_수량 += qty
            합_금액 += price * qty
            마지막행 = r
            if r.get("AllExecQty") not in (None, ""):
                누적_체결 = max(누적_체결, int(_f(r.get("AllExecQty"))))
        if 합_수량 > 0:
            _avg = 합_금액 / 합_수량
            print(f"[체결가확인] {stock_code} 주문:{order_num} -> "
                  f"단가:{round(_avg, 4)} 수량:{합_수량} "
                  f"(행합산 {len(_matched)}행 누적체결:{누적_체결}) | raw={마지막행}")
            if 누적_체결 > 합_수량:
                print(f"[체결 합산 경고] {stock_code} 주문:{order_num} "
                      f"합산 {합_수량}주 < 누적체결 {누적_체결}주 — 조회분이 부족합니다")
            return {"price": round(_avg, 4), "qty": 합_수량,
                    "amt": round(합_금액, 2), "raw": 마지막행}
        return None
    return None


# ---------------------------------------------------------------------------
# 전체 취소 / 전량 매도
# ---------------------------------------------------------------------------
def CancelAllOrders(stock_code=""):
    """미체결 주문을 모두 취소합니다. 취소 건수를 반환."""
    orders = GetOrderList(stock_code, che_gb="2")
    cnt = 0
    for o in orders:
        remain = o["RemainAmt"]
        if remain > 0 and CancelOrder(o["OrderNum"], o["StockCode"], remain):
            cnt += 1
    return cnt


def SellAllStock(stock_code):
    """특정 종목을 시장가로 전량 매도합니다."""
    for s in GetMyStockList():
        if s["StockCode"] == stock_code or s["StockCode"] == str(stock_code).lstrip("A"):
            qty = s["StockSellAbleAmt"] or s["StockAmt"]
            if qty > 0:
                return MakeSellMarketOrder(stock_code, qty)
    print(f"[KR.SellAllStock] 보유 수량 없음: {stock_code}")
    return {"OrderNum": 0, "OrderTime": "", "IsuNm": ""}


# ---------------------------------------------------------------------------
# 매수가능 조회 / 보정 (CSPAQ12200)
# ---------------------------------------------------------------------------
def CheckPossibleBuyInfo(stock_code=None, price=0, use_credit=False):
    """
    현금 주문가능금액 등 계좌 예수금 정보를 조회합니다. (CSPAQ12200)
    반환 dict:
      Money     : 현금주문가능금액(MnyOrdAbleAmt)
      CashMoney : 예수금(Dps)
      D2        : D2예수금(D2Dps)
      Amount    : price>0이면 살 수 있는 수량(금액/단가), 아니면 0
    """
    fail = {"Money": 0, "CashMoney": 0, "D2": 0, "Amount": 0}
    out = _order_post(_ACCNO_URL, "CSPAQ12200", "CSPAQ12200InBlock1",
                      {"BalCreTp": "0"}, ["CSPAQ12200OutBlock2"])
    if not out:
        return fail
    blk = out.get("CSPAQ12200OutBlock2") or {}
    def _n(v):
        try:
            return float(v)
        except Exception:
            return 0.0
    money = _n(blk.get("MnyOrdAbleAmt", 0))
    amount = 0
    if price and float(price) > 0:
        amount = int(money // float(price))
    return {
        "Money": money,
        "CashMoney": _n(blk.get("Dps", 0)),
        "D2": _n(blk.get("D2Dps", 0)),
        "Amount": amount,
    }


def AdjustPossibleAmt(stock_code, want_amt, price, use_credit=False):
    """원하는 매수수량이 가능수량을 넘지 않게 보정합니다."""
    info = CheckPossibleBuyInfo(stock_code, price, use_credit)
    possible = info["Amount"]
    want_amt = int(want_amt)
    return min(want_amt, possible) if possible > 0 else 0


def GetMarketOrderPrice(stock_code):
    """시장가 주문 참고용 현재가를 반환합니다. (간이)"""
    return GetCurrentPrice(stock_code)
