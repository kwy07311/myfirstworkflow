import pandas as pd
import os


# ==========================
# 파일 경로
# ==========================

HISTORY_FILE = "input/mydata.xlsx"

# 기존 수집 코드가 만든 오늘 데이터 파일
TODAY_FILE = "input/today_price.xlsx"

# 결과 폴더
OUTPUT_DIR = "output"

# 결과 파일
OUTPUT_FILE = os.path.join(
    OUTPUT_DIR,
    "breakout_result.xlsx"
)


# ==========================
# 결과 폴더 생성
# ==========================

os.makedirs(
    OUTPUT_DIR,
    exist_ok=True
)


# ==========================
# 데이터 읽기
# ==========================

history = pd.read_excel(
    HISTORY_FILE
)

today = pd.read_excel(
    TODAY_FILE
)


# ==========================
# 날짜 컬럼 자동 찾기
# name 제외
# ==========================

date_columns = [
    col for col in history.columns
    if col != "name"
]


if len(date_columns) == 0:
    raise Exception(
        "날짜 변동폭 컬럼을 찾을 수 없습니다."
    )


# ==========================
# 과거 최대 변동폭 계산
# ==========================

history["max_history_range"] = (
    history[date_columns]
    .max(axis=1)
)


# ==========================
# 오늘 데이터와 연결
# ==========================

df = today.merge(
    history[
        [
            "name",
            "max_history_range"
        ]
    ],
    on="name",
    how="inner"
)


# ==========================
# 오늘 현재 변동폭
# ==========================

df["today_range"] = (
    df["high"]
    -
    df["low"]
)


# ==========================
# 양봉 여부
# 현재가 > 시가
# ==========================

df["is_bullish"] = (
    df["current_price"]
    >
    df["open"]
)


# ==========================
# 위꼬리 비율
# ==========================

df["upper_shadow_ratio"] = 0


valid_range = (
    df["today_range"] > 0
)


df.loc[
    valid_range,
    "upper_shadow_ratio"
] = (
    (
        df.loc[valid_range, "high"]
        -
        df.loc[valid_range, "current_price"]
    )
    /
    df.loc[valid_range, "today_range"]
)


# ==========================
# 조건 검색
#
# 1. 오늘 변동폭 > 과거 최대
# 2. 양봉
# 3. 위꼬리 < 10%
# ==========================

result = df[
    (df["today_range"] > df["max_history_range"])
    &
    (df["is_bullish"])
    &
    (df["upper_shadow_ratio"] < 0.1)
]


# ==========================
# 결과 저장
# ==========================

result = result[
    [
        "name",
        "today_range",
        "max_history_range",
        "current_price",
        "upper_shadow_ratio"
    ]
]


result.to_excel(
    OUTPUT_FILE,
    index=False
)


print(
    f"검색 완료 : {len(result)}개 종목"
)

print(
    f"결과 저장 : {OUTPUT_FILE}"
)
