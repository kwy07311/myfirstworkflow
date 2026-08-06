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
    """
    전 종목의 고가/시가/저가/종가/거래량을 조회해서
    {code: "고가_시가_저가_종가_거래량"} 형태로 반환.
    (기존 daily_update의 get_range_data는 '가격범위_거래량' 2개 값만 저장했지만,
     stock_screener는 20일 이평선 계산을 위해 종가가 필요하고,
     find_my_strategy.py에서 시가/고가/저가도 함께 참고할 수 있도록 5개 값 전체를 저장)
    """

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

            high = int(row["고가"])
            open_ = int(row["시가"])
            low = int(row["저가"])
            close = int(row["종가"])
            volume = int(row["거래량"])


            result[code] = (
                f"{high}_{open_}_{low}_{close}_{volume}"
            )


        else:

            log(
                f"{code} 조회 실패 : 데이터 없음"
            )



    return date[2:], result
