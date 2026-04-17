# Base image use Python 3.11 slim for production stability and small footprint
FROM python:3.11-slim

# Install system dependencies required for confluent-kafka and standard build tools
RUN apt-get update && apt-get install -y \
    build-essential \
    librdkafka-dev \
    libcairo2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Keeps Python from generating .pyc files in the container
ENV PYTHONDONTWRITEBYTECODE=1

# Turns off buffering for easier container logging
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install pip requirements
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Create a non-root user for security
# Create logs directory and ensure the non-root user can write to it
RUN adduser -u 5678 --disabled-password --gecos "" appuser \
    && mkdir -p /app/logs \
    && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port 8000
EXPOSE 8000

# Use Gunicorn as the production process manager with Uvicorn workers
# -k uvicorn.workers.UvicornWorker allows Gunicorn to handle FastAPI's async nature
# --bind 0.0.0.0:8000 ensures accessibility inside the container network
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "-k", "uvicorn.workers.UvicornWorker", "app.main:app"]
