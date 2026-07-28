from pykrx import stock
from datetime import datetime, timedelta
import pandas as pd

# 오늘 날짜
today = datetime.today()
start = (today - timedelta(days=60)).strftime("%Y%m%d")
end = today.strftime("%Y%m%d")

result = []

tickers = stock.get_market_ticker_list(date=end)

print(f"전체 종목 : {len(tickers)}개")

for ticker in tickers:

    try:

        df = stock.get_market_ohlcv_by_date(
            start,
            end,
            ticker
        )

        # 데이터 부족
        if len(df) < 15:
            continue

        # 컬럼 계산
        df["range"] = df["고가"] - df["저가"]

        # 양봉 기준 윗꼬리
        df["upper_shadow"] = df["고가"] - df["종가"]

        # 최근10일 최대 전체길이(오늘 제외)
        df["max10"] = (
            df["range"]
            .shift(1)
            .rolling(10)
            .max()
        )

        last = df.iloc[-1]

        # 오늘이 음봉이면 제외
        if last["종가"] <= last["시가"]:
            continue

        # 전체길이 비교
        if last["range"] <= last["max10"]:
            continue

        # 윗꼬리 비율
        if last["upper_shadow"] / last["range"] >= 0.20:
            continue

        name = stock.get_market_ticker_name(ticker)

        result.append({
            "종목코드": ticker,
            "종목명": name,
            "시가": last["시가"],
            "고가": last["고가"],
            "저가": last["저가"],
            "종가": last["종가"],
            "전체길이": last["range"],
            "윗꼬리": last["upper_shadow"]
        })

        print(name)

    except Exception as e:
        print(ticker, e)

# 결과 저장
result_df = pd.DataFrame(result)

result_df.to_excel(
    "result.xlsx",
    index=False
)

print()
print("="*40)
print(f"검색 완료 : {len(result_df)}종목")
print("="*40)
