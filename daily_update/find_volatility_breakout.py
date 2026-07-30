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

    print(f"결과 JSON 저장 완료 : {RESULT_JSON}")


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
# 현재가 조회 (관심종목 멀티종목 시세조회 - 최대 30종목/1회)
# ==================================

def get_stock_price_batch(token, name_batch):
    """
    name_batch: 원본 name(예: '삼성전자_005930') 리스트, 최대 30개
    반환: [{'name':.., 'code':.., 'open':.., 'high':.., 'low':.., 'current_price':..}, ...]
    """
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
                # KIS가 에러를 반환한 경우, 실제 사유(rt_cd/msg_cd/msg1)를 그대로 노출
                raise RuntimeError(
                    f"HTTP {response.status_code} / "
                    f"rt_cd={data.get('rt_cd')} "
                    f"msg_cd={data.get('msg_cd')} "
                    f"msg1={data.get('msg1')}"
                )

            outputs = data["output"]
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
                    "current_price": float(output["inter2_prpr"])
                })

            return results

        except Exception as e:
            if retry < 2:
                time.sleep(0.5 * (retry + 1))
            else:
                # 배치 전체 실패 시, 배치에 포함된 모든 종목을 실패 처리
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
    # 과거 데이터
    # -------------------------------
    history = pd.read_excel(HISTORY_FILE)
    date_columns = [c for c in history.columns if c != "name"]
    history["max_history_range"] = history[date_columns].max(axis=1)

    token = get_access_token()
    stock_names = history["name"].tolist()
    total = len(stock_names)
    print(f"총 {total}개 종목 조회 예정")

    batches = list(chunk_list(stock_names, BATCH_SIZE))
    total_batches = len(batches)
    print(f"{BATCH_SIZE}종목씩 {total_batches}개 배치로 조회")

    # -------------------------------
    # 배치 순차 조회 (배치당 최대 30종목)
    # 동시 요청이 겹치면 KIS 서버가 "초당 거래건수 초과"로 판단하는
    # 문제가 있어, 병렬 대신 순차 + RateLimiter 조합으로 처리
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
    # 비교
    # -------------------------------
    today = pd.DataFrame(results)
    merged = today.merge(history[["name", "max_history_range"]], on="name", how="inner")

    merged["today_range"] = merged["high"] - merged["low"]
    merged["upper_shadow_ratio"] = (merged["high"] - merged["current_price"]) / merged["today_range"]
    merged["bullish"] = merged["current_price"] > merged["open"]

    result = merged[
        (merged["today_range"] > merged["max_history_range"])
        & (merged["bullish"])
        & (merged["upper_shadow_ratio"] < 0.1)
    ]

    save_result_json(result)

    # -------------------------------
    # 텔레그램 전송
    # -------------------------------
    if len(result) > 0:
        message = "📈 변동폭 돌파 종목\n\n"
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


if __name__ == "__main__":
    main()
