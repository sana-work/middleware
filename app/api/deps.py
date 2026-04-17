from fastapi import Query, Header, HTTPException, status
from typing import Optional

async def get_current_user(
    x_soeid: Optional[str] = Header(None, alias="x-soeid"),
    soeid: Optional[str] = Query(None),
) -> str:
    """
    Temporary Identity Adapter.
    Extracts the user identity (soeid) from headers or query parameters.
    
    This structures the codebase for future JWT/OIDC authentication.
    Currently, it serves as the trust anchor for ownership validation.
    """
    user_id = x_soeid or soeid
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User identity (SOEID) is required"
        )
    
    return user_id
