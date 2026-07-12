FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# il database SQLite vive in /app/data: montalo come volume per non perderlo
VOLUME ["/app/data"]
ENV DB_PATH=/app/data/flights.db

CMD ["python", "main.py"]
