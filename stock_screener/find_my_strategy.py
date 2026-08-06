name: Stock Screener - MA Reversal Scan

on:
  workflow_dispatch:

# ⭐ 1. Git Push를 가능하게 하려면 쓰기 권한(write)이 필요합니다.
permissions:
  contents: write

jobs:
  ma-reversal-scan:
    runs-on: ubuntu-latest
    steps:
      # 저장소 가져오기
      - name: Checkout repository
        uses: actions/checkout@v4

      # Python 설치
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      # 라이브러리 설치
      - name: Install packages
        run: |
          pip install pandas openpyxl requests

      # ⭐ 2. 암호화된 토큰 캐시가 있으면 복호화 (없으면 그냥 통과)
      - name: Decrypt cached token
        run: |
          if [ -f stock_screener/.token_state.enc ]; then
            openssl enc -d -aes-256-cbc -pbkdf2 \
              -in stock_screener/.token_state.enc \
              -out stock_screener/.token_state.json \
              -pass pass:"${{ secrets.TOKEN_CACHE_KEY }}" \
              || echo "복호화 실패 (키 변경 등) → 새로 발급됩니다"
          else
            echo "캐시된 토큰 없음 → 새로 발급됩니다"
          fi

      # 5일 이평선 하향 + 양봉 종목 검색 실행
      - name: Run MA reversal scan
        env:
          KIS_APP_KEY: ${{ secrets.KIS_APP_KEY }}
          KIS_APP_SECRET: ${{ secrets.KIS_APP_SECRET }}
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          cd stock_screener
          python find_my_strategy.py

      # ⭐ 3. 토큰 상태 파일을 다시 암호화하고, 평문 파일은 즉시 삭제
      - name: Encrypt token state
        if: always()
        run: |
          if [ -f stock_screener/.token_state.json ]; then
            openssl enc -e -aes-256-cbc -pbkdf2 \
              -in stock_screener/.token_state.json \
              -out stock_screener/.token_state.enc \
              -pass pass:"${{ secrets.TOKEN_CACHE_KEY }}"
            rm -f stock_screener/.token_state.json
          fi

      # ⭐ 4. 새로 생성/수정된 docs/screener_data.json + 암호화된 토큰 캐시를 저장소에 커밋 & 푸시
      - name: Commit and Push screener_data.json + token cache
        if: always()
        run: |
          git config --local user.email "github-actions[bot]@users.noreply.github.com"
          git config --local user.name "github-actions[bot]"
          git add docs/screener_data.json
          if [ -f stock_screener/.token_state.enc ]; then
            git add stock_screener/.token_state.enc
          fi
          # 변경 사항이 있을 때만 커밋 (에러 방지용 || exit 0)
          git commit -m "auto: update MA reversal screener_data.json" || exit 0
          git push
