from typing import Optional

from app.utils.ids import generate_session_id
from app.db.repositories.sessions_repository import SessionsRepository
from app.core.logging import get_logger

logger = get_logger(__name__)


class SessionService:
    """Service for session resolution and creation."""

    @staticmethod
    async def resolve_session_id(session_id: Optional[str]) -> str:
        """
        If the client provides a session_id, reuse it.
        Otherwise generate a new UUID v4 session_id.
        """
        if session_id:
            logger.info("Reusing existing session_id", session_id=session_id)
            return session_id

        new_id = generate_session_id()
        logger.info("Generated new session_id", session_id=new_id)
        return new_id
