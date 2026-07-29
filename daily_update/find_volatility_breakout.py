import os
import time
import threading
import requests
import pandas as pd

from requests.adapters import HTTPAdapter
from concurrent.futures import ThreadPoolExecutor, as_completed


# ==================================
# 환경 설정
# ==================================

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


REAL_URL = "https://openapi.koreainvestment.com:9443"


HISTORY_FILE = "input/mydata.xlsx"


MAX_WORKERS = 10

# 초당 허용 호출 수 (실패율 보고 재조정 예정)
RATE_LIMIT_PER_SEC = 8


# ==================================
# 커넥션 재사용 (Session + Connection Pool)
# ==================================

_session = requests.Session()
_adapter = HTTPAdapter(
    pool_connections=MAX_WORKERS,
    pool_maxsize=MAX_WORKERS,
)
_session.mount("https://", _adapter)


# ==================================
# 초당 호출 수 제한 (Rate Limiter)
# ==================================

class RateLimiter:

    def __init__(self, calls_per_sec):

        self.interval = 1.0 / calls_per_sec

        self.lock = threading.Lock()

        self.last_call = 0.0


    def wait(self):

        with self.lock:

            now = time.time()

            elapsed = now - self.last_call

            if elapsed < self.interval:

                time.sleep(self.interval - elapsed)

            self.last_call = time.time()


_rate_limiter = RateLimiter(RATE_LIMIT_PER_SEC)



# ==================================
# 텔레그램 전송
# ==================================

def send_telegram(message):

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )


    data = {

        "chat_id": TELEGRAM_CHAT_ID,

        "text": message

    }


    response = requests.post(

        url,

        data=data,

        timeout=10

    )


    response.raise_for_status()



# ==================================
# 토큰 발급
# ==================================

def get_access_token():


    url = f"{REAL_URL}/oauth2/tokenP"


    body = {

        "grant_type":
            "client_credentials",

        "appkey":
            APP_KEY,

        "appsecret":
            APP_SECRET

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


            _rate_limiter.wait()


            response = _session.get(

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

                # 재시도 간격을 점점 늘려서(0.5초, 1초) 같은 혼잡 구간에
                # 바로 재요청하지 않도록 함
                time.sleep(0.5 * (retry + 1))


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

    errors = []

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

                errors.append(data["error"])


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


    today = pd.DataFrame(results)



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
    # 텔레그램 전송
    # -------------------------------


    if len(result) > 0:


        message = (

            "📈 변동폭 돌파 종목\n\n"

        )


        for name in result["name"]:


            print(

                "★",

                name

            )


            message += (

                f"★ {name}\n"

            )


    else:


        message = (

            "📊 오늘 조건 만족 "

            "변동폭 돌파 종목 없음"

        )




    send_telegram(message)



    print(

        "텔레그램 전송 완료"

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


    if errors:


        error_counts = {}


        for err in errors:

            key = str(err)[:80]

            error_counts[key] = (
                error_counts.get(key, 0) + 1
            )


        print("-- 실패 유형 상위 5개 --")


        for key, count in sorted(

            error_counts.items(),

            key=lambda x: x[1],

            reverse=True

        )[:5]:


            print(f"  [{count}건] {key}")

    print(

        f"돌파 종목 : {len(result)}"

    )

    print(

        f"실행 시간 : {elapsed:.1f}초"

    )

    print("=" * 40)




if __name__ == "__main__":

    main()
