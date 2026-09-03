"""Read the latest local evaluation report without starting paid work."""

import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/evaluation", tags=["evaluation"])


@router.get("/latest", summary="Read the latest offline evaluation summary")
async def latest_evaluation():
    path = Path("var/evaluations/latest.json")
    if not path.exists():
        return {"available": False, "message": "尚未运行本地离线评测。"}
    try:
        return {"available": True, **json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError):
        return {"available": False, "message": "最近评测报告无法读取。"}
