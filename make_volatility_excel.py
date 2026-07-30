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

# 10개 거래일 (2026-07-15 ~ 2026-07-29, 주말 및 7/17 제외)
TARGET_DATES = [
    "2026.07.15", "2026.07.16", 
    "2026.07.20", "2026.07.21", "2026.07.22", "2026.07.23", "2026.07.24",
    "2026.07.27", "2026.07.28", "2026.07.29"
]

TARGET_DATES_SET = set(TARGET_DATES)

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
    row_data = {"name": stock_info}
    # 10개 날짜 컬럼을 기본값 'N/A'로 초기화
    for d in TARGET_DATES:
        row_data[d] = "N/A"

    try:
        parts = str(stock_info).split("_")
        if len(parts) < 2:
            row_data["status"] = "Format Error"
            return row_data
        
        stock_name = parts[0].strip()
        stock_code = parts[1].strip().zfill(6)
    except Exception as e:
        row_data["status"] = f"Parsing Error ({e})"
        return row_data

    collected_data = {}
    
    # 최근 시세 수집 (1~10페이지 탐색)
    for page in range(1, 11):
        url = f"https://finance.naver.com/item/sise_day.naver?code={stock_code}&page={page}"
        time.sleep(0.03)
        
        try:
            res = session.get(url, timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")
            
            rows = soup.find_all("tr")
            for row in rows:
                align_center_td = row.find("td", align="center")
                if not align_center_td or not align_center_td.text.strip():
                    continue
                
                date_str = align_center_td.text.strip()

                if date_str in TARGET_DATES_SET:
                    nums = row.find_all("td", class_="num")
                    if len(nums) >= 6:
                        # nums[3]: 고가, nums[4]: 저가, nums[5]: 거래량
                        high_p = int(nums[3].text.replace(",", "").strip())
                        low_p = int(nums[4].text.replace(",", "").strip())
                        vol = int(nums[5].text.replace(",", "").strip())
                        
                        # (고가 - 저가) 계산
                        diff = high_p - low_p
                        
                        # '계산값_거래량' 형식의 문자열 생성
                        collected_data[date_str] = f"{diff}_{vol}"

        except Exception as e:
            print(f"[수집 예외] {stock_name}({stock_code}) page {page}: {e}")

        # 10거래일 데이터가 모두 수집되었으면 탐색 중단
        if len(collected_data) == len(TARGET_DATES_SET):
            break

    if len(collected_data) == 0:
        row_data["status"] = "No Data Found"
        return row_data

    # 수집된 데이터를 날짜별 컬럼에 배치
    for d in TARGET_DATES:
        if d in collected_data:
            row_data[d] = collected_data[d]

    row_data["status"] = "Success" if len(collected_data) == len(TARGET_DATES_SET) else f"Partial ({len(collected_data)}/10)"
    
    return row_data

def main():
    if not os.path.exists(INPUT_FILE):
        print(f"입력 파일 없음: {INPUT_FILE}")
        return
    
    df_stocks = pd.read_excel(INPUT_FILE)
    if "name" not in df_stocks.columns:
        print("stocks.xlsx 파일 내 'name' 컬럼이 존재하지 않습니다.")
        return

    # name 컬럼의 유효 데이터 행(Row)만 추출
    stock_list = df_stocks["name"].dropna().tolist()
    print(f"총 {len(stock_list)}개 종목 크롤링 시작...")

    session = create_retry_session()
    results = []

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
                print(f"완료: {stock}")
            except Exception as exc:
                print(f"스레드 예외 ({stock}): {exc}")
                err_row = {"name": stock, "status": "Thread Error"}
                for d in TARGET_DATES:
                    err_row[d] = "N/A"
                results.append(err_row)

    # 원본 종목 순서 유지 및 컬럼 배치
    df_result = pd.DataFrame(results)
    
    column_order = ["name"] + TARGET_DATES + ["status"]
    df_result = df_result.set_index("name").reindex(stock_list).reset_index()
    df_result = df_result[column_order]
    
    df_result.to_excel(OUTPUT_FILE, index=False)
    print(f"\n작업 완료! 결과 파일 생성: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
