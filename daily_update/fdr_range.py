import FinanceDataReader as fdr
from datetime import datetime, timedelta


def get_last_business_day():

    today = datetime.today()

    while True:
        today -= timedelta(days=1)

        if today.weekday() < 5:
            return today.strftime("%Y-%m-%d")


def get_range_data():

    date = get_last_business_day()

    df = fdr.StockListing("KRX")

    result = {}

    for code in df["Code"]:

        try:
            price = fdr.DataReader(
                code,
                date,
                date
            )

            if not price.empty:
                result[code] = (
                    price.iloc[0]["High"]
                    -
                    price.iloc[0]["Low"]
                )

        except:
            continue

    return date.replace("-", "")[2:], result
