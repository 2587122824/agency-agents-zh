from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db.models import ProjectProductionProfileVersion


PROFILE_CONTRACT_VERSION = "project-production-profile.v1"

VIDEO_MOTION_STRATEGIES = {"adaptive", "three_frame", "start_end"}
KEYFRAME_STRATEGIES = {"adaptive", "omni_reference"}
ENFORCEMENT_MODES = {"required"}


def canonical_profile_contract(
    *,
    project_id: str,
    version_number: int,
    video_motion_strategy: str,
    keyframe_strategy: str,
    enforcement: str,
    selected_by: str,
) -> dict[str, Any]:
    return {
        "contract_version": PROFILE_CONTRACT_VERSION,
        "project_id": project_id,
        "version_number": version_number,
        "video_motion_strategy": video_motion_strategy,
        "keyframe_strategy": keyframe_strategy,
        "enforcement": enforcement,
        "selected_by": selected_by,
        "required_frame_roles": (
            ["start_frame", "middle_frame", "end_frame"]
            if video_motion_strategy == "three_frame"
            else ["start_frame", "end_frame"]
            if video_motion_strategy == "start_end"
            else []
        ),
    }


def profile_contract_hash(contract: dict[str, Any]) -> str:
    encoded = json.dumps(
        contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def active_profile(session: Session, project_id: str) -> ProjectProductionProfileVersion:
    row = session.scalar(
        select(ProjectProductionProfileVersion)
        .where(
            ProjectProductionProfileVersion.project_id == project_id,
            ProjectProductionProfileVersion.is_active.is_(True),
        )
        .order_by(ProjectProductionProfileVersion.version_number.desc())
    )
    if row is None:
        raise ValueError("PROJECT_PRODUCTION_PROFILE_MISSING")
    return row


def profile_manifest(session: Session, project_id: str) -> dict[str, Any]:
    row = active_profile(session, project_id)
    return {
        "id": row.id,
        "contract_version": row.contract_version,
        "version_number": row.version_number,
        "video_motion_strategy": row.video_motion_strategy,
        "keyframe_strategy": row.keyframe_strategy,
        "enforcement": row.enforcement,
        "selected_by": row.selected_by,
        "required_frame_roles": row.required_frame_roles or [],
        "contract_hash": row.contract_hash,
    }


def production_profile_options() -> dict[str, Any]:
    return {
        "contract_version": PROFILE_CONTRACT_VERSION,
        "video_motion_strategies": [
            {
                "key": "three_frame",
                "display_name": "首中尾三帧",
                "description": "每个镜头必须生成并审核首帧、中帧和尾帧，再使用三帧生视频。",
                "available": True,
                "recommended": True,
            },
            {
                "key": "adaptive",
                "display_name": "按镜头匹配",
                "description": "允许分镜导演声明单帧、三帧或纯文本需求，再由制作规划匹配当前工作流。",
                "available": True,
                "recommended": False,
            },
            {
                "key": "start_end",
                "display_name": "首尾帧",
                "description": "当前没有经过真实验证的首尾帧工作流，暂不可选择。",
                "available": False,
                "recommended": False,
            },
        ],
        "keyframe_strategies": [
            {
                "key": "adaptive",
                "display_name": "按镜头匹配参考",
                "description": "根据已确认人物、风格和镜头要求选择当前可执行的关键帧方案。",
                "available": True,
                "recommended": True,
            },
            {
                "key": "omni_reference",
                "display_name": "全能参考",
                "description": "当前缺少多参考输入合同和真实成功工作流证据，暂不可选择。",
                "available": False,
                "recommended": False,
            },
        ],
        "enforcement": "required",
    }
