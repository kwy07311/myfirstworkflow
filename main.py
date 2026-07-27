"""
매일 정해진 시간에 GitHub Actions가 이 파일을 실행하면:
1. 전 종목을 스캔해서 조건에 맞는 종목을 찾고
2. 결과를 엑셀(.xlsx)로 저장하고 (종목명 클릭 시 네이버 차트로 이동하는 하이퍼링크 포함)
3. 네이버 메일로 엑셀 파일을 첨부해서 보낸다.

=====================================================================
[AI/사람 공통 안내] 새로운 매수 기법을 추가해달라는 요청을 받았다면?
=====================================================================
이 파일을 처음 보는 상태에서도 아래 순서만 따르면 매수 기법을 추가할 수 있다.
(예: "MACD 골든크로스 기법 추가해줘", "OO 영상에 나온 매매법 추가해줘" 같은 요청)

  1. has_xxx_signal(ohlcv_df, ...) -> bool  형태의 새 함수를 하나 작성한다.
     - 이 파일에서 CONFIRMATION_TECHNIQUES 바로 위에 정의된 함수들
       (has_candle_ma_breakout_signal, has_macd_buy_signal, has_bollinger_rsi_buy_signal 등)이
       전부 이 패턴을 따르고 있으니, 그대로 복사해서 참고하면 된다.
     - 첫 번째 인자 ohlcv_df는 'Open','High','Low','Close','Volume' 컬럼을 가진 pandas
       DataFrame이며, 인덱스는 날짜(과거->최근 순), 마지막 행(iloc[-1])이 가장 최근 거래일이다.
     - 데이터가 부족하면(예: 계산에 필요한 기간보다 짧으면) 조용히 False를 반환해야 한다.
       (에러를 던지면 안 됨 - check_stock()에서 try/except로 잡긴 하지만 스캔이 느려진다)
     - 지표 계산이 필요하면 calculate_macd(), calculate_bollinger_bands(), calculate_rsi()처럼
       "계산 함수(지표 컬럼을 추가한 DataFrame 반환) + 판단 함수(bool 반환)"로 분리하는 패턴을 따른다.
     - 함수 이름과 그 위 docstring에 어떤 매매법인지, 어디서 나온 조건인지(영상 제목 등) 간단히 적어둔다.
     - [중요] OHLCV만으로는 판단할 수 없는 정보(상장주식수, 거래대금 순위, 시장 전체 시황 등)가
       필요하다면, check_stock()/fast_find_eaten_candles()에서 스캔 시작 전에 "전체 시장 기준 1회"만
       계산해서 ohlcv_df에 부가 컬럼(예: 'Shares', 'IsLeading', 'MarketBullish')으로 실어서 넘긴다.
       (아래 has_extreme_volume_bb_breakout_signal / has_leading_stock_signal /
        has_jsj_closing_bet_signal 이 이 패턴의 예시다.) 이렇게 하면 함수 시그니처는
       여전히 ohlcv_df 하나만 받는 형태를 유지할 수 있고, 매 종목마다 추가 API를 호출하지 않아
       스캔 속도가 느려지지 않는다.

  2. 파일 하단의 CONFIRMATION_TECHNIQUES 리스트에 ("기법이름", 함수) 한 줄만 추가한다.
     기법이름은 엑셀의 '충족조건' 컬럼에 그대로 표시된다.

  3. 그 외 코드(check_stock, fast_find_eaten_candles, save_excel, main 등)는 절대 손댈 필요 없다.
     (단, 위 [중요] 항목처럼 부가 컬럼이 필요한 기법을 추가할 때는 예외적으로
      check_stock / fast_find_eaten_candles 에도 "전체 시장 1회 계산" 로직을 추가해야 한다.)
=====================================================================
"""

import os
import ssl
import smtplib
import logging
from email.message import EmailMessage
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import FinanceDataReader as fdr
from openpyxl import Workbook
from openpyxl.styles import Font
from urllib.parse import quote

# =========================================================
# ▼▼▼ 비밀번호는 GitHub Secrets에서 환경변수로 주입됩니다 ▼▼▼
# =========================================================
NAVER_EMAIL = os.environ.get("NAVER_EMAIL", "")
NAVER_PASSWORD = os.environ.get("NAVER_PASSWORD", "")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "")
# =========================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(SCRIPT_DIR, "stock_auto_mail.log")

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8-sig",
)

# =========================================================
# 전체 시장 기준 1회 계산 파라미터 (기법2/기법3에서 사용)
# =========================================================
LEADING_STOCK_TOP_PCT = 0.03      # 거래대금 상위 3%를 '주도주 후보군'으로 정의


# =========================================================
# 매매기법 정의 구역
# =========================================================

def calculate_double_bollinger_bands(df, wb_period=4, wb_std=4, bb_period=20, bb_std=2):
    """
    더블 볼린저 밴드 지표 계산.
    - WB(4,4): 시가(Open) 기준, 단기 극한 변곡·돌파 포착용
    - 기본 BB(20,2): 종가(Close) 기준, 표준 레인지/평균회귀 기준
    """
    out = df.copy()

    wb_source = out['Open'].astype(float)
    wb_ma = wb_source.rolling(window=wb_period).mean()
    wb_stdval = wb_source.rolling(window=wb_period).std()
    out['WB_Mid'] = wb_ma
    out['WB_Upper'] = wb_ma + wb_std * wb_stdval
    out['WB_Lower'] = wb_ma - wb_std * wb_stdval

    bb_source = out['Close'].astype(float)
    bb_ma = bb_source.rolling(window=bb_period).mean()
    bb_stdval = bb_source.rolling(window=bb_period).std()
    out['BB_Mid'] = bb_ma
    out['BB_Upper'] = bb_ma + bb_std * bb_stdval
    out['BB_Lower'] = bb_ma - bb_std * bb_stdval

    return out


def has_double_bollinger_buy_signal(
    ohlcv_df,
    wb_period=4, wb_std=4, bb_period=20, bb_std=2,
    resistance_lookback=20, wick_ratio_threshold=1.5, support_tolerance_pct=1.0,
) -> bool:
    """
    [더블 볼린저 밴드(WB) 매매법] - 유튜브 '김직선 - 나스닥 트레이더' 채널
    https://www.youtube.com/watch?v=t800Joz9GHw

    WB(4,4,Open,빨간선) + 기본 볼린저(20,2,Close,흰선)를 함께 써서
    매수 관점에서 아래 두 패턴 중 하나라도 만족하면 True.

    [패턴 A] 하단 변곡(반전) 매수
      - 오늘 저가가 WB 하단 또는 BB 하단을 터치/이탈
      - 종가는 두 밴드 안쪽으로 복귀 (아래꼬리 긴 반전 마감)
      - 직전 저가(매물대) 대비 유의미한 신저가를 만들지 않음 (돌파 실패 확인)

    [패턴 B] 상단 진짜 돌파 후 추세 매수
      - 몸통(시가->종가)이 WB 상단과 BB 상단을 모두 꽉 채우며 강하게 돌파
      - 직전 고점(매물대, resistance_lookback일 종가 최고가)도 종가 기준 함께 돌파

    데이터 부족 시 조용히 False 반환.
    """
    min_len = max(wb_period, bb_period, resistance_lookback) + 2
    if ohlcv_df is None or len(ohlcv_df) < min_len:
        return False

    df = calculate_double_bollinger_bands(ohlcv_df, wb_period, wb_std, bb_period, bb_std)

    today = df.iloc[-1]

    if pd.isna(today['WB_Lower']) or pd.isna(today['BB_Lower']):
        return False

    # ---------- 패턴 A: 하단 변곡(반전) 매수 ----------
    touched_lower_band = (today['Low'] <= today['WB_Lower']) or (today['Low'] <= today['BB_Lower'])
    closed_back_inside = (today['Close'] > today['WB_Lower']) and (today['Close'] > today['BB_Lower'])

    body_high = max(today['Open'], today['Close'])
    body_low = min(today['Open'], today['Close'])
    body_size = body_high - body_low
    lower_wick = body_low - today['Low']
    has_long_lower_wick = body_size > 0 and (lower_wick > body_size * wick_ratio_threshold)

    prior_low = df['Low'].iloc[-(resistance_lookback + 1):-1].min()
    support_not_broken = today['Low'] >= prior_low * (1 - support_tolerance_pct / 100)

    reversal_pattern = bool(
        touched_lower_band and closed_back_inside and has_long_lower_wick and support_not_broken
    )

    # ---------- 패턴 B: 상단 진짜 돌파 후 추세 매수 ----------
    body_breaks_both_bands = (
        (today['Open'] <= today['WB_Upper']) and (today['Close'] > today['WB_Upper'])
        and (today['Open'] <= today['BB_Upper']) and (today['Close'] > today['BB_Upper'])
    )

    prior_high_close = df['Close'].iloc[-(resistance_lookback + 1):-1].max()
    breaks_resistance = today['Close'] > prior_high_close

    breakout_pattern = bool(body_breaks_both_bands and breaks_resistance)

    return reversal_pattern or breakout_pattern


def has_candle_ma_breakout_signal(ohlcv_df, volume_multiplier=1.5) -> bool:
    """
    [기존 1차 조건] 오늘 양봉이면서 시가는 MA5/MA20 아래, 종가는 MA5/MA20 위로 돌파,
    거래량은 전일 대비 volume_multiplier배 이상 증가.
    """
    if ohlcv_df is None or len(ohlcv_df) < 21:
        return False

    df = ohlcv_df.copy()
    df['MA5'] = df['Close'].rolling(window=5).mean()
    df['MA20'] = df['Close'].rolling(window=20).mean()

    today = df.iloc[-1]
    yesterday = df.iloc[-2]

    is_bullish = today['Close'] > today['Open']
    breaks_ma5 = (today['Open'] < today['MA5']) and (today['Close'] > today['MA5'])
    breaks_ma20 = (today['Open'] < today['MA20']) and (today['Close'] > today['MA20'])
    volume_up = today['Volume'] > (yesterday['Volume'] * volume_multiplier)

    return bool(is_bullish and breaks_ma5 and breaks_ma20 and volume_up)


def has_pullback_after_breakout_signal(
    ohlcv_df,
    ma5_period=5, ma20_period=20, ma60_period=60,
    lookback_days=15, volume_surge_mult=2.0, body_surge_pct=5.0,
    body_damage_tolerance=0.3, ma5_support_tolerance=0.02,
) -> bool:
    """
    [정배열 + 대량거래량 장대양봉 눌림목 기법]
    """
    min_len = ma60_period + lookback_days
    if ohlcv_df is None or len(ohlcv_df) < min_len:
        return False

    df = ohlcv_df.copy()
    df['MA5'] = df['Close'].rolling(window=ma5_period).mean()
    df['MA20'] = df['Close'].rolling(window=ma20_period).mean()
    df['MA60'] = df['Close'].rolling(window=ma60_period).mean()
    df['AvgVolume20'] = df['Volume'].rolling(window=20).mean()
    df['PrevHigh20'] = df['Close'].shift(1).rolling(window=20).max()

    today = df.iloc[-1]
    if pd.isna(today['MA60']):
        return False

    is_uptrend_alignment = (today['MA5'] > today['MA20']) and (today['MA20'] > today['MA60'])
    if not is_uptrend_alignment:
        return False

    recent = df.iloc[-(lookback_days + 1):-1]
    is_bullish = recent['Close'] > recent['Open']
    body_pct = (recent['Close'] - recent['Open']) / recent['Open'] * 100
    volume_surge = recent['Volume'] > (recent['AvgVolume20'] * volume_surge_mult)
    breaks_resistance = recent['Close'] > recent['PrevHigh20']

    breakout_candidates = recent[is_bullish & (body_pct >= body_surge_pct) & volume_surge & breaks_resistance]
    if breakout_candidates.empty:
        return False

    breakout_candle = breakout_candidates.iloc[-1]
    body_low = min(breakout_candle['Open'], breakout_candle['Close'])
    body_high = max(breakout_candle['Open'], breakout_candle['Close'])
    body_range = body_high - body_low
    if body_range <= 0:
        return False

    min_allowed_close = body_low - body_range * body_damage_tolerance
    body_not_damaged = today['Close'] >= min_allowed_close
    near_ma5_support = today['Close'] >= today['MA5'] * (1 - ma5_support_tolerance)
    volume_calmed = today['Volume'] < breakout_candle['Volume']

    return bool(body_not_damaged and near_ma5_support and volume_calmed)


def calculate_macd(df, close_col='Close', fast=12, slow=26, signal=9):
    out = df.copy()
    close = out[close_col].astype(float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    out['MACD'] = ema_fast - ema_slow
    out['Signal'] = out['MACD'].ewm(span=signal, adjust=False).mean()
    out['Histogram'] = out['MACD'] - out['Signal']
    return out


def has_macd_buy_signal(ohlcv_df) -> bool:
    """
    [MACD 기법] 골든크로스 또는 제로라인 상향 돌파
    """
    if ohlcv_df is None or len(ohlcv_df) < 2:
        return False

    df_macd = calculate_macd(ohlcv_df)
    today = df_macd.iloc[-1]
    yesterday = df_macd.iloc[-2]

    golden_cross = (yesterday['MACD'] < yesterday['Signal']) and (today['MACD'] > today['Signal'])
    zero_line_breakout = (yesterday['MACD'] < 0) and (today['MACD'] > 0)

    return bool(golden_cross or zero_line_breakout)


def calculate_bollinger_bands(df, close_col='Close', period=20, num_std=2):
    out = df.copy()
    close = out[close_col].astype(float)
    ma = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    out['BB_Mid'] = ma
    out['BB_Upper'] = ma + num_std * std
    out['BB_Lower'] = ma - num_std * std
    return out


def calculate_rsi(df, close_col='Close', period=14):
    out = df.copy()
    delta = out[close_col].astype(float).diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs = gain / loss
    out['RSI'] = 100 - (100 / (1 + rs))
    return out


def has_bollinger_rsi_buy_signal(ohlcv_df, bb_period=20, bb_std=2, rsi_period=14, rsi_buy_threshold=25) -> bool:
    """
    [볼린저밴드 + RSI 기법]
    """
    min_len = max(bb_period, rsi_period) + 1
    if ohlcv_df is None or len(ohlcv_df) < min_len:
        return False

    df_bb = calculate_bollinger_bands(ohlcv_df, period=bb_period, num_std=bb_std)
    df_rsi = calculate_rsi(ohlcv_df, period=rsi_period)

    today_close = df_bb['Close'].iloc[-1]
    today_lower = df_bb['BB_Lower'].iloc[-1]
    today_rsi = df_rsi['RSI'].iloc[-1]

    if pd.isna(today_lower) or pd.isna(today_rsi):
        return False

    price_below_lower_band = today_close < today_lower
    rsi_oversold = today_rsi <= rsi_buy_threshold

    return bool(price_below_lower_band and rsi_oversold)


def has_extreme_volume_bb_breakout_signal(
    ohlcv_df,
    bb_period=20, bb_std=1,
    volume_surge_mult=7.0, year_high_lookback=240,
    wick_body_ratio_max=1.0, turnover_min_pct=5.0,
) -> bool:
    """
    [매매기법1] 역대급 거래량 + 볼린저(20,1) 상단 강돌파

    - 역대급 거래량: 최근 year_high_lookback(기본 240봉=1년) 중 최고 거래량이거나,
      20일 평균 거래량 대비 volume_surge_mult(기본 700%) 이상 급증.
    - 볼린저 밴드(20,1) 상단선을 강하게 돌파하는 양봉.
    - 캔들 모양: 위꼬리가 몸통보다 길지 않음(짧거나 비등).
    - 회전율(거래량/상장주식수)이 높은 종목 우대.
      * 회전율 계산에는 상장주식수가 필요하므로, check_stock()에서 ohlcv_df에
        'Shares'(상장주식수) 컬럼을 미리 실어서 넘긴다. 'Shares' 컬럼이 없으면
        (데이터 미제공 종목 등) 회전율 조건은 건너뛰고 나머지 조건만으로 판단한다.

    데이터 부족 시 조용히 False 반환.
    """
    min_len = max(bb_period, 21) + 1
    if ohlcv_df is None or len(ohlcv_df) < min_len:
        return False

    df = calculate_bollinger_bands(ohlcv_df, period=bb_period, num_std=bb_std)
    today = df.iloc[-1]

    if pd.isna(today['BB_Upper']):
        return False

    is_bullish = today['Close'] > today['Open']

    # ---------- 역대급 거래량 ----------
    vol_window = df['Volume'].iloc[-min(year_high_lookback, len(df)):]
    is_year_high_volume = bool(today['Volume'] >= vol_window.max())

    avg20_volume = df['Volume'].iloc[-21:-1].mean()
    is_volume_surge = bool(today['Volume'] >= avg20_volume * volume_surge_mult)

    extreme_volume = is_year_high_volume or is_volume_surge

    # ---------- 볼린저(20,1) 상단 강돌파 ----------
    strong_bb_breakout = bool(today['Close'] > today['BB_Upper'])

    # ---------- 캔들 모양 (위꼬리 <= 몸통) ----------
    body_size = today['Close'] - today['Open']
    upper_wick = today['High'] - max(today['Open'], today['Close'])
    clean_candle = bool(body_size > 0 and upper_wick <= body_size * wick_body_ratio_max)

    # ---------- 회전율 (상장주식수 있을 때만 체크) ----------
    turnover_ok = True
    if 'Shares' in ohlcv_df.columns:
        shares = ohlcv_df['Shares'].iloc[-1]
        if pd.notna(shares) and shares > 0:
            turnover_rate = today['Volume'] / shares * 100
            turnover_ok = bool(turnover_rate >= turnover_min_pct)

    return bool(is_bullish and extreme_volume and strong_bb_breakout and clean_candle and turnover_ok)


def has_leading_stock_signal(
    ohlcv_df,
    benchmark_lookback=20, ma5_period=5, ma10_period=10,
    support_tolerance_pct=2.0, close_strength_pct=70.0,
    require_leading=True,
) -> bool:
    """
    [매매기법2] 주도주(거래대금 상위) 기준봉 돌파 / 눌림목 지지

    원본 기법의 "주도 테마·재료" 여부는 뉴스/공시 데이터가 없어 자동 판단이 불가능하므로
    제외했고, 대신 '당일 거래대금 상위 종목군에 속하는지'로 대체했다.
    (상위 그룹 판정은 fast_find_eaten_candles()에서 전체 시장을 대상으로 1회만 계산해
     ohlcv_df에 'IsLeading' 컬럼으로 실어서 넘긴다.)

    아래 두 패턴 중 하나라도 만족하면 True.
    [패턴 A] 최근 benchmark_lookback일 내 거래량이 가장 컸던 '기준봉'의 고점을
             오늘 종가가 강하게(고가권 마감) 돌파.
    [패턴 B] 5일/10일 이동평균선 부근에서 지지받으며 고가권으로 마감(눌림목).

    데이터 부족 또는 'IsLeading'이 아닌 종목은(require_leading=True일 때) False.
    """
    min_len = max(benchmark_lookback, ma10_period) + 2
    if ohlcv_df is None or len(ohlcv_df) < min_len:
        return False

    if require_leading:
        if 'IsLeading' not in ohlcv_df.columns or not bool(ohlcv_df['IsLeading'].iloc[-1]):
            return False

    df = ohlcv_df.copy()
    df['MA5'] = df['Close'].rolling(window=ma5_period).mean()
    df['MA10'] = df['Close'].rolling(window=ma10_period).mean()

    today = df.iloc[-1]

    day_range = today['High'] - today['Low']
    close_position_pct = ((today['Close'] - today['Low']) / day_range * 100) if day_range > 0 else 100.0
    strong_close = bool(close_position_pct >= close_strength_pct)

    # ---------- 패턴 A: 기준봉 고점 돌파 ----------
    recent = df.iloc[-(benchmark_lookback + 1):-1]
    pattern_a = False
    if not recent.empty and recent['Volume'].notna().any():
        benchmark_idx = recent['Volume'].idxmax()
        benchmark_high = recent.loc[benchmark_idx, 'High']
        pattern_a = bool(today['Close'] > benchmark_high and today['Close'] > today['Open'] and strong_close)

    # ---------- 패턴 B: 이동평균선 눌림목 지지 ----------
    pattern_b = False
    if pd.notna(today['MA5']) and pd.notna(today['MA10']):
        near_ma_support = (
            today['Close'] >= today['MA5'] * (1 - support_tolerance_pct / 100)
        ) or (
            today['Close'] >= today['MA10'] * (1 - support_tolerance_pct / 100)
        )
        pattern_b = bool(near_ma_support and strong_close)

    return pattern_a or pattern_b


def has_jsj_closing_bet_signal(
    ohlcv_df,
    new_high_lookback=120, gap_tolerance_pct=3.0,
    upper_wick_max_pct=15.0, volume_surge_mult=3.0,
    consolidation_period=60, consolidation_range_max_pct=20.0,
    require_market_bullish=True,
) -> bool:
    """
    [매매기법3] 신정재 종가베팅 (기술적 조건만 구현)

    원문의 6가지 조건 중 아래 4가지는 OHLCV로 판단 가능해 구현했다.
      1) 신고가/전고점 돌파
      2) 전고점과의 이격거리가 좁음(멀리 갭 뜨지 않은 돌파)
      3) 위꼬리가 거의 없는 깔끔한 양봉
      4) 평소 대비 거래량 급증
      5) 충분한 기간조정(consolidation_period일 레인지가 좁았는지) 후 상승

    "재료(뉴스·테마)"는 자동 판단이 불가능해 제외했다.
    "시황(지수 5일선)"은 스캔 시작 전 전체 시장 기준 1회만 계산해 'MarketBullish'
    컬럼으로 실어서 넘기며, require_market_bullish=True일 때만 조건에 반영한다.
    "수급(외국인/기관 순매수)"은 이번 구현에서는 제외했다 (안정성 우선, pykrx 등
    별도 의존성 필요 - 추후 필요하면 이 함수에 조건을 추가하면 된다).

    데이터 부족 시 조용히 False 반환.
    """
    min_len = max(new_high_lookback, consolidation_period) + 5
    if ohlcv_df is None or len(ohlcv_df) < min_len:
        return False

    df = ohlcv_df
    today = df.iloc[-1]

    # ---------- 신고가 + 이격거리 ----------
    prior = df.iloc[-(new_high_lookback + 1):-1]
    prior_high = prior['Close'].max()
    if pd.isna(prior_high) or prior_high <= 0:
        return False

    is_new_high = bool(today['Close'] > prior_high)
    if not is_new_high:
        return False

    gap_pct = (today['Close'] - prior_high) / prior_high * 100
    narrow_gap = bool(gap_pct <= gap_tolerance_pct)

    # ---------- 깔끔한 양봉 ----------
    body_size = today['Close'] - today['Open']
    upper_wick = today['High'] - max(today['Open'], today['Close'])
    clean_candle = bool(body_size > 0 and upper_wick <= body_size * (upper_wick_max_pct / 100))

    # ---------- 거래량 급증 ----------
    avg20_volume = df['Volume'].iloc[-21:-1].mean()
    volume_surge = bool(today['Volume'] >= avg20_volume * volume_surge_mult)

    # ---------- 기간조정 (돌파 직전 consolidation_period일 레인지) ----------
    consolidation_window = df.iloc[-(consolidation_period + 1):-1]
    cons_high = consolidation_window['Close'].max()
    cons_low = consolidation_window['Close'].min()
    had_consolidation = False
    if pd.notna(cons_high) and pd.notna(cons_low) and cons_low > 0:
        cons_range_pct = (cons_high - cons_low) / cons_low * 100
        had_consolidation = bool(cons_range_pct <= consolidation_range_max_pct)

    # ---------- 시황 (지수, 있을 때만 체크) ----------
    market_ok = True
    if require_market_bullish and 'MarketBullish' in ohlcv_df.columns:
        market_ok = bool(ohlcv_df['MarketBullish'].iloc[-1])

    return bool(narrow_gap and clean_candle and volume_surge and had_consolidation and market_ok)


CONFIRMATION_TECHNIQUES = [
    ("양봉+MA돌파+거래량", has_candle_ma_breakout_signal),
    ("MACD", has_macd_buy_signal),
    ("볼린저밴드+RSI", has_bollinger_rsi_buy_signal),
    ("정배열+장대양봉눌림목", has_pullback_after_breakout_signal),
    ("더블볼린저밴드(WB)", has_double_bollinger_buy_signal),
    ("역대급거래량+볼린저상단강돌파", has_extreme_volume_bb_breakout_signal),  # 매매기법1
    ("주도주+거래대금상위", has_leading_stock_signal),                        # 매매기법2
    ("신정재종가베팅", has_jsj_closing_bet_signal),                           # 매매기법3
]


def check_stock(row, start_date, end_date, market_bullish=True):
    code = row['Code']
    name = row['Name']
    try:
        df = fdr.DataReader(code, start=start_date, end=end_date)
        if len(df) < 25:
            return None

        ohlcv = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()

        # ---- 기법1/2/3에서 필요한 부가 정보 주입 (전체 시장 기준 1회 계산된 값) ----
        shares = row.get('Stocks', None)
        if pd.notna(shares) and shares:
            ohlcv['Shares'] = shares
        ohlcv['IsLeading'] = bool(row.get('IsLeading', False))
        ohlcv['MarketBullish'] = bool(market_bullish)

        today = df.iloc[-1]
        yesterday = df.iloc[-2]

        matched_names = []
        for tech_name, check_fn in CONFIRMATION_TECHNIQUES:
            try:
                if check_fn(ohlcv):
                    matched_names.append(tech_name)
            except Exception as e:
                logging.warning(f"[{tech_name} 확인 오류] {name}({code}): {e}")

        if not matched_names:
            return None

        change_rate = round(((today['Close'] - yesterday['Close']) / yesterday['Close']) * 100, 2)
        return {
            '종목코드': code,
            '종목명': name,
            '시장': row['Market'],
            '종가': int(today['Close']),
            '등락률(%)': change_rate,
            '거래량': int(today['Volume']),
            '매수신호': len(matched_names),
            '충족조건': ", ".join(matched_names),
        }
    except Exception:
        pass
    return None


def get_market_bullish_flag(end_date: str) -> bool:
    """
    [기법3용] 코스피 지수가 5일 이동평균선 위에 있는지(시황 상승/반등 여부)를
    스캔 시작 전 딱 1번만 조회해서 판단한다. 조회 실패 시 안전하게 True(조건 미적용)로
    처리해서 전체 스캔이 실패하지 않도록 한다.
    """
    try:
        start = (datetime.strptime(end_date, '%Y-%m-%d') - timedelta(days=30)).strftime('%Y-%m-%d')
        idx_df = fdr.DataReader('KS11', start=start, end=end_date)
        idx_df['MA5'] = idx_df['Close'].rolling(window=5).mean()
        last = idx_df.iloc[-1]
        if pd.isna(last['MA5']):
            return True
        return bool(last['Close'] >= last['MA5'])
    except Exception as e:
        logging.warning(f"코스피 지수 조회 실패, 시황 조건 기본값(True)으로 진행: {e}")
        return True


def fast_find_eaten_candles(max_workers=20):
    logging.info("전 종목 목록을 불러오는 중...")
    stocks = fdr.StockListing('KRX')
    stocks = stocks[stocks['Market'].isin(['KOSPI', 'KOSDAQ'])].copy()

    end_date = datetime.today().strftime('%Y-%m-%d')
    start_date = (datetime.today() - timedelta(days=200)).strftime('%Y-%m-%d')

    # ---- [기법2용] 거래대금(Amount) 상위 LEADING_STOCK_TOP_PCT 비율을 '주도주 후보군'으로 산정 ----
    # fdr.StockListing('KRX')가 이미 당일 거래대금을 담고 있어 추가 네트워크 호출이 필요 없다.
    if 'Amount' in stocks.columns:
        threshold = stocks['Amount'].quantile(1 - LEADING_STOCK_TOP_PCT)
        stocks['IsLeading'] = stocks['Amount'] >= threshold
    else:
        logging.warning("종목 목록에 'Amount'(거래대금) 컬럼이 없어 주도주 필터를 비활성화합니다.")
        stocks['IsLeading'] = False

    # ---- [기법3용] 코스피 시황(5일선) 1회 계산 ----
    market_bullish = get_market_bullish_flag(end_date)
    logging.info(f"코스피 시황(5일선 기준 상승 여부): {market_bullish}")

    results = []
    logging.info(f"총 {len(stocks)}개 종목을 {max_workers}개 스레드로 탐색합니다...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(check_stock, row, start_date, end_date, market_bullish)
            for _, row in stocks.iterrows()
        ]
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
                logging.info(f"[1차 포착] {res['종목명']}({res['종목코드']}) | 등락률: {res['등락률(%)']}%")

    return pd.DataFrame(results)


def save_excel(df: pd.DataFrame) -> str:
    save_path = os.path.join(SCRIPT_DIR, f"양봉포착_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx")

    wb = Workbook()
    ws = wb.active
    ws.title = "포착종목"

    headers = ["종목명", "종목코드", "시장", "종가", "등락률(%)", "거래량", "매수신호", "충족조건"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    if not df.empty:
        for _, row in df.iterrows():
            ws.append([
                row["종목명"], row["종목코드"], row["시장"],
                row["종가"], row["등락률(%)"], row["거래량"], row["매수신호"], row["충족조건"],
            ])
            r = ws.max_row

            name_cell = ws.cell(row=r, column=1)
            name_cell.hyperlink = f"https://finance.naver.com/item/main.naver?code={row['종목코드']}"
            name_cell.font = Font(color="0563C1", underline="single")

            code_cell = ws.cell(row=r, column=2)
            youtube_url = f"https://www.youtube.com/results?search_query={quote(row['종목명'] + ' 주가')}"
            code_cell.hyperlink = youtube_url
            code_cell.font = Font(color="FF0000", underline="single")

    for col_cells in ws.columns:
        length = max(len(str(c.value)) for c in col_cells if c.value is not None)
        ws.column_dimensions[col_cells[0].column_letter].width = max(10, length + 4)

    wb.save(save_path)
    return save_path


def send_mail(excel_path: str, stock_count: int):
    today_str = datetime.now().strftime('%Y-%m-%d')
    msg = EmailMessage()
    msg["Subject"] = f"[양봉 포착 결과] {today_str} - 총 {stock_count}건"
    msg["From"] = NAVER_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg.set_content(
        f"{today_str} 스캔 결과입니다.\n"
        f"총 {stock_count}개 종목이 포착되었습니다.\n"
        f"첨부된 엑셀 파일을 확인해주세요.\n"
        f"(종목명 클릭 시 네이버 차트로, 종목코드 클릭 시 유튜브 검색으로 이동합니다)"
    )

    with open(excel_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=os.path.basename(excel_path),
        )

    context = ssl.create_default_context()
    with smtplib.SMTP("smtp.naver.com", 587) as server:
        server.starttls(context=context)
        server.login(NAVER_EMAIL, NAVER_PASSWORD)
        server.send_message(msg)


def main():
    start_time = datetime.now()
    logging.info("===== 자동 실행 시작 =====")
    try:
        df_result = fast_find_eaten_candles(max_workers=20)
        excel_path = save_excel(df_result)
        logging.info(f"엑셀 저장 완료: {excel_path}")

        send_mail(excel_path, len(df_result))
        logging.info("메일 발송 완료")
    except Exception as e:
        logging.exception(f"실행 중 오류 발생: {e}")
    finally:
        logging.info(f"소요 시간: {datetime.now() - start_time}")
        logging.info("===== 자동 실행 종료 =====\n")


if __name__ == "__main__":
    main()
