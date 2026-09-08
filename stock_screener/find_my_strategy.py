import os
import json
import time
import threading
import requests
import pandas as pd

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

# 두 기법 모두 OHLCV 셀 포맷("고가_시가_저가_종가_거래량")을 쓰는
# mydata2.xlsx 하나만 사용한다. (mydata.xlsx의 "변동폭_거래량" 포맷은
# mydata2.xlsx의 고가-저가로 그대로 계산 가능한 하위호환 데이터라 별도로 읽지 않음)
HISTORY_FILE = "input/mydata2.xlsx"

# index.html이 fetch하는 파일명과 반드시 일치해야 함.
#   - data.json          -> "breakout" 탭 (📈 변동폭 돌파 = 기법 B)
#   - screener_data.json -> "ma" 탭      (📉 5일선 반전   = 기법 A)
DATA_JSON = "../docs/data.json"
SCREENER_JSON = "../docs/screener_data.json"

# 토큰 캐시 파일 (이 스크립트 전용, 다른 스크립트의 캐시와 분리)
TOKEN_STATE_FILE = ".token_state_find_my_strategy.json"

# 만료 판단시 안전마진(분)
TOKEN_SAFETY_MARGIN_MIN = 30

# 순차 처리이므로 커넥션 풀은 1개면 충분
MAX_WORKERS = 1

# 초당 허용 호출 수 (EGW00201 재발 방지를 위해 보수적으로 설정)
RATE_LIMIT_PER_SEC = 10

# 관심종목(멀티종목) 시세조회 API는 1회 호출에 최대 30종목까지 지원
BATCH_SIZE = 30

# ---- 기법 A: 5일선 거의 하향 + 양봉 + 몸통 확대 ----
MA_PERIOD = 5
TREND_LOOKBACK_DAYS = 55
TREND_DOWN_RATIO = 0.75
BODY_LOOKBACK_DAYS = 30
MIN_BODY_HISTORY_DAYS = 5

# ---- 기법 B: 변동폭 돌파 + 양봉 + 윗꼬리 짧음 + 거래량 스파이크 ----
MIN_VOLUME_HISTORY_DAYS = 3
VOLUME_SPIKE_MULTIPLIER = 1.5
UPPER_SHADOW_MAX_RATIO = 0.1

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
    """텔레그램 메시지 4096자 제한 대응. header + lines를 나눠서 순차 전송."""
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
        time.sleep(0.5)


# ==================================
# 결과 JSON 저장 (웹페이지에서 사용)
# ==================================

def save_result_json(result, output_path):
    """result(DataFrame)를 output_path에 웹페이지가 읽는 포맷으로 저장한다."""
    kst = timezone(timedelta(hours=9))
    now = datetime.now(kst)

    stock_list = result["name"].tolist() if len(result) > 0 else []

    data = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "stocks": stock_list
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"결과 JSON 저장 완료 (KST 시간: {now.strftime('%Y-%m-%d %H:%M:%S')}) : {output_path}")


def save_all_results_json(technique_a_result, technique_b_result):
    """기법 A -> screener_data.json(ma 탭), 기법 B -> data.json(breakout 탭)로 각각 저장."""
    save_result_json(technique_b_result, DATA_JSON)
    save_result_json(technique_a_result, SCREENER_JSON)


# ==================================
# 토큰 발급 (캐싱 적용)
# ==================================

def _load_cached_token():
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
# 종목코드 추출 (삼성전자_005930 -> 005930)
# ==================================

def extract_code(name):
    return str(name).split("_")[-1]


def chunk_list(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


# ==================================
# 현재가 및 당일 시가/고가/저가/거래량 조회 (최대 30종목/1회)
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
# 날짜 칼럼 정규화 (update_excel.py와 동일 방식)
# ==================================

def normalize_date_column(col):
    if isinstance(col, str) and col.strip().lower() == "name":
        return "name"
    if isinstance(col, (pd.Timestamp, datetime)):
        return col.strftime("%y%m%d")
    col_str = str(col).strip()
    if "-" in col_str:
        try:
            parsed = datetime.strptime(col_str, "%y-%m-%d")
            return parsed.strftime("%y%m%d")
        except ValueError:
            pass
    try:
        return str(int(float(col_str))).zfill(6)
    except ValueError:
        return col_str


# ==================================
# 과거 데이터 파싱
# 셀 형식: "고가_시가_저가_종가_거래량"
# ==================================

def parse_history_series(row, date_columns):
    """
    오래된 -> 최신 순으로 정렬된 (close, body, range, volume) 4종 시계열을 반환.
    한 번의 컬럼 순회로 기법 A/B에 필요한 값을 모두 뽑아낸다.
    """
    closes, bodies, ranges, volumes = [], [], [], []

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
            high = float(parts[0])
            open_ = float(parts[1])
            low = float(parts[2])
            close = float(parts[3])
            volume = float(parts[4])
        except ValueError:
            continue

        closes.append(close)
        bodies.append(abs(close - open_))
        ranges.append(high - low)
        volumes.append(volume)

    return closes, bodies, ranges, volumes


def calc_ma_trend(closes):
    """기법 A: 최근 TREND_LOOKBACK_DAYS거래일의 5일 이평선이 '거의' 하향인지 판단."""
    required_len = MA_PERIOD + TREND_LOOKBACK_DAYS - 1
    if len(closes) < required_len:
        return "insufficient"

    ma = pd.Series(closes).rolling(window=MA_PERIOD).mean().dropna()
    if len(ma) < TREND_LOOKBACK_DAYS:
        return "insufficient"

    ma_window = ma.iloc[-TREND_LOOKBACK_DAYS:]
    diffs = ma_window.diff().dropna()
    if len(diffs) == 0:
        return "insufficient"

    down_ratio = (diffs < 0).sum() / len(diffs)
    overall_declined = ma_window.iloc[-1] < ma_window.iloc[0]

    if down_ratio >= TREND_DOWN_RATIO and overall_declined:
        return "down"
    return "not_down"


def calc_avg_body(bodies, lookback=BODY_LOOKBACK_DAYS):
    """기법 A: 최근 lookback거래일의 평균 캔들 몸통 크기."""
    recent = bodies[-lookback:] if len(bodies) >= lookback else bodies
    if len(recent) < MIN_BODY_HISTORY_DAYS:
        return None
    return sum(recent) / len(recent)


def calc_max_range(ranges):
    """기법 B: 과거 최대 변동폭(고가-저가)."""
    return max(ranges) if ranges else 0.0


def calc_avg_volume(volumes):
    """기법 B: 과거 평균 거래량 (데이터 부족 시 0 -> 조건 자동 통과로 처리)."""
    if len(volumes) >= MIN_VOLUME_HISTORY_DAYS:
        return sum(volumes) / len(volumes)
    return 0.0


# ==================================
# 메인
# ==================================

def main():
    start_time = time.time()

    print("=" * 40)
    print("[통합] 5일선 하향+양봉+몸통확대 (기법A) OR 변동폭 돌파+양봉+거래량 스파이크 (기법B)")
    print("=" * 40)

    # -------------------------------
    # 과거 데이터 로드 + 정규화
    # -------------------------------
    history = pd.read_excel(HISTORY_FILE)
    history.columns = [normalize_date_column(c) for c in history.columns]

    if "name" not in history.columns:
        raise ValueError(f"'name' 컬럼을 찾을 수 없습니다. 실제 컬럼: {history.columns.tolist()}")

    all_date_columns = [c for c in history.columns if c != "name"]

    # 오늘 날짜(KST) 컬럼이 이미 채워져 있다면 과거 통계 계산에서는 제외
    # (오늘 실시간 시세 vs 어제까지의 과거 데이터로 항상 비교되도록 보장)
    kst = timezone(timedelta(hours=9))
    today_kst_str = datetime.now(kst).strftime("%y%m%d")
    today_column_found = [c for c in all_date_columns if str(c) == today_kst_str]
    date_columns = [c for c in all_date_columns if str(c) != today_kst_str]

    if today_column_found:
        print(f"오늘 날짜 컬럼 감지({today_column_found}) → 비교 대상에서 제외하고 어제까지 데이터로 계산합니다.")
    else:
        print("오늘 날짜 컬럼 없음 → 전체 과거 데이터로 계산합니다.")

    # -------------------------------
    # 종목별 과거 통계 계산 (기법 A, B에 필요한 값 모두)
    # -------------------------------
    ma_trends, avg_bodies, max_ranges, avg_volumes = [], [], [], []

    for _, row in history.iterrows():
        closes, bodies, ranges, volumes = parse_history_series(row, date_columns)
        ma_trends.append(calc_ma_trend(closes))
        avg_bodies.append(calc_avg_body(bodies))
        max_ranges.append(calc_max_range(ranges))
        avg_volumes.append(calc_avg_volume(volumes))

    history["ma_trend"] = ma_trends
    history["avg_body"] = avg_bodies
    history["max_history_range"] = max_ranges
    history["avg_volume"] = avg_volumes

    print(f"기법A 대상(이평선 하향) 종목 수 : {(history['ma_trend'] == 'down').sum()}")
    print(f"전체 종목 수 : {len(history)}")

    # -------------------------------
    # OR 조건이므로 기법B는 전체 종목을 대상으로 실시간 조회해야 함
    # (기법A처럼 이평선 하향 종목만 선별해 조회량을 줄일 수 없음)
    # -------------------------------
    token = get_access_token()
    stock_names = history["name"].tolist()
    total = len(stock_names)
    print(f"실시간 조회 대상 : {total}개 종목 (전체)")

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
        if errors:
            print("-- 실패 상세 내역 --")
            for err in errors:
                print(" -", err)
        save_all_results_json(today, today)
        send_telegram("📊 오늘 조건 만족 종목 없음 (시세 조회 데이터 없음)")
        return

    # -------------------------------
    # 거래정지 등 이상 데이터 제외
    # -------------------------------
    before_count = len(today)
    today = today[
        (today["open"] > 0)
        & (today["high"] > 0)
        & (today["low"] > 0)
        & (today["current_price"] > 0)
        & (today["volume"] > 0)
    ]
    excluded_count = before_count - len(today)
    if excluded_count > 0:
        print(f"거래정지/이상치 의심으로 제외된 종목 수 : {excluded_count}")

    if today.empty:
        print("거래정지 등 이상치 제외 후 남은 종목이 없습니다.")
        save_all_results_json(today, today)
        send_telegram("📊 오늘 조건 만족 종목 없음 (거래정지 등 제외 후 없음)")
        return

    # -------------------------------
    # 과거 통계와 병합
    # -------------------------------
    merged = today.merge(
        history[["name", "ma_trend", "avg_body", "max_history_range", "avg_volume"]],
        on="name",
        how="inner"
    )

    # 공통: 양봉 여부, 오늘 몸통, 오늘 변동폭
    merged["bullish"] = merged["current_price"] > merged["open"]
    merged["today_body"] = (merged["current_price"] - merged["open"]).abs()
    merged["today_range"] = merged["high"] - merged["low"]

    # ---- 기법 A: 이평선 거의 하향 + 양봉 + 오늘 몸통 > 평균 몸통 ----
    merged["body_bigger"] = (
        merged["avg_body"].notna() & (merged["today_body"] > merged["avg_body"])
    )
    merged["technique_a_match"] = (
        (merged["ma_trend"] == "down") & merged["bullish"] & merged["body_bigger"]
    )

    # ---- 기법 B: 과거 최대 변동폭 돌파 + 양봉 + 윗꼬리 짧음 + 거래량 스파이크 ----
    safe_range = merged["today_range"].replace(0, float("nan"))
    merged["upper_shadow_ratio"] = (merged["high"] - merged["current_price"]) / safe_range
    merged["range_breakout"] = merged["today_range"] > merged["max_history_range"]

    def check_volume_spike(row):
        if row["avg_volume"] <= 0:
            return True  # 과거 거래량 데이터 부족 시 조건 자동 통과
        return row["volume"] >= (row["avg_volume"] * VOLUME_SPIKE_MULTIPLIER)

    merged["volume_spike"] = merged.apply(check_volume_spike, axis=1)
    merged["technique_b_match"] = (
        merged["range_breakout"]
        & merged["bullish"]
        & (merged["upper_shadow_ratio"] < UPPER_SHADOW_MAX_RATIO)
        & merged["volume_spike"]
    )

    # ---- OR 결합 (텔레그램 알림용) ----
    result = merged[merged["technique_a_match"] | merged["technique_b_match"]].copy()

    def _match_label(row):
        if row["technique_a_match"] and row["technique_b_match"]:
            return "A+B"
        if row["technique_a_match"]:
            return "A"
        return "B"

    if len(result) > 0:
        result["matched_by"] = result.apply(_match_label, axis=1)

    # ---- 웹페이지용 JSON은 기법별로 분리해서 저장 ----
    # data.json(breakout 탭)          <- 기법 B만 만족한 종목
    # screener_data.json(ma 탭)       <- 기법 A만 만족한 종목
    technique_a_only = merged[merged["technique_a_match"]].copy()
    technique_b_only = merged[merged["technique_b_match"]].copy()
    save_all_results_json(technique_a_only, technique_b_only)

    # -------------------------------
    # 텔레그램 전송
    # -------------------------------
    if len(result) > 0:
        header = f"📈 통합 스크리너 (기법A 또는 기법B 만족, 총 {len(result)}개)"
        lines = []
        for _, r in result.iterrows():
            print("★", r["name"], f"[{r['matched_by']}]")
            lines.append(
                f"★ [{r['matched_by']}] {r['name']} "
                f"(시가 {r['open']:.0f} → 현재가 {r['current_price']:.0f})\n"
            )
        send_telegram_long(header, lines)
    else:
        send_telegram("📊 오늘 조건 만족 종목 없음 (기법A, 기법B 모두 미충족)")

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

    print(f"기법A만 매칭 : {(merged['technique_a_match'] & ~merged['technique_b_match']).sum()}")
    print(f"기법B만 매칭 : {(merged['technique_b_match'] & ~merged['technique_a_match']).sum()}")
    print(f"A+B 동시 매칭 : {(merged['technique_a_match'] & merged['technique_b_match']).sum()}")
    print(f"최종 추출 종목(OR, 텔레그램 기준) : {len(result)}")
    print("=" * 40)


if __name__ == "__main__":
    main()
