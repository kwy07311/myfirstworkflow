import pandas as pd
from pykrx import stock
from datetime import datetime, timedelta, timezone
from logger import log



def get_trade_date(target_date=None):

    # 사용자가 입력한 날짜가 있으면 사용
    # 입력 : 260805
    # 변환 : 20260805

    if target_date:

        if len(target_date) == 6:
            return "20" + target_date

        return target_date


    # 입력 없으면 오늘 날짜
    kst = timezone(timedelta(hours=9))

    today = datetime.now(kst)

    return today.strftime("%Y%m%d")



def get_range_data(codes, target_date=None):

    date = get_trade_date(target_date)

    result = {}


    try:

        df_kospi = stock.get_market_ohlcv_by_ticker(
            date,
            market="KOSPI"
        )

        df_kosdaq = stock.get_market_ohlcv_by_ticker(
            date,
            market="KOSDAQ"
        )

        df_all = pd.concat(
            [
                df_kospi,
                df_kosdaq
            ]
        )


    except Exception as e:

        log(
            f"전체 시세 조회 실패 : {e}"
        )

        return date[2:], result



    if df_all.empty:

        log(
            f"{date} 거래 데이터 없음 (휴장일일 수 있음)"
        )

        return date[2:], result



    for code in codes:


        if code in df_all.index:

            row = df_all.loc[code]


            price_range = int(
                row["고가"] - row["저가"]
            )

            volume = int(
                row["거래량"]
            )


            result[code] = (
                f"{price_range}_{volume}"
            )


        else:

            log(
                f"{code} 조회 실패 : 데이터 없음"
            )



    return date[2:], result
