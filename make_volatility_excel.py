import os
import time
import requests
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed


# =========================
# 환경 변수
# =========================

APP_KEY = os.environ["KIS_APP_KEY"]
APP_SECRET = os.environ["KIS_APP_SECRET"]

BASE_URL = "https://openapi.koreainvestment.com:9443"

TOKEN = None


# =========================
# KIS TOKEN
# =========================

def get_token():
    global TOKEN

    if TOKEN:
        return TOKEN

    url = f"{BASE_URL}/oauth2/tokenP"

    headers = {
        "content-type": "application/json; charset=utf-8"
    }

    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }

    response = requests.post(
        url,
        headers=headers,
        json=body
    )

    print("TOKEN RESPONSE:")
    print(response.text)

    response.raise_for_status()

    TOKEN = response.json()["access_token"]

    return TOKEN


# =========================
# 날짜 설정
# =========================

TARGET_DATES = [
    "20260715",
    "20260716",
    "20260720",
    "20260721",
    "20260722",
    "20260723",
    "20260724",
    "20260727",
    "20260728",
    "20260729",
]


# =========================
# 종목 일봉 조회
# =========================

def get_stock_data(code):

    token = get_token()

    url = (
        f"{BASE_URL}"
        "/uapi/domestic-stock/v1/quotations/"
        "inquire-daily-itemchartprice"
    )

    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST03010100"
    }

    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": "20260715",
        "FID_INPUT_DATE_2": "20260729",
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0"
    }

    try:
        r = requests.get(
            url,
            headers=headers,
            params=params,
            timeout=10
        )

        data = r.json()

        result = {}

        rows = data.get("output2", [])

        for row in rows:

            date = row["stck_bsop_date"]

            if date in TARGET_DATES:

                high = int(row["stck_hgpr"])
                low = int(row["stck_lwpr"])
                volume = row["acml_vol"]

                volatility = high - low

                result[date] = (
                    f"{volatility}_{volume}"
                )

        return code, result

    except Exception as e:
        print(code, e)
        return code, {}



# =========================
# 실행
# =========================

def main():

    print("시작")

    df = pd.read_excel(
        "stocks.xlsx"
    )


    # name : 삼성전자_005930
    df["code"] = (
        df["name"]
        .astype(str)
        .str.split("_")
        .str[-1]
        .str.zfill(6)
    )


    codes = df["code"].tolist()


    result_map = {}


    print(
        f"조회 종목수 : {len(codes)}"
    )


    with ThreadPoolExecutor(
        max_workers=10
    ) as executor:

        futures = [
            executor.submit(
                get_stock_data,
                code
            )
            for code in codes
        ]


        for future in as_completed(futures):

            code, data = future.result()

            result_map[code] = data


    print("엑셀 생성")


    for date in TARGET_DATES:

        col = datetime.strptime(
            date,
            "%Y%m%d"
        ).strftime(
            "%Y-%m-%d"
        )

        df[col] = df["code"].apply(
            lambda x:
            result_map.get(x, {}).get(
                date,
                ""
            )
        )


    df.drop(
        columns=["code"],
        inplace=True
    )


    df.to_excel(
        "volatility_result.xlsx",
        index=False
    )


    print(
        "완료 : volatility_result.xlsx"
    )


if __name__ == "__main__":
    main()
