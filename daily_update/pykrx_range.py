from pykrx import stock
from datetime import datetime, timedelta


def get_last_business_day():
    today = datetime.today()

    while True:
        today -= timedelta(days=1)

        df = stock.get_market_ohlcv_by_ticker(today.strftime("%Y%m%d"))

        if not df.empty:
            return today.strftime("%Y%m%d")


def get_range_data():

    date = get_last_business_day()

    df = stock.get_market_ohlcv_by_ticker(date)

    result = {}

    for code, row in df.iterrows():
        result[code] = row["고가"] - row["저가"]

    return date[2:], result
