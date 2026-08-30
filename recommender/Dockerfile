FROM python:3.12.5-slim-bookworm@sha256:c24c34b502635f1f7c4e99dc09a2cbd85d480b7dcfd077198c6b5af138906390

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --no-deps --no-build-isolation .

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; request=urllib.request.Request('http://127.0.0.1:8000/internal/health/live', headers={'Authorization':'Bearer test-c2-service-token'}); raise SystemExit(0 if urllib.request.urlopen(request, timeout=2).status == 200 else 1)"

CMD ["uvicorn", "feelm_recommender.api:app", "--host", "0.0.0.0", "--port", "8000"]
