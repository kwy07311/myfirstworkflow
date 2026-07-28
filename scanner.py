import FinanceDataReader as fdr
import pandas as pd
from datetime import datetime, timedelta

# 종목 목록
stocks = fdr.StockListing("KRX")

# 최근 30일 데이터 확보
start = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")

result = []

print(f"전체 종목 : {len(stocks)}")

for _, stock in stocks.iterrows():

    code = stock["Code"]
    name = stock["Name"]

    try:
        df = fdr.DataReader(code, start)

        if len(df) < 11:
            continue

        # 전체 길이
        df["range"] = df["High"] - df["Low"]

        # 윗꼬리
        df["upper"] = df["High"] - df["Close"]

        # 최근 10거래일(오늘 제외)
        df["max10"] = (
            df["range"]
            .shift(1)
            .rolling(10)
            .max()
        )

        last = df.iloc[-1]

        # 양봉
        if last["Close"] <= last["Open"]:
            continue

        # 최근10거래일보다 큰 캔들
        if last["range"] <= last["max10"]:
            continue

        # 윗꼬리 10%
        if last["upper"] / last["range"] >= 0.10:
            continue

        result.append({
            "종목코드": code,
            "종목명": name,
            "시가": last["Open"],
            "고가": last["High"],
            "저가": last["Low"],
            "종가": last["Close"],
            "전체길이": last["range"],
            "윗꼬리": last["upper"],
            "윗꼬리비율": round(last["upper"] / last["range"], 3)
        })

        print(name)

    except Exception:
        continue

pd.DataFrame(result).to_excel("result.xlsx", index=False)

print(f"\n검색 완료 : {len(result)}개")
