FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY fetch_discovery.py .

ENV GOOGLE_CLOUD_PROJECT=avian-voice-476622-r8
ENV GCS_BUCKET=blockrun-data

CMD ["python", "fetch_discovery.py"]
