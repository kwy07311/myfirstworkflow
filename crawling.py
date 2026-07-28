"""
코스피 + 코스닥 상장 전종목 리스트를 받아서
 - 스팩(SPAC)
 - 관리종목
 - 거래정지 종목
을 제외하고 엑셀 파일로 저장하는 스크립트.

GitHub Actions 등 서버 환경에서 실행하는 것을 전제로 작성했습니다.

필요 패키지:
    pip install pykrx pandas openpyxl requests

실행:
    python get_krx_stock_list.py

결과물:
    output/krx_stock_list_YYYYMMDD.xlsx
        - 시트 "전체종목": 필터링 후 최종 리스트
        - 시트 "제외_스팩": 스팩으로 판단해 제외한 종목
        - 시트 "제외_관리종목": 관리종목으로 판단해 제외한 종목
        - 시트 "제외_거래정지추정": 거래량 0 등으로 거래정지 추정, 제외한 종목
          (100% 확정 플래그가 아니므로 반드시 육안 확인 권장)
"""

import io
import os
import sys
import time
import datetime as dt

import pandas as pd
import requests


# ----------------------------------------------------------------------------
# 1. KRX 정보데이터시스템(data.krx.co.kr)에서 "전종목 기본정보"를 받아온다.
#    - 시장구분(KOSPI/KOSDAQ), 소속부(관리종목 여부 포함), 증권구분(보통주 등)이
#      함께 내려온다.
# ----------------------------------------------------------------------------
KRX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201020101",
}

GEN_OTP_URL = "http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd"
DOWNLOAD_URL = "http://data.krx.co.kr/comm/fileDn/download_csv/download.cmd"


def fetch_krx_basic_info() -> pd.DataFrame:
    """KRX 전종목 기본정보(코스피+코스닥 전체)를 DataFrame으로 반환."""
    otp_params = {
        "mktId": "ALL",
        "share": "1",
        "csvxls_isNo": "false",
        "name": "fileDown",
        "url": "dbms/MDC/STAT/standard/MDCSTAT01901",
    }

    r = requests.get(GEN_OTP_URL, params=otp_params, headers=KRX_HEADERS, timeout=10)
    r.raise_for_status()
    otp = r.content

    r = requests.post(DOWNLOAD_URL, data={"code": otp}, headers=KRX_HEADERS, timeout=10)
    r.raise_for_status()

    df = pd.read_csv(io.BytesIO(r.content), encoding="cp949")
    return df


def fetch_latest_trading_day() -> str:
    """pykrx를 이용해 가장 최근 영업일(YYYYMMDD)을 구한다."""
    from pykrx import stock

    today = dt.datetime.now().strftime("%Y%m%d")
    # 영업일 계산 도우미가 없는 구버전 pykrx 호환을 위해 최근 10일 중
    # 실제로 데이터가 있는 날짜를 역순으로 탐색한다.
    for i in range(10):
        d = (dt.datetime.now() - dt.timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = stock.get_market_ohlcv(d, market="KOSPI")
            if df is not None and len(df) > 0:
                return d
        except Exception:
            continue
    return today


def fetch_zero_volume_tickers(trd_dd: str) -> set:
    """
    최근 영업일 기준 거래량이 0인 종목(코스피+코스닥)을 거래정지 후보로 추정.
    ※ 완전히 정확한 '거래정지' 공식 플래그는 아니며, 최선의 근사치입니다.
    """
    from pykrx import stock

    zero_volume = set()
    for market in ("KOSPI", "KOSDAQ"):
        try:
            df = stock.get_market_ohlcv(trd_dd, market=market)
        except Exception as e:
            print(f"[경고] {market} 시세 조회 실패: {e}")
            continue
        if df is None or len(df) == 0:
            continue
        halted = df[df["거래량"] == 0]
        zero_volume.update(halted.index.tolist())
        time.sleep(0.5)  # KRX 서버 과호출 방지
    return zero_volume


# ----------------------------------------------------------------------------
# 2. 필터링
# ----------------------------------------------------------------------------
SPAC_KEYWORDS = ["스팩", "기업인수목적"]


def is_spac(name: str) -> bool:
    return any(kw in str(name) for kw in SPAC_KEYWORDS)


def main():
    print("1) KRX 전종목 기본정보 수집 중...")
    raw = fetch_krx_basic_info()

    # 컬럼명이 KRX 사이트 개편에 따라 바뀔 수 있으므로 방어적으로 처리
    raw.columns = [c.strip() for c in raw.columns]
    print("   -> 원본 컬럼:", list(raw.columns))

    col_map = {
        "단축코드": "종목코드",
        "한글 종목약명": "종목명",
        "한글종목약명": "종목명",
        "시장구분": "시장구분",
        "소속부": "소속부",
        "증권구분": "증권구분",
    }
    raw = raw.rename(columns={k: v for k, v in col_map.items() if k in raw.columns})

    required = ["종목코드", "종목명", "시장구분"]
    missing = [c for c in required if c not in raw.columns]
    if missing:
        print(f"[오류] 필수 컬럼이 없습니다: {missing}. KRX 응답 구조가 바뀐 것 같습니다.")
        sys.exit(1)

    df = raw[raw["시장구분"].isin(["KOSPI", "KOSDAQ"])].copy()
    df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)

    # 보통주만 남기기 (증권구분 컬럼이 있는 경우에만 적용)
    if "증권구분" in df.columns:
        df = df[df["증권구분"].astype(str).str.contains("보통주", na=False)]

    # ---- 스팩 제외 ----
    spac_mask = df["종목명"].apply(is_spac)
    excluded_spac = df[spac_mask].copy()
    df = df[~spac_mask].copy()

    # ---- 관리종목 제외 (소속부 컬럼 기준) ----
    if "소속부" in df.columns:
        admin_mask = df["소속부"].astype(str).str.contains("관리종목", na=False)
    else:
        print("[경고] '소속부' 컬럼이 없어 관리종목 필터를 건너뜁니다.")
        admin_mask = pd.Series(False, index=df.index)
    excluded_admin = df[admin_mask].copy()
    df = df[~admin_mask].copy()

    # ---- 거래정지 추정 종목 제외 ----
    print("2) 최근 영업일 조회 및 거래정지(추정) 종목 확인 중...")
    trd_dd = fetch_latest_trading_day()
    print(f"   -> 기준일: {trd_dd}")
    halted_tickers = fetch_zero_volume_tickers(trd_dd)

    halt_mask = df["종목코드"].isin(halted_tickers)
    excluded_halt = df[halt_mask].copy()
    df = df[~halt_mask].copy()

    df = df.sort_values(["시장구분", "종목코드"]).reset_index(drop=True)

    keep_cols = [c for c in ["종목코드", "종목명", "시장구분", "소속부", "증권구분"] if c in df.columns]
    df_final = df[keep_cols]

    print(f"3) 최종 종목 수: {len(df_final)}개 "
          f"(스팩 제외 {len(excluded_spac)}개, 관리종목 제외 {len(excluded_admin)}개, "
          f"거래정지추정 제외 {len(excluded_halt)}개)")

    # ---- 엑셀로 저장 (csv 대신 xlsx로 저장해서 한글 깨짐 방지) ----
    os.makedirs("output", exist_ok=True)
    today_str = dt.datetime.now().strftime("%Y%m%d")
    out_path = f"output/krx_stock_list_{today_str}.xlsx"

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        df_final.to_excel(writer, sheet_name="전체종목", index=False)
        excluded_spac[keep_cols].to_excel(writer, sheet_name="제외_스팩", index=False)
        excluded_admin[keep_cols].to_excel(writer, sheet_name="제외_관리종목", index=False)
        excluded_halt[keep_cols].to_excel(writer, sheet_name="제외_거래정지추정", index=False)

    print(f"완료: {out_path}")


if __name__ == "__main__":
    main()
