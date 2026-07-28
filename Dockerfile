FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

WORKDIR /app/src

ENV PORT=8080

# --timeout 120：webhook 立即回 200、實際工作在背景 daemon thread，請求本身
# 極快，120s 上限足以回收真正卡死的請求執行緒（原 --timeout 0 永不回收，可能
# 累積殭屍執行緒）。背景 daemon thread 不受 gunicorn 請求逾時影響。
CMD ["sh", "-c", "exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 120 app:app"]
