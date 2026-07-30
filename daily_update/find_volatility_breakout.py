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

HISTORY_FILE = "input/mydata.xlsx"
RESULT_JSON = "../docs/data.json"

# 순차 처리이므로 커넥션 풀은 1개면 충분
MAX_WORKERS = 1

# 초당 허용 호출 수 (EGW00201 재발 방지를 위해 보수적으로 설정)
RATE_LIMIT_PER_SEC = 10

# 관심종목(멀티종목) 시세조회 API는 1회 호출에 최대 30종목까지 지원
BATCH_SIZE = 30

# 거래량 평균을 신뢰하기 위해 필요한 최소 과거 데이터 일수
# (이보다 적으면 avg_volume=0으로 처리하여 거래량 조건을 자동 통과시킴)
MIN_VOLUME_HISTORY_DAYS = 3

# 거래량 스파이크 판단 배율 (평균 거래량 대비 몇 배 이상이어야 통과인지)
VOLUME_SPIKE_MULTIPLIER = 1.5

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


# ==================================
# 결과 JSON 저장 (웹페이지에서 사용)
# ==================================

def save_result_json(result):
    # UTC 시각에 +9시간을 적용하여 KST(한국 표준시) 생성
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
# 토큰 발급
# ==================================

def get_access_token():
    url = f"{REAL_URL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    response = requests.post(url, json=body, timeout=10)
    response.raise_for_status()
    return response.json()["access_token"]


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
# 현재가 및 당일 누적 거래량 조회 (최대 30종목/1회)
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

            # ---- 디버그: 최초 1회만 원본 응답 필드 전체 출력 ----
            if DEBUG_PRINT_RAW_OUTPUT and not getattr(get_stock_price_batch, "_printed", False):
                print("=" * 40)
                print("DEBUG: API 원본 응답 (첫 종목 전체 필드)")
                print(outputs[0])
                print("=" * 40)
                get_stock_price_batch._printed = True
            # ------------------------------------------------

            results = []

            for output in outputs:
                code = output["inter_shrn_iscd"]
                name = code_to_name.get(code)
                if name is None:
                    continue

                # acml_vol : 당일 누적 거래량 (※ inter2_acml_vol은 실제 응답에 존재하지 않는 필드였음 - 디버그로 확인됨)
                results.append({
                    "name": name,
                    "code": code,
                    "open": float(output["inter2_oprc"]),
                    "high": float(output["inter2_hgpr"]),
                    "low": float(output["inter2_lwpr"]),
                    "current_price": float(output["inter2_prpr"]),
                    "volume": float(output.get("acml_vol", 0))  # 오늘 누적 거래량 수집
                })

            return results

        except Exception as e:
            if retry < 2:
                time.sleep(0.5 * (retry + 1))
            else:
                return [{"name": name, "error": str(e)} for name in name_batch]


# ==================================
# 메인
# ==================================

def main():
    start_time = time.time()

    print("=" * 40)
    print("변동폭 돌파 검색 시작")
    print("=" * 40)

    # -------------------------------
    # 과거 데이터 및 전체 평균 거래량 계산
    # -------------------------------
    history = pd.read_excel(HISTORY_FILE)

    all_date_columns = [c for c in history.columns if c != "name"]

    # 오늘 날짜(KST)에 해당하는 컬럼이 이미 들어가 있다면
    # 과거 데이터 집계(max_history_range, avg_volume)에서는 제외한다.
    # -> daily_add_price.py가 먼저 실행되어 오늘 컬럼이 채워진 뒤에 이 스크립트를
    #    돌리더라도(장중/장마감 상관없이), 항상 "오늘 실시간 시세 vs 어제까지의
    #    과거 데이터"로 비교되도록 보장한다.
    _kst = timezone(timedelta(hours=9))
    _today_kst = datetime.now(_kst).date()

    def _is_today_column(col):
        try:
            return pd.to_datetime(str(col)).date() == _today_kst
        except (ValueError, TypeError):
            # 날짜로 파싱되지 않는 컬럼명이면 과거 데이터로 취급(제외하지 않음)
            return False

    today_column_found = [c for c in all_date_columns if _is_today_column(c)]
    date_columns = [c for c in all_date_columns if not _is_today_column(c)]

    if today_column_found:
        print(f"오늘 날짜 컬럼 감지({today_column_found}) → 비교 대상에서 제외하고 어제까지 데이터로 계산합니다.")
    else:
        print("오늘 날짜 컬럼 없음 → 전체 과거 데이터로 계산합니다.")

    # 각 행(종목)별로 과거 최대 변동폭과 평균 거래량을 구하는 행 단위 처리 함수
    def parse_history_row(row):
        ranges = []
        vols = []
        for col in date_columns:
            val = str(row[col])
            if val and val != "nan" and "_" in val:
                # "2500_1523000" (변동폭_거래량) 형태
                r, v = val.split("_")
                ranges.append(float(r))
                vols.append(float(v))
            elif val and val != "nan":
                # 기존 데이터(거래량 없이 숫자만 있던 과거 데이터 예외 처리)
                ranges.append(float(val))

        max_range = max(ranges) if ranges else 0.0

        # 거래량 데이터가 MIN_VOLUME_HISTORY_DAYS일 미만으로 쌓여 있으면
        # 아직 "평균"으로서 의미가 없다고 보고 avg_volume=0 (조건 자동 통과) 처리
        if len(vols) >= MIN_VOLUME_HISTORY_DAYS:
            avg_vol = sum(vols) / len(vols)
        else:
            avg_vol = 0.0

        return pd.Series([max_range, avg_vol], index=["max_history_range", "avg_volume"])

    # 행 단위 파싱 실행
    parsed_df = history.apply(parse_history_row, axis=1)
    history["max_history_range"] = parsed_df["max_history_range"]
    history["avg_volume"] = parsed_df["avg_volume"]

    token = get_access_token()
    stock_names = history["name"].tolist()
    total = len(stock_names)
    print(f"총 {total}개 종목 조회 예정")

    batches = list(chunk_list(stock_names, BATCH_SIZE))
    total_batches = len(batches)
    print(f"{BATCH_SIZE}종목씩 {total_batches}개 배치로 조회")

    # -------------------------------
    # 배치 순차 조회
    # -------------------------------
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

    # -------------------------------
    # 조건 비교 (변동폭 돌파 & 양봉 & 윗꼬리 10% 미만 & 거래량 1.5배 이상)
    # -------------------------------
    today = pd.DataFrame(results)

    if today.empty:
        print("조회된 당일 시세 데이터가 없습니다.")
        send_telegram("📊 오늘 조건 만족 변동폭 돌파 종목 없음 (시세 조회 데이터 없음)")
        return

    merged = today.merge(
        history[["name", "max_history_range", "avg_volume"]],
        on="name",
        how="inner"
    )

    # 당일 변동폭 계산
    merged["today_range"] = merged["high"] - merged["low"]

    # 0으로 나누기(DivByZero) 방지 처리
    safe_range = merged["today_range"].replace(0, float('nan'))
    merged["upper_shadow_ratio"] = (merged["high"] - merged["current_price"]) / safe_range

    # 양봉 판단
    merged["bullish"] = merged["current_price"] > merged["open"]

    # 거래량 조건 판단 (과거 거래량 데이터가 충분히 쌓이지 않은 경우는 조건 pass)
    def check_volume_spike(row):
        if row["avg_volume"] <= 0:
            return True  # 과거 거래량 데이터가 없거나 부족하면 분출 여부 판단을 건너뛰고 조건 통과
        return row["volume"] >= (row["avg_volume"] * VOLUME_SPIKE_MULTIPLIER)

    merged["volume_spike"] = merged.apply(check_volume_spike, axis=1)

    # 최종 스크리닝
    result = merged[
        (merged["today_range"] > merged["max_history_range"])  # 과거 최대 변동폭 돌파
        & (merged["bullish"])                                  # 양봉
        & (merged["upper_shadow_ratio"] < 0.1)                 # 윗꼬리 10% 미만
        & (merged["volume_spike"])                             # 과거 평균 대비 1.5배 이상 거래량 (또는 데이터 부족 시 통과)
    ]

    save_result_json(result)

    # -------------------------------
    # 텔레그램 전송
    # -------------------------------
    if len(result) > 0:
        message = f"📈 변동폭 돌파 종목 (거래량 {VOLUME_SPIKE_MULTIPLIER}배 분출)\n\n"
        for name in result["name"]:
            print("★", name)
            message += f"★ {name}\n"
    else:
        message = "📊 오늘 조건 만족 변동폭 돌파 종목 없음"

    send_telegram(message)
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

    print(f"돌파 종목 : {len(result)}")
    print(f"실행 시간 : {elapsed:.1f}초")
    print("=" * 40)

    print("변동폭 돌파:", (merged["today_range"] > merged["max_history_range"]).sum())
    print("양봉:", merged["bullish"].sum())
    print("윗꼬리 10% 미만:", (merged["upper_shadow_ratio"] < 0.1).sum())
    print("거래량 스파이크:", merged["volume_spike"].sum())
    print("전체 종목 수:", len(merged))


if __name__ == "__main__":
    main()
