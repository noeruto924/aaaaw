FROM python:3.11-slim

# 出力バッファリングを無効化（ログを即時出力）
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 依存関係のコピーとインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# コードのコピー
COPY bot.py .

# 実行 (-u オプションで非バッファリング指定)
CMD ["python", "-u", "bot.py"]
