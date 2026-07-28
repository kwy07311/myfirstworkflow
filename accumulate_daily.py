# -*- coding: utf-8 -*-
"""
전종목 일별 OHLCV 누적 스크립트
================================
GitHub Actions에서 장마감 후 하루 1회 실행하는 용도.

- pykrx로 "하루치 전종목 시세"를 한 번의 호출로 가져와 누적 CSV(data/ohlcv_history.csv)에 append.
- 이미 저장된 마지막 날짜 다음날부터 오늘까지 빠진 거래일을 자동으로 백필한다.
- 파일이 무한정 커지지 않도록 최근 KEEP_DAYS 거래일치만 유지한다.

사전 준비:
  pip install pykrx pandas
"""

import os
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock

DATA_DIR = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "ohlcv_history.csv")
KEEP_DAYS = 60  # 누적 파일에 유지할 최근 거래일 수 (계산엔 11일이면 충분하지만 여유있게)
N_INIT_DAYS = 10  # 최초 실행 시 정확히 채워둘 거래일 수 (오늘 제외, 어제까지)
MAX_CALENDAR_LOOKBACK = 45  # 최초 백필 시 최대 며칠 전까지 거슬러 올라갈지 (공휴일 대비 여유)


def fetch_if_trading_day(date_str: str):
    try:
        df = stock.get_market_ohlcv_by_ticker(date_str, market="ALL")
    except Exception:
        return None
    if df is None or df.empty or df["거래량"].sum() == 0:
        return None  # 주말/공휴일
    return df


def get_init_dates(n_days: int, end_date: datetime | None = None) -> list:
    """최초 실행용: end_date(기본값: 어제)부터 거꾸로 훑어 실제 거래일 n_days개를 찾아
    과거->최신 순으로 반환한다 (공휴일이 몰려 있어도 정확히 n_days개를 채운다).
    기본값이 '어제'인 이유: 장중에 실행하면 오늘 데이터가 미확정 상태라
    오늘은 제외하고 확정된 어제까지의 데이터만 채우기 위함."""
    if end_date is None:
        end_date = datetime.now() - timedelta(days=1)

    dates = []
    d = end_date
    checked = 0
    while len(dates) < n_days and checked < MAX_CALENDAR_LOOKBACK:
        date_str = d.strftime("%Y%m%d")
        if fetch_if_trading_day(date_str) is not None:
            dates.append(date_str)
        d -= timedelta(days=1)
        checked += 1
    dates.reverse()
    return dates


def get_incremental_dates(last_saved_date: str) -> list:
    """이후 실행용: 마지막 저장일 다음날부터 오늘까지의 날짜 후보를 순방향으로 반환.
    실제 거래일 여부는 호출하는 쪽에서 fetch_if_trading_day로 걸러진다."""
    today = datetime.now()
    next_day = datetime.strptime(last_saved_date, "%Y%m%d") + timedelta(days=1)

    dates = []
    d = next_day
    while d.date() <= today.date():
        dates.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return dates


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    if os.path.exists(HISTORY_FILE):
        history = pd.read_csv(HISTORY_FILE, dtype={"code": str, "date": str})
        last_saved_date = history["date"].max() if not history.empty else None
    else:
        history = pd.DataFrame(columns=["date", "code", "open", "high", "low", "close", "volume"])
        last_saved_date = None

    if last_saved_date is None:
        print(f"최초 실행: 오늘 제외, 어제까지의 최근 {N_INIT_DAYS}거래일 백필")
        target_dates = get_init_dates(N_INIT_DAYS)
    else:
        print(f"증분 실행: 마지막 저장일({last_saved_date}) 다음날부터 오늘까지 확인")
        target_dates = get_incremental_dates(last_saved_date)
    print(f"확인할 날짜: {target_dates}")

    new_rows = []
    for d in target_dates:
        df = fetch_if_trading_day(d)
        if df is None:
            print(f"{d}: 휴장일/데이터없음 -> 건너뜀")
            continue
        df = df.reset_index()[["티커", "시가", "고가", "저가", "종가", "거래량"]]
        df.columns = ["code", "open", "high", "low", "close", "volume"]
        df.insert(0, "date", d)
        new_rows.append(df)
        print(f"{d}: {len(df)}종목 수집 완료")

    if new_rows:
        added = pd.concat(new_rows, ignore_index=True)
        history = pd.concat([history, added], ignore_index=True)
        history = history.drop_duplicates(subset=["date", "code"], keep="last")

    if not history.empty:
        unique_dates = sorted(history["date"].unique())
        keep_dates = set(unique_dates[-KEEP_DAYS:])
        history = history[history["date"].isin(keep_dates)]

    history = history.sort_values(["code", "date"]).reset_index(drop=True)
    history.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")
    print(f"\n누적 완료: {history['date'].nunique()}거래일 / {len(history)}행 -> {HISTORY_FILE}")


if __name__ == "__main__":
    main()
