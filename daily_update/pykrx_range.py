import pandas as pd
from pykrx import stock
from datetime import datetime
from logger import log


def get_trade_date():
    today = datetime.now()
    return today.strftime("%Y%m%d")


def get_range_data(codes):
    date = get_trade_date()
    result = {}

    try:
        df_kospi = stock.get_market_ohlcv_by_ticker(date, market="KOSPI")
        df_kosdaq = stock.get_market_ohlcv_by_ticker(date, market="KOSDAQ")
        df_all = pd.concat([df_kospi, df_kosdaq])
    except Exception as e:
        log(f"전체 시세 조회 실패 : {e}")
        return date[2:], result

    if df_all.empty:
        log(f"{date} 거래 데이터 없음 (휴장일일 수 있음)")
        return date[2:], result

    for code in codes:
        if code in df_all.index:
            row = df_all.loc[code]
            result[code] = row["고가"] - row["저가"]
        else:
            log(f"{code} 조회 실패 : 데이터 없음")

    return date[2:], result
