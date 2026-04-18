# 1. Use the core enterprise Miniconda image
FROM python:3.13-slim AS builder

# 2. Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 3. Install system-level Kafka dependencies (librdkafka)
# NOTE: This stage requires root permissions to use 'yum'
USER root
RUN yum update -y && yum install -y librdkafka-devel gcc python3-devel && yum clean all
USER 1001

# 4. Setup Python tools
RUN source /env && \
    pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir uv

# 5. Install dependencies
COPY requirements.txt .
RUN source /env && uv pip install --no-cache-dir -r requirements.txt

# 6. Build the final application image
FROM python:3.13-slim

# Re-install librdkafka in the final image so the app can run
USER root
RUN yum install -y librdkafka && yum clean all
USER 1001

WORKDIR /app
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .

ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
