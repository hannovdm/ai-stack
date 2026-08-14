FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        cmake \
        pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch>=2.4,<3" \
	&& pip install --no-cache-dir -r requirements.txt

COPY server.py .

EXPOSE 8092

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8092"]