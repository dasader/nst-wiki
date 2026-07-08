FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONPATH=/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY pipeline ./pipeline
COPY scripts ./scripts
COPY tasks.py .
COPY llm.py llm_config.json cost.py data_schema.py wiki_ops.py embeddings.py search.py text2sql.py ./
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
