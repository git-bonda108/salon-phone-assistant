FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY data ./data
# prompt + knowledge base are used by the prompt-lab agent (same text as in the Suisse Line portal)
COPY suisse-line ./suisse-line
COPY docs/architecture/system-architecture.svg ./docs/architecture/system-architecture.svg
RUN useradd -m appuser && mkdir -p /tmp/lina && chown appuser /tmp/lina
USER appuser
ENV DATABASE_PATH=/tmp/lina/app.db
EXPOSE 8000
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
