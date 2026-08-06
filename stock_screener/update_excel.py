import pandas as pd
from pykrx_range import get_range_data
from logger import log

STOCK_FILE = "input/mydata.xlsx"
MAX_DATE_COLUMNS = 60   # 기존 daily_update는 10일치, stock_screener는 20일 이평선 계산을 위해 60일치 보관


def extract_code(value):
    value = str(value)
    code = value.split("_")[-1]
    return code.zfill(6)


def update_excel(target_date=None):

    df = pd.read_excel(STOCK_FILE)

    codes = []

    for value in df["name"]:
        code = extract_code(value)
        codes.append(code)

    log(f"조회 종목 수 : {len(codes)}")


    # 날짜 지정 조회
    date, range_data = get_range_data(
        codes,
        target_date
    )

    log(f"{date} 데이터 업데이트")


    if date not in df.columns:
        df[date] = None


    for idx, code in enumerate(codes):

        if code in range_data:
            df.at[idx, date] = range_data[code]


    # name 제외 날짜 컬럼
    date_columns = [
        col for col in df.columns
        if col != "name"
    ]


    # 최근 60개(MAX_DATE_COLUMNS)만 유지
    if len(date_columns) > MAX_DATE_COLUMNS:

        columns_to_drop = date_columns[
            :len(date_columns) - MAX_DATE_COLUMNS
        ]

        df = df.drop(columns=columns_to_drop)

        log(
            f"오래된 날짜 칼럼 삭제 : {columns_to_drop}"
        )


    df.to_excel(
        STOCK_FILE,
        index=False
    )

    log("엑셀 업데이트 완료")
