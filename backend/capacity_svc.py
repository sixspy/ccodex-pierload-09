"""额载禁入：地磅额载状态汇总与纪要只读。"""
from __future__ import annotations

from fastapi import APIRouter

from auth import AuthHeader, require_user
from db import capacity_overview, connect

router = APIRouter(tags=["capacity"])


@router.get("/api/capacity")
def get_capacity(authorization: AuthHeader = None):
    """账实唯一口径的地磅额载状态，供地磅/车道/过磅/超重/对账五页同态引用。"""
    require_user(authorization)
    with connect() as conn:
        return capacity_overview(conn)
