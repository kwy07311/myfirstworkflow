import pandas as pd
from pykrx_range import get_range_data
from logger import log

STOCK_FILE = "myfirstworkflow/stock_screener/input/mydata2.xlsx"
MAX_DATE_COLUMNS = 120

EXPECTED_FIELDS = 5  # 고가, 시가, 저가, 종가, 거래량


def extract_code(value):
    value = str(value)
    code = value.split("_")[-1]
    return code.zfill(6)


def parse_ohlcv(raw_value):
    """
    '고가_시가_저가_종가_거래량' 형태의 문자열을 _로 스플릿해서 검증.
    형식이 올바르면(필드 5개) 그대로 문자열로 반환해서 셀에 저장,
    형식이 깨져 있으면 None을 반환해서 해당 값은 저장하지 않고 건너뜀.
    """
    parts = str(raw_value).split("_")
    if len(parts) != EXPECTED_FIELDS:
        log(f"[경고] OHLCV 형식이 아님 (건너뜀): {raw_value}")
        return None
    return "_".join(parts)


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
            parsed = parse_ohlcv(range_data[code])
            if parsed is not None:
                df.at[idx, date] = parsed

    # name 제외 날짜 컬럼
    date_columns = [
        col for col in df.columns
        if col != "name"
    ]

    # 최근 120개(MAX_DATE_COLUMNS)만 유지
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
