# -*- coding: utf-8 -*-
"""
=============================================================================
LS_Common.py  —  LS증권 OpenAPI 공통 모듈 (자체 구현본)

LS증권 OpenAPI 공식 명세(엔드포인트/파라미터/응답 필드)를 참고하여
직접 작성한 모듈입니다. 외부 모듈의 코드를 복제하지 않았습니다.

[현재 단계] 1단계 — 인증 / 토큰 / 모드
  · 설정 파일(./myLS_StockInfo.yaml)에서 앱키·시크릿·URL 등을 읽습니다.
  · REAL(실계좌) / VIRTUAL(모의계좌) 모드 전환을 지원합니다.
    (LS는 실계좌 위주이며, VIRTUAL 설정이 없으면 REAL만 사용)
  · 접근토큰을 발급·저장·재사용하며, 만료 시 자동 재발급합니다.

[설정 파일 예시 — myLS_StockInfo.yaml]
  REAL_APP_KEY: "..."
  REAL_APP_SECRET: "..."
  REAL_TOKEN_PATH: "./ls_real_token.json"
  REAL_URL: "https://openapi.ls-sec.co.kr:8080"

[공식 명세 요약 — 토큰 발급]
  · POST /oauth2/token
      Content-Type: application/x-www-form-urlencoded   ★ KIS와 다름
      파라미터(폼): grant_type=client_credentials,
                    appkey, appsecretkey, scope=oob       ★ appsecret'key', scope 필요
      응답: access_token, expires_in(초), token_type(Bearer), scope
  · 토큰 유효시간 약 24시간(86400초)
  · 도메인: https://openapi.ls-sec.co.kr:8080
=============================================================================
"""

import os
import json
import base64
from datetime import datetime, timedelta, timezone

import yaml
import requests


# ---------------------------------------------------------------------------
# 시각 기준 (KST 고정)
#   운영 서버가 UTC로 도는 경우가 있어 naive datetime.now()를 쓰면
#   토큰의 "발급일/만료일" 경계가 09:00 KST로 밀린다.
#   토큰 관련 시각은 전부 KST로 고정한다.
# ---------------------------------------------------------------------------
_KST = timezone(timedelta(hours=9))


def _now_kst():
    """KST 기준 현재시각(naive). 파일에 기록/비교하는 모든 시각의 단일 기준."""
    return datetime.now(_KST).replace(tzinfo=None)


def _jwt_exp_kst(token):
    """
    LS 접근토큰(JWT)의 exp 클레임을 KST naive datetime으로 반환. 실패 시 None.

    ★ 왜 필요한가 (2026-08-04 사고)
      LS는 같은 앱키로 토큰을 다시 요청하면 '기존 토큰을 그대로' 돌려준다.
      그리고 응답의 expires_in은 '남은 시간'이 아니라 토큰의 총 수명(exp-iat)이다.
      따라서 expire = now + expires_in 으로 계산하면, 수명 중반에 재요청한 토큰의
      만료를 실제보다 최대 22시간까지 늦게 잡는다.

      실제 사고 데이터:
        iat 2026-08-03 09:00:04 KST / exp 2026-08-04 06:59:59 KST (수명 79195초)
        06:50 KST에 재요청 → 같은 토큰 반환, expires_in=79195
        코드가 저장한 만료 2026-08-05 04:49 KST  (21.8시간 과대평가)
        → 07:00에 토큰이 죽었는데 "살아있다"고 믿어 재발급하지 않고,
          g3101이 HTTP 500만 반복하며 봇이 복구 불능에 빠졌다.

      토큰 자체가 만료시각을 들고 있으므로, 저장값보다 exp를 신뢰한다.
    """
    try:
        payload = str(token).split(".")[1]
        payload += "=" * (-len(payload) % 4)          # base64url 패딩 보정
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        if not exp:
            return None
        return datetime.fromtimestamp(int(exp), _KST).replace(tzinfo=None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 설정 파일 로딩
# ---------------------------------------------------------------------------
_SETTING_FILE = "./myLS_StockInfo.yaml"

stock_info = None
try:
    with open(_SETTING_FILE, encoding="UTF-8") as f:
        stock_info = yaml.safe_load(f)
except FileNotFoundError:
    stock_info = None


# ---------------------------------------------------------------------------
# 모드(실계좌 / 모의계좌) 상태
# ---------------------------------------------------------------------------
NOW_DIST = ""
_TOKEN_CACHE = {}   # { "REAL": {"token": "...", "expire": datetime}, ... }


def _require_setting():
    """설정 파일이 정상 로딩됐는지 확인합니다."""
    if stock_info is None:
        raise RuntimeError(
            f"설정 파일을 찾을 수 없습니다: {_SETTING_FILE}\n"
            f"앱키·앱시크릿·URL 등을 담은 yaml 파일을 같은 폴더에 두세요."
        )


def _setting(key):
    """설정값 하나를 안전하게 읽습니다."""
    _require_setting()
    if key not in stock_info:
        raise KeyError(f"설정 파일에 '{key}' 항목이 없습니다. ({_SETTING_FILE} 확인)")
    return stock_info[key]


# ---------------------------------------------------------------------------
# [모드] 현재 계좌 모드 설정 / 조회
# ---------------------------------------------------------------------------
def SetChangeMode(dist="REAL"):
    """
    계좌 모드를 설정합니다. (REAL=실계좌, VIRTUAL=모의계좌)
    설정과 동시에 토큰 상태를 점검해 필요하면 새로 발급해 둡니다.
    """
    global NOW_DIST
    NOW_DIST = dist
    try:
        CheckToken(dist)
    except Exception as e:
        print(f"[SetChangeMode] 토큰 사전 점검 경고: {e}")


def GetNowDist():
    """현재 설정된 모드(REAL / VIRTUAL)를 반환합니다."""
    return NOW_DIST


# ---------------------------------------------------------------------------
# [설정 조회] 앱키 / 앱시크릿 / URL / 토큰경로
# ---------------------------------------------------------------------------
def GetAppKey(dist="REAL"):
    """앱키(APP_KEY)를 반환합니다."""
    return _setting(f"{dist}_APP_KEY")


def GetAppSecret(dist="REAL"):
    """앱시크릿(APP_SECRET)을 반환합니다."""
    return _setting(f"{dist}_APP_SECRET")


def GetUrlBase(dist="REAL"):
    """
    모드에 맞는 기본 API URL을 반환합니다.
    설정 파일에 URL이 있으면 그것을 쓰고, 없으면 LS 공식 기본값을 씁니다.
    """
    key = f"{dist}_URL"
    if stock_info is not None and key in stock_info and stock_info[key]:
        return stock_info[key]
    # LS는 실전/모의 도메인 구분이 없는 단일 도메인 체계
    return "https://openapi.ls-sec.co.kr:8080"


def GetTokenPath(dist="REAL"):
    """토큰을 저장할 파일 경로를 반환합니다."""
    key = f"{dist}_TOKEN_PATH"
    if stock_info is not None and key in stock_info and stock_info[key]:
        return stock_info[key]
    return f"./ls_{dist.lower()}_token.json"


# ---------------------------------------------------------------------------
# [계좌] LS는 계좌번호가 필요 없습니다 (토큰에 계좌가 매핑됨).
# KIS와 인터페이스를 맞추기 위한 호환용 함수 — 빈 값을 반환합니다.
# ---------------------------------------------------------------------------
def GetAccountNo(dist="REAL"):
    """LS는 계좌번호 입력이 불필요. 호환용으로 설정값이 있으면 반환, 없으면 빈 문자열."""
    key = f"{dist}_CANO"
    if stock_info is not None and key in stock_info and stock_info[key]:
        return stock_info[key]
    return ""


def GetPrdtNo(dist="REAL"):
    """LS는 상품코드 입력이 불필요. 호환용으로 설정값이 있으면 반환, 없으면 빈 문자열."""
    key = f"{dist}_ACNT_PRDT_CD"
    if stock_info is not None and key in stock_info and stock_info[key]:
        return stock_info[key]
    return ""


# ---------------------------------------------------------------------------
# [토큰] 발급 / 저장 / 조회 / 점검
# ---------------------------------------------------------------------------
def _save_token_file(dist, token, expire_dt):
    """발급받은 토큰과 만료시각을 파일로 저장합니다."""
    data = {
        "authorization": token,
        "expire_date": expire_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "issued_date": _now_kst().strftime("%Y-%m-%d %H:%M:%S"),
        "dist": dist,
    }
    with open(GetTokenPath(dist), "w", encoding="UTF-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_token_file(dist):
    """저장된 토큰 파일을 읽어 dict로 반환합니다. 없거나 깨졌으면 None."""
    path = GetTokenPath(dist)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="UTF-8") as f:
            return json.load(f)
    except Exception:
        return None


def _is_token_alive(token_data):
    """토큰 데이터의 만료시각을 보고 아직 유효한지 판단합니다."""
    if not token_data or "authorization" not in token_data:
        return False
    # ★ 저장된 expire_date보다 토큰(JWT) 자체의 exp를 우선 신뢰한다.
    #   저장값은 now+expires_in으로 계산돼 실제보다 최대 22시간 늦을 수 있다.
    #   (사유는 _jwt_exp_kst 주석 참고)
    expire_dt = _jwt_exp_kst(token_data["authorization"])
    if expire_dt is None:
        expire_str = token_data.get("expire_date")
        if not expire_str:
            return False
        try:
            expire_dt = datetime.strptime(expire_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return False

    # 만료 10분 전부터는 미리 만료된 것으로 보고 재발급을 유도합니다.
    return _now_kst() < (expire_dt - timedelta(minutes=10))


def MakeToken(dist="REAL"):
    """
    접근토큰을 새로 발급받아 파일과 메모리에 저장한 뒤 토큰 문자열을 반환합니다.

    공식 명세 (LS)
      POST {URL}/oauth2/token
      헤더 Content-Type: application/x-www-form-urlencoded
      폼   grant_type=client_credentials, appkey, appsecretkey, scope=oob
      응답 access_token, expires_in(초), token_type(Bearer)
    """
    url = f"{GetUrlBase(dist)}/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    # LS는 폼 방식이며, 앱시크릿 파라미터명이 appsecretkey 임에 주의
    form = {
        "grant_type": "client_credentials",
        "appkey": GetAppKey(dist),
        "appsecretkey": GetAppSecret(dist),
        "scope": "oob",
    }

    res = requests.post(url, headers=headers, data=form, timeout=10)

    if res.status_code != 200:
        print(f"[MakeToken] 토큰 발급 실패 (HTTP {res.status_code}): {res.text}")
        raise RuntimeError("접근토큰 발급에 실패했습니다.")

    result = res.json()
    token = result.get("access_token")
    if not token:
        print(f"[MakeToken] 응답에 access_token이 없습니다: {result}")
        raise RuntimeError("접근토큰 응답이 올바르지 않습니다.")

    # 만료시각: expires_in(초) 기준으로 계산 (LS는 만료시각 문자열을 안 줌)
    # ★ 만료시각은 토큰(JWT)의 exp를 사용한다.
    #   expires_in은 '남은 시간'이 아니라 총 수명(exp-iat)이라, 같은 앱키로
    #   재요청해 기존 토큰을 돌려받은 경우 now+expires_in은 크게 빗나간다.
    #   exp를 못 읽을 때만 근사치(now+expires_in)로 대체한다.
    expires_in = int(result.get("expires_in", 86400))   # 기본 24시간
    issued_dt = _now_kst()
    expire_dt = _jwt_exp_kst(token)
    if expire_dt is None:
        expire_dt = issued_dt + timedelta(seconds=expires_in)
        print("[MakeToken] ⚠️ JWT exp를 읽지 못해 근사치로 만료를 계산합니다.")

    _save_token_file(dist, token, expire_dt)
    _TOKEN_CACHE[dist] = {"token": token, "expire": expire_dt, "issued": issued_dt}

    print(f"[MakeToken] 새 토큰 발급 완료 (만료: {expire_dt:%Y-%m-%d %H:%M:%S})")
    return token


def GetToken(dist="REAL"):
    """
    유효한 접근토큰을 반환합니다.
    우선순위: 메모리 캐시 → 파일 → (둘 다 만료/없음이면) 새로 발급.
    """
    now = _now_kst()
    cached = _TOKEN_CACHE.get(dist)
    if cached and now < (cached["expire"] - timedelta(minutes=10)):
        return cached["token"]

    token_data = _load_token_file(dist)
    if _is_token_alive(token_data):
        tok = token_data["authorization"]
        # 여기서도 저장값이 아니라 JWT exp를 캐시에 넣는다.
        expire_dt = _jwt_exp_kst(tok)
        if expire_dt is None:
            expire_dt = datetime.strptime(token_data["expire_date"], "%Y-%m-%d %H:%M:%S")
        _TOKEN_CACHE[dist] = {"token": tok, "expire": expire_dt, "issued": now}
        return tok

    return MakeToken(dist)


def CheckToken(dist="REAL"):
    """토큰 상태를 점검하고, 유효하지 않으면 재발급합니다."""
    token_data = _load_token_file(dist)
    if _is_token_alive(token_data):
        return token_data["authorization"]
    return MakeToken(dist)


def RevokeToken(dist="REAL"):
    """
    접근토큰을 폐기합니다. (선택 기능)
    공식 명세: POST /oauth2/revoke
      폼 appkey, appsecretkey, token_type_hint=access_token, token
    """
    token = _TOKEN_CACHE.get(dist, {}).get("token")
    if not token:
        td = _load_token_file(dist)
        token = td.get("authorization") if td else None
    if not token:
        return False

    url = f"{GetUrlBase(dist)}/oauth2/revoke"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    form = {
        "appkey": GetAppKey(dist),
        "appsecretkey": GetAppSecret(dist),
        "token_type_hint": "access_token",
        "token": token,
    }
    try:
        res = requests.post(url, headers=headers, data=form, timeout=10)
        ok = res.status_code == 200
        if ok:
            _TOKEN_CACHE.pop(dist, None)
        return ok
    except Exception as e:
        print(f"[RevokeToken] 오류: {e}")
        return False


# ---------------------------------------------------------------------------
# [공통 요청 헤더] 다른 모듈(KR/US 헬퍼)에서 공통으로 쓰는 인증 헤더
# ---------------------------------------------------------------------------
def GetCommonHeader(dist=None, tr_cd="", tr_cont="N", tr_cont_key=""):
    """
    LS API 호출용 공통 헤더를 만들어 줍니다.
    LS는 헤더에 tr_cd(거래코드)·tr_cont(연속조회) 등을 담습니다.
      dist        : 모드(None이면 현재 모드)
      tr_cd       : 거래 코드 (API별로 다름)
      tr_cont     : 연속조회 여부 (N=최초, Y=다음)
      tr_cont_key : 연속조회 키
    """
    if dist is None:
        dist = NOW_DIST if NOW_DIST else "REAL"
    return {
        "Content-Type": "application/json; charset=UTF-8",
        "authorization": f"Bearer {GetToken(dist)}",
        "tr_cd": tr_cd,
        "tr_cont": tr_cont,
        "tr_cont_key": tr_cont_key,
    }


# ===========================================================================
# 2단계 — 날짜 유틸 / 데이터 조회(OHLCV, t8410) / 보조지표
# ===========================================================================
import time
from pytz import timezone

import pandas as pd

try:
    import FinanceDataReader as fdr
    _FDR_OK = True
except Exception:
    _FDR_OK = False


# ---------------------------------------------------------------------------
# [날짜] 현재/상대/기준 날짜 문자열
# ---------------------------------------------------------------------------
def _tz(area):
    """지역에 맞는 시간대. (KR=서울, US=뉴욕)"""
    return timezone("America/New_York") if area == "US" else timezone("Asia/Seoul")


# ---------------------------------------------------------------------------
# [서버시간] LS 서버 시각 조회 (t0167) — PC 시계 오차 방지
# ---------------------------------------------------------------------------
def GetServerTime(area="KR"):
    """
    LS 서버의 현재 시각을 datetime으로 반환합니다. (t0167 서버시간조회)
    공식 명세: POST /etc/time-search, tr_cd t0167
              응답 t0167OutBlock.dt(YYYYMMDD) / time(HHMMSSssssss)
    LS 서버는 한국시간 기준이며, area="US"면 미국 동부시간으로 변환해 반환합니다.
    실패 시 None (호출부가 PC 시계로 대체하도록).
    """
    url = f"{GetUrlBase(GetNowDist())}/etc/time-search"
    headers = GetCommonHeader(tr_cd="t0167")
    body = {"t0167InBlock": {"id": ""}}
    try:
        res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
        if res.status_code != 200:
            return None
        out = res.json().get("t0167OutBlock", {})
        dt = out.get("dt", "")        # YYYYMMDD
        tm = out.get("time", "")      # HHMMSSssssss
        if not dt or len(tm) < 6:
            return None
        # 한국시간으로 파싱 (LS 서버 기준)
        kst = timezone("Asia/Seoul")
        naive = datetime.strptime(dt + tm[:6], "%Y%m%d%H%M%S")
        kst_dt = kst.localize(naive)
        if area == "US":
            return kst_dt.astimezone(timezone("America/New_York"))
        return kst_dt
    except Exception as e:
        print(f"[GetServerTime] 오류: {e}")
        return None


def GetNow(area="KR"):
    """
    현재 시각(datetime)을 반환합니다.
    먼저 LS 서버 시각(t0167)을 시도하고, 실패하면 PC 시계로 대체합니다.
    """
    server = GetServerTime(area)
    if server is not None:
        return server
    return datetime.now(_tz(area))


def GetNowDateStr(area="KR", type="NONE"):
    """현재 날짜 문자열. type="NONE"이면 YYYYMMDD, 그 외엔 YYYY-MM-DD."""
    now = datetime.now(_tz(area))
    return now.strftime("%Y%m%d") if type.upper() == "NONE" else now.strftime("%Y-%m-%d")


def GetFromNowDateStr(area="KR", type="NONE", days=100):
    """현재에서 days만큼 이동(음수=과거)한 날짜 문자열."""
    now = datetime.now(_tz(area))
    target = now - timedelta(days=abs(days)) if days < 0 else now + timedelta(days=days)
    return target.strftime("%Y%m%d") if type.upper() == "NONE" else target.strftime("%Y-%m-%d")


def GetFromDateStr(base_dt, type="NONE", days=100):
    """주어진 datetime에서 days만큼 이동한 날짜 문자열."""
    target = base_dt - timedelta(days=abs(days)) if days < 0 else base_dt + timedelta(days=days)
    return target.strftime("%Y%m%d") if type.upper() == "NONE" else target.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# [데이터 표준화]
# ---------------------------------------------------------------------------
_STD_COLS = ["open", "high", "low", "close", "volume", "value", "change"]


def _standardize(df):
    """OHLCV DataFrame을 표준 컬럼/형식으로 정리합니다."""
    if df is None or len(df) == 0:
        return None
    df = df.copy()
    for c in ["open", "high", "low", "close", "volume"]:
        if c not in df.columns:
            return None
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["close"])
    df = df[df["close"] > 0]
    if len(df) == 0:
        return None
    if "value" not in df.columns:
        df["value"] = ((df["open"] + df["high"] + df["low"] + df["close"]) / 4.0) * df["volume"]
    df["change"] = (df["close"] - df["close"].shift(1)) / df["close"].shift(1)
    df.index = pd.to_datetime(df.index).strftime("%Y-%m-%d")
    df.index.name = "Date"
    df = df.sort_index()
    return df[_STD_COLS]


# ---------------------------------------------------------------------------
# [데이터 소스] LS API 일봉 (t8410) — 연속조회로 과거까지 수집
# ---------------------------------------------------------------------------
def _get_from_ls(stock_code, limit=500, adj_ok="1"):
    """
    LS API(t8410)로 국내 일봉을 limit개수만큼 모아 표준 형식으로 반환합니다.
    1회 최대 약 500건이라, 응답의 cts_date로 과거까지 연속조회합니다.
    adj_ok="1"이면 수정주가(sujung="Y") 적용.
    """
    url = f"{GetUrlBase(GetNowDist())}/stock/chart"
    sujung = "Y" if adj_ok == "1" else "N"

    collected = {}
    cts_date = ""        # 처음엔 공백, 이후 연속조회키
    edate = "99999999"   # 최초 조회 기준일(최신)
    safety = 0

    while len(collected) < limit and safety < 40:
        safety += 1
        body = {
            "t8410InBlock": {
                "shcode": stock_code,
                "gubun": "2",              # 2: 일봉
                "qrycnt": 500,             # 비압축 최대 500
                "sdate": "",
                "edate": edate,
                "cts_date": cts_date,
                "comp_yn": "N",
                "sujung": sujung,
            }
        }
        headers = GetCommonHeader(tr_cd="t8410", tr_cont=("Y" if cts_date else "N"))
        try:
            res = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
        except Exception as e:
            print(f"[_get_from_ls] 요청 오류: {e}")
            break
        if res.status_code != 200:
            print(f"[_get_from_ls] HTTP {res.status_code}: {res.text[:150]}")
            break

        j = res.json()
        rows = j.get("t8410OutBlock1", [])
        if not rows:
            break

        added = 0
        for r in rows:
            d = r.get("date", "")
            c = r.get("close", "")
            if not d or c in ("", 0, "0"):
                continue
            if d not in collected:
                collected[d] = {
                    "open":   float(r.get("open", 0) or 0),
                    "high":   float(r.get("high", 0) or 0),
                    "low":    float(r.get("low", 0) or 0),
                    "close":  float(c),
                    "volume": float(r.get("jdiff_vol", 0) or 0),
                    "value":  float(r.get("value", 0) or 0),
                }
                added += 1

        # 다음 연속조회 키
        next_cts = j.get("t8410OutBlock", {}).get("cts_date", "")
        if added == 0 or not next_cts or next_cts == cts_date:
            break   # 더 과거 데이터 없음
        cts_date = next_cts
        time.sleep(0.2)

    if not collected:
        return None

    df = pd.DataFrame.from_dict(collected, orient="index")
    df.index = pd.to_datetime(df.index, format="%Y%m%d")
    df = df.sort_index()
    return _standardize(df)


def _get_from_fdr(stock_code, limit=500, adj_ok="1"):
    """FinanceDataReader 보조 소스 (국내)."""
    if not _FDR_OK:
        return None
    start = GetFromNowDateStr("KR", "BAR", -int(limit))
    end = GetNowDateStr("KR", "BAR")
    raw = fdr.DataReader(stock_code, start, end)
    if raw is None or len(raw) == 0:
        return None
    close_col = "Adj Close" if (adj_ok == "1" and "Adj Close" in raw.columns) else "Close"
    df = pd.DataFrame({
        "open": raw["Open"], "high": raw["High"], "low": raw["Low"],
        "close": raw[close_col], "volume": raw["Volume"],
    })
    time.sleep(0.2)
    return _standardize(df)


# ---------------------------------------------------------------------------
# [데이터 조회] GetOhlcv — LS 우선, 필요시 외부 소스
# ---------------------------------------------------------------------------
def GetOhlcv(area, stock_code, limit=500, adj_ok="1", source="ls"):
    """
    일봉 OHLCV를 조회해 표준 형식 DataFrame으로 반환합니다.

    source="ls"  : LS API(t8410)만 사용 (백테스트·정확한 수정주가)  ★기본
    source="fdr" : FinanceDataReader만 사용 (빠른 조회)
    source="mix" : FDR 우선 + LS 최신 보충

    LS API는 국내 종목용입니다. (area는 호환을 위해 받지만 KR 기준)
    반환: 최근 limit개 행 (인덱스=날짜문자열, 표준 컬럼)
    """
    df = None
    if source == "fdr":
        df = _get_from_fdr(stock_code, limit, adj_ok)
    elif source == "mix":
        base = _get_from_fdr(stock_code, limit, adj_ok)
        ls_df = _get_from_ls(stock_code, limit, adj_ok)
        if base is not None and ls_df is not None:
            last = base.index[-1]
            add = ls_df[ls_df.index > last]
            df = pd.concat([base, add]) if len(add) else base
        else:
            df = base if base is not None else ls_df
    else:  # "ls" 기본
        df = _get_from_ls(stock_code, limit, adj_ok)

    if df is None or len(df) == 0:
        return pd.DataFrame()
    return df.iloc[-int(limit):]


# ---------------------------------------------------------------------------
# [유틸] 종가 안전 조회 / 종목 포함 여부
# ---------------------------------------------------------------------------
def GetCloseData(df, st):
    """기준 위치 st의 종가를 안전하게 반환. 부족하면 가장 오래된 값."""
    if len(df) < abs(st):
        return df["close"].iloc[-len(df)]
    return df["close"].iloc[st]


def CheckStockCodeInList(stock_code_list, find_code):
    """종목코드 리스트에 find_code가 있으면 True."""
    return find_code in stock_code_list


# ---------------------------------------------------------------------------
# [보조지표] 공개 수식 기반 — KIS 모듈과 동일 (차트와 일치하는 표준 방식)
# ---------------------------------------------------------------------------
def GetMA(ohlcv, period, st):
    """이동평균(MA): 단순이동평균의 st 위치 값."""
    return float(ohlcv["close"].rolling(period).mean().iloc[st])


def GetRSI(ohlcv, period, st):
    """RSI: Wilder 방식(EWM) — 차트와 일치하는 표준."""
    delta = ohlcv["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return float((100 - (100 / (1 + rs))).iloc[st])


def GetBB(ohlcv, period, st, uni=2.0):
    """볼린저밴드: 중심/상단/하단 dict."""
    close = ohlcv["close"]
    end = len(close) + st + 1 if st < 0 else st + 1
    window = close.iloc[max(0, end - period):end]
    center = float(window.mean())
    band = uni * float(window.std(ddof=0))
    return {"ma": center, "upper": center + band, "lower": center - band}


def GetMACD(ohlcv, st):
    """MACD(12,26,9): macd/시그널/오실레이터 dict."""
    close = ohlcv["close"]
    macd = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    signal = macd.ewm(span=9).mean()
    m = float(macd.iloc[st]); s = float(signal.iloc[st])
    return {"macd": m, "macd_siginal": s, "ocl": m - s}


def GetStoch(ohlcv, period, st):
    """스토캐스틱: fast_k / slow_d dict."""
    low_min = ohlcv["low"].rolling(window=period, min_periods=1).min()
    high_max = ohlcv["high"].rolling(window=period, min_periods=1).max()
    fast_k = (ohlcv["close"] - low_min) / (high_max - low_min) * 100
    slow_d = fast_k.rolling(window=3, min_periods=1).mean()
    return {"fast_k": float(fast_k.iloc[st]), "slow_d": float(slow_d.iloc[st])}


def GetIC(ohlcv, st):
    """일목균형표: 전환/기준/선행A/선행B/후행 dict."""
    high = ohlcv["high"]; low = ohlcv["low"]; close = ohlcv["close"]
    conv = (high.rolling(9).max() + low.rolling(9).min()) / 2
    base = (high.rolling(26).max() + low.rolling(26).min()) / 2
    span_a = ((conv + base) / 2).shift(26)
    span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    lagging = close.shift(-26)
    return {
        "conversion": float(conv.iloc[st]),
        "base": float(base.iloc[st]),
        "sunhang_span_a": float(span_a.iloc[-1]),
        "sunhang_span_b": float(span_b.iloc[-1]),
        "huhang_span": float(lagging.iloc[-27]),
    }


# ---------------------------------------------------------------------------
# [영업일 판단] OHLCV 마지막 거래일 기준
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# [영업일 판단] OHLCV 마지막 거래일 기준
# ---------------------------------------------------------------------------
# 영업일 판단용 대표 종목: 시장 대표 ETF를 사용합니다.
#   - 개별 종목(삼성전자 등)은 거래정지·관리종목 가능성이 있으나,
#     시장 대표 ETF는 장이 열리면 거의 항상 거래되어 휴장 판단에 더 안정적입니다.
_PROBE_DEFAULT = {
    "KR": "069500",   # KODEX 200 (코스피200 추종)
    "US": "SPY",      # SPDR S&P 500 ETF
}

# 영업일 판단 결과 캐시: { "KR_20260602": True/False }  (하루 1회만 실제 조회)
_TRADING_DAY_CACHE = {}


def IsTradingDayByOhlcv(area, probe_code=None, use_cache=True):
    """
    공휴일 목록 없이 '오늘이 거래일인가'를 판단합니다.
    시장 대표 ETF(KR=KODEX200, US=SPY) 일봉을 받아,
    마지막 거래일이 '오늘(서버시간 기준)'과 같고 거래량이 있으면 거래일로 봅니다.

    - 마지막 거래일이 오늘이 아니면(어제 등) → 오늘 휴장 (단발 공휴일도 걸러짐)
    - 같은 날은 결과를 캐시해 하루 1회만 실제 조회합니다.
    반환: True(거래일) / False(휴장) / None(판단 불가)
    """
    if probe_code is None:
        probe_code = _PROBE_DEFAULT.get(area, "069500")

    today = GetNow(area).date()                     # 서버시간 우선
    cache_key = f"{area}_{today.strftime('%Y%m%d')}"
    if use_cache and cache_key in _TRADING_DAY_CACHE:
        return _TRADING_DAY_CACHE[cache_key]

    try:
        df = GetOhlcv(area, probe_code, limit=5)
    except Exception as e:
        print(f"[IsTradingDayByOhlcv] 조회 실패: {e}")
        return None
    if df is None or len(df) == 0:
        return None

    try:
        last_dt = datetime.strptime(df.index[-1], "%Y-%m-%d").date()
    except Exception:
        return None

    # 마지막 거래일이 '오늘'과 같고 거래량이 있어야 거래일
    last_vol = 0
    try:
        last_vol = float(df["volume"].iloc[-1])
    except Exception:
        last_vol = 0
    result = (last_dt == today) and (last_vol > 0)

    if use_cache:
        _TRADING_DAY_CACHE[cache_key] = result
    return result


def IsOpenDayKR(date_str=None, probe_code=None):
    """
    국내 개장일 여부. (LS는 별도 휴장일 API를 쓰지 않고 OHLCV 기반으로 판단)
    대표 종목 미지정 시 KODEX 200(069500)을 사용합니다.
    오늘 기준이면 IsTradingDayByOhlcv로 판단합니다.
    반환: True(개장) / False(휴장) / None(판단 불가)
    """
    if probe_code is None:
        probe_code = _PROBE_DEFAULT["KR"]
    today_str = GetNow("KR").strftime("%Y%m%d")
    if date_str is None or date_str == today_str:
        return IsTradingDayByOhlcv("KR", probe_code)
    # 과거 특정일은 그 날짜가 일봉에 존재하는지로 판단
    try:
        df = GetOhlcv("KR", probe_code, limit=500)
        if df is None or len(df) == 0:
            return None
        target = datetime.strptime(date_str, "%Y%m%d").strftime("%Y-%m-%d")
        return target in set(df.index)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# [해시키] LS는 별도 해시키가 필요 없습니다 (KIS 호환용 더미).
# ---------------------------------------------------------------------------
def GetHashKey(body_dict):
    """LS는 주문 시 해시키가 불필요. 호환용으로 빈 문자열을 반환합니다."""
    return ""


# ---------------------------------------------------------------------------
# [통합 잔고] 원화 기준 총 평가 (국내 헬퍼 잔고를 합산)
# ---------------------------------------------------------------------------
def GetBalanceKrwTotal():
    """
    원화 기준 계좌 총자산을 반환합니다.
    국내 잔고(t0424)의 추정순자산을 사용합니다.
    반환: float (실패 시 0)
    """
    try:
        import LS_API_Helper_KR_new as LsiKR
        bal = LsiKR.GetBalance()
        return float(bal.get("TotalMoney", 0))
    except Exception as e:
        print(f"[GetBalanceKrwTotal] 오류: {e}")
        return 0


# ---------------------------------------------------------------------------
# 단독 실행 시: 토큰 발급 자가 점검 (실제 키가 설정돼 있어야 동작)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("LS_Common 1단계(인증/토큰/모드) 자가 점검")
    print("-" * 50)
    try:
        SetChangeMode("REAL")
        print("현재 모드 :", GetNowDist())
        print("기본 URL  :", GetUrlBase("REAL"))
        print("토큰 경로 :", GetTokenPath("REAL"))
        tok = GetToken("REAL")
        print("토큰 앞부분:", str(tok)[:20], "...")
        print("✅ 인증/토큰 정상 동작")
    except Exception as e:
        print("점검 중 오류:", e)
        print("→ 실제 앱키/시크릿이 myLS_StockInfo.yaml 에 있어야 토큰 발급이 됩니다.")
