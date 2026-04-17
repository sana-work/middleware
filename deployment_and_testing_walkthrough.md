# Deployment and Testing Guide: Agentic Middleware

This guide provides a structured, step-by-step walkthrough for deploying and verifying the **Agentic Middleware** on a new production or staging environment.

---

## 1. System Prerequisites

Ensure the following are installed and accessible on the target system:
- **Python 3.11+**: The application relies on Python 3.11 features (and is backwards compatible with 3.9).
- **MongoDB**: Access to a MongoDB cluster (local or Atlas) with a user that has index-creation permissions.
- **Kafka / Confluent**: Access to the Kafka bootstrap servers (usually port 9095/9092) with SSL enabled.
- **OpenSSL / Librdkafka**: Required for the `confluent-kafka` library to handle SSL handshakes.

---

## 2. Step 1: Code Initialization

Clone the repository and prepare the virtual environment:

```bash
# 1. Clone the repo (replace with your git path)
git clone https://github.com/sana-work/middleware.git
cd middleware

# 2. Create and activate a clean virtual environment
python3 -m venv venv

# macOS / Linux:
source venv/bin/activate

# Windows (PowerShell):
.\venv\Scripts\Activate.ps1

# 3. Install core and test dependencies
pip install -r requirements.txt
```

---

## 3. Step 2: Environment Configuration

The application uses `pydantic-settings` to load values from the environment or a `.env` file.

1.  **Initialize .env**:
    ```bash
    cp .env.example .env
    ```
2.  **Populate Rotated Secrets**:
    Open `.env` and fill in the following critical production values:
    - `MONGODB_URI`: Full connection string (including username/password).
    - `KAFKA_SSL_PASSWORD`: The password for the SSL private key.
    - `TOKEN_CLIENT_SECRET`: The secret for the Token API.
    - `BACKEND_APPLICATION_ID` & `BACKEND_CONFIG_ID`: Environment-specific identifiers.

---

## 4. Step 3: SSL Certificate Management

For secure Kafka communication, the middleware requires specific certificates in a `./certs/` directory (mapped via `.env` path variables).

1.  **Create certs directory**:
    ```bash
    mkdir certs
    ```
2.  **Deploy Certificates**:
    Place the following files in the `certs/` folder:
    - `Citi_DEVELOPMENT_chain.pem` (CA Chain)
    - `Certificate.pem` (Client Certificate)
    - `PrivateKey.key` (Client Private Key)

> [!IMPORTANT]
> Ensure the file paths in your `.env` (e.g., `KAFKA_SSL_CAFILE`) point exactly to these locations (usually `/app/certs/...` when inside Docker, or `./certs/...` when running locally).

---

## 5. Step 4: Running the Middleware

### Option A: Local Run (Uvicorn)
Perfect for smoke-testing and development.
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Option B: Containerized Run (Docker)
Recommended for staging/production consistency.
```bash
docker-compose up --build -d
```

---

## 6. Step 5: Operational Verification

Once started, verify the system's "vital signs" using the internal health check routes.

1.  **Liveness Check**:
    `curl http://localhost:8000/health/live` -> Returns `{status: "ok"}`
2.  **Readiness Check**:
    `curl http://localhost:8000/health/ready` -> Verifies MongoDB and Kafka connectivity.

> [!TIP]
> **Database Initialization**: On the first boot, the application will automatically create all necessary compound indexes on the `recon`, `sessions`, and `events` collections. Check the logs for `MongoDB indexes created`.

---

## 7. Step 6: Running the Full Test Suite

Execute the pre-built test suite to ensure all hardening measures (idempotency, ownership gating, redaction) are functioning on the new host.

```bash
# Run all 29 tests with verbose output
python3 -m pytest tests/ -v
```

**What is being tested?**
- **Security**: Ownership leak prevention (returns 404 for unauthorized access).
- **Integrity**: Kafka idempotency via `event_idempotency_key`.
- **Resilience**: Kafka offset commit-after-persistence logic.
- **Operations**: PII/Secret redaction in audit logs.

---

## 8. Step 7: Functional Smoke Test

To verify the full integration path:

1.  **Trigger Execution**:
    Call `POST /api/v1/chat/execute` with a valid `X-SOEID`.
2.  **Monitor Logs**:
    Watch for `EXECUTION_INITIALIZED` in the audit logs.
3.  **Check WebSocket**:
    Connect to the `websocket_url` returned in the POST response and verify that status events (Running -> Completed) flow through after the backend publishes to Kafka.

---

## 10. Dev Server Background Deployment (Systemd)

On a persistent dev server (Linux), you often need the application to run as a supervised background service.

### 1. Create a Service File
`sudo nano /etc/systemd/system/recon-middleware.service`

### 2. Add the following template:
```ini
[Unit]
Description=Gunicorn instance to serve Agentic Middleware
After=network.target

[Service]
User=youruser
Group=yourgroup
WorkingDirectory=/path/to/OPSUI-AGENT-RECON-API
Environment="PATH=/path/to/OPSUI-AGENT-RECON-API/venv/bin"
# Load environment from file
EnvironmentFile=/path/to/OPSUI-AGENT-RECON-API/.env
ExecStart=/path/to/OPSUI-AGENT-RECON-API/venv/bin/gunicorn \
    -w 4 -k uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    app.main:app

[Install]
WantedBy=multi-user.target
```

### 3. Start and Enable
```bash
sudo systemctl start recon-middleware
sudo systemctl enable recon-middleware
sudo systemctl status recon-middleware
```

---

## 11. Reverse Proxy (Nginx)

For production/staging consistency, it is recommended to put Nginx in front of the application to handle SSL termination and provide a clean URL.

**Nginx Config Snippet:**
```nginx
server {
    listen 80;
    server_name middleware.dev.icg;

    location / {
        proxy_pass http://localhost:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

> [!CAUTION]
> **WebSocket Timeout**: Ensure your Nginx `proxy_read_timeout` is set high enough (e.g., 600s) so that the Agent WebSocket doesn't disconnect during long-running "Thinking" phases.
