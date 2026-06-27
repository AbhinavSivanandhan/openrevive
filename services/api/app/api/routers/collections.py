from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.collection import Collection
from app.models.workspace import Workspace

router = APIRouter(
    prefix="/v1/workspaces/{workspace_id}/collections",
    tags=["collections"],
)


class CollectionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5000)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        normalized_value = value.strip()

        if not normalized_value:
            raise ValueError("name must not be blank")

        return normalized_value

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None

        normalized_value = value.strip()
        return normalized_value or None


class CollectionResponse(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


async def get_workspace_or_404(
    workspace_id: UUID,
    session: AsyncSession,
) -> Workspace:
    workspace = await session.get(Workspace, workspace_id)

    if workspace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="workspace not found",
        )

    return workspace


@router.get("", response_model=list[CollectionResponse])
async def list_collections(
    workspace_id: UUID,
    session: AsyncSession = Depends(get_db_session),
) -> list[Collection]:
    await get_workspace_or_404(workspace_id, session)

    result = await session.scalars(
        select(Collection)
        .where(Collection.workspace_id == workspace_id)
        .order_by(Collection.created_at.asc())
    )

    return list(result)


@router.post(
    "",
    response_model=CollectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_collection(
    workspace_id: UUID,
    payload: CollectionCreateRequest,
    session: AsyncSession = Depends(get_db_session),
) -> Collection:
    await get_workspace_or_404(workspace_id, session)

    collection = Collection(
        workspace_id=workspace_id,
        name=payload.name,
        description=payload.description,
    )
    session.add(collection)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a collection with this name already exists in this workspace",
        ) from exc

    await session.refresh(collection)

    return collection
