# 1. Use the core enterprise Miniconda image (aligned with the Python 3.13 transition)
FROM python:3.13-slim AS builder

# 2. Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/env/bin:$PATH"

WORKDIR /app

# 3. Securely source the enterprise environment for Artifactory access
# This matches your pipeline.yaml requirements
RUN source /env && \
    pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir uv

# 4. Copy and Install dependencies using UV (fastest way in the Miniconda agent)
COPY requirements.txt .
RUN source /env && uv pip install --no-cache-dir -r requirements.txt

# 5. Build the final application image
FROM python:3.13-slim

WORKDIR /app

# Copy the virtual environment and code
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .

# Environment setup for internal Citi proxy/ca-certs if needed
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

EXPOSE 8000

# Start command
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
