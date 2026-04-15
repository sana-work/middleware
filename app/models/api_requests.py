from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class ChatExecuteRequest(BaseModel):
    """Inbound request from frontend to start an agent execution."""
    context: str = Field(..., description="The user instruction / investigation query")
    soeid: str = Field(..., description="The user's SOEID identifier")
    session_id: Optional[str] = Field(None, description="Optional existing session UUID to reuse")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Optional client metadata")
