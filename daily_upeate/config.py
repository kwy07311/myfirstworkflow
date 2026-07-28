import os

APP_KEY = os.environ.get("KIS_APP_KEY")
APP_SECRET = os.environ.get("KIS_APP_SECRET")

BASE_URL = "https://openapi.koreainvestment.com:9443"

# API 재시도
MAX_RETRY = 3

# 호출 간격
REQUEST_DELAY = 0.35