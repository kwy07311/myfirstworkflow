import os
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ==============================================================
# 1. 설정 및 상수 정의
# ==============================================================
INPUT_FILE = "stocks.xlsx"
OUTPUT_FILE = "volatility_result.xlsx"

# 2026-07-15 ~ 2026-07-29 (7/17 제외 총 10거래일, 네이버 표기 'YYYY.MM.DD' 형식)
TARGET_DATES = {
    "2026.07.15", "2026.07.16", "2026.07.20", "2026.07.21", "2026.07.22",
    "2026.07.23", "2026.07.24", "2026.07.27", "2026.07.28", "2026.07.29"
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ==============================================================
# 2. HTTP 세션 생성 (재시도 및 Connection Pooling)
# ==============================================================
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

# ==============================================================
# 3. 네이버 금융 크롤링 및 변동성 계산
# ==============================================================
def fetch_stock_volatility(stock_info: str, session: requests.Session) -> dict:
    """
    stock_info: '삼성전자_005930' 형태의 문자열
    """
    try:
        parts = str(stock_info).split("_")
        if len(parts) < 2:
            raise ValueError("Invalid format in 'name' column. Expected '종목명_종목코드'.")
        
        stock_name = parts[0]
        stock_code = parts[1].strip().zfill(6)
    except Exception as e:
        print(f"[Error] 종목명 파싱 실패 ({stock_info}): {e}")
        return {"name": stock_info, "status": "Parsing Error", "result": "N/A"}

    collected_data = {}
    
    # 최근 10거래일은 네이버 일별 시세 기준 보통 1~2페이지 내에 모두 존재
    for page in range(1, 3):
        url = f"https://finance.naver.com/item/sise_day.naver?code={stock_code}&page={page}"
        
        time.sleep(0.05) # 서버 부하 방지
        try:
            res = session.get(url, timeout=10)
            soup = BeautifulSoup(res.text, "html.parser")
            
            rows = soup.find_all("tr")
            for row in rows:
                date_td = row.find("td", class_="num")
                if not date_td or not date_td.text.strip():
                    # 날짜가 표시된 첫 번째 num 클래스 td 찾기
                    align_center_td = row.find("td", align="center")
                    if align_center_td and align_center_td.text.strip():
                        date_str = align_center_td.text.strip()
                    else:
                        continue
                else:
                    continue

                if date_str in TARGET_DATES:
                    nums = row.find_all("td", class_="num")
                    if len(nums) >= 6:
                        # 네이버 일별 시세 컬럼 구조:
                        # nums[0]: 종가, nums[1]: 전일비, nums[2]: 시가, nums[3]: 고가, nums[4]: 저가, nums[5]: 거래량
                        high_price = int(nums[3].text.replace(",", "").strip())
                        low_price = int(nums[4].text.replace(",", "").strip())
                        volume = int(nums[5].text.replace(",", "").strip())
                        
                        collected_data[date_str] = (high_price - low_price) * volume

        except Exception as e:
            print(f"[Exception] {stock_name}({stock_code}) page {page} 수집 중 에러: {e}")

        # Target 날짜 10개를 모두 수집했으면 조기 종료
        if len(collected_data) == len(TARGET_DATES):
            break

    if not collected_data:
        return {"name": stock_info, "status": "No Target Date Data", "result": "N/A"}

    total_volatility = sum(collected_data.values())
    return {
        "name": stock_info,
        "status": "Success",
        "result": total_volatility
    }

# ==============================================================
# 4. 메인 실행 함수
# ==============================================================
def main():
    if not os.path.exists(INPUT_FILE):
        raise FileNotFoundError(f"입력 파일 '{INPUT_FILE}'을 찾을 수 없습니다.")
    
    df_stocks = pd.read_excel(INPUT_FILE)
    if "name" not in df_stocks.columns:
        raise KeyError("stocks.xlsx 파일에 'name' 컬럼이 없습니다.")

    stock_list = df_stocks["name"].dropna().tolist()
    print(f"총 {len(stock_list)}개 종목 수집을 시작합니다.")

    session = create_retry_session()
    results = []

    # ThreadPoolExecutor 적용 (max_workers=5)
    print("ThreadPoolExecutor(max_workers=5)로 크롤링 진행 중...")
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
                print(f"스레드 예외 발생 ({stock}): {exc}")
                results.append({"name": stock, "status": "Thread Error", "result": "N/A"})

    # 원본 Excel의 'name' 순서대로 정렬하여 저장
    df_result = pd.DataFrame(results)
    df_result = df_result.set_index("name").reindex(stock_list).reset_index()
    
    df_result.to_excel(OUTPUT_FILE, index=False)
    print(f"결과가 {OUTPUT_FILE}에 성공적으로 저장되었습니다.")

if __name__ == "__main__":
    main()