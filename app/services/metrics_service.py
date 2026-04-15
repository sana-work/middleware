from prometheus_client import Counter, Gauge

# Kafka Metrics
KAFKA_MESSAGES_CONSUMED_TOTAL = Counter(
    "middleware_kafka_messages_consumed_total", 
    "Total number of Kafka messages consumed"
)
KAFKA_EVENTS_PROCESSED_TOTAL = Counter(
    "middleware_kafka_events_processed_total", 
    "Total number of events successfully normalized and persisted",
    ["event_type"]
)
KAFKA_EVENTS_IGNORED_TOTAL = Counter(
    "middleware_kafka_events_ignored_total", 
    "Total number of events ignored (unsupported type)"
)
KAFKA_EVENTS_DUPLICATE_TOTAL = Counter(
    "middleware_kafka_events_duplicate_total", 
    "Total number of duplicate events suppressed"
)
KAFKA_PERSISTENCE_FAILURES_TOTAL = Counter(
    "middleware_kafka_persistence_failures_total", 
    "Total number of database persistence failures"
)
KAFKA_COMMIT_SUCCESS_TOTAL = Counter(
    "middleware_kafka_commit_success_total", 
    "Total number of successful Kafka offset commits"
)
KAFKA_COMMIT_FAILURE_TOTAL = Counter(
    "middleware_kafka_commit_failure_total", 
    "Total number of failed Kafka offset commits"
)

# Execution Metrics
ACTIVE_EXECUTIONS_BY_STATUS = Gauge(
    "middleware_active_executions_by_status",
    "Current number of active executions partitioned by status",
    ["status"]
)

# WebSocket Metrics
ACTIVE_WS_CONNECTIONS = Gauge(
    "middleware_active_websocket_connections",
    "Current number of active WebSocket connections"
)
WS_CONNECTIONS_TOTAL = Counter(
    "middleware_websocket_connections_total",
    "Total number of established WebSocket connections"
)
WS_THROTTLED_TOTAL = Counter(
    "middleware_websocket_throttled_total",
    "Total number of WebSocket connections rejected due to rate limiting"
)

def increment_active_executions(status_val: str):
    ACTIVE_EXECUTIONS_BY_STATUS.labels(status=status_val).inc()

def decrement_active_executions(status_val: str):
    ACTIVE_EXECUTIONS_BY_STATUS.labels(status=status_val).dec()
