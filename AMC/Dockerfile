FROM python:3.11-slim

WORKDIR /app

# System dependencies required by Playwright's Chromium on ARM64 (Raspberry Pi)
# and x86_64. playwright install-deps resolves the full list automatically.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        wget \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Chromium system deps then the browser itself.
# This is cached in its own layer so rebuilds after code changes are fast.
RUN playwright install-deps chromium \
    && playwright install chromium

COPY . .

CMD ["python", "main.py"]
