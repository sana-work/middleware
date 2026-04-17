from typing import Dict, Any, Optional

from app.clients.token_client import token_client
from app.clients.backend_executor_client import backend_executor_client
from app.core.logging import get_logger
from app.db.repositories.sessions_repository import SessionsRepository
from app.db.repositories.executions_repository import ExecutionsRepository
from app.models.api_requests import ChatExecuteRequest
from app.models.api_responses import ChatExecuteResponse, ExecuteData
from app.services.session_service import SessionService
from app.services.metrics_service import increment_active_executions
from app.utils.audit_logger import audit_logger
from app.utils.ids import generate_correlation_id
from app.utils.datetime_utils import utc_now, format_iso

logger = get_logger(__name__)


class ChatExecutionService:
    """
    Orchestrates the main POST /api/v1/chat/execute flow:
      1. Generate correlation_id (UUID v4)
      2. Resolve or generate session_id (UUID v4)
      3. Fetch auth token
      4. Call backend executor
      5. Persist session (upsert) + execution (insert) to MongoDB
      6. Return immediate response with websocket_url
    """

    @staticmethod
    async def execute(request: ChatExecuteRequest) -> ChatExecuteResponse:
        """Execute the full chat execution orchestration flow."""
        correlation_id = generate_correlation_id()
        session_id = await SessionService.resolve_session_id(request.session_id)

        logger.info(
            "Starting chat execution",
            correlation_id=correlation_id,
            session_id=session_id,
            soeid=request.soeid,
        )

        # Fetch token
        token = await token_client.get_token()

        now = format_iso(utc_now())

        # 1. Persist session (upsert)
        await SessionsRepository.create_or_update_session(
            session_id=session_id,
            soeid=request.soeid,
            correlation_id=correlation_id,
            metadata=request.metadata,
        )

        # 2. Persist execution record FIRST to avoid race conditions with fast Kafka events
        from app.core.config import settings
        execution_doc: Dict[str, Any] = {
            "correlation_id": correlation_id,
            "session_id": session_id,
            "soeid": request.soeid,
            "request_context": request.context,
            "backend_request": {
                "headers_sent": {
                    "Config-ID": "masked",
                    "X-Correlation-ID": correlation_id,
                    "X-Application-ID": settings.BACKEND_APPLICATION_ID,
                    "Session-ID": session_id,
                    "x-soeid": request.soeid,
                },
                "body": {"context": request.context},
            },
            "backend_ack": None, # Will be updated after call
            "status": "accepted",
            "latest_event_type": None,
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        await ExecutionsRepository.create_execution(execution_doc)

        # 3. Call backend executor
        backend_response = await backend_executor_client.execute(
            context=request.context,
            correlation_id=correlation_id,
            session_id=session_id,
            soeid=request.soeid,
            token=token,
        )

        # 4. Update execution with backend ack
        await ExecutionsRepository.update_execution_status(
            correlation_id=correlation_id,
            status="accepted",
            latest_event_type=None,
            completed_at=None,
            # We add a way to update backend_ack specifically
        )
        # Actually I should just update the document with the ack
        db = ExecutionsRepository.get_db_for_update()
        await db.executions.update_one(
            {"correlation_id": correlation_id},
            {"$set": {"backend_ack": backend_response, "updated_at": format_iso(utc_now())}}
        )

        
        # Metrics & Audit
        increment_active_executions("accepted")
        audit_logger.log_event(
            "EXECUTION_INITIALIZED",
            user_id=request.soeid,
            correlation_id=correlation_id,
            data={"session_id": session_id, "body": request.model_dump()}
        )

        logger.info(
            "Chat execution initialized",
            correlation_id=correlation_id,
            session_id=session_id,
        )

        # Build response
        backend_ack_message = (
            backend_response.get("message", "Execution initialized successfully")
            if isinstance(backend_response, dict)
            else "Execution initialized successfully"
        )

        return ChatExecuteResponse(
            success=True,
            message="Execution initialized successfully",
            data=ExecuteData(
                correlation_id=correlation_id,
                session_id=session_id,
                status="accepted",
                backend_ack={"message": backend_ack_message},
                websocket_url=f"/ws/v1/chat/progress/{correlation_id}",
                created_at=now,
            ),
        )
