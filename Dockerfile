FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY fetch_discovery.py .

# Environment variables (set these in Cloud Run)
# ENV SUPABASE_URL=https://fipgpddebmfytowkurvb.supabase.co
# ENV SUPABASE_SERVICE_KEY=<your-service-role-key>

# Legacy GCS config (kept for backup, optional)
ENV GOOGLE_CLOUD_PROJECT=avian-voice-476622-r8
ENV GCS_BUCKET=blockrun-data

CMD ["python", "fetch_discovery.py"]
