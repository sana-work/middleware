# Architecture Quick Reference

This is the short architecture memory aid for `OPSUI-AGENT-RECON-API`. For full teaching detail, use `CODEBASE_UNDERSTANDING_GUIDE.md`.

## System Purpose

This service is an agent execution middleware. It turns a frontend chat request into a durable backend execution, then bridges asynchronous backend progress back to the frontend.

```text
Frontend
 -> FastAPI middleware
 -> Token service
 -> Backend agent executor
 -> Kafka events
 -> FastAPI middleware
 -> MongoDB source of truth
 -> WebSocket live updates
```

## Major Components

| Layer | Files | Responsibility |
|---|---|---|
| App composition | `app/main.py`, `app/core/lifespan.py` | create FastAPI app, middleware, metrics, startup/shutdown |
| REST API | `app/api/chat_routes.py` | execute, status, history |
| WebSocket API | `app/api/websocket_routes.py` | live progress stream |
| Health | `app/api/health_routes.py` | liveness/readiness |
| Services | `app/services/*.py` | orchestration, status building, event processing, WebSocket fan-out |
| Clients | `app/clients/*.py` | token service, backend executor, Kafka |
| Persistence | `app/db/*.py` | Mongo connection, indexes, repositories |
| Models | `app/models/*.py` | API, DB, Kafka, and WebSocket contracts |
| Utilities | `app/utils/*.py` | IDs, timestamps, retries, audit logging |

## Source Of Truth

MongoDB is the source of truth.

```text
sessions = conversation/thread metadata
recon    = current execution state
events   = append-only event timeline
```

WebSocket is live delivery only. Kafka is event transport only. The frontend should recover from Mongo-backed REST endpoints.

## Identifier Model

```text
session_id:
  conversation/thread
  reused across multiple prompts
  used by /history

correlation_id:
  one execution/run
  generated for every /execute call
  used by backend executor, Kafka events, /status, and WebSocket

soeid:
  user identity
  used for ownership enforcement
```

## Execute Lifecycle

```text
1. Frontend calls POST /api/v1/chat/execute with X-SOEID.
2. Route validates body as ChatExecuteRequest.
3. get_current_user extracts identity.
4. Route overwrites body.soeid with trusted identity.
5. ChatExecutionService generates correlation_id.
6. SessionService reuses or creates session_id.
7. TokenClient returns cached or freshly fetched token.
8. BackendExecutorClient starts backend agent execution.
9. SessionsRepository upserts sessions document.
10. ExecutionsRepository inserts recon document with status accepted.
11. Metrics and audit logs are written.
12. Client receives correlation_id, session_id, status accepted, and websocket_url.
```

Core chain:

```text
route -> service -> token client -> backend client -> session repo -> execution repo -> response
```

## Kafka Lifecycle

```text
1. Backend executor emits Kafka event with x_correlation_id.
2. KafkaEventConsumer polls message in executor thread.
3. _process_message decodes JSON.
4. Unsupported or malformed messages are committed and skipped.
5. RawKafkaEvent is created.
6. EventProcessingService loads execution by correlation_id.
7. Event type is normalized.
8. Event idempotency key is built.
9. Event is inserted into events.
10. Execution status is updated in recon.
11. Audit and metrics are written.
12. WebSocketManager broadcasts live update.
13. Kafka offset is committed after persistence.
```

Core chain:

```text
Kafka consumer -> normalization -> Mongo events/recon -> WebSocket broadcast -> Kafka commit
```

## WebSocket Lifecycle

```text
1. Frontend connects to /ws/v1/chat/progress/{correlation_id}?soeid=USER.
2. Route throttles rapid reconnects by user/IP.
3. Route verifies recon ownership with soeid + correlation_id.
4. Route accepts socket.
5. Route sends connected message with current status.
6. WebSocketManager registers socket under correlation_id.
7. Heartbeat loop sends heartbeat messages.
8. Kafka broadcasts send event and status messages.
9. completed/failed status sends terminal message and closes sockets.
10. Reconnect gets current status only; REST is used for history replay.
```

## Ownership Model

Identity source:

```text
REST:      X-SOEID header, with query fallback
WebSocket: soeid query parameter
```

Ownership checks:

```text
/status:
  ExecutionsRepository.get_execution_by_soeid(correlation_id, soeid)

/history:
  SessionsRepository.get_session_by_soeid(session_id, soeid)

WebSocket:
  ExecutionsRepository.get_execution_by_soeid(correlation_id, soeid)
```

Failure policy:

```text
REST missing identity -> 401
REST not found or wrong owner -> 404
WebSocket wrong owner or throttled -> 1008 policy violation
```

## MongoDB Model

### `sessions`

One document per conversation.

```text
session_id
soeid
created_at
updated_at
last_correlation_id
metadata
```

### `recon`

One document per execution.

```text
correlation_id
session_id
soeid
request_context
backend_request
backend_ack
status
latest_event_type
created_at
updated_at
completed_at
```

### `events`

One document per normalized Kafka event.

```text
event_idempotency_key
correlation_id
session_id
soeid
event_type
normalized_event_type
status
agent_name
tool_name
timestamp
summary
raw_payload
normalized_payload
kafka metadata
created_at
```

## Event And Status Maps

| Raw event | Normalized event | Execution status | Summary |
|---|---|---|---|
| `AGENT_START_EVENT` | `agent_started` | `running` | `Agent started: {agent_name}` |
| `TOOL_INPUT_EVENT` | `tool_started` | `in_progress` | `Tool invoked: {tool_name}` |
| `TOOL_OUTPUT_EVENT` | `tool_completed` | `in_progress` | `Tool output received: {tool_name}` |
| `TOOL_ERROR_EVENT` | `tool_failed` | `failed` | `Tool failed: {tool_name}` |
| `AGENT_COMPLETION_EVENT` | `agent_completed` | `in_progress` | `Agent completed: {agent_name}` |
| `AGENT_ERROR_EVENT` | `agent_failed` | `failed` | `Agent failed: {agent_name}` |
| `EXECUTION_FINAL_RESPONSE` | `agent_completed` | `completed` | `Final response generated` |

Why `AGENT_COMPLETION_EVENT` is not terminal:

```text
The backend can run multi-agent or staged workflows.
An individual agent may complete before the orchestrator produces the final response.
The full execution completes only on EXECUTION_FINAL_RESPONSE.
```

## Idempotency And Commit Safety

Idempotency key:

```text
{correlation_id}:{event_type}:{timestamp}:{tool_name}
```

Guarantee:

```text
Kafka may deliver at least once.
Mongo unique index suppresses duplicate events.
Consumer commits only after persistence.
```

Failure behavior:

```text
DB failure -> no commit -> redelivery
duplicate event -> commit -> no duplicate broadcast
broadcast failure -> commit -> REST can recover from Mongo
```

## Observability

| Mechanism | Path/file | Notes |
|---|---|---|
| Structured logs | `app/core/logging.py` | stdout and rotating file |
| Audit logs | `app/utils/audit_logger.py` | redacts secrets and context |
| Metrics | `/metrics` | Prometheus ASGI app |
| Liveness | `/health/live` | process is alive |
| Readiness | `/health/ready` | Mongo ping, Kafka initialized, token/backend config |

## Mental Model

The system has three lanes:

```text
Start lane:
  REST execute -> token -> backend executor -> Mongo accepted record

Progress lane:
  Kafka -> normalize -> Mongo event/status -> WebSocket live update

Recovery lane:
  REST status/history -> Mongo source of truth -> frontend rebuilds state
```

If you can explain those three lanes, you can explain the architecture quickly and accurately.

