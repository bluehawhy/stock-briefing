"""Build identity is baked into the image, independent of the mounted checkout."""

from datetime import datetime
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo


def version_text() -> str:
    build_time, build_commit = "정보 없음", "정보 없음"
    try:
        info = json.loads((Path(__file__).resolve().parent.parent / "build-info.json").read_text())
        at = datetime.fromisoformat(info["build_time"])
        if at.tzinfo is not None:
            build_time = at.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M:%S")
        commit = info.get("build_commit", "")
        if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit):
            build_commit = commit
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return f"빌드시간: {build_time}\n빌드번호: {build_commit}"
