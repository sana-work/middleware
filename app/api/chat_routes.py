from fastapi import APIRouter, Query, Depends, Request

from app.core.limiter import limiter
from app.api.deps import get_current_user
from app.models.api_requests import ChatExecuteRequest
from app.models.api_responses import ChatExecuteResponse, ChatStatusResponse, ChatHistoryResponse
from app.services.chat_execution_service import ChatExecutionService
from app.services.status_service import StatusService

router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


@router.post("/execute", response_model=ChatExecuteResponse)
@limiter.limit("10/minute")
async def execute_chat(
    request: Request,
    body: ChatExecuteRequest,
    current_user: str = Depends(get_current_user)
) -> ChatExecuteResponse:
    """
    Accept a frontend chat request. Verified current user must match request soeid.
    """
    # Enforce request soeid matches authenticated principal
    body.soeid = current_user
    return await ChatExecutionService.execute(body)


@router.get("/status/{correlation_id}", response_model=ChatStatusResponse)
@limiter.limit("20/minute")
async def get_status(
    request: Request,
    correlation_id: str,
    current_user: str = Depends(get_current_user),
) -> ChatStatusResponse:
    """
    Return current execution status if owned by current_user.
    Returns 404 if not found or unauthorized to prevent enumeration.
    """
    return await StatusService.get_execution_status(correlation_id, current_user)


@router.get("/history/{session_id}", response_model=ChatHistoryResponse)
@limiter.limit("5/minute")
async def get_history(
    request: Request,
    session_id: str,
    current_user: str = Depends(get_current_user),
    skip: int = Query(0, ge=0, description="Number of executions to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max executions to return"),
    include_events: bool = Query(True, description="Include events in each execution"),
) -> ChatHistoryResponse:
    """
    Return all executions for a session if owned by current_user.
    Returns 404 if not found or unauthorized.
    """
    return await StatusService.get_session_history(
        session_id=session_id,
        soeid=current_user,
        skip=skip,
        limit=limit,
        include_events=include_events,
    )
