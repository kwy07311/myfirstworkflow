import pandas as pd
import os


# ==================================
# 파일 경로
# ==================================

# 과거 변동폭 누적 데이터 (읽기 전용)
HISTORY_FILE = "input/mydata.xlsx"

# 기존 종가수집 코드가 생성한 오늘 데이터
TODAY_FILE = "input/today_stock_data.xlsx"

# 결과 저장
OUTPUT_DIR = "output"
OUTPUT_FILE = os.path.join(
    OUTPUT_DIR,
    "volatility_breakout_result.xlsx"
)


# ==================================
# 결과 폴더 생성
# ==================================

os.makedirs(
    OUTPUT_DIR,
    exist_ok=True
)


# ==================================
# 엑셀 읽기
# ==================================

history = pd.read_excel(
    HISTORY_FILE
)

today = pd.read_excel(
    TODAY_FILE
)


# ==================================
# 과거 변동폭 최대값 계산
# name 제외 모든 컬럼 = 날짜
# ==================================

date_columns = [
    col for col in history.columns
    if col != "name"
]


history["max_history_range"] = (
    history[date_columns]
    .max(axis=1)
)


# 필요한 데이터만 사용
history_ref = history[
    [
        "name",
        "max_history_range"
    ]
]


# ==================================
# 오늘 데이터와 연결
# ==================================

df = today.merge(
    history_ref,
    on="name",
    how="inner"
)


# ==================================
# 오늘 변동폭 계산
# ==================================

df["today_range"] = (
    df["high"]
    -
    df["low"]
)


# ==================================
# 양봉 여부
# 현재가 > 시가
# ==================================

df["bullish"] = (
    df["current_price"]
    >
    df["open"]
)


# ==================================
# 위꼬리 비율 계산
# ==================================

df["upper_shadow_ratio"] = 0


valid = (
    df["today_range"] > 0
)


df.loc[valid, "upper_shadow_ratio"] = (
    (
        df.loc[valid, "high"]
        -
        df.loc[valid, "current_price"]
    )
    /
    df.loc[valid, "today_range"]
)


# ==================================
# 조건 검색
#
# 1. 오늘 변동폭 > 과거 최대 변동폭
# 2. 양봉
# 3. 위꼬리 10% 미만
# ==================================

result = df[
    (df["today_range"] > df["max_history_range"])
    &
    (df["bullish"])
    &
    (df["upper_shadow_ratio"] < 0.1)
]


# ==================================
# 결과 저장
# ==================================

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
    f"변동폭 돌파 검색 완료 : {len(result)}개"
)

print(
    f"결과 파일 : {OUTPUT_FILE}"
)
