# Code Cheat Sheet

Fast recall notes for `OPSUI-AGENT-RECON-API`. Use this when you already understand the system and need to remember where something lives or what calls what.

## Local Setup And Tests

```bash
python3.13 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m pytest tests/ -q
```

Latest verified result:

```text
29 passed in 6.56s
```

## One-Sentence System Summary

FastAPI middleware accepts frontend chat executions, starts backend agent work, persists execution state in MongoDB, consumes backend Kafka progress events, normalizes them, updates status, and streams live updates over WebSocket.

## Primary Entry Points

| Entry point | File | Calls |
|---|---|---|
| FastAPI app | `app/main.py` | routers, CORS, metrics, exception handlers, lifespan |
| Startup/shutdown | `app/core/lifespan.py` | Mongo init, WebSocket manager, Kafka consumer, throttler cleanup |
| Execute REST | `app/api/chat_routes.py` | `ChatExecutionService.execute` |
| Status REST | `app/api/chat_routes.py` | `StatusService.get_execution_status` |
| History REST | `app/api/chat_routes.py` | `StatusService.get_session_history` |
| Progress WS | `app/api/websocket_routes.py` | `ExecutionsRepository`, `WebSocketManager` |
| Kafka loop | `app/clients/kafka_consumer.py` | `EventProcessingService`, `WebSocketManager` |

## Most Important IDs

| ID | Meaning | Created by | Used for |
|---|---|---|---|
| `session_id` | Conversation/thread ID | client or `generate_session_id()` | history, grouping executions |
| `correlation_id` | One execution/run ID | `generate_correlation_id()` | backend call, Kafka event link, status, WebSocket |
| `soeid` | User identity | `get_current_user()` from `X-SOEID` or query | ownership checks |

Rule of thumb:

```text
One session_id -> many correlation_id values
One correlation_id -> exactly one execution
```

## Core Call Chains

### Execute

```text
POST /api/v1/chat/execute
 -> chat_routes.execute_chat
 -> get_current_user
 -> ChatExecutionService.execute
 -> generate_correlation_id
 -> SessionService.resolve_session_id
 -> token_client.get_token
 -> TokenClient._fetch_token if cache miss
 -> backend_executor_client.execute
 -> SessionsRepository.create_or_update_session
 -> ExecutionsRepository.create_execution
 -> metrics + audit
 -> ChatExecuteResponse
```

### Status

```text
GET /api/v1/chat/status/{correlation_id}
 -> chat_routes.get_status
 -> get_current_user
 -> StatusService.get_execution_status
 -> ExecutionsRepository.get_execution_by_soeid
 -> EventsRepository.get_events_by_correlation
 -> ChatStatusResponse
```

### History

```text
GET /api/v1/chat/history/{session_id}
 -> chat_routes.get_history
 -> get_current_user
 -> StatusService.get_session_history
 -> SessionsRepository.get_session_by_soeid
 -> ExecutionsRepository.get_executions_by_session
 -> ExecutionsRepository.count_executions_by_session
 -> EventsRepository.get_events_by_correlation for each execution if include_events=true
 -> ChatHistoryResponse
```

### Kafka Event

```text
KafkaEventConsumer._poll_loop
 -> KafkaEventConsumer._process_message
 -> json.loads
 -> RawKafkaEvent
 -> EventProcessingService.process_kafka_event
 -> ExecutionsRepository.get_execution
 -> EventsRepository.insert_event
 -> ExecutionsRepository.update_execution_status
 -> audit + metrics
 -> WebSocketManager.broadcast
 -> KafkaEventConsumer._safe_commit
```

### WebSocket Connect

```text
WS /ws/v1/chat/progress/{correlation_id}?soeid=USER
 -> websocket_routes.websocket_progress
 -> get_current_user
 -> check_throttle
 -> ExecutionsRepository.get_execution_by_soeid
 -> websocket.accept
 -> send WSConnectedMessage
 -> WebSocketManager.connect
 -> receive loop
 -> WebSocketManager.disconnect on exit
```

## MongoDB Collections

| Collection | File | Purpose | Key indexes |
|---|---|---|---|
| `sessions` | `sessions_repository.py` | conversation ownership and metadata | unique `session_id`, `(soeid, session_id)` |
| `recon` | `executions_repository.py` | one execution per `correlation_id` | unique `correlation_id`, `session_id`, `(soeid, correlation_id)` |
| `events` | `events_repository.py` | append-only normalized event timeline | `correlation_id`, `session_id`, `timestamp`, unique `event_idempotency_key` |

## Status Map

| Event | Execution status |
|---|---|
| `AGENT_START_EVENT` | `running` |
| `TOOL_INPUT_EVENT` | `in_progress` |
| `TOOL_OUTPUT_EVENT` | `in_progress` |
| `AGENT_COMPLETION_EVENT` | `in_progress` |
| `TOOL_ERROR_EVENT` | `failed` |
| `AGENT_ERROR_EVENT` | `failed` |
| `EXECUTION_FINAL_RESPONSE` | `completed` |

Terminal statuses:

```text
completed
failed
```

## Event Normalization Map

| Event | Normalized event |
|---|---|
| `AGENT_START_EVENT` | `agent_started` |
| `TOOL_INPUT_EVENT` | `tool_started` |
| `TOOL_OUTPUT_EVENT` | `tool_completed` |
| `TOOL_ERROR_EVENT` | `tool_failed` |
| `AGENT_COMPLETION_EVENT` | `agent_completed` |
| `AGENT_ERROR_EVENT` | `agent_failed` |
| `EXECUTION_FINAL_RESPONSE` | `agent_completed` |

## Event Summary Map

| Event | Summary template |
|---|---|
| `AGENT_START_EVENT` | `Agent started: {agent_name}` |
| `TOOL_INPUT_EVENT` | `Tool invoked: {tool_name}` |
| `TOOL_OUTPUT_EVENT` | `Tool output received: {tool_name}` |
| `TOOL_ERROR_EVENT` | `Tool failed: {tool_name}` |
| `AGENT_COMPLETION_EVENT` | `Agent completed: {agent_name}` |
| `AGENT_ERROR_EVENT` | `Agent failed: {agent_name}` |
| `EXECUTION_FINAL_RESPONSE` | `Final response generated` |

## Ownership Enforcement

REST identity:

```text
X-SOEID header preferred
soeid query parameter fallback
```

WebSocket identity:

```text
soeid query parameter
```

Ownership queries:

```text
status:  recon.find_one({"soeid": soeid, "correlation_id": correlation_id})
history: sessions.find_one({"soeid": soeid, "session_id": session_id})
ws:      recon.find_one({"soeid": soeid, "correlation_id": correlation_id})
```

Failure behavior:

```text
REST unauthorized/missing resource -> 404
WebSocket unauthorized/throttled -> close code 1008
missing identity -> 401
```

## WebSocket Message Types

| Type | Sent when |
|---|---|
| `connected` | socket accepted after ownership check |
| `heartbeat` | periodically while clients are connected |
| `event` | persisted Kafka event is broadcast |
| `status` | compact status update after each event |
| `completed` | terminal success |
| `failed` | terminal failure |

Reconnect rule:

```text
WebSocket does not replay history. Reconnect gives current status only; use /status or /history for durable replay.
```

## Kafka Commit Rules

| Case | Commit? | Why |
|---|---|---|
| malformed JSON | yes | poison message cannot be fixed by retry |
| unsupported event type | yes | irrelevant to this workflow |
| missing `x_correlation_id` | yes | cannot attach to execution |
| duplicate event | yes | already persisted |
| Mongo persistence failure | no | must redeliver |
| WebSocket broadcast failure | yes | MongoDB already has source of truth |

## External Integrations

| Integration | File | Protocol | Purpose |
|---|---|---|---|
| Token service | `token_client.py` | HTTP POST | obtain backend auth token |
| Backend executor | `backend_executor_client.py` | HTTP POST | start agent execution |
| Kafka | `kafka_consumer.py` | confluent-kafka | consume async progress |
| MongoDB | `mongo.py` + repositories | Motor async driver | source of truth |
| Prometheus | `metrics_service.py` + `/metrics` | metrics scrape | observability |

## Common Change Points

Add a Kafka event:

```text
Update ALLOWED_EVENT_TYPES
Update EVENT_NORMALIZATION_MAP
Update EXECUTION_STATUS_MAP
Update EVENT_SUMMARY_MAP
Update tests
Update frontend handling if needed
```

Change auth:

```text
Replace get_current_user
Keep repository ownership queries
Decide WebSocket identity strategy
```

Change terminal behavior:

```text
Update EXECUTION_STATUS_MAP
Update WebSocketManager.broadcast terminal status check if new terminal values exist
Update docs/tests/frontend expectations
```

## Sharp Edges

- `ChatExecuteRequest.soeid` is required by the model even though the route overwrites it from identity.
- `SessionService.resolve_session_id` does not validate ownership of a supplied session ID.
- `websocket_routes.cleanup_throttle_cache` is unused and references an undefined `_CONNECT_THROTTLE`.
- `db_models.py` documents shapes but repositories do not enforce those Pydantic models.
- `WebSocketManager` is process-local; multiple Gunicorn workers do not share socket state.
- Kafka processing schedules message handling concurrently, so think carefully about ordering if adding stricter sequencing.

