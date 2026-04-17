# Agent API (Agentic Middleware)

A production-style Python FastAPI middleware that connects a frontend chat UI to a backend conversational agent executor.

The middleware acts as a robust broker that:

1. Accepts chat execution requests via REST POST
2. Fetches required API tokens
3. Calls the backend executor synchronously to start the process
4. Consumes asynchronous execution progress events from Kafka
5. Normalizes and safely persists all execution state to MongoDB (acting as the system of record)
6. Broadcasts real-time live progress updates to frontend clients over WebSockets
7. Exposes REST endpoints to fetch chat status and history (with pagination)

* **MongoDB**: Central data store for `sessions`, `recon` (executions), and `events`. Guarantees idempotency via unique keys.

## Data Integrity & Kafka Semantics

This middleware implements a robust **at-least-once** processing model to ensure no events are lost, while safely handling potential duplicates.

* **Duplicate Suppression through Idempotency**: Every Kafka event is assigned a unique `event_idempotency_key` (compound of `correlation_id`, `event_type`, and `timestamp`). MongoDB enforces a unique index on this key. Duplicate messages from Kafka (e.g., after a rebalance or crash) are filtered out, ensuring deterministic state.
* **Commit after Persistence (At-Least-Once Delivery)**: The Kafka consumer only commits offsets **after successful persistence** to MongoDB. In case of a database failure, the offset is not committed, ensuring the message will be redelivered (guaranteeing at-least-once delivery).
* **Concurrency**: Blocking Kafka poll calls are isolated in a dedicated background thread pool using `asyncio.run_in_executor`, keeping the FastAPI event loop responsive.
* **Audit Traceability**: All state-changing lifecycle events are logged to a structured audit trail with automatic PII/secret redaction.

## Multi-Agent Orchestration

The middleware is designed to support **multi-agent backend workflows** natively:
* It tracks distinct agents dynamically using `agent_name` in the payload, creating targeted UI summaries (e.g., "Agent started: Data_Retrieval_Agent").
* It translates intermediate `AGENT_COMPLETION_EVENT`s into `in_progress` execution states. This ensures the client UI natively handles handoffs between multiple chained agents without prematurely dropping the loading spinner.
* An execution is only marked as fully `completed` when the backend orchestrator explicitly emits the `EXECUTION_FINAL_RESPONSE` event.

## Prerequisites

* **Python 3.11+**: The project is designed and tested for Python 3.11.
* **MongoDB**: A running instance (local or Atlas).
* **Kafka**: Connection details for event consumption.

## Privacy & Security Enforcement

To prevent ID enumeration and protect user data, the middleware enforces a strict **Ownership Policy**:

1. **REST API (`/execute`, `/status`, `/history`)**: If an ID (Session or Correlation) exists but does not belong to the authenticated `X-SOEID`, the server returns **404 Not Found** instead of 403.
2. **WebSockets (`/ws/progress`)**: Ownership mismatches during connection result in an immediate socket closure with **status code 1008** (Policy Violation).

## Architecture Overview

## Local Running using Docker Compose

1. **Setup Environment config**:

   ```bash
   cp .env.example .env
   # Ensure you populate backend URLs and generate test certs under ./certs
   ```

2. **Setup SSL Certs Volume**:
   Since Kafka Confluent Consumer expects SSL certs, put them in `./certs/`. This corresponds to `.env` variables `KAFKA_SSL_CERTFILE` etc.

   ```bash
   mkdir certs
   # Place ca.pem, client-cert.pem, and client-key.pem in certs/
   ```

3. **Build and Run**:

   ```bash
   docker-compose up --build -d
   ```

4. **Check Health**:

   ```bash
   curl http://localhost:8000/health/ready
   ```

## Local Running (Without Docker)

1. **Install dependencies**:
   Ensure `librdkafka` is loaded via your system package manager (e.g., `brew install librdkafka`).

   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Start App**:

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

## Core APIs

* `POST /api/v1/chat/execute`
* `GET /api/v1/chat/status/{correlation_id}?soeid=USER`
* `GET /api/v1/chat/history/{session_id}?soeid=USER&limit=50&skip=0`
* `WS /ws/v1/chat/progress/{correlation_id}?soeid=USER`
* `GET /api/v1/chat/export/pdf/{correlation_id}?download=true` (Export execution report as PDF using X-SOEID auth)

## Running Tests

```bash
pytest tests/ -v
```
