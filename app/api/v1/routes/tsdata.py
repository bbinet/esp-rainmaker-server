"""Time-series read routes (mobile app)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps.auth import get_current_user
from app.api.v1.deps.db import get_db
from app.core.errors import invalid_request
from app.models.user import User
from app.services import access as access_service
from app.services import tsdata as tsdata_service

router = APIRouter(tags=["tsdata"])


def _ts(seconds: int) -> datetime:
    return datetime.fromtimestamp(seconds, tz=UTC)


@router.get("/user/nodes/tsdata")
async def get_tsdata(
    node_id: str = Query(...),
    param: str = Query(...),
    start_time: int = Query(...),
    end_time: int = Query(...),
    aggregate: str | None = Query(default=None),
    aggregate_interval: str | None = Query(default=None),
    num_records: int = Query(default=200, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await access_service.require_access(db, user_id=user.id, node_id=node_id)
    try:
        records = await tsdata_service.query_tsdata(
            db,
            node_id=node_id,
            param=param,
            start_time=_ts(start_time),
            end_time=_ts(end_time),
            aggregate=aggregate,
            aggregate_interval=aggregate_interval,
            limit=num_records,
        )
    except ValueError as exc:
        raise invalid_request(str(exc)) from exc
    return {
        "node_id": node_id,
        "param": param,
        "records": records,
    }


@router.get("/user/nodes/simple_tsdata")
async def get_simple_tsdata(
    node_id: str = Query(...),
    param: str = Query(...),
    start_time: int = Query(...),
    end_time: int = Query(...),
    num_records: int = Query(default=200, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    return await get_tsdata(
        node_id=node_id,
        param=param,
        start_time=start_time,
        end_time=end_time,
        aggregate=None,
        aggregate_interval=None,
        num_records=num_records,
        user=user,
        db=db,
    )
