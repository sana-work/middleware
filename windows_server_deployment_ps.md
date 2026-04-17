# Windows Server Deployment Guide (PowerShell)

This guide details the steps to deploy the **Agentic Middleware** on a Windows Server environment using PowerShell, following Enterprise Standards (Python 3.13, Lightspeed Pipelines).

---

## 1. Environment Preparation

### Prerequisites
*   **Python 3.13**: Install from the Microsoft Store or Python.org. Ensure "Add Python to PATH" is checked.
*   **Git for Windows**: To clone the repository.
*   **NSSM (Optional but Recommended)**: [Download NSSM](https://nssm.cc/) to run the app as a Windows Service.

### Verify Python Version
```powershell
python --version
# Output should be: Python 3.13.x
```

---

## 2. Project Initialization

Open **PowerShell (Admin)** and navigate to your deployment directory (e.g., `C:\Apps`).

```powershell
# 1. Clone the repository
git clone <your-bitbucket-repo-url>
cd opsui-agent-recon-api

# 2. Create the virtual environment
python -m venv venv

# 3. Enable PowerShell script execution (if blocked)
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 4. Activate the environment
.\venv\Scripts\Activate.ps1

# 5. Install dependencies
pip install -r requirements.txt
```

---

## 3. Configuration & SSL Certificates

### Setup Environment Variables
```powershell
cp .env.example .env
notepad .env
```
*   **Paths**: On Windows, use forward slashes `/` or double backslashes `\\` for paths in the `.env` file (e.g., `KAFKA_SSL_CAFILE=C:/Apps/certs/ca.pem`).

### Deploy Kafka Certificates
Create a `certs` folder in the project root and place your Citi certificates inside:
*   `ca.pem`
*   `Certificate.pem`
*   `PrivateKey.key`

---

## 4. Running as a Windows Service (Persistence)

To ensure the middleware runs 24/7 and restarts automatically, use **NSSM**.

1.  **Open PowerShell as Admin** and navigate to where you downloaded `nssm.exe`.
2.  **Install the service**:
    ```powershell
    .\nssm.exe install ReconMiddleware
    ```
3.  **Configure the GUI window that appears**:
    *   **Path**: `C:\Apps\opsui-agent-recon-api\venv\Scripts\python.exe`
    *   **Startup Directory**: `C:\Apps\opsui-agent-recon-api`
    *   **Arguments**: `-m uvicorn app.main:app --host 0.0.0.0 --port 8000`
4.  **Set Environment Variables**: In the "Env" tab, paste your `.env` contents or set the `ENV_FILE_PATH`.
5.  **Click "Install Service"**.
6.  **Start the service**:
    ```powershell
    Start-Service ReconMiddleware
    ```

---

## 5. Enterprise Pipeline Integration (Lightspeed)

Based on the `pipeline.yaml` requirements, ensure your project is properly tagged for Artifactory publishing.

### Manual Versioning & Tagging
```powershell
# 1. Update version in setup.py
# 2. Commit changes
git add .
git commit -m "Release version 2.0.1"

# 3. Create Tag
git tag -a 2.0.1 -m "Version 2.0.1"

# 4. Push to Bitbucket (triggers pipeline)
git push origin 2.0.1
```

### Local Build Testing
```powershell
# Build sdist and wheel locally
python setup.py sdist
python setup.py bdist_wheel
```

---

## 6. Verification (Smoke Test)

Use the following PowerShell command to verify the server is healthy:

```powershell
# Check Health Status
$response = Invoke-RestMethod -Uri "http://localhost:8000/health/ready" -Method Get
$response | ConvertTo-Json
```

### Troubleshooting Windows Specifics
*   **Port 8000**: Ensure you have a Firewall Inbound Rule allowing traffic on port 8000.
*   **Librdkafka**: If Kafka fails to connect, ensure `msvcp140.dll` (Visual C++ Redistributable) is installed on the Windows Server.
