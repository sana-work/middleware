from typing import Optional, List, Dict, Any

from app.core.exceptions import ExecutionNotFoundError, SessionNotFoundError
from app.core.logging import get_logger
from app.db.repositories.executions_repository import ExecutionsRepository
from app.db.repositories.events_repository import EventsRepository
from app.db.repositories.sessions_repository import SessionsRepository
from app.models.api_responses import (
    EventSummary,
    StatusData,
    ChatStatusResponse,
    ExecutionInHistory,
    HistoryData,
    ChatHistoryResponse,
)
from app.utils.datetime_utils import format_iso

logger = get_logger(__name__)


class StatusService:
    """Service for building status and history responses from MongoDB."""

    @staticmethod
    async def get_execution_status(correlation_id: str, soeid: str) -> ChatStatusResponse:
        """
        Get the current execution status with timeline events.
        Validates ownership using soeid.
        """
        execution = await ExecutionsRepository.get_execution_by_soeid(correlation_id, soeid)
        if not execution:
            raise ExecutionNotFoundError(
                f"Execution not found for correlation_id={correlation_id} and soeid={soeid}"
            )

        events_raw = await EventsRepository.get_events_by_correlation(correlation_id)
        events = [
            EventSummary(
                event_type=e.get("event_type", ""),
                normalized_event_type=e.get("normalized_event_type", ""),
                status=e.get("status", ""),
                agent_name=e.get("agent_name"),
                tool_name=e.get("tool_name"),
                timestamp=e.get("timestamp", ""),
                summary=e.get("summary", ""),
            )
            for e in events_raw
        ]

        data = StatusData(
            correlation_id=execution["correlation_id"],
            session_id=execution["session_id"],
            soeid=execution["soeid"],
            status=execution["status"],
            latest_event_type=execution.get("latest_event_type"),
            started_at=execution["created_at"],
            updated_at=execution["updated_at"],
            completed_at=execution.get("completed_at"),
            request_context=execution["request_context"],
            events=events,
        )

        return ChatStatusResponse(success=True, data=data)

    @staticmethod
    async def get_session_history(
        session_id: str,
        soeid: str,
        skip: int = 0,
        limit: int = 50,
        include_events: bool = True,
    ) -> ChatHistoryResponse:
        """
        Get full session history with all executions and their events.
        Validates ownership using soeid. Supports pagination.
        """
        session = await SessionsRepository.get_session_by_soeid(session_id, soeid)
        if not session:
            raise SessionNotFoundError(
                f"Session not found for session_id={session_id} and soeid={soeid}"
            )

        executions_raw = await ExecutionsRepository.get_executions_by_session(
            session_id, skip=skip, limit=limit
        )
        total = await ExecutionsRepository.count_executions_by_session(session_id)

        executions: List[ExecutionInHistory] = []
        for ex in executions_raw:
            events: List[EventSummary] = []
            if include_events:
                events_raw = await EventsRepository.get_events_by_correlation(
                    ex["correlation_id"]
                )
                events = [
                    EventSummary(
                        event_type=e.get("event_type", ""),
                        normalized_event_type=e.get("normalized_event_type", ""),
                        status=e.get("status", ""),
                        agent_name=e.get("agent_name"),
                        tool_name=e.get("tool_name"),
                        timestamp=e.get("timestamp", ""),
                        summary=e.get("summary", ""),
                    )
                    for e in events_raw
                ]

            executions.append(
                ExecutionInHistory(
                    correlation_id=ex["correlation_id"],
                    status=ex["status"],
                    request_context=ex["request_context"],
                    started_at=ex["created_at"],
                    completed_at=ex.get("completed_at"),
                    latest_event_type=ex.get("latest_event_type"),
                    events=events,
                )
            )

        data = HistoryData(
            session_id=session["session_id"],
            soeid=session["soeid"],
            created_at=session["created_at"],
            updated_at=session["updated_at"],
            executions=executions,
            total=total,
            limit=limit,
            skip=skip,
        )

        return ChatHistoryResponse(success=True, data=data)
