FROM python:3.11-slim

# Install necessary system dependencies, especially for confluent-kafka
RUN apt-get update && apt-get install -y \
    build-essential \
    librdkafka-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements file first to leverage Docker cache
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose port
EXPOSE 8000

# Run string
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
