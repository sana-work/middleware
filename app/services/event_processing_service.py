from typing import Optional, Dict, Any

from app.core.logging import get_logger
from app.db.repositories.events_repository import EventsRepository
from app.db.repositories.executions_repository import ExecutionsRepository
from app.models.kafka_events import (
    RawKafkaEvent,
    EVENT_NORMALIZATION_MAP,
    EXECUTION_STATUS_MAP,
    EVENT_SUMMARY_MAP,
    NormalizedEvent,
    ALLOWED_EVENT_TYPES,
    TERMINAL_ERROR_PATTERNS,
    TOOL_BUSINESS_CONTEXT_MAP,
    AGENT_BUSINESS_CONTEXT_MAP
)
from app.services.metrics_service import (
    KAFKA_EVENTS_PROCESSED_TOTAL,
    KAFKA_EVENTS_IGNORED_TOTAL,
    KAFKA_EVENTS_DUPLICATE_TOTAL,
    KAFKA_PERSISTENCE_FAILURES_TOTAL,
    increment_active_executions,
    decrement_active_executions
)
from app.utils.audit_logger import audit_logger
from app.utils.datetime_utils import utc_now, format_iso
from app.utils.business_logic import get_business_description

logger = get_logger(__name__)


class EventProcessingService:
    """
    Core event normalization and persistence logic.

    Pipeline:
      1. Normalize raw event
      2. Generate idempotency key
      3. Insert event (skip if duplicate)
      4. Update execution status
      5. Return processed event dict for WebSocket broadcast
    """

    @staticmethod
    async def process_kafka_event(
        raw_event: RawKafkaEvent,
        correlation_id: str,
        kafka_metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Process a single Kafka event.
        Returns a dict suitable for WebSocket broadcast, or None if the event was a duplicate.
        """
        event_type = raw_event.event_type
        
        # 0. Infer event_type if missing (Handling backend terminal signals with just status)
        if not event_type:
            if raw_event.status == "SUCCESS":
                 event_type = "EXECUTION_FINAL_RESPONSE"
            elif raw_event.status == "FAILED":
                 event_type = "AGENT_ERROR_EVENT"
            
        # Check if allowed after inference
        if event_type not in ALLOWED_EVENT_TYPES:
            logger.debug("Ignoring event with no type mapping", correlation_id=correlation_id, status=raw_event.status)
            KAFKA_EVENTS_IGNORED_TOTAL.inc()
            return None

        normalized_type = EVENT_NORMALIZATION_MAP.get(event_type, "unknown")
        execution_status = EXECUTION_STATUS_MAP.get(event_type, "unknown")

        # Generate business-friendly description
        business_desc = get_business_description(
            event_type, raw_event.tool_name, raw_event.agent_name
        )

        # Build summary
        summary_template = EVENT_SUMMARY_MAP.get(event_type, "Event received")
        summary = summary_template.format(
            tool_name=raw_event.tool_name or "unknown",
            agent_name=raw_event.agent_name or "unknown",
        )

        # 0.1 Handle hidden failures in final response (e.g. MCP Errors)
        if event_type == "EXECUTION_FINAL_RESPONSE" and raw_event.response:
            resp_str = str(raw_event.response)
            if any(pattern in resp_str for pattern in TERMINAL_ERROR_PATTERNS):
                logger.warning(
                    "Detected hidden failure in final response", 
                    correlation_id=correlation_id,
                    pattern_found=True
                )
                execution_status = "failed"
                normalized_type = "agent_failed"
                summary = "Final execution failed: Hidden agent error detected"

        # Build timestamp
        event_timestamp = raw_event.timestamp or format_iso(utc_now())

        # Build normalized payload
        normalized_payload = None
        if raw_event.response is not None:
            if isinstance(raw_event.response, dict):
                normalized_payload = {"response": raw_event.response}
            else:
                normalized_payload = {"response": str(raw_event.response)}

        # Look up execution to get session_id and soeid
        execution = await ExecutionsRepository.get_execution(correlation_id)
        if not execution:
            logger.warning("No execution found for event", correlation_id=correlation_id)
            return None

        session_id = execution["session_id"]
        soeid = execution["soeid"]

        # Generate idempotency key
        idempotency_key = f"{correlation_id}:{event_type}:{event_timestamp}:{raw_event.tool_name or ''}"

        # Build event document
        now = format_iso(utc_now())
        event_doc: Dict[str, Any] = {
            "event_idempotency_key": idempotency_key,
            "correlation_id": correlation_id,
            "session_id": session_id,
            "soeid": soeid,
            "event_type": event_type,
            "normalized_event_type": normalized_type,
            "status": execution_status,
            "agent_name": raw_event.agent_name,
            "tool_name": raw_event.tool_name,
            "business_description": business_desc,
            "timestamp": event_timestamp,
            "summary": summary,
            "raw_payload": raw_event.model_dump(),
            "normalized_payload": normalized_payload,
            "created_at": now,
        }

        # Add Kafka metadata
        if kafka_metadata:
            for k, v in kafka_metadata.items():
                event_doc[f"kafka_{k}"] = v

        # Insert event (idempotent)
        try:
            inserted = await EventsRepository.insert_event(event_doc)
            if not inserted:
                KAFKA_EVENTS_DUPLICATE_TOTAL.inc()
                return None
        except Exception as e:
            KAFKA_PERSISTENCE_FAILURES_TOTAL.inc()
            raise e

        # Update execution status
        old_status = execution["status"]
        completed_at = event_timestamp if execution_status in ("completed", "failed") else None
        
        await ExecutionsRepository.update_execution_status(
            correlation_id=correlation_id,
            status=execution_status,
            latest_event_type=event_type,
            completed_at=completed_at,
        )

        # Audit Log
        audit_logger.log_event(
            "KAFKA_EVENT_PROCESSED",
            user_id=soeid,
            correlation_id=correlation_id,
            data={"event_type": event_type, "status": execution_status, "summary": summary}
        )

        # Metrics
        KAFKA_EVENTS_PROCESSED_TOTAL.labels(event_type=event_type).inc()
        
        # Update gauges
        if old_status != execution_status:
            decrement_active_executions(old_status)
            increment_active_executions(execution_status)

        # Return data for WebSocket broadcast
        return {
            "correlation_id": correlation_id,
            "session_id": session_id,
            "event_type": event_type,
            "normalized_event_type": normalized_type,
            "status": execution_status,
            "agent_name": raw_event.agent_name,
            "tool_name": raw_event.tool_name,
            "business_description": business_desc,
            "timestamp": event_timestamp,
            "summary": summary,
            "payload": normalized_payload,
        }
