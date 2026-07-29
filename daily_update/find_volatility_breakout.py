# find_volatility_breakout.py

import os
import time
import requests
import pandas as pd

from concurrent.futures import ThreadPoolExecutor, as_completed


# ==================================
# 환경 설정
# ==================================

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")

REAL_URL = "https://openapi.koreainvestment.com:9443"


HISTORY_FILE = "input/mydata.xlsx"

OUTPUT_FILE = (
    "output/volatility_breakout_result.xlsx"
)


# 동시에 조회할 개수
MAX_WORKERS = 10



# ==================================
# 토큰 발급
# ==================================

def get_access_token():

    url = f"{REAL_URL}/oauth2/tokenP"

    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }


    response = requests.post(
        url,
        json=body,
        timeout=10
    )

    response.raise_for_status()

    return response.json()["access_token"]



# ==================================
# 종목코드 추출
# 삼성전자_005930
# ==================================

def extract_code(name):

    return str(name).split("_")[-1]



# ==================================
# 현재가 조회
# ==================================

def get_stock_price(token, name):

    code = extract_code(name)


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
            code

    }


    for retry in range(3):

        try:

            response = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=10
            )


            data = response.json()


            output = data["output"]


            return {

                "name":
                    name,

                "code":
                    code,

                "open":
                    float(output["stck_oprc"]),

                "high":
                    float(output["stck_hgpr"]),

                "low":
                    float(output["stck_lwpr"]),

                "current_price":
                    float(output["stck_prpr"])

            }


        except Exception as e:

            if retry < 2:

                time.sleep(1)

            else:

                return {

                    "name":
                        name,

                    "error":
                        str(e)

                }




# ==================================
# 메인
# ==================================

def main():

    start_time = time.time()


    print("=" * 40)
    print("변동폭 돌파 검색 시작")
    print("=" * 40)



    # -------------------------------
    # 과거 데이터
    # -------------------------------

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



    stock_names = (
        history["name"]
        .tolist()
    )


    total = len(stock_names)


    print(
        f"총 {total}개 종목 조회 예정"
    )



    # -------------------------------
    # 병렬 조회
    # -------------------------------

    results = []

    success = 0

    fail = 0



    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:


        futures = {

            executor.submit(
                get_stock_price,
                token,
                name
            ):
            name

            for name in stock_names

        }



        for idx, future in enumerate(
            as_completed(futures),
            start=1
        ):


            data = future.result()


            if "error" in data:

                fail += 1

            else:

                success += 1

                results.append(data)



            if idx % 100 == 0 or idx == total:

                print(
                    f"[{idx}/{total}] "
                    f"조회 완료 "
                    f"(성공:{success}, 실패:{fail})"
                )



    # -------------------------------
    # 비교
    # -------------------------------


    today = pd.DataFrame(
        results
    )


    merged = today.merge(
        history[
            [
                "name",
                "max_history_range"
            ]
        ],
        on="name",
        how="inner"
    )



    merged["today_range"] = (
        merged["high"]
        -
        merged["low"]
    )


    merged["upper_shadow_ratio"] = (

        (
            merged["high"]
            -
            merged["current_price"]
        )
        /
        merged["today_range"]

    )



    merged["bullish"] = (

        merged["current_price"]
        >
        merged["open"]

    )



    result = merged[

        (merged["today_range"]
         >
         merged["max_history_range"])

        &

        (merged["bullish"])

        &

        (merged["upper_shadow_ratio"]
         <
         0.1)

    ]



    # -------------------------------
    # 저장
    # -------------------------------


    os.makedirs(
        "output",
        exist_ok=True
    )


    result.to_excel(
        OUTPUT_FILE,
        index=False
    )



    elapsed = (
        time.time()
        -
        start_time
    )



    print("=" * 40)

    print(
        f"조회 성공 : {success}"
    )

    print(
        f"조회 실패 : {fail}"
    )

    print(
        f"돌파 종목 : {len(result)}"
    )


    if len(result) > 0:

        print("")

        for name in result["name"]:

            print(
                "★",
                name
            )


    print(
        f"실행 시간 : {elapsed:.1f}초"
    )


    print("=" * 40)



if __name__ == "__main__":

    main()
