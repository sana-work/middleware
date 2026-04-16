# Technical Walkthrough: Agentic Middleware System

This document provides a deep-dive architectural and operational walkthrough of the Agentic Middleware. It is designed for architects, backend/frontend engineers, and DevOps teams to understand the system's runtime dynamics, security boundaries, and data integrity guarantees.

---

## Slide 1: Executive Architecture (Full Ecosystem)

The middleware acts as a high-fidelity broker between the ephemeral frontend UI and the asynchronous backend Agent Executor.

```mermaid
graph TB
    %% ── Styles ──
    classDef clientNode fill:#eff6ff,stroke:#3b82f6,stroke-width:2px,color:#1e40af,font-weight:bold
    classDef mwNode fill:#ecfeff,stroke:#0891b2,stroke-width:3px,color:#155e75,font-weight:bold
    classDef authNode fill:#f5f3ff,stroke:#7c3aed,stroke-width:2px,color:#5b21b6,font-weight:bold
    classDef execNode fill:#fff7ed,stroke:#ea580c,stroke-width:2px,color:#9a3412,font-weight:bold
    classDef eventNode fill:#ecfdf5,stroke:#059669,stroke-width:2px,color:#065f46,font-weight:bold
    classDef dbNode fill:#ecfdf5,stroke:#059669,stroke-width:2px,color:#065f46,font-weight:bold
    classDef monitorNode fill:#fef2f2,stroke:#dc2626,stroke-width:2px,color:#991b1b,font-weight:bold
    classDef logNode fill:#f8fafc,stroke:#475569,stroke-width:2px,color:#334155,font-weight:bold
    classDef identityNode fill:#fef2f2,stroke:#dc2626,stroke-width:2px,color:#991b1b,font-weight:bold

    %% ── Nodes ──
    SOEID["🛡️ X-SOEID Gateway\nEnforces ownership on all access"]:::identityNode
    UI["💬 Frontend Chat UI\nREST calls · WS progress"]:::clientNode
    TS["🔒 Token Service\nOAuth / Bearer credentials"]:::authNode

    MW["⚙️ FastAPI Middleware\nSync UI → Async Execution\nRoutes · WS · Kafka · Audit"]:::mwNode

    BE["🧠 Agent Executor\nAsync task processing"]:::execNode

    K[("📡 Kafka Event Bus")]:::eventNode
    MG[("🗄️ MongoDB\nrecon · sessions · events")]:::dbNode

    PROM["📊 Prometheus\nMetrics endpoint"]:::monitorNode
    AL["📝 Audit Logs\nStructured JSON"]:::logNode

    %% ── Connections ──
    UI <-->|"REST / WS"| MW
    SOEID -.->|"X-SOEID context"| MW
    MW -->|"fetch token"| TS
    MW -->|"POST /execute"| BE
    BE -.->|"async events"| K
    K -.->|"consume"| MW
    MW <-->|"ID & State"| MG
    MW -.->|"metrics"| PROM
    MW -.->|"audit"| AL

    %% ── Identity Boundary ──
    subgraph BOUNDARY ["🔐 Identity Boundary"]
        MW
        MG
    end

    style BOUNDARY fill:none,stroke:#db2777,stroke-width:2px,stroke-dasharray: 8 4,color:#db2777,font-weight:bold
```

> [!TIP]
> **Key Anchors**:
> - **FastAPI**: Provides high-performance async concurrency for IO-bound Kafka/DB operations.
> - **Kafka**: Decouples long-running Agent execution from user requesting thread.
> - **MongoDB**: Serves as the immutable "System of Record" for all historical and active runs.

---

## Slide 2: Detailed Component Map

Internal modular structure of the `app/` directory, illustrating separation of concerns between delivery, logic, and persistence.

```mermaid
graph TD
    classDef api fill:#eff6ff,stroke:#3b82f6,stroke-width:2px,color:#1e40af
    classDef svc fill:#ecfeff,stroke:#0891b2,stroke-width:2px,color:#155e75
    classDef ext fill:#fff7ed,stroke:#ea580c,stroke-width:2px,color:#9a3412
    classDef db fill:#ecfdf5,stroke:#059669,stroke-width:2px,color:#065f46

    subgraph L1 ["🌐 API Layer"]
        CR[chat_routes]:::api
        WR[ws_routes]:::api
    end

    subgraph L2 ["⚙️ Service Layer"]
        CES[execution_svc]:::svc
        EPS[event_proc_svc]:::svc
        WM[ws_manager]:::svc
    end

    subgraph L3 ["🔗 External Clients"]
        TC[token_client]:::ext
        BEC[executor_client]:::ext
        KC[kafka_consumer]:::ext
    end

    subgraph L4 ["🗄️ Persistence"]
        SR[sessions_repo]:::db
        ER[executions_repo]:::db
        EVR[events_repo]:::db
    end

    %% Enforce Layering
    L1 ~~~ L2
    L2 ~~~ L3
    L2 ~~~ L4

    %% API to Services
    CR -->|POST| CES
    WR -->|WS| WM

    %% Services to Clients
    CES -->|fetch auth| TC
    CES -->|POST /execute| BEC
    KC -->|poll| EPS

    %% Services to DB
    CES -->|insert| SR
    CES -->|insert| ER
    EPS -->|upsert| EVR
    EPS -->|update| ER

    %% Internal Service
    EPS -->|broadcast| WM

    style L1 fill:#f8faff,stroke:#3b82f6,stroke-width:2px,stroke-dasharray: 5 5
    style L2 fill:#f0fdfa,stroke:#0891b2,stroke-width:2px,stroke-dasharray: 5 5
    style L3 fill:#fffbf5,stroke:#ea580c,stroke-width:2px,stroke-dasharray: 5 5
    style L4 fill:#f0fdf8,stroke:#059669,stroke-width:2px,stroke-dasharray: 5 5
```

---

## Slide 3: Core Design Decisions

Strategic callouts explaining *why* the architecture is structured this way.

| Decision | Rationale |
| :--- | :--- |
| **Session vs Correlation ID** | `session_id` persists through an entire user conversation. `correlation_id` (UUID v4) provides a deterministic trace for a *single* run within that session. |
| **The `recon` Collection** | Maps Execution/Run records to a centralized "Reconciliation" collection for operational audits and history tracking. |
| **Mongo as Source of Truth** | WebSockets are for "Volatile Delivery" (transient). Mongo allows a client to disconnect/reconnect and immediately "hydrate" state from the historical event log. |
| **Privacy: 404 vs 403** | Unauthorized access to another user's `session` or `correlation_id` returns **404 Not Found** to prevent malicious ID enumeration (Silent Rejection). |

---

## Slide 4: End-to-End Sequence Diagram

The complete lifecycle from a user's initial prompt to real-time status updates and historical retrieval.

```mermaid
sequenceDiagram
    participant User
    participant UI as Frontend UI
    participant MW as Middleware
    participant TS as Token Service
    participant BE as Backend Executor
    participant K as Kafka
    participant DB as MongoDB

    User->>UI: Types Prompt
    UI->>MW: POST /v1/chat/execute (X-SOEID)
    MW->>MW: Identity Validation (deps.py)
    MW->>MW: Generate correlation_id (v4)
    MW->>TS: Fetch Auth Token
    MW->>BE: POST /execute (Headers + Body)
    BE-->>MW: 202 Accepted (Sync ACK)
    MW->>DB: Persist Execution to 'recon' (Status: Accepted)
    MW-->>UI: 200 OK (correlation_id + WS URL)
    UI->>MW: WS Connect /ws/progress/{id}
    
    Note over BE,K: Backend Async Execution starts...
    BE->>K: AGENT_START_EVENT
    K->>MW: Consume Event
    MW->>DB: Persist Event + Update 'recon' (Status: Running)
    MW->>UI: WS Broadcast {status: "running"}
    
    BE->>K: TOOL_INPUT_EVENT
    K->>MW: Consume Event
    MW->>DB: Persist Event + Update 'recon' (Status: in_progress)
    MW->>UI: WS Broadcast {status: "in_progress", tool: "Search"}
    
    Note over User,DB: Future Retrieval
    User->>UI: Refresh Page
    UI->>MW: GET /v1/chat/history/{session_id}
    MW->>DB: Query 'recon' + 'events' (Filtered by soeid)
    DB-->>MW: Documents
    MW-->>UI: History JSON
```

---

## Slide 5: Kafka Processing Pipeline

A robust, at-least-once processing pipeline with duplicate suppression and atomic persistence.

```mermaid
flowchart LR
    K[Kafka Cluster] -->|Poll| KC[KafkaConsumer]
    KC -->|Extract x_correlation_id| VAL{Allowed Type?}
    VAL -- No --> IGN[Ignore/Metric]
    VAL -- Yes --> NORM[Normalize Payload]
    NORM --> IDEM[Generate idempotency_key]
    IDEM --> DB{Upsert Event}
    DB -- Duplicate --> SUP[Suppress/Stop]
    DB -- New --> UPD[Update 'recon' Status]
    UPD --> COM[Commit Offset to Kafka]
    COM --> BR[Broadcast via WebSocket]
```

> [!IMPORTANT]
> **Commit-after-Persistence**: Offsets are only committed to Kafka *after* successful MongoDB persistence. If DB is down, events are redelivered, and idempotency keys prevent duplicates.

---

## Slide 6: WebSocket Lifecycle

Gated, throttled, and resilient real-time delivery.

```mermaid
stateDiagram-v2
    [*] --> VerifyIdentity: Connect Attempt
    VerifyIdentity --> Throttled: Too many attempts (Standard Limit)
    Throttled --> [*]: Close (1008)
    VerifyIdentity --> Authorized: soeid matches correlation_id
    Authorized --> Connected: Active Session
    Connected --> Heartbeat: Idle Ping
    Connected --> EventPush: Progress Update
    Connected --> Disconnected: User Leave
    Connected --> Terminated: Agent Completion/Error
    Terminated --> [*]: Graceful Close
```

> [!WARNING]
> **Horizontal Scaling Notice**: The current `WebSocketManager` is in-memory and limited to **single-instance** deployments. Migration to shared Pub/Sub (e.g., Redis) is required for multi-node middleware clusters.

---

## Slide 7: MongoDB Data Model

A split-view of logical entity connections and the optimized query paths derived from compound indexes.

### Layer 1: Logical Relationships
```mermaid
erDiagram
    SESSIONS ||--o{ RECON : "owns executions"
    RECON ||--o{ EVENTS : "emits"

    SESSIONS {
        string session_id PK
        string soeid
        string last_correlation_id
        timestamp created_at
    }
    RECON {
        string correlation_id PK
        string session_id FK
        string soeid
        string status
        timestamp created_at
    }
    EVENTS {
        string event_idempotency_key PK
        string correlation_id FK
        string event_type
        timestamp timestamp
    }
```

### Layer 2: Query & Index Patterns

| Target Collection | Query Path (Filters) | Supporting Index | Rationale |
| :--- | :--- | :--- | :--- |
| **recon** | `{soeid, correlation_id}` | `(soeid, correlation_id)` | Gated lookup; index prefix matches auth check. |
| **recon** | `{session_id}` | `session_id` | Historical listing within a conversation. |
| **sessions** | `{soeid, session_id}` | `(soeid, session_id)` | Identity-gated session retrieval. |
| **events** | `{correlation_id, timestamp}` | `(correlation_id, timestamp)` | Sequential event log replay for the UI. |
| **events** | `{event_idempotency_key}` | `event_idempotency_key` (Unique) | Kafka duplicate prevention. |

---

## Slide 8: Security & Ownership Flow

Detailed mechanics of the `soeid` identity adapter and the 404 privacy policy.

```mermaid
flowchart TD
    REQ[Incoming Request] --> HDR[Extract X-SOEID Header]
    HDR --> VAL{get_current_user}
    VAL -- Missing --> E401[401 Unauthorized]
    VAL -- Present --> GATED[Retrieve Record from DB]
    GATED --> OWN{Internal soeid matches?}
    OWN -- No --> E404[404 Not Found - Privacy Policy]
    OWN -- Yes --> RTN[Return Data]

    subgraph "Ownership Gating"
        GATED
        OWN
        E404
    end
```

---

## Slide 9: Observability Stack

Metrics and Logging categorized by operational impact.

```mermaid
graph LR
    subgraph "Metrics Categories"
        T[Traffic] --> MT1[middleware_websocket_connections_total]
        T --> MT2[middleware_kafka_messages_consumed_total]
        
        I[Integrity] --> MI1[middleware_kafka_commit_failure_total]
        I --> MI2[middleware_kafka_persistence_failures_total]
        I --> MI3[middleware_kafka_events_duplicate_total]
        
        W[WebSocket] --> MW1[middleware_active_websocket_connections]
        W --> MW2[middleware_websocket_throttled_total]
        
        L[Lifecycle] --> ML1[middleware_active_executions_by_status]
    end

    subgraph "Logging"
        AL[Audit Logger] --> RED[Redaction Engine: Mask Secrets/PII]
        RED --> ST[Structured JSON Logs]
    end
```

---

## Slide 10: Developer Code Map

Navigational guide for engineers maintaining the repository.

| Folder / File | Responsibility | Core Concept |
| :--- | :--- | :--- |
| `app/main.py` | App entry, lifespan config, rate limit init. | Bootstrap |
| `app/api/` | Delivery layer: Chat routes, WebSockets, Auth dependencies. | Deliver |
| `app/core/` | Cross-cutting concerns: Config, Throttling, Exceptions. | Config |
| `app/services/` | Business logic: Execution orchestration, Event processing. | Orchestrate |
| `app/clients/` | External IO: Token API, Backend API, Kafka Polling. | Connect |
| `app/db/repositories/` | Physical storage mapping (recon, sessions, events). | Persist |
| `app/utils/` | Shared utilities: Audit logging, Metrics, Retry, ID generation. | Utility |
| `app/models/` | Type safety: API Schemas, DB Docs, Kafka Event shapes. | Schema |

---

## Slide 11: Feature-to-Code Traceability

Mapping high-level system guarantees to specific technical implementations.

| Feature | Primary Files | Supporting Files | Runtime Behavior |
| :--- | :--- | :--- | :--- |
| **Execute Flow** | `chat_execution_service.py` | `chat_routes.py`, `backend_executor_client.py` | Accepts POST, generates IDs, fetches tokens, calls backend, and persists initial record. |
| **At-Least-Once Kafka** | `kafka_consumer.py` | `event_processing_service.py` | Polls partitions; commits offset *only after* repository commit is confirmed. |
| **Idempotency** | `events_repository.py` | `mongo.py`, `kafka_events.py` | Uses `event_idempotency_key` with Mongo Unique Index to drop duplicate messages. |
| **Ownership Gating** | `deps.py` | `chat_routes.py`, `executions_repository.py` | Extracts `X-SOEID`; every repo query includes mandatory `soeid` filter predicate. |
| **Privacy Policy** | `chat_routes.py` | `status_service.py` | Intercepts ownership mismatches and returns 404 to hide resource existence. |
| **Live Updates** | `websocket_manager.py` | `websocket_routes.py`, `event_processing_service.py` | Manages active socket maps; pushes normalized events to correct user channel. |
| **Audit Hygiene** | `audit_logger.py` | `chat_execution_service.py` | Masks secrets and context fields before sending lifecycle data to logs. |
| **Throttling** | `throttler.py` | `websocket_routes.py`, `lifespan.py` | Tracks connection frequency per IP/User; prunes stale map entries via background loop. |
