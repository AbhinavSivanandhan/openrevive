from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.workspace import Workspace

router = APIRouter(
    prefix="/v1/workspaces",
    tags=["workspaces"],
)


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=120,
    )


class WorkspaceResponse(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=list[WorkspaceResponse])
async def list_workspaces(
    session: AsyncSession = Depends(get_db_session),
) -> list[Workspace]:
    result = await session.scalars(
        select(Workspace).order_by(Workspace.created_at.asc())
    )
    return list(result)


@router.post(
    "",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace(
    payload: WorkspaceCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Workspace:
    workspace = Workspace(name=payload.name)

    session.add(workspace)
    await session.commit()
    await session.refresh(workspace)

    return workspace
