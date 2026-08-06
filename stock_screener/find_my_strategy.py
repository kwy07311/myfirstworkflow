import os
import json
import time
import threading
import requests
import pandas as pd
import numpy as np

from datetime import datetime, timezone, timedelta
from requests.adapters import HTTPAdapter


# ==================================
# 환경 설정
# ==================================

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

REAL_URL = "https://openapi.koreainvestment.com:9443"

HISTORY_FILE = "input/mydata2.xlsx"
RESULT_JSON = "../docs/screener_data.json"   # 기존 daily_update의 ../docs/data.json과 겹치지 않도록 파일명 분리

# 토큰 캐시 파일 (stock_screener 폴더 내부에서 독립적으로 관리)
# daily_update의 .token_state.json과는 완전히 별개 파일 (서로 영향 없음)
TOKEN_STATE_FILE = ".token_state.json"

# 만료 판단시 안전마진(분)
TOKEN_SAFETY_MARGIN_MIN = 30

# 순차 처리이므로 커넥션 풀은 1개면 충분
MAX_WORKERS = 1

# 초당 허용 호출 수 (EGW00201 재발 방지를 위해 보수적으로 설정)
RATE_LIMIT_PER_SEC = 10

# 관심종목(멀티종목) 시세조회 API는 1회 호출에 최대 30종목까지 지원
BATCH_SIZE = 30

# 이동평균선 계산 기간 (거래일 기준)
MA_PERIOD = 5

# 이평선 하향 추세 판단 기준: 오늘 MA5 값을 며칠 전 MA5 값과 비교할지 (거래일 기준)
TREND_LOOKBACK_DAYS = 30

# 캔들 몸통 평균 계산 기간 (거래일 기준)
BODY_LOOKBACK_DAYS = 30

# 몸통 평균 계산에 필요한 최소 과거 데이터 일수 (이보다 적으면 판단 불가로 제외)
MIN_BODY_HISTORY_DAYS = 5

# 디버그: 첫 배치에서 API 원본 응답 필드를 한 번 출력할지 여부
DEBUG_PRINT_RAW_OUTPUT = False


# ==================================
# 커넥션 재사용 (Session + Connection Pool)
# ==================================

_session = requests.Session()
_adapter = HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
_session.mount("https://", _adapter)


# ==================================
# 초당 호출 수 제한 (Rate Limiter)
# ==================================

class RateLimiter:
    def __init__(self, calls_per_sec):
        self.interval = 1.0 / calls_per_sec
        self.lock = threading.Lock()
        self.last_call = 0.0

    def wait(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_call
            if elapsed < self.interval:
                time.sleep(self.interval - elapsed)
            self.last_call = time.time()


_rate_limiter = RateLimiter(RATE_LIMIT_PER_SEC)


# ==================================
# 텔레그램 전송
# ==================================

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    response = requests.post(url, data=data, timeout=10)
    response.raise_for_status()


def send_telegram_long(header, lines, chunk_char_limit=3500):
    """
    텔레그램 메시지 4096자 제한 대응.
    header + lines를 chunk_char_limit 기준으로 여러 메시지로 쪼개서 순차 전송.
    """
    if not lines:
        send_telegram(header)
        return

    chunks = []
    current = header + "\n\n"

    for line in lines:
        if len(current) + len(line) > chunk_char_limit:
            chunks.append(current)
            current = ""
        current += line

    if current:
        chunks.append(current)

    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        prefix = f"[{idx}/{total}]\n" if total > 1 else ""
        send_telegram(prefix + chunk)
        time.sleep(0.5)  # 텔레그램 API 연속 호출 방지용 짧은 대기


# ==================================
# 결과 JSON 저장 (웹페이지에서 사용)
# ==================================

def save_result_json(result):
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)

    stock_list = result["name"].tolist() if len(result) > 0 else []

    data = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "stocks": stock_list
    }

    os.makedirs(os.path.dirname(RESULT_JSON), exist_ok=True)

    with open(RESULT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"결과 JSON 저장 완료 (KST 시간: {now.strftime('%Y-%m-%d %H:%M:%S')}) : {RESULT_JSON}")


# ==================================
# 토큰 발급 (캐싱 적용)
# ==================================

def _load_cached_token():
    """캐시 파일에서 유효한 토큰을 읽어온다. 없거나 만료됐으면 None 반환."""
    if not os.path.exists(TOKEN_STATE_FILE):
        return None

    try:
        with open(TOKEN_STATE_FILE, "r", encoding="utf-8") as f:
            cached = json.load(f)

        token = cached.get("access_token")
        expire_at_str = cached.get("expire_at")
        if not token or not expire_at_str:
            return None

        expire_at = datetime.fromisoformat(expire_at_str)
        now = datetime.now(timezone.utc)
        remaining = expire_at - now

        if remaining > timedelta(minutes=TOKEN_SAFETY_MARGIN_MIN):
            print(f"캐시된 토큰 재사용 (만료까지 약 {remaining}남음)")
            return token
        else:
            print("캐시된 토큰이 곧 만료되거나 이미 만료됨 → 재발급 진행")
            return None

    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"토큰 캐시 파일 파싱 실패({e}) → 재발급 진행")
        return None


def _issue_new_token():
    """KIS 서버에 실제로 새 토큰을 요청하고 캐시 파일에 저장한다."""
    url = f"{REAL_URL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    response = requests.post(url, json=body, timeout=10)
    response.raise_for_status()
    data = response.json()

    token = data["access_token"]

    expire_at = datetime.now(timezone.utc) + timedelta(hours=23)

    with open(TOKEN_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "access_token": token,
            "expire_at": expire_at.isoformat()
        }, f)

    print(f"새 토큰 발급 완료 (만료 예정: {expire_at.isoformat()})")
    return token


def get_access_token():
    cached = _load_cached_token()
    if cached:
        return cached
    return _issue_new_token()


# ==================================
# 종목코드 추출
# 삼성전자_005930
# ==================================

def extract_code(name):
    return str(name).split("_")[-1]


# ==================================
# 종목 리스트를 BATCH_SIZE 단위로 분할
# ==================================

def chunk_list(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ==================================
# 현재가 및 당일 시가/고가/저가 조회 (최대 30종목/1회)
# ==================================

def get_stock_price_batch(token, name_batch):
    code_to_name = {extract_code(name): name for name in name_batch}

    url = f"{REAL_URL}/uapi/domestic-stock/v1/quotations/intstock-multprice"

    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST11300006"
    }

    params = {}
    for idx, name in enumerate(name_batch, start=1):
        code = extract_code(name)
        params[f"FID_COND_MRKT_DIV_CODE_{idx}"] = "J"
        params[f"FID_INPUT_ISCD_{idx}"] = code

    for retry in range(3):
        try:
            _rate_limiter.wait()
            response = _session.get(url, headers=headers, params=params, timeout=10)
            data = response.json()

            if "output" not in data:
                raise RuntimeError(
                    f"HTTP {response.status_code} / "
                    f"rt_cd={data.get('rt_cd')} "
                    f"msg_cd={data.get('msg_cd')} "
                    f"msg1={data.get('msg1')}"
                )

            outputs = data["output"]

            if DEBUG_PRINT_RAW_OUTPUT and not getattr(get_stock_price_batch, "_printed", False):
                print("=" * 40)
                print("DEBUG: API 원본 응답 (첫 종목 전체 필드)")
                print(outputs[0])
                print("=" * 40)
                get_stock_price_batch._printed = True

            results = []

            for output in outputs:
                code = output["inter_shrn_iscd"]
                name = code_to_name.get(code)
                if name is None:
                    continue

                results.append({
                    "name": name,
                    "code": code,
                    "open": float(output["inter2_oprc"]),
                    "high": float(output["inter2_hgpr"]),
                    "low": float(output["inter2_lwpr"]),
                    "current_price": float(output["inter2_prpr"]),
                    "volume": float(output.get("acml_vol", 0))
                })

            return results

        except Exception as e:
            if retry < 2:
                time.sleep(0.5 * (retry + 1))
            else:
                msg = str(e)
                if "EGW00121" in msg or "기간이 만료" in msg or "인증" in msg:
                    if os.path.exists(TOKEN_STATE_FILE):
                        os.remove(TOKEN_STATE_FILE)
                        print("토큰 인증 오류 감지 → 캐시 파일 삭제(다음 실행 시 재발급)")
                return [{"name": name, "error": str(e)} for name in name_batch]


# ==================================
# 과거 데이터에서 종가 시계열 추출 + 5일 이평선 추세 판단
# 셀 형식: "고가_시가_저가_종가_거래량"
# ==================================

def parse_close_series(row, date_columns):
    """오래된 -> 최신 순으로 정렬된 종가 리스트 반환"""
    closes = []

    for col in date_columns:
        val = row[col]

        if pd.isna(val):
            continue

        val = str(val)

        if "_" not in val:
            continue

        parts = val.split("_")

        if len(parts) != 5:
            continue

        try:
            close = float(parts[3])  # 고가_시가_저가_종가_거래량 -> index 3
            closes.append(close)
        except ValueError:
            continue

    return closes


def parse_body_series(row, date_columns):
    """오래된 -> 최신 순으로 정렬된 캔들 몸통 크기(|종가-시가|) 리스트 반환"""
    bodies = []

    for col in date_columns:
        val = row[col]

        if pd.isna(val):
            continue

        val = str(val)

        if "_" not in val:
            continue

        parts = val.split("_")

        if len(parts) != 5:
            continue

        try:
            open_ = float(parts[1])  # 고가_시가_저가_종가_거래량 -> index 1
            close = float(parts[3])  # index 3
            bodies.append(abs(close - open_))
        except ValueError:
            continue

    return bodies


def calc_ma_trend(closes):
    """
    최근 TREND_LOOKBACK_DAYS(기본 30)거래일 동안의 5일 이동평균선을
    선형회귀로 추세선을 그어 그 기울기(slope)로 하향/상향 판단.
    (개별 날짜의 양봉/음봉 여부는 무관 - MA5 라인 자체의 전반적인 방향만 본다)

    closes 리스트는 "오늘"을 제외한 어제까지의 종가(오래된 -> 최신 순)라고 가정.
    즉 이동평균선의 마지막 지점은 "어제"의 MA5 값이고,
    그 지점으로부터 TREND_LOOKBACK_DAYS거래일 전까지의 구간 기울기를 본다.

    데이터가 부족하면 'insufficient', 아니면 'down' / 'up' / 'flat' 반환.
    """
    # TREND_LOOKBACK_DAYS개의 "유효한" MA5 값을 얻으려면
    # 최소 MA_PERIOD + TREND_LOOKBACK_DAYS - 1개의 종가가 필요
    required_len = MA_PERIOD + TREND_LOOKBACK_DAYS - 1

    if len(closes) < required_len:
        return "insufficient"

    ma = pd.Series(closes).rolling(window=MA_PERIOD).mean().dropna()

    if len(ma) < TREND_LOOKBACK_DAYS:
        return "insufficient"

    # 최근 TREND_LOOKBACK_DAYS개의 MA5 값(어제까지)로 추세선 기울기 계산
    ma_window = ma.iloc[-TREND_LOOKBACK_DAYS:].values
    x = np.arange(len(ma_window))

    slope, _ = np.polyfit(x, ma_window, 1)

    if slope < 0:
        return "down"
    elif slope > 0:
        return "up"
    return "flat"


def calc_max_body(bodies, lookback=BODY_LOOKBACK_DAYS):
    """
    최근 lookback(기본 30)거래일 중 가장 큰 캔들 몸통 크기 반환.
    데이터가 MIN_BODY_HISTORY_DAYS보다 적으면 None 반환(판단 불가).
    """
    recent = bodies[-lookback:] if len(bodies) >= lookback else bodies

    if len(recent) < MIN_BODY_HISTORY_DAYS:
        return None

    return max(recent)


# ==================================
# 메인
# ==================================

def main():
    start_time = time.time()

    print("=" * 40)
    print("5일선 하향(30일) + 양봉 + 몸통 확대 검색 시작")
    print("=" * 40)

    # -------------------------------
    # 과거 데이터 로드
    # -------------------------------
    history = pd.read_excel(HISTORY_FILE)

    all_date_columns = [c for c in history.columns if c != "name"]

    # 오늘 날짜(KST) 컬럼이 이미 들어가 있다면 이평선 계산에서는 제외
    # (daily_add_price.py가 먼저 실행되어 오늘 컬럼이 채워진 뒤에 이 스크립트를 돌리더라도
    #  항상 "오늘 실시간 시세 vs 어제까지의 과거 데이터"로 비교되도록 보장)
    _kst = timezone(timedelta(hours=9))
    _today_kst = datetime.now(_kst).date()

    def _is_today_column(col):
        try:
            return pd.to_datetime(str(col)).date() == _today_kst
        except (ValueError, TypeError):
            return False

    today_column_found = [c for c in all_date_columns if _is_today_column(c)]
    date_columns = [c for c in all_date_columns if not _is_today_column(c)]

    if today_column_found:
        print(f"오늘 날짜 컬럼 감지({today_column_found}) → 비교 대상에서 제외하고 어제까지 데이터로 계산합니다.")
    else:
        print("오늘 날짜 컬럼 없음 → 전체 과거 데이터로 계산합니다.")

    # -------------------------------
    # 종목별 5일 이평선 추세(30거래일 전 대비) + 30거래일 최대 캔들 몸통 계산
    # -------------------------------
    trends = []
    max_bodies = []
    for _, row in history.iterrows():
        closes = parse_close_series(row, date_columns)
        trend = calc_ma_trend(closes)
        trends.append(trend)

        bodies = parse_body_series(row, date_columns)
        max_body = calc_max_body(bodies)
        max_bodies.append(max_body)

    history["ma_trend"] = trends
    history["max_body"] = max_bodies

    # 이평선 하향 + 최대 몸통 계산 가능(데이터 충분)한 종목만 1차 후보로 선정
    down_trend_stocks = history[
        (history["ma_trend"] == "down") & (history["max_body"].notna())
    ]
    print(f"5일 이평선 하향(30거래일 기준) 종목 수 : {len(down_trend_stocks)}")

    if down_trend_stocks.empty:
        print("이평선 하향 조건을 만족하는 종목이 없습니다.")
        save_result_json(down_trend_stocks)
        send_telegram("📉 오늘 조건 만족 종목 없음 (이평선 하향 종목 자체가 없음)")
        return

    # -------------------------------
    # 이평선 하향 종목만 대상으로 오늘 실시간 시세 조회 (API 호출 최소화)
    # -------------------------------
    token = get_access_token()
    stock_names = down_trend_stocks["name"].tolist()
    max_body_lookup = dict(zip(down_trend_stocks["name"], down_trend_stocks["max_body"]))
    total = len(stock_names)
    print(f"실시간 조회 대상 : {total}개 종목")

    batches = list(chunk_list(stock_names, BATCH_SIZE))
    total_batches = len(batches)
    print(f"{BATCH_SIZE}종목씩 {total_batches}개 배치로 조회")

    results = []
    errors = []
    success = 0
    fail = 0

    for idx, batch in enumerate(batches, start=1):
        batch_results = get_stock_price_batch(token, batch)

        for data in batch_results:
            if "error" in data:
                fail += 1
                errors.append(data["error"])
            else:
                success += 1
                results.append(data)

        print(f"[배치 {idx}/{total_batches}] 조회 완료 (누적 성공:{success}, 실패:{fail})")

    today = pd.DataFrame(results)

    if today.empty:
        print("조회된 당일 시세 데이터가 없습니다.")
        save_result_json(today)
        send_telegram("📉 오늘 조건 만족 종목 없음 (시세 조회 데이터 없음)")
        return

    # -------------------------------
    # 양봉 판단 (현재가 > 시가) + 오늘 몸통 크기가 30거래일 중 최대 몸통보다 큰지 판단
    # -------------------------------
    today["bullish"] = today["current_price"] > today["open"]
    today["today_body"] = (today["current_price"] - today["open"]).abs()
    today["max_body"] = today["name"].map(max_body_lookup)
    today["body_bigger"] = today["today_body"] > today["max_body"]

    result = today[today["bullish"] & today["body_bigger"]]

    save_result_json(result)

    # -------------------------------
    # 텔레그램 전송
    # -------------------------------
    if len(result) > 0:
        header = f"📉📈 5일선 하향(30일) + 양봉 + 30일 내 최대 몸통 갱신 종목 (총 {len(result)}개)"
        lines = []
        for _, r in result.iterrows():
            print("★", r["name"])
            lines.append(
                f"★ {r['name']} "
                f"(시가 {r['open']:.0f} → 현재가 {r['current_price']:.0f}, "
                f"오늘 몸통 {r['today_body']:.0f} / 30일 내 최대 몸통 {r['max_body']:.0f})\n"
            )
        send_telegram_long(header, lines)
    else:
        send_telegram("📉 오늘 조건 만족 종목 없음 (이평선 하향은 있으나 양봉+몸통 갱신 조건 미충족)")

    print("텔레그램 전송 완료")

    elapsed = time.time() - start_time

    print("=" * 40)
    print(f"조회 성공 : {success}")
    print(f"조회 실패 : {fail}")

    if errors:
        error_counts = {}
        for err in errors:
            key = str(err)[:80]
            error_counts[key] = error_counts.get(key, 0) + 1

        print("-- 실패 유형 상위 5개 --")
        for key, count in sorted(error_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
            print(f"  [{count}건] {key}")

    print(f"최종 추출 종목 : {len(result)}")
    print(f"실행 시간 : {elapsed:.1f}초")
    print("=" * 40)


if __name__ == "__main__":
    main()
