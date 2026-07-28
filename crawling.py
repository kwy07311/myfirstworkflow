"""
코스피 + 코스닥 상장 전종목 리스트를 받아서
 - 스팩(SPAC)
 - 관리종목 (KRX 공식 관리종목 지정 현황 기준)
 - 거래정지 추정 종목 (당일 거래량 0)
을 제외하고 엑셀 파일로 저장하는 스크립트.

필요 패키지 (requirements.txt):
    pandas
    finance-datareader
    openpyxl

실행:
    python get_krx_stock_list.py

결과물:
    output/krx_stock_list_YYYYMMDD.xlsx
        - 시트 "전체종목": 필터링 후 최종 리스트
        - 시트 "제외_스팩": 스팩으로 판단해 제외한 종목
        - 시트 "제외_관리종목": KRX 관리종목 지정 현황 기준으로 제외한 종목
        - 시트 "제외_거래정지추정": 당일 거래량 0 등으로 거래정지 추정, 제외한 종목
          (공식 '거래정지' 플래그가 아닌 근사치이므로 반드시 육안 확인 권장)
"""

import os
import sys
import datetime as dt

import pandas as pd
import FinanceDataReader as fdr


SPAC_KEYWORDS = ["스팩", "기업인수목적"]


def is_spac(name: str) -> bool:
    return any(kw in str(name) for kw in SPAC_KEYWORDS)


def main():
    # ------------------------------------------------------------------
    # 1) 코스피+코스닥(+코넥스) 전종목 기본/시세 정보
    #    Code, Name, Market, Dept(소속부), Volume(당일 거래량) 등을 포함
    # ------------------------------------------------------------------
    print("1) KRX 전종목 리스트 수집 중...")
    df = fdr.StockListing("KRX")
    print("   -> 컬럼:", list(df.columns))

    if "Code" not in df.columns or "Market" not in df.columns:
        print("[오류] 예상한 컬럼이 없습니다. FinanceDataReader 응답 구조가 바뀐 것 같습니다.")
        sys.exit(1)

    df["Code"] = df["Code"].astype(str).str.zfill(6)

    # 코넥스(KONEX) 제외 -> 코스피 + 코스닥만 남김
    df = df[~df["Market"].astype(str).str.contains("코넥스|KONEX", na=False)].copy()

    # ---- 스팩 제외 ----
    spac_mask = df["Name"].apply(is_spac)
    excluded_spac = df[spac_mask].copy()
    df = df[~spac_mask].copy()

    # ------------------------------------------------------------------
    # 2) KRX 공식 관리종목 지정 현황으로 제외
    # ------------------------------------------------------------------
    print("2) KRX 관리종목 지정 현황 수집 중...")
    try:
        df_admin = fdr.StockListing("KRX-ADMINISTRATIVE")
        admin_codes = set(df_admin["Symbol"].astype(str).str.zfill(6))
    except Exception as e:
        print(f"[경고] 관리종목 리스트 조회 실패: {e}")
        admin_codes = set()

    admin_mask = df["Code"].isin(admin_codes)
    excluded_admin = df[admin_mask].copy()
    df = df[~admin_mask].copy()

    # ------------------------------------------------------------------
    # 3) 거래정지 추정 (당일 거래량 0) 종목 제외
    #    ※ 공식 '거래정지' 플래그가 아닌 근사치입니다.
    # ------------------------------------------------------------------
    if "Volume" in df.columns:
        halt_mask = df["Volume"].fillna(0) == 0
    else:
        print("[경고] 'Volume' 컬럼이 없어 거래정지 추정 필터를 건너뜁니다.")
        halt_mask = pd.Series(False, index=df.index)

    excluded_halt = df[halt_mask].copy()
    df = df[~halt_mask].copy()

    df = df.sort_values(["Market", "Code"]).reset_index(drop=True)

    keep_cols = [c for c in ["Code", "Name", "Market", "Dept", "Close", "Volume"] if c in df.columns]
    df_final = df[keep_cols]

    print(
        f"3) 최종 종목 수: {len(df_final)}개 "
        f"(스팩 제외 {len(excluded_spac)}개, 관리종목 제외 {len(excluded_admin)}개, "
        f"거래정지추정 제외 {len(excluded_halt)}개)"
    )

    # ------------------------------------------------------------------
    # 4) 엑셀로 저장 (csv 대신 xlsx로 저장해서 한글 깨짐 방지)
    # ------------------------------------------------------------------
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
