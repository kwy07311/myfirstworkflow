# find_volatility_breakout.py

import os
import json
import time
import requests
import pandas as pd


# ==============================
# 환경 설정
# ==============================

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")


REAL_URL = "https://openapi.koreainvestment.com:9443"


HISTORY_FILE = "input/mydata.xlsx"

OUTPUT_DIR = "output"

OUTPUT_FILE = os.path.join(
    OUTPUT_DIR,
    "volatility_breakout_result.xlsx"
)


# ==============================
# 결과 폴더 생성
# ==============================

os.makedirs(
    OUTPUT_DIR,
    exist_ok=True
)


# ==============================
# KIS Access Token 발급
# ==============================

def get_access_token():

    url = f"{REAL_URL}/oauth2/tokenP"

    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }

    res = requests.post(
        url,
        json=body
    )

    res.raise_for_status()

    return res.json()["access_token"]



# ==============================
# 현재가 조회
# ==============================

def get_stock_price(
    token,
    stock_code
):

    url = (
        f"{REAL_URL}/uapi/domestic-stock/v1/"
        "quotations/inquire-price"
    )


    headers = {

        "authorization":
            f"Bearer {token}",

        "appkey":
            APP_KEY,

        "appsecret":
            APP_SECRET,

        "tr_id":
            "FHKST01010100"

    }


    params = {

        "FID_COND_MRKT_DIV_CODE":
            "J",

        "FID_INPUT_ISCD":
            stock_code

    }


    res = requests.get(
        url,
        headers=headers,
        params=params
    )


    data = res.json()


    output = data["output"]


    return {

        "open":
            float(output["stck_oprc"]),

        "high":
            float(output["stck_hgpr"]),

        "low":
            float(output["stck_lwpr"]),

        "current_price":
            float(output["stck_prpr"])

    }



# ==============================
# 종목코드 추출
# 삼성전자_005930
# ==============================

def extract_code(name):

    return str(name).split("_")[-1]



# ==============================
# 메인
# ==============================


def main():


    print("변동폭 돌파 검색 시작")


    # --------------------------
    # 과거 데이터 읽기
    # --------------------------

    history = pd.read_excel(
        HISTORY_FILE
    )


    date_columns = [
        c for c in history.columns
        if c != "name"
    ]


    history["max_history_range"] = (
        history[date_columns]
        .max(axis=1)
    )


    token = get_access_token()


    result_list = []


    # --------------------------
    # 종목별 현재 데이터 조회
    # --------------------------

    for _, row in history.iterrows():


        name = row["name"]

        code = extract_code(name)


        try:

            price = get_stock_price(
                token,
                code
            )


            today_range = (
                price["high"]
                -
                price["low"]
            )


            if today_range <= 0:
                continue


            upper_shadow_ratio = (
                price["high"]
                -
                price["current_price"]
            ) / today_range


            bullish = (
                price["current_price"]
                >
                price["open"]
            )


            max_history = (
                row["max_history_range"]
            )


            # 조건

            if (
                today_range > max_history
                and bullish
                and upper_shadow_ratio < 0.1
            ):

                result_list.append({

                    "name":
                        name,

                    "code":
                        code,

                    "today_range":
                        today_range,

                    "max_history_range":
                        max_history,

                    "current_price":
                        price["current_price"],

                    "upper_shadow_ratio":
                        upper_shadow_ratio

                })


        except Exception as e:

            print(
                name,
                "조회 오류:",
                e
            )


        # API 호출 제한 고려

        time.sleep(0.1)



    # --------------------------
    # 결과 저장
    # --------------------------

    result = pd.DataFrame(
        result_list
    )


    result.to_excel(
        OUTPUT_FILE,
        index=False
    )


    print(
        "완료:",
        len(result),
        "개 종목"
    )

    print(
        OUTPUT_FILE
    )



if __name__ == "__main__":

    main()
