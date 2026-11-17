# Grant desk with the agent lane's tools inside, so the screen shows live numbers.
# Build from the repo root (the context must include agent/grant_agent):
#   gcloud builds submit --config frontend/cloudbuild.yaml .
FROM python:3.12-slim

WORKDIR /app
COPY frontend/requirements.txt frontend/requirements.txt
COPY agent/grant_agent/requirements.txt agent/grant_agent/requirements.txt
RUN pip install --no-cache-dir -r frontend/requirements.txt -r agent/grant_agent/requirements.txt

COPY agent/grant_agent agent/grant_agent
COPY frontend frontend

ENV PYTHONUNBUFFERED=1
CMD exec uvicorn app:app --app-dir frontend --host 0.0.0.0 --port ${PORT:-8080}
