import requests
import time
from datetime import datetime

import config


def get_today_range(token, code):


    url = (
        f"{config.BASE_URL}"
        "/uapi/domestic-stock/v1/quotations/inquire-daily-price"
    )


    headers = {

        "authorization":
            f"Bearer {token}",

        "appkey":
            config.APP_KEY,

        "appsecret":
            config.APP_SECRET,

        "tr_id":
            "FHKST01010400"
    }


    params = {

        "FID_COND_MRKT_DIV_CODE":"J",

        "FID_INPUT_ISCD":code,

        "FID_PERIOD_DIV_CODE":"D",

        "FID_ORG_ADJ_PRC":"0"
    }



    for retry in range(config.MAX_RETRY):

        try:

            res=requests.get(
                url,
                headers=headers,
                params=params,
                timeout=10
            )


            data=res.json()


            rows=data.get(
                "output",
                []
            )


            if len(rows)==0:
                return None,None



            # 가장 최근 거래일
            row=rows[0]


            date=row["stck_bsop_date"]


            high=int(row["stck_hgpr"])

            low=int(row["stck_lwpr"])


            return date, high-low



        except Exception as e:


            time.sleep(1)



    return None,None
