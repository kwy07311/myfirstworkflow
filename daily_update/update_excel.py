import pandas as pd

from pykrx_range import get_range_data

STOCK_FILE = "input/mydata.xlsx"


def update_excel():

    date, range_dict = get_range_data()

    print(date, "데이터 조회 완료")

    df = pd.read_excel(STOCK_FILE)

    # 이미 컬럼이 있으면 덮어쓰기
    if date not in df.columns:
        df[date] = None

    for idx, row in df.iterrows():

        code = str(row["name"]).split("_")[-1]

        if code in range_dict:
            df.at[idx, date] = range_dict[code]

    df.to_excel(STOCK_FILE, index=False)

    print("엑셀 저장 완료")
