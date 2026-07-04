FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONPATH=/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY pipeline ./pipeline
COPY scripts ./scripts
COPY tasks.py .
COPY llm.py llm_config.json ./
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
