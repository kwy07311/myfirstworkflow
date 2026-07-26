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
       (has_candle_ma_breakout_signal, has_macd_buy_signal, has_bollinger_rsi_buy_signal)이
       전부 이 패턴을 따르고 있으니, 그대로 복사해서 참고하면 된다.
     - 첫 번째 인자 ohlcv_df는 'Open','High','Low','Close','Volume' 컬럼을 가진 pandas
       DataFrame이며, 인덱스는 날짜(과거->최근 순), 마지막 행(iloc[-1])이 가장 최근 거래일이다.
     - 데이터가 부족하면(예: 계산에 필요한 기간보다 짧으면) 조용히 False를 반환해야 한다.
       (에러를 던지면 안 됨 - check_stock()에서 try/except로 잡긴 하지만 스캔이 느려진다)
     - 지표 계산이 필요하면 calculate_macd(), calculate_bollinger_bands(), calculate_rsi()처럼
       "계산 함수(지표 컬럼을 추가한 DataFrame 반환) + 판단 함수(bool 반환)"로 분리하는 패턴을 따른다.
     - 함수 이름과 그 위 docstring에 어떤 매매법인지, 어디서 나온 조건인지(영상 제목 등) 간단히 적어둔다.

  2. 파일 하단의 CONFIRMATION_TECHNIQUES 리스트에 ("기법이름", 함수) 한 줄만 추가한다.
     기법이름은 엑셀의 '충족조건' 컬럼에 그대로 표시된다.

  3. 그 외 코드(check_stock, fast_find_eaten_candles, save_excel, main 등)는 절대 손댈 필요 없다.
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


CONFIRMATION_TECHNIQUES = [
    ("양봉+MA돌파+거래량", has_candle_ma_breakout_signal),
    ("MACD", has_macd_buy_signal),
    ("볼린저밴드+RSI", has_bollinger_rsi_buy_signal),
    ("정배열+장대양봉눌림목", has_pullback_after_breakout_signal),
    ("더블볼린저밴드(WB)", has_double_bollinger_buy_signal),  # 신규 추가
]


def check_stock(row, start_date, end_date):
    code = row['Code']
    name = row['Name']
    try:
        df = fdr.DataReader(code, start=start_date, end=end_date)
        if len(df) < 25:
            return None

        ohlcv = df[['Open', 'High', 'Low', 'Close', 'Volume']]
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


def fast_find_eaten_candles(max_workers=20):
    logging.info("전 종목 목록을 불러오는 중...")
    stocks = fdr.StockListing('KRX')
    stocks = stocks[stocks['Market'].isin(['KOSPI', 'KOSDAQ'])]

    end_date = datetime.today().strftime('%Y-%m-%d')
    start_date = (datetime.today() - timedelta(days=200)).strftime('%Y-%m-%d')

    results = []
    logging.info(f"총 {len(stocks)}개 종목을 {max_workers}개 스레드로 탐색합니다...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(check_stock, row, start_date, end_date) for _, row in stocks.iterrows()]
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
