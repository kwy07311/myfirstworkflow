import pandas as pd
from pykrx_range import get_range_data
from logger import log

STOCK_FILE = "input/mydata.xlsx"

def extract_code(value):
    value = str(value)
    code = value.split("_")[-1]
    return code.zfill(6)

def update_excel():
    df = pd.read_excel(STOCK_FILE)
    codes = []
    for value in df["name"]:
        code = extract_code(value)
        codes.append(code)

    log(f"조회 종목 수 : {len(codes)}")

    date, range_data = get_range_data(codes)
    log(f"{date} 데이터 업데이트")

    if date not in df.columns:
        df[date] = None

    for idx, code in enumerate(codes):
        if code in range_data:
            df.at[idx, date] = range_data[code]

    df.to_excel(STOCK_FILE, index=False)
    log("엑셀 업데이트 완료")
