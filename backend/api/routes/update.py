"""版本与更新检查接口（只读）。"""

from fastapi import APIRouter, Depends, Query

from backend.core.auth import get_current_user
from backend.models.user import User
from backend.services.update_check import build_info, get_update_status

router = APIRouter()


@router.get("/version")
def get_version(current_user: User = Depends(get_current_user)) -> dict:
    """当前程序版本与构建信息。"""
    return build_info()


@router.get("/status")
def get_status(
    refresh: int = Query(0, ge=0, le=1),
    current_user: User = Depends(get_current_user),
) -> dict:
    """检查是否有新版本（结果缓存 6 小时，refresh=1 强制重新检查）。"""
    return get_update_status(force=bool(refresh))
