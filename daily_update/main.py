import pandas as pd
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from kis_token import get_access_token
from today_range import get_today_range
from update_excel import update_excel
from logger import log

import config


STOCK_FILE = "input/mydata.xlsx"

# 동시에 실행할 API 요청 수
MAX_WORKERS = 5


token = get_access_token()


stocks = pd.read_excel(
    STOCK_FILE
)


def extract_code(name):

    match = re.search(
        r"\d{6}",
        str(name)
    )

    if match:
        return match.group()

    return None



def request_stock(row):

    name = row["name"]

    code = extract_code(name)


    if code is None:

        return {
            "name": name,
            "date": None,
            "value": None,
            "error": "code error"
        }


    try:

        date, value = get_today_range(
            token,
            code
        )


        return {
            "name": name,
            "date": date,
            "value": value,
            "error": None
        }


    except Exception as e:

        return {
            "name": name,
            "date": None,
            "value": None,
            "error": str(e)
        }




result = []

success = 0
fail = 0



log(
    f"수집 시작 : 총 {len(stocks)}종목"
)



with ThreadPoolExecutor(
    max_workers=MAX_WORKERS
) as executor:


    futures = []


    for _, row in stocks.iterrows():

        futures.append(
            executor.submit(
                request_stock,
                row
            )
        )



    for idx, future in enumerate(
        as_completed(futures),
        1
    ):


        data = future.result()


        if data["value"] is not None:


            result.append(
                {
                    "name": data["name"],
                    data["date"][2:]: data["value"]
                }
            )

            success += 1


        else:

            fail += 1

            log(
                f"실패 : {data['name']} / {data['error']}"
            )


        if idx % 100 == 0:

            log(
                f"{idx}/{len(stocks)} 완료"
            )



if result:

    update_excel(
        result
    )



log(
    f"완료 성공:{success} 실패:{fail}"
)
