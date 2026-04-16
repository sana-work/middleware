# Frontend Integration Handoff: Agentic Middleware System

This document provides the technical contract and integration guidance required for the frontend team to build a production-grade chat UI on top of the Agentic Middleware.

---

## 1. Overview

The Agentic Middleware sits between the frontend chat UI and the backend asynchronous Agent Executor.

### Core architectural principles
- **Asynchronous execution**: long-running agent tasks are decoupled from the initial HTTP request.
- **Identity gating**: all access is verified using the Citi user identifier `soeid`.
- **Durable state**: MongoDB (`recon`, `sessions`, `events`) is the source of truth for historical and refreshable state.
- **Reactive updates**: WebSocket delivers transient live progress while a run is active.
- **Idempotency**: Kafka at-least-once delivery is handled at the middleware layer; the frontend receives normalized, deduplicated events.

### Source-of-truth rule
- **Mongo-backed APIs** (`/status`, `/history`) are the durable source of truth.
- **WebSocket** is for live progress only and should not be treated as the canonical history source.

---

## 2. Integration Summary

| Integration Point | Protocol | Exact Path | When to use |
| :--- | :--- | :--- | :--- |
| **Execute** | REST | `POST /api/v1/chat/execute` | When the user submits a new prompt. Returns IDs and WS URL. |
| **Progress Stream** | WebSocket | `WS /ws/v1/chat/progress/{correlation_id}` | Immediately after `/execute` to receive live progress events. |
| **Execution Status** | REST | `GET /api/v1/chat/status/{correlation_id}` | Refresh/sync check for a single execution. |
| **Conversation History** | REST | `GET /api/v1/chat/history/{session_id}` | On page load or conversation resume to rebuild the chat thread. |

### Identifiers to preserve
- **`session_id`**: persists for the life of a conversation thread. Reuse across consecutive turns.
- **`correlation_id`**: unique per prompt execution/run. Use this to attach to the matching WebSocket stream and status endpoint.

### Recommended frontend flow
1. User submits prompt.
2. Frontend calls `POST /api/v1/chat/execute`.
3. Frontend stores/updates `session_id`.
4. Frontend receives new `correlation_id`.
5. Frontend opens WebSocket for that `correlation_id`.
6. Frontend renders live events until terminal state.
7. On refresh/reload, frontend rebuilds state using `GET /status` or `GET /history`.

---

## 3. Authentication / Identity

The current identity adapter is keyed on the Citi unique user identifier: `soeid`.

- **Required header for REST**: `X-SOEID` (example: `X-SOEID: ab12345`)
- **Authorization model**: middleware enforces strict ownership. A user can only access sessions/executions created by that same `soeid`.
- **REST identity context**: identity is extracted from the header. Do not pass sensitive identity data in REST query parameters.
- **WebSocket identity context**: because browsers do not reliably support arbitrary headers on the native `WebSocket` object, the `soeid` is passed as a query parameter for the socket connection.

### Failure behavior
- **REST**: returns **404 Not Found** when a resource exists but belongs to another user, to reduce ID enumeration risk.
- **WebSocket**: closes with **1008 (Policy Violation)** for unauthorized access.

> **Note**
> `X-SOEID` is a temporary identity adapter. The middleware is structured so JWT/OIDC can replace it later without changing the business flow.

---

## 4. Identifier Rules

Understanding the relationship between sessions and runs is critical for correct UI state management.

| Identifier | Format | Lifecycle | Meaning | Frontend Usage |
| :--- | :--- | :--- | :--- | :--- |
| **`session_id`** | UUID v4 | Long-lived | Conversation/thread ID | Reuse for later turns in the same chat |
| **`correlation_id`** | UUID v4 | Per run | One execution/run inside a session | Use for WebSocket and status lookups |

### Example flow
1. **New conversation**: call `/execute` without a `session_id`.
   - Example returned `session_id`: `7ae36173-61a0-4752-9da1-9689f257b4f1`
2. **Continued conversation**: call `/execute` and pass the existing `session_id`.
3. **Run tracking**: every execute response returns a new `correlation_id`.
   - Example `correlation_id`: `8d22f1b4-2972-4f51-8d2a-718695028495`

### Frontend rule of thumb
- **One session** can contain **many runs**
- **One run** always belongs to **exactly one session**

---

## 5. REST API Contract

### Base URL
`https://<middleware-host>`

### 5.1 POST `/api/v1/chat/execute`
Starts an asynchronous agent execution.

#### Request headers
```http
X-SOEID: <user-id>
Content-Type: application/json
```

#### Request body
```json
{
  "context": "Check the status of the trade reconciliation for last Friday",
  "session_id": "7ae36173-61a0-4752-9da1-9689f257b4f1",
  "metadata": {
    "client_version": "1.0.0"
  }
}
```

#### Notes
- `session_id` is optional for a new conversation.
- `soeid` is derived from the `X-SOEID` header and must **not** be sent in the JSON body.

#### Success response (`200 OK`)
```json
{
  "success": true,
  "message": "Execution initialized successfully",
  "data": {
    "correlation_id": "8d22f1b4-2972-4f51-8d2a-718695028495",
    "session_id": "7ae36173-61a0-4752-9da1-9689f257b4f1",
    "status": "accepted",
    "backend_ack": {
      "status": "QUEUED",
      "job_id": "job_123"
    },
    "websocket_url": "/ws/v1/chat/progress/8d22f1b4-2972-4f51-8d2a-718695028495",
    "created_at": "2024-04-15T10:00:00Z"
  }
}
```

#### Example error response (`404 Not Found`)
```json
{
  "success": false,
  "message": "Session not found or access denied"
}
```

#### Frontend behavior
- Update local conversation state with returned `session_id`
- Create a new chat turn associated with the returned `correlation_id`
- Immediately open the matching WebSocket stream

---

### 5.2 GET `/api/v1/chat/status/{correlation_id}`
Fetch the current state and event timeline for one execution.

#### Request headers
```http
X-SOEID: <user-id>
```

#### Success response (`200 OK`)
```json
{
  "success": true,
  "data": {
    "correlation_id": "8d22f1b4-2972-4f51-8d2a-718695028495",
    "session_id": "7ae36173-61a0-4752-9da1-9689f257b4f1",
    "status": "in_progress",
    "latest_event_type": "TOOL_INPUT_EVENT",
    "events": [
      {
        "event_type": "AGENT_START_EVENT",
        "normalized_event_type": "agent_started",
        "summary": "Agent started",
        "timestamp": "2024-04-15T10:00:01Z"
      },
      {
        "event_type": "TOOL_INPUT_EVENT",
        "normalized_event_type": "tool_started",
        "tool_name": "ReconService",
        "summary": "Scanning trade logs",
        "timestamp": "2024-04-15T10:00:05Z"
      }
    ]
  }
}
```

#### Frontend behavior
- Use as fallback when WebSocket disconnects
- Use on browser refresh for active runs
- Treat response data as canonical current state

---

### 5.3 GET `/api/v1/chat/history/{session_id}`
Retrieve the full history for a conversation thread.

#### Request headers
```http
X-SOEID: <user-id>
```

#### Query params
- `limit=50`
- `skip=0`
- `include_events=true`

#### Success response (`200 OK`)
```json
{
  "success": true,
  "data": {
    "session_id": "7ae36173-61a0-4752-9da1-9689f257b4f1",
    "executions": [
      {
        "correlation_id": "8d22f1b4-2972-4f51-8d2a-718695028495",
        "status": "completed",
        "request_context": "Check the status of the trade reconciliation for last Friday",
        "started_at": "2024-04-15T10:00:00Z",
        "completed_at": "2024-04-15T10:00:20Z",
        "events": [
          {
            "event_type": "AGENT_START_EVENT",
            "summary": "Agent started",
            "timestamp": "2024-04-15T10:00:01Z"
          },
          {
            "event_type": "AGENT_COMPLETION_EVENT",
            "summary": "No discrepancies found",
            "timestamp": "2024-04-15T10:00:20Z"
          }
        ]
      }
    ]
  }
}
```

#### Frontend behavior
- Use to rebuild previous conversations from persisted state
- Do not rebuild history from WebSocket messages
- One execution should render as one user turn + one backend run in the conversation

---

## 6. WebSocket Contract

### Endpoint
`ws://<middleware-host>/ws/v1/chat/progress/{correlation_id}?soeid=ab12345`

### When to open
Immediately after receiving the `websocket_url` from the `/execute` response.

### Authentication
Browsers typically do not allow arbitrary request headers on the native `WebSocket` constructor. For that reason, the socket currently uses `soeid` as a query parameter.

### Terminal behavior
- Treat `completed` and `failed` as terminal
- Save final state locally
- Close the socket after the terminal message
- Do not expect more messages after a terminal state
- If reconnect is attempted after terminal state, use `GET /status` to confirm the final DB state

### Resiliency / reconnect
- If the socket disconnects unexpectedly **before terminal state**, attempt **one immediate reconnect**
- If reconnect fails, fall back to polling `GET /api/v1/chat/status/{correlation_id}`
- Stop polling once terminal state is reached

### Heartbeat
Server sends:
```json
{ "type": "heartbeat" }
```
Expected cadence: every 15 seconds.

### Message types
1. `connected`
2. `event`
3. `status`
4. `completed`
5. `failed`
6. `heartbeat`

### Example messages

#### Connected
```json
{
  "type": "connected",
  "correlation_id": "8d22f1b4-2972-4f51-8d2a-718695028495",
  "message": "WebSocket connected"
}
```

#### Event
```json
{
  "type": "event",
  "correlation_id": "8d22f1b4-2972-4f51-8d2a-718695028495",
  "event": {
    "normalized_event_type": "tool_started",
    "tool_name": "ReconService",
    "summary": "Searching trade logs for soeid ab12345..."
  }
}
```

#### Status
```json
{
  "type": "status",
  "correlation_id": "8d22f1b4-2972-4f51-8d2a-718695028495",
  "status": "in_progress"
}
```

#### Completed
```json
{
  "type": "completed",
  "correlation_id": "8d22f1b4-2972-4f51-8d2a-718695028495",
  "status": "completed",
  "summary": "Reconciliation completed successfully"
}
```

#### Failed
```json
{
  "type": "failed",
  "correlation_id": "8d22f1b4-2972-4f51-8d2a-718695028495",
  "status": "failed",
  "summary": "VaultService timeout"
}
```

---

## 7. Execution Flow (Step-by-Step)

1. User submits a prompt in the chat UI.
2. Frontend calls `POST /api/v1/chat/execute`.
3. Frontend receives `session_id`, `correlation_id`, initial `status`, and `websocket_url`.
4. Frontend adds a pending/loading state for the new run.
5. Frontend opens WebSocket for that `correlation_id`.
6. Frontend renders `event` and `status` messages as progress updates.
7. Frontend renders `completed` or `failed` as terminal state.
8. On refresh or reconnect, frontend uses `GET /status` or `GET /history` to rehydrate.

---

## 8. Event-to-UI Mapping

| Kafka / Normalized Event | Execution Status | Frontend Meaning | Suggested UI Treatment |
| :--- | :--- | :--- | :--- |
| `AGENT_START_EVENT` | `running` | Agent run started | Show “Agent is thinking…” |
| `TOOL_INPUT_EVENT` | `in_progress` | Tool started | Show tool step spinner / “Running Tool: [Tool Name]…” |
| `TOOL_OUTPUT_EVENT` | `in_progress` | Tool produced output | Pulse the tool step or append progress text |
| `AGENT_COMPLETION_EVENT` | `completed` | Final success | Display final result and stop loading |
| `TOOL_ERROR_EVENT` | `failed` | Tool failure | Show warning/error on the tool step |
| `AGENT_ERROR_EVENT` | `failed` | Run failure | Show global error banner for the run |

---

## 9. Status-to-UI Mapping

| Status | Recommended UI State | Suggested Indicator | Retry Guidance |
| :--- | :--- | :--- | :--- |
| `accepted` | Queued | Grey badge / spinner | No retry yet |
| `running` | Thinking | Blue pulse | No retry yet |
| `in_progress` | Actively working | Progress bar / tool logs | No retry yet |
| `completed` | Success | Green check / final result | No retry needed |
| `failed` | Error | Red warning / inline error | Offer retry |

---

## 10. History Rendering Guidance

When the user reloads the page or selects a prior session:

- Call `GET /api/v1/chat/history/{session_id}`
- Treat each `execution` as a single backend run inside the thread
- Render:
  - user prompt / request context
  - live-run summary or final outcome
  - optional nested event details
- Sort:
  - executions by `started_at`
  - events by `timestamp`

### Important rule
Use history from persisted APIs only. Do not attempt to stitch history together from previously received WebSocket traffic.

---

## 11. Error Handling Guide

| Error Scenario | Middleware Behavior | Frontend Action |
| :--- | :--- | :--- |
| Identity missing | `401 Unauthorized` | Redirect to login or ensure `X-SOEID` is present |
| Ownership violation | `404 Not Found` | Show “Resource not found or access denied” |
| Validation error | `400 Bad Request` | Show inline request error; do not retry automatically |
| Rate limit | `429 Too Many Requests` | Show “Too many requests, please wait” |
| WebSocket unauthorized | Close code `1008` | Stop reconnecting; surface access error |
| Execution failed | WS `failed` or status `failed` | Render error and offer retry |
| Unexpected WS disconnect | Socket closes before terminal state | Reconnect once, then fallback to `GET /status` |
| Backend/Kafka delay | Run remains non-terminal longer than expected | Keep pending UI state and poll status if needed |

---

## 12. Sample Payloads (JSON)

### Detailed history example
```json
{
  "success": true,
  "data": {
    "session_id": "7ae36173-61a0-4752-9da1-9689f257b4f1",
    "executions": [
      {
        "correlation_id": "8d22f1b4-2972-4f51-8d2a-718695028495",
        "status": "failed",
        "request_context": "Run reconciliation for Friday",
        "started_at": "2024-04-15T10:00:00Z",
        "completed_at": "2024-04-15T10:01:20Z",
        "events": [
          {
            "event_type": "AGENT_START_EVENT",
            "summary": "Agent logic initialized",
            "timestamp": "2024-04-15T10:00:01Z"
          },
          {
            "event_type": "TOOL_ERROR_EVENT",
            "summary": "Access timeout on log vault (non-recoverable)",
            "tool_name": "VaultService",
            "timestamp": "2024-04-15T10:01:20Z"
          }
        ]
      }
    ]
  }
}
```

### WebSocket event example
```json
{
  "type": "event",
  "correlation_id": "8d22f1b4-2972-4f51-8d2a-718695028495",
  "event": {
    "normalized_event_type": "tool_started",
    "summary": "Searching trade logs for soeid ab12345..."
  }
}
```

---

## 13. Environment & Configuration

- **Development base URL**: `http://localhost:8000`
- **Development WS URL**: `ws://localhost:8000`
- **CORS**: frontend origin (for example `http://localhost:3000`) must be allowlisted in middleware configuration
- **Identity mocking**: for local development, set `X-SOEID` on REST requests and append `?soeid=<user-id>` on WebSocket connections

---

## 14. Frontend Implementation Recommendations

- **Left navigation** should represent **sessions/conversations**
- **Chat feed** should represent **executions/runs** within a session
- Persist `session_id` in route state, URL state, or local app state for continuity
- Use a custom hook such as `useAgentExecution(correlationId)` to encapsulate:
  - WebSocket connection lifecycle
  - fallback status polling
  - terminal-state handling
  - local UI state transitions

### Retry guidance
- Users can safely retry the same prompt if an execution fails
- A retry always creates a **new `correlation_id`**
- Reuse the same `session_id` on retry to keep the conversation thread intact

### Recommended UI model
- **Session** = thread in left navigation
- **Execution** = one run/turn in the main chat panel
- **WebSocket** = live progress channel
- **History/status APIs** = refreshable durable state

---

## 15. Appendix: Quick Reference

- **`recon`**: execution/run records collection
- **`sessions`**: conversation metadata collection
- **`events`**: normalized Kafka event log collection
- **Correlation ID**: primary run identifier for status + WebSocket
- **Status 1008**: WebSocket authentication/policy failure
- **At-least-once**: persisted history is durable even if event delivery retries occur
