from pykrx import stock
from datetime import datetime
import os


def get_trade_date():

    today = datetime.now()

    return today.strftime("%Y%m%d")


def get_range_data(codes):

    date = get_trade_date()

    result = {}

    for code in codes:

        try:

            df = stock.get_market_ohlcv(
                date,
                date,
                code
            )

            if df.empty:
                continue

            high = df.iloc[0]["고가"]
            low = df.iloc[0]["저가"]

            result[code] = high - low


        except Exception as e:

            print(
                f"{code} 조회 실패 : {e}"
            )


    return date[2:], result
