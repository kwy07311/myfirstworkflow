import pandas as pd
from datetime import datetime
from pykrx_range import get_range_data
from logger import log

STOCK_FILE = "input/mydata2.xlsx"
MAX_DATE_COLUMNS = 60   # 기존 daily_update는 10일치, stock_screener는 20일 이평선 계산을 위해 60일치 보관


def extract_code(value):
    value = str(value)
    code = value.split("_")[-1]
    return code.zfill(6)


def normalize_date_column(col):
    """
    엑셀 칼럼 헤더를 'YYMMDD'(6자리 문자열, 대시 없음)로 통일.
    - name 칼럼은 그대로 둠
    - datetime/Timestamp 타입으로 저장된 옛날 칼럼 -> 문자열로 변환
    - '26-08-05' 처럼 대시가 들어간 문자열 -> 대시 제거한 6자리로 변환
    - 이미 'YYMMDD' 형식인 문자열/숫자 -> 그대로 6자리 문자열로 변환
    """
    if col == "name":
        return col

    # datetime, Timestamp 타입 (엑셀이 자동으로 날짜로 인식해버린 경우)
    if isinstance(col, (pd.Timestamp, datetime)):
        return col.strftime("%y%m%d")

    col_str = str(col).strip()

    # '26-08-05' 처럼 대시 포함된 문자열
    if "-" in col_str:
        try:
            parsed = datetime.strptime(col_str, "%y-%m-%d")
            return parsed.strftime("%y%m%d")
        except ValueError:
            pass

    # 숫자만 있는 경우 (260806.0 처럼 float로 읽혔을 수도 있음) -> 정수화 후 6자리 문자열
    try:
        return str(int(float(col_str))).zfill(6)
    except ValueError:
        return col_str  # 위 어떤 패턴에도 안 맞으면 원본 그대로 (name 등 예외 상황 대비)


def update_excel(target_date=None):

    df = pd.read_excel(STOCK_FILE)

    # 칼럼 헤더 형식 통일 (기존 datetime/대시 포함 칼럼들을 전부 YYMMDD 문자열로 정리)
    df.columns = [normalize_date_column(col) for col in df.columns]

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

    # 조회 결과가 완전히 비어있으면(휴장일이거나, 당일 시세가 아직 KRX에 게시되지 않은 경우)
    # 빈 날짜 칼럼을 만들지 않고 여기서 중단 -> 엑셀 저장/커밋도 하지 않음
    if not range_data:
        log(
            f"[중단] {date} 데이터가 비어있습니다. "
            f"휴장일이거나, 당일 시세가 아직 KRX에 게시되지 않았을 수 있습니다. "
            f"(보통 장마감 후 저녁 늦게 게시되니 시간을 두고 다시 시도해주세요)"
        )
        return

    log(f"{date} 데이터 업데이트 (조회 성공 종목 수 : {len(range_data)}/{len(codes)})")


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
