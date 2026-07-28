import pandas as pd
import os

# 로컬에 다운로드한 CSV 파일 경로
file_path = r"C:\Users\user\Desktop\파이썬\상장법인목록.csv"

# 인코딩은 cp949가 가장 안정적
df = pd.read_csv(file_path, encoding='cp949')

# 컬럼명 확인
print(df.columns)

# 종목코드 6자리 맞추기
df['종목코드'] = df['종목코드'].apply(lambda x: str(x).zfill(6))

# 실행 파일 경로에 저장
current_path = os.path.dirname(os.path.abspath(__file__))
save_path = os.path.join(current_path, "kospi_kosdaq.xlsx")
df.to_excel(save_path, index=False)

print("✅ 엑셀 저장 완료:", save_path)
