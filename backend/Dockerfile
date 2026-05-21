FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY frontend/ /frontend/

RUN mkdir -p logs

EXPOSE 8000

CMD ["python", "main.py"]
