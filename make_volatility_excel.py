import os
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

INPUT_FILE = "stocks.xlsx"
OUTPUT_FILE = "volatility_result.xlsx"

# 지정된 10거래일 (2026-07-15 ~ 2026-07-29, 주말 제외, 7/17 제외)
TARGET_DATES = {
    "2026.07.15", "2026.07.16", 
    "2026.07.20", "2026.07.21", "2026.07.22", "2026.07.23", "2026.07.24",
    "2026.07.27", "2026.07.28", "2026.07.29"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def create_retry_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False
    )
    adapter = HTTPAdapter(max_retries=retries, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session

def fetch_stock_volatility(stock_info: str, session: requests.Session) -> dict:
    try:
        parts = str(stock_info).split("_")
        if len(parts) < 2:
            print(f"[형식 오류] '{stock_info}' -> '종목명_종목코드' 형식이 아닙니다.")
            return {"name": stock_info, "status": "Format Error", "result": "N/A"}
        
        stock_name = parts[0].strip()
        stock_code = parts[1].strip().zfill(6)
    except Exception as e:
        return {"name": stock_info, "status": f"Parsing Error ({e})", "result": "N/A"}

    collected_data = {}
    
    # 해당 날짜를 찾기 위해 페이지 탐색 범위를 1~10페이지로 늘림
    for page in range(1, 11):
        url = f"https://finance.naver.com/item/sise_day.naver?code={stock_code}&page={page}"
        time.sleep(0.03)  # 호출 지연
        
        try:
            res = session.get(url, timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")
            
            rows = soup.find_all("tr")
            for row in rows:
                align_center_td = row.find("td", align="center")
                if not align_center_td or not align_center_td.text.strip():
                    continue
                
                date_str = align_center_td.text.strip()

                if date_str in TARGET_DATES:
                    nums = row.find_all("td", class_="num")
                    if len(nums) >= 6:
                        # nums[3]: 고가, nums[4]: 저가, nums[5]: 거래량
                        high_p = int(nums[3].text.replace(",", "").strip())
                        low_p = int(nums[4].text.replace(",", "").strip())
                        vol = int(nums[5].text.replace(",", "").strip())
                        
                        # (고가 - 저가) * 거래량
                        collected_data[date_str] = (high_p - low_p) * vol

        except Exception as e:
            print(f"[수집 예외] {stock_name}({stock_code}) page {page}: {e}")

        # 10거래일 데이터가 모두 수집되었으면 탐색 중단
        if len(collected_data) == len(TARGET_DATES):
            break

    # 데이터 수집 결과 체크
    if len(collected_data) == 0:
        print(f"[데이터 없음] {stock_name}({stock_code}): 해당 날짜 데이터를 찾지 못했습니다.")
        return {"name": stock_info, "status": "No Data Found", "result": "N/A"}
    
    elif len(collected_data) < len(TARGET_DATES):
        print(f"[일부 수집] {stock_name}({stock_code}): 10일 중 {len(collected_data)}일만 수집됨")

    total_volatility = sum(collected_data.values())
    return {
        "name": stock_info,
        "status": "Success",
        "result": total_volatility
    }

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"입력 파일 없음: {INPUT_FILE}")
        return
    
    df_stocks = pd.read_excel(INPUT_FILE)
    if "name" not in df_stocks.columns:
        print("stocks.xlsx 파일 내 'name' 컬럼이 존재하지 않습니다.")
        return

    stock_list = df_stocks["name"].dropna().tolist()
    print(f"총 {len(stock_list)}개 종목 크롤링 시작...")

    session = create_retry_session()
    results = []

    # max_workers=5로 스레드 처리
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_stock = {
            executor.submit(fetch_stock_volatility, stock, session): stock 
            for stock in stock_list
        }
        
        for future in as_completed(future_to_stock):
            stock = future_to_stock[future]
            try:
                data = future.result()
                results.append(data)
                print(f"완료: {stock} -> {data['result']}")
            except Exception as exc:
                print(f"스레드 예외 ({stock}): {exc}")
                results.append({"name": stock, "status": "Thread Error", "result": "N/A"})

    # 원본 Excel 순서 유지
    df_result = pd.DataFrame(results)
    df_result = df_result.set_index("name").reindex(stock_list).reset_index()
    
    df_result.to_excel(OUTPUT_FILE, index=False)
    print(f"\n최종 완료! 결과 파일: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
