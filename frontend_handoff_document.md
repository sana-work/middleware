# Frontend Integration Handoff: Agentic Middleware System

This document provides the technical specifications and integration patterns required for the frontend team to build a production-grade chat UI on top of the Agentic Middleware.

---

## 1. Overview

The Agentic Middleware acts as a robust broker between the frontend chat UI and the backend asynchronous Agent Executor. 

**Core Architectural Principles:**
- **Asynchronicity**: Long-running agent tasks are decoupled from the initial HTTP request.
- **Identity Gating**: All access is verified via the `soeid` identifier.
- **Durable State**: MongoDB (the `recon` and `sessions` collections) is the source of truth for all historical data.
- **Reactive Updates**: WebSockets provide transient, live progress updates while a task is running.
- **Idempotency**: Kafka at-least-once delivery is handled at the middleware layer; the frontend receives normalized, deduplicated events.

---

## 2. Integration Summary

| Integration Point | Protocol | When to use |
| :--- | :--- | :--- |
| **`POST /execute`** | REST | When the user submits a new prompt. Returns IDs and WS URL. |
| **`WS /progress`** | WebSocket | Immediately after `/execute` to receive live progress events. |
| **`GET /status`** | REST | Fallback/Sync check for a single execution on refresh. |
| **`GET /history`** | REST | On page load or conversation resume to rebuild the chat thread. |

**Identifiers to Preserve:**
- **`session_id`**: Persists for the life of a conversation thread. Must be stored in the UI state/local storage and reused for consecutive turns.
- **`correlation_id`**: Unique per prompt execution. Used to connect to the specific WebSocket channel for that run.

---

## 3. Authentication / Identity

The system currently uses an identity adapter keyed on the unique user identifier: `soeid`.

- **Required Header**: `X-SOEID` (e.g., `X-SOEID: ab12345`).
- **Authorization**: The middleware uses this identifier to enforce strict ownership. You can only access sessions or executions created by your own `soeid`.
- **Identity Context**: Identity is extracted strictly from the header for REST calls. Avoid passing sensitive identity data in query parameters for HTTP requests.
- **Failure Behavior**: 
    - **REST**: Returns **404 Not Found** for an ID that exists but belongs to another user (to prevent ID enumeration).
    - **WebSocket**: Closes the connection with **Status Code 1008 (Policy Violation)**.

> [!NOTE]
> The `X-SOEID` header is a temporary identity adapter. The middleware is architected to transition to JWT/OIDC without breaking the downstream API contract.

---

## 4. Identifier Rules

Understanding the relationship between sessions and executions is critical for proper state management.

| Identifier | Format | Lifecycle | Usage |
| :--- | :--- | :--- | :--- |
| **`session_id`** | UUID v4 | Permanent (Conversation) | Reuse this across multiple `POST /execute` calls to keep them in one thread. |
| **`correlation_id`** | UUID v4 | Short-lived (Run) | Every new `POST /execute` generates a brand new `correlation_id`. |

**Frontend Flow (Example IDs):**
1. **New Conversation**: Call `/execute` without a `session_id`. Store the returned `session_id` (e.g., `7ae36173-0000-4752-9da1-000000000001`).
2. **Continued Conversation**: Call `/execute` and pass the existing `session_id`.
3. **Execution Tracking**: Receive a new `correlation_id` (e.g., `8d22f1b4-0000-4f51-8d2a-000000000002`). Use this for the WebSocket.

---

## 5. REST API Contract

Base URL: `https://<middleware-host>/api/v1/chat`

### **POST `/execute`**
Starts an asynchronous agent execution.

- **Request Headers**:
  ```http
  X-SOEID: <user-id>
  Content-Type: application/json
  ```
- **Request Body**:
  ```json
  {
    "context": "Check the status of the trade reconciliation for last Friday",
    "session_id": "7ae36173-61a0-4752-9da1-9689f257b4f1", 
    "metadata": { "client_version": "1.0.0" }
  }
  ```
- **Note on Identity**: The `soeid` field is derived automatically from the `X-SOEID` header by the middleware layer and should not be included in the JSON body.
- **Response (200 OK)**:
  ```json
  {
    "success": true,
    "message": "Execution initialized successfully",
    "data": {
      "correlation_id": "8d22f1b4-2972-4f51-8d2a-718695028495",
      "session_id": "7ae36173-61a0-4752-9da1-9689f257b4f1",
      "status": "accepted",
      "backend_ack": { "status": "QUEUED", "job_id": "job_123" },
      "websocket_url": "/ws/v1/chat/progress/8d22f1b4-2972-4f51-8d2a-718695028495",
      "created_at": "2024-04-15T10:00:00Z"
    }
  }
  ```

### **GET `/status/{correlation_id}`**
Fetch the current state and full event log for a specific execution.

- **Request Headers**:
  ```http
  X-SOEID: <user-id>
  ```
- **Response (200 OK)**:
  ```json
  {
    "success": true,
    "data": {
      "correlation_id": "...",
      "status": "in_progress",
      "events": [
        { "event_type": "AGENT_START_EVENT", "summary": "Agent started", "timestamp": "..." }
      ]
    }
  }
  ```

### **GET `/history/{session_id}`**
Retrieve the full history of a conversation thread.

- **Request Headers**:
  ```http
  X-SOEID: <user-id>
  ```
- **Query Params**: `limit=50`, `skip=0`, `include_events=true`.
- **Response (200 OK)**:
  ```json
  {
    "success": true,
    "data": {
      "session_id": "...",
      "executions": [
        { "correlation_id": "...", "status": "completed", "request_context": "..." }
      ]
    }
  }
  ```

---

## 6. WebSocket Contract

Endpoint: `ws://<middleware-host>/ws/v1/chat/progress/{correlation_id}?soeid=ab12345`

- **Authentication**: Since most browsers do not support custom headers on the `WebSocket` object, the `soeid` must be passed as a **query parameter**.
- **When to Open**: Immediately after receiving the `websocket_url` from the `/execute` response.
- **Terminal Behavior**:
  - The frontend should treat `completed` / `failed` messages as **Terminal States**.
  - Save the final state/response locally and close the socket.
  - Do not expect more messages after a terminal receipt.
  - If a reconnect is needed after a terminal state, use `GET /status` to verify current DB state.
- **Resiliency**:
  - If the socket disconnects unexpectedly in a non-terminal state, perform **one immediate reconnect**.
  - If the second connection fails, fallback to polling `GET /status/{correlation_id}` until a terminal state is reached.
- **Heartbeat**: The server sends a `{ "type": "heartbeat" }` every 15 seconds.

**Message Types:**
1.  **`connected`**: Received immediately upon successful connection.
2.  **`event`**: Contains a normalized progress event (Agent start, Tool call, etc.).
3.  **`status`**: Sent when the macro execution status changes.
4.  **`completed` / `failed`**: Terminal states. Close the socket after receipt.

---

## 7. Execution Flow (Step-by-Step)

1.  **Submit**: User types "Run recon" and hits enter.
2.  **Call API**: Frontend sends `POST /api/v1/chat/execute`.
3.  **Process Response**: Frontend receives `correlation_id` and adds a "Loading" bubble to the chat.
4.  **Initialize Stream**: Frontend opens WebSocket using the `websocket_url`.
5.  **Render Events**: As `event` messages arrive (e.g., `TOOL_INPUT_EVENT`), the UI updates the "Loading" bubble with specific progress text (e.g., "Scanning trades...").
6.  **Terminal State**: Received `completed` message. UI renders the final answer/result. Close WebSocket.
7.  **Persist**: The `session_id` is updated in local state for the next turn.

---

## 8. Event-to-UI Mapping

| Kafka / Normalized Event | Execution Status | UI Treatment |
| :--- | :--- | :--- |
| `AGENT_START_EVENT` | `running` | Show "Agent is thinking..." |
| `TOOL_INPUT_EVENT` | `in_progress` | Show "Running Tool: [Tool Name]..." |
| `TOOL_OUTPUT_EVENT` | `in_progress` | Briefly pulse the tool name to indicate progress. |
| `AGENT_COMPLETION_EVENT`| `completed` | Display final result; stop the loading indicator. |
| `TOOL_ERROR_EVENT` | `failed` | Show a warning next to the tool step. |
| `AGENT_ERROR_EVENT` | `failed` | Show a global error banner for the specific run. |

---

## 9. Status-to-UI Mapping

| Status | Recommended UI State | Indicator |
| :--- | :--- | :--- |
| **`accepted`** | Queued | Grey Badge / Spinner |
| **`running`** | Thinking | Blue Pulse |
| **`in_progress`** | Actively Working | Progress Bar / Tool Logs |
| **`completed`** | Success | Green Check / Final Result |
| **`failed`** | Error | Red Warning / Retry Button |

---

## 10. History Rendering Guidance

When the user reloads the page or selects a past session from the navigation:
- **Source of Truth**: Call `GET /api/v1/chat/history/{session_id}`.
- **Rendering**: Map each `execution` in the response to one chat turn (User prompt -> Agent events -> Agent result).
- **Sequence**: Sort executions by `started_at` and events within each execution by `timestamp`.

---

## 11. Error Handling Guide

| Error Scenario | Middleware Code | Frontend Action |
| :--- | :--- | :--- |
| **Identity Missing** | 401 Unauthorized | Redirect to Login / Provide `X-SOEID`. |
| **Ownership Violation**| 404 Not Found | Show "Resource not found or access denied." |
| **Execution Failed** | WebSocket `failed` | Render the error message; allow user to retry. |
| **WS Disconnect** | Unexpected Close | Attempt 1 reconnect; fallback to `GET /status` if persistent. |
| **Rate Limit** | 429 Too Many Requests | Show "Too many requests, please wait." |

---

## 12. Sample Payloads (JSON)

### **Detailed History Example**
Full context reconstruction example:
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
            "summary": "Access timeout on log vault (Non-recoverable)",
            "tool_name": "VaultService",
            "timestamp": "2024-04-15T10:01:20Z"
          }
        ]
      }
    ]
  }
}
```

### **WebSocket Event Message**
```json
{
  "type": "event",
  "correlation_id": "8023-...",
  "event": {
    "normalized_event_type": "tool_started",
    "summary": "Searching trade logs for soeid 1234..."
  }
}
```

---

## 13. Environment & Configuration

- **Development URL**: `http://localhost:8000`
- **CORS**: Ensure your frontend origin (e.g., `http://localhost:3000`) is whitelisted in the middleware configuration.
- **Identity Mocking**: For local development, manually set the `X-SOEID` header in your HTTP client.

---

## 14. Frontend Implementation Recommendations

- **Left Navigation**: Should represent **Sessions**.
- **Chat Feed**: Represents **Executions** (Runs) within a session.
- **Persistence**: Store the `session_id` in the URL or local state to ensure continuity.
- **WebSocket Logic**: Encapsulate the WebSocket logic in a custom hook (e.g., `useAgentExecution(correlationId)`) that handles the state machine (Accepted -> Running -> Terminal).

- **Retry Guidance (POST /execute)**:
  - Users can safely retry the same prompt if an execution fails.
  - A retry **always creates a brand-new `correlation_id`** and unique WebSocket URL.
  - Reusing the same `session_id` on retry is recommended to maintain the chat thread context.

---

## 15. Appendix: Quick Reference

- **Domain Model**: Individual runs are child objects of a session.
- **Correlation ID**: The primary key for life-cycle tracking and WebSocket addressing.
- **Status 1008**: WebSocket authentication/policy failure.
- **At-Least-Once**: Expect that events in the history log are immutable once persistent.
