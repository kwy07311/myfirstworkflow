import os
import time
import requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ================= ============================================
# 1. 설정 및 환경 변수 (GitHub Secrets 참조)
# ==============================================================
# 실전투자 URL: https://openapi.koreainvestment.com:9443
# 모의투자 URL: https://openapivts.koreainvestment.com:29443
CANONICAL_URL = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
APP_KEY = os.getenv("KIS_APPKEY")
APP_SECRET = os.getenv("KIS_APPSECRET")

INPUT_FILE = "stocks.xlsx"
OUTPUT_FILE = "volatility_result.xlsx"

# 대상 거래일 (2026-07-15 ~ 2026-07-29, 7/17 제외 총 10거래일)
TARGET_DATES = [
    "20260715", "20260716", "20260720", "20260721", "20260722",
    "20260723", "20260724", "20260727", "20260728", "20260729"
]

# ==============================================================
# 2. HTTP 세션 생성 (재시도 및 Connection Pooling 적용)
# ==============================================================
def create_retry_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,  # 1초, 2초, 4초... 지연 재시도
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

# ==============================================================
# 3. 접근 토큰(Access Token) 발급 (1회 호출)
# ==============================================================
def get_access_token(session: requests.Session) -> str:
    url = f"{CANONICAL_URL}/oauth2/tokenP"
    headers = {"content-type": "application/json; charset=utf-8"}
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    
    response = session.post(url, headers=headers, json=body)
    res_data = response.json()
    
    if "access_token" in res_data:
        print("Successfully obtained KIS Access Token.")
        return res_data["access_token"]
    else:
        raise ValueError(f"Failed to issue Access Token: {res_data}")

# ==============================================================
# 4. 종목별 일별 시세 조회 및 지표 계산
# ==============================================================
def fetch_stock_volatility(stock_info: str, token: str, session: requests.Session) -> dict:
    """
    stock_info: '삼성전자_005930' 형태의 문자열
    """
    try:
        parts = str(stock_info).split("_")
        if len(parts) < 2:
            raise ValueError("Invalid format in 'name' column. Expected '종목명_종목코드'.")
        
        stock_name = parts[0]
        stock_code = parts[1].strip().zfill(6)  # 6자리 종목코드 맞춤
    except Exception as e:
        print(f"[Error] Stock info parsing failed for {stock_info}: {e}")
        return {"name": stock_info, "status": "Parsing Error", "result": "N/A"}

    url = f"{CANONICAL_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST03010100"  # 주식일봉조회 TR
    }
    
    # 조회 시작일~종료일 범위 설정
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
        "FID_INPUT_DATE_1": "20260715",
        "FID_INPUT_DATE_2": "20260729",
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0"
    }

    # API 초당 호출 제한(TPS) 방지를 위한 찰나의 대기
    time.sleep(0.05)

    try:
        res = session.get(url, headers=headers, params=params, timeout=10)
        res_json = res.json()
        
        if res_json.get("rt_cd") != "0":
            msg = res_json.get("msg1", "Unknown Error")
            print(f"[API Error] {stock_name}({stock_code}): {msg}")
            return {"name": stock_info, "status": f"API Error ({msg})", "result": "N/A"}

        output2 = res_json.get("output2", [])
        if not output2:
            return {"name": stock_info, "status": "No Data", "result": "N/A"}

        # API 응답 데이터를 DataFrame으로 변환
        df_daily = pd.DataFrame(output2)
        
        # 지정된 10개 거래일 데이터만 필터링
        df_filtered = df_daily[df_daily["stck_bsop_date"].isin(TARGET_DATES)].copy()
        
        if df_filtered.empty:
            return {"name": stock_info, "status": "No Target Date Data", "result": "N/A"}

        # 수치형 컬럼 타입 변환 (고가, 저가, 거래량)
        df_filtered["stck_hgpr"] = pd.to_numeric(df_filtered["stck_hgpr"], errors="coerce")
        df_filtered["stck_lwpr"] = pd.to_numeric(df_filtered["stck_lwpr"], errors="coerce")
        df_filtered["acml_vol"] = pd.to_numeric(df_filtered["acml_vol"], errors="coerce")

        # 지표 계산: (고가 - 저가) * 거래량
        df_filtered["volatility"] = (df_filtered["stck_hgpr"] - df_filtered["stck_lwpr"]) * df_filtered["acml_vol"]
        
        # 10거래일 합산
        total_volatility = df_filtered["volatility"].sum()

        return {
            "name": stock_info,
            "status": "Success",
            "result": total_volatility
        }

    except Exception as e:
        print(f"[Exception] {stock_name}({stock_code}): {str(e)}")
        return {"name": stock_info, "status": f"Exception ({str(e)})", "result": "N/A"}

# ==============================================================
# 5. 메인 실행 함수
# ==============================================================
def main():
    if not APP_KEY or not APP_SECRET:
        raise ValueError("KIS_APPKEY and KIS_APPSECRET environment variables must be set.")

    # 엑셀 파일 읽기
    if not os.path.exists(INPUT_FILE):
        raise FileNotFoundError(f"Input file '{INPUT_FILE}' not found.")
    
    df_stocks = pd.read_excel(INPUT_FILE)
    if "name" not in df_stocks.columns:
        raise KeyError("Column 'name' is missing in stocks.xlsx.")

    stock_list = df_stocks["name"].dropna().tolist()
    print(f"Loaded {len(stock_list)} stocks from {INPUT_FILE}.")

    # 세션 생성 및 토큰 발급 (1회)
    session = create_retry_session()
    token = get_access_token(session)

    results = []
    
    # ThreadPoolExecutor를 사용한 멀티스레딩 처리 (max_workers=5)
    print("Starting processing with ThreadPoolExecutor (max_workers=5)...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_stock = {
            executor.submit(fetch_stock_volatility, stock, token, session): stock 
            for stock in stock_list
        }
        
        for future in as_completed(future_to_stock):
            stock = future_to_stock[future]
            try:
                data = future.result()
                results.append(data)
                print(f"Completed: {stock} -> {data['result']}")
            except Exception as exc:
                print(f"Thread generated an exception for {stock}: {exc}")
                results.append({"name": stock, "status": "Thread Error", "result": "N/A"})

    # 결과를 DataFrame으로 정리하여 엑셀로 저장
    df_result = pd.DataFrame(results)
    
    # 원본 Excel의 'name' 순서 유지
    df_result = df_result.set_index("name").reindex(stock_list).reset_index()
    
    df_result.to_excel(OUTPUT_FILE, index=False)
    print(f"Successfully saved output to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
