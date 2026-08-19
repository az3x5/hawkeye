"""Who the caller is."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.v1.security import get_principal
from app.core.errors import ErrorResponse
from app.domain.auth import Principal, Scope

router = APIRouter(tags=["system"])


class IdentityResponse(BaseModel):
    """The authenticated caller's own identity."""

    subject: str = Field(description="Who the credential belongs to.")
    kind: str = Field(description="'user' or 'service'.")
    scopes: list[Scope] = Field(description="What this credential may do.")
    token_uuid: UUID
    expires_at: datetime | None = Field(
        default=None, description="When the credential stops working; null if never."
    )


@router.get(
    "/me",
    response_model=IdentityResponse,
    summary="Identify the authenticated caller",
    responses={401: {"model": ErrorResponse}},
)
async def read_me(
    principal: Annotated[Principal, Depends(get_principal)],
) -> IdentityResponse:
    """Report who the presented credential belongs to.

    Requires no scope: any valid credential may ask who it is. The review UI
    shows this, because decisions are attributed to it and a reviewer should
    never be in doubt about which identity they are acting under.
    """
    return IdentityResponse(
        subject=principal.subject,
        kind=principal.kind,
        scopes=sorted(principal.scopes, key=lambda scope: scope.value),
        token_uuid=principal.token_uuid,
        expires_at=principal.expires_at,
    )
