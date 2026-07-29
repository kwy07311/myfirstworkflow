import pandas as pd
from pykrx_range import get_range_data
from logger import log

STOCK_FILE = "input/mydata.xlsx"
MAX_DATE_COLUMNS = 10   # name 칼럼 제외하고 유지할 날짜 칼럼 수


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

    # name을 제외한 날짜 칼럼만 추출 (등장 순서 = 오래된 순 -> 최신 순으로 쌓인다고 가정)
    date_columns = [col for col in df.columns if col != "name"]

    # 최대 개수를 초과하면 가장 오래된(왼쪽) 날짜 칼럼부터 삭제
    if len(date_columns) > MAX_DATE_COLUMNS:
        columns_to_drop = date_columns[: len(date_columns) - MAX_DATE_COLUMNS]
        df = df.drop(columns=columns_to_drop)
        log(f"오래된 날짜 칼럼 삭제 : {columns_to_drop}")

    df.to_excel(STOCK_FILE, index=False)
    log("엑셀 업데이트 완료")
