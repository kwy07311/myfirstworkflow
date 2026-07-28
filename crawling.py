import pandas as pd
import FinanceDataReader as fdr


# 현재 상장 종목 목록 가져오기
stocks = fdr.StockListing('KRX')


# 엑셀 저장
stocks.to_excel('상장법인목록.xlsx', index=False)

print("상장 종목 목록 저장 완료")
