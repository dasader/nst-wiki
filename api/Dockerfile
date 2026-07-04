FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    git libxcb1 libxrender1 libx11-6 libxext6 libsm6 libxkbcommon0 libdbus-1-3 libgl1 \
    libglib2.0-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-shape0 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY pipeline ./pipeline
COPY scripts ./scripts
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
