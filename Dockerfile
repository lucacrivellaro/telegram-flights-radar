FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# il database SQLite vive in /app/data: monta lì un volume per non perderlo
# (niente direttiva VOLUME: Railway la rifiuta, i volumi si montano dal pannello)
ENV DB_PATH=/app/data/flights.db

CMD ["python", "main.py"]
