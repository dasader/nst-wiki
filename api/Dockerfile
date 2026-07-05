FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONPATH=/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# EasyOCR ko/en 모델을 빌드 시 사전 다운로드 → 런타임 동시 다운로드 레이스(/root/.EasyOCR/model/temp.zip) 방지
RUN python -c "import easyocr; easyocr.Reader(['ko','en'], gpu=False)"
COPY app ./app
COPY pipeline ./pipeline
COPY scripts ./scripts
COPY tasks.py .
COPY llm.py llm_config.json wiki_ops.py embeddings.py search.py text2sql.py ./
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
