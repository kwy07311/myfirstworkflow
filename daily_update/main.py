import pandas as pd
import time
import os

from kis_token import get_access_token
from today_range import get_today_range
from update_excel import update_excel
from logger import log

import config



STOCK_FILE="input/mydata.xlsx"



token=get_access_token()


stocks=pd.read_excel(
    STOCK_FILE
)



result=[]


success=0
fail=0



log(
    f"수집 시작 : 총 {len(stocks)}종목"
)



for idx,row in stocks.iterrows():


    name=row["name"]


    code=name.split("_")[-1]



    date,value=get_today_range(
        token,
        code
    )


    if value is not None:


        result.append(
            {
                "name":name,
                date[2:]:value
            }
        )


        success+=1


    else:

        fail+=1

        log(
            f"실패 : {name}"
        )



    if idx % 100 ==0:

        log(
            f"{idx}/{len(stocks)} 완료"
        )


    time.sleep(
        config.REQUEST_DELAY
    )



# 최종 저장

if result:

    update_excel(
        result
    )


log(
    f"완료 성공:{success} 실패:{fail}"
)