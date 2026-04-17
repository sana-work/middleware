# Complete Code Understanding Guide

Repository: `OPSUI-AGENT-RECON-API`

Generated from local source inspection on 2026-04-16.

This guide is intentionally written as a teaching document. It explains the codebase from the outside in, then from the inside out: first the system purpose and runtime behavior, then each file, function, mapping, data model, and integration point.

Verification note: the guide has been refreshed after the PDF export feature was added. I updated the local `venv/` with the new `requirements.txt` entries (`weasyprint`, `Jinja2`) and reran `venv/bin/pytest tests/ -q`. The current local macOS run stops during collection because WeasyPrint cannot load native GObject/Pango libraries (`libgobject-2.0-0`). The Dockerfile now installs the corresponding Linux libraries, so containerized runs should have the PDF engine dependencies present; local macOS needs equivalent system packages before the full suite can collect.

---

# 1. Executive overview

## What this system does

This project is a Python FastAPI middleware service. It sits between a frontend chat UI and a backend conversational agent executor.

The frontend sends a chat or investigation request to this middleware. The middleware:

1. Identifies the user through `X-SOEID` or a WebSocket query parameter.
2. Generates a per-run `correlation_id`.
3. Reuses or creates a conversation-level `session_id`.
4. Fetches an authorization token from an external token service.
5. Calls a backend agent executor synchronously to start the long-running work.
6. Persists the accepted execution to MongoDB.
7. Consumes asynchronous execution events from Kafka.
8. Normalizes Kafka events into stable frontend-facing status and event shapes.
9. Persists every accepted event to MongoDB with an idempotency key.
10. Updates execution status in MongoDB.
11. Broadcasts live progress to connected WebSocket clients.
12. Exposes REST APIs for status, history, health, readiness, and Prometheus metrics.

In plain English: this service turns a one-shot frontend request into a durable, observable, real-time, recoverable agent execution lifecycle.

## Why it exists

Long-running agent workflows do not fit cleanly into a single HTTP request. A backend agent may call tools, emit intermediate progress, fail halfway through, complete later, or produce duplicated Kafka events because Kafka is at-least-once by design.

This middleware exists to solve those problems:

- The frontend gets an immediate `accepted` response instead of waiting for the full agent run.
- MongoDB becomes the durable source of truth for active and historical executions.
- Kafka decouples backend execution progress from frontend delivery.
- WebSocket gives the frontend live progress, but does not become the history store.
- Idempotency protects against duplicated Kafka messages.
- Ownership checks prevent users from seeing each other's sessions or executions.
- Metrics and audit logs provide operational visibility.

## Role in the larger architecture

The larger system looks like this:

```text
Frontend Chat UI
    |
    | REST POST /api/v1/chat/execute
    v
FastAPI Middleware
    |
    | fetch token
    v
Token Service

FastAPI Middleware
    |
    | POST backend executor with token + IDs
    v
Backend Agent Executor
    |
    | async progress events
    v
Kafka
    |
    | consume, normalize, persist
    v
FastAPI Middleware
    |
    | write source of truth
    v
MongoDB

FastAPI Middleware
    |
    | live updates
    v
Frontend WebSocket Client
```

The middleware is therefore both:

- a synchronous REST broker for starting work
- an asynchronous event processor for tracking work

## Major moving parts

- `app/main.py`: constructs the FastAPI application, middleware, exception handlers, metrics mount, and routers.
- `app/core/lifespan.py`: starts and stops MongoDB, Kafka consumer, WebSocket manager, and throttler cleanup.
- `app/api/chat_routes.py`: REST endpoints for execute, status, and history.
- `app/api/websocket_routes.py`: WebSocket endpoint for live progress.
- `app/api/health_routes.py`: liveness and readiness probes.
- `app/api/export_routes.py`: REST endpoint for generating a PDF execution report.
- `app/services/chat_execution_service.py`: orchestrates the execute flow.
- `app/services/event_processing_service.py`: normalizes Kafka events, persists them, updates execution state, and returns broadcast payloads.
- `app/services/status_service.py`: reads MongoDB and builds status/history responses.
- `app/services/websocket_manager.py`: tracks live WebSocket clients and sends event/status/terminal/heartbeat messages.
- `app/services/pdf_export_service.py`: loads execution/event data and renders printable PDF bytes with Jinja2 and WeasyPrint.
- `app/clients/token_client.py`: fetches and caches external auth tokens.
- `app/clients/backend_executor_client.py`: calls the backend agent executor.
- `app/clients/kafka_consumer.py`: consumes Kafka messages and coordinates persistence, broadcast, and offset commits.
- `app/db/mongo.py`: owns MongoDB connection and indexes.
- `app/db/repositories/*.py`: collection-specific MongoDB operations.
- `app/models/*.py`: Pydantic request, response, DB, Kafka, WebSocket, and export DTO schemas.
- `app/templates/execution_report.html`: Jinja2/HTML template used by the PDF export service.
- `app/utils/*.py`: IDs, timestamps, retry decorator, and audit logging.
- `tests/*.py`: intended behavior around execute, token, backend client, event processing, Kafka integrity, and WebSocket manager.

---

# 2. Architecture overview

## Frontend

The frontend is expected to be a chat or investigation UI.

It calls:

- `POST /api/v1/chat/execute` when the user submits a prompt.
- `WS /ws/v1/chat/progress/{correlation_id}?soeid=USER` for live updates.
- `GET /api/v1/chat/status/{correlation_id}` to refresh or recover the state of a single execution.
- `GET /api/v1/chat/history/{session_id}` to rebuild a whole conversation.
- `GET /api/v1/chat/export/pdf/{correlation_id}` to download or view a PDF report for one execution.

The frontend must preserve two identifiers:

- `session_id`: conversation thread identity, reused across turns.
- `correlation_id`: one execution/run identity, unique per submitted prompt.

## Middleware

The middleware is the code in this repository. It is responsible for:

- request validation
- identity extraction
- rate limiting
- session/correlation ID management
- token fetch
- backend executor call
- Mongo persistence
- Kafka consumption
- event normalization
- WebSocket broadcasting
- REST status/history reads
- health checks
- metrics
- audit logging

FastAPI provides the HTTP and WebSocket server. The service is asynchronous for IO-heavy work such as MongoDB, HTTP clients, and WebSocket sends.

## Backend executor

The backend executor is external to this repository. The middleware calls it through `BackendExecutorClient.execute`.

The backend call is synchronous from the middleware's point of view: the middleware sends a POST and expects an immediate acknowledgment. That acknowledgment does not mean the whole agent workflow is done. It only means the backend accepted or initialized the run.

The backend executor later emits progress events to Kafka.

## Token service

The token service is external. The middleware calls it through `TokenClient`.

`TokenClient` sends:

```json
{"clientSecret": "<secret>"}
```

to `settings.TOKEN_URL`, then extracts either:

- a raw JWT-like string beginning with `ey`
- or JSON containing `access_token` and optional `expires_in`

The returned token is sent to the backend executor in `X-Authorization-Coin: Bearer <token>`.

## Kafka

Kafka is used because backend agent execution is asynchronous. Instead of making the frontend wait for the agent to complete, the backend publishes progress events to a topic. This middleware consumes those events.

The consumer uses `confluent-kafka`, which is blocking. To avoid blocking FastAPI's event loop, polling runs in an executor thread and schedules async processing back onto the main event loop with `asyncio.run_coroutine_threadsafe`.

Kafka offset commits are manual. The code commits only after the message is safely handled or intentionally skipped. For valid persistence events, it commits only after MongoDB persistence succeeds.

## MongoDB

MongoDB is the source of truth.

Collections:

- `sessions`: one conversation thread per `session_id`.
- `recon`: one execution/run per `correlation_id`.
- `events`: append-only normalized Kafka events.

MongoDB is used for:

- ownership checks
- status endpoint responses
- history endpoint responses
- idempotency through a unique `event_idempotency_key`
- debug/replay metadata such as Kafka topic, partition, offset, and raw payload

## WebSocket

WebSocket is used for live progress delivery. It is not the source of truth.

When a frontend connects to:

```text
/ws/v1/chat/progress/{correlation_id}?soeid=USER
```

the route:

1. throttles rapid reconnects per user/IP
2. verifies that the execution exists and belongs to the current user
3. accepts the socket
4. sends an immediate `connected` message
5. registers the socket with `WebSocketManager`
6. waits for incoming text solely to keep the connection alive

When Kafka events are processed, `WebSocketManager.broadcast` sends:

- an `event` message with full normalized event data
- a `status` message with current execution status
- if terminal, a `completed` or `failed` message and then closes sockets for that run

## Monitoring and logging

Monitoring is split into:

- Prometheus metrics under `/metrics`
- health endpoints under `/health/live` and `/health/ready`
- application structured JSON logging through `structlog`
- audit logging through `AuditLogger`

Prometheus metrics cover:

- Kafka consumed messages
- processed/ignored/duplicate Kafka events
- persistence failures
- commit success/failure
- active executions by status
- active WebSocket connections
- WebSocket total connections
- WebSocket throttle rejections

Audit logging records state-changing lifecycle events while redacting secrets and user context.

## Who calls whom

```text
app.main
  includes chat_routes, websocket_routes, health_routes
  uses lifespan
  mounts metrics

chat_routes.execute_chat
  -> get_current_user
  -> ChatExecutionService.execute
       -> SessionService.resolve_session_id
       -> token_client.get_token
       -> backend_executor_client.execute
       -> SessionsRepository.create_or_update_session
       -> ExecutionsRepository.create_execution
       -> metrics_service.increment_active_executions
       -> audit_logger.log_event

chat_routes.get_status
  -> get_current_user
  -> StatusService.get_execution_status
       -> ExecutionsRepository.get_execution_by_soeid
       -> EventsRepository.get_events_by_correlation

chat_routes.get_history
  -> get_current_user
  -> StatusService.get_session_history
       -> SessionsRepository.get_session_by_soeid
       -> ExecutionsRepository.get_executions_by_session
       -> ExecutionsRepository.count_executions_by_session
       -> EventsRepository.get_events_by_correlation

websocket_routes.websocket_progress
  -> get_current_user
  -> check_throttle
  -> ExecutionsRepository.get_execution_by_soeid
  -> get_ws_manager
  -> WebSocketManager.connect/disconnect

lifespan
  -> setup_logging
  -> init_mongo
  -> WebSocketManager
  -> KafkaEventConsumer.start
  -> throttler_cleanup_loop

KafkaEventConsumer._process_message
  -> RawKafkaEvent
  -> EventProcessingService.process_kafka_event
       -> ExecutionsRepository.get_execution
       -> EventsRepository.insert_event
       -> ExecutionsRepository.update_execution_status
       -> audit_logger.log_event
       -> metrics
  -> WebSocketManager.broadcast
  -> KafkaEventConsumer._safe_commit
```

## Synchronous vs asynchronous flow

Synchronous from the user's perspective:

- `POST /execute` returns immediately after token fetch, backend executor acknowledgment, and MongoDB insert.
- `GET /status` reads MongoDB and returns current status.
- `GET /history` reads MongoDB and returns paginated session executions.
- health endpoints return immediate service checks.

Asynchronous:

- backend agent work happens after the initial executor acknowledgment
- backend emits Kafka events later
- Kafka consumer processes events in the background
- WebSocket live updates happen as events arrive
- heartbeat messages are sent on a background task per correlation ID with active clients
- throttler cleanup runs in a background task

## Source of truth

MongoDB is the source of truth.

Specifically:

- `recon.status` is the current execution status.
- `events` is the durable event timeline.
- `sessions` ties many executions into one conversation thread.

WebSocket is intentionally transient. If the browser disconnects or misses messages, it should recover through `/status` or `/history`.

## Lifecycle of a request

The core lifecycle is:

1. Frontend sends `POST /api/v1/chat/execute`.
2. FastAPI validates `ChatExecuteRequest`.
3. `get_current_user` extracts `X-SOEID`.
4. Route overwrites `body.soeid` with authenticated identity.
5. `ChatExecutionService.execute` generates `correlation_id`.
6. `SessionService.resolve_session_id` returns existing `session_id` or generates a new one.
7. `TokenClient.get_token` returns cached or freshly fetched token.
8. `BackendExecutorClient.execute` calls external executor.
9. `SessionsRepository.create_or_update_session` upserts the session.
10. `ExecutionsRepository.create_execution` inserts an accepted execution in `recon`.
11. Execute response returns IDs and WebSocket URL.
12. Frontend opens WebSocket for `correlation_id`.
13. WebSocket route verifies ownership from MongoDB.
14. Backend executor emits Kafka events.
15. Kafka consumer parses, filters, and validates each message.
16. `EventProcessingService.process_kafka_event` normalizes and persists it.
17. `recon.status` is updated.
18. WebSocket manager broadcasts event and status.
19. Terminal status closes the WebSocket stream.
20. Frontend can always reload state from `/status` or `/history`.

---

# 3. File/folder map

## Full project structure

```text
.
├── .env.example
├── .gitignore
├── Dockerfile
├── README.md
├── CODEBASE_UNDERSTANDING_GUIDE.md
├── deployment_and_testing_walkthrough.md
├── docker-compose.yml
├── frontend_handoff_document_enhanced.md
├── interactive_architecture_diagram.html
├── requirements.txt
├── walkthrough_diagram_pack.md
├── websocket_tester.html
├── app
│   ├── __init__.py
│   ├── main.py
│   ├── api
│   │   ├── __init__.py
│   │   ├── chat_routes.py
│   │   ├── deps.py
│   │   ├── export_routes.py
│   │   ├── health_routes.py
│   │   └── websocket_routes.py
│   ├── clients
│   │   ├── __init__.py
│   │   ├── backend_executor_client.py
│   │   ├── kafka_consumer.py
│   │   └── token_client.py
│   ├── core
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── exceptions.py
│   │   ├── lifespan.py
│   │   ├── limiter.py
│   │   ├── logging.py
│   │   └── throttler.py
│   ├── db
│   │   ├── __init__.py
│   │   ├── mongo.py
│   │   └── repositories
│   │       ├── __init__.py
│   │       ├── events_repository.py
│   │       ├── executions_repository.py
│   │       └── sessions_repository.py
│   ├── models
│   │   ├── __init__.py
│   │   ├── api_requests.py
│   │   ├── api_responses.py
│   │   ├── db_models.py
│   │   ├── export_models.py
│   │   ├── kafka_events.py
│   │   └── websocket_messages.py
│   ├── services
│   │   ├── __init__.py
│   │   ├── chat_execution_service.py
│   │   ├── event_processing_service.py
│   │   ├── metrics_service.py
│   │   ├── pdf_export_service.py
│   │   ├── session_service.py
│   │   ├── status_service.py
│   │   └── websocket_manager.py
│   ├── templates
│   │   └── execution_report.html
│   └── utils
│       ├── __init__.py
│       ├── audit_logger.py
│       ├── datetime_utils.py
│       ├── ids.py
│       └── retry.py
└── tests
    ├── __init__.py
    ├── conftest.py
    ├── test_backend_executor_client.py
    ├── test_chat_execute_endpoint.py
    ├── test_event_processing_service.py
    ├── test_export_service.py
    ├── test_integrity_integration.py
    ├── test_token_client.py
    └── test_websocket_manager.py
```

Generated/transient local folders observed but not part of the source design:

- `.pytest_cache/`: pytest cache.
- `.benchmarks/`: local benchmark output folder, not referenced by code.
- `__pycache__/`: Python bytecode caches.
- `.DS_Store`: macOS filesystem metadata.

## Root files

### `.env.example`

Why it exists: shows required environment variables for local, Docker, and deployment usage.

Responsibility: configuration template for app name, MongoDB, Kafka, token service, backend executor, WebSocket timing, and retry settings.

Who depends on it: humans and deployment automation. At runtime, `app/core/config.py` reads real environment variables or `.env`.

### `.gitignore`

Why it exists: prevents secrets, caches, certificates, logs, virtual environments, IDE files, and OS artifacts from entering git.

Important security entries:

- `.env`
- `certs/*.pem`
- `certs/*.key`
- `logs/`

### `requirements.txt`

Why it exists: dependency list for local, test, and Docker installs.

Important dependency groups:

- FastAPI server: `fastapi`, `uvicorn`, `gunicorn`
- configuration and validation: `pydantic`, `pydantic-settings`
- MongoDB: `motor`
- Kafka: `confluent-kafka`
- HTTP clients: `httpx`
- logging: `structlog`
- rate limiting: `slowapi`
- metrics: `prometheus-client`
- PDF export: `weasyprint`, `Jinja2`
- tests: `pytest`, `pytest-asyncio`

### `Dockerfile`

Why it exists: production-style container build.

Responsibility:

- uses Python 3.11 slim
- installs `build-essential` and `librdkafka-dev`
- installs WeasyPrint native rendering libraries: Cairo, Pango, GDK Pixbuf, libffi, and shared MIME info
- installs Python requirements
- copies app source
- creates non-root `appuser`
- creates writable `/app/logs`
- exposes port 8000
- starts Gunicorn with Uvicorn workers

Who depends on it: `docker-compose.yml`, deployment pipelines.

### `docker-compose.yml`

Why it exists: local container orchestration for app, MongoDB, Kafka, and Zookeeper.

Responsibility:

- builds app container
- maps app port `8000`
- loads `.env`
- mounts `certs` read-only and `logs` writable
- starts MongoDB 7
- starts Zookeeper and Kafka

Who depends on it: local developers and staging-like smoke tests.

### `README.md`

Why it exists: project-level overview and quick start.

Responsibility: explains the middleware, MongoDB, Kafka idempotency, commit-after-persistence, ownership policy, local running, core APIs, and tests.

Current README also documents the multi-agent orchestration interpretation: intermediate `AGENT_COMPLETION_EVENT` values are treated as `in_progress`, and only `EXECUTION_FINAL_RESPONSE` marks the whole execution as `completed`.

Current README also lists the PDF export API: `GET /api/v1/chat/export/pdf/{correlation_id}?download=true`.

### `deployment_and_testing_walkthrough.md`

Why it exists: operational setup and verification guide.

Responsibility: environment prep, SSL certificate setup, running locally or in Docker, health checks, test suite, smoke testing, and troubleshooting.

### `frontend_handoff_document_enhanced.md`

Why it exists: frontend integration contract.

Responsibility: endpoint contract, identifiers, auth/identity expectations, WebSocket behavior, PDF export behavior, and UI recovery guidance.

Important note: this file was already modified before this guide was created. This guide did not modify it.

### `walkthrough_diagram_pack.md`

Why it exists: diagram-heavy architecture walkthrough.

Responsibility: Mermaid diagrams and slide-like explanations of component flow.

### `interactive_architecture_diagram.html`

Why it exists: standalone visual architecture poster.

Responsibility: HTML/CSS/SVG visual explanation of identity, frontend, middleware, token service, backend executor, Kafka, MongoDB, Prometheus, and audit logs.

It is documentation/UI only. The runtime app does not import it.

### `websocket_tester.html`

Why it exists: manual testing page for WebSocket progress streams.

Responsibility:

- lets a tester enter SOEID and correlation ID
- opens `ws://localhost:8000/ws/v1/chat/progress/{cid}?soeid={soeid}`
- logs incoming JSON messages
- shows close code `1008` as policy/ownership failure

It is not served by the FastAPI app automatically. Open it directly in a browser or serve it as a static file separately.

## `app/` package

### Package marker files

Files:

- `app/__init__.py`
- `app/api/__init__.py`
- `app/clients/__init__.py`
- `app/core/__init__.py`
- `app/db/__init__.py`
- `app/db/repositories/__init__.py`
- `app/models/__init__.py`
- `app/services/__init__.py`
- `app/utils/__init__.py`
- `tests/__init__.py`

Why they exist: they mark directories as Python packages so imports like `app.services.status_service` and `tests.conftest` work consistently.

Responsibility: no runtime logic; they are intentionally trivial.

### `app/main.py`

Application composition. Everything public starts here.

Depends on:

- `settings`
- `lifespan`
- exception classes
- SlowAPI limiter
- Prometheus ASGI app
- API routers

Depended on by:

- Uvicorn/Gunicorn: `app.main:app`
- tests importing the FastAPI app

### `app/api/`

HTTP and WebSocket delivery layer. Routes should be thin and delegate business behavior to services.

- `chat_routes.py`: execute/status/history.
- `websocket_routes.py`: live progress socket.
- `health_routes.py`: health and readiness.
- `export_routes.py`: PDF execution report export.
- `deps.py`: shared identity extraction dependency.

### `app/clients/`

External integration clients.

- `token_client.py`: external token service.
- `backend_executor_client.py`: external backend agent executor.
- `kafka_consumer.py`: Kafka event ingestion.

### `app/core/`

Cross-cutting app infrastructure.

- `config.py`: environment-driven settings.
- `lifespan.py`: startup/shutdown resources.
- `exceptions.py`: domain-specific exception classes.
- `logging.py`: structlog setup.
- `limiter.py`: SlowAPI limiter.
- `throttler.py`: in-memory WebSocket connection throttle.

### `app/db/`

MongoDB connection and repositories.

- `mongo.py`: connection lifecycle and indexes.
- `repositories/sessions_repository.py`: session collection.
- `repositories/executions_repository.py`: `recon` collection.
- `repositories/events_repository.py`: event collection.

### `app/models/`

Pydantic schemas and mapping constants.

- `api_requests.py`: inbound REST request bodies.
- `api_responses.py`: REST response models.
- `db_models.py`: documented MongoDB document shapes.
- `export_models.py`: DTOs used when assembling a PDF export.
- `kafka_events.py`: raw/normalized Kafka events and event/status/summary maps.
- `websocket_messages.py`: WebSocket message envelopes.

### `app/services/`

Business logic.

- `chat_execution_service.py`: execute orchestration.
- `event_processing_service.py`: Kafka event normalization and persistence.
- `status_service.py`: Mongo-backed status/history builders.
- `session_service.py`: session ID resolution.
- `websocket_manager.py`: active WebSocket connection manager.
- `metrics_service.py`: Prometheus metrics definitions.
- `pdf_export_service.py`: PDF report DTO construction and rendering.

### `app/templates/`

HTML templates used by server-side rendering flows.

- `execution_report.html`: Jinja2 template rendered by `PDFExportService` before WeasyPrint converts HTML to PDF bytes.

### `app/utils/`

Small reusable support functions.

- `ids.py`: UUID generation.
- `datetime_utils.py`: UTC timestamps and ISO formatting.
- `retry.py`: async retry decorator.
- `audit_logger.py`: structured redacting audit logger.

## `tests/`

Tests are organized around units and important integration semantics:

- `conftest.py`: fixtures.
- `test_chat_execute_endpoint.py`: REST execute behavior.
- `test_event_processing_service.py`: event maps and event processing.
- `test_export_service.py`: PDF export DTO building and export route headers/error behavior.
- `test_integrity_integration.py`: ownership, idempotency, commit safety.
- `test_backend_executor_client.py`: backend HTTP client.
- `test_token_client.py`: token fetch/cache behavior.
- `test_websocket_manager.py`: WebSocket connection and broadcast behavior.

---

# 4. End-to-end runtime flow

## Execute flow: frontend to backend executor

### Step 1: frontend sends execute request

Request:

```http
POST /api/v1/chat/execute
X-SOEID: TEST001
Content-Type: application/json
```

Body, as currently required by `ChatExecuteRequest`:

```json
{
  "context": "Investigate case TEST_123",
  "soeid": "TEST001",
  "session_id": null,
  "metadata": {"client": "web"}
}
```

Important: the route overwrites `body.soeid` with `current_user`. So the header identity wins. However, because `ChatExecuteRequest.soeid` is required, the body still must include `soeid` unless the model is changed. This is a mismatch with frontend documentation that says the body should not include `soeid`.

### Step 2: FastAPI route receives request

`app/api/chat_routes.py` defines:

```python
@router.post("/execute", response_model=ChatExecuteResponse)
@limiter.limit("10/minute")
async def execute_chat(request, body, current_user=Depends(get_current_user)):
    body.soeid = current_user
    return await ChatExecutionService.execute(body)
```

What it does:

- Applies route prefix `/api/v1/chat`, so full path is `/api/v1/chat/execute`.
- Applies a SlowAPI limit of 10 requests per minute per remote address.
- Uses `get_current_user` to extract identity.
- Mutates the request object so the trusted identity controls `soeid`.
- Delegates all real work to `ChatExecutionService.execute`.

Why it exists:

- Keeps route logic thin.
- Centralizes the business flow in the service layer.
- Prevents the request body from spoofing a different user than the authenticated header/query identity.

### Step 3: identity dependency runs

`get_current_user` checks:

1. `X-SOEID` header
2. `soeid` query parameter

If neither exists, it raises `401`.

For REST, the intended identity source is the header. Query parameter support exists mainly because WebSocket clients commonly cannot set arbitrary headers with the browser-native WebSocket API.

### Step 4: service generates IDs

`ChatExecutionService.execute` calls:

- `generate_correlation_id()`: always creates a new UUID v4 for this run.
- `SessionService.resolve_session_id(request.session_id)`: reuses provided session ID or generates a new UUID v4.

The relationship:

```text
session_id = conversation/thread
correlation_id = one execution/run inside that thread
```

### Step 5: token fetched

`token_client.get_token()` returns a cached token if still valid. Otherwise it calls `_fetch_token()`.

Token fetch sends:

```json
{"clientSecret": settings.TOKEN_CLIENT_SECRET}
```

to `settings.TOKEN_URL`.

Network impact:

- outbound HTTP POST to token service
- timeout controlled by `TOKEN_TIMEOUT_SECONDS`
- SSL verification controlled by `TOKEN_VERIFY_SSL`
- retries controlled by `HTTP_RETRY_ATTEMPTS` and `HTTP_RETRY_BACKOFF_SECONDS`

### Step 6: backend executor called

`backend_executor_client.execute(...)` posts to:

```text
{BACKEND_EXECUTOR_BASE_URL}{BACKEND_EXECUTOR_PATH}
```

Headers sent:

```text
Config-ID: settings.BACKEND_CONFIG_ID
X-Correlation-ID: <correlation_id>
X-Application-ID: settings.BACKEND_APPLICATION_ID
Session-ID: <session_id>
X-SOEID: <soeid>
X-Authorization-Coin: Bearer <token>
Content-Type: application/json
```

Body:

```json
{"context": "<user prompt>"}
```

The backend returns an immediate acknowledgment, not the final agent answer.

### Step 7: Mongo session upsert

`SessionsRepository.create_or_update_session` writes to `sessions`.

Filter:

```python
{"session_id": session_id}
```

Update:

```python
"$set": {
  "soeid": soeid,
  "updated_at": now,
  "last_correlation_id": correlation_id,
  "metadata": metadata
},
"$setOnInsert": {
  "session_id": session_id,
  "created_at": now
}
```

Purpose:

- create a session if new
- update the last correlation ID and metadata if continuing a thread

Hidden assumption:

- the supplied `session_id` belongs to this `soeid`. The code does not check ownership before upsert. Because UUIDs are hard to guess, this may be accepted operationally, but strict ownership would require filtering by both `session_id` and `soeid` or checking before update.

### Step 8: Mongo execution insert

`ExecutionsRepository.create_execution` inserts into `recon`.

Stored fields include:

- `correlation_id`
- `session_id`
- `soeid`
- `request_context`
- masked `backend_request`
- raw `backend_ack`
- initial `status: "accepted"`
- `latest_event_type: None`
- timestamps

Purpose:

- create a durable record before Kafka events arrive
- allow WebSocket ownership verification
- allow `/status` to work immediately

### Step 9: metrics and audit

Metrics:

```python
increment_active_executions("accepted")
```

Audit:

```python
audit_logger.log_event(
    "EXECUTION_INITIALIZED",
    user_id=request.soeid,
    correlation_id=correlation_id,
    data={"session_id": session_id, "body": request.model_dump()}
)
```

The audit logger redacts non-whitelisted body fields. In practice, `context` is replaced with `***` when inside `body`.

### Step 10: execute response returned

Response model:

```json
{
  "success": true,
  "message": "Execution initialized successfully",
  "data": {
    "correlation_id": "<uuid>",
    "session_id": "<uuid>",
    "status": "accepted",
    "backend_ack": {"message": "<backend message or default>"},
    "websocket_url": "/ws/v1/chat/progress/<correlation_id>",
    "created_at": "<iso timestamp>"
  }
}
```

The response intentionally contains enough information for the frontend to:

- track the run
- keep the conversation ID
- open a live progress WebSocket

## PDF export flow: Mongo timeline to downloadable report

### Step 1: frontend requests a report

Request:

```http
GET /api/v1/chat/export/pdf/{correlation_id}?download=true
X-SOEID: TEST001
```

Optional query parameters:

- `download`: when true, response uses `Content-Disposition: attachment`; when false, response uses `inline`.
- `include_raw`: when true, raw Kafka payload excerpts are included in event timeline DTOs and rendered in the PDF template.
- `include_timestamps`: currently accepted for API/spec alignment but not passed into the service; the template always renders event timestamps it receives.

### Step 2: export route reads identity header

Unlike `chat_routes.py`, `export_routes.py` does not use `get_current_user`. It directly requires:

```python
x_soeid: str = Header(...)
```

That means this route requires an `X-SOEID` header specifically. It does not accept the `soeid` query fallback used by the shared identity dependency.

### Step 3: route delegates to PDF service

`export_execution_pdf` calls:

```python
PDFExportService.generate_pdf(
    correlation_id=correlation_id,
    soeid=x_soeid,
    include_raw=include_raw,
)
```

The route itself does not query MongoDB. Ownership and data assembly happen inside the service.

### Step 4: service builds an export DTO

`generate_pdf` calls `build_export_dto`.

`build_export_dto`:

1. Loads execution with `ExecutionsRepository.get_execution_by_soeid(correlation_id, soeid)`.
2. Returns `None` if missing or unauthorized.
3. Loads ordered events with `EventsRepository.get_events_by_correlation(correlation_id)`.
4. Converts each raw Mongo event into an `ExportEventDTO`.
5. Extracts `final_response` from `EXECUTION_FINAL_RESPONSE` or any event whose status is `completed`.
6. Extracts `error_details` from the first failed event.
7. Returns `ExportExecutionDTO`.

### Step 5: service renders PDF

`generate_pdf`:

1. Verifies WeasyPrint's `HTML` class is available.
2. Loads `app/templates/execution_report.html` through Jinja2 `FileSystemLoader`.
3. Renders the template with `report=dto.model_dump()`.
4. Converts HTML to PDF bytes using `HTML(string=html_content).write_pdf()`.
5. Returns `bytes`.

### Step 6: route returns PDF response

If bytes are returned:

- response body is PDF bytes
- `Content-Type` is `application/pdf`
- `Content-Disposition` is attachment or inline with filename `agentic_execution_{correlation_id}.pdf`

If service returns `None`, route returns `404` with:

```json
{"detail": "Execution not found or access denied."}
```

If the PDF engine fails, route returns `500`.

### Risks / edge cases

- WeasyPrint needs native system libraries. Docker installs Linux libraries, but local macOS must have equivalent libraries available or app import/test collection can fail.
- `include_raw=true` can place raw event payload data in the PDF, which may include sensitive backend details.
- The route uses `X-SOEID` directly instead of shared `get_current_user`, so auth behavior differs slightly from other REST routes.

## WebSocket flow: frontend attaches to live updates

### Step 1: frontend opens socket

```text
ws://localhost:8000/ws/v1/chat/progress/{correlation_id}?soeid=TEST001
```

### Step 2: dependency extracts identity

`get_current_user` reads `soeid` query parameter because browser WebSocket APIs often cannot set `X-SOEID`.

### Step 3: throttle check

`check_throttle(current_user, client_ip)` rejects connections attempted within 2 seconds for the same user/IP key.

If throttled:

- increments `WS_THROTTLED_TOTAL`
- closes with code `1008`

### Step 4: ownership check

`ExecutionsRepository.get_execution_by_soeid(correlation_id, current_user)` verifies the execution exists and belongs to the user.

If not:

- closes WebSocket with code `1008`
- does not accept the connection

This is the WebSocket equivalent of REST returning `404` for unauthorized resources.

### Step 5: connection accepted

After verification:

- `websocket.accept()`
- send `WSConnectedMessage`
- `ws_manager.connect(correlation_id, websocket)`

`WSConnectedMessage` includes:

- `type: "connected"`
- `correlation_id`
- `session_id`
- current execution `status`
- timestamp

### Step 6: route keeps socket open

The route loops:

```python
while True:
    await websocket.receive_text()
```

It does not process inbound messages. The receive loop is a keep-alive mechanism and a way to detect disconnects.

### Step 7: disconnect cleanup

On `WebSocketDisconnect` or error, it calls:

```python
await ws_manager.disconnect(correlation_id, websocket)
```

That removes the socket, decrements active connection gauge, and cancels heartbeat if it was the last client for that correlation ID.

## Kafka event flow: backend progress to Mongo and WebSocket

### Step 1: backend emits Kafka message

Expected payload shape:

```json
{
  "x_correlation_id": "<correlation_id>",
  "event_type": "TOOL_INPUT_EVENT",
  "agent_name": "recon_ops_investigator_agent",
  "tool_name": "find_payments_by_amount",
  "response": {"maximum_amount": "1037.24"},
  "timestamp": "2026-04-15T10:24:47.633300"
}
```

### Step 2: Kafka consumer polls

`KafkaEventConsumer._poll_loop` runs in a background executor thread. It calls:

```python
msg = self._consumer.poll(timeout=1.0)
```

For each non-error message, it schedules:

```python
asyncio.run_coroutine_threadsafe(self._process_message(msg), loop)
```

### Step 3: message parsed

`_process_message` decodes UTF-8 and parses JSON.

Malformed JSON:

- logs error
- commits offset
- returns

Why commit malformed messages: they are poison messages. Retrying would likely loop forever.

### Step 4: event filtered

Unsupported `event_type`:

- logs debug
- commits offset
- returns

Allowed event types are defined in `ALLOWED_EVENT_TYPES`.

### Step 5: correlation ID required

Missing `x_correlation_id`:

- logs warning
- commits offset
- returns

Without correlation ID, the middleware cannot attach the event to an execution.

### Step 6: Kafka metadata built

Metadata includes:

- topic
- partition
- offset
- consumed_at
- raw_key

`EventProcessingService` stores these on the event document with a `kafka_` prefix.

### Step 7: raw event model created

`RawKafkaEvent(**payload)` tolerates optional fields because different event types carry different data.

### Step 8: event processor loads execution

`EventProcessingService.process_kafka_event` looks up:

```python
ExecutionsRepository.get_execution(correlation_id)
```

If no execution exists:

- logs warning
- returns `None`

Important behavior: `_process_message` treats `processed is None` as duplicate/already safe and commits. That means events for unknown correlation IDs are committed and not retried. This is intentional if unknown means irrelevant, but it would drop early events if Kafka beats the execute record insert.

### Step 9: event normalized

The processor maps:

- raw event type to normalized event type
- raw event type to execution status
- raw event type to summary text

Example:

```text
TOOL_INPUT_EVENT -> normalized_event_type "tool_started"
TOOL_INPUT_EVENT -> status "in_progress"
TOOL_INPUT_EVENT -> summary "Tool invoked: {tool_name}"
```

### Step 10: idempotency key created

Key:

```text
{correlation_id}:{event_type}:{event_timestamp}:{tool_name or ""}
```

MongoDB has a unique index on `event_idempotency_key`.

Purpose:

- Kafka can redeliver messages.
- Duplicate event inserts raise `DuplicateKeyError`.
- Repository returns `False`.
- Processor returns `None`.
- Consumer commits duplicate offset because the event is already persisted.

### Step 11: event inserted

`EventsRepository.insert_event(event_doc)` inserts into `events`.

If insert succeeds:

- returns `True`

If duplicate:

- logs warning
- returns `False`

If another DB error:

- propagates exception

### Step 12: execution status updated

`ExecutionsRepository.update_execution_status` sets:

- `status`
- `latest_event_type`
- `updated_at`
- optionally `completed_at`

`completed_at` is only set for statuses:

```python
("completed", "failed")
```

### Step 13: audit and metrics

Audit event:

```text
KAFKA_EVENT_PROCESSED
```

Metric:

```python
KAFKA_EVENTS_PROCESSED_TOTAL.labels(event_type=event_type).inc()
```

Execution gauges move from old status to new status if the status changed.

### Step 14: broadcast returned

The processor returns a dict shaped for WebSocket broadcast:

```json
{
  "correlation_id": "...",
  "session_id": "...",
  "event_type": "...",
  "normalized_event_type": "...",
  "status": "...",
  "agent_name": "...",
  "tool_name": "...",
  "timestamp": "...",
  "summary": "...",
  "payload": {"response": "..."}
}
```

### Step 15: WebSocket broadcast

`WebSocketManager.broadcast` sends:

1. `type: "event"`
2. `type: "status"`
3. if terminal, `type: "completed"` or `type: "failed"`, then closes sockets

Broadcast errors are logged but do not block Kafka offset commits.

### Step 16: Kafka commit

After successful persistence, and after broadcast attempt, `_safe_commit` commits the message synchronously:

```python
self._consumer.commit(message=msg, asynchronous=False)
```

If persistence fails, `_process_message` returns without committing. Kafka will redeliver the message later.

---

# 5. File-by-file deep dive

## `app/main.py`

### Purpose

This file creates the FastAPI app object and wires the process-level application behavior:

- title from settings
- lifespan context
- rate limiting
- Prometheus metrics mount
- CORS
- exception handlers
- route registration

It is the ASGI entry point used by Uvicorn/Gunicorn as `app.main:app`.

### Imports and why they are needed

```python
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
```

FastAPI creates the app and exception handler signatures. CORS middleware allows browser frontends. `JSONResponse` creates custom error response bodies.

```python
from app.core.config import settings
from app.core.lifespan import lifespan
```

Settings provide the app title and CORS origins. Lifespan starts/stops infrastructure.

```python
from app.core.exceptions import (...)
```

Custom exceptions are mapped to HTTP responses.

```python
from app.core.limiter import limiter
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from slowapi.middleware import SlowAPIMiddleware
```

SlowAPI is the request rate limiter. The app stores a limiter on `app.state`, registers the default exceeded handler, and adds middleware to enforce route decorators.

```python
from prometheus_client import make_asgi_app
```

Creates a WSGI/ASGI-compatible Prometheus metrics app mounted at `/metrics`.

```python
from app.api.chat_routes import router as chat_router
from app.api.websocket_routes import router as ws_router
from app.api.health_routes import router as health_router
from app.api.export_routes import router as export_router
```

Route modules are imported and included at the bottom.

### Code block: app construction

```python
app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
)
```

What it does: creates the FastAPI application with a dynamic title and startup/shutdown lifecycle.

Why it exists: all routers and middleware attach to this `app`.

Inputs: `settings.APP_NAME`, `lifespan`.

Outputs: global `app`.

Called by: ASGI server imports `app.main:app`.

Calls into: `lifespan` when the server starts and stops.

Risks / edge cases: if `settings` cannot load because dependencies or environment are broken, app import fails.

Frontend/backend impact: no app means no API surface.

### Code block: rate limiting setup

```python
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
```

What it does: attaches SlowAPI limiter to FastAPI, tells FastAPI how to return rate-limit errors, and installs middleware.

Why it exists: route decorators such as `@limiter.limit("10/minute")` need app-level middleware.

Inputs: `limiter`.

Outputs: rate-limited routes.

Called by: app startup/import.

Calls into: SlowAPI middleware.

Risks / edge cases: rate limiting uses remote address, which may be the proxy IP unless proxy headers are configured upstream.

Frontend/backend impact: clients exceeding limits get rate-limit errors before service logic runs.

### Code block: metrics

```python
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)
```

What it does: exposes Prometheus metrics under `/metrics`.

Why it exists: lets monitoring scrape counters and gauges defined in `metrics_service.py`.

Inputs: global Prometheus registry.

Outputs: `/metrics` endpoint.

Called by: Prometheus scraper or humans.

Calls into: prometheus-client ASGI app.

Risks / edge cases: no auth is applied here in code; production exposure must be controlled at network/ingress level.

Frontend/backend impact: operational visibility only.

### Code block: CORS

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

What it does: permits configured browser origins to call REST APIs with credentials and custom headers.

Why it exists: browser frontend calls need CORS permission, especially for `X-SOEID`.

Inputs: comma-separated `CORS_ALLOW_ORIGINS`, converted by `cors_origins_list`.

Outputs: CORS headers on responses.

Risks / edge cases: overly broad origins would allow unwanted browser origins.

Frontend/backend impact: without this, browser REST calls from frontend dev hosts would fail preflight.

### Code block: custom exception handlers

Handlers:

- `ExecutionNotFoundError` -> `404`
- `SessionNotFoundError` -> `404`
- `TokenFetchError` -> `502`
- `BackendExecutorError` -> `502`

What they do: translate Python exceptions to stable JSON API responses.

Why they exist:

- `404` supports privacy-preserving ownership behavior.
- `502` means a downstream integration failed.

Outputs:

```json
{"success": false, "message": "..."}
```

Risks / edge cases: `TokenFetchError` and `BackendExecutorError` hide detailed downstream errors, which is good for clients but requires logs for debugging.

Frontend/backend impact: frontend can treat `404` as missing/unavailable and `502` as retryable/downstream failure.

### Code block: route registration

```python
app.include_router(chat_router)
app.include_router(ws_router)
app.include_router(health_router)
app.include_router(export_router)
```

What it does: attaches route modules to the app.

Why it exists: without inclusion, endpoints are not exposed.

Inputs: APIRouter instances.

Outputs: public HTTP, WebSocket, health, metrics, and PDF export routes.

## `app/core/config.py`

### Purpose

Defines all runtime configuration through Pydantic `BaseSettings`. Values come from environment variables or `.env`.

### Code block: `Settings` class

The class groups configuration into:

- app settings
- MongoDB settings
- Kafka settings
- token service settings
- backend executor settings
- WebSocket settings
- HTTP retry settings

### Important settings

App:

- `APP_NAME`: FastAPI title.
- `APP_ENV`: environment label.
- `APP_HOST`, `APP_PORT`: useful for server startup, though not directly used by code.
- `LOG_LEVEL`: used by logging setup.
- `LOG_FILE_PATH`: file target for rotating logs.
- `CORS_ALLOW_ORIGINS`: comma-separated frontend origins.

MongoDB:

- `MONGODB_URI`: connection string.
- `MONGODB_DATABASE`: database name.

Kafka:

- `KAFKA_BOOTSTRAP_SERVERS`
- `KAFKA_TOPIC`
- `KAFKA_CONSUMER_GROUP`
- `KAFKA_SECURITY_PROTOCOL`
- SSL certificate/key/password settings
- optional debug string

Token:

- `TOKEN_URL`
- `TOKEN_CLIENT_SECRET`
- `TOKEN_TIMEOUT_SECONDS`
- `TOKEN_VERIFY_SSL`

Backend:

- `BACKEND_EXECUTOR_BASE_URL`
- `BACKEND_EXECUTOR_PATH`
- `BACKEND_CONFIG_ID`
- `BACKEND_APPLICATION_ID`
- `BACKEND_REQUEST_TIMEOUT_SECONDS`
- `BACKEND_VERIFY_SSL`

WebSocket:

- `WEBSOCKET_HEARTBEAT_SECONDS`
- `WEBSOCKET_IDLE_TIMEOUT_SECONDS`

Retry:

- `HTTP_RETRY_ATTEMPTS`
- `HTTP_RETRY_BACKOFF_SECONDS`

### Code block: `model_config`

```python
model_config = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore"
)
```

What it does: tells Pydantic settings to load `.env`, decode it as UTF-8, and ignore unknown env vars.

Why it exists: developers can add local config without changing code; extra deployment variables will not break startup.

### Function: `cors_origins_list`

```python
@property
def cors_origins_list(self) -> List[str]:
    return [o.strip() for o in self.CORS_ALLOW_ORIGINS.split(",") if o.strip()]
```

What it does: converts a comma-separated string into a list for FastAPI CORS middleware.

Why it exists: environment variables are strings, but `CORSMiddleware` expects a list.

Input: `CORS_ALLOW_ORIGINS`.

Output: list of non-empty stripped origins.

What breaks if removed: `app/main.py` cannot configure CORS using the expected property.

## `app/core/lifespan.py`

### Purpose

Owns application startup and shutdown. This is where global runtime resources are created and cleaned up.

### Globals

```python
kafka_consumer: KafkaEventConsumer | None = None
ws_manager: WebSocketManager | None = None
```

What they do: hold app-wide singletons.

Why they exist: route code and health checks need access to the live WebSocket manager and Kafka consumer.

Risks: global mutable state complicates tests and multi-process deployments. With Gunicorn multiple workers, each worker has its own consumer and WebSocket manager unless deployment controls worker count and consumer group semantics.

### Function: `get_ws_manager`

Returns the global `WebSocketManager`. Uses `assert ws_manager is not None`.

Called by: `websocket_routes.websocket_progress`.

If called before startup: assertion error.

### Function: `get_kafka_consumer`

Returns the global Kafka consumer or `None`.

Called by: readiness endpoint.

Why it may be `None`: tests or failed Kafka initialization.

### Code block: startup in `lifespan`

```python
setup_logging()
await init_mongo()
ws_manager = WebSocketManager()
kafka_consumer = KafkaEventConsumer(ws_manager=ws_manager)
consumer_task = asyncio.create_task(kafka_consumer.start())
cleanup_task = asyncio.create_task(throttler_cleanup_loop())
yield
```

What it does:

1. configures logging
2. connects to MongoDB and creates indexes
3. creates WebSocket manager
4. creates Kafka consumer that can broadcast through that manager
5. starts Kafka consumer as a background task
6. starts throttler cleanup as a background task
7. yields control to FastAPI to serve requests

Why it exists: resources must be initialized before routes handle traffic.

Inputs: app instance, settings indirectly through called functions.

Outputs: initialized globals and background tasks.

Called by: FastAPI lifespan protocol.

Calls into: `setup_logging`, `init_mongo`, `WebSocketManager`, `KafkaEventConsumer.start`, `throttler_cleanup_loop`.

Risks / edge cases:

- `consumer_task` is created but not directly awaited or cancelled on shutdown. `kafka_consumer.stop()` signals it to stop; this usually ends the executor loop.
- If Kafka initialization fails, `KafkaEventConsumer.start` logs and returns. The app can still run but readiness reports Kafka down.

### Code block: shutdown in `lifespan`

```python
cleanup_task.cancel()
if kafka_consumer:
    await kafka_consumer.stop()
if ws_manager:
    await ws_manager.close_all()
await close_mongo()
```

What it does: stops background cleanup, closes Kafka consumer, closes all WebSocket clients, and closes MongoDB.

Why it exists: graceful shutdown avoids leaked sockets and clients.

Risks: cancelling `cleanup_task` without awaiting it can leave cancellation warnings in some environments, though the loop catches `CancelledError`.

## `app/core/exceptions.py`

### Purpose

Defines custom exception types so services can raise domain/integration errors and `app/main.py` can map them to HTTP responses.

Classes:

- `IntegrationError`: base class for downstream integration errors.
- `TokenFetchError`: token service failure.
- `BackendExecutorError`: backend executor failure.
- `ExecutionNotFoundError`: execution missing or unauthorized.
- `SessionNotFoundError`: session missing or unauthorized.

Each class has no custom behavior. The type itself is the signal.

## `app/core/logging.py`

### Purpose

Configures structured JSON logging for stdout and a rotating file.

### Function: `setup_logging`

Code blocks:

1. Determine log directory from `settings.LOG_FILE_PATH`.
2. Create directory if missing.
3. Convert `settings.LOG_LEVEL` string to a logging level.
4. Create stdout and rotating file handlers.
5. Configure Python logging basic config.
6. Configure `structlog` processors:
   - log level
   - logger name
   - ISO timestamp
   - structured tracebacks
   - JSON renderer

Why it exists: service logs should be machine-readable and persistent.

Side effects:

- creates log directory
- configures process-global logging

Risks:

- repeated calls to `logging.basicConfig` can behave unexpectedly if logging was already configured by a test runner or server.
- `LOG_FILE_PATH` must be writable by the container user.

### Function: `get_logger`

Returns `structlog.get_logger(name)`.

Called by most modules.

Why it exists: centralizes logger creation and keeps call sites consistent.

## `app/core/limiter.py`

### Purpose

Creates a SlowAPI limiter:

```python
limiter = Limiter(key_func=get_remote_address)
```

The key function means rate limits are applied by remote IP address.

Called by: route decorators in `chat_routes.py`; app wiring in `main.py`.

Risk: behind a proxy, `get_remote_address` may see proxy IP unless trusted proxy headers are configured.

## `app/core/throttler.py`

### Purpose

Provides a lightweight in-memory throttle for WebSocket connection attempts.

It is separate from SlowAPI because WebSocket connections do not use the same HTTP request lifecycle as regular REST endpoints.

### Constants

- `_CONNECT_THROTTLE`: dict from `"user:ip"` to last attempt timestamp.
- `THROTTLE_SECONDS = 2.0`: minimum seconds between connection attempts per key.
- `CLEANUP_INTERVAL_SECONDS = 600`: cleanup loop sleeps for 10 minutes.
- `STALE_THRESHOLD_SECONDS = 3600`: entries older than 1 hour are removed.

### Function: `check_throttle(user_id, client_ip)`

What it does:

1. Builds key `"{user_id}:{client_ip}"`.
2. Gets current UNIX timestamp.
3. Looks up previous timestamp.
4. If the difference is under 2 seconds, returns `True`.
5. Otherwise records the new timestamp and returns `False`.

Inputs: user ID, client IP.

Output: `True` means reject; `False` means allow.

Side effects: mutates `_CONNECT_THROTTLE`.

Called by: `websocket_routes.websocket_progress`.

What breaks if removed: rapid reconnect storms could hit MongoDB and WebSocket resources repeatedly.

### Function: `throttler_cleanup_loop`

What it does:

1. Logs startup.
2. Sleeps for `CLEANUP_INTERVAL_SECONDS`.
3. Finds throttle keys older than `STALE_THRESHOLD_SECONDS`.
4. Removes them.
5. Logs cleanup count.
6. Exits cleanly on cancellation.
7. Logs unexpected errors and continues.

Called by: `lifespan`.

Why it exists: without cleanup, `_CONNECT_THROTTLE` grows forever as users/IPs change.

## `app/api/deps.py`

### Purpose

Shared FastAPI dependency for temporary identity handling.

### Function: `get_current_user`

Signature:

```python
async def get_current_user(
    x_soeid: Optional[str] = Header(None, alias="X-SOEID"),
    soeid: Optional[str] = Query(None),
) -> str:
```

What it does:

1. Reads `X-SOEID` header.
2. Reads `soeid` query parameter.
3. Chooses header first, query second.
4. If neither exists, raises `401`.
5. Returns user ID string.

Why it exists: abstracts identity extraction so future JWT/OIDC can replace it without rewriting services.

Inputs:

- REST header `X-SOEID`
- WebSocket/REST query `soeid`

Output: current user ID.

Called by:

- REST routes
- WebSocket route

Security impact:

- Current code trusts the provided SOEID. It is an identity adapter, not full authentication.
- Production should place real auth in front of or inside this dependency.

## `app/api/chat_routes.py`

### Purpose

Defines the REST chat API:

- execute a prompt
- get a single execution status
- get session history

Router prefix:

```python
router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])
```

### Endpoint: `POST /api/v1/chat/execute`

Function: `execute_chat`

Important blocks:

- `@limiter.limit("10/minute")`: rate limit.
- `body: ChatExecuteRequest`: Pydantic validates body.
- `current_user: str = Depends(get_current_user)`: identity.
- `body.soeid = current_user`: trusted identity overrides body.
- `return await ChatExecutionService.execute(body)`: service orchestration.

Why it exists: starts a backend agent execution.

DB impact: service inserts/updates session and execution.

Network impact: service calls token service and backend executor.

### Endpoint: `GET /api/v1/chat/status/{correlation_id}`

Function: `get_status`

Important blocks:

- `@limiter.limit("20/minute")`
- path parameter `correlation_id`
- identity dependency
- delegates to `StatusService.get_execution_status`

Why it exists: lets frontend recover or refresh one execution's current state and event timeline.

DB impact: reads `recon` and `events`.

Ownership: service reads by `correlation_id + soeid`.

Unauthorized behavior: raises `ExecutionNotFoundError`, mapped to `404`.

### Endpoint: `GET /api/v1/chat/history/{session_id}`

Function: `get_history`

Important blocks:

- `@limiter.limit("5/minute")`
- query params:
  - `skip`: default 0, minimum 0
  - `limit`: default 50, min 1, max 200
  - `include_events`: default true
- delegates to `StatusService.get_session_history`

Why it exists: rebuilds a conversation thread.

DB impact:

- reads `sessions` with ownership
- reads `recon` by session
- counts executions
- optionally reads `events` per execution

Performance note: when `include_events=True`, this performs one event query per execution. For large histories, that is an N+1 query pattern.

## `app/api/websocket_routes.py`

### Purpose

Defines the WebSocket endpoint for live progress.

### Imports

Important imports:

- `WebSocket`, `WebSocketDisconnect`, `status`: FastAPI WebSocket types and close code constants.
- `check_throttle`: in-memory connection rate limiting.
- `get_current_user`: identity dependency.
- `get_ws_manager`: global WebSocket manager.
- `ExecutionsRepository`: ownership check.
- `WSConnectedMessage`: initial accepted message.
- `utc_now`, `format_iso`: timestamp.

Unused imports:

- `Optional`, `Dict`
- `ACTIVE_WS_CONNECTIONS`, `WS_CONNECTIONS_TOTAL`

These unused imports do not affect runtime but can be cleaned up.

### Endpoint: `WS /ws/v1/chat/progress/{correlation_id}`

Function: `websocket_progress`

Code block: throttle

```python
client_ip = websocket.client.host if websocket.client else "unknown"
if check_throttle(current_user, client_ip):
    WS_THROTTLED_TOTAL.inc()
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Rate limit exceeded")
    return
```

What it does: rejects too-frequent connection attempts from same user/IP.

Why it exists: protects the service from reconnect storms.

Frontend impact: frontend receives close code `1008`.

Code block: ownership

```python
execution = await ExecutionsRepository.get_execution_by_soeid(correlation_id, current_user)
if not execution:
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Execution not found or unauthorized")
    return
```

What it does: verifies the current user owns the execution.

Why it exists: WebSocket streams expose live progress and must not leak cross-user data.

Code block: accept and connected message

```python
await websocket.accept()
connected_msg = WSConnectedMessage(...)
await websocket.send_json(connected_msg.model_dump())
await ws_manager.connect(correlation_id, websocket)
```

What it does: accepts the socket, sends current state, and registers it for future broadcasts.

Why it exists: clients need immediate confirmation and status even before new Kafka events arrive.

Code block: receive loop

```python
while True:
    await websocket.receive_text()
```

What it does: keeps the route alive until the client disconnects.

Why it exists: FastAPI WebSocket handlers need to keep awaiting to keep the connection open and detect disconnect.

Risks: inbound messages are ignored. If frontend never sends anything, this still waits correctly; server-side heartbeats are sent by the manager, not this loop.

Code block: cleanup

```python
finally:
    await ws_manager.disconnect(correlation_id, websocket)
```

What it does: unregisters the socket.

Why it exists: prevents stale connection tracking.

### Function: `cleanup_throttle_cache`

This function is currently not used and appears broken:

```python
def cleanup_throttle_cache():
    now = time.time()
    stale = [uid for uid, ts in _CONNECT_THROTTLE.items() if now - ts > 60]
    for uid in stale:
        _CONNECT_THROTTLE.pop(uid, None)
```

Problem:

- `_CONNECT_THROTTLE` is defined in `app/core/throttler.py`, not imported here.
- The real cleanup loop already exists as `throttler_cleanup_loop`.

Most likely interpretation: this is leftover code from an earlier local throttle implementation.

What would happen if called: `NameError`.

## `app/api/health_routes.py`

### Purpose

Provides service health endpoints for orchestration and monitoring.

### Endpoint: `GET /health/live`

Function: `health_live`

Returns:

```json
{"status": "ok"}
```

What it means: process is alive enough to serve a lightweight route.

It does not check dependencies.

### Endpoint: `GET /health/ready`

Function: `health_ready`

What it checks:

1. MongoDB: calls `db.command("ping")`.
2. Kafka: gets global consumer and checks `is_initialized()`.
3. Token config: verifies `TOKEN_URL` and `TOKEN_CLIENT_SECRET` are set.
4. Backend config: verifies backend base URL, config ID, and application ID.

Return shape:

```json
{
  "status": "ready" | "degraded",
  "checks": {
    "mongodb": "up" | "down",
    "kafka": "up" | "down",
    "token_client_config": "up" | "down",
    "backend_client_config": "up" | "down"
  }
}
```

Why it exists: deployment systems need to know whether the service is safe to receive traffic.

Risks:

- Token and backend checks are config checks, not network calls. They do not prove external services are reachable.
- Kafka check reports initialized/running, not necessarily able to consume successfully forever.

## `app/api/export_routes.py`

### Purpose

Defines the REST endpoint that turns one execution timeline into a PDF report.

The route reconstructs the report from MongoDB through `PDFExportService`; it does not use WebSocket state.

### Imports

```python
from fastapi import APIRouter, Header, Query, HTTPException, Request
from fastapi.responses import Response
```

FastAPI provides route construction, required header extraction, query parameters, HTTP exceptions, and raw binary response support.

```python
from app.services.pdf_export_service import PDFExportService
from app.core.logging import get_logger
```

The service performs DTO assembly and PDF rendering. The logger records denial and generation failures.

### Router

```python
router = APIRouter(prefix="/api/v1/chat/export")
```

The route defined as `@router.get("/pdf/{correlation_id}")` becomes:

```text
GET /api/v1/chat/export/pdf/{correlation_id}
```

### Endpoint: `export_execution_pdf`

Signature:

```python
async def export_execution_pdf(
    request: Request,
    correlation_id: str,
    x_soeid: str = Header(...),
    include_timestamps: bool = Query(True, ...),
    include_raw: bool = Query(False, ...),
    download: bool = Query(True, ...),
)
```

### Code block: service call

```python
pdf_bytes = await PDFExportService.generate_pdf(
    correlation_id=correlation_id,
    soeid=x_soeid,
    include_raw=include_raw,
)
```

What it does: asks the PDF service to verify ownership, gather MongoDB data, render HTML, and return PDF bytes.

Why it exists: keeps route logic focused on HTTP response behavior.

Inputs: path `correlation_id`, required `X-SOEID` header, `include_raw` query option.

Outputs: bytes or `None`.

Called by: FastAPI when frontend requests a PDF.

Calls into: `PDFExportService.generate_pdf`.

Risks / edge cases: this route does not use the shared `get_current_user`, so it requires the header form of identity and does not accept `soeid` query fallback.

### Code block: not found / unauthorized

```python
if not pdf_bytes:
    logger.warning("PDF export denied or missing", ...)
    raise HTTPException(status_code=404, detail="Execution not found or access denied.")
```

What it does: treats missing execution and ownership mismatch the same way.

Why it exists: matches the privacy policy used by status/history routes, though the JSON shape is FastAPI's default `{"detail": ...}` rather than the custom `{"success": false, "message": ...}` shape.

### Code block: response headers

```python
headers = {"Content-Type": "application/pdf"}
if download:
    headers["Content-Disposition"] = f'attachment; filename="agentic_execution_{correlation_id}.pdf"'
else:
    headers["Content-Disposition"] = f'inline; filename="agentic_execution_{correlation_id}.pdf"'
return Response(content=pdf_bytes, headers=headers)
```

What it does: returns raw PDF bytes and tells the browser whether to download or render inline.

Frontend impact: `download=true` is appropriate for a button-triggered file download; `download=false` lets browsers/plugins display inline.

### Code block: error handling

```python
except RuntimeError:
    raise HTTPException(status_code=500, detail="Cannot generate PDF on this server.")
except Exception:
    raise HTTPException(status_code=500, detail="An error occurred building the export.")
```

What it does: converts rendering failures to `500`.

Why it exists: PDF generation depends on external native libraries and template rendering, both of which can fail independently of MongoDB.

Important import-time risk: `pdf_export_service.py` catches `ImportError` when importing WeasyPrint, but native library load failures can raise `OSError` during import. On a local machine without GObject/Pango, importing `app.main` can fail before this route-level `RuntimeError` handler is reached.

## `app/services/chat_execution_service.py`

### Purpose

This is the main orchestration service for `POST /api/v1/chat/execute`.

It connects:

- ID generation
- token service
- backend executor
- MongoDB session/execution persistence
- metrics
- audit logging
- response construction

### Class: `ChatExecutionService`

Stateless class with one static async method.

### Function: `execute`

Signature:

```python
async def execute(request: ChatExecuteRequest) -> ChatExecuteResponse
```

Code block: generate IDs

```python
correlation_id = generate_correlation_id()
session_id = await SessionService.resolve_session_id(request.session_id)
```

What it does: creates a new run ID and resolves the conversation ID.

Why it exists: backend, Kafka, Mongo, WebSocket, and frontend need stable IDs.

Code block: fetch token

```python
token = await token_client.get_token()
```

What it does: obtains authorization material for backend executor.

Why it exists: middleware must authenticate to backend executor.

Failure: raises `TokenFetchError`, mapped to `502`.

Code block: call backend

```python
backend_response = await backend_executor_client.execute(...)
```

What it does: starts backend agent execution.

Why it exists: middleware itself does not run the agent.

Failure: raises `BackendExecutorError`, mapped to `502`.

Code block: session upsert

```python
await SessionsRepository.create_or_update_session(...)
```

What it does: creates/updates conversation metadata.

DB impact: writes `sessions`.

Code block: execution document

```python
execution_doc = {
    "correlation_id": correlation_id,
    "session_id": session_id,
    "soeid": request.soeid,
    "request_context": request.context,
    "backend_request": {...masked...},
    "backend_ack": backend_response,
    "status": "accepted",
    ...
}
```

What it does: constructs durable initial execution state.

Why it exists: `/status`, `/history`, WebSocket authorization, and event processing all depend on this record.

Important detail: `Config-ID` is stored as `"masked"` and token is not stored.

Code block: execution insert

```python
await ExecutionsRepository.create_execution(execution_doc)
```

DB impact: inserts one document into `recon`.

Potential failure: duplicate `correlation_id` would raise from MongoDB, though UUID collision is extremely unlikely.

Code block: metrics and audit

```python
increment_active_executions("accepted")
audit_logger.log_event("EXECUTION_INITIALIZED", ...)
```

What it does: records operational and audit trace.

Code block: response

```python
return ChatExecuteResponse(...)
```

What it does: returns the stable API contract to the frontend.

Why it exists: frontend needs IDs and WebSocket URL.

## `app/services/session_service.py`

### Purpose

Small service for resolving session identity.

### Function: `resolve_session_id`

Signature:

```python
async def resolve_session_id(session_id: Optional[str]) -> str
```

What it does:

- if a session ID is provided, return it
- otherwise generate a UUID v4

Why it exists:

- keeps session ID behavior separate from execute orchestration
- makes future validation easier to add

Inputs: optional session ID.

Output: non-empty session ID.

Side effects: logging only.

DB impact: none.

Important: despite importing `SessionsRepository`, this function does not query MongoDB. It does not verify that an existing session ID exists or belongs to the user.

## `app/services/status_service.py`

### Purpose

Builds REST status and history response models from MongoDB documents.

### Function: `get_execution_status`

Signature:

```python
async def get_execution_status(correlation_id: str, soeid: str) -> ChatStatusResponse
```

Code block: ownership read

```python
execution = await ExecutionsRepository.get_execution_by_soeid(correlation_id, soeid)
if not execution:
    raise ExecutionNotFoundError(...)
```

What it does: verifies the run exists and belongs to the user.

Why it exists: prevents ID enumeration and cross-user data leaks.

DB impact: reads `recon`.

Code block: load events

```python
events_raw = await EventsRepository.get_events_by_correlation(correlation_id)
```

What it does: fetches the timeline.

Why no `soeid` filter here: ownership was already verified on the unique execution record.

DB impact: reads `events`.

Code block: map events to `EventSummary`

Each raw event becomes:

- `event_type`
- `normalized_event_type`
- `status`
- `agent_name`
- `tool_name`
- `timestamp`
- `summary`

What it does: shapes DB documents into API response models.

Code block: build `StatusData`

Uses execution fields and mapped events.

Output: `ChatStatusResponse(success=True, data=data)`.

What would break if removed: frontend could not refresh a single run.

### Function: `get_session_history`

Signature:

```python
async def get_session_history(
    session_id: str,
    soeid: str,
    skip: int = 0,
    limit: int = 50,
    include_events: bool = True,
) -> ChatHistoryResponse
```

Code block: ownership read

```python
session = await SessionsRepository.get_session_by_soeid(session_id, soeid)
if not session:
    raise SessionNotFoundError(...)
```

What it does: verifies user owns the session.

DB impact: reads `sessions`.

Code block: executions and count

```python
executions_raw = await ExecutionsRepository.get_executions_by_session(...)
total = await ExecutionsRepository.count_executions_by_session(session_id)
```

What it does: gets paginated execution list plus total count.

DB impact: reads `recon`.

Code block: optionally fetch events per execution

```python
if include_events:
    events_raw = await EventsRepository.get_events_by_correlation(ex["correlation_id"])
```

What it does: includes per-execution timeline in history.

Risk: N+1 DB query pattern.

Code block: build `HistoryData`

Returns:

- session metadata
- executions list
- total
- limit
- skip

What would break if removed: frontend could not rebuild conversation history.

## `app/services/pdf_export_service.py`

### Purpose

Builds a printable execution report from MongoDB and renders it to PDF bytes.

This service is part of the recovery/history side of the system. It does not start executions, process Kafka, or use WebSocket state. It reads the durable MongoDB source of truth and formats it for export.

### Imports

```python
import json
from typing import Optional
from jinja2 import Environment, FileSystemLoader
```

`json` is used to stringify dict responses and errors. Jinja2 loads and renders the HTML report template.

```python
try:
    from weasyprint import HTML
except ImportError:
    HTML = None
```

WeasyPrint converts rendered HTML into PDF bytes. If the Python package is not installed, `HTML` becomes `None` and `generate_pdf` raises `RuntimeError`.

Important nuance: missing native WeasyPrint libraries can raise `OSError`, not `ImportError`, during import. In that case app import can fail before `HTML = None` is assigned.

```python
from app.models.export_models import ExportExecutionDTO, ExportEventDTO
from app.db.repositories.executions_repository import ExecutionsRepository
from app.db.repositories.events_repository import EventsRepository
```

The service shapes MongoDB documents into export DTOs, using existing repositories for ownership-safe execution lookup and event timeline loading.

### Class: `PDFExportService`

Stateless service with two static async methods:

- `build_export_dto`
- `generate_pdf`

### Function: `build_export_dto`

Signature:

```python
async def build_export_dto(
    correlation_id: str, soeid: str, include_raw: bool = False
) -> Optional[ExportExecutionDTO]
```

### Code block: ownership-safe execution lookup

```python
execution = await ExecutionsRepository.get_execution_by_soeid(correlation_id, soeid)
if not execution:
    return None
```

What it does: verifies that the execution exists and belongs to the requesting SOEID.

Why it exists: PDF exports contain request context, timeline, final response, and possibly raw payload excerpts, so they must be protected exactly like status/history.

Inputs: `correlation_id`, `soeid`.

Outputs: execution document or `None`.

DB impact: reads `recon` using `(soeid, correlation_id)`.

### Code block: events loading

```python
events = await EventsRepository.get_events_by_correlation(correlation_id)
```

What it does: loads the durable event timeline for the execution.

Why it exists: the PDF is reconstructed from MongoDB, not from transient WebSocket state.

DB impact: reads `events`, sorted by timestamp by repository behavior.

### Code block: event DTO loop

```python
for event in events:
    event_type = event.get("event_type")
    status = event.get("status")
    excerpt = event.get("raw_payload") if include_raw else None
    ev_dto = ExportEventDTO(...)
    event_dtos.append(ev_dto)
```

What it does: transforms Mongo event documents into a report-specific event shape.

Why it exists: the PDF template should consume a stable DTO, not arbitrary MongoDB documents.

Inputs: Mongo event fields.

Outputs: `ExportEventDTO` entries.

Risks / edge cases: `include_raw=True` can expose raw backend payloads in the PDF.

### Code block: final response extraction

```python
if event_type == "EXECUTION_FINAL_RESPONSE" or status == "completed":
    raw_payload = event.get("raw_payload", {})
    normalized_payload = event.get("normalized_payload", {}) or {}
    resp = raw_payload.get("response", "") or normalized_payload.get("response", "")
```

What it does: finds a final response string for the report summary block.

Why it exists: the event timeline may contain final answer content inside raw or normalized payloads; the PDF needs a prominent final-response section.

Outputs:

- dict responses are pretty-printed JSON
- scalar responses become strings

Risk: any event with `status == "completed"` can set `final_response`, not only `EXECUTION_FINAL_RESPONSE`.

### Code block: failure extraction

```python
elif status == "failed" and not error_details:
    raw_payload = event.get("raw_payload", {})
    err = raw_payload.get("response", "") or event.get("summary", "Unknown failure")
```

What it does: captures the first failure reason.

Why it exists: failed reports should show a clear error summary near the top/bottom of the PDF.

Outputs:

- dict errors are pretty-printed JSON
- scalar errors become strings

### Code block: completed fallback

```python
if not final_response and execution.get("status") == "completed":
    final_response = "Execution completed successfully."
```

What it does: provides a friendly completion message if no event payload contains final text.

Why it exists: completed executions should not render an empty final section just because payload data is absent.

### Code block: DTO assembly

```python
dto = ExportExecutionDTO(
    soeid=soeid,
    session_id=execution.get("session_id", ""),
    correlation_id=correlation_id,
    status=execution.get("status", "unknown"),
    request_context=execution.get("request_context", "N/A"),
    ...
)
```

What it does: creates the full report DTO consumed by the template.

Called by: `generate_pdf`; tests also call it directly.

### Function: `generate_pdf`

Signature:

```python
async def generate_pdf(
    correlation_id: str, soeid: str, include_raw: bool = False
) -> Optional[bytes]
```

### Code block: engine availability

```python
if HTML is None:
    raise RuntimeError("WeasyPrint is not installed or available.")
```

What it does: fails clearly if the Python package import failed.

Why it exists: PDF generation cannot proceed without WeasyPrint.

### Code block: DTO creation

```python
dto = await PDFExportService.build_export_dto(correlation_id, soeid, include_raw)
if not dto:
    return None
```

What it does: builds report data and preserves 404/unauthorized behavior by returning `None`.

Called by: `export_routes.export_execution_pdf`.

### Code block: template render

```python
env = Environment(loader=FileSystemLoader("app/templates"))
template = env.get_template("execution_report.html")
html_content = template.render(report=dto.model_dump())
```

What it does: loads the HTML template and renders it with the DTO as a plain dict.

Why it exists: HTML/CSS is easier to design and WeasyPrint can render it to PDF.

Risks / edge cases:

- The path is relative to the process working directory.
- If the server is started from a different directory, template loading may fail.

### Code block: PDF render

```python
pdf_bytes = HTML(string=html_content).write_pdf()
```

What it does: converts HTML into PDF bytes.

Network impact: none.

Filesystem impact: reads template only; does not write a file.

Output: `bytes`.

What would break if removed: export route could build data but not return a PDF.

## `app/services/event_processing_service.py`

### Purpose

This is the core Kafka event normalization and persistence service.

It is the most important data integrity layer in the codebase.

### Class: `EventProcessingService`

Stateless class with one static async method.

### Function: `process_kafka_event`

Signature:

```python
async def process_kafka_event(
    raw_event: RawKafkaEvent,
    correlation_id: str,
    kafka_metadata: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]
```

Code block: allowed event filter

```python
event_type = raw_event.event_type
if event_type not in ALLOWED_EVENT_TYPES:
    KAFKA_EVENTS_IGNORED_TOTAL.inc()
    return None
```

What it does: ignores unsupported event types.

Why it exists: backend/Kafka may contain events not meant for this frontend flow.

Impact: caller treats `None` as no broadcast needed.

Code block: mapping

```python
normalized_type = EVENT_NORMALIZATION_MAP.get(event_type, "unknown")
execution_status = EXECUTION_STATUS_MAP.get(event_type, "unknown")
summary_template = EVENT_SUMMARY_MAP.get(event_type, "Event received")
summary = summary_template.format(...)
```

What it does: converts backend event vocabulary to middleware/frontend vocabulary.

Why it exists: frontend should not need to understand raw backend events directly.

Inputs: raw event type, optional tool/agent names.

Outputs: normalized type, status, human summary.

Code block: timestamp

```python
event_timestamp = raw_event.timestamp or format_iso(utc_now())
```

What it does: uses backend-provided timestamp if present, otherwise middleware timestamp.

Why it exists: events require stable ordering and idempotency keys.

Risk: if backend duplicates an event but changes timestamp, idempotency will not suppress it.

Code block: normalized payload

```python
if raw_event.response is not None:
    if isinstance(raw_event.response, dict):
        normalized_payload = {"response": raw_event.response}
    else:
        normalized_payload = {"response": str(raw_event.response)}
```

What it does: wraps response data consistently under `response`.

Why it exists: WebSocket payload shape remains predictable.

Code block: execution lookup

```python
execution = await ExecutionsRepository.get_execution(correlation_id)
if not execution:
    logger.warning(...)
    return None
```

What it does: obtains `session_id` and `soeid` from the execution record.

Why it exists: Kafka event payload only carries correlation ID; Mongo ties that to owner/session.

DB impact: reads `recon`.

Risk: if Kafka event arrives before execution insert is committed, event is skipped and committed by consumer.

Code block: idempotency key

```python
idempotency_key = f"{correlation_id}:{event_type}:{event_timestamp}:{raw_event.tool_name or ''}"
```

What it does: creates a duplicate detection key.

Why it exists: Kafka redelivery and rebalance can produce duplicate messages.

Risk: two distinct tool events with same correlation/event/timestamp/tool collide. Conversely, duplicate events with slightly different timestamps are not deduplicated.

Code block: event document

Fields:

- event identity and ownership
- raw and normalized event names
- status
- agent/tool names
- timestamp
- summary
- raw payload
- normalized payload
- created timestamp
- optional Kafka metadata

What it does: creates the MongoDB event document.

DB impact: eventual insert into `events`.

Code block: metadata prefixing

```python
for k, v in kafka_metadata.items():
    event_doc[f"kafka_{k}"] = v
```

What it does: stores Kafka debug metadata with `kafka_` prefixes.

Note: `db_models.EventDocument` defines `consumed_at`, but this code writes `kafka_consumed_at`. That model is documentation-like and not enforced at insert time.

Code block: insert event

```python
inserted = await EventsRepository.insert_event(event_doc)
if not inserted:
    KAFKA_EVENTS_DUPLICATE_TOTAL.inc()
    return None
```

What it does: persists or suppresses duplicate.

Why it exists: event insert must happen before status update and Kafka commit.

DB impact: writes `events`.

Code block: persistence exception

```python
except Exception as e:
    KAFKA_PERSISTENCE_FAILURES_TOTAL.inc()
    raise e
```

What it does: increments failure metric and rethrows.

Why it exists: Kafka consumer must know not to commit.

Code block: update execution status

```python
old_status = execution["status"]
completed_at = event_timestamp if execution_status in ("completed", "failed") else None
await ExecutionsRepository.update_execution_status(...)
```

What it does: mutates current execution state in `recon`.

Why it exists: status/history endpoints need current state without recalculating from all events.

DB impact: updates `recon`.

Code block: audit and metrics

```python
audit_logger.log_event("KAFKA_EVENT_PROCESSED", ...)
KAFKA_EVENTS_PROCESSED_TOTAL.labels(event_type=event_type).inc()
if old_status != execution_status:
    decrement_active_executions(old_status)
    increment_active_executions(execution_status)
```

What it does: records processing and moves status gauges.

Risk: gauges can become inaccurate if process restarts, events are reprocessed after restart, or old status count was never initialized in this process.

Code block: return broadcast dict

Returns normalized event payload for WebSocket.

What it does: decouples event processing from WebSocket message model construction.

Called by: Kafka consumer.

## `app/services/websocket_manager.py`

### Purpose

Tracks active WebSocket clients by `correlation_id` and sends messages to them.

This manager is in-memory. It does not share connections across processes.

### Internal state

```python
self._connections: Dict[str, Set[WebSocket]] = {}
self._heartbeat_tasks: Dict[str, asyncio.Task] = {}
```

`_connections` maps each correlation ID to all connected clients watching that execution.

`_heartbeat_tasks` maps each correlation ID to one heartbeat loop task.

### Method: `connect`

What it does:

1. Creates a connection set for the correlation ID if missing.
2. Adds the WebSocket object.
3. Increments active and total WebSocket metrics.
4. Starts heartbeat loop if this is the first client for that correlation ID.
5. Logs connection.

Called by: WebSocket route after accepting and sending connected message.

Side effects: mutates `_connections`, creates background task.

### Method: `disconnect`

What it does:

1. Removes socket from the correlation ID set.
2. Decrements active WebSocket gauge.
3. If no clients remain, deletes the set.
4. Cancels and removes heartbeat task for that correlation ID.
5. Logs disconnection.

Called by: WebSocket route `finally`.

Risk: if called for a socket that was not actually registered but the correlation ID exists, it still decrements the gauge. Current route calls it after `connect`, so normal flow is safe.

### Method: `broadcast`

What it does:

1. Returns immediately if nobody is connected for the correlation ID.
2. Extracts session ID, event type, status, and timestamp.
3. Builds `WSEventMessage`.
4. Sends it to all clients.
5. Builds `WSStatusMessage`.
6. Sends it to all clients.
7. If status is `completed` or `failed`, sends terminal message and closes all clients.

Called by: Kafka consumer after successful event persistence.

Inputs: correlation ID and normalized event dict.

Outputs: none; sends WebSocket messages.

Network impact: WebSocket `send_json` and possibly `close`.

Risk: no per-client backpressure strategy. Slow or broken clients are removed on send exception.

### Method: `send_personal`

What it does: sends one JSON message to one WebSocket and logs warning if it fails.

Called by: no current code path.

Likely purpose: future direct messages or errors.

### Method: `send_terminal_and_close`

What it does:

1. Returns if no connections exist.
2. Builds `WSCompletedMessage` if status is `completed`.
3. Otherwise builds `WSFailedMessage`.
4. Sends terminal message to all clients.
5. Closes all WebSocket connections for that correlation ID.
6. Removes connection set.
7. Cancels heartbeat task.

Called by: `broadcast` and tests.

Why it exists: terminal execution state means no more live progress is expected.

Frontend impact: clients should treat terminal messages and socket close as normal end of stream.

### Method: `_send_to_all`

What it does:

1. Returns if no connection set.
2. Iterates over sockets.
3. Calls `send_json`.
4. Logs failures and remembers broken sockets.
5. Removes broken sockets from the set.

Called by: `broadcast`, `send_terminal_and_close`, `_heartbeat_loop`.

Risk: modifying sets while iterating is avoided by collecting disconnected sockets first. However, no lock protects `_connections`; this is usually acceptable on one event loop but concurrent coroutines can interleave.

### Method: `_heartbeat_loop`

What it does:

1. Sleeps `WEBSOCKET_HEARTBEAT_SECONDS`.
2. If correlation ID has no connections, exits.
3. Builds `WSHeartbeatMessage`.
4. Sends to all clients.
5. Repeats until cancelled or no connections.

Called by: `connect`.

Why it exists: keeps connections alive through idle periods and lets frontend know stream is still open.

### Method: `close_all`

What it does:

1. Iterates all correlation IDs and sockets.
2. Closes every WebSocket.
3. Clears `_connections`.
4. Cancels heartbeat tasks.
5. Clears `_heartbeat_tasks`.

Called by: app shutdown.

Why it exists: graceful resource cleanup.

## `app/services/metrics_service.py`

### Purpose

Defines all Prometheus metrics used by the app.

### Kafka counters

- `KAFKA_MESSAGES_CONSUMED_TOTAL`: every Kafka message returned by poll before error filtering.
- `KAFKA_EVENTS_PROCESSED_TOTAL{event_type}`: normalized and persisted events.
- `KAFKA_EVENTS_IGNORED_TOTAL`: unsupported events ignored by `EventProcessingService`.
- `KAFKA_EVENTS_DUPLICATE_TOTAL`: duplicates suppressed by idempotent insert.
- `KAFKA_PERSISTENCE_FAILURES_TOTAL`: DB failures during event persistence.
- `KAFKA_COMMIT_SUCCESS_TOTAL`: successful offset commits.
- `KAFKA_COMMIT_FAILURE_TOTAL`: failed offset commits.

### Execution gauge

- `ACTIVE_EXECUTIONS_BY_STATUS{status}`: incremented/decremented as execution statuses change.

Important interpretation: because it increments terminal statuses too, it is closer to "process-local count by last observed status" than strictly "currently active work".

### WebSocket metrics

- `ACTIVE_WS_CONNECTIONS`: current open WebSocket count.
- `WS_CONNECTIONS_TOTAL`: total accepted WebSocket connections.
- `WS_THROTTLED_TOTAL`: rejected WebSocket connections due to throttle.

### Helper functions

- `increment_active_executions(status_val)`: increments gauge label.
- `decrement_active_executions(status_val)`: decrements gauge label.

## `app/clients/token_client.py`

### Purpose

Fetches and caches auth tokens for backend executor calls.

### Constants

`_REFRESH_BUFFER_SECONDS = 60`

Meaning: refresh token one minute before expiry.

Risk: if `expires_in` or fallback timeout is less than 60 seconds, `_expires_at` is in the past, so the token effectively never caches.

### Class: `TokenClient`

State:

- `_token`: cached token string.
- `_expires_at`: UNIX timestamp when token should be considered expired.

### Method: `__init__`

Initializes empty cache.

### Method: `get_token`

What it does:

1. If cached token exists and current time is before `_expires_at`, return it.
2. Otherwise log and fetch a new token.

Called by: `ChatExecutionService.execute`.

Network impact: none if cache valid; outbound token call if expired.

### Method: `_fetch_token`

Decorated with `@async_retry`.

What it does:

1. Creates `httpx.AsyncClient` with timeout and SSL verification settings.
2. POSTs to token URL with `{"clientSecret": ...}`.
3. Raises `TokenFetchError` on HTTP status or request error.
4. Reads response text.
5. If raw text starts with `ey`, treats it as a raw JWT.
6. Otherwise attempts JSON decode.
7. Extracts `access_token`.
8. Handles rare case where decoded JSON itself is a token string.
9. Uses `expires_in` if available, otherwise `TOKEN_TIMEOUT_SECONDS`.
10. Caches token and expiry.
11. Returns token.

Inputs: settings.

Outputs: token string.

Network impact: outbound HTTP POST.

Errors:

- HTTP status failure -> `TokenFetchError`
- request failure -> `TokenFetchError`
- unexpected format -> `TokenFetchError`
- missing `access_token` -> `TokenFetchError`

What would break if removed: execute flow cannot authenticate to backend executor.

## `app/clients/backend_executor_client.py`

### Purpose

Calls the external backend conversational task executor.

### Class: `BackendExecutorClient`

Stateless class with one static async method.

### Method: `execute`

Decorated with `@async_retry`.

Signature:

```python
async def execute(
    context: str,
    correlation_id: str,
    session_id: str,
    soeid: str,
    token: str,
) -> Dict[str, Any]
```

What it does:

1. Builds URL from backend base URL and path.
2. Builds required headers.
3. Builds body `{"context": context}`.
4. Masks sensitive headers for logging.
5. Sends POST with timeout and SSL verification settings.
6. Converts HTTP status/request failures to `BackendExecutorError`.
7. Parses JSON response.
8. Logs response key names.
9. Returns response dict.

Inputs:

- prompt context
- IDs
- user ID
- bearer token
- settings

Outputs: backend acknowledgment dict.

Network impact: outbound HTTP POST to backend executor.

Errors:

- HTTP status error -> `BackendExecutorError`
- request error/timeouts -> `BackendExecutorError`
- JSON decode error is not caught explicitly and will propagate, then retry only if it matches configured exception types. It likely does not match, depending on exception class.

What would break if removed: middleware could create local records but could not start backend work.

## `app/clients/kafka_consumer.py`

### Purpose

Consumes backend execution events from Kafka, delegates normalization/persistence, broadcasts live updates, and commits offsets safely.

### Class: `KafkaEventConsumer`

State:

- `_ws_manager`: WebSocket manager for broadcasts.
- `_running`: controls poll loop.
- `_consumer`: confluent-kafka consumer instance.

### Method: `__init__`

Stores WebSocket manager, sets running false, consumer none.

### Method: `_build_config`

What it does:

1. Creates base config:
   - `bootstrap.servers`
   - `group.id`
   - `auto.offset.reset: earliest`
   - `enable.auto.commit: False`
2. Adds security protocol if configured.
3. Adds SSL CA/cert/key/password if configured.
4. Adds debug if configured.
5. Returns config dict.

Why it exists: isolates environment-to-confluent config mapping.

Important: manual commits are enabled by setting auto commit false.

### Method: `start`

What it does:

1. Sets `_running = True`.
2. Builds config.
3. Defines assignment, revocation, and error callbacks.
4. Creates `Consumer(config)`.
5. Subscribes to `settings.KAFKA_TOPIC`.
6. Logs subscription.
7. Gets current event loop.
8. Awaits `loop.run_in_executor(None, self._poll_loop, loop)`.

Called by: `lifespan` as a background task.

Why run in executor: `confluent_kafka.Consumer.poll` is blocking.

Failure: if `Consumer` creation/subscription raises `KafkaException`, logs and returns. The app can still start but readiness Kafka check fails.

### Method: `_poll_loop`

What it does:

1. Logs startup.
2. While `_running`, calls `poll(timeout=1.0)`.
3. Skips `None`.
4. Increments consumed metric.
5. Handles Kafka errors.
6. Schedules `_process_message(msg)` on the asyncio loop.
7. Continues polling.
8. Logs exit when `_running` false.

Important behavior: processing is scheduled and not awaited by the poll loop. This allows overlapping message processing. It improves throughput but can allow commits to occur out of order if processing durations differ.

### Method: `_process_message`

Signature:

```python
async def _process_message(self, msg) -> None
```

Logical blocks:

1. Extract raw value, key, topic, partition, offset.
2. Decode and parse JSON.
3. Commit malformed messages and return.
4. Ignore/commit unsupported event types.
5. Require `x_correlation_id`; commit and return if missing.
6. Build Kafka metadata.
7. Create `RawKafkaEvent`.
8. Call `EventProcessingService.process_kafka_event`.
9. If processor returns `None`, commit and return.
10. If processing raises, log and do not commit.
11. Broadcast processed event to WebSocket subscribers.
12. Broadcast errors are logged but do not stop commit.
13. Commit offset.

Why this ordering matters:

- Persistence happens before broadcast and commit.
- Broadcast failure does not cause event redelivery, because MongoDB has already persisted the event.
- DB failure prevents commit, so Kafka can redeliver.

### Method: `_safe_commit`

What it does:

1. Calls `self._consumer.commit(message=msg, asynchronous=False)`.
2. Increments commit success metric.
3. On exception, increments commit failure metric and logs error.

Why synchronous commit: after persistence, the service wants clear commit success/failure semantics.

### Method: `stop`

What it does:

1. Sets `_running = False`.
2. Closes consumer if present.
3. Logs closure.

Called by: app shutdown.

### Method: `is_initialized`

Returns:

```python
self._consumer is not None and self._running
```

Called by: readiness endpoint.

## `app/db/mongo.py`

### Purpose

Owns MongoDB client/database globals and index creation.

### Globals

- `_client`: `AsyncIOMotorClient | None`
- `_db`: `AsyncIOMotorDatabase | None`

### Function: `get_database`

Returns `_db`, asserting it is initialized.

Called by all repositories and health checks.

What breaks if called before startup: assertion error.

### Function: `init_mongo`

What it does:

1. Creates `AsyncIOMotorClient(settings.MONGODB_URI)`.
2. Selects `settings.MONGODB_DATABASE`.
3. Logs connection.
4. Calls `_create_indexes`.

Called by: lifespan startup.

Network impact: MongoDB connection initialization and index commands.

### Function: `close_mongo`

What it does:

1. If client exists, closes it.
2. Resets globals to `None`.
3. Logs close.

Called by: lifespan shutdown.

### Function: `_create_indexes`

Creates indexes:

Sessions:

- unique `session_id`
- compound `(soeid, session_id)`

Recon/executions:

- unique `correlation_id`
- `session_id`
- compound `(soeid, correlation_id)`

Events:

- `correlation_id`
- `session_id`
- `timestamp`
- compound `(session_id, timestamp)`
- compound `(soeid, correlation_id)`
- unique `event_idempotency_key`

Why these indexes exist:

- fast ownership checks
- fast status/history lookups
- chronological event ordering
- duplicate suppression

## `app/db/repositories/sessions_repository.py`

### Purpose

Encapsulates MongoDB operations for `sessions`.

### Method: `create_or_update_session`

See execute flow for full update document.

Side effects: upserts one session.

Called by: `ChatExecutionService.execute`.

Risk: filters by `session_id` only, so it can overwrite `soeid` for an existing session ID.

### Method: `get_session`

Finds by `session_id`, excludes Mongo `_id`.

Called by: no current runtime path.

### Method: `get_session_by_soeid`

Finds by both `soeid` and `session_id`, excludes `_id`.

Called by: `StatusService.get_session_history`.

Purpose: ownership gate for history.

## `app/db/repositories/executions_repository.py`

### Purpose

Encapsulates MongoDB operations for `recon`, the execution collection.

### Method: `create_execution`

Inserts a new execution document.

Called by: `ChatExecutionService.execute`.

DB impact: `db.recon.insert_one`.

### Method: `update_execution_status`

Updates current execution state by `correlation_id`.

Fields always set:

- `status`
- `latest_event_type`
- `updated_at`

Field conditionally set:

- `completed_at`

Called by: `EventProcessingService.process_kafka_event`.

### Method: `get_execution`

Reads execution by `correlation_id`, excludes `_id`.

Called by: `EventProcessingService.process_kafka_event`.

Purpose: event processor needs session and user ownership context.

### Method: `get_execution_by_soeid`

Reads execution by `soeid` and `correlation_id`, excludes `_id`.

Called by:

- `StatusService.get_execution_status`
- `websocket_routes.websocket_progress`

Purpose: ownership gate.

### Method: `get_executions_by_session`

Reads executions by `session_id`, sorted by `created_at` ascending, with skip/limit.

Called by: `StatusService.get_session_history`.

Purpose: conversation history.

### Method: `count_executions_by_session`

Counts executions for a session.

Called by: `StatusService.get_session_history`.

Purpose: pagination metadata.

## `app/db/repositories/events_repository.py`

### Purpose

Encapsulates MongoDB operations for `events`.

### Method: `insert_event`

What it does:

1. Attempts `db.events.insert_one(doc)`.
2. Logs and returns `True` on success.
3. Catches `DuplicateKeyError`.
4. Logs duplicate and returns `False`.

Called by: `EventProcessingService.process_kafka_event`.

Why it exists: centralizes idempotent insert behavior.

### Method: `get_events_by_correlation`

Reads events for a correlation ID, sorted by timestamp ascending, excludes `_id`, max 1000 returned.

Called by:

- `StatusService.get_execution_status`
- `StatusService.get_session_history`

Purpose: execution timeline.

### Method: `get_events_by_session`

Reads events for a session, sorted by timestamp ascending, skip/limit.

Called by: no current code path.

Likely purpose: future session-level event timeline queries.

## `app/models/api_requests.py`

### Purpose

Defines inbound request body schema for execute.

### Class: `ChatExecuteRequest`

Fields:

- `context: str`: user prompt/investigation query.
- `soeid: str`: user identifier, currently required even though route overwrites it.
- `session_id: Optional[str]`: existing conversation ID to reuse.
- `metadata: Optional[Dict[str, Any]]`: client metadata.

Why it exists: FastAPI validation and OpenAPI schema.

## `app/models/api_responses.py`

### Purpose

Defines REST response schemas.

### Execute models

`ExecuteData`:

- `correlation_id`: run ID.
- `session_id`: conversation ID.
- `status`: initial status, usually `accepted`.
- `backend_ack`: sanitized acknowledgment for client.
- `websocket_url`: relative WebSocket URL.
- `created_at`: ISO timestamp.

`ChatExecuteResponse`:

- `success`
- `message`
- `data`

### Status models

`EventSummary`:

- `event_id`: optional but not currently populated.
- `event_type`: raw backend event type.
- `normalized_event_type`: middleware/frontend event type.
- `status`: execution status after this event.
- `agent_name`
- `tool_name`
- `timestamp`
- `summary`

`StatusData`:

- execution identity
- owner
- current status
- latest event
- timestamps
- original request context
- events list

`ChatStatusResponse`:

- `success`
- `data`

### History models

`ExecutionInHistory`:

- correlation ID
- status
- request context
- start/completion timestamps
- latest event
- events

`HistoryData`:

- session ID
- owner
- session timestamps
- execution list
- total/limit/skip pagination fields

`ChatHistoryResponse`:

- `success`
- `data`

### Health models

`HealthLiveResponse`:

- `status`

`HealthReadyChecks`:

- `mongodb`
- `kafka`
- `token_client_config`
- `backend_client_config`

`HealthReadyResponse`:

- `status`
- `checks`

## `app/models/db_models.py`

### Purpose

Documents MongoDB document shapes as Pydantic models.

Important: repositories do not instantiate these models before writing. They are schema documentation and reusable validation models if future code chooses to use them.

### `SessionDocument`

Fields:

- `session_id`
- `soeid`
- `created_at`
- `updated_at`
- `last_correlation_id`
- `metadata`

### `ExecutionDocument`

Fields:

- `correlation_id`
- `session_id`
- `soeid`
- `request_context`
- `backend_request`
- `backend_ack`
- `status`
- `latest_event_type`
- `created_at`
- `updated_at`
- `completed_at`

### `EventDocument`

Fields:

- idempotency key
- correlation/session/user identity
- raw and normalized event type
- status
- agent/tool
- timestamp
- summary
- raw/normalized payloads
- Kafka metadata
- created timestamp

Mismatch note: event processing writes `kafka_consumed_at`; model has `consumed_at`. Since the model is not enforced, runtime still works, but documentation/schema alignment is imperfect.

## `app/models/export_models.py`

### Purpose

Defines the report-specific DTOs used by `PDFExportService` and the Jinja2 template.

These models are not REST response models. They are internal data-transfer shapes for the export pipeline:

```text
Mongo execution/events -> ExportExecutionDTO/ExportEventDTO -> Jinja2 template -> WeasyPrint PDF
```

### `ExportEventDTO`

Fields:

- `event_type: str`: raw backend/Kafka event type.
- `normalized_event_type: str`: normalized event name used by middleware/frontend.
- `status: str`: execution status associated with the event.
- `summary: str`: human-readable summary from the persisted event.
- `timestamp: str`: event timestamp rendered in the report timeline.
- `tool_name: Optional[str]`: tool name, shown only when present.
- `raw_payload_excerpt: Optional[Any]`: raw payload included only when `include_raw=true`.

Why it exists: the PDF template needs a clean, predictable event shape rather than direct MongoDB documents.

### `ExportExecutionDTO`

Fields:

- `soeid: str`: report owner/requesting user.
- `session_id: str`: conversation ID.
- `correlation_id: str`: execution/run ID.
- `status: str`: current execution status.
- `request_context: str`: original prompt/request text.
- `started_at: Optional[str]`: execution creation timestamp.
- `completed_at: Optional[str]`: terminal timestamp when available.
- `latest_event_type: Optional[str]`: most recent raw event type stored on execution.
- `events: List[ExportEventDTO] = []`: timeline events.
- `final_response: Optional[str]`: extracted final answer or completed fallback text.
- `error_details: Optional[str]`: extracted failure reason for failed runs.

Why it exists: it is the single object rendered by `execution_report.html`.

Risk: `events` uses a mutable list default. Pydantic v2 handles model defaults more safely than plain dataclasses, but `Field(default_factory=list)` would still make the intent clearer.

## `app/models/kafka_events.py`

### Purpose

Defines event vocabulary and Pydantic event models.

### `ALLOWED_EVENT_TYPES`

Allowed raw events:

- `TOOL_INPUT_EVENT`
- `TOOL_OUTPUT_EVENT`
- `TOOL_ERROR_EVENT`
- `AGENT_START_EVENT`
- `AGENT_COMPLETION_EVENT`
- `AGENT_ERROR_EVENT`
- `EXECUTION_FINAL_RESPONSE`

Only these events are processed.

### `EVENT_NORMALIZATION_MAP`

Maps backend names to frontend/middleware names:

- `AGENT_START_EVENT` -> `agent_started`
- `TOOL_INPUT_EVENT` -> `tool_started`
- `TOOL_OUTPUT_EVENT` -> `tool_completed`
- `TOOL_ERROR_EVENT` -> `tool_failed`
- `AGENT_COMPLETION_EVENT` -> `agent_completed`
- `AGENT_ERROR_EVENT` -> `agent_failed`
- `EXECUTION_FINAL_RESPONSE` -> `agent_completed`

### `EXECUTION_STATUS_MAP`

Maps events to execution-level status:

- `AGENT_START_EVENT` -> `running`
- `TOOL_INPUT_EVENT` -> `in_progress`
- `TOOL_OUTPUT_EVENT` -> `in_progress`
- `AGENT_COMPLETION_EVENT` -> `in_progress`
- `TOOL_ERROR_EVENT` -> `failed`
- `AGENT_ERROR_EVENT` -> `failed`
- `EXECUTION_FINAL_RESPONSE` -> `completed`

Important design meaning: agent completion is not treated as full execution completion. Only final response completes the run. This is consistent with a multi-agent or post-processing workflow where an agent can complete before the final response is ready.

### `EVENT_SUMMARY_MAP`

Human-readable summary templates:

- `AGENT_START_EVENT`: `Agent started: {agent_name}`
- `TOOL_INPUT_EVENT`: `Tool invoked: {tool_name}`
- `TOOL_OUTPUT_EVENT`: `Tool output received: {tool_name}`
- `TOOL_ERROR_EVENT`: `Tool failed: {tool_name}`
- `AGENT_COMPLETION_EVENT`: `Agent completed: {agent_name}`
- `AGENT_ERROR_EVENT`: `Agent failed: {agent_name}`
- `EXECUTION_FINAL_RESPONSE`: `Final response generated`

### `RawKafkaEvent`

Flexible event input model. Fields are optional because different event types contain different data.

Fields:

- `x_correlation_id`
- `status`
- `event_type`
- `agent_name`
- `tool_name`
- `invocation_id`
- `function_call_id`
- `response`
- `timestamp`

### `NormalizedEvent`

Normalized event shape ready for persistence/broadcast.

Not currently instantiated in the processor, but documents the intended normalized shape.

### `KafkaMessageMetadata`

Documents Kafka metadata:

- topic
- partition
- offset
- consumed_at
- raw_key

The processor uses a plain dict and prefixes these keys with `kafka_`.

## `app/models/websocket_messages.py`

### Purpose

Defines message envelopes sent over WebSocket.

### `WSConnectedMessage`

Sent immediately after socket accept.

Fields:

- `type = "connected"`
- `correlation_id`
- `session_id`
- `status`
- `timestamp`

### `WSEventMessage`

Sent for each persisted Kafka event.

Fields:

- `type = "event"`
- `correlation_id`
- `session_id`
- `event`: dict containing event details

### `WSStatusMessage`

Sent after event messages as a compact status update.

Fields:

- `type = "status"`
- `correlation_id`
- `status`
- `latest_event_type`
- `timestamp`

### `WSCompletedMessage`

Terminal success.

Fields:

- `type = "completed"`
- `correlation_id`
- `status = "completed"`
- `latest_event_type`
- `timestamp`

### `WSFailedMessage`

Terminal failure.

Fields:

- `type = "failed"`
- `correlation_id`
- `status = "failed"`
- `latest_event_type`
- `timestamp`
- `error`

### `WSHeartbeatMessage`

Periodic keep-alive.

Fields:

- `type = "heartbeat"`
- `correlation_id`
- `timestamp`

## `app/templates/execution_report.html`

### Purpose

HTML/CSS report template used by `PDFExportService.generate_pdf`.

It is rendered with:

```python
template.render(report=dto.model_dump())
```

Then WeasyPrint converts the rendered HTML string into PDF bytes.

### Major template sections

Metadata block:

- requested-by SOEID
- session ID
- correlation ID
- status
- started timestamp
- completed timestamp when present

User request block:

- renders `report.request_context`

Timeline block:

- loops over `report.events`
- renders timestamp, summary, raw event type, optional tool name
- colors the timeline dot through `status-{{ event.status }}`
- optionally renders raw payload excerpt with Jinja's `pprint` filter

Final response block:

- rendered only when `report.final_response` exists

Error details block:

- rendered only when `report.error_details` exists

### Why it exists

Keeping report layout in a template avoids hardcoding PDF HTML in Python. It also lets designers/developers modify PDF presentation without touching data-fetching logic.

### Risks / edge cases

- Raw payload excerpts can expose sensitive backend data if `include_raw=true`.
- Template path resolution depends on the app running from the repository root or another working directory where `app/templates` is valid.
- The template renders stored `request_context`; because that may contain user-provided sensitive content, access must stay ownership-gated.

## `app/utils/audit_logger.py`

### Purpose

Structured audit logger with redaction.

### Constants

`SENSITIVE_FIELDS`:

- token
- access_token
- clientSecret
- Config-ID
- X-Authorization-Coin
- password

`ALLOWED_BODY_FIELDS`:

- session_id
- correlation_id
- soeid
- metadata

### Class: `AuditLogger`

### Method: `log_event`

What it does:

1. Creates a new payload.
2. Redacts keys in `SENSITIVE_FIELDS`.
3. Redacts direct `context` or `request_context` values with length only.
4. For nested `body` dicts, only allows whitelisted fields and masks the rest.
5. Logs using structlog logger named `audit`.

Called by:

- `ChatExecutionService.execute`
- `EventProcessingService.process_kafka_event`

Why it exists: audit logs should record lifecycle without leaking prompts, tokens, or secrets.

## `app/utils/retry.py`

### Purpose

Provides async retry decorator used by HTTP integration clients.

### Function: `async_retry`

Signature:

```python
def async_retry(attempts=3, backoff_seconds=2.0, exception_types=(Exception,))
```

What it does:

1. Returns a decorator.
2. Decorator wraps async function.
3. For each attempt:
   - tries to await function
   - catches configured exception types
   - if final attempt, logs and breaks
   - otherwise sleeps exponential backoff
4. Raises last error.

Backoff formula:

```python
sleep_time = backoff_seconds * (2 ** (attempt - 1))
```

Called by:

- `TokenClient._fetch_token`
- `BackendExecutorClient.execute`

Risk: if `attempts` is zero, `last_err` remains `None` and `raise last_err` would be invalid. Settings default to 3.

## `app/utils/ids.py`

### Purpose

Generates UUID v4 IDs.

Functions:

- `generate_correlation_id()`
- `generate_session_id()`

Both return `str(uuid.uuid4())`.

## `app/utils/datetime_utils.py`

### Purpose

Centralizes timezone-aware UTC timestamps and ISO formatting.

Functions:

- `utc_now()`: returns `datetime.now(timezone.utc)`.
- `format_iso(dt)`: returns `dt.isoformat()`.

Why it exists: consistent timestamp style across documents and WebSocket messages.

## `websocket_tester.html`

### Purpose

Manual WebSocket testing utility.

Code blocks:

- HTML inputs for SOEID and correlation ID.
- Connect button builds WebSocket URL.
- `onopen` logs success and disables button.
- `onmessage` parses JSON and appends pretty printed log entry.
- `onclose` logs code and explains `1008`.
- Clear button resets log.

Why it exists: quick manual validation of live progress without building full frontend.

## Documentation HTML/Markdown files

The existing Markdown and HTML documentation files do not participate in runtime. They serve developer onboarding, frontend handoff, deployment, and architecture visualization. They are important for humans but not imported by application code.

---

# 6. Function-by-function explanation

This section lists every function/method in the runtime codebase with call relationships, effects, and why it matters.

## Application and core functions

### `app.main.execution_not_found_handler(request, exc)`

- Defined in: `app/main.py`
- Called by: FastAPI when `ExecutionNotFoundError` is raised.
- Calls: `JSONResponse`.
- Inputs: request, exception.
- Output: HTTP 404 JSON response.
- Side effects: none.
- DB/network impact: none.
- Errors: none expected.
- Why needed: maps missing/unauthorized execution to privacy-preserving 404.
- If removed: FastAPI returns default 500 or generic error for this domain exception.
- Simple English: "When code says an execution cannot be found, turn that into a 404 response."

### `app.main.session_not_found_handler(request, exc)`

- Defined in: `app/main.py`
- Called by: FastAPI on `SessionNotFoundError`.
- Calls: `JSONResponse`.
- Inputs: request, exception.
- Output: HTTP 404 JSON response.
- Side effects: none.
- Why needed: session ownership failures look like not found.
- If removed: history endpoint errors would not have the intended API shape.

### `app.main.token_fetch_error_handler(request, exc)`

- Defined in: `app/main.py`
- Called by: FastAPI on `TokenFetchError`.
- Output: HTTP 502 with `"Token service unavailable"`.
- Why needed: distinguishes downstream token service failure from client error.
- Network impact: none inside handler; failure happened earlier.
- If removed: token failures become generic server errors.

### `app.main.backend_executor_error_handler(request, exc)`

- Defined in: `app/main.py`
- Called by: FastAPI on `BackendExecutorError`.
- Output: HTTP 502 with `"Backend executor unavailable"`.
- Why needed: tells frontend that downstream executor failed.
- If removed: backend integration failures become generic server errors.

### `Settings.cors_origins_list(self)`

- Defined in: `app/core/config.py`
- Called by: `app/main.py`.
- Calls: string split/strip.
- Input: `CORS_ALLOW_ORIGINS`.
- Output: list of origins.
- Side effects: none.
- Why needed: `CORSMiddleware` expects list form.
- If removed: CORS setup breaks or must duplicate parsing.

### `get_ws_manager()`

- Defined in: `app/core/lifespan.py`
- Called by: `websocket_routes.websocket_progress`.
- Calls: assertion only.
- Inputs: none.
- Output: global `WebSocketManager`.
- Side effects: none.
- Errors: assertion if not initialized.
- Why needed: routes need the startup-created manager.
- If removed: WebSocket route would need another dependency injection strategy.

### `get_kafka_consumer()`

- Defined in: `app/core/lifespan.py`
- Called by: `health_ready`.
- Output: Kafka consumer or `None`.
- Why needed: readiness check needs consumer state.
- If removed: readiness could not report Kafka status.

### `lifespan(app)`

- Defined in: `app/core/lifespan.py`.
- Called by: FastAPI startup/shutdown protocol.
- Calls: `setup_logging`, `init_mongo`, `WebSocketManager`, `KafkaEventConsumer.start`, `throttler_cleanup_loop`, `KafkaEventConsumer.stop`, `WebSocketManager.close_all`, `close_mongo`.
- Inputs: FastAPI app.
- Output: async context manager behavior.
- Side effects: initializes/tears down process resources.
- DB impact: connects to MongoDB and creates indexes.
- Network impact: opens MongoDB and Kafka clients.
- Errors: startup errors can prevent app startup unless caught by called functions.
- Why needed: without it, DB/Kafka/WS manager are not initialized.
- If removed: routes depending on Mongo or WS manager fail.

### `setup_logging()`

- Defined in: `app/core/logging.py`.
- Called by: `lifespan`.
- Calls: `os.makedirs`, `logging.basicConfig`, `structlog.configure`.
- Output: none.
- Side effects: configures global logging and creates log directory.
- Why needed: consistent JSON logs.
- If removed: logs become default/unstructured and file logging disappears.

### `get_logger(name)`

- Defined in: `app/core/logging.py`.
- Called by: most modules at import.
- Output: structlog logger.
- Side effects: none.
- Why needed: consistent logger creation.

### `check_throttle(user_id, client_ip)`

- Defined in: `app/core/throttler.py`.
- Called by: `websocket_progress`.
- Calls: `time.time`.
- Inputs: user ID, client IP.
- Output: bool.
- Side effects: updates `_CONNECT_THROTTLE`.
- Why needed: limits rapid WS reconnects.
- If removed: no WS-specific reconnect throttle.

### `throttler_cleanup_loop()`

- Defined in: `app/core/throttler.py`.
- Called by: `lifespan`.
- Calls: `asyncio.sleep`, map cleanup.
- Output: never returns normally.
- Side effects: prunes throttle map.
- Why needed: prevents memory growth.
- If removed: throttle map can grow indefinitely.

## API functions

### `get_current_user(x_soeid=None, soeid=None)`

- Defined in: `app/api/deps.py`.
- Called by: FastAPI dependency injection for REST and WS routes.
- Inputs: `X-SOEID` header and `soeid` query.
- Output: user ID string.
- Side effects: raises HTTP 401 if missing.
- Security impact: trust anchor for ownership checks.
- Why needed: all ownership rules need a current user.
- If removed: routes need duplicated identity extraction.

### `execute_chat(request, body, current_user)`

- Defined in: `app/api/chat_routes.py`.
- Called by: `POST /api/v1/chat/execute`.
- Calls: `ChatExecutionService.execute`.
- Inputs: FastAPI request, `ChatExecuteRequest`, current user.
- Output: `ChatExecuteResponse`.
- Side effects: mutates `body.soeid`.
- DB impact: through service, writes sessions and recon.
- Network impact: through service, token and backend calls.
- Errors: validation 422, auth 401, downstream 502, DB errors.
- Why needed: REST entry point for new executions.
- If removed: frontend cannot start agent runs.

### `get_status(request, correlation_id, current_user)`

- Defined in: `app/api/chat_routes.py`.
- Called by: `GET /api/v1/chat/status/{correlation_id}`.
- Calls: `StatusService.get_execution_status`.
- Output: `ChatStatusResponse`.
- DB impact: reads recon/events.
- Errors: 401, 404.
- Why needed: refresh/recover single execution state.

### `get_history(request, session_id, current_user, skip, limit, include_events)`

- Defined in: `app/api/chat_routes.py`.
- Called by: `GET /api/v1/chat/history/{session_id}`.
- Calls: `StatusService.get_session_history`.
- Output: `ChatHistoryResponse`.
- DB impact: reads sessions/recon/events.
- Errors: 401, 404, query validation 422.
- Why needed: rebuild conversation history.

### `websocket_progress(websocket, correlation_id, current_user)`

- Defined in: `app/api/websocket_routes.py`.
- Called by: WebSocket route.
- Calls: `check_throttle`, `get_ws_manager`, `ExecutionsRepository.get_execution_by_soeid`, `websocket.accept`, `send_json`, `WebSocketManager.connect`, `receive_text`, `WebSocketManager.disconnect`.
- Inputs: socket, correlation ID, current user.
- Output: none; maintains WebSocket connection.
- Side effects: opens/closes socket, metrics through throttle/manager.
- DB impact: ownership read.
- Network impact: WebSocket accept/send/receive/close.
- Errors: unauthorized/throttle close with 1008; logs runtime errors.
- Why needed: live progress entry point.
- If removed: frontend only has polling/status endpoints.

### `cleanup_throttle_cache()`

- Defined in: `app/api/websocket_routes.py`.
- Called by: no current code.
- Calls: undefined `_CONNECT_THROTTLE`.
- Output: intended cleanup, but broken if called.
- Why needed: likely obsolete.
- If removed: no runtime behavior changes.

### `health_live()`

- Defined in: `app/api/health_routes.py`.
- Called by: `GET /health/live`.
- Output: `HealthLiveResponse(status="ok")`.
- Why needed: lightweight liveness probe.

### `health_ready()`

- Defined in: `app/api/health_routes.py`.
- Called by: `GET /health/ready`.
- Calls: `get_database`, Mongo `ping`, `get_kafka_consumer`, settings checks.
- Output: `HealthReadyResponse`.
- Side effects: dependency pings and warnings.
- DB impact: Mongo ping.
- Why needed: readiness/dependency health.

### `export_execution_pdf(request, correlation_id, x_soeid, include_timestamps, include_raw, download)`

- Defined in: `app/api/export_routes.py`.
- Called by: `GET /api/v1/chat/export/pdf/{correlation_id}`.
- Calls: `PDFExportService.generate_pdf`, `Response`, `HTTPException`.
- Inputs: request object, path correlation ID, required `X-SOEID` header, query flags.
- Output: raw PDF `Response`.
- Side effects: logs denied/missing exports and rendering errors.
- DB impact: through PDF service, reads `recon` and `events`.
- Network impact: none.
- Errors: `404` for missing/unauthorized, `500` for PDF engine or unexpected build failures.
- Why needed: lets frontend download a durable execution report.
- If removed: users can still view status/history but cannot export a printable report.
- Simple English: "Take a run ID and user header, ask the service for PDF bytes, then return those bytes with browser-friendly PDF headers."

## Service functions

### `ChatExecutionService.execute(request)`

- Defined in: `app/services/chat_execution_service.py`.
- Called by: `execute_chat`.
- Calls: ID generation, `SessionService.resolve_session_id`, `token_client.get_token`, `backend_executor_client.execute`, session/execution repositories, metrics, audit logger.
- Inputs: `ChatExecuteRequest`.
- Output: `ChatExecuteResponse`.
- Side effects: logs, audit, metrics.
- DB impact: upsert session, insert execution.
- Network impact: token service, backend executor.
- Errors: token/backend/DB errors.
- Why needed: central execute orchestration.
- If removed: no coherent execute workflow.

### `SessionService.resolve_session_id(session_id)`

- Defined in: `app/services/session_service.py`.
- Called by: `ChatExecutionService.execute`.
- Calls: `generate_session_id`.
- Inputs: optional session ID.
- Output: session ID string.
- Side effects: logs.
- Why needed: encapsulates "reuse or generate" policy.
- If removed: execute service duplicates logic.

### `StatusService.get_execution_status(correlation_id, soeid)`

- Defined in: `app/services/status_service.py`.
- Called by: `get_status`.
- Calls: `ExecutionsRepository.get_execution_by_soeid`, `EventsRepository.get_events_by_correlation`, response models.
- Output: `ChatStatusResponse`.
- DB impact: reads execution and events.
- Errors: `ExecutionNotFoundError`.
- Why needed: single-run source-of-truth API.

### `StatusService.get_session_history(session_id, soeid, skip, limit, include_events)`

- Defined in: `app/services/status_service.py`.
- Called by: `get_history`.
- Calls: `SessionsRepository.get_session_by_soeid`, execution repository reads/count, event repository reads, response models.
- Output: `ChatHistoryResponse`.
- DB impact: reads sessions/recon/events.
- Errors: `SessionNotFoundError`.
- Why needed: conversation source-of-truth API.

### `PDFExportService.build_export_dto(correlation_id, soeid, include_raw=False)`

- Defined in: `app/services/pdf_export_service.py`.
- Called by: `PDFExportService.generate_pdf`; tests call it directly.
- Calls: `ExecutionsRepository.get_execution_by_soeid`, `EventsRepository.get_events_by_correlation`, `ExportEventDTO`, `ExportExecutionDTO`, `json.dumps`.
- Inputs: correlation ID, SOEID, include-raw flag.
- Output: `ExportExecutionDTO` or `None`.
- Side effects: none besides repository reads.
- DB impact: reads `recon` with ownership and reads `events` by correlation ID.
- Network impact: none.
- Errors: repository errors propagate.
- Why needed: separates report data assembly from PDF rendering.
- If removed: PDF generation would have to work directly with raw Mongo documents and template logic would become brittle.
- Simple English: "Load the execution and its events, shape them into a clean report object, and extract final answer/error summary."

### `PDFExportService.generate_pdf(correlation_id, soeid, include_raw=False)`

- Defined in: `app/services/pdf_export_service.py`.
- Called by: `export_execution_pdf`.
- Calls: `PDFExportService.build_export_dto`, Jinja2 `Environment`, `FileSystemLoader`, template `render`, WeasyPrint `HTML.write_pdf`.
- Inputs: correlation ID, SOEID, include-raw flag.
- Output: PDF bytes or `None`.
- Side effects: logs successful report generation.
- DB impact: through `build_export_dto`.
- Network impact: none.
- Filesystem impact: reads `app/templates/execution_report.html`.
- Errors: `RuntimeError` if WeasyPrint Python import was unavailable; template/native rendering errors propagate.
- Why needed: converts durable execution history into a binary PDF response.
- If removed: export route could not produce a PDF.
- Simple English: "Build the report data, render HTML from a template, and ask WeasyPrint to turn it into PDF bytes."

### `EventProcessingService.process_kafka_event(raw_event, correlation_id, kafka_metadata=None)`

- Defined in: `app/services/event_processing_service.py`.
- Called by: `KafkaEventConsumer._process_message`.
- Calls: mapping constants, repositories, metrics, audit logger, timestamp utils.
- Output: normalized dict for WebSocket or `None`.
- Side effects: logs, metrics, audit.
- DB impact: reads execution, inserts event, updates execution.
- Errors: persistence errors propagate.
- Why needed: core event normalization and idempotent persistence.
- If removed: Kafka events cannot become durable state or live updates.

### `WebSocketManager.__init__()`

- Defined in: `app/services/websocket_manager.py`.
- Called by: `lifespan`, tests.
- Output: manager instance with empty maps.
- Why needed: initializes connection state.

### `WebSocketManager.connect(correlation_id, websocket)`

- Defined in: `app/services/websocket_manager.py`.
- Called by: WebSocket route.
- Calls: metrics, `_heartbeat_loop` via task.
- Output: none.
- Side effects: tracks socket, starts heartbeat.
- Why needed: broadcast needs to find clients.

### `WebSocketManager.disconnect(correlation_id, websocket)`

- Defined in: `app/services/websocket_manager.py`.
- Called by: WebSocket route cleanup.
- Side effects: removes socket, decrements metrics, cancels heartbeat if last.
- Why needed: prevent stale clients.

### `WebSocketManager.broadcast(correlation_id, event_data)`

- Defined in: `app/services/websocket_manager.py`.
- Called by: Kafka consumer.
- Calls: WebSocket message models, `_send_to_all`, `send_terminal_and_close`.
- Output: none.
- Network impact: WebSocket sends/closes.
- Why needed: live frontend progress.

### `WebSocketManager.send_personal(websocket, message)`

- Defined in: `app/services/websocket_manager.py`.
- Called by: no current runtime path.
- Network impact: one WebSocket send.
- Why needed: future utility for one-client messages.

### `WebSocketManager.send_terminal_and_close(correlation_id, status, event_type, timestamp, error_message=None)`

- Defined in: `app/services/websocket_manager.py`.
- Called by: `broadcast`, tests.
- Calls: terminal message models, `_send_to_all`, WebSocket close.
- Side effects: sends terminal message, closes/removes clients, cancels heartbeat.
- Why needed: close stream after final status.

### `WebSocketManager._send_to_all(correlation_id, message)`

- Defined in: `app/services/websocket_manager.py`.
- Called by: broadcast/terminal/heartbeat.
- Network impact: WebSocket `send_json`.
- Side effects: removes failed sockets.
- Why needed: shared fan-out logic.

### `WebSocketManager._heartbeat_loop(correlation_id)`

- Defined in: `app/services/websocket_manager.py`.
- Called by: `connect` through `asyncio.create_task`.
- Calls: sleep, heartbeat model, `_send_to_all`.
- Side effects: periodic WebSocket sends.
- Why needed: keep live sockets warm/observable.

### `WebSocketManager.close_all()`

- Defined in: `app/services/websocket_manager.py`.
- Called by: app shutdown.
- Side effects: closes all sockets and cancels heartbeats.
- Why needed: graceful shutdown.

### `increment_active_executions(status_val)`

- Defined in: `app/services/metrics_service.py`.
- Called by: chat execution and event processor.
- Side effects: Prometheus gauge increment.
- Why needed: status metrics.

### `decrement_active_executions(status_val)`

- Defined in: `app/services/metrics_service.py`.
- Called by: event processor.
- Side effects: Prometheus gauge decrement.
- Why needed: status transition metrics.

## Client functions

### `TokenClient.__init__()`

- Defined in: `app/clients/token_client.py`.
- Called by: singleton construction and tests.
- Output: fresh cache state.

### `TokenClient.get_token()`

- Defined in: `app/clients/token_client.py`.
- Called by: `ChatExecutionService.execute`.
- Calls: `_fetch_token` if needed.
- Output: token string.
- Side effects: may update cache.
- Network impact: only on cache miss/expiry.
- Why needed: backend executor authentication.

### `TokenClient._fetch_token()`

- Defined in: `app/clients/token_client.py`.
- Called by: `get_token`.
- Calls: `httpx.AsyncClient.post`, response parsing.
- Output: token string.
- Side effects: cache update.
- Network impact: token service call.
- Errors: `TokenFetchError`.
- Why needed: obtains new token.

### `BackendExecutorClient.execute(context, correlation_id, session_id, soeid, token)`

- Defined in: `app/clients/backend_executor_client.py`.
- Called by: `ChatExecutionService.execute`.
- Calls: `httpx.AsyncClient.post`.
- Output: backend JSON dict.
- Network impact: backend executor call.
- Errors: `BackendExecutorError`.
- Why needed: starts backend agent execution.

### `KafkaEventConsumer.__init__(ws_manager)`

- Defined in: `app/clients/kafka_consumer.py`.
- Called by: lifespan.
- Output: consumer wrapper.
- Why needed: ties Kafka ingestion to WebSocket broadcasting.

### `KafkaEventConsumer._build_config()`

- Defined in: `app/clients/kafka_consumer.py`.
- Called by: `start`.
- Output: confluent-kafka config dict.
- Why needed: maps settings to Kafka client.

### `KafkaEventConsumer.start()`

- Defined in: `app/clients/kafka_consumer.py`.
- Called by: lifespan background task.
- Calls: `_build_config`, `Consumer`, `subscribe`, `_poll_loop`.
- Network impact: Kafka connection/subscription.
- Errors: Kafka initialization errors are logged and swallowed.
- Why needed: starts event ingestion.

### `KafkaEventConsumer._poll_loop(loop)`

- Defined in: `app/clients/kafka_consumer.py`.
- Called by: `start` inside executor thread.
- Calls: `consumer.poll`, schedules `_process_message`.
- Side effects: consumes messages and metrics.
- Why needed: blocking Kafka polling isolated from event loop.

### `KafkaEventConsumer._process_message(msg)`

- Defined in: `app/clients/kafka_consumer.py`.
- Called by: `_poll_loop` through thread-safe coroutine scheduling.
- Calls: JSON parsing, `RawKafkaEvent`, event processor, WebSocket broadcast, `_safe_commit`.
- DB impact: through event processor.
- Network impact: through WebSocket broadcast and Kafka commit.
- Why needed: core Kafka message handling.

### `KafkaEventConsumer._safe_commit(msg)`

- Defined in: `app/clients/kafka_consumer.py`.
- Called by: `_process_message`.
- Calls: `consumer.commit`.
- Side effects: commits Kafka offset and metrics.
- Why needed: at-least-once handling after persistence.

### `KafkaEventConsumer.stop()`

- Defined in: `app/clients/kafka_consumer.py`.
- Called by: lifespan shutdown.
- Calls: consumer close.
- Why needed: graceful shutdown.

### `KafkaEventConsumer.is_initialized()`

- Defined in: `app/clients/kafka_consumer.py`.
- Called by: readiness endpoint.
- Output: bool.
- Why needed: readiness signal.

## Database functions

### `get_database()`

- Defined in: `app/db/mongo.py`.
- Called by: repositories and health checks.
- Output: Motor database.
- Errors: assertion if Mongo not initialized.
- Why needed: central DB access.

### `init_mongo()`

- Defined in: `app/db/mongo.py`.
- Called by: lifespan startup.
- Calls: `AsyncIOMotorClient`, `_create_indexes`.
- Network impact: MongoDB.
- Why needed: DB setup.

### `close_mongo()`

- Defined in: `app/db/mongo.py`.
- Called by: lifespan shutdown.
- Side effects: closes Mongo client and clears globals.
- Why needed: resource cleanup.

### `_create_indexes()`

- Defined in: `app/db/mongo.py`.
- Called by: `init_mongo`.
- Calls: Mongo `create_index`.
- DB impact: creates indexes.
- Why needed: uniqueness, idempotency, query performance.

### `SessionsRepository.create_or_update_session(...)`

- Defined in: `app/db/repositories/sessions_repository.py`.
- Called by: `ChatExecutionService.execute`.
- DB impact: `update_one(..., upsert=True)`.
- Why needed: session source of truth.

### `SessionsRepository.get_session(session_id)`

- Defined in: `app/db/repositories/sessions_repository.py`.
- Called by: no current runtime path.
- DB impact: `find_one`.
- Why needed: general lookup utility.

### `SessionsRepository.get_session_by_soeid(session_id, soeid)`

- Defined in: `app/db/repositories/sessions_repository.py`.
- Called by: `StatusService.get_session_history`.
- DB impact: ownership lookup.
- Why needed: secure history access.

### `ExecutionsRepository.create_execution(doc)`

- Defined in: `app/db/repositories/executions_repository.py`.
- Called by: `ChatExecutionService.execute`.
- DB impact: insert into `recon`.
- Why needed: initial execution state.

### `ExecutionsRepository.update_execution_status(...)`

- Defined in: `app/db/repositories/executions_repository.py`.
- Called by: `EventProcessingService`.
- DB impact: update `recon`.
- Why needed: current status cache.

### `ExecutionsRepository.get_execution(correlation_id)`

- Defined in: `app/db/repositories/executions_repository.py`.
- Called by: `EventProcessingService`.
- DB impact: execution lookup without ownership.
- Why needed: Kafka event has correlation ID but not session/user context.

### `ExecutionsRepository.get_execution_by_soeid(correlation_id, soeid)`

- Defined in: `app/db/repositories/executions_repository.py`.
- Called by: status service and WebSocket route.
- DB impact: ownership lookup.
- Why needed: secure status/socket access.

### `ExecutionsRepository.get_executions_by_session(session_id, skip=0, limit=50)`

- Defined in: `app/db/repositories/executions_repository.py`.
- Called by: history service.
- DB impact: paginated query.
- Why needed: session history.

### `ExecutionsRepository.count_executions_by_session(session_id)`

- Defined in: `app/db/repositories/executions_repository.py`.
- Called by: history service.
- DB impact: count query.
- Why needed: pagination metadata.

### `EventsRepository.insert_event(doc)`

- Defined in: `app/db/repositories/events_repository.py`.
- Called by: event processor.
- DB impact: insert into `events`.
- Handles: duplicate key returns false.
- Why needed: durable event timeline and idempotency.

### `EventsRepository.get_events_by_correlation(correlation_id)`

- Defined in: `app/db/repositories/events_repository.py`.
- Called by: status/history services.
- DB impact: event query.
- Why needed: execution timeline.

### `EventsRepository.get_events_by_session(session_id, skip=0, limit=500)`

- Defined in: `app/db/repositories/events_repository.py`.
- Called by: no current runtime path.
- DB impact: session event query.
- Why needed: likely future session-level timeline.

## Utility functions

### `AuditLogger.log_event(event_name, user_id, correlation_id=None, data=None)`

- Defined in: `app/utils/audit_logger.py`.
- Called by: execute and event processor.
- Calls: structlog audit logger.
- Inputs: event name, user ID, optional correlation ID, data dict.
- Output: none.
- Side effects: audit log entry.
- Why needed: traceability with redaction.

### `async_retry(attempts=3, backoff_seconds=2.0, exception_types=(Exception,))`

- Defined in: `app/utils/retry.py`.
- Called by: decorators on token/backend clients.
- Output: decorator.
- Side effects: logs retries and sleeps.
- Why needed: resilience for transient HTTP integration failures.

### `generate_correlation_id()`

- Defined in: `app/utils/ids.py`.
- Called by: execute service.
- Output: UUID v4 string.
- Why needed: per-run identity.

### `generate_session_id()`

- Defined in: `app/utils/ids.py`.
- Called by: session service.
- Output: UUID v4 string.
- Why needed: conversation identity.

### `utc_now()`

- Defined in: `app/utils/datetime_utils.py`.
- Called by: many services/repositories.
- Output: timezone-aware UTC datetime.
- Why needed: consistent timestamps.

### `format_iso(dt)`

- Defined in: `app/utils/datetime_utils.py`.
- Called by: many services/repositories.
- Output: ISO 8601 string.
- Why needed: stable JSON/Mongo timestamp strings.

---

# 7. Mapping and constants explanation

## Event allowlist

Defined in `app/models/kafka_events.py`:

```python
ALLOWED_EVENT_TYPES = {
    "TOOL_INPUT_EVENT",
    "TOOL_OUTPUT_EVENT",
    "TOOL_ERROR_EVENT",
    "AGENT_START_EVENT",
    "AGENT_COMPLETION_EVENT",
    "AGENT_ERROR_EVENT",
    "EXECUTION_FINAL_RESPONSE",
}
```

What it means: only these backend event types affect middleware state.

Why it exists: Kafka topics may contain unrelated or future events.

Where used:

- `KafkaEventConsumer._process_message`
- `EventProcessingService.process_kafka_event`
- tests validate map coverage

Frontend/backend dependency:

- Backend must publish one of these names for the frontend to see progress.
- Frontend sees both raw and normalized names.

## Event normalization map

Defined in `app/models/kafka_events.py`.

Purpose: raw backend events become stable frontend-facing categories.

| Raw event | Normalized event | Meaning |
|---|---|---|
| `AGENT_START_EVENT` | `agent_started` | Agent began working. |
| `TOOL_INPUT_EVENT` | `tool_started` | Agent invoked a tool. |
| `TOOL_OUTPUT_EVENT` | `tool_completed` | Tool returned output. |
| `TOOL_ERROR_EVENT` | `tool_failed` | Tool failed. |
| `AGENT_COMPLETION_EVENT` | `agent_completed` | Agent completed a stage. |
| `AGENT_ERROR_EVENT` | `agent_failed` | Agent failed. |
| `EXECUTION_FINAL_RESPONSE` | `agent_completed` | Final answer is available. |

## Execution status map

Defined in `app/models/kafka_events.py`.

Purpose: raw events move the execution-level state in `recon.status`.

| Raw event | Execution status | Meaning |
|---|---|---|
| `AGENT_START_EVENT` | `running` | Execution has started. |
| `TOOL_INPUT_EVENT` | `in_progress` | Tool work is ongoing. |
| `TOOL_OUTPUT_EVENT` | `in_progress` | Work is still ongoing after a tool result. |
| `AGENT_COMPLETION_EVENT` | `in_progress` | Agent stage finished, but final response is not necessarily ready. |
| `TOOL_ERROR_EVENT` | `failed` | Execution failed due to tool error. |
| `AGENT_ERROR_EVENT` | `failed` | Execution failed due to agent error. |
| `EXECUTION_FINAL_RESPONSE` | `completed` | Final result is ready. |

Important nuance: `AGENT_COMPLETION_EVENT` is not terminal in current code. `EXECUTION_FINAL_RESPONSE` is the success terminal event.

## Event summary map

Purpose: generate human-readable event timeline summaries.

| Event | Template |
|---|---|
| `AGENT_START_EVENT` | `Agent started: {agent_name}` |
| `TOOL_INPUT_EVENT` | `Tool invoked: {tool_name}` |
| `TOOL_OUTPUT_EVENT` | `Tool output received: {tool_name}` |
| `TOOL_ERROR_EVENT` | `Tool failed: {tool_name}` |
| `AGENT_COMPLETION_EVENT` | `Agent completed: {agent_name}` |
| `AGENT_ERROR_EVENT` | `Agent failed: {agent_name}` |
| `EXECUTION_FINAL_RESPONSE` | `Final response generated` |

Fallbacks:

- missing tool name -> `"unknown"`
- missing agent name -> `"unknown"`
- missing event template -> `"Event received"` (though allowlist should prevent unknown allowed events)

## WebSocket message types

Defined by defaults in `app/models/websocket_messages.py`:

- `connected`: initial accepted connection state.
- `event`: full normalized event.
- `status`: compact latest status update.
- `completed`: terminal success message.
- `failed`: terminal failure message.
- `heartbeat`: periodic keep-alive.

Frontend dependency: frontend should switch on `type`.

## Health check states

Health live:

- `ok`

Readiness top-level:

- `ready`: all checks are `up`
- `degraded`: one or more checks are `down`

Readiness checks:

- `up`
- `down`

## HTTP retry constants

Settings:

- `HTTP_RETRY_ATTEMPTS`
- `HTTP_RETRY_BACKOFF_SECONDS`

Used by:

- token fetch
- backend executor call

Backoff:

```text
attempt 1 fail -> sleep base
attempt 2 fail -> sleep base * 2
attempt 3 fail -> final error
```

## Token constants

`_REFRESH_BUFFER_SECONDS = 60`

Used by `TokenClient` to refresh before expiry.

## WebSocket throttle constants

In `app/core/throttler.py`:

- `THROTTLE_SECONDS = 2.0`
- `CLEANUP_INTERVAL_SECONDS = 600`
- `STALE_THRESHOLD_SECONDS = 3600`

Meaning:

- reject same user/IP connection attempts faster than 2 seconds
- cleanup every 10 minutes
- remove entries older than 1 hour

## Sensitive audit constants

`SENSITIVE_FIELDS`:

- `token`
- `access_token`
- `clientSecret`
- `Config-ID`
- `X-Authorization-Coin`
- `password`

`ALLOWED_BODY_FIELDS`:

- `session_id`
- `correlation_id`
- `soeid`
- `metadata`

Why they exist: avoid leaking prompts/secrets into audit logs.

## Mongo collection/index constants

Collection names are hard-coded:

- `sessions`
- `recon`
- `events`

Important indexes:

- `sessions.session_id` unique
- `recon.correlation_id` unique
- `events.event_idempotency_key` unique

These are not named constants, but they are important architectural constants.

## Backend header constants

Headers sent to backend:

- `Config-ID`
- `X-Correlation-ID`
- `X-Application-ID`
- `Session-ID`
- `X-SOEID`
- `X-Authorization-Coin`
- `Content-Type`

Why they exist:

- backend needs config/application identity
- backend and Kafka need correlation/session identity
- backend needs user identity
- backend needs auth token

---

# 8. API walkthrough

## `POST /api/v1/chat/execute`

### Request model

`ChatExecuteRequest`:

```json
{
  "context": "Investigate case TEST_123",
  "soeid": "TEST001",
  "session_id": null,
  "metadata": {"client": "web"}
}
```

Current code requires `soeid` in body, then overwrites it from authenticated identity. If you want the documented frontend contract where `soeid` is header-only, make `ChatExecuteRequest.soeid` optional or remove it from the body model.

### Response model

`ChatExecuteResponse` with nested `ExecuteData`.

### Auth rules

- `get_current_user` requires `X-SOEID` header or `soeid` query.
- Header wins over query.
- Missing identity -> `401`.

### Ownership rules

- New execution is owned by current user.
- Existing session ID is reused without verifying ownership first.

### Service called

`ChatExecutionService.execute`.

### DB queries/writes

- upsert `sessions`
- insert `recon`

### Network calls

- token service
- backend executor

### Error cases

- missing required request fields -> `422`
- missing identity -> `401`
- token failure -> `502`
- backend failure -> `502`
- Mongo insert/upsert failure -> unhandled server error unless caught by FastAPI default behavior
- rate limit exceeded -> SlowAPI rate-limit response

### Example flow

1. Client posts prompt.
2. Route validates body and identity.
3. Service creates IDs.
4. Token fetched.
5. Backend executor called.
6. Mongo session/execution written.
7. Audit and metrics recorded.
8. Client receives `accepted` with WebSocket URL.

## `GET /api/v1/chat/status/{correlation_id}`

### Request

Path:

```text
correlation_id
```

Header:

```text
X-SOEID
```

### Response model

`ChatStatusResponse` with nested:

- `StatusData`
- list of `EventSummary`

### Auth rules

Requires identity.

### Ownership rules

Uses `ExecutionsRepository.get_execution_by_soeid(correlation_id, soeid)`.

If the record exists for another user, query returns none, and service raises not found.

### Service called

`StatusService.get_execution_status`.

### DB queries

- `recon.find_one({"soeid": soeid, "correlation_id": correlation_id})`
- `events.find({"correlation_id": correlation_id}).sort("timestamp", 1)`

### Error cases

- missing identity -> `401`
- no execution for user -> `404`
- DB failure -> unhandled server error
- rate limit exceeded

### Example flow

1. Client asks for status.
2. Service verifies ownership.
3. Service loads event timeline.
4. Service maps events into summaries.
5. Client receives current status plus timeline.

## `GET /api/v1/chat/history/{session_id}`

### Query parameters

- `skip`: default `0`, minimum `0`
- `limit`: default `50`, minimum `1`, maximum `200`
- `include_events`: default `true`

### Response model

`ChatHistoryResponse` with:

- session fields
- execution list
- pagination fields

### Auth rules

Requires identity.

### Ownership rules

Uses `SessionsRepository.get_session_by_soeid(session_id, soeid)`.

### Service called

`StatusService.get_session_history`.

### DB queries

- session ownership read
- execution list by session
- execution count by session
- optionally event list per execution

### Error cases

- missing identity -> `401`
- no session for user -> `404`
- invalid query params -> `422`
- DB failure -> unhandled server error
- rate limit exceeded

### Example flow

1. Client opens/resumes conversation.
2. Requests session history.
3. Service verifies session belongs to user.
4. Loads executions.
5. Loads events for each execution if requested.
6. Returns full conversation state.

## `WS /ws/v1/chat/progress/{correlation_id}?soeid=USER`

### Request model

No JSON body. Uses:

- path `correlation_id`
- query `soeid`

### Response/message models

WebSocket messages:

- `WSConnectedMessage`
- `WSEventMessage`
- `WSStatusMessage`
- `WSCompletedMessage`
- `WSFailedMessage`
- `WSHeartbeatMessage`

### Auth rules

Uses `get_current_user`; query SOEID is common for browser clients.

### Ownership rules

Must find execution by `correlation_id + soeid`.

### DB queries

- one ownership lookup in `recon` before accept

### Error cases

- missing identity -> dependency failure before WebSocket accept
- throttled -> close `1008`
- unauthorized/not found -> close `1008`
- send/receive errors -> logged and cleanup

### Example flow

1. Client connects after execute response.
2. Route throttles and checks ownership.
3. Route accepts and sends current state.
4. Kafka events later trigger broadcasts.
5. Terminal event closes stream.

## `GET /api/v1/chat/export/pdf/{correlation_id}`

### Request

Path:

```text
correlation_id
```

Required header:

```text
X-SOEID
```

Query parameters:

- `include_timestamps`: default `true`; currently retained for spec alignment and not passed into service logic.
- `include_raw`: default `false`; includes raw event payload excerpts in the PDF timeline DTO/template.
- `download`: default `true`; selects attachment vs inline content disposition.

### Response model

No Pydantic response model. Returns a raw `fastapi.responses.Response` containing PDF bytes.

Headers:

```text
Content-Type: application/pdf
Content-Disposition: attachment; filename="agentic_execution_{correlation_id}.pdf"
```

or:

```text
Content-Disposition: inline; filename="agentic_execution_{correlation_id}.pdf"
```

### Auth rules

Requires `X-SOEID` header directly through `Header(...)`.

Unlike the other chat REST endpoints, it does not use `get_current_user`, so there is no `soeid` query fallback.

### Ownership rules

`PDFExportService.build_export_dto` calls:

```python
ExecutionsRepository.get_execution_by_soeid(correlation_id, soeid)
```

If no execution is found for that user, the service returns `None` and the route raises `404`.

### Service called

`PDFExportService.generate_pdf`.

### DB queries

- `recon.find_one({"soeid": soeid, "correlation_id": correlation_id})`
- `events.find({"correlation_id": correlation_id}).sort("timestamp", 1)`

### Rendering flow

```text
export route
 -> PDFExportService.generate_pdf
 -> PDFExportService.build_export_dto
 -> ExecutionsRepository.get_execution_by_soeid
 -> EventsRepository.get_events_by_correlation
 -> ExportExecutionDTO / ExportEventDTO
 -> Jinja2 execution_report.html
 -> WeasyPrint HTML.write_pdf()
 -> Response(application/pdf)
```

### Error cases

- missing `X-SOEID` -> FastAPI validation error
- wrong owner or missing execution -> `404`
- WeasyPrint unavailable at runtime -> `500`
- template/rendering/unexpected errors -> `500`
- local environment missing native WeasyPrint libraries can fail earlier, at import/test collection time

### Example flow

1. User clicks "Download PDF".
2. Frontend calls endpoint with `X-SOEID`.
3. Service verifies execution ownership.
4. Service loads event timeline.
5. Service extracts final response or error details.
6. Template renders HTML report.
7. WeasyPrint converts HTML to PDF.
8. Browser downloads or opens the PDF based on `download`.

## `GET /health/live`

No auth. Returns `{"status": "ok"}`.

## `GET /health/ready`

No auth. Checks Mongo ping, Kafka initialized, token config, backend config.

Returns `ready` or `degraded`.

## `GET /metrics`

Mounted Prometheus ASGI app. Exposes all metrics in the default Prometheus text format.

No auth in code.

---

# 9. WebSocket walkthrough

## Connection lifecycle

1. Client connects with correlation ID and SOEID.
2. Route checks throttle.
3. Route checks ownership in MongoDB.
4. Route accepts WebSocket.
5. Route sends `connected`.
6. Manager registers socket.
7. Manager starts heartbeat for this correlation ID if needed.
8. Route waits for incoming text until disconnect.
9. Kafka events trigger manager broadcasts.
10. Terminal state triggers terminal message and close.
11. Disconnect cleanup removes socket.

## Authentication

`get_current_user` extracts identity. For WebSocket, the practical source is query parameter:

```text
?soeid=USER
```

This is not strong authentication by itself. It is an adapter that should eventually be backed by JWT/OIDC or trusted gateway headers.

## Ownership

Before accepting:

```python
ExecutionsRepository.get_execution_by_soeid(correlation_id, current_user)
```

If no record:

- close with `1008`
- do not disclose whether the correlation ID exists

## Correlation ID usage

`correlation_id` is the WebSocket subscription key.

All clients connected to the same correlation ID receive the same broadcasts. Multiple browser tabs can subscribe to one execution.

## Message origin

Messages originate from:

- route itself: initial `connected`
- heartbeat loop: `heartbeat`
- Kafka consumer through event processor: `event`, `status`, terminal messages

## Heartbeat behavior

One heartbeat task runs per correlation ID with at least one connected client.

It sends:

```json
{
  "type": "heartbeat",
  "correlation_id": "...",
  "timestamp": "..."
}
```

every `WEBSOCKET_HEARTBEAT_SECONDS`.

## Terminal state handling

Terminal statuses:

- `completed`
- `failed`

On terminal status:

1. event message sent
2. status message sent
3. terminal message sent
4. all sockets for the correlation ID closed
5. connection set removed
6. heartbeat cancelled

## Reconnect implications

If a client reconnects after terminal closure:

- the route will accept if the execution exists and belongs to the user
- it sends `connected` with current status
- no automatic replay of events occurs over WebSocket

The frontend should call `/status` or `/history` for replay/history.

## Where messages originate from

```text
Kafka backend event
 -> KafkaEventConsumer._process_message
 -> EventProcessingService.process_kafka_event
 -> WebSocketManager.broadcast
 -> WSEventMessage + WSStatusMessage
 -> optional WSCompletedMessage/WSFailedMessage
```

---

# 10. Kafka walkthrough

## Why Kafka is used

Kafka decouples long-running backend agent execution from the frontend request. It allows the backend executor to publish progress asynchronously and allows the middleware to process events independently from the initial REST call.

## Consumer startup

During FastAPI startup:

```text
lifespan
 -> KafkaEventConsumer(ws_manager)
 -> asyncio.create_task(kafka_consumer.start())
```

`start` creates the confluent consumer and subscribes to the configured topic.

## Config mapping

Core config:

- bootstrap servers
- consumer group
- earliest offset reset
- manual commits

Optional SSL:

- CA location
- cert location
- key location
- key password

## Polling model

`confluent-kafka` polling is blocking, so `_poll_loop` runs in a thread executor.

The poll loop schedules async message processing back on the event loop.

## Message parsing

`_process_message`:

- decodes bytes as UTF-8
- parses JSON
- commits malformed JSON because retrying cannot fix poison data

## Event filtering

Unsupported event types are committed and skipped.

This prevents irrelevant messages from blocking the consumer group.

## Correlation ID requirement

Missing `x_correlation_id` is committed and skipped.

Without it, the event cannot be linked to an execution.

## Normalization

`RawKafkaEvent` keeps optional backend fields. The event processor maps it to:

- normalized event type
- execution status
- summary
- normalized payload

## Idempotency

Idempotency key:

```text
correlation_id:event_type:timestamp:tool_name
```

Mongo unique index enforces it.

Duplicate handling:

1. Insert raises `DuplicateKeyError`.
2. Repository returns `False`.
3. Processor increments duplicate metric and returns `None`.
4. Consumer commits offset.
5. No WebSocket broadcast is sent.

## Commit-after-persistence

Valid event flow:

```text
parse -> validate -> normalize -> insert event -> update execution -> broadcast -> commit
```

If persistence fails:

```text
parse -> validate -> normalize -> insert/update fails -> log -> no commit
```

This gives at-least-once delivery with duplicate suppression.

## What happens on failure

Malformed JSON:

- commit and skip

Unsupported event:

- commit and skip

Missing correlation:

- commit and skip

Unknown execution:

- processor returns `None`; consumer commits. This avoids blocking but may drop events if they arrive before execution record creation.

DB failure:

- no commit
- Kafka redelivery expected

WebSocket failure:

- log warning
- commit anyway because DB is source of truth

Commit failure:

- metric increment
- log error
- Kafka may redeliver later, idempotency should suppress duplicate DB insert

---

# 11. MongoDB walkthrough

## Collections

### `sessions`

Purpose: conversation/thread metadata.

One session can contain many executions.

Fields:

- `session_id`
- `soeid`
- `created_at`
- `updated_at`
- `last_correlation_id`
- `metadata`

### `recon`

Purpose: execution/run state.

One execution belongs to one session.

Fields:

- `correlation_id`
- `session_id`
- `soeid`
- `request_context`
- `backend_request`
- `backend_ack`
- `status`
- `latest_event_type`
- `created_at`
- `updated_at`
- `completed_at`

### `events`

Purpose: append-only event timeline and idempotency record.

Fields:

- `event_idempotency_key`
- `correlation_id`
- `session_id`
- `soeid`
- `event_type`
- `normalized_event_type`
- `status`
- `agent_name`
- `tool_name`
- `timestamp`
- `summary`
- `raw_payload`
- `normalized_payload`
- Kafka metadata
- `created_at`

## Indexes

Sessions:

- unique `session_id`
- compound `(soeid, session_id)`

Recon:

- unique `correlation_id`
- `session_id`
- compound `(soeid, correlation_id)`

Events:

- `correlation_id`
- `session_id`
- `timestamp`
- `(session_id, timestamp)`
- `(soeid, correlation_id)`
- unique `event_idempotency_key`

## Query patterns

Execute:

- upsert session by `session_id`
- insert execution by unique `correlation_id`

Status:

- find execution by `soeid + correlation_id`
- find events by `correlation_id`

History:

- find session by `soeid + session_id`
- find executions by `session_id`
- count executions by `session_id`
- find events by each execution's `correlation_id`

Kafka:

- find execution by `correlation_id`
- insert event by unique `event_idempotency_key`
- update execution by `correlation_id`

WebSocket:

- find execution by `soeid + correlation_id`

## Ownership checks

Ownership is enforced at API boundaries:

- status checks execution by `soeid + correlation_id`
- history checks session by `soeid + session_id`
- WebSocket checks execution by `soeid + correlation_id`

After ownership is verified, follow-up event queries usually use only correlation ID.

## Data relationships

```text
sessions.session_id
  -> recon.session_id
      -> events.session_id

recon.correlation_id
  -> events.correlation_id
```

The strongest run-level link is `correlation_id`.

The strongest conversation-level link is `session_id`.

---

# 12. Data model walkthrough

## Request models

### `ChatExecuteRequest`

- `context`: prompt/investigation text. Populated by frontend. Consumed by backend executor and stored in `recon.request_context`.
- `soeid`: user identity. Populated by frontend body currently, then overwritten by route. Consumed by service/repositories.
- `session_id`: optional existing session. Populated by frontend for continued conversation. Consumed by session service.
- `metadata`: optional client metadata. Stored on session.

## Response models

### `ExecuteData`

- `correlation_id`: generated by middleware.
- `session_id`: generated or reused.
- `status`: starts as `accepted`.
- `backend_ack`: reduced backend acknowledgment.
- `websocket_url`: route frontend can connect to.
- `created_at`: middleware timestamp.

### `ChatExecuteResponse`

- `success`: true on success.
- `message`: human message.
- `data`: execute payload.

### `EventSummary`

- `event_id`: optional, not currently populated.
- `event_type`: raw Kafka event type.
- `normalized_event_type`: frontend-friendly event type.
- `status`: resulting execution status.
- `agent_name`: backend-provided agent.
- `tool_name`: backend-provided tool.
- `timestamp`: event time.
- `summary`: generated human summary.

### `StatusData`

Combines current execution document plus event summaries.

### `ChatStatusResponse`

Top-level status response.

### `ExecutionInHistory`

One run inside a session history.

### `HistoryData`

Session-level response payload, including executions and pagination metadata.

### `ChatHistoryResponse`

Top-level history response.

### `HealthLiveResponse`, `HealthReadyChecks`, `HealthReadyResponse`

Health endpoint contracts.

## Export DTO models

### `ExportEventDTO`

- `event_type`: raw event type from Mongo event document.
- `normalized_event_type`: frontend/middleware normalized event type.
- `status`: status stored with the event.
- `summary`: human-readable event summary.
- `timestamp`: event timestamp shown in the timeline.
- `tool_name`: optional tool name.
- `raw_payload_excerpt`: optional raw payload included only when the export request uses `include_raw=true`.

Populated by: `PDFExportService.build_export_dto`.

Consumed by: `app/templates/execution_report.html`.

### `ExportExecutionDTO`

- `soeid`: owner/requesting user.
- `session_id`: conversation ID.
- `correlation_id`: execution ID.
- `status`: current execution status from `recon`.
- `request_context`: original prompt from `recon`.
- `started_at`: execution `created_at`.
- `completed_at`: execution `completed_at`.
- `latest_event_type`: latest raw event stored on execution.
- `events`: list of `ExportEventDTO`.
- `final_response`: extracted success response text or completed fallback.
- `error_details`: extracted first failure reason.

Populated by: `PDFExportService.build_export_dto`.

Consumed by: Jinja2 report template before WeasyPrint rendering.

## DB models

These mirror intended MongoDB shapes but are not enforced by repositories at write time.

Use them as documentation or future validation hooks.

## Kafka event models

### `RawKafkaEvent`

Flexible because backend event payloads vary. It allows missing values so one model can parse all event types.

### `NormalizedEvent`

Documents processed event shape. Current service returns a dict instead of instantiating this model.

### `KafkaMessageMetadata`

Documents Kafka metadata captured by the consumer.

## WebSocket message models

Each model has a default `type` field. This is important because frontend message handling should switch on `type`, not infer from payload shape.

---

# 13. Dependency graph / call graph

## Layered dependency map

```text
API layer
  chat_routes
    -> deps
    -> ChatExecutionService
    -> StatusService
  websocket_routes
    -> deps
    -> throttler
    -> lifespan.get_ws_manager
    -> ExecutionsRepository
  health_routes
    -> mongo.get_database
    -> lifespan.get_kafka_consumer
  export_routes
    -> PDFExportService

Service layer
  ChatExecutionService
    -> TokenClient
    -> BackendExecutorClient
    -> SessionService
    -> SessionsRepository
    -> ExecutionsRepository
    -> metrics
    -> audit
  StatusService
    -> SessionsRepository
    -> ExecutionsRepository
    -> EventsRepository
  EventProcessingService
    -> kafka event maps
    -> ExecutionsRepository
    -> EventsRepository
    -> metrics
    -> audit
  WebSocketManager
    -> websocket message models
    -> metrics
  PDFExportService
    -> ExecutionsRepository
    -> EventsRepository
    -> export DTO models
    -> Jinja2 template
    -> WeasyPrint

Client layer
  TokenClient
    -> httpx
    -> settings
    -> retry
  BackendExecutorClient
    -> httpx
    -> settings
    -> retry
  KafkaEventConsumer
    -> confluent_kafka
    -> EventProcessingService
    -> WebSocketManager

Persistence layer
  repositories
    -> mongo.get_database

Core layer
  lifespan
    -> logging
    -> mongo
    -> KafkaEventConsumer
    -> WebSocketManager
    -> throttler cleanup
```

## Entry points

Runtime entry points:

- `app.main:app`
- `execute_chat`
- `get_status`
- `get_history`
- `websocket_progress`
- `export_execution_pdf`
- `health_live`
- `health_ready`
- `KafkaEventConsumer.start` as background task
- `throttler_cleanup_loop` as background task

## Utility/support functions

- ID generation
- timestamp formatting
- retry decorator
- audit redaction
- logging setup
- config parsing

---

# 14. Error handling and resilience

## Retries

HTTP integration clients use `async_retry`.

Token retry catches:

- `TokenFetchError`
- `httpx.HTTPError`

Backend retry catches:

- `BackendExecutorError`
- `httpx.HTTPError`

Backoff is exponential.

## Timeouts

Token:

- `TOKEN_TIMEOUT_SECONDS`

Backend:

- `BACKEND_REQUEST_TIMEOUT_SECONDS`

Kafka poll:

- `poll(timeout=1.0)`

WebSocket heartbeat:

- `WEBSOCKET_HEARTBEAT_SECONDS`

## Exception handling

REST:

- domain not-found exceptions -> 404
- integration exceptions -> 502
- validation errors -> FastAPI 422
- missing identity -> 401

Kafka:

- malformed JSON -> commit and skip
- unsupported event -> commit and skip
- missing correlation -> commit and skip
- persistence failure -> no commit
- broadcast failure -> log and commit
- commit failure -> log/metric

WebSocket:

- throttle/unauthorized -> close 1008
- disconnect -> cleanup
- unexpected route error -> log warning and cleanup

PDF export:

- missing/wrong-owner execution -> `404`
- `PDFExportService.generate_pdf` returning `None` -> `404`
- `RuntimeError` during PDF generation -> `500` with `"Cannot generate PDF on this server."`
- other render/build errors -> `500` with `"An error occurred building the export."`
- native WeasyPrint library failures can occur at import time and prevent app/test collection before route-level handlers run

## Status transitions on failure

Failures come from Kafka events:

- `TOOL_ERROR_EVENT` -> `failed`
- `AGENT_ERROR_EVENT` -> `failed`

Terminal failure causes WebSocket failed message and close.

If token/backend call fails during execute, no execution document is created because persistence happens after those calls.

## 404 vs 1008

REST returns 404 for not found and unauthorized resource access. This prevents ID enumeration.

WebSocket closes with 1008 because after a socket upgrade path, policy violation close codes are the standard way to reject unauthorized use.

---

# 15. Security walkthrough

## Identity handling

Current identity is `soeid`.

Source:

- REST: `X-SOEID` header preferred.
- WebSocket: `soeid` query parameter commonly used.

This is a temporary trust anchor. It is structured so future JWT/OIDC can replace `get_current_user`.

## Ownership enforcement

Enforced by querying MongoDB with identity:

- execution status: `soeid + correlation_id`
- session history: `soeid + session_id`
- WebSocket connect: `soeid + correlation_id`
- PDF export: `soeid + correlation_id`

## X-SOEID usage

`X-SOEID` is used as the authenticated principal in REST.

The execute route overwrites body `soeid` with header/query identity. This prevents body spoofing once identity has been established.

The PDF export route directly requires `X-SOEID` through `Header(...)`. It does not use the shared `get_current_user` dependency and therefore does not accept the `soeid` query fallback.

## Query parameter usage for WebSocket

Browsers cannot reliably set arbitrary headers on native WebSocket constructors, so query parameter identity is accepted.

Security implication: query strings can appear in logs. Production should prefer secure tokens, short-lived signed URLs, or gateway-authenticated headers if possible.

## Unauthorized REST behavior

Unauthorized resource access returns 404 through service exceptions.

This means clients cannot distinguish:

- ID does not exist
- ID exists but belongs to another user

## Unauthorized WS behavior

Unauthorized or missing execution closes with code `1008`.

## Secret handling

Secrets are loaded from environment variables.

`.gitignore` excludes:

- `.env`
- cert files
- logs

Backend request persistence masks `Config-ID` and does not store token.

Audit logger redacts sensitive fields.

## Audit log redaction

Direct sensitive keys become `***`.

Direct `context` or `request_context` becomes:

```text
REDACTED (length: N)
```

Nested body fields are whitelisted; everything else becomes `***`.

## Security concerns to understand

1. `ChatExecuteRequest.soeid` is required even though header identity overwrites it.
2. Existing `session_id` is not ownership-checked before `create_or_update_session`.
3. WebSocket query identity is not cryptographically authenticated by this code.
4. `/metrics` and `/health` are unauthenticated in code.
5. `request_context` is stored in MongoDB in plain form.
6. PDF export renders `request_context`, final response, error details, and optionally raw payload excerpts; keep this endpoint ownership-gated and be cautious with `include_raw=true`.

---

# 16. Observability walkthrough

## Logging

Logging is JSON structured through `structlog`.

Destinations:

- stdout
- rotating file at `LOG_FILE_PATH`

## Audit logger

Separate logger name:

```python
structlog.get_logger("audit")
```

Audit events:

- `EXECUTION_INITIALIZED`
- `KAFKA_EVENT_PROCESSED`

## Metrics

Metrics are defined in `metrics_service.py` and exposed at `/metrics`.

Important operational dashboards could show:

- Kafka messages consumed rate
- event processed rate by type
- duplicate event count
- persistence failure count
- Kafka commit failures
- active WebSocket connections
- throttle rejections
- execution counts by status

## Health checks

Liveness:

- returns ok if app route works

Readiness:

- Mongo ping
- Kafka consumer initialized
- token config present
- backend config present

## Readiness vs liveness behavior

Liveness should be used for "restart if dead".

Readiness should be used for "send traffic only if dependencies are usable enough".

---

# 17. Tests walkthrough

## Test execution status

Setup and command:

```bash
python3.13 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m pytest tests/ -q
```

Current result after PDF export dependencies were added:

```text
tests/test_export_service.py collection fails locally because WeasyPrint cannot load native GObject/Pango libraries:
OSError: cannot load library 'libgobject-2.0-0'
```

Interpretation:

- Python package dependencies install successfully after allowing network access.
- The Dockerfile now installs Linux libraries needed by WeasyPrint (`libcairo2`, `libpango-1.0-0`, `libpangocairo-1.0-0`, `libgdk-pixbuf2.0-0`, `libffi-dev`, `shared-mime-info`).
- The local macOS shell still needs equivalent native libraries before tests importing `app.main` can collect.
- The import guard in `pdf_export_service.py` catches `ImportError`, but this failure is an `OSError` raised by WeasyPrint while loading native libraries.

Previous rerun note:

After dependency installation, the first full run executed the suite but failed two token-client tests. Both failures came from mocks in `tests/test_token_client.py` that configured `response.json()` but left `response.text` as a `MagicMock`. The production token client checks `resp.text.strip()` first so it can support raw JWT token responses. Real JSON HTTP responses have string `.text`, so the tests now set `.text` to matching JSON strings.

Original test command:

```bash
pytest tests/ -q
```

## `tests/conftest.py`

Provides:

- `mock_settings`: patches `app.core.config.settings` with test values.
- `sample_execute_request`: representative execute body.
- `sample_execution_doc`: representative Mongo execution.
- `sample_raw_kafka_event`: representative Kafka event.

Why it matters: centralizes reusable test fixtures and avoids hitting real infrastructure.

## `tests/test_chat_execute_endpoint.py`

Tests:

- happy path execute returns 200 and accepted response
- session ID is auto-generated when absent
- missing `context` returns 422
- missing body `soeid` returns 422

Mocking:

- lifespan Mongo/Kafka setup is patched out
- token fetch is mocked
- backend executor call is mocked
- repository writes are mocked

Confidence: validates route/service integration for execute without external systems.

Important implication: test expects `soeid` body to be required, matching current model but conflicting with frontend doc language.

## `tests/test_event_processing_service.py`

Tests:

- every allowed event type has normalization mapping
- every allowed event type has status mapping
- specific normalization values
- specific status values
- ignored events are not allowed
- processing agent start event
- duplicate event returns `None`
- tool input event summary includes tool name

Mocking:

- execution lookup
- event insert
- execution status update

Current source alignment:

- Tests expect `AGENT_COMPLETION_EVENT` status to be `in_progress`.
- Tests expect `EXECUTION_FINAL_RESPONSE` status to be `completed`.
- Tests expect agent start summary to include the agent name, for example `Agent started: recon_ops_investigator_agent`.

That aligns with the multi-agent interpretation documented in the README and frontend handoff: one agent can complete while the overall execution is still waiting for the final response.

## `tests/test_integrity_integration.py`

Tests:

- unauthorized status access returns 404
- unauthorized history access returns 404
- Kafka duplicate idempotency behavior
- Kafka commit omitted on persistence failure
- Kafka commit called after success

Mocking:

- app lifespan
- repository/service calls
- Kafka consumer internals

Confidence: targets the most important correctness guarantees: privacy, idempotency, and commit-after-persistence.

Note: one patch path uses `app.services.event_processing_service.EventProcessingService.process_kafka_event`, but `KafkaEventConsumer` imports `EventProcessingService` into `app.clients.kafka_consumer`. Depending on Python object identity, patching the original class method may still affect it, but patching at the lookup site is usually clearer.

## `tests/test_backend_executor_client.py`

Tests:

- backend executor success sends expected headers and returns JSON
- timeout raises `BackendExecutorError`

Mocking:

- `httpx.AsyncClient`
- settings in one test

Confidence: validates outbound backend contract.

Oddity: there is an empty first `with patch(...)` block importing `httpx`, then a second patch block. It is harmless but messy.

## `tests/test_token_client.py`

Tests:

- `_fetch_token` parses JSON token response
- cached token is reused
- expired token triggers fresh fetch

Mocking:

- `httpx.AsyncClient`

Confidence: validates token cache behavior.

Coverage gap: raw JWT response path and unexpected format errors are not tested.

## `tests/test_export_service.py`

Tests:

- `build_export_dto` successfully builds a report DTO with events and final response extraction.
- failed execution DTOs extract `error_details`.
- missing or unauthorized execution returns `None`.
- export route returns 404 when service returns no PDF bytes.
- successful route returns `application/pdf`, attachment disposition, and expected bytes.
- `download=false` returns inline content disposition.

Mocking:

- repository calls for service-level DTO tests
- `PDFExportService.generate_pdf` for route-level tests

Confidence: validates the export service's Mongo-to-DTO shaping and route response headers.

Local collection caveat: because the test imports `app.main`, it imports `export_routes`, which imports `pdf_export_service`, which imports WeasyPrint. If native WeasyPrint libraries are missing locally, this test module can fail during collection before mocks are applied.

## `tests/test_websocket_manager.py`

Tests:

- connect registers client
- disconnect removes client
- broadcast sends to all clients
- broadcast with no connections is no-op
- terminal completed closes connections
- terminal failed closes connections
- one failed client does not crash broadcast

Mocking:

- mock WebSocket objects with async `send_json` and `close`

Confidence: validates in-memory fan-out and cleanup behavior.

Coverage gap: heartbeat loop behavior and metrics are not deeply tested.

---

# 18. Read this like a developer notes

## Best reading order

1. `README.md`
2. `app/main.py`
3. `app/core/lifespan.py`
4. `app/api/chat_routes.py`
5. `app/services/chat_execution_service.py`
6. `app/clients/token_client.py`
7. `app/clients/backend_executor_client.py`
8. `app/db/mongo.py` and repositories
9. `app/clients/kafka_consumer.py`
10. `app/services/event_processing_service.py`
11. `app/models/kafka_events.py`
12. `app/services/websocket_manager.py`
13. `app/services/status_service.py`
14. `app/api/export_routes.py`
15. `app/services/pdf_export_service.py`
16. `app/models/export_models.py`
17. `app/templates/execution_report.html`
18. tests

## Easy misunderstandings

- `AGENT_COMPLETION_EVENT` does not complete the execution in current code; `EXECUTION_FINAL_RESPONSE` does.
- WebSocket is live-only, not history.
- MongoDB is the source of truth.
- `session_id` and `correlation_id` are different lifecycles.
- Kafka commit happens after persistence, not after broadcast reliability.
- Body `soeid` is required by the model but overwritten by route identity.
- `db_models.py` is not currently enforced in repositories.
- PDF export is Mongo-backed like status/history; it does not use WebSocket history.
- PDF export requires native WeasyPrint libraries in addition to Python packages.
- Export route identity handling is header-only `X-SOEID`, unlike routes using `get_current_user`.

## Hidden assumptions

- The backend executor will publish Kafka events with matching `x_correlation_id`.
- Kafka events will not arrive before the execution document exists.
- Timestamps are stable enough to participate in idempotency.
- One process's in-memory WebSocket manager is sufficient for connected clients.
- SOEID provided to the service is trustworthy because an upstream system or future auth will enforce it.
- Session IDs are hard enough to guess that reusing by ID without pre-check is acceptable, though stronger validation would be safer.

## Naming conventions

- `correlation_id`: per execution/run.
- `session_id`: per conversation/thread.
- `soeid`: user identity.
- `recon`: execution collection.
- `events`: event timeline.
- `normalized_event_type`: frontend-friendly event vocabulary.

## Repeated patterns

- Routes are thin and delegate to services.
- Services use repositories for DB operations.
- Repositories exclude Mongo `_id` in read responses.
- Integration clients wrap HTTP errors into domain exceptions.
- Mappings convert backend vocabulary to frontend vocabulary.
- Ownership checks are performed by compound queries with `soeid`.

## Practical modification notes

- To add a new Kafka event type:
  1. add it to `ALLOWED_EVENT_TYPES`
  2. add normalization/status/summary mappings
  3. update tests
  4. verify frontend can handle new normalized type/status

- To change terminal behavior:
  1. update `EXECUTION_STATUS_MAP`
  2. update WebSocket manager terminal status logic if new terminal statuses exist
  3. update tests

- To change PDF export:
  1. update `ExportExecutionDTO` / `ExportEventDTO` if report data shape changes
  2. update `PDFExportService.build_export_dto` for Mongo-to-report mapping
  3. update `app/templates/execution_report.html` for layout
  4. update `tests/test_export_service.py`
  5. verify WeasyPrint native dependencies in Docker and local dev docs

- To add real auth:
  1. replace `get_current_user`
  2. keep route/service ownership checks
  3. decide whether WebSocket uses token query, cookie, or gateway identity

- To scale WebSockets across workers:
  1. understand that current manager is process-local
  2. use sticky sessions, one worker, or shared pub/sub fan-out

---

# 19. Common interview-style questions and answers

## Why is Kafka needed here?

Because the backend agent execution is long-running and asynchronous. The initial REST call only starts the work. Kafka carries progress events after the request has returned, letting the middleware persist and stream updates without blocking the frontend.

## Why are `session_id` and `correlation_id` separate?

`session_id` represents the conversation thread. It can span multiple prompts. `correlation_id` represents one execution/run inside that thread. This lets the frontend show a continuous conversation while tracking each submitted prompt independently.

## Why is MongoDB the source of truth?

WebSocket is transient and Kafka is a delivery mechanism. MongoDB stores current execution state and event history durably. If a browser refreshes or misses live messages, `/status` and `/history` can rebuild the truth from MongoDB.

## Why does WebSocket not act as history?

WebSocket only delivers messages to currently connected clients. It does not replay missed events. Persisting every event to MongoDB keeps history reliable and queryable.

## Why is commit-after-persistence important?

If the consumer committed before MongoDB insert/update succeeded, a crash or DB failure could lose the event forever. By committing only after persistence, Kafka can redeliver unpersisted messages.

## Why is idempotency needed?

Kafka can redeliver messages after consumer crashes, commit failures, or rebalances. A unique `event_idempotency_key` lets MongoDB reject duplicates safely so repeated delivery does not duplicate event history or status transitions.

## Why does REST use 404 and WebSocket use 1008?

REST uses 404 to avoid revealing whether an ID exists for another user. WebSocket uses close code 1008 because it is the standard policy violation code for rejecting a connection after the WebSocket flow begins.

## Why is recon used?

`recon` is the execution collection. It stores one document per agent run. The name likely reflects the business domain: reconciliation or investigation. It is the current-state table for executions.

## Why does the backend acknowledgment not contain the final answer?

The backend executor only acknowledges that the run started. Final answers arrive later through Kafka as `EXECUTION_FINAL_RESPONSE`, then are persisted and broadcast.

## What happens if WebSocket broadcast fails?

The error is logged, but Kafka processing continues and the offset is committed because the event is already persisted in MongoDB. The frontend can recover through REST status/history.

## What happens if MongoDB insert fails during Kafka processing?

The processor raises, the consumer logs, and the Kafka offset is not committed. Kafka should redeliver later.

## Why are status and event messages both sent over WebSocket?

The `event` message carries rich timeline details. The `status` message gives the frontend a simple current-state update. This lets UI components update independently.

## Why is PDF export built from MongoDB instead of WebSocket?

MongoDB is the durable source of truth. WebSocket is transient live delivery and may miss events during disconnects or refreshes. The PDF export needs a complete historical report, so it reads `recon` and `events` from MongoDB through the same ownership boundary as status/history.

## Why does PDF export need system packages?

The Python package `weasyprint` renders HTML/CSS into PDF using native text and graphics libraries such as Cairo and Pango. The Dockerfile installs those Linux libraries. Local machines need equivalent native libraries or app import/test collection can fail when WeasyPrint loads.

## What would you improve first?

Strong candidates:

1. Make body `soeid` optional/removed if identity should come only from header.
2. Validate existing `session_id` ownership before upsert.
3. Remove or fix unused `cleanup_throttle_cache`.
4. Align tests with current event status/summary mappings.
5. Decide whether unknown execution Kafka events should be retried instead of committed.
6. Use Pydantic DB models for write validation or remove/rename mismatched fields.
7. Make the WeasyPrint import guard catch native-library load failures or lazy-load WeasyPrint inside `generate_pdf` so non-export routes/tests can import without local PDF system libraries.

---

# 20. If I had to explain this to someone else

## 30 seconds

This is a FastAPI middleware between a chat frontend and an asynchronous backend agent executor. The frontend starts a run through REST, the middleware fetches a token and calls the backend, stores the run in MongoDB, then consumes backend progress events from Kafka. Each event is normalized, persisted idempotently, updates the execution status, and is streamed live to the frontend over WebSocket. MongoDB is the source of truth, while WebSocket is only live delivery; the same MongoDB history can also be rendered into a PDF execution report.

## 2 minutes

The system has two halves. The synchronous half starts work: `POST /api/v1/chat/execute` validates the user identity, generates a `correlation_id`, resolves a `session_id`, fetches a token, calls the backend executor, writes a session and execution record to MongoDB, and returns an accepted response with a WebSocket URL.

The asynchronous half tracks work: a Kafka consumer listens for backend events using manual commits. It parses each message, filters unsupported events, requires `x_correlation_id`, looks up the execution, maps raw events to normalized event types and statuses, inserts the event into MongoDB using a unique idempotency key, updates the current execution status in `recon`, broadcasts to connected WebSocket clients, and commits the Kafka offset only after persistence succeeds.

For recovery, the frontend uses `/status/{correlation_id}` for one run or `/history/{session_id}` for a conversation. For reporting, it calls `/api/v1/chat/export/pdf/{correlation_id}` with `X-SOEID` to generate a PDF from the persisted execution and events. Ownership is enforced using `soeid`, with REST returning 404 for unauthorized IDs and WebSocket closing with 1008.

## 5 minutes

At startup, `app/main.py` creates a FastAPI app with CORS, SlowAPI rate limiting, Prometheus metrics, exception handlers, and routers. `lifespan.py` configures logging, initializes MongoDB and indexes, creates a process-local `WebSocketManager`, starts `KafkaEventConsumer`, and starts a cleanup loop for the WebSocket throttle map.

The execute route is intentionally thin. It uses `get_current_user` to extract SOEID, overwrites the body SOEID, and delegates to `ChatExecutionService`. That service generates a new correlation ID, reuses or creates a session ID, gets a token from `TokenClient`, calls the backend with `BackendExecutorClient`, stores the session and initial execution in MongoDB, records metrics/audit, and returns the IDs and WebSocket URL.

MongoDB has three collections. `sessions` stores conversation ownership and metadata. `recon` stores one execution per correlation ID and holds current status. `events` stores every normalized Kafka event, with a unique `event_idempotency_key` to suppress duplicates. Status, history, and PDF export endpoints read these collections and enforce ownership by querying with SOEID.

Kafka is the bridge from backend async execution to middleware state. The consumer disables auto-commit. It commits poison or irrelevant messages after skipping them, but for valid execution events it commits only after `EventProcessingService` has inserted the event and updated the execution. If MongoDB fails, it does not commit, so Kafka can redeliver. If WebSocket broadcast fails, it still commits because MongoDB already has the truth.

WebSocket connections are keyed by correlation ID. On connect, the route throttles rapid reconnects, verifies ownership, accepts, sends a `connected` message, and registers the socket. When events arrive, the manager sends an `event` message and a `status` message. On `completed` or `failed`, it sends a terminal message and closes the sockets for that run. Heartbeats keep idle streams alive.

## Technical depth

The most important design invariant is: MongoDB persistence precedes Kafka commit. This gives at-least-once event handling without duplicate state, because MongoDB's unique `event_idempotency_key` makes repeated delivery safe.

The second important invariant is: user-facing reads are ownership-gated. Status and WebSocket use `soeid + correlation_id`; history uses `soeid + session_id`. REST hides unauthorized resources as 404, while WebSocket uses policy close 1008.

The third important invariant is: raw backend event vocabulary is translated before reaching the frontend. `EVENT_NORMALIZATION_MAP`, `EXECUTION_STATUS_MAP`, and `EVENT_SUMMARY_MAP` are the contract boundary. If backend adds events or frontend needs new UI states, those maps and tests are where the change begins.

The fourth important invariant is: `session_id` and `correlation_id` have different lifecycles. A session can have many executions. A correlation ID belongs to exactly one execution. The frontend should store both.

The fifth important invariant is: WebSocket is not durable. It is a live fan-out attached to process memory. MongoDB-backed REST endpoints are how clients recover after refresh, reconnect, missed events, or terminal closure.

If you can explain those five invariants, then explain the execute chain and Kafka chain, you can confidently explain the system end to end.
