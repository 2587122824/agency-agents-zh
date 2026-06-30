from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import shutil
import sys
import threading
import time
import traceback
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from PIL import Image

from my_codex_core.cloud_comfyui_adapter import CloudComfyUIAdapter
from my_codex_core.production_pipeline import retry_production_job
from my_codex_core.workflow_engine import WorkflowCheckpointPause, WorkflowEngine


WORKSPACE_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = WORKSPACE_ROOT / "my_task_output"
WORKFLOW_ROOT = WORKSPACE_ROOT / "my_workflows"
STAFF_ROOT = WORKSPACE_ROOT / "my_custom_staff"
MEMORY_ROOT = WORKSPACE_ROOT / "my_memory"
REFERENCE_ROOT = WORKSPACE_ROOT / "my_reference_images"
VOICE_SAMPLE_ROOT = WORKSPACE_ROOT / "my_voice_samples"
KNOWLEDGE_ROOT = WORKSPACE_ROOT / "my_knowledge_base"
ASSET_LIBRARY_ROOT = WORKSPACE_ROOT / "my_asset_library"
ASSET_LIBRARY_INDEX = ASSET_LIBRARY_ROOT / "library.json"
ASSET_LIBRARY_TAG_FOLDERS = {
    "character_base": "01_character_base",
    "product_base": "02_product_base",
    "scene_base": "03_scene_base",
    "character_turnaround": "04_character_turnaround",
    "product_turnaround": "05_product_turnaround",
    "style_reference": "06_style_reference",
    "keyframe": "07_keyframe",
    "cover_key_visual": "08_cover_key_visual",
    "image_inpaint_fix": "09_image_inpaint_fix",
    "background_remove": "10_background_remove",
    "i2v_first_frame": "11_i2v_first_frame",
    "i2v_first_last_frame": "12_i2v_first_last_frame",
    "i2v_first_middle_last_frame": "12_i2v_first_last_frame",
    "live_to_anime": "13_live_to_anime",
    "motion_transfer": "14_motion_transfer",
    "talking_image": "15_talking_image",
    "broll_scene_video": "16_broll_scene_video",
    "empty_transition_video": "17_empty_transition_video",
    "video_upscale": "18_video_upscale",
    "frame_interpolation": "19_frame_interpolation",
    "video_deflicker_stabilize": "20_video_deflicker_stabilize",
    "video_inpaint_fix": "21_video_inpaint_fix",
    "bgm": "22_bgm",
}
COMFY_DEBUG_TASK = "__comfy_debug__"
COMFY_DEBUG_ROOT = OUTPUT_ROOT / COMFY_DEBUG_TASK
LOCAL_MODEL_PRESETS = WORKSPACE_ROOT / "my_local_models" / "local_model_presets.json"
RUN_JOBS: dict[str, dict] = {}
RUN_JOBS_LOCK = threading.RLock()
IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".jpe",
    ".jfif",
    ".pjpeg",
    ".pjp",
    ".webp",
    ".bmp",
    ".dib",
    ".gif",
    ".tif",
    ".tiff",
    ".avif",
    ".heic",
    ".heif",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".aac", ".m4a", ".flac", ".ogg"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS
mimetypes.add_type("image/jpeg", ".jfif")
mimetypes.add_type("image/jpeg", ".jpe")
mimetypes.add_type("image/jpeg", ".pjpeg")
mimetypes.add_type("image/jpeg", ".pjp")
mimetypes.add_type("image/bmp", ".bmp")
mimetypes.add_type("image/bmp", ".dib")
mimetypes.add_type("image/tiff", ".tif")
mimetypes.add_type("image/tiff", ".tiff")
mimetypes.add_type("image/avif", ".avif")
mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("image/heif", ".heif")

COMFY_DEBUG_WORKFLOWS = [
    {"id": "01_character_base", "name": "01 角色基础图", "type": "image", "stage": "image_base", "purpose": "生成可复用人物角色设定图。", "asset_tag": "character_base", "recommended": True, "default_task_type": "character_generation", "default_control_mode": "none", "default_image_task_type": "character_generation", "default_width": 1080, "default_height": 1920, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "02_product_base", "name": "02 产品基础图", "type": "image", "stage": "image_base", "purpose": "生成可复用产品主体图。", "asset_tag": "product_base", "recommended": True, "default_task_type": "product_generation", "default_control_mode": "none", "default_image_task_type": "product_generation", "default_width": 1080, "default_height": 1920, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "03_scene_base", "name": "03 场景基础图", "type": "image", "stage": "image_base", "purpose": "生成办公、行业、科技等可复用场景图。", "asset_tag": "scene_base", "recommended": True, "default_task_type": "scene_generation", "default_control_mode": "none", "default_image_task_type": "scene_generation", "default_width": 1080, "default_height": 1920, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "04_character_turnaround", "name": "04 角色三视图", "type": "image", "stage": "image_control", "purpose": "基于角色参考图生成正面/侧面/背面三视图。", "asset_tag": "character_turnaround", "recommended": True, "default_task_type": "character_turnaround", "default_control_mode": "character_reference", "default_image_task_type": "character_turnaround", "default_width": 1080, "default_height": 1920, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "05_product_turnaround", "name": "05 产品三视图", "type": "image", "stage": "image_control", "purpose": "基于产品参考图生成正面/侧面/背面三视图。", "asset_tag": "product_turnaround", "recommended": True, "default_task_type": "product_turnaround", "default_control_mode": "product_reference", "default_image_task_type": "product_turnaround", "default_width": 1080, "default_height": 1920, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "06_style_reference", "name": "06 风格参考图", "type": "image", "stage": "image_base", "purpose": "生成统一色彩、光线和画面气质的风格基准图。", "asset_tag": "style_reference", "recommended": True, "default_task_type": "style_reference", "default_control_mode": "none", "default_image_task_type": "style_reference", "default_width": 1080, "default_height": 1920, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "07_keyframe", "name": "07 关键帧生成", "type": "image", "stage": "image_keyframe", "purpose": "根据分镜文本生成视频首帧关键帧。", "asset_tag": "keyframe", "recommended": True, "default_task_type": "keyframe", "default_control_mode": "none", "default_image_task_type": "keyframe", "default_width": 1080, "default_height": 1920, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "08_cover_key_visual", "name": "08 封面关键视觉", "type": "image", "stage": "image_packaging", "purpose": "生成封面主视觉和标题安全区画面。", "asset_tag": "cover_key_visual", "recommended": True, "default_task_type": "cover_key_visual", "default_control_mode": "style_reference", "default_image_task_type": "cover_key_visual", "default_width": 1080, "default_height": 1920, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "09_image_inpaint_fix", "name": "09 局部修复 / 重绘", "type": "image", "stage": "image_repair", "purpose": "修脸、修手、去水印、局部替换。", "asset_tag": "image_inpaint_fix", "recommended": True, "default_task_type": "inpaint_fix", "default_control_mode": "mask_inpaint", "default_image_task_type": "inpaint_fix", "default_width": 1080, "default_height": 1920, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "10_background_remove", "name": "10 抠图 / 透明素材", "type": "image", "stage": "image_post", "purpose": "生成可叠加的人物、产品、图标透明素材。", "asset_tag": "background_remove", "recommended": False, "default_task_type": "background_remove", "default_control_mode": "matting", "default_image_task_type": "background_remove", "default_width": 1080, "default_height": 1920, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "11_i2v_first_frame", "name": "11 图生视频：首帧", "type": "video", "stage": "video_production", "purpose": "用单张首帧生成 3-8 秒视频片段。", "asset_tag": "i2v_first_frame", "recommended": True, "default_task_type": "img2video", "default_control_mode": "first_frame", "default_width": 960, "default_height": 544, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "12_i2v_first_last_frame", "name": "12 图生视频：首尾帧", "type": "video", "stage": "video_production", "purpose": "用首帧和尾帧控制转场与镜头运动。", "asset_tag": "i2v_first_last_frame", "recommended": True, "default_task_type": "first_last_frame_video", "default_control_mode": "first_last_frame", "default_width": 960, "default_height": 544, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "13_live_to_anime", "name": "13 真人转动漫风格", "type": "video", "stage": "video_style", "purpose": "把真人参考转成动漫或风格化视频。", "asset_tag": "live_to_anime", "recommended": False, "default_task_type": "live_to_anime_video", "default_control_mode": "style_transfer", "default_width": 960, "default_height": 544, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "14_motion_transfer", "name": "14 动作迁移", "type": "video", "stage": "video_control", "purpose": "用动作或姿态参考迁移到目标人物。", "asset_tag": "motion_transfer", "recommended": False, "default_task_type": "motion_transfer_video", "default_control_mode": "motion_reference", "default_width": 960, "default_height": 544, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "15_talking_image", "name": "15 图片说话 / 口型同步", "type": "video", "stage": "video_talking", "purpose": "让单张人物图按音频或文本口型说话。", "asset_tag": "talking_image", "recommended": True, "default_task_type": "talking_image_video", "default_control_mode": "audio_lipsync", "default_width": 960, "default_height": 544, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "16_broll_scene_video", "name": "16 B-roll / 场景视频", "type": "video", "stage": "video_broll", "purpose": "生成办公、行业、产品和抽象概念补充视频。", "asset_tag": "broll_scene_video", "recommended": True, "default_task_type": "txt2video", "default_control_mode": "none", "default_width": 960, "default_height": 544, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "17_empty_transition_video", "name": "17 空镜 / 转场视频", "type": "video", "stage": "video_transition", "purpose": "生成空镜、氛围镜头和章节转场。", "asset_tag": "empty_transition_video", "recommended": False, "default_task_type": "transition_video", "default_control_mode": "none", "default_width": 960, "default_height": 544, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "18_video_upscale", "name": "18 视频放大 / 清晰化", "type": "video", "stage": "video_post", "purpose": "对已生成视频做 2x/4x 放大、去噪和锐化。", "asset_tag": "video_upscale", "recommended": True, "default_task_type": "video_upscale", "default_control_mode": "upscale", "default_width": 1920, "default_height": 1080, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "19_frame_interpolation", "name": "19 视频补帧", "type": "video", "stage": "video_post", "purpose": "把低帧率视频补到 24/30/60fps。", "asset_tag": "frame_interpolation", "recommended": True, "default_task_type": "frame_interpolation", "default_control_mode": "interpolate", "default_width": 1920, "default_height": 1080, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "20_video_deflicker_stabilize", "name": "20 视频去闪烁 / 稳定", "type": "video", "stage": "video_post", "purpose": "降低视频闪烁、抖动和画面不稳定。", "asset_tag": "video_deflicker_stabilize", "recommended": False, "default_task_type": "video_deflicker_stabilize", "default_control_mode": "stabilize", "default_width": 1920, "default_height": 1080, "default_endpoint": "", "default_node_info": "[]"},
    {"id": "21_video_inpaint_fix", "name": "21 视频局部修复", "type": "video", "stage": "video_repair", "purpose": "对视频局部区域做修复、遮罩重绘或瑕疵处理。", "asset_tag": "video_inpaint_fix", "recommended": False, "default_task_type": "video_inpaint_fix", "default_control_mode": "video_mask_inpaint", "default_width": 1920, "default_height": 1080, "default_endpoint": "", "default_node_info": "[]"},
]
COMFY_DEBUG_WORKFLOWS = [
    {"id": "01_base_asset_image", "name": "01 基础资产图：角色/产品/场景", "type": "image", "stage": "image_base", "purpose": "一个工作流生成角色、产品或场景基础图。", "asset_tag": "character_base", "recommended": True, "default_task_type": "character_generation", "default_control_mode": "none", "default_image_task_type": "character_generation", "default_width": 1920, "default_height": 1080, "default_endpoint": "", "default_node_info": "[]", "modes": [{"value": "character_base", "label": "角色基础图", "asset_tag": "character_base", "task_type": "character_generation", "control_mode": "none", "requires_reference": False}, {"value": "product_base", "label": "产品基础图", "asset_tag": "product_base", "task_type": "product_generation", "control_mode": "none", "requires_reference": False}, {"value": "scene_base", "label": "场景基础图", "asset_tag": "scene_base", "task_type": "scene_generation", "control_mode": "none", "requires_reference": False}]},
    {"id": "02_turnaround", "name": "02 三视图：角色/产品", "type": "image", "stage": "image_control", "purpose": "一个工作流生成角色或产品三视图。", "asset_tag": "character_turnaround", "recommended": True, "default_task_type": "character_turnaround", "default_control_mode": "character_reference", "default_image_task_type": "character_turnaround", "default_width": 1920, "default_height": 1080, "default_endpoint": "", "default_node_info": "[]", "modes": [{"value": "character_turnaround", "label": "角色三视图", "asset_tag": "character_turnaround", "task_type": "character_turnaround", "control_mode": "character_reference", "requires_reference": True}, {"value": "product_turnaround", "label": "产品三视图", "asset_tag": "product_turnaround", "task_type": "product_turnaround", "control_mode": "product_reference", "requires_reference": True}]},
    {"id": "03_style_cover_image", "name": "03 风格参考 / 封面关键视觉", "type": "image", "stage": "image_packaging", "purpose": "一个工作流生成风格参考图或封面关键视觉。", "asset_tag": "style_reference", "recommended": True, "default_task_type": "style_reference", "default_control_mode": "none", "default_image_task_type": "style_reference", "default_width": 1920, "default_height": 1080, "default_endpoint": "", "default_node_info": "[]", "modes": [{"value": "style_reference", "label": "风格参考图", "asset_tag": "style_reference", "task_type": "style_reference", "control_mode": "none", "requires_reference": False}, {"value": "cover_key_visual", "label": "封面关键视觉", "asset_tag": "cover_key_visual", "task_type": "cover_key_visual", "control_mode": "style_reference", "requires_reference": False}]},
    {"id": "04_keyframe", "name": "04 关键帧生成", "type": "image", "stage": "image_keyframe", "purpose": "根据分镜文本生成视频首帧关键帧。", "asset_tag": "keyframe", "recommended": True, "default_task_type": "keyframe", "default_control_mode": "none", "default_image_task_type": "keyframe", "default_width": 1920, "default_height": 1080, "default_endpoint": "", "default_node_info": "[]", "modes": [{"value": "keyframe", "label": "关键帧", "asset_tag": "keyframe", "task_type": "keyframe", "control_mode": "none", "requires_reference": False}]},
    {"id": "05_image_repair_cutout", "name": "05 图片修复 / 抠图", "type": "image", "stage": "image_post", "purpose": "一个工作流处理图片局部修复、重绘或抠图透明素材。", "asset_tag": "image_inpaint_fix", "recommended": True, "default_task_type": "inpaint_fix", "default_control_mode": "mask_inpaint", "default_image_task_type": "inpaint_fix", "default_width": 1920, "default_height": 1080, "default_endpoint": "", "default_node_info": "[]", "modes": [{"value": "image_inpaint_fix", "label": "局部修复/重绘", "asset_tag": "image_inpaint_fix", "task_type": "inpaint_fix", "control_mode": "mask_inpaint", "requires_reference": True}, {"value": "background_remove", "label": "抠图/透明素材", "asset_tag": "background_remove", "task_type": "background_remove", "control_mode": "matting", "requires_reference": True}]},
    {"id": "06_i2v_first_frame", "name": "06A 图生视频：首帧", "type": "video", "stage": "video_production", "purpose": "单独的首帧图生视频工作流：一张关键帧生成视频片段，默认生产模式。", "asset_tag": "i2v_first_frame", "recommended": True, "default_task_type": "img2video", "default_control_mode": "first_frame", "default_width": 1024, "default_height": 576, "default_endpoint": "", "default_node_info": "[]", "modes": [{"value": "i2v_first_frame", "label": "首帧图生视频", "asset_tag": "i2v_first_frame", "task_type": "img2video", "control_mode": "first_frame", "requires_reference": True}]},
    {"id": "06_i2v_first_last_frame", "name": "06B 图生视频：首尾帧", "type": "video", "stage": "video_production", "purpose": "单独的首尾帧图生视频工作流：首帧+尾帧控制A到B运动，仅用于特殊镜头。", "asset_tag": "i2v_first_last_frame", "recommended": True, "default_task_type": "first_last_frame_video", "default_control_mode": "first_last_frame", "default_width": 1024, "default_height": 576, "default_duration": 4, "default_fps": 4, "default_endpoint": "", "default_node_info": "[]", "modes": [{"value": "i2v_first_last_frame", "label": "首尾帧图生视频", "asset_tag": "i2v_first_last_frame", "task_type": "first_last_frame_video", "control_mode": "first_last_frame", "requires_reference": True}]},
    {"id": "06_i2v_first_middle_last_frame", "name": "06C 图生视频：首中尾帧（实验）", "type": "video", "stage": "video_production", "purpose": "独立首中尾帧实验工作流：首帧+中帧+尾帧控制同一镜头，不替换 06B。", "asset_tag": "i2v_first_last_frame", "recommended": False, "default_task_type": "first_middle_last_frame_video", "default_control_mode": "first_middle_last_frame", "default_width": 1024, "default_height": 576, "default_duration": 4, "default_fps": 4, "default_endpoint": "", "default_node_info": "[]", "modes": [{"value": "i2v_first_middle_last_frame", "label": "首中尾帧图生视频", "asset_tag": "i2v_first_last_frame", "task_type": "first_middle_last_frame_video", "control_mode": "first_middle_last_frame", "requires_reference": True}]},
    {"id": "07_live_to_anime", "name": "07 真人转动漫风格", "type": "video", "stage": "video_style", "purpose": "真人参考转动漫或风格化视频。", "asset_tag": "live_to_anime", "recommended": False, "default_task_type": "live_to_anime_video", "default_control_mode": "style_transfer", "default_width": 960, "default_height": 544, "default_endpoint": "", "default_node_info": "[]", "modes": [{"value": "live_to_anime", "label": "真人转动漫", "asset_tag": "live_to_anime", "task_type": "live_to_anime_video", "control_mode": "style_transfer", "requires_reference": True}]},
    {"id": "08_motion_transfer", "name": "08 动作迁移", "type": "video", "stage": "video_control", "purpose": "动作或姿态参考迁移到目标人物。", "asset_tag": "motion_transfer", "recommended": False, "default_task_type": "motion_transfer_video", "default_control_mode": "motion_reference", "default_width": 960, "default_height": 544, "default_endpoint": "", "default_node_info": "[]", "modes": [{"value": "motion_transfer", "label": "动作迁移", "asset_tag": "motion_transfer", "task_type": "motion_transfer_video", "control_mode": "motion_reference", "requires_reference": True}]},
    {"id": "09_talking_image", "name": "09 图片说话 / 口型同步", "type": "video", "stage": "video_talking", "purpose": "人物图按音频或口播文本说话。", "asset_tag": "talking_image", "recommended": True, "default_task_type": "talking_image_video", "default_control_mode": "audio_lipsync", "default_width": 960, "default_height": 544, "default_endpoint": "", "default_node_info": "[]", "modes": [{"value": "talking_image", "label": "图片说话/口型同步", "asset_tag": "talking_image", "task_type": "talking_image_video", "control_mode": "audio_lipsync", "requires_reference": True}]},
    {"id": "10_broll_transition_video", "name": "10 B-roll / 空镜 / 转场", "type": "video", "stage": "video_broll", "purpose": "一个工作流生成 B-roll、空镜和转场视频。", "asset_tag": "broll_scene_video", "recommended": True, "default_task_type": "txt2video", "default_control_mode": "none", "default_width": 960, "default_height": 544, "default_endpoint": "", "default_node_info": "[]", "modes": [{"value": "broll_scene_video", "label": "B-roll/场景视频", "asset_tag": "broll_scene_video", "task_type": "txt2video", "control_mode": "broll", "requires_reference": False}, {"value": "empty_transition_video", "label": "空镜/转场视频", "asset_tag": "empty_transition_video", "task_type": "transition_video", "control_mode": "transition", "requires_reference": False}]},
    {"id": "11_video_enhance", "name": "11 视频增强：放大/补帧/稳定", "type": "video", "stage": "video_post", "purpose": "一个工作流处理视频放大、补帧、去闪烁和稳定。", "asset_tag": "video_upscale", "recommended": True, "default_task_type": "video_upscale", "default_control_mode": "upscale", "default_width": 1920, "default_height": 1080, "default_endpoint": "", "default_node_info": "[]", "modes": [{"value": "video_upscale", "label": "视频放大/清晰化", "asset_tag": "video_upscale", "task_type": "video_upscale", "control_mode": "upscale", "requires_reference": True}, {"value": "frame_interpolation", "label": "视频补帧", "asset_tag": "frame_interpolation", "task_type": "frame_interpolation", "control_mode": "interpolate", "requires_reference": True}, {"value": "video_deflicker_stabilize", "label": "去闪烁/稳定", "asset_tag": "video_deflicker_stabilize", "task_type": "video_deflicker_stabilize", "control_mode": "stabilize", "requires_reference": True}]},
    {"id": "12_video_inpaint_fix", "name": "12 视频局部修复", "type": "video", "stage": "video_repair", "purpose": "视频局部区域修复、遮罩重绘或瑕疵处理。", "asset_tag": "video_inpaint_fix", "recommended": False, "default_task_type": "video_inpaint_fix", "default_control_mode": "video_mask_inpaint", "default_width": 1920, "default_height": 1080, "default_endpoint": "", "default_node_info": "[]", "modes": [{"value": "video_inpaint_fix", "label": "视频局部修复", "asset_tag": "video_inpaint_fix", "task_type": "video_inpaint_fix", "control_mode": "video_mask_inpaint", "requires_reference": True}]},
]

# Generation and pre-production use a low-cost 480p working canvas. Final
# delivery resolution is handled by the enhancement/editing stages (11/22).
for _workflow in COMFY_DEBUG_WORKFLOWS:
    if str(_workflow.get("id") or "").split("_", 1)[0] in {f"{index:02d}" for index in range(1, 11)}:
        _workflow["default_width"] = 848
        _workflow["default_height"] = 480
    if _workflow.get("id") == "06_i2v_first_middle_last_frame":
        _workflow["default_fps"] = 24
    for _mode in _workflow.get("modes") or []:
        _mode_value = str(_mode.get("value") or "")
        _required_inputs = []
        if _mode.get("requires_reference"):
            _required_inputs.append("input_base_image")
        if _mode_value == "i2v_first_middle_last_frame":
            _required_inputs.extend(["input_middle_frame", "input_last_frame"])
        elif _mode_value == "i2v_first_last_frame":
            _required_inputs.append("input_last_frame")
        if _mode_value in {"image_inpaint_fix", "video_inpaint_fix"}:
            _required_inputs.append("input_mask_image")
        if _mode_value == "talking_image":
            _required_inputs.append("input_audio_file")
        _mode["required_inputs"] = list(dict.fromkeys(_required_inputs))
        _mode["outputs"] = ["output_final_video" if _workflow.get("type") == "video" else "output_final_image"]
        if _mode_value == "background_remove":
            _mode["outputs"].append("output_mask_alpha")


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>自定义工作流管理台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --line: #e1e6ef;
      --line-strong: #ccd5e1;
      --text: #1b1f24;
      --muted: #667085;
      --accent: #0f766e;
      --accent-strong: #0b5f59;
      --accent-soft: #eefaf8;
      --danger: #b42318;
      --ok: #166534;
      --warn: #92400e;
      --shadow: 0 1px 2px rgba(16, 24, 40, .05);
      --shadow-soft: 0 8px 22px rgba(16, 24, 40, .06);
    }
    * { box-sizing: border-box; }
    * {
      scrollbar-width: thin;
      scrollbar-color: #cbd5e1 transparent;
    }
    ::-webkit-scrollbar {
      width: 9px;
      height: 9px;
    }
    ::-webkit-scrollbar-thumb {
      background: #cbd5e1;
      border-radius: 999px;
      border: 2px solid transparent;
      background-clip: content-box;
    }
    ::-webkit-scrollbar-track {
      background: transparent;
    }
    body {
      margin: 0;
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      letter-spacing: 0;
    }
    header {
      height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 20px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      box-shadow: 0 1px 2px rgba(16, 24, 40, .04);
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 18px;
      min-width: 0;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      white-space: nowrap;
    }
    .top-nav {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 4px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f6f8fb;
    }
    .nav-btn {
      min-height: 30px;
      padding: 5px 10px;
      border: 0;
      background: transparent;
      color: var(--muted);
      font-size: 13px;
    }
    .nav-btn.active {
      background: #fff;
      color: var(--accent);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      font-weight: 650;
    }
    main {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      min-height: calc(100vh - 56px);
    }
    body[data-view="run"] main,
    body[data-view="config"] main,
    body[data-view="staff"] main,
    body[data-view="workflow"] main,
    body[data-view="assets"] main,
    body[data-view="comfyDebug"] main,
    body[data-view="system"] main {
      grid-template-columns: 1fr;
    }
    aside {
      border-right: 1px solid var(--line);
      background: #fbfcfd;
      padding: 16px;
      overflow: auto;
    }
    section {
      padding: 18px;
      overflow: auto;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow-soft);
    }
    .view[hidden], aside[hidden] { display: none; }
    .stack {
      display: grid;
      gap: 14px;
      align-content: start;
    }
    #configSections {
      grid-auto-rows: max-content;
      align-content: start;
    }
    .row {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 600;
    }
    select, textarea, input, button {
      font: inherit;
      border-radius: 8px;
    }
    select, textarea, input {
      width: 100%;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      padding: 9px 10px;
      outline: none;
      transition: border-color .14s ease, box-shadow .14s ease, background .14s ease;
    }
    select:hover, textarea:hover, input:hover {
      border-color: var(--line-strong);
    }
    select:focus, textarea:focus, input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 118, 110, .10);
    }
    textarea {
      min-height: 180px;
      resize: vertical;
      line-height: 1.55;
    }
    button {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      padding: 9px 12px;
      cursor: pointer;
      min-height: 38px;
      transition: border-color .14s ease, background .14s ease, box-shadow .14s ease, color .14s ease;
    }
    button:hover:not(:disabled) {
      border-color: var(--line-strong);
      background: #f8fafc;
      box-shadow: var(--shadow);
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
      font-weight: 650;
    }
    button.primary:hover { background: var(--accent-strong); }
    button.primary.run-progress {
      --run-progress: 0%;
      min-width: 116px;
      color: #fff;
      border-color: var(--accent);
      background:
        linear-gradient(90deg, var(--accent-strong) 0 var(--run-progress), var(--accent) var(--run-progress) 100%);
      box-shadow: inset 0 0 0 1px rgba(255, 255, 255, .18), var(--shadow);
    }
    button.primary.run-progress:disabled {
      opacity: 1;
      cursor: progress;
      color: #fff;
      background:
        linear-gradient(90deg, var(--accent-strong) 0 var(--run-progress), var(--accent) var(--run-progress) 100%);
    }
    button.danger {
      color: var(--danger);
      border-color: #fecdca;
      background: #fff7f6;
      font-weight: 650;
    }
    button.danger:hover:not(:disabled) {
      color: #912018;
      border-color: #fda29b;
      background: #fef3f2;
    }
    button:disabled {
      opacity: .6;
      cursor: not-allowed;
      background: #f8fafc;
      color: #98a2b3;
    }
    .form {
      padding: 16px;
      display: grid;
      gap: 14px;
      align-content: start;
    }
    .run-form {
      min-height: calc(100vh - 120px);
      align-content: center;
      justify-items: center;
      gap: 16px;
      background: transparent;
      border: 0;
      box-shadow: none;
    }
    .run-composer {
      width: min(920px, 100%);
      border: 1px solid var(--line);
      border-radius: 16px;
      background: #fff;
      box-shadow: var(--shadow-soft);
      padding: 14px;
      display: grid;
      gap: 10px;
    }
    .run-composer textarea {
      min-height: 170px;
      border: 0;
      box-shadow: none;
      resize: vertical;
      padding: 8px;
      font-size: 15px;
      line-height: 1.6;
    }
    .run-composer textarea:focus,
    .run-composer textarea:hover {
      border: 0;
      box-shadow: none;
    }
    .run-composer.is-locked {
      border-color: #99f6e4;
      background: linear-gradient(180deg, #fff, #f0fdfa);
    }
    .run-composer.is-locked textarea {
      color: #667085;
      background: transparent;
    }
    .composer-actions {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      border-top: 1px solid var(--line);
      padding-top: 10px;
      flex-wrap: wrap;
    }
    #cancelRunBtn:disabled {
      display: none;
    }
    .run-section {
      border-bottom: 1px solid var(--line);
      padding: 0 0 16px;
      display: grid;
      gap: 14px;
      min-width: 0;
    }
    .run-section:last-of-type {
      border-bottom: 0;
      padding-bottom: 0;
    }
    .run-section-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 12px;
      flex-wrap: wrap;
      padding-bottom: 2px;
    }
    .run-section-head strong {
      font-size: 15px;
    }
    .run-primary-grid {
      display: grid;
      grid-template-columns: minmax(180px, .8fr) minmax(240px, 1fr) minmax(240px, 1fr);
      gap: 12px;
    }
    .run-model-grid {
      display: grid;
      grid-template-columns: minmax(160px, .65fr) minmax(280px, 1fr) minmax(160px, .65fr);
      gap: 12px;
    }
    .run-input textarea {
      min-height: 150px;
    }
    .run-actions {
      position: sticky;
      bottom: 0;
      z-index: 2;
      margin: 0 -16px -16px;
      padding: 12px 16px;
      border-top: 1px solid var(--line);
      background: rgba(255, 255, 255, .96);
      backdrop-filter: blur(6px);
    }
    .run-form .run-section,
    .run-form .run-actions {
      display: none;
    }
    .run-form .run-composer,
    .run-form .progress-box {
      display: grid;
    }
    .run-form .progress-box {
      width: min(920px, 100%);
    }
    .split {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) 140px minmax(220px, 1fr);
      gap: 12px;
    }
    details {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 0;
      overflow: hidden;
      box-shadow: var(--shadow);
      width: 100%;
      box-sizing: border-box;
      align-self: start;
    }
    summary {
      cursor: pointer;
      padding: 11px 12px;
      list-style: none;
      color: #334155;
      background: #fff;
      display: flex;
      align-items: center;
      gap: 6px;
      min-height: 44px;
      box-sizing: border-box;
      overflow: hidden;
    }
    summary::-webkit-details-marker {
      display: none;
    }
    summary::before {
      content: "▶";
      flex: 0 0 14px;
      width: 14px;
      color: #334155;
      font-size: 12px;
      line-height: 1;
      text-align: center;
    }
    details[open] > summary::before {
      content: "▼";
    }
    summary strong {
      flex: 0 0 auto;
      white-space: nowrap;
    }
    summary .muted {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    details[open] summary {
      border-bottom: 1px solid var(--line);
      background: #f8fafc;
    }
    [hidden] {
      display: none !important;
    }
    .details-body {
      padding: 14px 12px 12px;
      display: grid;
      gap: 14px;
    }
    details:not([open]) > .details-body {
      display: none;
    }
    .automation-config .details-body {
      gap: 16px;
      background: #fbfcfd;
    }
    .config-card {
      position: relative;
      display: grid;
      grid-template-columns: repeat(3, minmax(220px, 1fr));
      gap: 12px;
      align-items: start;
      padding: 42px 12px 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
      box-shadow: var(--shadow);
    }
    .config-card::before {
      content: attr(data-title);
      position: absolute;
      top: 12px;
      left: 12px;
      color: #0f172a;
      font-size: 14px;
      font-weight: 700;
    }
    .config-card::after {
      content: attr(data-desc);
      position: absolute;
      top: 14px;
      left: 120px;
      right: 12px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .config-card label {
      min-width: 0;
    }
    .config-card textarea {
      min-height: 160px;
    }
    .config-card.mapping-card {
      grid-template-columns: minmax(420px, 1.2fr) minmax(260px, .8fr) minmax(180px, .45fr);
    }
    .workflow-summary-card {
      margin: 0;
      border: 1px solid #99f6e4;
      border-left: 4px solid var(--accent);
      border-radius: 12px;
      background: #f0fdfa;
      padding: 10px;
    }
    .provider-grid {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) minmax(260px, 1fr) minmax(260px, 1fr);
      gap: 12px;
      align-items: start;
    }
    .automation-config .provider-grid.config-card {
      grid-template-columns: repeat(3, minmax(220px, 1fr));
    }
    .automation-config .provider-grid.config-card.mapping-card {
      grid-template-columns: minmax(420px, 1.2fr) minmax(260px, .8fr) minmax(180px, .45fr);
    }
    .comfy-mapping-grid textarea {
      min-height: 132px;
    }
    .comfy-mapping-grid input[type="file"],
    .comfy-mapping-grid select {
      min-height: 38px;
    }
    .video-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(160px, 1fr));
      gap: 12px;
    }
    .reference-list {
      display: grid;
      gap: 7px;
      margin-top: 10px;
    }
    .reference-item {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 11px;
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      background: #fbfcfd;
      font-size: 13px;
      min-width: 0;
    }
    .reference-item.active {
      border-color: var(--accent);
      background: var(--accent-soft);
      box-shadow: inset 2px 0 0 var(--accent);
    }
    .reference-item:hover {
      border-color: var(--line-strong);
      background: #fbfcfd;
    }
    .comfy-parameter-panel {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      display: grid;
      gap: 0;
      max-height: 320px;
      overflow: auto;
      box-shadow: var(--shadow-soft);
    }
    .comfy-parameter-head {
      position: sticky;
      top: 0;
      z-index: 1;
      background: #f8fafc;
      border-bottom: 1px solid var(--line);
      padding: 8px 10px;
    }
    .comfy-parameter-row {
      display: grid;
      grid-template-columns: minmax(220px, .8fr) minmax(180px, .7fr) minmax(240px, 1fr);
      gap: 10px;
      align-items: center;
      padding: 9px 10px;
      border-bottom: 1px solid var(--line);
      font-size: 13px;
      background: #fff;
    }
    .comfy-parameter-row:nth-child(even) {
      background: #fbfcfd;
    }
    .comfy-parameter-row:last-child {
      border-bottom: 0;
    }
    .comfy-parameter-row label {
      min-width: 0;
    }
    .comfy-parameter-left {
      display: grid;
      gap: 3px;
    }
    .comfy-parameter-row select,
    .comfy-parameter-row input {
      height: 34px;
      min-height: 34px;
    }
    .comfy-parameter-name,
    .comfy-parameter-value {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    @media (max-width: 900px) {
      .config-card,
      .config-card.mapping-card,
      .automation-config .provider-grid.config-card,
      .automation-config .provider-grid.config-card.mapping-card {
        grid-template-columns: 1fr;
        padding-top: 58px;
      }
      .config-card::after {
        top: 32px;
        left: 12px;
      }
      .comfy-parameter-row {
        grid-template-columns: 1fr;
      }
    }
    .inline-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .inline-actions button {
      width: auto;
    }
    .reference-preview {
      width: 64px;
      height: 64px;
      min-width: 64px;
      border-radius: 6px;
      border: 1px solid var(--line);
      object-fit: cover;
      background: #f2f4f7;
    }
    .reference-info {
      display: grid;
      gap: 4px;
      min-width: 0;
      flex: 1;
    }
    .reference-name {
      font-weight: 650;
      overflow-wrap: anywhere;
    }
    .list {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .item {
      text-align: left;
      padding: 10px;
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      width: 100%;
    }
    .item.active { border-color: var(--accent); box-shadow: inset 3px 0 0 var(--accent); }
    .item-main {
      display: grid;
      gap: 4px;
      min-width: 0;
      flex: 1;
    }
    .item-title { font-weight: 650; overflow-wrap: anywhere; }
    .item-meta { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    .icon-btn {
      width: 32px;
      min-width: 32px;
      height: 32px;
      min-height: 32px;
      padding: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 18px;
      line-height: 1;
    }
    .icon-btn.danger {
      color: var(--danger);
      border-color: #fecdca;
      background: #fffafa;
    }
    .icon-btn.danger:hover {
      background: #fef3f2;
      border-color: var(--danger);
    }
    .status {
      padding: 8px 10px;
      border-radius: 8px;
      border: 1px solid #d7ebe8;
      background: #f3fbfa;
      color: #134e4a;
      font-size: 13px;
    }
    .status.error { background: #fef3f2; color: var(--danger); }
    .toast {
      position: fixed;
      top: 16px;
      right: 16px;
      z-index: 9999;
      max-width: min(420px, calc(100vw - 32px));
      padding: 10px 12px;
      border: 1px solid #bae6fd;
      border-radius: 8px;
      background: #f0f9ff;
      color: #075985;
      box-shadow: 0 12px 28px rgba(15, 23, 42, 0.18);
      font-size: 13px;
      font-weight: 650;
      opacity: 0;
      transform: translateY(-8px);
      pointer-events: none;
      transition: opacity 0.16s ease, transform 0.16s ease;
    }
    .toast.show {
      opacity: 1;
      transform: translateY(0);
    }
    .toast.error {
      border-color: #fecaca;
      background: #fef2f2;
      color: #991b1b;
    }
    .progress-box {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
      display: grid;
      gap: 10px;
      box-shadow: var(--shadow);
    }
    .progress-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      font-size: 13px;
      color: var(--muted);
    }
    .progress-bar {
      height: 7px;
      border-radius: 999px;
      background: #edf1f7;
      overflow: hidden;
    }
    .progress-fill {
      height: 100%;
      width: 0%;
      background: var(--accent);
      transition: width .2s ease;
    }
    .progress-list {
      display: grid;
      gap: 8px;
      margin-top: 2px;
    }
    .progress-step-wrap {
      display: grid;
      gap: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      overflow: hidden;
      box-shadow: var(--shadow);
    }
    .progress-step-wrap > summary {
      list-style: none;
      cursor: default;
      background: #fff;
      min-height: auto;
    }
    .progress-step-wrap > summary::-webkit-details-marker {
      display: none;
    }
    .progress-step-wrap.has-details > summary {
      cursor: pointer;
    }
    .progress-step {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
      border: 0;
      border-radius: 0;
      padding: 10px 12px;
      background: transparent;
      font-size: 13px;
      line-height: 1.35;
    }
    .progress-step span:first-child {
      min-width: 0;
    }
    .progress-step.active { box-shadow: inset 3px 0 0 var(--accent); background: #fbfefe; }
    .progress-step.done { color: #166534; background: #f6fef9; }
    .progress-step.error { color: var(--danger); background: #fff7f6; }
    .progress-step-main {
      display: flex;
      align-items: flex-start;
      gap: 8px;
      min-width: 0;
      flex: 1 1 auto;
    }
    .progress-step-toggle {
      display: inline-flex;
      width: 18px;
      height: 18px;
      flex: 0 0 18px;
      color: var(--muted);
      font-size: 12px;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      background: #f1f5f9;
    }
    .progress-step-wrap[open] .progress-step-toggle {
      background: #dff5f1;
      color: var(--accent);
    }
    .progress-step-title {
      overflow: visible;
      text-overflow: clip;
      white-space: normal;
      word-break: break-word;
      overflow-wrap: anywhere;
      font-weight: 650;
    }
    .progress-step-status {
      text-align: right;
      flex: 0 0 auto;
      padding-top: 1px;
    }
    .progress-detail-list {
      display: grid;
      gap: 0;
      margin: 0;
      padding: 8px 12px 10px 40px;
      border-top: 1px solid var(--line);
      background: #fbfcfd;
    }
    .progress-detail-item {
      position: relative;
      border: 0;
      border-left: 1px solid var(--line);
      border-radius: 0;
      padding: 7px 0 8px 14px;
      background: transparent;
      font-size: 12px;
      color: var(--text);
    }
    .progress-detail-item::before {
      content: "";
      position: absolute;
      left: -4px;
      top: 13px;
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: var(--line-strong);
    }
    .progress-detail-item.done {
      color: #166534;
    }
    .progress-detail-item.done::before { background: var(--ok); }
    .progress-detail-item.error {
      color: var(--danger);
    }
    .progress-detail-item.error::before { background: var(--danger); }
    .progress-detail-main {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: baseline;
    }
    .progress-detail-meta {
      margin-top: 4px;
      color: var(--muted);
      word-break: break-all;
      line-height: 1.45;
    }
    .viewer {
      display: grid;
      grid-template-rows: auto auto minmax(320px, 1fr);
      min-height: 520px;
    }
    .viewer-head {
      padding: 12px;
      border-bottom: 1px solid var(--line);
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
    }
    .file-tabs {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .file-tabs button {
      min-height: 30px;
      padding: 5px 9px;
      font-size: 12px;
      border-radius: 999px;
      background: #f8fafc;
    }
    .file-tabs button.active {
      border-color: var(--accent);
      color: var(--accent);
      font-weight: 650;
      background: var(--accent-soft);
    }
    .inline-check {
      min-height: 34px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 0 8px;
      color: var(--muted);
      font-size: 13px;
    }
    .inline-check input {
      width: auto;
      margin: 0;
    }
    .output-dashboard {
      display: grid;
      gap: 12px;
      padding: 14px;
      border-bottom: 1px solid var(--line);
      background: #f8fafc;
    }
    .video-preview {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
      display: grid;
      gap: 8px;
      box-shadow: var(--shadow);
    }
    .video-preview[hidden] {
      display: none;
    }
    .video-preview video {
      width: 100%;
      max-height: 520px;
      background: #000;
      border-radius: 6px;
    }
    .output-summary-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(140px, 1fr));
      gap: 10px;
    }
    .output-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: linear-gradient(180deg, #fff, #fbfcfd);
      padding: 11px 12px;
      display: grid;
      gap: 5px;
      min-width: 0;
      box-shadow: var(--shadow-soft);
    }
    .output-card .label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .output-card .value {
      font-size: 14px;
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .output-sections {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 12px;
      min-width: 0;
    }
    .output-section {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
      display: grid;
      gap: 8px;
      min-width: 0;
      box-shadow: var(--shadow-soft);
    }
    .output-section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .output-section-head .inline-actions {
      justify-content: flex-end;
      flex-wrap: wrap;
    }
    .output-link-list {
      display: grid;
      gap: 7px;
      max-height: 180px;
      overflow: auto;
    }
    .output-link {
      display: grid;
      gap: 2px;
      text-align: left;
      min-height: 54px;
      padding: 9px 10px;
      border-radius: 8px;
      background: #fbfcfd;
      align-content: center;
      min-width: 0;
      overflow: hidden;
      box-shadow: none;
    }
    .output-link.active {
      border-color: var(--accent);
      color: var(--accent);
      background: var(--accent-soft);
      box-shadow: inset 2px 0 0 var(--accent);
    }
    .output-link:hover {
      border-color: var(--line-strong);
      background: #f8fafc;
    }
    .output-link span {
      overflow: visible;
      text-overflow: clip;
      white-space: normal;
      overflow-wrap: anywhere;
    }
    .output-link .output-link-title {
      font-weight: 650;
      line-height: 1.3;
    }
    .output-link .output-link-subtitle {
      display: block;
      line-height: 1.25;
    }
    .task-comfy-debug-list {
      display: grid;
      grid-template-columns: 1fr;
      align-content: start;
      max-height: 360px;
      min-height: 0;
      gap: 12px;
      padding-right: 4px;
      overflow-y: auto;
      overflow-x: hidden;
    }
    .task-comfy-debug-item {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
      min-height: 84px;
      padding: 14px 16px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      overflow: visible;
      box-sizing: border-box;
    }
    .task-comfy-debug-item.active {
      border-color: var(--accent);
      background: var(--accent-soft);
      box-shadow: inset 3px 0 0 var(--accent);
    }
    .task-comfy-debug-main {
      display: grid;
      gap: 6px;
      min-width: 0;
      flex: 1 1 auto;
    }
    .task-comfy-debug-title {
      font-weight: 700;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .task-comfy-debug-progress {
      width: 100%;
      height: 7px;
      border-radius: 999px;
      background: #e5edf4;
      overflow: hidden;
    }
    .task-comfy-debug-progress span {
      display: block;
      height: 100%;
      width: var(--task-comfy-debug-progress, 0%);
      background: linear-gradient(90deg, var(--accent), #20b486);
      border-radius: inherit;
      transition: width .25s ease;
    }
    .task-comfy-debug-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
      align-items: flex-start;
      flex: 0 0 auto;
      min-width: 168px;
    }
    .task-comfy-debug-actions button {
      white-space: nowrap;
    }
    .task-comfy-debug-item.is-approved {
      opacity: .72;
    }
    @media (max-width: 760px) {
      .task-comfy-debug-item {
        flex-direction: column;
      }
      .task-comfy-debug-actions {
        justify-content: flex-start;
        min-width: 0;
      }
    }
    .asset-thumb {
      width: 100%;
      max-height: 120px;
      object-fit: cover;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: #f8fafc;
      margin-bottom: 4px;
    }
    .asset-gallery {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
      gap: 10px;
      max-height: 360px;
      overflow: auto;
      padding: 2px;
      align-items: start;
    }
    .asset-card {
      position: relative;
      display: grid;
      gap: 7px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
      padding: 7px;
      min-width: 0;
      cursor: pointer;
      text-align: left;
      overflow: hidden;
      box-shadow: var(--shadow-soft);
      transition: transform .14s ease, box-shadow .14s ease, border-color .14s ease;
    }
    .asset-card:hover {
      transform: translateY(-1px);
      border-color: var(--accent);
      box-shadow: 0 12px 24px rgba(15, 23, 42, .12);
    }
    .asset-card.is-favorited {
      border-color: rgba(20, 184, 166, .72);
      background: linear-gradient(180deg, #f0fdfa, #fff);
    }
    .asset-card-media {
      width: 100%;
      aspect-ratio: 16 / 10;
      border-radius: 9px;
      overflow: hidden;
      background: linear-gradient(135deg, #e2e8f0, #f8fafc);
      display: grid;
      place-items: center;
      color: var(--muted);
      font-size: 12px;
    }
    .asset-card-media img,
    .asset-card-media video,
    .asset-card-media audio {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .asset-card-kind {
      position: absolute;
      top: 12px;
      left: 12px;
      border-radius: 999px;
      background: rgba(15, 23, 42, .72);
      color: #fff;
      padding: 3px 7px;
      font-size: 11px;
      font-weight: 700;
      backdrop-filter: blur(8px);
    }
    .asset-card-badge {
      position: absolute;
      right: 10px;
      top: 10px;
      z-index: 4;
      padding: 4px 8px;
      border-radius: 999px;
      background: rgba(15, 118, 110, .9);
      color: #fff;
      font-size: 11px;
      font-weight: 800;
      box-shadow: 0 8px 18px rgba(15, 23, 42, .16);
      backdrop-filter: blur(8px);
    }
    .asset-card-title {
      overflow: visible;
      text-overflow: clip;
      white-space: normal;
      overflow-wrap: anywhere;
      font-size: 12px;
      font-weight: 700;
      color: #0f172a;
    }
    .asset-card-subtitle {
      overflow: visible;
      text-overflow: clip;
      white-space: normal;
      overflow-wrap: anywhere;
      color: var(--muted);
      font-size: 11px;
    }
    .asset-tag-row,
    .asset-meta-editor {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      align-items: center;
      min-width: 0;
    }
    .asset-chip {
      border-radius: 999px;
      background: #ecfeff;
      color: #0f766e;
      border: 1px solid rgba(20, 184, 166, .28);
      padding: 2px 7px;
      font-size: 11px;
      font-weight: 700;
      line-height: 1.4;
    }
    .asset-meta-editor {
      margin-top: 2px;
      padding-top: 6px;
      border-top: 1px dashed var(--line);
    }
    .asset-meta-editor select,
    .asset-meta-editor input {
      min-width: 0;
      font-size: 12px;
      padding: 6px 8px;
    }
    .asset-meta-editor select {
      flex: 0 0 96px;
    }
    .asset-meta-editor input {
      flex: 1 1 120px;
    }
    .asset-meta-editor button {
      padding: 6px 9px;
      white-space: nowrap;
    }
    .asset-meta-editor .asset-delete-btn {
      margin-left: auto;
      border-color: rgba(239, 68, 68, .35);
      color: #b91c1c;
      background: #fff7f7;
    }
    .asset-meta-editor .asset-delete-btn:hover {
      border-color: rgba(220, 38, 38, .65);
      background: #fee2e2;
    }
    .asset-library-shell {
      display: grid;
      grid-template-columns: minmax(0, 1fr);
      gap: 16px;
      min-height: 640px;
    }
    .asset-library-main {
      min-width: 0;
      display: grid;
      gap: 16px;
      align-content: start;
    }
    .asset-library-hero {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 14px;
      padding: 4px 2px 0;
    }
    .asset-library-title {
      margin: 0;
      font-size: 34px;
      line-height: 1.1;
      letter-spacing: 0;
      color: #0f172a;
    }
    .asset-library-tabs {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      width: fit-content;
      padding: 4px;
      border-radius: 999px;
      background: #f1f5f9;
      border: 1px solid var(--line);
    }
    .asset-library-tab {
      border: 0;
      border-radius: 999px;
      background: transparent;
      color: #334155;
      padding: 7px 18px;
      min-width: 74px;
      font-weight: 750;
      box-shadow: none;
    }
    .asset-library-tab.active {
      color: #0f172a;
      background: #fff;
      box-shadow: 0 8px 20px rgba(15, 23, 42, .08);
    }
    .asset-library-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(164px, 196px));
      gap: 18px;
      align-items: start;
      min-height: 360px;
    }
    .asset-library-card {
      position: relative;
      display: grid;
      gap: 10px;
      padding: 12px;
      min-width: 0;
      min-height: 266px;
      border: 1px solid transparent;
      border-radius: 20px;
      background: #f8fafc;
      text-align: left;
      cursor: pointer;
      transition: border-color .14s ease, box-shadow .14s ease, transform .14s ease, background .14s ease;
    }
    .asset-library-card:hover,
    .asset-library-card.active {
      transform: translateY(-1px);
      border-color: rgba(20, 184, 166, .55);
      background: #fff;
      box-shadow: 0 16px 34px rgba(15, 23, 42, .10);
    }
    .asset-library-media {
      width: 100%;
      aspect-ratio: 9 / 14;
      border-radius: 18px;
      overflow: hidden;
      background: #e5e7eb;
      display: grid;
      place-items: center;
    }
    .asset-library-media img,
    .asset-library-media video {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }
    .asset-library-add {
      background: #f8fafc;
    }
    .asset-library-add .asset-library-media {
      background: #e7e7e8;
      color: #111827;
    }
    .asset-library-plus {
      width: 38px;
      height: 38px;
      border-radius: 999px;
      display: grid;
      place-items: center;
      background: #111827;
      color: #fff;
      font-size: 28px;
      line-height: 1;
      font-weight: 800;
    }
    .asset-library-card-title {
      display: block;
      color: #0f172a;
      font-size: 14px;
      font-weight: 760;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .asset-library-card-meta {
      display: block;
      color: #64748b;
      font-size: 12px;
      line-height: 1.35;
      overflow-wrap: anywhere;
    }
    .asset-library-card-actions {
      position: absolute;
      right: 18px;
      top: 18px;
      display: flex;
      gap: 6px;
      opacity: 0;
      transform: translateY(-2px);
      transition: opacity .14s ease, transform .14s ease;
    }
    .asset-library-card:hover .asset-library-card-actions,
    .asset-library-card.active .asset-library-card-actions {
      opacity: 1;
      transform: translateY(0);
    }
    .asset-library-card-actions button {
      width: 30px;
      height: 30px;
      padding: 0;
      border-radius: 999px;
      background: rgba(255, 255, 255, .92);
      box-shadow: 0 8px 18px rgba(15, 23, 42, .14);
    }
    .asset-library-detail {
      position: fixed;
      right: 18px;
      top: 72px;
      bottom: 18px;
      z-index: 9700;
      width: min(420px, calc(100vw - 36px));
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #fff;
      padding: 16px;
      box-shadow: 0 24px 60px rgba(15, 23, 42, .22);
      min-width: 0;
      box-sizing: border-box;
    }
    .asset-library-detail[hidden] {
      display: none;
    }
    .asset-detail-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 10px;
    }
    .asset-detail-head strong {
      font-size: 16px;
      color: #0f172a;
    }
    .asset-detail-close-btn {
      width: 36px;
      min-width: 36px;
      height: 36px;
      min-height: 36px;
      padding: 0;
      border-radius: 999px;
      border: 1px solid rgba(148, 163, 184, .28);
      background: rgba(248, 250, 252, .92);
      color: #475569;
      font-size: 18px;
      line-height: 1;
      font-weight: 500;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      box-shadow: none;
    }
    .asset-detail-close-btn:hover:not(:disabled) {
      border-color: rgba(148, 163, 184, .5);
      background: #fff;
      color: #0f172a;
      box-shadow: 0 10px 22px rgba(15, 23, 42, .08);
      transform: translateY(-1px);
    }
    .asset-detail-close-btn:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 118, 110, .10);
      outline: none;
    }
    .asset-detail-preview {
      width: 100%;
      aspect-ratio: 16 / 9;
      min-height: 0;
      border-radius: 12px;
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 12px;
      box-sizing: border-box;
    }
    .asset-detail-preview img,
    .asset-detail-preview video {
      width: auto;
      height: auto;
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      object-position: center center;
      display: block;
    }
    .asset-detail-media.fit-height {
      width: auto;
      height: 100%;
    }
    .asset-detail-media.fit-width {
      width: 100%;
      height: auto;
    }
    .asset-detail-form {
      display: grid;
      gap: 10px;
    }
    .asset-detail-form label {
      display: grid;
      gap: 5px;
      font-size: 12px;
      color: #475569;
    }
    .asset-detail-form textarea {
      min-height: 84px;
      resize: vertical;
    }
    .asset-detail-actions,
    .asset-import-actions {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 8px;
    }
    .asset-import-modal {
      position: fixed;
      inset: 0;
      z-index: 9800;
      display: grid;
      place-items: center;
      padding: 20px;
      background: rgba(15, 23, 42, .42);
    }
    .asset-import-modal[hidden] {
      display: none;
    }
    .asset-import-card {
      width: min(560px, 100%);
      border-radius: 16px;
      background: #fff;
      border: 1px solid var(--line);
      box-shadow: 0 24px 60px rgba(15, 23, 42, .22);
      padding: 18px;
      display: grid;
      gap: 14px;
    }
    .asset-import-card h3 {
      margin: 0;
      font-size: 18px;
    }
    .asset-import-grid {
      display: grid;
      gap: 10px;
    }
    .asset-import-grid label {
      display: grid;
      gap: 6px;
      color: #475569;
      font-size: 12px;
    }
    .asset-import-grid textarea {
      min-height: 76px;
      resize: vertical;
    }
    @media (max-width: 980px) {
      .asset-library-shell {
        grid-template-columns: 1fr;
      }
    }
    @media (max-width: 640px) {
      .asset-library-hero {
        align-items: flex-start;
        flex-direction: column;
      }
      .asset-library-title {
        font-size: 28px;
      }
      .asset-library-tabs {
        width: 100%;
        overflow: auto;
      }
      .asset-library-tab {
        min-width: auto;
        padding: 7px 13px;
      }
      .asset-library-grid {
        grid-template-columns: repeat(auto-fill, minmax(142px, 1fr));
      }
      .asset-library-detail {
        top: auto;
        left: 0;
        right: 0;
        bottom: 0;
        width: 100%;
        max-height: min(82vh, 680px);
        border-radius: 16px 16px 0 0;
      }
      .asset-detail-preview {
        aspect-ratio: 16 / 9;
        min-height: 0;
      }
    }
    .asset-lightbox {
      position: fixed;
      inset: 0;
      z-index: 10000;
      display: grid;
      grid-template-rows: minmax(44px, auto) minmax(0, 1fr) minmax(24px, auto);
      background: rgba(2, 6, 23, .9);
      color: #fff;
      padding: 18px;
      gap: 12px;
    }
    .asset-lightbox[hidden] {
      display: none;
    }
    .asset-lightbox-head,
    .asset-lightbox-foot {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-width: 0;
      position: relative;
      z-index: 2;
    }
    .asset-lightbox-title {
      display: grid;
      gap: 3px;
      min-width: 0;
    }
    .asset-lightbox-title strong,
    .asset-lightbox-title span {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .asset-lightbox-body {
      min-width: 0;
      min-height: 0;
      height: auto;
      display: grid;
      grid-template-columns: 54px minmax(0, 1fr) 54px;
      align-items: center;
      gap: 14px;
      overflow: hidden;
    }
    .asset-lightbox-stage {
      min-width: 0;
      min-height: 0;
      height: 100%;
      width: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 16px;
      background: rgba(15, 23, 42, .6);
      overflow: hidden;
      padding: 10px;
      box-sizing: border-box;
    }
    .asset-lightbox-stage img,
    .asset-lightbox-stage video {
      width: auto;
      height: auto;
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
      object-position: center center;
      display: block;
      border-radius: 12px;
      background: #000;
      box-shadow: 0 24px 60px rgba(0, 0, 0, .35);
    }
    .asset-lightbox-media {
      width: auto !important;
      height: auto !important;
      max-width: 100% !important;
      max-height: 100% !important;
      object-fit: contain !important;
      object-position: center center !important;
      flex: 0 1 auto;
    }
    .asset-lightbox-media.fit-height {
      width: auto !important;
      height: 100% !important;
    }
    .asset-lightbox-media.fit-width {
      width: 100% !important;
      height: auto !important;
    }
    .asset-lightbox button {
      border-color: rgba(255, 255, 255, .26);
      background: rgba(255, 255, 255, .1);
      color: #fff;
      position: relative;
      z-index: 3;
    }
    .asset-lightbox-nav {
      width: 48px;
      height: 56px;
      border-radius: 999px;
      font-size: 26px;
    }
    .step-confirm-bar {
      margin: 0 14px 12px;
      border: 1px solid #99f6e4;
      border-radius: 10px;
      background: linear-gradient(180deg, #f0fdfa, #fff);
      padding: 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      box-shadow: var(--shadow-soft);
    }
    .step-confirm-bar[hidden] {
      display: none;
    }
    .step-confirm-copy {
      display: grid;
      gap: 4px;
      min-width: 0;
    }
    .step-confirm-title {
      font-weight: 750;
      color: var(--accent-strong);
    }
    .step-confirm-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .staff-manager {
      display: grid;
      grid-template-columns: minmax(300px, 360px) minmax(0, 1fr);
      gap: 14px;
      align-items: start;
      width: 100%;
      min-width: 0;
      overflow: hidden;
    }
    .manager-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      padding-bottom: 2px;
    }
    .manager-title {
      display: grid;
      gap: 3px;
    }
    .manager-title strong {
      font-size: 16px;
    }
    .manager-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .staff-sidebar {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
      display: grid;
      gap: 8px;
      min-width: 0;
      box-shadow: var(--shadow-soft);
    }
    .staff-list {
      display: grid;
      gap: 8px;
      align-content: start;
      max-height: calc(100vh - 250px);
      overflow: auto;
      padding-right: 4px;
    }
    .staff-card {
      text-align: left;
      padding: 12px 12px;
      border: 1px solid var(--line);
      background: #fbfcfd;
      border-radius: 8px;
      display: grid;
      grid-template-rows: auto auto auto;
      gap: 6px;
      min-width: 0;
      min-height: 86px;
      height: auto;
      align-content: start;
      overflow: visible;
      box-shadow: none;
      transition: border-color .14s ease, background .14s ease, box-shadow .14s ease, transform .14s ease;
    }
    .staff-card strong {
      display: block;
      min-width: 0;
      font-size: 15px;
      line-height: 1.35;
      overflow: visible;
      text-overflow: clip;
      white-space: normal;
      word-break: break-word;
      overflow-wrap: anywhere;
    }
    .staff-card .staff-meta {
      display: block;
      min-width: 0;
      line-height: 1.35;
      overflow: visible;
      text-overflow: clip;
      white-space: normal;
      overflow-wrap: anywhere;
    }
    .staff-card .staff-role {
      display: inline-block;
      justify-self: start;
      max-width: 100%;
      margin-top: 2px;
      padding: 2px 6px;
      border-radius: 6px;
      background: #e6f4f1;
      color: var(--accent);
      line-height: 1.35;
      overflow: visible;
      text-overflow: clip;
      white-space: normal;
      overflow-wrap: anywhere;
    }
    .staff-card.active {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(15, 118, 110, .10);
      background: var(--accent-soft);
      transform: translateY(-1px);
    }
    .staff-card:hover {
      border-color: var(--line-strong);
      background: #fbfcfd;
    }
    .staff-editor {
      display: grid;
      gap: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
      min-width: 0;
      max-width: 100%;
      overflow: hidden;
      box-shadow: var(--shadow-soft);
    }
    .staff-editor label {
      min-width: 0;
    }
    .staff-editor input,
    .staff-editor textarea {
      min-width: 0;
      max-width: 100%;
    }
    .staff-editor textarea {
      min-height: 220px;
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 13px;
      overflow: auto;
      white-space: pre;
    }
    .workflow-step {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 10px;
      display: grid;
      gap: 10px;
      box-shadow: var(--shadow-soft);
    }
    .workflow-step-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }
    .workflow-step-grid {
      display: grid;
      grid-template-columns: minmax(180px, 240px) minmax(220px, 1fr) minmax(220px, 1fr);
      gap: 10px;
    }
    .comfy-debug-layout {
      display: grid;
      grid-template-columns: minmax(260px, .72fr) minmax(0, 1.4fr);
      gap: 14px;
      align-items: start;
    }
    .comfy-debug-sidebar,
    .comfy-debug-main {
      display: grid;
      gap: 12px;
      min-width: 0;
    }
    .comfy-debug-list {
      display: grid;
      gap: 8px;
      align-content: start;
      grid-auto-rows: max-content;
      max-height: calc(100vh - 260px);
      overflow: auto;
      padding-right: 4px;
    }
    .comfy-debug-tree-group {
      display: grid;
      gap: 6px;
      padding: 7px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #f8fafc;
    }
    .comfy-debug-tree-group-title {
      width: 100%;
      border: 0;
      background: transparent;
      padding: 2px 4px;
      color: #334155;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .04em;
      text-align: left;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .comfy-debug-tree-group-title:hover { color: var(--accent); }
    .comfy-debug-tree-group-toggle { font-size: 11px; transition: transform .15s ease; }
    .comfy-debug-tree-group.collapsed .comfy-debug-tree-group-toggle { transform: rotate(-90deg); }
    .comfy-debug-tree-children { display: grid; gap: 6px; }
    .comfy-debug-tree-leaf { background: #fff; }
    button.comfy-debug-tree-leaf {
      width: 100%;
      color: inherit;
      font: inherit;
      text-align: left;
    }
    .comfy-debug-card {
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #fff;
      padding: 10px;
      display: grid;
      gap: 6px;
      align-content: start;
      min-height: 0;
      cursor: pointer;
    }
    .comfy-debug-card.active {
      border-color: var(--accent);
      background: #f0fdfa;
      box-shadow: inset 3px 0 0 var(--accent);
    }
    .comfy-debug-card.editing {
      outline: 2px solid rgba(15, 118, 110, .25);
      outline-offset: 2px;
    }
    .comfy-debug-card strong,
    .comfy-debug-card span {
      white-space: normal;
      overflow-wrap: anywhere;
    }
    .comfy-debug-card-head {
      display: grid;
      grid-template-columns: 18px minmax(0, 1fr);
      gap: 8px;
      align-items: flex-start;
      min-width: 0;
    }
    .comfy-debug-card-head input {
      margin-top: 3px;
      flex: 0 0 auto;
    }
    .comfy-debug-select-marker {
      margin-top: 1px;
      color: var(--accent);
      font-size: 15px;
      line-height: 1;
      user-select: none;
    }
    .comfy-debug-card-title {
      display: grid;
      gap: 3px;
      min-width: 0;
    }
    .comfy-debug-card-title strong {
      line-height: 1.35;
    }
    .comfy-debug-type {
      display: inline-flex;
      justify-self: start;
      border-radius: 999px;
      padding: 2px 7px;
      background: #e0f2fe;
      color: #075985;
      font-size: 11px;
      font-weight: 800;
    }
    .comfy-debug-type.configured {
      background: #dcfce7;
      color: #166534;
    }
    .comfy-debug-run-state {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      justify-self: start;
      font-size: 11px;
      color: var(--muted);
      line-height: 1.4;
      margin-top: -2px;
    }
    .comfy-debug-run-state::before {
      content: "";
      width: 7px;
      height: 7px;
      border-radius: 999px;
      background: #94a3b8;
      flex: 0 0 auto;
    }
    .comfy-debug-run-state.running {
      color: #0369a1;
      font-weight: 700;
    }
    .comfy-debug-run-state.running::before {
      background: #0ea5e9;
      box-shadow: 0 0 0 4px rgba(14, 165, 233, .14);
    }
    .comfy-debug-run-state.completed {
      color: #166534;
      font-weight: 700;
    }
    .comfy-debug-run-state.completed::before {
      background: #22c55e;
    }
    .comfy-debug-run-state.failed {
      color: #b91c1c;
      font-weight: 700;
    }
    .comfy-debug-run-state.failed::before {
      background: #ef4444;
    }
    .comfy-reference-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(180px, 1fr));
      gap: 12px;
    }
    .comfy-reference-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
      display: grid;
      gap: 10px;
      min-width: 0;
    }
    .comfy-reference-card[hidden] {
      display: none;
    }
    .comfy-reference-card-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
    }
    .comfy-reference-card-head span {
      font-size: 11px;
    }
    .comfy-reference-body {
      display: grid;
      grid-template-columns: 96px minmax(0, 1fr);
      gap: 12px;
      align-items: start;
    }
    .comfy-reference-preview {
      width: 96px;
      height: 96px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      background: #f8fafc;
      display: grid;
      place-items: center;
      overflow: hidden;
    }
    .comfy-reference-preview img,
    .comfy-reference-preview video {
      width: 100%;
      height: 100%;
      object-fit: contain;
      background: #f8fafc;
    }
    .comfy-reference-preview .empty {
      color: var(--muted);
      font-size: 12px;
      padding: 12px;
      text-align: center;
    }
    .comfy-reference-controls {
      display: grid;
      gap: 8px;
      min-width: 0;
    }
    .comfy-reference-controls select,
    .comfy-reference-controls input[type="file"] {
      width: 100%;
    }
    .comfy-upload-control {
      display: grid;
      gap: 6px;
      min-width: 0;
    }
    .comfy-upload-state {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      min-height: 38px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
      color: var(--text);
      font-size: 12px;
    }
    .comfy-upload-state[hidden] {
      display: none;
    }
    .comfy-upload-state span {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .comfy-upload-state button {
      flex: 0 0 auto;
      padding: 5px 9px;
      font-size: 12px;
    }
    .comfy-reference-meta {
      min-height: 16px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    label:has(#comfyDebugReference) {
      display: none;
    }
    .comfy-debug-result-grid {
      display: grid;
      gap: 10px;
    }
    .comfy-debug-result {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
      padding: 12px;
      display: grid;
      gap: 10px;
    }
    .comfy-debug-result-head {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 12px;
    }
    .comfy-debug-log {
      max-height: 180px;
      overflow: auto;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      border: 1px solid var(--line);
      background: #0f172a;
      color: #dbeafe;
      border-radius: 10px;
      padding: 10px;
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 12px;
      line-height: 1.45;
    }
    .comfy-debug-running {
      border: 1px dashed var(--accent);
      background: #f0fdfa;
      border-radius: 12px;
      padding: 14px;
      display: grid;
      gap: 8px;
    }
    .comfy-debug-running-bar {
      height: 8px;
      border-radius: 999px;
      background: linear-gradient(90deg, var(--accent), #99f6e4, var(--accent));
      background-size: 200% 100%;
      animation: debugPulse 1.2s linear infinite;
    }
    @keyframes debugPulse {
      from { background-position: 0 0; }
      to { background-position: 200% 0; }
    }
    .health-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(180px, 1fr));
      gap: 12px;
    }
    .health-card {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
      display: grid;
      gap: 6px;
      min-height: 96px;
      box-shadow: var(--shadow-soft);
    }
    .health-card.ok { border-color: #c9f2d8; background: #f7fffa; }
    .health-card.warn { border-color: #f6e4a7; background: #fffdf4; }
    .health-card.error { border-color: #fac8c5; background: #fff8f7; }
    .health-card strong {
      font-size: 14px;
    }
    .health-state {
      font-weight: 650;
      color: var(--muted);
    }
    .health-card.ok .health-state { color: var(--ok); }
    .health-card.warn .health-state { color: var(--warn); }
    .health-card.error .health-state { color: var(--danger); }
    pre {
      margin: 0;
      padding: 16px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.55;
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 13px;
      background: #fff;
    }
    .file-editor {
      min-height: 520px;
      border: 0;
      border-radius: 0;
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 13px;
      line-height: 1.55;
      background: #fff;
      resize: vertical;
    }
    .muted { color: var(--muted); }
    .small { font-size: 12px; }
    .staff-card,
    .progress-step,
    .output-link,
    .asset-card {
      line-height: 1.35;
    }
    .staff-card *,
    .progress-step *,
    .output-link *,
    .asset-card * {
      max-height: none;
    }
    @media (max-width: 860px) {
      header {
        height: auto;
        align-items: flex-start;
        gap: 10px;
        padding: 12px;
        flex-direction: column;
      }
      .brand {
        width: 100%;
        align-items: flex-start;
        gap: 10px;
        flex-direction: column;
      }
      .top-nav { width: 100%; overflow-x: auto; }
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .split { grid-template-columns: 1fr; }
      .run-primary-grid { grid-template-columns: 1fr; }
      .run-model-grid { grid-template-columns: 1fr; }
      .asset-lightbox { padding: 10px; }
      .asset-lightbox-body {
        height: auto;
        grid-template-columns: 38px minmax(0, 1fr) 38px;
        gap: 8px;
      }
      .asset-lightbox-nav {
        width: 36px;
        height: 46px;
      }
      .asset-lightbox-media {
        width: auto !important;
        height: auto !important;
        max-width: 100% !important;
        max-height: 100% !important;
      }
      .run-actions { position: static; margin: 0; padding: 0; border-top: 0; background: transparent; }
      .provider-grid { grid-template-columns: 1fr; }
      .video-grid { grid-template-columns: 1fr; }
      .staff-manager { grid-template-columns: 1fr; }
      .output-summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .output-sections { grid-template-columns: 1fr; }
      .workflow-step-grid { grid-template-columns: 1fr; }
      .comfy-debug-layout { grid-template-columns: 1fr; }
      .health-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body data-view="run">
  <header>
    <div class="brand">
      <h1>自定义工作流管理台</h1>
      <nav class="top-nav" aria-label="主功能">
        <button class="nav-btn active" data-view-target="run" type="button">新建任务</button>
        <button class="nav-btn" data-view-target="config" type="button">系统配置</button>
        <button class="nav-btn" data-view-target="staff" type="button">数字员工</button>
        <button class="nav-btn" data-view-target="workflow" type="button">工作流</button>
        <button class="nav-btn" data-view-target="assets" type="button">素材库</button>
        <button class="nav-btn" data-view-target="comfyDebug" type="button">ComfyUI调试</button>
        <button class="nav-btn" data-view-target="output" type="button">任务输出</button>
        <button class="nav-btn" data-view-target="system" type="button">系统状态</button>
      </nav>
    </div>
    <div class="muted small" id="env">加载中</div>
  </header>
  <div id="toast" class="toast" role="status" aria-live="polite"></div>
  <main>
    <aside id="taskSidebar" hidden>
      <div class="row">
        <strong>任务输出</strong>
        <button id="refreshTasks" title="刷新任务列表">刷新</button>
      </div>
      <div class="list" id="taskList"></div>
    </aside>
    <section class="stack">
      <div class="panel form run-form view" data-view="run">
        <div class="run-section">
          <div class="run-section-head">
            <strong>任务基础信息</strong>
            <span class="muted small">这里仅填写本次长视频任务，模型和自动化参数到“系统配置”维护</span>
          </div>
          <div class="run-primary-grid">
            <label hidden>产品类型
              <select id="productTemplate">
                <option value="long_video" selected>长视频工作流</option>
              </select>
            </label>
            <label>工作流
              <select id="workflow"></select>
            </label>
            <label>任务名称
              <input id="taskTitle" autocomplete="off" spellcheck="false" placeholder="例如 AI员工工作流平台长视频，可留空" />
            </label>
          </div>
          <div class="run-model-grid" id="modelRuntimeConfig">
            <label>执行模式
              <select id="provider">
                <option value="auto">auto</option>
                <option value="offline">offline</option>
                <option value="openai">openai</option>
              </select>
            </label>
            <label>模型
              <select id="model">
                <optgroup label="推荐主力模型">
                  <option value="gpt-5.5" selected>GPT-5.5 - 复杂任务/最高质量</option>
                  <option value="gpt-5.4">GPT-5.4 - 通用高质量</option>
                </optgroup>
                <optgroup label="轻量与低成本">
                  <option value="gpt-5.4-mini">GPT-5.4 mini - 速度/成本平衡</option>
                  <option value="gpt-5.4-nano">GPT-5.4 nano - 最低延迟/批量任务</option>
                </optgroup>
                <optgroup label="推理模型">
                  <option value="o3">o3 - 深度推理</option>
                  <option value="o4-mini">o4-mini - 快速推理</option>
                </optgroup>
                <optgroup label="兼容旧模型">
                  <option value="gpt-4.1">GPT-4.1 - 旧版通用</option>
                  <option value="gpt-4.1-mini">GPT-4.1 mini - 旧版低成本</option>
                  <option value="gpt-4o">GPT-4o - 旧版多模态</option>
                  <option value="gpt-4o-mini">GPT-4o mini - 旧版轻量</option>
                </optgroup>
                <optgroup label="自定义">
                  <option value="custom">手动输入模型名</option>
                </optgroup>
              </select>
            </label>
            <label>模型超时
              <select id="modelTimeout">
                <option value="120">120 秒（云端默认）</option>
                <option value="300">300 秒</option>
                <option value="600">600 秒</option>
                <option value="900" selected>900 秒（本地模型推荐）</option>
                <option value="1800">1800 秒</option>
              </select>
            </label>
          </div>
        </div>
        <div class="run-composer">
          <textarea id="userInput" aria-label="长视频需求" placeholder="输入你要制作的长视频需求。比如：主题、平台、目标观众、风格、可用素材、交付目标。"></textarea>
          <div class="composer-actions">
            <span id="status" class="status">准备就绪</span>
            <div class="row">
              <button class="danger" id="cancelRunBtn" disabled hidden>终止</button>
              <button class="primary" id="runBtn">开始生成</button>
            </div>
          </div>
        </div>
        <div class="row run-actions" hidden>
          <button id="sampleBtn">填入长视频示例</button>
          <button id="longVideoSampleBtn" hidden>长视频示例</button>
          <button id="gameSampleBtn" hidden>游戏示例</button>
          <button id="clearSettingsBtn">清除已保存配置</button>
        </div>
      </div>

      <div class="panel form view" data-view="config" hidden>
        <div class="run-section">
          <div class="run-section-head">
            <strong>系统配置</strong>
            <span class="muted small">模型接口、自动化、ComfyUI、配音、记忆都在这里维护；运行页只负责输入需求和启动任务。</span>
          </div>
        </div>
        <div class="stack" id="configSections">
        <details data-config-section>
          <summary><strong>模型接口配置</strong> <span class="muted small">API Key、中转站和自定义模型名</span></summary>
          <div class="details-body">
            <div class="provider-grid">
              <label>API Key
                <input id="apiKey" type="password" autocomplete="off" spellcheck="false" placeholder="sk-...，只用于本次运行，不保存" />
              </label>
              <label>中转站 Base URL
                <input id="baseUrl" autocomplete="off" spellcheck="false" placeholder="例如 https://api.example.com/v1，留空用官方地址" />
              </label>
              <label>自定义模型名
                <input id="customModel" placeholder="选择“手动输入模型名”时填写" disabled />
              </label>
            </div>
            <div class="row">
              <button id="localOfflineBtn" type="button">一键本地离线模式</button>
              <span class="muted small">自动使用 Ollama + qwen3:8b-q4_K_M + 项目内 runtime/models</span>
            </div>
            <div class="provider-grid">
              <label>本地模型服务
                <select id="localModelPreset">
                  <option value="">不使用本地预设</option>
                </select>
              </label>
              <label>本地模型名
                <select id="localModelName">
                  <option value="">先选择本地模型服务</option>
                </select>
              </label>
              <label>连接测试
                <button id="testModelBtn" type="button">测试当前模型接口</button>
              </label>
            </div>
          </div>
        </details>
        <details data-config-section>
          <summary><strong>记忆与继承</strong> <span class="muted small">长期记忆和历史任务上下文</span></summary>
          <div class="details-body">
            <div class="provider-grid">
              <label>长期记忆
                <select id="useMemory">
                  <option value="video_output" selected>仅视频输出阶段使用 my_memory</option>
                  <option value="off">不使用长期记忆</option>
                  <option value="all">全流程使用 my_memory（高级）</option>
                </select>
              </label>
              <label>继承历史任务
                <select id="inheritTask">
                  <option value="">不继承</option>
                </select>
              </label>
              <label>继承范围
                <select id="inheritMode">
                  <option value="final_output" selected>只参考上次最终成品</option>
                  <option value="input_and_final">参考上次需求和最终成品</option>
                </select>
              </label>
            </div>
            <div class="provider-grid">
              <label>本地知识库
                <select id="useKnowledge">
                  <option value="off" selected>不追加知识库</option>
                  <option value="on">追加 my_knowledge_base</option>
                </select>
              </label>
              <label>上传知识文件
                <input id="knowledgeFile" type="file" accept=".md,.txt,.json,.csv" />
              </label>
              <label>知识库操作
                <button id="uploadKnowledgeBtn" type="button">上传到知识库</button>
              </label>
            </div>
            <div class="reference-list" id="knowledgeList"></div>
          </div>
        </details>
        <details class="automation-config" data-config-section open>
          <summary><strong>自动化与成片配置</strong> <span class="muted small">打开“一键到底”自动跑完整流程；关闭后每步确认再继续</span></summary>
          <div class="details-body">
            <div class="provider-grid config-card" data-title="流程开关" data-desc="控制推进方式、自动生产模式、剪辑方式和最终输出文件名">
              <label>推进方式
                <select id="workflowAdvanceMode">
                  <option value="auto" selected>一键到底：自动跑到最终产物</option>
                  <option value="step_confirm">逐步确认：每步输出确认后继续</option>
                </select>
              </label>
              <label>自动生成模式
                <select id="autoProductionMode">
                  <option value="off" selected>关闭</option>
                  <option value="package_only">只出制作包：不调用接口、不剪辑</option>
                  <option value="audio_package">出制作包 + 配音字幕文本：不生成视频</option>
                  <option value="api_ready">只生图，不生视频</option>
                  <option value="comfy_full">全自动成片预览：调用 ComfyUI 素材接口 + FFmpeg 自动剪辑</option>
                </select>
              </label>
              <label>ComfyUI 调试门禁
                <select id="comfyDebugGate">
                  <option value="on" selected>开启：按调试台顺序确认后再下一步</option>
                  <option value="off">关闭：直接按生产配置自动调用</option>
                </select>
              </label>
              <label>自动剪辑方式
                <select id="composeTool">
                  <option value="ffmpeg" selected>本地 FFmpeg 自动剪辑预览</option>
                  <option value="runninghub">只调用云端 ComfyUI 生成素材</option>
                  <option value="jianying">剪映工程（预留）</option>
                  <option value="manual">只生成清单</option>
                </select>
              </label>
              <label>预览/最终视频文件名
                <input id="finalVideoName" autocomplete="off" spellcheck="false" placeholder="final_video.mp4" />
              </label>
            </div>
            <div class="provider-grid config-card" data-title="ComfyUI 连接" data-desc="配置 RunningHub/云端 ComfyUI 的密钥、基础地址和当前编辑槽位接口">
              <label>ComfyUI 平台密钥
                <input id="comfyApiKey" type="password" autocomplete="off" spellcheck="false" placeholder="RunningHub 或云端 ComfyUI API Key" />
              </label>
              <label>ComfyUI 平台接口地址
                <input id="comfyBaseUrl" autocomplete="off" spellcheck="false" placeholder="RunningHub: https://www.runninghub.cn/openapi/v2" />
              </label>
              <label>当前编辑槽位接口
                <input id="comfyWorkflowEndpoint" autocomplete="off" spellcheck="false" placeholder="/run/workflow/你的素材预览工作流ID 或 /run/ai-app/你的应用ID" />
              </label>
            </div>
            <div class="config-card" data-title="工作流配置入口" data-desc="具体 Endpoint、nodeInfoList、API JSON 导入和调试结果预览已统一迁移到 ComfyUI 调试台">
              <div class="row">
                <span class="muted small">这里仅保留全局 API Key 和 Base URL。每个图片/视频工作流的参数请到 ComfyUI 调试台左侧单独保存。</span>
                <button type="button" data-view-target="comfyDebug">打开 ComfyUI 调试台</button>
              </div>
            </div>
            <div class="provider-grid config-card" data-title="两工作流路由" data-desc="运行时按素材类型自动选择全能图片或全能视频，保存当前槽位映射" hidden>
              <label>ComfyUI 工作流库（运行时自动选择）
                <select id="comfyWorkflowPreset"></select>
              </label>
              <label>当前工作流说明
                <input id="comfyWorkflowPresetNote" autocomplete="off" spellcheck="false" placeholder="例如：用参考图生成统一风格关键帧" />
              </label>
              <label>工作流库操作
                <div class="inline-actions">
                  <button id="applyComfyWorkflowPresetBtn" type="button">应用到配置</button>
                  <button id="saveComfyWorkflowPresetBtn" type="button">保存当前配置</button>
                  <button id="resetComfyWorkflowPresetBtn" type="button">重置槽位</button>
                </div>
              </label>
            </div>
            <div class="reference-list workflow-summary-card" id="comfyWorkflowLibraryList" hidden></div>
            <div class="provider-grid comfy-mapping-grid config-card mapping-card" data-title="节点映射" data-desc="导入 API JSON 后确认可传参节点；支持 prompt、reference_image、control_mode 等占位符" hidden>
              <label>当前编辑槽位节点映射 JSON
                <textarea id="comfyNodeInfoList" spellcheck="false" placeholder='[]; 可使用 {{prompt}}、{{negative_prompt}}、{{image_prompt}}、{{video_prompt}}、{{reference_image}}、{{payload}}'></textarea>
              </label>
              <label>导入 API JSON 自动识别
                <input id="comfyApiWorkflowFile" type="file" accept=".json,application/json" />
              </label>
              <label>ComfyUI 轮询超时
                <select id="comfyPollTimeout">
                  <option value="900">15 分钟</option>
                  <option value="1800">30 分钟</option>
                  <option value="3600" selected>60 分钟</option>
                  <option value="7200">120 分钟</option>
                </select>
              </label>
            </div>
            <div class="reference-list parameter-map" id="comfyParameterMapper" hidden></div>
            <div class="provider-grid config-card" data-title="素材质检" data-desc="自动评分，不合格时按最大尝试次数重试素材生成">
              <label>素材自动评审
                <select id="assetQualityGate">
                  <option value="on" selected>启用：不合格自动重试</option>
                  <option value="off">关闭：只跑一次</option>
                </select>
              </label>
              <label>最多尝试次数
                <select id="assetMaxAttempts">
                  <option value="1">1 次</option>
                  <option value="2" selected>2 次</option>
                  <option value="3">3 次</option>
                  <option value="4">4 次</option>
                </select>
              </label>
              <label>最低通过分
                <select id="assetMinScore">
                  <option value="60">60 分：宽松</option>
                  <option value="70" selected>70 分：标准</option>
                  <option value="80">80 分：严格</option>
                  <option value="90">90 分：很严格</option>
                </select>
              </label>
            </div>
            <div class="provider-grid config-card" data-title="本地配音" data-desc="选择 TTS 模式、默认音色和参考音频，供自动成片使用">
              <label>本地配音
                <select id="voiceMode">
                  <option value="off" selected>不生成配音音频</option>
                  <option value="windows_sapi">Windows 本地语音（最快备用）</option>
                  <option value="preset">默认 AI 音色</option>
                  <option value="voxcpm2">VoxCPM2 本地仿声</option>
                </select>
              </label>
              <label>默认 AI 音色
                <select id="voicePreset">
                  <option value="warm_female">暖心女声：自然亲和，适合口播种草</option>
                  <option value="clear_female">清爽女声：干净利落，适合教程解说</option>
                  <option value="pro_male">专业男声：稳重可信，适合商业介绍</option>
                  <option value="deep_male">沉稳男声：低沉有质感，适合纪录片旁白</option>
                  <option value="young_male">活力男声：节奏明快，适合长视频重点段落</option>
                  <option value="story_female">故事女声：温柔叙事，适合长视频讲述</option>
                </select>
              </label>
              <label>本人参考音频
                <input id="voiceReferenceFile" type="file" accept="audio/wav,audio/mpeg,audio/mp4,audio/flac,audio/ogg,.wav,.mp3,.m4a,.flac,.ogg" />
              </label>
              <label>已上传参考音频路径
                <input id="voiceReferenceAudioPath" autocomplete="off" spellcheck="false" placeholder="上传后自动填入，也可手动填 my_voice_samples/xxx.wav" />
              </label>
            </div>
            <div class="provider-grid config-card" data-title="配音高级项" data-desc="仿声参考文本、VoxCPM2 命令模板和配音超时设置">
              <label>参考音频原文
                <input id="voiceReferenceText" autocomplete="off" spellcheck="false" placeholder="可选：参考音频里本人说的话，能提高仿声稳定性" />
              </label>
              <label>VoxCPM2 命令模板
                <input id="voiceCommandTemplate" autocomplete="off" spellcheck="false" placeholder="留空使用项目内 VoxCPM2；高级用户可填自定义命令" />
              </label>
              <label>配音超时
                <select id="voiceTimeout">
                  <option value="180">3 分钟（预览优先）</option>
                  <option value="300">5 分钟</option>
                  <option value="600">10 分钟</option>
                  <option value="900">15 分钟</option>
                  <option value="1800">30 分钟</option>
                  <option value="3600" selected>60 分钟（VoxCPM2 CPU 推荐）</option>
                  <option value="5400">90 分钟</option>
                </select>
              </label>
            </div>
          </div>
        </details>
        </div>
      </div>

      <div class="panel form view" data-view="staff" hidden>
        <div class="manager-toolbar">
          <div class="manager-title">
            <strong>数字员工管理</strong>
            <span id="staffStatus" class="status">管理 my_custom_staff</span>
          </div>
          <div class="manager-actions">
            <button id="refreshStaffBtn">刷新员工</button>
            <button id="newStaffBtn">新建员工</button>
            <button class="danger" id="deleteStaffBtn" disabled>删除员工</button>
          </div>
        </div>
        <div class="staff-manager">
          <div class="staff-sidebar">
            <label>员工搜索
              <input id="staffFilter" autocomplete="off" spellcheck="false" placeholder="按名称、编号或角色筛选" />
            </label>
            <label class="inline-check">
              <input id="showArchivedStaff" type="checkbox" />
              显示归档员工
            </label>
            <div class="staff-list" id="staffList"></div>
          </div>
          <div class="staff-editor">
            <label>员工文件夹名
              <input id="staffName" autocomplete="off" spellcheck="false" placeholder="例如 20_销售话术专员" />
            </label>
            <label>agent.md
              <textarea id="staffAgentMd" spellcheck="false" placeholder="选择一个员工后查看或编辑 agent.md"></textarea>
            </label>
            <label>flow_rule.json
              <textarea id="staffFlowRule" spellcheck="false" placeholder="选择一个员工后查看或编辑 flow_rule.json"></textarea>
            </label>
            <div class="row">
              <button class="primary" id="saveStaffBtn">保存员工</button>
              <span class="muted small">保存后会直接写入 my_custom_staff；flow_rule.json 必须是合法 JSON。</span>
            </div>
          </div>
        </div>
      </div>

      <div class="panel form view" data-view="workflow" hidden>
        <div class="row">
          <strong>工作流编辑器</strong>
          <button id="refreshWorkflowsBtn" type="button">刷新工作流</button>
          <button id="newWorkflowBtn" type="button">新建工作流</button>
          <button id="addWorkflowStepBtn" type="button">新增步骤</button>
          <button class="danger" id="deleteWorkflowBtn" type="button" disabled>删除工作流</button>
          <label class="inline-check">
            <input id="showArchivedWorkflows" type="checkbox" />
            显示归档工作流
          </label>
          <span id="workflowEditorStatus" class="status">管理 my_workflows</span>
        </div>
        <div class="staff-manager">
          <div class="staff-list" id="workflowList"></div>
          <div class="staff-editor">
            <div class="provider-grid">
              <label>工作流文件名
                <input id="workflowFile" autocomplete="off" spellcheck="false" placeholder="例如 workflow_长视频全流程" />
              </label>
              <label>工作流名称
                <input id="workflowName" autocomplete="off" spellcheck="false" placeholder="例如 长视频全流程" />
              </label>
              <label>说明
                <input id="workflowDescription" autocomplete="off" spellcheck="false" placeholder="这个工作流用于什么场景" />
              </label>
            </div>
            <div class="row">
              <strong>执行步骤</strong>
              <span class="muted small">每一步选择一个数字员工，填写它要完成的任务和输出物。</span>
            </div>
            <div class="reference-list" id="workflowSteps"></div>
            <div class="row">
              <button class="primary" id="saveWorkflowBtn" type="button">保存工作流</button>
              <span class="muted small">保存后写入 my_workflows/*.json，并同步到“运行工作流”的下拉列表。</span>
            </div>
          </div>
        </div>
      </div>

      <div class="panel form view" data-view="assets" hidden>
        <div class="asset-library-shell">
          <section class="asset-library-main">
            <div class="asset-library-hero">
              <div>
                <h1 class="asset-library-title">资产库</h1>
                <span class="muted small">沉淀可复用的角色、商品、参考图和视频素材，后续任务可直接引用。</span>
              </div>
              <div class="row">
                <button id="refreshAssetLibraryBtn" type="button">刷新</button>
                <span id="assetLibraryStatus" class="status">未加载</span>
              </div>
            </div>
            <div class="asset-library-tabs" id="assetLibraryTabs" role="tablist" aria-label="素材分类">
              <button class="asset-library-tab active" data-asset-section="all" type="button">全部</button>
              <button class="asset-library-tab" data-asset-section="material" type="button">素材</button>
              <button class="asset-library-tab" data-asset-section="character" type="button">角色</button>
              <button class="asset-library-tab" data-asset-section="product" type="button">商品</button>
              <button class="asset-library-tab" data-asset-section="reference" type="button">参考</button>
            </div>
            <div class="row">
              <label>标签筛选
                <select id="assetLibraryTagFilter">
                  <option value="">全部标签</option>
                </select>
              </label>
            </div>
            <div class="asset-library-grid" id="assetLibraryGrid">
              <div class="muted small">从“任务输出”的已生成素材里点击“收藏复用”，或点击新增资产导入本地图片/视频。</div>
            </div>
          </section>
        </div>
        <div class="asset-library-detail" id="assetLibraryDetail" hidden>
          <div class="asset-detail-head">
            <strong>资产详情</strong>
            <button class="asset-detail-close-btn" id="assetLibraryDetailCloseBtn" type="button" aria-label="关闭资产详情" title="关闭">×</button>
          </div>
          <div class="asset-detail-preview" id="assetLibraryDetailPreview"></div>
          <div class="asset-detail-form">
            <label>名称
              <input id="assetLibraryDetailName" autocomplete="off" spellcheck="false" />
            </label>
            <label>分类
              <select id="assetLibraryDetailCategory"></select>
            </label>
            <label>备注
              <textarea id="assetLibraryDetailNote" spellcheck="false" placeholder="写下这个素材适合怎么复用。"></textarea>
            </label>
            <div class="asset-detail-actions">
              <button id="assetLibraryDetailOpenBtn" type="button">打开预览</button>
              <button class="danger" id="assetLibraryDetailDeleteBtn" type="button">删除</button>
              <button class="primary" id="assetLibraryDetailSaveBtn" type="button">保存修改</button>
            </div>
            <span class="muted small" id="assetLibraryDetailMeta"></span>
          </div>
        </div>
        <div class="asset-import-modal" id="assetImportModal" hidden>
          <div class="asset-import-card">
            <div class="row">
              <h3 id="assetImportTitle">新增资产</h3>
              <button id="assetImportCloseBtn" type="button">关闭</button>
            </div>
            <div class="asset-import-grid">
              <label>本地图片/视频
                <input id="assetImportFile" type="file" accept="image/*,video/*,audio/*" />
              </label>
              <label>名称
                <input id="assetImportName" autocomplete="off" spellcheck="false" placeholder="留空则使用文件名" />
              </label>
              <label>分类
                <select id="assetImportCategory"></select>
              </label>
              <label>备注
                <textarea id="assetImportNote" spellcheck="false" placeholder="例如：适合品牌人物设定、商品主图、风格参考等。"></textarea>
              </label>
            </div>
            <div class="asset-import-actions">
              <button id="assetImportComfyBtn" type="button">去 ComfyUI 生成</button>
              <button class="primary" id="assetImportSaveBtn" type="button">导入资产</button>
            </div>
            <span class="muted small" id="assetImportStatus"></span>
          </div>
        </div>
      </div>

      <div class="panel form view" data-view="comfyDebug" hidden>
        <div class="manager-toolbar">
          <div class="manager-title">
            <strong>ComfyUI / RunningHub 调试台</strong>
            <span class="muted small">单独调试参考图、分段视频、放大、补帧等工作流；每次运行当前选中的一个工作流，结果可连续预览。</span>
          </div>
          <div class="row">
            <button id="refreshComfyDebugBtn" type="button">刷新工作流</button>
            <span id="comfyDebugStatus" class="status">未加载</span>
          </div>
        </div>
        <div class="comfy-debug-layout">
          <aside class="comfy-debug-sidebar">
            <div class="output-section">
              <div class="output-section-head">
                <strong>调试工作流</strong>
                <span class="muted small" id="comfyDebugSelectedMeta">单选调试</span>
              </div>
              <div class="comfy-debug-list" id="comfyDebugWorkflowList"></div>
            </div>
          </aside>
          <section class="comfy-debug-main">
            <div class="output-section">
              <div class="output-section-head">
                <strong>RunningHub 调用参数</strong>
                <div class="inline-actions">
                  <button class="primary" id="runComfyDebugBtn" type="button">运行当前工作流</button>
                </div>
              </div>
              <div class="muted small">默认复用系统配置里的 ComfyUI 密钥和 Base URL；左侧每个工作流都有独立配置和预览。</div>
              <div class="provider-grid">
                <label>API Key
                  <input id="comfyDebugApiKey" type="password" autocomplete="off" spellcheck="false" placeholder="留空则使用系统配置 ComfyUI 平台密钥" />
                </label>
                <label>Base URL
                  <input id="comfyDebugBaseUrl" autocomplete="off" spellcheck="false" placeholder="留空则使用系统配置 ComfyUI 平台接口地址" />
                </label>
                <label>轮询超时
                  <select id="comfyDebugPollTimeout">
                    <option value="300">5 分钟</option>
                    <option value="900">15 分钟</option>
                    <option value="1800">30 分钟</option>
                    <option value="3600" selected>60 分钟</option>
                  </select>
                </label>
              </div>
              <div class="provider-grid">
                <label>接口地址
                  <input id="comfyDebugEndpoint" autocomplete="off" spellcheck="false" placeholder="/run/workflow/xxx 或 /run/ai-app/xxx；留空用槽位配置" />
                </label>
                <label id="comfyDebugReferencePathField">参考图/视频路径
                  <input id="comfyDebugReference" autocomplete="off" spellcheck="false" placeholder="可填 my_workspace/my_asset_library/xxx.png 或任务输出里的相对路径" />
                </label>
                <label id="comfyDebugAssetTagFilterField">素材标签筛选
                  <select id="comfyDebugAssetTagFilter">
                    <option value="">全部素材</option>
                  </select>
                </label>
                <label id="comfyDebugMaskImageField" hidden>蒙版路径
                  <input id="comfyDebugMaskImage" autocomplete="off" spellcheck="false" placeholder="input_mask_image：局部修复使用的黑白蒙版" />
                </label>
                <label id="comfyDebugAudioFileField" hidden>口型音频路径
                  <input id="comfyDebugAudioFile" autocomplete="off" spellcheck="false" placeholder="input_audio_file：本地 TTS 生成的最终 WAV" />
                </label>
              </div>
              <input id="comfyDebugMiddleFrameReference" type="hidden" />
              <input id="comfyDebugLastFrameReference" type="hidden" />
              <div class="comfy-reference-grid" id="comfyDebugReferenceGrid">
                <div class="comfy-reference-card" id="comfyDebugStartFrameCard">
                  <div class="comfy-reference-card-head">
                    <strong>首帧 / 主参考</strong>
                    <span class="muted small">reference_image</span>
                  </div>
                  <div class="comfy-reference-body">
                    <div class="comfy-reference-preview" id="comfyDebugReferencePreview">
                      <span class="empty">未选择参考素材</span>
                    </div>
                    <div class="comfy-reference-controls">
                      <label>从素材库选择
                        <select id="comfyDebugAssetReference">
                          <option value="">不使用素材库参考</option>
                        </select>
                      </label>
                      <div class="comfy-upload-control">
                        <label id="comfyDebugReferenceFileLabel">上传参考文件
                          <input id="comfyDebugReferenceFile" type="file" accept="image/*" />
                        </label>
                        <div id="comfyDebugReferenceUploadState" class="comfy-upload-state" hidden>
                          <span id="comfyDebugReferenceUploadName"></span>
                          <button id="comfyDebugReferenceReuploadBtn" type="button">重新上传</button>
                        </div>
                      </div>
                    </div>
                  </div>
                  <span class="muted small comfy-reference-meta" id="comfyDebugReferencePreviewMeta">未选择</span>
                </div>
                <div class="comfy-reference-card" id="comfyDebugMiddleFrameCard" hidden>
                  <div class="comfy-reference-card-head">
                    <strong>中帧</strong>
                    <span class="muted small">middle_frame_image</span>
                  </div>
                  <div class="comfy-reference-body">
                    <div class="comfy-reference-preview" id="comfyDebugMiddleFramePreview">
                      <span class="empty">首中尾帧模式下选择中帧</span>
                    </div>
                    <div class="comfy-reference-controls">
                      <label>从素材库选择
                        <select id="comfyDebugMiddleFrameAssetReference">
                          <option value="">不使用中帧素材</option>
                        </select>
                      </label>
                      <div class="comfy-upload-control">
                        <label id="comfyDebugMiddleFrameReferenceFileLabel">上传中帧文件
                          <input id="comfyDebugMiddleFrameReferenceFile" type="file" accept="image/*" />
                        </label>
                        <div id="comfyDebugMiddleFrameUploadState" class="comfy-upload-state" hidden>
                          <span id="comfyDebugMiddleFrameUploadName"></span>
                          <button id="comfyDebugMiddleFrameReuploadBtn" type="button">重新上传</button>
                        </div>
                      </div>
                    </div>
                  </div>
                  <span class="muted small comfy-reference-meta" id="comfyDebugMiddleFrameReferenceHint">首中尾帧视频需要第二张中帧图。</span>
                </div>
                <div class="comfy-reference-card" id="comfyDebugLastFrameCard" hidden>
                  <div class="comfy-reference-card-head">
                    <strong>尾帧</strong>
                    <span class="muted small">last_frame_image</span>
                  </div>
                  <div class="comfy-reference-body">
                    <div class="comfy-reference-preview" id="comfyDebugLastFramePreview">
                      <span class="empty">首尾帧模式下选择尾帧</span>
                    </div>
                    <div class="comfy-reference-controls">
                      <label>从素材库选择
                        <select id="comfyDebugLastFrameAssetReference">
                          <option value="">不使用尾帧素材</option>
                        </select>
                      </label>
                      <div class="comfy-upload-control">
                        <label id="comfyDebugLastFrameReferenceFileLabel">上传尾帧文件
                          <input id="comfyDebugLastFrameReferenceFile" type="file" accept="image/*" />
                        </label>
                        <div id="comfyDebugLastFrameUploadState" class="comfy-upload-state" hidden>
                          <span id="comfyDebugLastFrameUploadName"></span>
                          <button id="comfyDebugLastFrameReuploadBtn" type="button">重新上传</button>
                        </div>
                      </div>
                    </div>
                  </div>
                  <span class="muted small comfy-reference-meta" id="comfyDebugLastFrameReferenceHint">首尾帧视频需要第二张尾帧图。</span>
                </div>
              </div>
              <div class="row">
                <button id="clearComfyDebugReferenceBtn" type="button">清空参考</button>
                <span class="muted small" id="comfyDebugReferenceHint">可直接输入路径、选择素材库资产，或上传本地参考图/视频。</span>
              </div>
              <div class="provider-grid">
                <label hidden>工作流子类型
                  <select id="comfyDebugWorkflowMode"></select>
                </label>
                <label>随机种子
                  <input id="comfyDebugSeed" autocomplete="off" spellcheck="false" placeholder="留空随机；固定 seed 方便复现" />
                </label>
                <label>宽度
                  <input id="comfyDebugWidth" autocomplete="off" spellcheck="false" placeholder="横屏 848 / 竖屏 480" />
                </label>
                <label>高度
                  <input id="comfyDebugHeight" autocomplete="off" spellcheck="false" placeholder="横屏 480 / 竖屏 848" />
                </label>
                <label id="comfyDebugDurationField">视频时长（秒）
                  <input id="comfyDebugDuration" type="number" min="0.5" step="0.5" autocomplete="off" spellcheck="false" placeholder="例如 4，表示 4 秒" />
                  <span class="muted small" id="comfyDebugFrameCountHint">帧数会按 FPS 自动计算。</span>
                </label>
                <label id="comfyDebugFpsField">帧率（视频）
                  <input id="comfyDebugFps" type="number" min="1" step="1" autocomplete="off" spellcheck="false" placeholder="例如 16 / 24 / 30" />
                </label>
              </div>
              <label>正向提示词
                <textarea id="comfyDebugPrompt" spellcheck="false" placeholder="描述要生成/修复/放大的画面。调试阶段建议短而具体。"></textarea>
              </label>
              <label>负面提示词
                <textarea id="comfyDebugNegative" spellcheck="false" placeholder="例如：文字、水印、畸形手、脸部变形、闪烁、低清晰度"></textarea>
              </label>
              <label>nodeInfoList JSON（覆盖槽位）
                <textarea id="comfyDebugNodeInfoList" spellcheck="false" placeholder="留空使用所选工作流槽位的 nodeInfoList；视频长度节点请优先用 {{frame_count}}，可用 {{prompt}}、{{negative_prompt}}、{{reference_image}}、{{last_frame_image}}、{{seed}}、{{width}}、{{height}}、{{duration}}、{{fps}}、{{frame_count}}、{{task_type}}、{{image_task_mode}}、{{control_mode}}"></textarea>
              </label>
              <label>导入 API JSON 自动识别（仅本次调试）
                <input id="comfyDebugApiWorkflowFile" type="file" accept=".json,application/json" />
              </label>
            </div>
            <div class="output-section">
              <div class="output-section-head">
                <strong>调试结果预览</strong>
                <span class="muted small" id="comfyDebugResultMeta">暂无结果</span>
              </div>
              <div class="comfy-debug-result-grid" id="comfyDebugResults"></div>
            </div>
          </section>
        </div>
      </div>

      <div class="panel viewer view" data-view="output" hidden>
        <div class="viewer-head">
          <div>
            <strong id="viewerTitle">未选择任务</strong>
            <div class="muted small" id="viewerMeta">运行后会在这里查看输出文件</div>
          </div>
          <div class="row">
            <button id="saveFileBtn" type="button" disabled>保存当前文件</button>
            <button id="rebuildFinalBtn" type="button" disabled>重建最终汇总</button>
            <button id="rerunStepBtn" type="button" disabled>重跑当前步骤</button>
            <button id="resumeTaskBtn" type="button" disabled>继续任务</button>
            <button class="danger" id="outputCancelRunBtn" type="button" disabled hidden>终止任务</button>
            <button id="exportTaskBtn" type="button" disabled>导出产品包</button>
            <label class="inline-check">
              <input id="showDebugFiles" type="checkbox" />
              显示调试文件
            </label>
          </div>
          <div class="file-tabs" id="fileTabs"></div>
        </div>
        <div class="output-dashboard" id="outputDashboard">
          <div class="progress-box" id="progressBox" hidden>
            <div class="progress-head">
              <strong id="progressTitle">等待运行</strong>
              <span id="progressMeta">0/0</span>
            </div>
            <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
            <div class="progress-list" id="progressList"></div>
          </div>
          <div class="output-summary-grid" id="outputSummaryGrid" hidden></div>
          <div class="video-preview" id="videoPreviewBox" hidden>
            <div class="output-section-head">
              <strong>最终视频预览</strong>
              <span class="muted small" id="videoPreviewMeta"></span>
            </div>
            <video id="videoPreview" controls preload="metadata"></video>
          </div>
          <div class="output-sections">
            <div class="output-section">
              <div class="output-section-head">
                <strong>步骤输出</strong>
                <span class="muted small" id="stepOutputMeta">0 个步骤</span>
              </div>
              <div class="output-link-list" id="stepOutputList">
                <div class="muted small">选择任务后显示每个员工的输出。</div>
              </div>
            </div>
            <div class="output-section">
              <div class="output-section-head">
                <strong>已生成素材</strong>
                <span class="muted small" id="assetOutputMeta">未生成</span>
              </div>
              <div class="output-link-list" id="assetOutputList">
                <div class="muted small">运行后只显示图片和视频素材。</div>
              </div>
            </div>
            <div class="output-section">
              <div class="output-section-head">
                <strong>产品包文件</strong>
                <span class="muted small" id="packageOutputMeta">未生成</span>
              </div>
              <div class="output-link-list" id="packageOutputList">
                <div class="muted small">点击“导出产品包”后显示可交付文件。</div>
              </div>
            </div>
          </div>
        </div>
        <div class="step-confirm-bar" id="stepConfirmBar" hidden>
          <div class="step-confirm-copy">
            <span class="step-confirm-title" id="stepConfirmTitle">当前步骤已完成</span>
            <span class="muted small" id="stepConfirmHint">请检查下方输出，确认无误后继续下一步。</span>
          </div>
          <div class="step-confirm-actions">
            <button class="primary" id="confirmStepContinueBtn" type="button">确认并继续下一步</button>
            <button id="confirmStepRerunBtn" type="button">重跑当前步骤</button>
          </div>
        </div>
        <div class="output-section" id="taskComfyDebugPanel" hidden>
          <div class="output-section-head">
            <strong>ComfyUI 调试队列</strong>
            <span class="muted small" id="taskComfyDebugMeta">未启用</span>
          </div>
          <div class="task-comfy-debug-list" id="taskComfyDebugList"></div>
        </div>
        <textarea class="file-editor" id="fileContent" spellcheck="false">选择左侧任务，或运行一个新任务。</textarea>
      </div>

      <div class="asset-lightbox" id="assetLightbox" hidden>
        <div class="asset-lightbox-head">
          <div class="asset-lightbox-title">
            <strong id="assetLightboxTitle">素材预览</strong>
            <span class="muted small" id="assetLightboxMeta"></span>
          </div>
          <div class="row">
            <button id="assetLightboxFavoriteBtn" type="button">收藏复用</button>
            <button id="assetLightboxOpenBtn" type="button">新窗口打开</button>
            <button id="assetLightboxCloseBtn" type="button">关闭</button>
          </div>
        </div>
        <div class="asset-lightbox-body">
          <button class="asset-lightbox-nav" id="assetLightboxPrevBtn" type="button" aria-label="上一张">‹</button>
          <div class="asset-lightbox-stage" id="assetLightboxStage"></div>
          <button class="asset-lightbox-nav" id="assetLightboxNextBtn" type="button" aria-label="下一张">›</button>
        </div>
        <div class="asset-lightbox-foot">
          <span class="muted small">支持键盘 ← / → 翻页，Esc 关闭</span>
          <span class="muted small" id="assetLightboxCounter"></span>
        </div>
      </div>

      <div class="panel form view" data-view="system" hidden>
        <div class="row">
          <strong>系统状态</strong>
          <button id="refreshHealthBtn" type="button">刷新状态</button>
          <span id="healthStatus" class="status">检查本地运行环境</span>
        </div>
        <div class="health-grid" id="healthGrid"></div>
        <details open>
          <summary><strong>首次启动向导</strong> <span class="muted small">面向一站式本地部署</span></summary>
          <div class="details-body">
            <div class="reference-list">
              <div class="reference-item">
                <div class="reference-info">
                  <div class="reference-name">1. 一键启动</div>
                  <div class="muted small">Windows 用户可双击项目根目录的 start_local.bat；它会启动 Ollama、启动管理台并打开浏览器。</div>
                </div>
              </div>
              <div class="reference-item">
                <div class="reference-info">
                  <div class="reference-name">2. 选择本地模型</div>
                  <div class="muted small">点击“运行工作流 -> 模型接口配置 -> 一键本地离线模式”，系统会自动填好 Ollama、local、Base URL 和 qwen3:8b-q4_K_M。</div>
                </div>
              </div>
              <div class="reference-item">
                <div class="reference-info">
                  <div class="reference-name">3. 测试模型接口</div>
                  <div class="muted small">点击“测试当前模型接口”。通过后再运行工作流；如果失败，先看系统状态里的 Ollama 和模型服务提示。</div>
                </div>
              </div>
              <div class="reference-item">
                <div class="reference-info">
                  <div class="reference-name">4. 上传知识库</div>
                  <div class="muted small">把公司资料、产品说明、话术 SOP 上传到 my_knowledge_base，运行时选择“追加 my_knowledge_base”。</div>
                </div>
              </div>
              <div class="reference-item">
                <div class="reference-info">
                  <div class="reference-name">5. 运行示例工作流</div>
                  <div class="muted small">先用 offline 检查流程，再切到 openai/本地模型执行。输出统一写入 my_task_output。</div>
                </div>
              </div>
            </div>
          </div>
        </details>
      </div>
    </section>
  </main>

  <script>
    const els = {
      env: document.getElementById('env'),
      configSections: document.getElementById('configSections'),
      productTemplate: document.getElementById('productTemplate'),
      workflow: document.getElementById('workflow'),
      provider: document.getElementById('provider'),
      model: document.getElementById('model'),
      customModel: document.getElementById('customModel'),
      taskTitle: document.getElementById('taskTitle'),
      apiKey: document.getElementById('apiKey'),
      baseUrl: document.getElementById('baseUrl'),
      modelTimeout: document.getElementById('modelTimeout'),
      localModelPreset: document.getElementById('localModelPreset'),
      localModelName: document.getElementById('localModelName'),
      localOfflineBtn: document.getElementById('localOfflineBtn'),
      testModelBtn: document.getElementById('testModelBtn'),
      userInput: document.getElementById('userInput'),
      useMemory: document.getElementById('useMemory'),
      inheritTask: document.getElementById('inheritTask'),
      inheritMode: document.getElementById('inheritMode'),
      useKnowledge: document.getElementById('useKnowledge'),
      knowledgeFile: document.getElementById('knowledgeFile'),
      uploadKnowledgeBtn: document.getElementById('uploadKnowledgeBtn'),
      knowledgeList: document.getElementById('knowledgeList'),
      workflowAdvanceMode: document.getElementById('workflowAdvanceMode'),
      autoProductionMode: document.getElementById('autoProductionMode'),
      comfyDebugGate: document.getElementById('comfyDebugGate'),
      composeTool: document.getElementById('composeTool'),
      finalVideoName: document.getElementById('finalVideoName'),
      comfyApiKey: document.getElementById('comfyApiKey'),
      comfyBaseUrl: document.getElementById('comfyBaseUrl'),
      comfyWorkflowEndpoint: document.getElementById('comfyWorkflowEndpoint'),
      comfyWorkflowPreset: document.getElementById('comfyWorkflowPreset'),
      comfyWorkflowPresetNote: document.getElementById('comfyWorkflowPresetNote'),
      applyComfyWorkflowPresetBtn: document.getElementById('applyComfyWorkflowPresetBtn'),
      saveComfyWorkflowPresetBtn: document.getElementById('saveComfyWorkflowPresetBtn'),
      resetComfyWorkflowPresetBtn: document.getElementById('resetComfyWorkflowPresetBtn'),
      comfyWorkflowLibraryList: document.getElementById('comfyWorkflowLibraryList'),
      comfyNodeInfoList: document.getElementById('comfyNodeInfoList'),
      comfyApiWorkflowFile: document.getElementById('comfyApiWorkflowFile'),
      comfyParameterMapper: document.getElementById('comfyParameterMapper'),
      comfyPollTimeout: document.getElementById('comfyPollTimeout'),
      assetQualityGate: document.getElementById('assetQualityGate'),
      assetMaxAttempts: document.getElementById('assetMaxAttempts'),
      assetMinScore: document.getElementById('assetMinScore'),
      voiceMode: document.getElementById('voiceMode'),
      voicePreset: document.getElementById('voicePreset'),
      voiceReferenceFile: document.getElementById('voiceReferenceFile'),
      voiceReferenceAudioPath: document.getElementById('voiceReferenceAudioPath'),
      voiceReferenceText: document.getElementById('voiceReferenceText'),
      voiceCommandTemplate: document.getElementById('voiceCommandTemplate'),
      voiceTimeout: document.getElementById('voiceTimeout'),
      imageTool: document.getElementById('imageTool'),
      imagePositivePrompt: document.getElementById('imagePositivePrompt'),
      imageModel: document.getElementById('imageModel'),
      imageSize: document.getElementById('imageSize'),
      imageCount: document.getElementById('imageCount'),
      imageStyle: document.getElementById('imageStyle'),
      imageQuality: document.getElementById('imageQuality'),
      imageApiKey: document.getElementById('imageApiKey'),
      imageBaseUrl: document.getElementById('imageBaseUrl'),
      imageWorkflowEndpoint: document.getElementById('imageWorkflowEndpoint'),
      imageInstanceType: document.getElementById('imageInstanceType'),
      imageNodeInfoList: document.getElementById('imageNodeInfoList'),
      imagePollTimeout: document.getElementById('imagePollTimeout'),
      imageNegativePrompt: document.getElementById('imageNegativePrompt'),
      imageConsistency: document.getElementById('imageConsistency'),
      imageSeed: document.getElementById('imageSeed'),
      imageGuidance: document.getElementById('imageGuidance'),
      imageSteps: document.getElementById('imageSteps'),
      imageDenoise: document.getElementById('imageDenoise'),
      imageSampler: document.getElementById('imageSampler'),
      imageControl: document.getElementById('imageControl'),
      videoTool: document.getElementById('videoTool'),
      videoPositivePrompt: document.getElementById('videoPositivePrompt'),
      videoModel: document.getElementById('videoModel'),
      videoAspect: document.getElementById('videoAspect'),
      videoDuration: document.getElementById('videoDuration'),
      videoStyle: document.getElementById('videoStyle'),
      videoPromptNotes: document.getElementById('videoPromptNotes'),
      videoApiKey: document.getElementById('videoApiKey'),
      videoBaseUrl: document.getElementById('videoBaseUrl'),
      videoWorkflowEndpoint: document.getElementById('videoWorkflowEndpoint'),
      videoNodeInfoList: document.getElementById('videoNodeInfoList'),
      videoPollTimeout: document.getElementById('videoPollTimeout'),
      videoNegativePrompt: document.getElementById('videoNegativePrompt'),
      videoSeed: document.getElementById('videoSeed'),
      videoFps: document.getElementById('videoFps'),
      videoMotionStrength: document.getElementById('videoMotionStrength'),
      videoCameraMotion: document.getElementById('videoCameraMotion'),
      videoResolution: document.getElementById('videoResolution'),
      videoGuidance: document.getElementById('videoGuidance'),
      videoFrames: document.getElementById('videoFrames'),
      videoImageStrength: document.getElementById('videoImageStrength'),
      videoCameraPath: document.getElementById('videoCameraPath'),
      videoAudioNotes: document.getElementById('videoAudioNotes'),
      videoAdvancedParams: document.getElementById('videoAdvancedParams'),
      referenceImages: document.getElementById('referenceImages'),
      referenceRole: document.getElementById('referenceRole'),
      referenceNote: document.getElementById('referenceNote'),
      referenceList: document.getElementById('referenceList'),
      runBtn: document.getElementById('runBtn'),
      cancelRunBtn: document.getElementById('cancelRunBtn'),
      sampleBtn: document.getElementById('sampleBtn'),
      longVideoSampleBtn: document.getElementById('longVideoSampleBtn'),
      gameSampleBtn: document.getElementById('gameSampleBtn'),
      clearSettingsBtn: document.getElementById('clearSettingsBtn'),
      status: document.getElementById('status'),
      toast: document.getElementById('toast'),
      progressBox: document.getElementById('progressBox'),
      progressTitle: document.getElementById('progressTitle'),
      progressMeta: document.getElementById('progressMeta'),
      progressFill: document.getElementById('progressFill'),
      progressList: document.getElementById('progressList'),
      taskList: document.getElementById('taskList'),
      refreshTasks: document.getElementById('refreshTasks'),
      viewerTitle: document.getElementById('viewerTitle'),
      viewerMeta: document.getElementById('viewerMeta'),
      fileTabs: document.getElementById('fileTabs'),
      fileContent: document.getElementById('fileContent'),
      outputSummaryGrid: document.getElementById('outputSummaryGrid'),
      videoPreviewBox: document.getElementById('videoPreviewBox'),
      videoPreview: document.getElementById('videoPreview'),
      videoPreviewMeta: document.getElementById('videoPreviewMeta'),
      stepOutputMeta: document.getElementById('stepOutputMeta'),
      stepOutputList: document.getElementById('stepOutputList'),
      assetOutputMeta: document.getElementById('assetOutputMeta'),
      assetOutputList: document.getElementById('assetOutputList'),
      assetLightbox: document.getElementById('assetLightbox'),
      assetLightboxTitle: document.getElementById('assetLightboxTitle'),
      assetLightboxMeta: document.getElementById('assetLightboxMeta'),
      assetLightboxStage: document.getElementById('assetLightboxStage'),
      assetLightboxCounter: document.getElementById('assetLightboxCounter'),
      assetLightboxPrevBtn: document.getElementById('assetLightboxPrevBtn'),
      assetLightboxNextBtn: document.getElementById('assetLightboxNextBtn'),
      assetLightboxFavoriteBtn: document.getElementById('assetLightboxFavoriteBtn'),
      assetLightboxOpenBtn: document.getElementById('assetLightboxOpenBtn'),
      assetLightboxCloseBtn: document.getElementById('assetLightboxCloseBtn'),
      refreshAssetLibraryBtn: document.getElementById('refreshAssetLibraryBtn'),
      assetLibraryTagFilter: document.getElementById('assetLibraryTagFilter'),
      assetLibraryStatus: document.getElementById('assetLibraryStatus'),
      assetLibraryGrid: document.getElementById('assetLibraryGrid'),
      assetLibraryTabs: document.getElementById('assetLibraryTabs'),
      assetLibraryDetail: document.getElementById('assetLibraryDetail'),
      assetLibraryDetailPreview: document.getElementById('assetLibraryDetailPreview'),
      assetLibraryDetailName: document.getElementById('assetLibraryDetailName'),
      assetLibraryDetailCategory: document.getElementById('assetLibraryDetailCategory'),
      assetLibraryDetailNote: document.getElementById('assetLibraryDetailNote'),
      assetLibraryDetailMeta: document.getElementById('assetLibraryDetailMeta'),
      assetLibraryDetailCloseBtn: document.getElementById('assetLibraryDetailCloseBtn'),
      assetLibraryDetailOpenBtn: document.getElementById('assetLibraryDetailOpenBtn'),
      assetLibraryDetailDeleteBtn: document.getElementById('assetLibraryDetailDeleteBtn'),
      assetLibraryDetailSaveBtn: document.getElementById('assetLibraryDetailSaveBtn'),
      assetImportModal: document.getElementById('assetImportModal'),
      assetImportTitle: document.getElementById('assetImportTitle'),
      assetImportCloseBtn: document.getElementById('assetImportCloseBtn'),
      assetImportFile: document.getElementById('assetImportFile'),
      assetImportName: document.getElementById('assetImportName'),
      assetImportCategory: document.getElementById('assetImportCategory'),
      assetImportNote: document.getElementById('assetImportNote'),
      assetImportComfyBtn: document.getElementById('assetImportComfyBtn'),
      assetImportSaveBtn: document.getElementById('assetImportSaveBtn'),
      assetImportStatus: document.getElementById('assetImportStatus'),
      refreshComfyDebugBtn: document.getElementById('refreshComfyDebugBtn'),
      comfyDebugStatus: document.getElementById('comfyDebugStatus'),
      comfyDebugWorkflowList: document.getElementById('comfyDebugWorkflowList'),
      comfyDebugSelectedMeta: document.getElementById('comfyDebugSelectedMeta'),
      comfyDebugApiKey: document.getElementById('comfyDebugApiKey'),
      comfyDebugBaseUrl: document.getElementById('comfyDebugBaseUrl'),
      comfyDebugPollTimeout: document.getElementById('comfyDebugPollTimeout'),
      comfyDebugEndpoint: document.getElementById('comfyDebugEndpoint'),
      comfyDebugReferencePathField: document.getElementById('comfyDebugReferencePathField'),
      comfyDebugAssetTagFilterField: document.getElementById('comfyDebugAssetTagFilterField'),
      comfyDebugReferenceGrid: document.getElementById('comfyDebugReferenceGrid'),
      comfyDebugStartFrameCard: document.getElementById('comfyDebugStartFrameCard'),
      comfyDebugReference: document.getElementById('comfyDebugReference'),
      comfyDebugMiddleFrameReference: document.getElementById('comfyDebugMiddleFrameReference'),
      comfyDebugLastFrameReference: document.getElementById('comfyDebugLastFrameReference'),
      comfyDebugMaskImageField: document.getElementById('comfyDebugMaskImageField'),
      comfyDebugMaskImage: document.getElementById('comfyDebugMaskImage'),
      comfyDebugAudioFileField: document.getElementById('comfyDebugAudioFileField'),
      comfyDebugAudioFile: document.getElementById('comfyDebugAudioFile'),
      comfyDebugReferencePreview: document.getElementById('comfyDebugReferencePreview'),
      comfyDebugReferencePreviewMeta: document.getElementById('comfyDebugReferencePreviewMeta'),
      comfyDebugMiddleFrameCard: document.getElementById('comfyDebugMiddleFrameCard'),
      comfyDebugMiddleFramePreview: document.getElementById('comfyDebugMiddleFramePreview'),
      comfyDebugLastFrameCard: document.getElementById('comfyDebugLastFrameCard'),
      comfyDebugLastFramePreview: document.getElementById('comfyDebugLastFramePreview'),
      comfyDebugAssetTagFilter: document.getElementById('comfyDebugAssetTagFilter'),
      comfyDebugAssetReference: document.getElementById('comfyDebugAssetReference'),
      comfyDebugMiddleFrameAssetReference: document.getElementById('comfyDebugMiddleFrameAssetReference'),
      comfyDebugLastFrameAssetReference: document.getElementById('comfyDebugLastFrameAssetReference'),
      comfyDebugReferenceFile: document.getElementById('comfyDebugReferenceFile'),
      comfyDebugMiddleFrameReferenceFile: document.getElementById('comfyDebugMiddleFrameReferenceFile'),
      comfyDebugLastFrameReferenceFile: document.getElementById('comfyDebugLastFrameReferenceFile'),
      comfyDebugReferenceFileLabel: document.getElementById('comfyDebugReferenceFileLabel'),
      comfyDebugMiddleFrameReferenceFileLabel: document.getElementById('comfyDebugMiddleFrameReferenceFileLabel'),
      comfyDebugLastFrameReferenceFileLabel: document.getElementById('comfyDebugLastFrameReferenceFileLabel'),
      comfyDebugReferenceUploadState: document.getElementById('comfyDebugReferenceUploadState'),
      comfyDebugMiddleFrameUploadState: document.getElementById('comfyDebugMiddleFrameUploadState'),
      comfyDebugLastFrameUploadState: document.getElementById('comfyDebugLastFrameUploadState'),
      comfyDebugReferenceUploadName: document.getElementById('comfyDebugReferenceUploadName'),
      comfyDebugMiddleFrameUploadName: document.getElementById('comfyDebugMiddleFrameUploadName'),
      comfyDebugLastFrameUploadName: document.getElementById('comfyDebugLastFrameUploadName'),
      comfyDebugReferenceReuploadBtn: document.getElementById('comfyDebugReferenceReuploadBtn'),
      comfyDebugMiddleFrameReuploadBtn: document.getElementById('comfyDebugMiddleFrameReuploadBtn'),
      comfyDebugLastFrameReuploadBtn: document.getElementById('comfyDebugLastFrameReuploadBtn'),
      clearComfyDebugReferenceBtn: document.getElementById('clearComfyDebugReferenceBtn'),
      comfyDebugReferenceHint: document.getElementById('comfyDebugReferenceHint'),
      comfyDebugMiddleFrameReferenceHint: document.getElementById('comfyDebugMiddleFrameReferenceHint'),
      comfyDebugLastFrameReferenceHint: document.getElementById('comfyDebugLastFrameReferenceHint'),
      comfyDebugWorkflowMode: document.getElementById('comfyDebugWorkflowMode'),
      comfyDebugSeed: document.getElementById('comfyDebugSeed'),
      comfyDebugWidth: document.getElementById('comfyDebugWidth'),
      comfyDebugHeight: document.getElementById('comfyDebugHeight'),
      comfyDebugDuration: document.getElementById('comfyDebugDuration'),
      comfyDebugDurationField: document.getElementById('comfyDebugDurationField'),
      comfyDebugFrameCountHint: document.getElementById('comfyDebugFrameCountHint'),
      comfyDebugFps: document.getElementById('comfyDebugFps'),
      comfyDebugFpsField: document.getElementById('comfyDebugFpsField'),
      comfyDebugPrompt: document.getElementById('comfyDebugPrompt'),
      comfyDebugNegative: document.getElementById('comfyDebugNegative'),
      comfyDebugNodeInfoList: document.getElementById('comfyDebugNodeInfoList'),
      comfyDebugApiWorkflowFile: document.getElementById('comfyDebugApiWorkflowFile'),
      runComfyDebugBtn: document.getElementById('runComfyDebugBtn'),
      comfyDebugResults: document.getElementById('comfyDebugResults'),
      comfyDebugResultMeta: document.getElementById('comfyDebugResultMeta'),
      packageOutputMeta: document.getElementById('packageOutputMeta'),
      packageOutputList: document.getElementById('packageOutputList'),
      stepConfirmBar: document.getElementById('stepConfirmBar'),
      stepConfirmTitle: document.getElementById('stepConfirmTitle'),
      stepConfirmHint: document.getElementById('stepConfirmHint'),
      confirmStepContinueBtn: document.getElementById('confirmStepContinueBtn'),
      confirmStepRerunBtn: document.getElementById('confirmStepRerunBtn'),
      taskComfyDebugPanel: document.getElementById('taskComfyDebugPanel'),
      taskComfyDebugMeta: document.getElementById('taskComfyDebugMeta'),
      taskComfyDebugList: document.getElementById('taskComfyDebugList'),
      saveFileBtn: document.getElementById('saveFileBtn'),
      rebuildFinalBtn: document.getElementById('rebuildFinalBtn'),
      rerunStepBtn: document.getElementById('rerunStepBtn'),
      resumeTaskBtn: document.getElementById('resumeTaskBtn'),
      outputCancelRunBtn: document.getElementById('outputCancelRunBtn'),
      exportTaskBtn: document.getElementById('exportTaskBtn'),
      showDebugFiles: document.getElementById('showDebugFiles'),
      refreshStaffBtn: document.getElementById('refreshStaffBtn'),
      newStaffBtn: document.getElementById('newStaffBtn'),
      deleteStaffBtn: document.getElementById('deleteStaffBtn'),
      saveStaffBtn: document.getElementById('saveStaffBtn'),
      staffStatus: document.getElementById('staffStatus'),
      staffFilter: document.getElementById('staffFilter'),
      showArchivedStaff: document.getElementById('showArchivedStaff'),
      staffList: document.getElementById('staffList'),
      staffName: document.getElementById('staffName'),
      staffAgentMd: document.getElementById('staffAgentMd'),
      staffFlowRule: document.getElementById('staffFlowRule'),
      refreshWorkflowsBtn: document.getElementById('refreshWorkflowsBtn'),
      newWorkflowBtn: document.getElementById('newWorkflowBtn'),
      addWorkflowStepBtn: document.getElementById('addWorkflowStepBtn'),
      deleteWorkflowBtn: document.getElementById('deleteWorkflowBtn'),
      saveWorkflowBtn: document.getElementById('saveWorkflowBtn'),
      workflowEditorStatus: document.getElementById('workflowEditorStatus'),
      showArchivedWorkflows: document.getElementById('showArchivedWorkflows'),
      workflowList: document.getElementById('workflowList'),
      workflowFile: document.getElementById('workflowFile'),
      workflowName: document.getElementById('workflowName'),
      workflowDescription: document.getElementById('workflowDescription'),
      workflowSteps: document.getElementById('workflowSteps'),
      taskSidebar: document.getElementById('taskSidebar'),
      refreshHealthBtn: document.getElementById('refreshHealthBtn'),
      healthStatus: document.getElementById('healthStatus'),
      healthGrid: document.getElementById('healthGrid'),
    };
    function detachedControl(tag = 'input', value = '') {
      const el = document.createElement(tag);
      el.value = value;
      return el;
    }
    function detachedSelect(value = '') {
      const el = document.createElement('select');
      const option = document.createElement('option');
      option.value = value;
      option.textContent = value;
      el.appendChild(option);
      el.value = value;
      return el;
    }
    Object.assign(els, {
      imageTool: els.imageTool || detachedSelect('prompt_only'),
      imagePositivePrompt: els.imagePositivePrompt || detachedControl('input', ''),
      imageModel: els.imageModel || detachedControl('input', ''),
      imageSize: els.imageSize || detachedSelect('16:9'),
      imageCount: els.imageCount || detachedSelect('1'),
      imageStyle: els.imageStyle || detachedControl('input', ''),
      imageQuality: els.imageQuality || detachedSelect('standard'),
      imageApiKey: els.imageApiKey || detachedControl('input', ''),
      imageBaseUrl: els.imageBaseUrl || detachedControl('input', ''),
      imageWorkflowEndpoint: els.imageWorkflowEndpoint || detachedControl('input', ''),
      imageInstanceType: els.imageInstanceType || detachedSelect('default'),
      imageNodeInfoList: els.imageNodeInfoList || detachedControl('textarea', ''),
      imagePollTimeout: els.imagePollTimeout || detachedSelect('900'),
      imageNegativePrompt: els.imageNegativePrompt || detachedControl('input', ''),
      imageConsistency: els.imageConsistency || detachedControl('input', ''),
      imageSeed: els.imageSeed || detachedControl('input', ''),
      imageGuidance: els.imageGuidance || detachedControl('input', ''),
      imageSteps: els.imageSteps || detachedControl('input', ''),
      imageDenoise: els.imageDenoise || detachedControl('input', ''),
      imageSampler: els.imageSampler || detachedControl('input', ''),
      imageControl: els.imageControl || detachedControl('input', ''),
      videoTool: els.videoTool || detachedSelect('prompt_only'),
      videoPositivePrompt: els.videoPositivePrompt || detachedControl('input', ''),
      videoModel: els.videoModel || detachedControl('input', ''),
      videoAspect: els.videoAspect || detachedSelect('16:9'),
      videoDuration: els.videoDuration || detachedSelect('custom'),
      videoStyle: els.videoStyle || detachedControl('input', ''),
      videoPromptNotes: els.videoPromptNotes || detachedControl('input', ''),
      videoApiKey: els.videoApiKey || detachedControl('input', ''),
      videoBaseUrl: els.videoBaseUrl || detachedControl('input', ''),
      videoWorkflowEndpoint: els.videoWorkflowEndpoint || detachedControl('input', ''),
      videoNodeInfoList: els.videoNodeInfoList || detachedControl('textarea', ''),
      videoPollTimeout: els.videoPollTimeout || detachedSelect('1800'),
      videoNegativePrompt: els.videoNegativePrompt || detachedControl('input', ''),
      videoSeed: els.videoSeed || detachedControl('input', ''),
      videoFps: els.videoFps || detachedSelect('30'),
      videoMotionStrength: els.videoMotionStrength || detachedSelect('medium'),
      videoCameraMotion: els.videoCameraMotion || detachedSelect('push_in'),
      videoResolution: els.videoResolution || detachedSelect('1080p'),
      videoGuidance: els.videoGuidance || detachedControl('input', ''),
      videoFrames: els.videoFrames || detachedControl('input', ''),
      videoImageStrength: els.videoImageStrength || detachedControl('input', ''),
      videoCameraPath: els.videoCameraPath || detachedControl('input', ''),
      videoAudioNotes: els.videoAudioNotes || detachedControl('input', ''),
      videoAdvancedParams: els.videoAdvancedParams || detachedControl('input', ''),
      referenceImages: els.referenceImages || detachedControl('input', ''),
      referenceRole: els.referenceRole || detachedSelect('视觉风格参考'),
      referenceNote: els.referenceNote || detachedControl('input', ''),
      referenceList: els.referenceList || document.createElement('div'),
    });
    const navButtons = Array.from(document.querySelectorAll('[data-view-target]'));
    const views = Array.from(document.querySelectorAll('[data-view]'));
    let selectedTask = null;
    let selectedFile = null;
    let selectedTaskSummary = {};
    let selectedTaskStatus = null;
    let selectedTaskAllowedActions = [];
    let currentTaskFiles = [];
    let selectedStaff = null;
    let selectedWorkflow = null;
    let workflowEditorSteps = [];
    let workflowEditorBase = {};
    let staffOptions = [];
    let selectedReferenceFiles = [];
    let referencePreviewUrls = new Map();
    let comfyParameterCandidates = [];
    let progressTimer = null;
    let currentRunId = "";
    let currentRunStatus = "";
    let autoFocusOutputDuringRun = false;
    let activeRunTaskName = "";
    let workflowInteractionLocked = false;
    let lastTaskDetailRefreshAt = 0;
    let assetPreviewItems = [];
    let assetPreviewTaskName = "";
    let assetPreviewIndex = 0;
    let assetLibraryItems = [];
    let assetLibrarySection = 'all';
    let selectedAssetLibraryId = '';
    let assetLibraryDetailDirty = false;
    const ASSET_LIBRARY_SECTIONS = [
      { value: 'all', label: '全部', addLabel: '新增资产', tags: [] },
      { value: 'material', label: '素材', addLabel: '新增素材', tags: ['scene', 'broll', 'bgm', 'music', 'cover', 'scene_base', 'cover_key_visual', 'image_inpaint_fix', 'background_remove', 'broll_scene_video', 'empty_transition_video', 'video_upscale', 'frame_interpolation', 'video_deflicker_stabilize', 'video_inpaint_fix'] },
      { value: 'character', label: '角色', addLabel: '新增角色', tags: ['person', 'character_base', 'character_turnaround', 'character_generation'] },
      { value: 'product', label: '商品', addLabel: '新增商品', tags: ['product', 'product_base', 'product_turnaround', 'product_generation'] },
      { value: 'reference', label: '参考', addLabel: '新增参考', tags: ['style', 'style_reference', 'keyframe', 'reference', 'i2v_first_frame', 'i2v_first_last_frame', 'i2v_first_middle_last_frame', 'live_to_anime', 'motion_transfer', 'talking_image'] },
    ];
    const ASSET_CATEGORY_TAGS = [
      { value: 'person', label: '人物' },
      { value: 'product', label: '产品' },
      { value: 'scene', label: '场景' },
      { value: 'broll', label: 'B-roll' },
      { value: 'bgm', label: 'BGM 配乐' },
      { value: 'cover', label: '封面' },
      { value: 'style', label: '风格参考' },
      { value: 'keyframe', label: '关键帧' },
      { value: 'reference', label: '参考图' },
      { value: 'character_base', label: '01 角色基础图' },
      { value: 'product_base', label: '02 产品基础图' },
      { value: 'scene_base', label: '03 场景基础图' },
      { value: 'character_turnaround', label: '04 角色三视图' },
      { value: 'product_turnaround', label: '05 产品三视图' },
      { value: 'cover_key_visual', label: '08 封面关键视觉' },
      { value: 'image_inpaint_fix', label: '09 图片修复' },
      { value: 'background_remove', label: '10 抠图透明素材' },
      { value: 'i2v_first_frame', label: '11 首帧视频' },
      { value: 'i2v_first_last_frame', label: '12 首尾帧视频' },
      { value: 'i2v_first_middle_last_frame', label: '12 首中尾帧视频' },
      { value: 'live_to_anime', label: '13 真人转动漫' },
      { value: 'motion_transfer', label: '14 动作迁移' },
      { value: 'talking_image', label: '15 图片说话' },
      { value: 'broll_scene_video', label: '16 B-roll 场景视频' },
      { value: 'empty_transition_video', label: '17 空镜转场' },
      { value: 'video_upscale', label: '18 视频放大' },
      { value: 'frame_interpolation', label: '19 视频补帧' },
      { value: 'video_deflicker_stabilize', label: '20 去闪烁稳定' },
      { value: 'video_inpaint_fix', label: '21 视频局部修复' },
    ];
    const COMFY_IMAGE_TASK_TYPES = [
      { value: 'character_generation', label: '角色生成', taskType: 'character_generation', controlMode: 'none', requiresReference: false, prompt: '生成统一角色设定图：单个专业人物角色，正面半身，服装、发型、气质清晰，写实商业风格，干净背景，无文字水印。' },
      { value: 'product_generation', label: '产品生成', taskType: 'product_generation', controlMode: 'none', requiresReference: false, prompt: '生成产品主体图：产品外观清晰，材质、颜色、比例稳定，商业摄影风格，干净背景，无文字水印。' },
      { value: 'scene_generation', label: '场景生成', taskType: 'scene_generation', controlMode: 'none', requiresReference: false, prompt: '生成可复用场景图：办公、科技、行业业务场景，空间层次清楚，适合后续人物或产品合成，写实风格，无文字水印。' },
      { value: 'character_turnaround', label: '角色三视图', taskType: 'character_turnaround', controlMode: 'character_reference', requiresReference: true, prompt: '基于参考图生成角色三视图：同一人物正面、侧面、背面，服装发型一致，比例稳定，干净白底或浅色背景，无文字水印。' },
      { value: 'product_turnaround', label: '产品三视图', taskType: 'product_turnaround', controlMode: 'product_reference', requiresReference: true, prompt: '基于参考图生成产品三视图：同一产品正面、侧面、背面，材质颜色一致，结构准确，干净背景，无文字水印。' },
      { value: 'keyframe', label: '关键帧', taskType: 'keyframe', controlMode: 'none', requiresReference: false, prompt: '根据分镜文本生成视频关键帧：单张画面，构图适合后续图生视频，写实商业风格，无文字水印。' },
      { value: 'cover_key_visual', label: '封面关键视觉', taskType: 'cover_key_visual', controlMode: 'style_reference', requiresReference: false, prompt: '生成封面关键视觉：主体明确，构图有冲击力，适合横屏视频封面，预留标题安全区，写实商业科技风格，无文字水印。' },
      { value: 'style_reference', label: '风格参考图', taskType: 'style_reference', controlMode: 'none', requiresReference: false, prompt: '生成统一风格参考图：色彩、光线、材质和画面气质明确，可作为后续整条视频的视觉风格基准，无文字水印。' },
      { value: 'inpaint_fix', label: '局部修复/重绘', taskType: 'inpaint_fix', controlMode: 'mask_inpaint', requiresReference: true, prompt: '基于参考图进行局部修复或重绘：修正脸部、手部、文字、水印或局部瑕疵，保持原图主体和风格一致。' },
    ];
    let comfyDebugWorkflows = [];
    let activeComfyDebugWorkflowId = '';
    let activeComfyDebugWorkflowMode = '';
    const comfyDebugStateByWorkflowId = new Map();
    const comfyDebugCollapsedCapabilityGroups = new Set();
    const COMFY_DEBUG_CAPABILITY_GROUPS = [
      { id: 'asset_image', label: '01 基础资产', modes: ['character_base', 'product_base', 'scene_base', 'style_reference', 'character_turnaround', 'product_turnaround', 'cover_key_visual'] },
      { id: 'storyboard_keyframe', label: '02 分镜关键帧', modes: ['keyframe'] },
      { id: 'image_post', label: '03 图片处理', modes: ['image_inpaint_fix', 'background_remove'] },
      { id: 'video_creation', label: '04 视频生成', modes: ['i2v_first_frame', 'i2v_first_middle_last_frame', 'i2v_first_last_frame', 'broll_scene_video', 'empty_transition_video'] },
      { id: 'video_control', label: '05 视频控制', modes: ['live_to_anime', 'motion_transfer'] },
      { id: 'digital_human', label: '06 数字人口播', modes: ['talking_image'] },
      { id: 'video_post', label: '07 视频后期', modes: ['video_upscale', 'frame_interpolation', 'video_deflicker_stabilize', 'video_inpaint_fix'] },
    ];
    let comfyDebugFormHydrated = false;
    const comfyDebugPollTimers = new Map();
    let comfyDebugElapsedTimer = null;
    let settingsRestoring = false;
    let comfyDebugLastResults = [];
    const progressStepOpenState = new Map();
    const progressUserToggledSteps = new Set();
    let localModelPresets = [];
    const DEFAULT_LOCAL_MODEL = 'qwen3:8b-q4_K_M';
    const OLLAMA_BASE_URL = 'http://127.0.0.1:11434/v1';
    const SETTINGS_KEY = 'my_workspace.workflow_settings.v2';
    let comfyWorkflowLibrary = [];
    const DEFAULT_COMFY_WORKFLOW_PRESET_ID = 'all_in_one_image';
    const DEFAULT_COMFY_WORKFLOW_LIBRARY = [
      {
        id: 'all_in_one_image',
        name: '全能图片',
        purpose: '统一图片 API：文生图、图生图、关键帧、封面、人物/产品/风格参考统一从 image_jobs 调用',
        materialTypes: ['image'],
        endpoint: '',
        nodeInfoList: JSON.stringify([
          { nodeId: '10', fieldName: 'text', fieldValue: '{{prompt}}' },
          { nodeId: '11', fieldName: 'text', fieldValue: '{{negative_prompt}}' },
          { nodeId: '12', fieldName: 'image', fieldValue: '{{reference_image}}' },
          { nodeId: '63', fieldName: 'switch', fieldValue: '{{has_reference_image}}' },
          { nodeId: '20', fieldName: 'width', fieldValue: '{{width}}' },
          { nodeId: '20', fieldName: 'height', fieldValue: '{{height}}' },
          { nodeId: '40', fieldName: 'filename_prefix', fieldValue: 'all_in_one_image' },
        ], null, 2),
        pollTimeout: '3600',
      },
      {
        id: 'all_in_one_video',
        name: '全能视频',
        purpose: '统一视频 API：文生视频、图生视频、首帧视频、B-roll、转场、人物/产品参考统一从 video_jobs 调用',
        materialTypes: ['video'],
        endpoint: '',
        nodeInfoList: JSON.stringify([
          { nodeId: '2483', fieldName: 'text', fieldValue: 'Use the reference image as strict person identity control. Keep the same face shape, facial features, hairstyle, body proportions, outfit, product appearance, color palette and subject silhouette. {{prompt}}' },
          { nodeId: '2612', fieldName: 'text', fieldValue: 'identity drift, different person, different face, changed facial features, changed hairstyle, changed outfit, inconsistent body proportions, distorted face, deformed body, bad hands, inconsistent product, {{negative_prompt}}' },
          { nodeId: '2004', fieldName: 'image', fieldValue: '{{reference_image}}' },
          { nodeId: '4977', fieldName: 'value', fieldValue: false },
          { nodeId: '3159', fieldName: 'strength', fieldValue: 1.0 },
          { nodeId: '4979', fieldName: 'value', fieldValue: 121 },
          { nodeId: '3059', fieldName: 'width', fieldValue: 960 },
          { nodeId: '3059', fieldName: 'height', fieldValue: 544 },
          { nodeId: '3059', fieldName: 'length', fieldValue: 121 },
          { nodeId: '4823', fieldName: 'filename_prefix', fieldValue: 'all_in_one_video' },
          { nodeId: '4852', fieldName: 'filename_prefix', fieldValue: 'all_in_one_video' },
        ], null, 2),
        pollTimeout: '3600',
      },
    ];
    const LONG_VIDEO_WORKFLOW_STEM = 'workflow_长视频全流程';
    const ACTIVE_STAFF_PREFIXES = ['01_', '03_', '04_', '05_', '06_', '07_', '20_', '22_', '23_'];
    function isActiveLongVideoWorkflow(workflow) {
      if (workflow && workflow.archived === false) return true;
      if (workflow && workflow.archived === true) return false;
      const stem = workflow?.stem || workflow?.name || '';
      return stem === LONG_VIDEO_WORKFLOW_STEM || String(stem).includes('长视频全流程');
    }
    function isActiveLongVideoStaff(staff) {
      if (staff && staff.archived === false) return true;
      if (staff && staff.archived === true) return false;
      const name = staff?.name || staff || '';
      return ACTIVE_STAFF_PREFIXES.some(prefix => String(name).startsWith(prefix));
    }
    const PRODUCT_TEMPLATES = {
      long_video: {
        workflow: LONG_VIDEO_WORKFLOW_STEM,
        taskTitle: '长视频内容生产',
        sample: '我要做一条 12-18 分钟的长视频，主题是“中小企业如何用 AI 员工工作流平台降低重复劳动”。目标平台是 B 站和视频号，目标观众是中小企业老板、运营负责人和想做 AI 自动化服务的人。视频要专业、清晰、有案例感，结构包括痛点、平台演示、落地步骤、成本和风险、最后引导私信咨询。可用素材包括管理台录屏、工作流输出截图、本人配音和少量 AI 示意图；不要夸大收益，不承诺具体增长结果。',
        autoProductionMode: 'comfy_full',
        imageSize: '16:9',
        videoAspect: '16:9',
        videoDuration: 'custom',
      },
    };

    let toastTimer = null;

    function showToast(text, isError = false) {
      if (!els.toast || !text) return;
      els.toast.textContent = text;
      els.toast.classList.toggle('error', isError);
      els.toast.classList.add('show');
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(() => {
        els.toast.classList.remove('show');
      }, isError ? 5200 : 2600);
    }

    function setStatus(text, isError = false, showPopup = true) {
      els.status.textContent = text;
      els.status.classList.toggle('error', isError);
      if (showPopup) showToast(text, isError);
    }

    function buttonLabel(button) {
      return (button.textContent || button.title || button.getAttribute('aria-label') || '按钮')
        .replace(/\s+/g, ' ')
        .trim();
    }

    function showButtonFeedback(button) {
      if (!button || button.disabled) return;
      const label = buttonLabel(button);
      if (!label) return;
      if (button.dataset.viewTarget) {
        setStatus(`正在切换：${label}`, false, false);
        return;
      }
      const currentView = document.body.dataset.view || 'run';
      const message = `正在处理：${label}`;
      if (currentView === 'staff') {
        setStaffStatus(message);
      } else if (currentView === 'workflow') {
        setWorkflowEditorStatus(message);
      } else if (currentView === 'system') {
        setHealthStatus(message);
      } else {
        setStatus(message);
      }
    }

    function bindButtonClickFeedback() {
      document.addEventListener('click', event => {
        const button = event.target.closest('button');
        if (!button) return;
        showButtonFeedback(button);
      }, true);
    }

    function moveConfigSections() {
      const configSections = document.getElementById('configSections') || els.configSections;
      if (!configSections) return;
      const modelRuntimeConfig = document.getElementById('modelRuntimeConfig');
      const firstConfigBody = document.querySelector('[data-config-section] .details-body');
      if (modelRuntimeConfig && firstConfigBody) {
        firstConfigBody.insertBefore(modelRuntimeConfig, firstConfigBody.firstChild);
      }
      document.querySelectorAll('[data-config-section]').forEach(section => {
        configSections.appendChild(section);
        section.hidden = false;
      });
    }

    function showView(viewName) {
      document.body.dataset.view = viewName;
      if (viewName === 'config') {
        moveConfigSections();
      }
      for (const view of views) {
        view.hidden = view.dataset.view !== viewName;
      }
      for (const btn of navButtons) {
        btn.classList.toggle('active', btn.dataset.viewTarget === viewName);
      }
      els.taskSidebar.hidden = viewName !== 'output';
      if (viewName === 'output') {
        loadTasks()
          .then(tasks => {
            if (!selectedTask && tasks.length) return selectTask(tasks[0].name);
            if (selectedTask) return refreshSelectedTaskDetail({ openMissingFile: true });
          })
          .catch(err => setStatus(err.message, true));
      }
      if (viewName === 'system') {
        loadSystemHealth().catch(err => setHealthStatus(err.message, true));
      }
      if (viewName === 'workflow') {
        loadWorkflowList().catch(err => setWorkflowEditorStatus(err.message, true));
      }
      if (viewName === 'assets') {
        loadAssetLibrary();
      }
      if (viewName === 'comfyDebug') {
        loadAssetLibrary();
        loadComfyDebugWorkflows();
      }
    }

    function setHealthStatus(text, isError = false, showPopup = true) {
      els.healthStatus.textContent = text;
      els.healthStatus.classList.toggle('error', isError);
      if (showPopup) showToast(text, isError);
    }

    function setRunButtonProgress(percent = 0, label = '') {
      const safePercent = Math.max(0, Math.min(100, Math.round(Number(percent) || 0)));
      if (!els.runBtn) return;
      if (!label && safePercent <= 0) {
        els.runBtn.classList.remove('run-progress');
        els.runBtn.style.removeProperty('--run-progress');
        els.runBtn.textContent = '开始生成';
        return;
      }
      els.runBtn.classList.add('run-progress');
      els.runBtn.style.setProperty('--run-progress', `${safePercent}%`);
      els.runBtn.textContent = label || `运行中 ${safePercent}%`;
    }

    function syncRunControlButtons() {
      const hasRun = Boolean(currentRunId && ['queued', 'running'].includes(currentRunStatus));
      if (els.cancelRunBtn) {
        els.cancelRunBtn.hidden = true;
        els.cancelRunBtn.disabled = true;
      }
      if (els.outputCancelRunBtn) {
        els.outputCancelRunBtn.hidden = !hasRun;
        els.outputCancelRunBtn.disabled = !hasRun;
      }
    }

    function maybeShowOutput() {
      if (autoFocusOutputDuringRun || document.body.dataset.view === 'output') {
        showView('output');
        return true;
      }
      return false;
    }

    function setWorkflowInteractionLocked(locked) {
      workflowInteractionLocked = Boolean(locked);
      const runView = document.querySelector('[data-view="run"]');
      const composer = document.querySelector('.run-composer');
      if (composer) composer.classList.toggle('is-locked', workflowInteractionLocked);
      for (const button of navButtons) {
        button.disabled = false;
      }
      if (runView) {
        for (const control of runView.querySelectorAll('input, select, textarea, button')) {
          if (control === els.cancelRunBtn) {
            control.disabled = true;
            control.hidden = true;
          } else {
            control.disabled = workflowInteractionLocked;
          }
        }
      }
      for (const control of document.querySelectorAll('[data-config-section] input, [data-config-section] select, [data-config-section] textarea, [data-config-section] button')) {
        control.disabled = workflowInteractionLocked;
      }
      if (!workflowInteractionLocked && els.runBtn) els.runBtn.disabled = false;
      syncRunControlButtons();
      syncOutputButtons();
    }

    function showStartupProgress(label = '启动中') {
      els.progressBox.hidden = false;
      els.progressTitle.textContent = label;
      els.progressMeta.textContent = '准备连接模型/API';
      els.progressFill.style.width = '1%';
      setRunButtonProgress(1, label);
    }

    function resetProgress() {
      if (progressTimer) {
        clearTimeout(progressTimer);
        progressTimer = null;
      }
      currentRunId = "";
      progressStepOpenState.clear();
      progressUserToggledSteps.clear();
      syncRunControlButtons();
      els.progressBox.hidden = true;
      els.progressTitle.textContent = '等待运行';
      els.progressMeta.textContent = '0/0';
      els.progressFill.style.width = '0%';
      els.progressList.innerHTML = '';
      setRunButtonProgress(0);
    }

    function trackRun(runId) {
      if (currentRunId !== (runId || "")) {
        progressStepOpenState.clear();
        progressUserToggledSteps.clear();
      }
      currentRunId = runId || "";
      if (!currentRunId) {
        activeRunTaskName = "";
        currentRunStatus = "";
      }
      syncRunControlButtons();
      syncOutputButtons();
    }

    function progressStepKey(job, stepNo) {
      return `${job.run_id || currentRunId || 'run'}:${stepNo}`;
    }

    function progressProductionStep(event, steps) {
      const stage = String(event.stage || '').toLowerCase();
      const message = String(event.message || '').toLowerCase();
      const findStep = keywords => {
        const hit = (steps || []).find(step => {
          const name = `${step.agent_name || ''} ${step.agent_id || ''} ${step.task || ''}`.toLowerCase();
          return keywords.some(keyword => name.includes(keyword));
        });
        return hit ? Number(hit.step || 0) : 0;
      };
      if (stage.includes('comfy') || stage.includes('runninghub') || stage.includes('material') || message.includes('runninghub') || message.includes('comfy')) {
        return findStep(['comfy', '素材', 'material']) || 9;
      }
      if (stage.includes('tts') || stage.includes('voice') || stage.includes('audio')) {
        return findStep(['语音', '字幕', 'tts', 'audio']) || 8;
      }
      if (stage.includes('ffmpeg') || stage.includes('compose') || stage.includes('production') || stage.includes('package')) {
        return findStep(['剪辑', '成片', 'ffmpeg', 'compose']) || (steps || []).length || 10;
      }
      return Number(event.step || 0) || Number((steps || []).find(step => step.status === 'active')?.step || 0) || 1;
    }

    function compactProgressMeta(event) {
      const parts = [];
      const push = (label, value) => {
        if (value === undefined || value === null || value === '') return;
        parts.push(`${label}: ${value}`);
      };
      push('阶段', event.stage);
      push('接口', event.endpoint);
      push('任务ID', event.task_id || event.taskId);
      push('远端状态', event.remote_status);
      push('素材', event.current_job && event.total_jobs ? `${event.current_job}/${event.total_jobs}` : event.current_job);
      push('完成素材', event.completed_jobs && event.total_jobs ? `${event.completed_jobs}/${event.total_jobs}` : event.completed_jobs);
      push('成功', event.success_count);
      push('失败', event.failed_count);
      push('已下载', event.downloaded_count);
      push('文件', event.downloaded_file || event.output_file);
      push('类型', event.output_type || event.job_type);
      push('质量分', event.quality_score);
      push('错误', event.error);
      return parts.join(' | ');
    }

    function progressDetailsByStep(job, steps) {
      const grouped = new Map();
      const add = (stepNo, item) => {
        const safeStep = Number(stepNo || 0) || 1;
        if (!grouped.has(safeStep)) grouped.set(safeStep, []);
        grouped.get(safeStep).push(item);
      };
      for (const event of job.detail_events || []) {
        const kind = event.kind || 'active';
        if (!['active', 'done', 'error'].includes(kind)) continue;
        const eventStep = Number(event.step || 0);
        if (!eventStep) continue;
        add(eventStep, {
          kind,
          title: kind === 'error' ? '步骤错误' : kind === 'done' ? '步骤完成' : '步骤进度',
          message: event.message || '',
          meta: '',
          updated_at: Number(event.updated_at || 0),
        });
      }
      for (const event of job.production_events || []) {
        const statusText = String(event.status || event.job_status || '').toLowerCase();
        const isError = Boolean(event.error) || statusText.includes('failed') || statusText.includes('timeout') || statusText.includes('error');
        const isDone = ['success', 'partial_success', 'final_video_generated', 'skipped', 'downloaded'].includes(statusText);
        add(progressProductionStep(event, steps), {
          kind: isError ? 'error' : isDone ? 'done' : 'active',
          title: event.stage && String(event.stage).toLowerCase().includes('comfy') ? 'RunningHub / ComfyUI 明细' : '自动生成明细',
          message: event.message || '',
          meta: compactProgressMeta(event),
          updated_at: Number(event.updated_at || 0),
        });
      }
      if (false && job.error) {
        add(job.current_step || (steps || []).find(step => step.status === 'error')?.step || (steps || []).length || 1, {
          kind: 'error',
          title: '错误',
          message: job.error,
          meta: '',
          updated_at: Date.now() / 1000,
        });
      }
      if (job.error) {
        add(job.current_step || (steps || []).find(step => step.status === 'error')?.step || (steps || []).length || 1, {
          kind: 'error',
          title: '任务错误',
          message: job.error,
          meta: '',
          updated_at: Date.now() / 1000,
        });
      }
      for (const items of grouped.values()) {
        items.sort((a, b) => (a.updated_at || 0) - (b.updated_at || 0));
      }
      return grouped;
    }

    function currentProgressStep(job, steps) {
      const items = steps || [];
      const explicitStep = Number(job.rerun_step || job.awaiting_confirmation_step || job.current_step || 0);
      if (explicitStep) {
        const found = items.find(step => Number(step.step || 0) === explicitStep);
        if (found) return found;
      }
      return items.find(step => step.status === 'active')
        || items.find(step => step.status === 'error')
        || items.find(step => step.status === 'pending')
        || [...items].reverse().find(step => step.status === 'done')
        || null;
    }

    function renderProgress(job) {
      currentRunStatus = String(job?.status || currentRunStatus || "");
      els.progressBox.hidden = false;
      const total = job.total_steps || 0;
      const completed = job.completed_steps || 0;
      const percent = total ? Math.round((completed / total) * 100) : 0;
      const productionMessage = job.production_message || '';
      const currentMessage = job.current_message || productionMessage || '';
      const statusText = {
        queued: '排队中',
        running: '运行中',
        completed: '已完成',
        failed: '失败',
        paused: '已暂停',
        cancelled: '已终止',
      }[job.status] || job.status || '运行中';
      const jobTitle = job.task_title || job.workflow_name || '';
      els.progressTitle.textContent = `${statusText}${jobTitle ? `：${jobTitle}` : ''}`;
      els.progressMeta.textContent = `${completed}/${total} 步 · ${percent}%${currentMessage ? ` · ${currentMessage}` : ''}`;
      els.progressFill.style.width = `${percent}%`;
      if (job.status === 'running' || job.status === 'queued') {
        setRunButtonProgress(percent || 1, `${percent || 1}%`);
      } else if (job.status === 'completed') {
        setRunButtonProgress(100, '完成');
      } else if (job.status === 'paused') {
        setRunButtonProgress(percent, '待确认');
      } else {
        setRunButtonProgress(0);
      }
      els.progressList.innerHTML = '';
      if (job.rerun && job.rerun_step) {
        els.progressTitle.textContent = `重跑第 ${job.rerun_step} 步${jobTitle ? ` - ${jobTitle}` : ''}`;
      }

      const steps = job.steps || [];
      const detailsByStep = progressDetailsByStep(job, steps);
      const currentStep = currentProgressStep(job, steps);
      const visibleSteps = currentStep ? [currentStep] : [];
      for (const step of visibleSteps) {
        const stepNo = Number(step.step || 0);
        const detailItems = detailsByStep.get(stepNo) || [];
        const stepKey = progressStepKey(job, stepNo);
        const hasUserState = progressUserToggledSteps.has(stepKey);
        const wrapper = document.createElement('details');
        wrapper.className = `progress-step-wrap ${detailItems.length ? 'has-details' : ''}`;
        wrapper.open = hasUserState
          ? Boolean(progressStepOpenState.get(stepKey))
          : false;
        const item = document.createElement('summary');
        item.className = `progress-step ${step.status || ''}`;
        const left = document.createElement('span');
        left.className = 'progress-step-main';
        if (detailItems.length) {
          const toggle = document.createElement('span');
          toggle.className = 'progress-step-toggle';
          toggle.textContent = wrapper.open ? '−' : '+';
          wrapper.addEventListener('toggle', () => {
            progressUserToggledSteps.add(stepKey);
            progressStepOpenState.set(stepKey, wrapper.open);
            toggle.textContent = wrapper.open ? '−' : '+';
          });
          left.appendChild(toggle);
        }
        const titleNode = document.createElement('span');
        titleNode.className = 'progress-step-title';
        const taskText = step.task ? ` - ${step.task}` : '';
        titleNode.textContent = `${step.step}. ${step.agent_name || step.agent_id || '等待中'}${taskText}`;
        left.appendChild(titleNode);
        const right = document.createElement('span');
        right.className = 'muted small progress-step-status';
        const elapsedText = step.elapsed_seconds ? ` · ${step.elapsed_seconds}s` : '';
        right.textContent = step.message || (step.status === 'done' ? `完成${elapsedText}` : step.status === 'active' ? '执行中' : step.status === 'error' ? '失败' : '等待');
        item.appendChild(left);
        item.appendChild(right);
        wrapper.appendChild(item);
        if (detailItems.length) {
          const detailList = document.createElement('div');
          detailList.className = 'progress-detail-list';
          for (const detail of detailItems.slice(-30)) {
            const detailItem = document.createElement('div');
            detailItem.className = `progress-detail-item ${detail.kind === 'error' ? 'error' : detail.kind === 'done' ? 'done' : ''}`;
            const main = document.createElement('div');
            main.className = 'progress-detail-main';
            const title = document.createElement('span');
            title.textContent = detail.title || '明细';
            const message = document.createElement('span');
            message.className = 'muted small';
            message.textContent = detail.message || '';
            main.appendChild(title);
            main.appendChild(message);
            detailItem.appendChild(main);
            if (detail.meta) {
              const meta = document.createElement('div');
              meta.className = 'progress-detail-meta';
              meta.textContent = detail.meta;
              detailItem.appendChild(meta);
            }
            detailList.appendChild(detailItem);
          }
          wrapper.appendChild(detailList);
        }
        els.progressList.appendChild(wrapper);
      }
      const showRunDetails = false;
      if (showRunDetails) {
        const detailEvents = (job.detail_events || []).map(event => ({
          ...event,
          detailType: 'step',
          sortTime: Number(event.updated_at || 0),
        }));
        const productionEvents = (job.production_events || []).map(event => ({
          ...event,
          detailType: 'production',
          sortTime: Number(event.updated_at || 0),
        }));
        const latestEvents = detailEvents
          .concat(productionEvents)
          .sort((a, b) => a.sortTime - b.sortTime)
          .slice(-1);
        for (const event of latestEvents) {
          const item = document.createElement('div');
          const isError = String(event.status || event.job_status || '').includes('failed') || event.error;
          const isDone = ['success', 'partial_success', 'final_video_generated', 'skipped'].includes(String(event.status || event.job_status || ''));
          item.className = `progress-step ${event.kind === 'error' || isError ? 'error' : event.kind === 'done' || isDone ? 'done' : 'active'}`;
          const left = document.createElement('span');
          left.textContent = event.detailType === 'production'
            ? `后处理 · ${event.stage || 'production'}`
            : (event.step ? `第 ${event.step} 步明细` : '运行明细');
          const right = document.createElement('span');
          right.className = 'muted small';
          right.textContent = event.message || '';
          item.appendChild(left);
          item.appendChild(right);
          els.progressList.appendChild(item);
        }
      }
    }

    async function pollRunStatus(runId) {
      const job = await api(`/api/run-status?id=${encodeURIComponent(runId)}`);
      if (currentRunId && currentRunId !== runId) return;
      if (!currentRunId && !autoFocusOutputDuringRun) return;
      renderProgress(job);
      if (job.task_name && ['queued', 'running', 'paused'].includes(job.status)) {
        if (maybeShowOutput()) {
          await selectActiveRunTask(job);
          await refreshActiveRunTaskDetail(job);
        }
      }
      if (job.status === 'running') {
        const runningText = job.current_message || job.production_message || '工作流运行中';
        setStatus(runningText, false);
      }
      if (job.status === 'completed') {
        const productionStatus = job.production_status && job.production_status !== 'off' ? `，自动生成：${job.production_status}` : '';
        setStatus(`完成：${job.task_title || job.workflow_name}，${job.step_count || job.completed_steps} 步${productionStatus}`);
        await loadTasks();
        if (job.task_name) {
          if (maybeShowOutput()) await selectTaskAndOpenJobOutput(job);
          if (job.rerun_result && job.rerun_result.file) {
            await openFile(job.rerun_result.file);
            setStatus(`重跑完成：第 ${job.rerun_step || ''} 步`);
          }
        }
        autoFocusOutputDuringRun = false;
        els.runBtn.disabled = false;
        trackRun("");
        setWorkflowInteractionLocked(false);
        setTimeout(() => setRunButtonProgress(0), 900);
        syncOutputButtons();
        progressTimer = null;
        return;
      }
      if (job.status === 'failed') {
        const isCheckpoint = job.awaiting_confirmation || String(job.error || job.current_message || '').includes('等待确认');
        if (isCheckpoint) {
          setStatus(job.error || '当前步骤已完成，确认输出后点击继续下一步', false);
          await loadTasks();
          if (job.task_name && maybeShowOutput()) await selectTaskAndOpenJobOutput(job);
          autoFocusOutputDuringRun = false;
          els.runBtn.disabled = false;
          trackRun("");
          setWorkflowInteractionLocked(false);
          setTimeout(() => setRunButtonProgress(0), 900);
          syncOutputButtons();
          progressTimer = null;
          return;
        }
        setStatus(job.error || '工作流运行失败', true);
        await loadTasks();
        if (job.task_name) {
          if (maybeShowOutput()) await selectTaskAndOpenJobOutput(job);
        }
        autoFocusOutputDuringRun = false;
        els.runBtn.disabled = false;
        trackRun("");
        setWorkflowInteractionLocked(false);
        setRunButtonProgress(0);
        syncOutputButtons();
        progressTimer = null;
        return;
      }
      if (job.status === 'cancelled') {
        setStatus(job.error || '任务已终止', true);
        await loadTasks();
        if (job.task_name) {
          if (maybeShowOutput()) await selectTaskAndOpenJobOutput(job);
        }
        autoFocusOutputDuringRun = false;
        els.runBtn.disabled = false;
        trackRun("");
        setWorkflowInteractionLocked(false);
        setRunButtonProgress(0);
        syncOutputButtons();
        progressTimer = null;
        return;
      }
      if (job.status === 'paused') {
        const isCheckpoint = job.awaiting_confirmation || String(job.error || '').includes('等待确认');
        setStatus(
          job.error || (isCheckpoint ? '当前步骤已完成，确认输出后点击继续下一步' : '任务已暂停，可在任务输出里点击继续任务'),
          !isCheckpoint
        );
        await loadTasks();
        if (job.task_name) {
          if (maybeShowOutput()) await selectTaskAndOpenJobOutput(job);
        }
        autoFocusOutputDuringRun = false;
        els.runBtn.disabled = false;
        trackRun("");
        setWorkflowInteractionLocked(false);
        setTimeout(() => setRunButtonProgress(0), 900);
        syncOutputButtons();
        progressTimer = null;
        return;
      }
      progressTimer = setTimeout(() => {
        pollRunStatus(runId).catch(err => {
          setStatus(err.message, true);
          els.runBtn.disabled = false;
          setWorkflowInteractionLocked(false);
          setRunButtonProgress(0);
          syncOutputButtons();
          progressTimer = null;
        });
      }, 1000);
    }

    async function restoreActiveRun() {
      const data = await api('/api/active-run');
      const job = data.run;
      if (!job) return;
      showView('output');
      renderProgress(job);
      if (job.task_name) {
        await loadTasks();
        await selectTaskAndOpenJobOutput(job);
      }
      if (job.status === 'queued' || job.status === 'running') {
        autoFocusOutputDuringRun = true;
        setWorkflowInteractionLocked(true);
        trackRun(job.run_id);
        await pollRunStatus(job.run_id);
      } else {
        setWorkflowInteractionLocked(false);
        syncOutputButtons();
      }
    }

    async function api(path, options) {
      const res = await fetch(path, options);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      return body;
    }

    async function loadConfig() {
      const data = await api('/api/config');
      localModelPresets = data.local_model_presets || [];
      staffOptions = (data.staff || []).filter(isActiveLongVideoStaff);
      els.env.textContent = data.openai_configured ? 'OpenAI 已配置' : 'OpenAI 未配置，默认离线模式';
      const activeWorkflows = (data.workflows || []).filter(isActiveLongVideoWorkflow);
      els.workflow.innerHTML = activeWorkflows.map(w => `<option value="${w.stem}">${w.name}</option>`).join('');
      setIfExists(els.productTemplate, 'long_video');
      setIfExists(els.workflow, LONG_VIDEO_WORKFLOW_STEM);
      renderLocalModelPresets();
      restoreSettings();
      setIfExists(els.productTemplate, 'long_video');
      setIfExists(els.workflow, LONG_VIDEO_WORKFLOW_STEM);
    }

    function readSettings() {
      try {
        return JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}');
      } catch {
        return {};
      }
    }

    function serializeComfyDebugState() {
      const out = {};
      for (const [id, state] of comfyDebugStateByWorkflowId.entries()) {
        if (!id || !state || typeof state !== 'object') continue;
        out[id] = {
          endpoint: state.endpoint || '',
          reference: state.reference || '',
          middleFrameReference: state.middleFrameReference || '',
          lastFrameReference: state.lastFrameReference || '',
          maskImage: state.maskImage || '',
          audioFile: state.audioFile || '',
          seed: state.seed || '',
          width: state.width || '',
          height: state.height || '',
          duration: state.duration || '',
          fps: state.fps || '',
          workflowMode: state.workflowMode || '',
          prompt: state.prompt || '',
          negative: state.negative || '',
          nodeInfoList: state.nodeInfoList || '[]',
          pollTimeout: state.pollTimeout || '3600',
          assetReference: state.assetReference || '',
          middleFrameAssetReference: state.middleFrameAssetReference || '',
          lastFrameAssetReference: state.lastFrameAssetReference || '',
          referenceHint: state.referenceHint || '',
          middleFrameReferenceHint: state.middleFrameReferenceHint || '',
          lastFrameReferenceHint: state.lastFrameReferenceHint || '',
          results: compactComfyDebugResults(state.results),
          running: Boolean(state.running),
          runId: state.runId || '',
          status: state.status || '',
          error: state.error || '',
          startedAt: Number(state.startedAt || 0),
          finishedAt: Number(state.finishedAt || 0),
          elapsedSeconds: Number(state.elapsedSeconds || 0),
        };
      }
      return out;
    }

    function compactComfyDebugResults(results) {
      if (!Array.isArray(results)) return [];
      return results.slice(0, 12).map(result => ({
        id: result?.id || '',
        name: result?.name || '',
        status: result?.status || '',
        type: result?.type || '',
        task: result?.task || '__comfy_debug__',
        endpoint: result?.endpoint || '',
        error: result?.error || '',
        files: Array.isArray(result?.files) ? result.files.slice(0, 80) : [],
      }));
    }

    function restoreComfyDebugState(value) {
      comfyDebugStateByWorkflowId.clear();
      if (!value || typeof value !== 'object' || Array.isArray(value)) return;
      Object.entries(value).forEach(([id, state]) => {
        if (!id || !state || typeof state !== 'object' || Array.isArray(state)) return;
        comfyDebugStateByWorkflowId.set(id, {
          endpoint: state.endpoint || '',
          reference: state.reference || '',
          middleFrameReference: state.middleFrameReference || '',
          lastFrameReference: state.lastFrameReference || '',
          maskImage: state.maskImage || '',
          audioFile: state.audioFile || '',
          seed: state.seed || '',
          width: state.width || '',
          height: state.height || '',
          duration: state.duration || '',
          fps: state.fps || '',
          workflowMode: state.workflowMode || '',
          prompt: state.prompt || '',
          negative: state.negative || '',
          nodeInfoList: state.nodeInfoList || '[]',
          pollTimeout: state.pollTimeout || '3600',
          assetReference: state.assetReference || '',
          middleFrameAssetReference: state.middleFrameAssetReference || '',
          lastFrameAssetReference: state.lastFrameAssetReference || '',
          referenceHint: state.referenceHint || '',
          middleFrameReferenceHint: state.middleFrameReferenceHint || '',
          lastFrameReferenceHint: state.lastFrameReferenceHint || '',
          results: compactComfyDebugResults(state.results),
          running: Boolean(state.running),
          runId: state.runId || '',
          status: state.status || '',
          error: state.error || '',
          startedAt: Number(state.startedAt || 0),
          finishedAt: Number(state.finishedAt || 0),
          elapsedSeconds: Number(state.elapsedSeconds || 0),
        });
      });
    }

    function saveSettings() {
      if (settingsRestoring) return;
      saveCurrentComfyDebugUiState();
      const previousSettings = readSettings();
      const settings = {
        ...previousSettings,
        productTemplate: els.productTemplate.value,
        workflow: els.workflow.value,
        provider: els.provider.value,
        model: els.model.value,
        customModel: els.customModel.value,
        apiKey: els.apiKey.value,
        baseUrl: els.baseUrl.value,
        modelTimeout: els.modelTimeout.value,
        localModelPreset: els.localModelPreset.value,
        localModelName: els.localModelName.value,
        useMemory: els.useMemory.value,
        inheritTask: els.inheritTask.value,
        inheritMode: els.inheritMode.value,
        useKnowledge: els.useKnowledge.value,
        workflowAdvanceMode: els.workflowAdvanceMode.value,
        autoProductionMode: els.autoProductionMode.value,
        comfyDebugGate: els.comfyDebugGate.value,
        composeTool: els.composeTool.value,
        finalVideoName: els.finalVideoName.value,
        comfyApiKey: els.comfyApiKey.value,
        comfyBaseUrl: els.comfyBaseUrl.value,
        comfyWorkflowEndpoint: els.comfyWorkflowEndpoint.value,
        comfyWorkflowPreset: els.comfyWorkflowPreset.value,
        comfyWorkflowPresetNote: els.comfyWorkflowPresetNote.value,
        comfyWorkflowLibrary,
        comfyDebugStateByWorkflowId: serializeComfyDebugState(),
        activeComfyDebugWorkflowId,
        activeComfyDebugWorkflowMode,
        comfyNodeInfoList: els.comfyNodeInfoList.value,
        comfyPollTimeout: els.comfyPollTimeout.value,
        assetQualityGate: els.assetQualityGate.value,
        assetMaxAttempts: els.assetMaxAttempts.value,
        assetMinScore: els.assetMinScore.value,
        voiceMode: els.voiceMode.value,
        voicePreset: els.voicePreset.value,
        voiceReferenceAudioPath: els.voiceReferenceAudioPath.value,
        voiceReferenceText: els.voiceReferenceText.value,
        voiceCommandTemplate: els.voiceCommandTemplate.value,
        voiceTimeout: els.voiceTimeout.value,
        imageTool: els.imageTool.value,
        imagePositivePrompt: els.imagePositivePrompt.value,
        imageModel: els.imageModel.value,
        imageSize: els.imageSize.value,
        imageCount: els.imageCount.value,
        imageStyle: els.imageStyle.value,
        imageQuality: els.imageQuality.value,
        imageApiKey: els.imageApiKey.value,
        imageBaseUrl: els.imageBaseUrl.value,
        imageWorkflowEndpoint: els.imageWorkflowEndpoint.value,
        imageInstanceType: els.imageInstanceType.value,
        imageNodeInfoList: els.imageNodeInfoList.value,
        imagePollTimeout: els.imagePollTimeout.value,
        imageNegativePrompt: els.imageNegativePrompt.value,
        imageConsistency: els.imageConsistency.value,
        imageSeed: els.imageSeed.value,
        imageGuidance: els.imageGuidance.value,
        imageSteps: els.imageSteps.value,
        imageDenoise: els.imageDenoise.value,
        imageSampler: els.imageSampler.value,
        imageControl: els.imageControl.value,
        videoTool: els.videoTool.value,
        videoPositivePrompt: els.videoPositivePrompt.value,
        videoModel: els.videoModel.value,
        videoAspect: els.videoAspect.value,
        videoDuration: els.videoDuration.value,
        videoStyle: els.videoStyle.value,
        videoPromptNotes: els.videoPromptNotes.value,
        videoApiKey: els.videoApiKey.value,
        videoBaseUrl: els.videoBaseUrl.value,
        videoWorkflowEndpoint: els.videoWorkflowEndpoint.value,
        videoNodeInfoList: els.videoNodeInfoList.value,
        videoPollTimeout: els.videoPollTimeout.value,
        videoNegativePrompt: els.videoNegativePrompt.value,
        videoSeed: els.videoSeed.value,
        videoFps: els.videoFps.value,
        videoMotionStrength: els.videoMotionStrength.value,
        videoCameraMotion: els.videoCameraMotion.value,
        videoResolution: els.videoResolution.value,
        videoGuidance: els.videoGuidance.value,
        videoFrames: els.videoFrames.value,
        videoImageStrength: els.videoImageStrength.value,
        videoCameraPath: els.videoCameraPath.value,
        videoAudioNotes: els.videoAudioNotes.value,
        videoAdvancedParams: els.videoAdvancedParams.value,
        referenceRole: els.referenceRole.value,
        referenceNote: els.referenceNote.value,
      };
      [
        'imageTool',
        'imagePositivePrompt',
        'imageModel',
        'imageSize',
        'imageCount',
        'imageStyle',
        'imageQuality',
        'imageApiKey',
        'imageBaseUrl',
        'imageWorkflowEndpoint',
        'imageInstanceType',
        'imageNodeInfoList',
        'imagePollTimeout',
        'imageNegativePrompt',
        'imageConsistency',
        'imageSeed',
        'imageGuidance',
        'imageSteps',
        'imageDenoise',
        'imageSampler',
        'imageControl',
        'videoTool',
        'videoPositivePrompt',
        'videoModel',
        'videoAspect',
        'videoDuration',
        'videoStyle',
        'videoPromptNotes',
        'videoApiKey',
        'videoBaseUrl',
        'videoWorkflowEndpoint',
        'videoNodeInfoList',
        'videoPollTimeout',
        'videoNegativePrompt',
        'videoSeed',
        'videoFps',
        'videoMotionStrength',
        'videoCameraMotion',
        'videoResolution',
        'videoGuidance',
        'videoFrames',
        'videoImageStrength',
        'videoCameraPath',
        'videoAudioNotes',
        'videoAdvancedParams',
        'referenceRole',
        'referenceNote',
      ].forEach(key => {
        if (els[key]?.isConnected) return;
        if (Object.prototype.hasOwnProperty.call(previousSettings, key)) settings[key] = previousSettings[key];
        else delete settings[key];
      });
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    }

    function restoreSettings() {
      settingsRestoring = true;
      comfyDebugFormHydrated = false;
      const settings = readSettings();
      setIfExists(els.productTemplate, 'long_video');
      setIfExists(els.workflow, LONG_VIDEO_WORKFLOW_STEM);
      setIfExists(els.provider, settings.provider);
      setIfExists(els.model, settings.model);
      els.customModel.value = settings.customModel || '';
      els.taskTitle.value = '';
      els.apiKey.value = settings.apiKey || '';
      els.baseUrl.value = settings.baseUrl || '';
      setIfExists(els.modelTimeout, settings.modelTimeout);
      setIfExists(els.localModelPreset, settings.localModelPreset);
      renderLocalModelNames();
      setIfExists(els.localModelName, settings.localModelName);
      setIfExists(els.useMemory, settings.useMemory === 'on' ? 'video_output' : settings.useMemory);
      setIfExists(els.inheritTask, settings.inheritTask);
      setIfExists(els.inheritMode, settings.inheritMode);
      setIfExists(els.useKnowledge, settings.useKnowledge);
      setIfExists(els.workflowAdvanceMode, settings.workflowAdvanceMode || 'auto');
      setIfExists(els.autoProductionMode, settings.autoProductionMode);
      setIfExists(els.comfyDebugGate, settings.comfyDebugGate || 'on');
      setIfExists(els.composeTool, settings.composeTool);
      els.finalVideoName.value = settings.finalVideoName || '';
      els.comfyApiKey.value = settings.comfyApiKey || '';
      els.comfyBaseUrl.value = settings.comfyBaseUrl || '';
      els.comfyWorkflowEndpoint.value = settings.comfyWorkflowEndpoint || '';
      comfyWorkflowLibrary = normalizeComfyWorkflowLibrary(settings.comfyWorkflowLibrary);
      restoreComfyDebugState(settings.comfyDebugStateByWorkflowId);
      activeComfyDebugWorkflowId = settings.activeComfyDebugWorkflowId || '';
      activeComfyDebugWorkflowMode = settings.activeComfyDebugWorkflowMode || '';
      comfyDebugCollapsedCapabilityGroups.clear();
      COMFY_DEBUG_CAPABILITY_GROUPS.forEach(group => comfyDebugCollapsedCapabilityGroups.add(group.id));
      renderComfyWorkflowLibrary();
      setIfExists(els.comfyWorkflowPreset, settings.comfyWorkflowPreset || DEFAULT_COMFY_WORKFLOW_PRESET_ID);
      const selectedComfyWorkflow = getSelectedComfyWorkflowPreset();
      els.comfyWorkflowPresetNote.value = selectedComfyWorkflow?.purpose || settings.comfyWorkflowPresetNote || '';
      els.comfyWorkflowEndpoint.value = selectedComfyWorkflow?.endpoint || settings.comfyWorkflowEndpoint || '';
      els.comfyNodeInfoList.value = selectedComfyWorkflow?.nodeInfoList || settings.comfyNodeInfoList || '[]';
      setIfExists(els.comfyPollTimeout, selectedComfyWorkflow?.pollTimeout || settings.comfyPollTimeout || '3600');
      setIfExists(els.assetQualityGate, settings.assetQualityGate || 'on');
      setIfExists(els.assetMaxAttempts, settings.assetMaxAttempts || '2');
      setIfExists(els.assetMinScore, settings.assetMinScore || '70');
      renderComfyWorkflowLibraryList();
      setIfExists(els.voiceMode, settings.voiceMode);
      setIfExists(els.voicePreset, settings.voicePreset || 'warm_female');
      els.voiceReferenceAudioPath.value = settings.voiceReferenceAudioPath || '';
      els.voiceReferenceText.value = settings.voiceReferenceText || '';
      els.voiceCommandTemplate.value = settings.voiceCommandTemplate || '';
      setIfExists(els.voiceTimeout, normalizeVoiceTimeout(settings.voiceTimeout));
      syncVoiceCommandTemplateForMode();
      setIfExists(els.imageTool, settings.imageTool);
      els.imagePositivePrompt.value = settings.imagePositivePrompt || '';
      els.imageModel.value = settings.imageModel || '';
      setIfExists(els.imageSize, settings.imageSize);
      setIfExists(els.imageCount, settings.imageCount);
      els.imageStyle.value = settings.imageStyle || '';
      setIfExists(els.imageQuality, settings.imageQuality);
      els.imageApiKey.value = settings.imageApiKey || '';
      els.imageBaseUrl.value = settings.imageBaseUrl || '';
      els.imageWorkflowEndpoint.value = settings.imageWorkflowEndpoint || '';
      setIfExists(els.imageInstanceType, settings.imageInstanceType);
      els.imageNodeInfoList.value = settings.imageNodeInfoList || '';
      setIfExists(els.imagePollTimeout, settings.imagePollTimeout);
      els.imageNegativePrompt.value = settings.imageNegativePrompt || '';
      els.imageConsistency.value = settings.imageConsistency || '';
      els.imageSeed.value = settings.imageSeed || '';
      els.imageGuidance.value = settings.imageGuidance || '';
      els.imageSteps.value = settings.imageSteps || '';
      els.imageDenoise.value = settings.imageDenoise || '';
      els.imageSampler.value = settings.imageSampler || '';
      els.imageControl.value = settings.imageControl || '';
      setIfExists(els.videoTool, settings.videoTool);
      els.videoPositivePrompt.value = settings.videoPositivePrompt || '';
      els.videoModel.value = settings.videoModel || '';
      setIfExists(els.videoAspect, settings.videoAspect);
      setIfExists(els.videoDuration, settings.videoDuration);
      els.videoStyle.value = settings.videoStyle || '';
      els.videoPromptNotes.value = settings.videoPromptNotes || '';
      els.videoApiKey.value = settings.videoApiKey || '';
      els.videoBaseUrl.value = settings.videoBaseUrl || '';
      els.videoWorkflowEndpoint.value = settings.videoWorkflowEndpoint || '';
      els.videoNodeInfoList.value = settings.videoNodeInfoList || '';
      setIfExists(els.videoPollTimeout, settings.videoPollTimeout);
      els.videoNegativePrompt.value = settings.videoNegativePrompt || '';
      els.videoSeed.value = settings.videoSeed || '';
      setIfExists(els.videoFps, settings.videoFps);
      setIfExists(els.videoMotionStrength, settings.videoMotionStrength);
      setIfExists(els.videoCameraMotion, settings.videoCameraMotion);
      setIfExists(els.videoResolution, settings.videoResolution);
      els.videoGuidance.value = settings.videoGuidance || '';
      els.videoFrames.value = settings.videoFrames || '';
      els.videoImageStrength.value = settings.videoImageStrength || '';
      els.videoCameraPath.value = settings.videoCameraPath || '';
      els.videoAudioNotes.value = settings.videoAudioNotes || '';
      els.videoAdvancedParams.value = settings.videoAdvancedParams || '';
      setIfExists(els.referenceRole, settings.referenceRole);
      els.referenceNote.value = settings.referenceNote || '';
      syncCustomModelState(false);
      applyImageProviderDefaults();
      applyVideoProviderDefaults();
      applyComfyProviderDefaults();
      renderComfyParameterMapper();
      setIfExists(els.productTemplate, 'long_video');
      setIfExists(els.workflow, LONG_VIDEO_WORKFLOW_STEM);
      settingsRestoring = false;
    }

    async function cancelCurrentRun() {
      const canCancelSelectedTask = Boolean(selectedTask && selectedTaskAllowedActions.includes('cancel'));
      if (!currentRunId && !canCancelSelectedTask) {
        setStatus('当前没有可终止的任务', true);
        return;
      }
      if (!confirm('确定终止当前任务？\n\n系统会停止正在运行的流程，已生成的文件会保留，后续可从任务输出继续。')) return;
      const runIdToCancel = currentRunId;
      const taskToCancel = !runIdToCancel && canCancelSelectedTask ? selectedTask : '';
      if (progressTimer) {
        clearTimeout(progressTimer);
        progressTimer = null;
      }
      autoFocusOutputDuringRun = false;
      setStatus('正在终止任务...');
      trackRun("");
      selectedTaskAllowedActions = (selectedTaskAllowedActions || []).filter(action => action !== 'cancel');
      setWorkflowInteractionLocked(false);
      setRunButtonProgress(0);
      syncOutputButtons();
      try {
        if (els.cancelRunBtn) els.cancelRunBtn.disabled = true;
        if (els.outputCancelRunBtn) els.outputCancelRunBtn.disabled = true;
        const result = await api('/api/cancel-run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ run_id: runIdToCancel, task_name: taskToCancel }),
        });
        setStatus(result.message || '任务已终止');
        renderProgress(result);
        setWorkflowInteractionLocked(false);
        setRunButtonProgress(0);
        trackRun("");
        await refreshSelectedTaskDetail({ openMissingFile: true }).catch(() => {});
        selectedTaskAllowedActions = (selectedTaskAllowedActions || []).filter(action => action !== 'cancel');
        syncOutputButtons();
        if (['cancelled', 'failed', 'paused', 'completed'].includes(result.status)) {
          progressTimer = null;
        }
      } catch (err) {
        setStatus(err.message, true);
        setWorkflowInteractionLocked(false);
        setRunButtonProgress(0);
        syncOutputButtons();
      }
    }

    function pauseCurrentRunOnExit() {
      if (!currentRunId) return;
      const payload = JSON.stringify({ run_id: currentRunId, reason: 'browser_exit' });
      try {
        if (navigator.sendBeacon) {
          navigator.sendBeacon('/api/pause-run', new Blob([payload], { type: 'application/json' }));
        } else {
          fetch('/api/pause-run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: payload,
            keepalive: true,
          }).catch(() => {});
        }
      } catch {}
    }

    function comfyDebugLeafKey(workflowId, mode = '') {
      return `${String(workflowId || '')}::${String(mode || '')}`;
    }

    function activeComfyDebugStateKey() {
      return comfyDebugLeafKey(activeComfyDebugWorkflowId, activeComfyDebugWorkflowMode || els.comfyDebugWorkflowMode?.value || '');
    }

    function comfyDebugCapabilityForMode(mode) {
      return COMFY_DEBUG_CAPABILITY_GROUPS.find(group => group.modes.includes(String(mode || '')))?.id || 'other';
    }

    function normalizeComfyModeConfig(raw = {}, fallback = {}) {
      const item = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
      return {
        endpoint: String(item.endpoint ?? fallback.endpoint ?? ''),
        nodeInfoList: sanitizeComfyVisualNodeInfoList(String(item.nodeInfoList ?? item.node_info_list_json ?? fallback.nodeInfoList ?? '[]')),
        pollTimeout: String(item.pollTimeout ?? item.poll_timeout_seconds ?? fallback.pollTimeout ?? '3600'),
        defaultWidth: String(item.defaultWidth ?? item.default_width ?? fallback.defaultWidth ?? ''),
        defaultHeight: String(item.defaultHeight ?? item.default_height ?? fallback.defaultHeight ?? ''),
        defaultReference: String(item.defaultReference ?? item.default_reference ?? fallback.defaultReference ?? ''),
        defaultMiddleFrameReference: String(item.defaultMiddleFrameReference ?? item.default_middle_frame_reference ?? fallback.defaultMiddleFrameReference ?? ''),
        defaultLastFrameReference: String(item.defaultLastFrameReference ?? item.default_last_frame_reference ?? fallback.defaultLastFrameReference ?? ''),
        defaultSeed: String(item.defaultSeed ?? item.default_seed ?? fallback.defaultSeed ?? ''),
        defaultDuration: String(item.defaultDuration ?? item.default_duration ?? fallback.defaultDuration ?? ''),
        defaultFps: String(item.defaultFps ?? item.default_fps ?? fallback.defaultFps ?? ''),
        defaultPrompt: String(item.defaultPrompt ?? item.default_prompt ?? fallback.defaultPrompt ?? ''),
        defaultNegative: String(item.defaultNegative ?? item.default_negative ?? fallback.defaultNegative ?? ''),
        defaultAssetReference: String(item.defaultAssetReference ?? item.default_asset_reference ?? fallback.defaultAssetReference ?? ''),
        defaultMiddleFrameAssetReference: String(item.defaultMiddleFrameAssetReference ?? item.default_middle_frame_asset_reference ?? fallback.defaultMiddleFrameAssetReference ?? ''),
        defaultLastFrameAssetReference: String(item.defaultLastFrameAssetReference ?? item.default_last_frame_asset_reference ?? fallback.defaultLastFrameAssetReference ?? ''),
        defaultReferenceHint: String(item.defaultReferenceHint ?? item.default_reference_hint ?? fallback.defaultReferenceHint ?? ''),
        defaultMiddleFrameReferenceHint: String(item.defaultMiddleFrameReferenceHint ?? item.default_middle_frame_reference_hint ?? fallback.defaultMiddleFrameReferenceHint ?? ''),
        defaultLastFrameReferenceHint: String(item.defaultLastFrameReferenceHint ?? item.default_last_frame_reference_hint ?? fallback.defaultLastFrameReferenceHint ?? ''),
      };
    }

    function normalizeComfyModeConfigs(raw, fallback = {}, defaultMode = '') {
      const source = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {};
      const result = {};
      Object.entries(source).forEach(([mode, config]) => {
        if (mode) result[mode] = normalizeComfyModeConfig(config, fallback);
      });
      if (!Object.keys(result).length && defaultMode) result[defaultMode] = normalizeComfyModeConfig({}, fallback);
      return result;
    }

    function getComfyWorkflowModeConfig(workflow, mode = activeComfyDebugWorkflowMode, create = true) {
      if (!workflow) return null;
      const item = getComfyWorkflowLibraryItemById(workflow.id);
      if (!item) return null;
      const selectedMode = String(mode || workflow.modes?.[0]?.value || item.defaultWorkflowMode || 'default');
      if (!item.modeConfigs || typeof item.modeConfigs !== 'object') item.modeConfigs = {};
      if (!item.modeConfigs[selectedMode] && create) {
        item.modeConfigs[selectedMode] = normalizeComfyModeConfig({}, item);
      }
      return item.modeConfigs[selectedMode] || null;
    }

    function normalizeComfyWorkflowLibrary(value) {
      const saved = Array.isArray(value) ? value : [];
      const byId = new Map(saved.filter(item => item && item.id).map(item => [item.id, item]));
      const normalized = DEFAULT_COMFY_WORKFLOW_LIBRARY.map(defaultItem => {
        const item = byId.get(defaultItem.id) || {};
        const savedNodeInfo = String(item.nodeInfoList || item.node_info_list_json || defaultItem.nodeInfoList);
        byId.delete(defaultItem.id);
        return {
          ...defaultItem,
          name: defaultItem.name,
          purpose: defaultItem.purpose,
          materialTypes: Array.isArray(defaultItem.materialTypes) ? defaultItem.materialTypes : [],
          endpoint: String(item.endpoint || ''),
          nodeInfoList: sanitizeComfyVisualNodeInfoList(savedNodeInfo),
          pollTimeout: String(item.pollTimeout || item.poll_timeout_seconds || defaultItem.pollTimeout),
          defaultWidth: String(item.defaultWidth || item.default_width || ''),
          defaultHeight: String(item.defaultHeight || item.default_height || ''),
          defaultReference: String(item.defaultReference || item.default_reference || item.reference || ''),
          defaultMiddleFrameReference: String(item.defaultMiddleFrameReference || item.default_middle_frame_reference || item.middleFrameReference || item.middle_frame_image || ''),
          defaultLastFrameReference: String(item.defaultLastFrameReference || item.default_last_frame_reference || item.lastFrameReference || item.last_frame_image || ''),
          defaultSeed: String(item.defaultSeed || item.default_seed || item.seed || ''),
          defaultDuration: String(item.defaultDuration || item.default_duration || item.duration || ''),
          defaultPrompt: String(item.defaultPrompt || item.default_prompt || item.prompt || ''),
          defaultNegative: String(item.defaultNegative || item.default_negative || item.negative || item.negative_prompt || ''),
          defaultAssetReference: String(item.defaultAssetReference || item.default_asset_reference || item.assetReference || ''),
          defaultMiddleFrameAssetReference: String(item.defaultMiddleFrameAssetReference || item.default_middle_frame_asset_reference || item.middleFrameAssetReference || ''),
          defaultLastFrameAssetReference: String(item.defaultLastFrameAssetReference || item.default_last_frame_asset_reference || item.lastFrameAssetReference || ''),
          defaultReferenceHint: String(item.defaultReferenceHint || item.default_reference_hint || item.referenceHint || ''),
          defaultMiddleFrameReferenceHint: String(item.defaultMiddleFrameReferenceHint || item.default_middle_frame_reference_hint || item.middleFrameReferenceHint || ''),
          defaultLastFrameReferenceHint: String(item.defaultLastFrameReferenceHint || item.default_last_frame_reference_hint || item.lastFrameReferenceHint || ''),
          defaultWorkflowMode: String(item.defaultWorkflowMode || item.default_workflow_mode || ''),
          modeConfigs: normalizeComfyModeConfigs(item.modeConfigs || item.mode_configs, item, String(item.defaultWorkflowMode || item.default_workflow_mode || '')),
          defaultImageTaskType: String(item.defaultImageTaskType || item.default_image_task_type || defaultItem.defaultImageTaskType || ''),
          defaultFps: String(item.defaultFps || item.default_fps || ''),
          debugWorkflow: Boolean(item.debugWorkflow || item.debug_workflow),
        };
      });
      byId.forEach(item => {
        const savedNodeInfo = String(item.nodeInfoList || item.node_info_list_json || '[]');
        normalized.push({
          id: String(item.id || '').trim(),
          name: String(item.name || item.title || item.id || '').trim(),
          purpose: String(item.purpose || '').trim(),
          materialTypes: Array.isArray(item.materialTypes) ? item.materialTypes : Array.isArray(item.material_types) ? item.material_types : [],
          endpoint: String(item.endpoint || item.workflow_endpoint || ''),
          nodeInfoList: sanitizeComfyVisualNodeInfoList(savedNodeInfo),
          pollTimeout: String(item.pollTimeout || item.poll_timeout_seconds || '3600'),
          defaultWidth: String(item.defaultWidth || item.default_width || ''),
          defaultHeight: String(item.defaultHeight || item.default_height || ''),
          defaultReference: String(item.defaultReference || item.default_reference || item.reference || ''),
          defaultMiddleFrameReference: String(item.defaultMiddleFrameReference || item.default_middle_frame_reference || item.middleFrameReference || item.middle_frame_image || ''),
          defaultLastFrameReference: String(item.defaultLastFrameReference || item.default_last_frame_reference || item.lastFrameReference || item.last_frame_image || ''),
          defaultSeed: String(item.defaultSeed || item.default_seed || item.seed || ''),
          defaultDuration: String(item.defaultDuration || item.default_duration || item.duration || ''),
          defaultPrompt: String(item.defaultPrompt || item.default_prompt || item.prompt || ''),
          defaultNegative: String(item.defaultNegative || item.default_negative || item.negative || item.negative_prompt || ''),
          defaultAssetReference: String(item.defaultAssetReference || item.default_asset_reference || item.assetReference || ''),
          defaultMiddleFrameAssetReference: String(item.defaultMiddleFrameAssetReference || item.default_middle_frame_asset_reference || item.middleFrameAssetReference || ''),
          defaultLastFrameAssetReference: String(item.defaultLastFrameAssetReference || item.default_last_frame_asset_reference || item.lastFrameAssetReference || ''),
          defaultReferenceHint: String(item.defaultReferenceHint || item.default_reference_hint || item.referenceHint || ''),
          defaultMiddleFrameReferenceHint: String(item.defaultMiddleFrameReferenceHint || item.default_middle_frame_reference_hint || item.middleFrameReferenceHint || ''),
          defaultLastFrameReferenceHint: String(item.defaultLastFrameReferenceHint || item.default_last_frame_reference_hint || item.lastFrameReferenceHint || ''),
          defaultWorkflowMode: String(item.defaultWorkflowMode || item.default_workflow_mode || ''),
          modeConfigs: normalizeComfyModeConfigs(item.modeConfigs || item.mode_configs, item, String(item.defaultWorkflowMode || item.default_workflow_mode || '')),
          defaultImageTaskType: String(item.defaultImageTaskType || item.default_image_task_type || ''),
          defaultFps: String(item.defaultFps || item.default_fps || ''),
          debugWorkflow: Boolean(item.debugWorkflow || item.debug_workflow),
        });
      });
      return normalized.filter(item => item.id);
    }

    function ensureComfyDebugWorkflowsInLibrary() {
      if (!Array.isArray(comfyWorkflowLibrary)) comfyWorkflowLibrary = [];
      const byId = new Map(comfyWorkflowLibrary.filter(item => item && item.id).map(item => [item.id, item]));
      let changed = false;
      comfyDebugWorkflows.forEach(workflow => {
        const id = String(workflow.id || '').trim();
        if (!id) return;
        if (byId.has(id)) {
          const existing = byId.get(id);
          const modes = Array.isArray(workflow.modes) ? workflow.modes : [];
          if (!existing.modeConfigs || typeof existing.modeConfigs !== 'object') existing.modeConfigs = {};
          modes.forEach(mode => {
            const value = String(mode?.value || '');
            if (value && !existing.modeConfigs[value]) existing.modeConfigs[value] = normalizeComfyModeConfig({}, existing);
          });
          return;
        }
        const item = {
          id,
          name: workflow.name || id,
          purpose: workflow.purpose || '',
          materialTypes: workflow.type ? [workflow.type] : [],
          endpoint: workflow.default_endpoint || '',
          nodeInfoList: sanitizeComfyVisualNodeInfoList(workflow.default_node_info || '[]'),
          pollTimeout: String(workflow.poll_timeout_seconds || workflow.default_poll_timeout || '3600'),
          defaultWidth: String(workflow.default_width || ''),
          defaultHeight: String(workflow.default_height || ''),
          defaultReference: '',
          defaultMiddleFrameReference: '',
          defaultLastFrameReference: '',
          defaultSeed: '',
          defaultDuration: '',
          defaultWorkflowMode: Array.isArray(workflow.modes) && workflow.modes.length === 1 ? workflow.modes[0].value || '' : '',
          defaultImageTaskType: workflow.default_image_task_type || workflow.default_task_type || '',
          defaultPrompt: '',
          defaultNegative: '',
          defaultAssetReference: '',
          defaultMiddleFrameAssetReference: '',
          defaultLastFrameAssetReference: '',
          defaultReferenceHint: '',
          defaultMiddleFrameReferenceHint: '',
          defaultLastFrameReferenceHint: '',
          modeConfigs: {},
          debugWorkflow: true,
        };
        (Array.isArray(workflow.modes) ? workflow.modes : []).forEach(mode => {
          if (mode?.value) item.modeConfigs[mode.value] = normalizeComfyModeConfig({}, item);
        });
        comfyWorkflowLibrary.push(item);
        byId.set(id, item);
        changed = true;
      });
      if (changed) {
        renderComfyWorkflowLibrary();
        saveSettings();
      }
    }

    function getComfyWorkflowLibraryItemById(id) {
      return comfyWorkflowLibrary.find(item => String(item.id || '') === String(id || '')) || null;
    }

    function activeComfyDebugWorkflow() {
      return comfyDebugWorkflows.find(item => item.id === activeComfyDebugWorkflowId)
        || comfyDebugWorkflows[0]
        || null;
    }

    function readComfyDebugFormState() {
      return {
        endpoint: els.comfyDebugEndpoint?.value || '',
        reference: els.comfyDebugReference?.value || '',
        middleFrameReference: els.comfyDebugMiddleFrameReference?.value || '',
        lastFrameReference: els.comfyDebugLastFrameReference?.value || '',
        maskImage: els.comfyDebugMaskImage?.value || '',
        audioFile: els.comfyDebugAudioFile?.value || '',
        seed: els.comfyDebugSeed?.value || '',
        width: els.comfyDebugWidth?.value || '',
        height: els.comfyDebugHeight?.value || '',
        duration: els.comfyDebugDuration?.value || '',
        fps: els.comfyDebugFps?.value || '',
        workflowMode: els.comfyDebugWorkflowMode?.value || '',
        prompt: els.comfyDebugPrompt?.value || '',
        negative: els.comfyDebugNegative?.value || '',
        nodeInfoList: els.comfyDebugNodeInfoList?.value || '',
        pollTimeout: els.comfyDebugPollTimeout?.value || '3600',
        assetReference: els.comfyDebugAssetReference?.value || '',
        middleFrameAssetReference: els.comfyDebugMiddleFrameAssetReference?.value || '',
        lastFrameAssetReference: els.comfyDebugLastFrameAssetReference?.value || '',
        referenceHint: els.comfyDebugReferenceHint?.textContent || '',
        middleFrameReferenceHint: els.comfyDebugMiddleFrameReferenceHint?.textContent || '',
        lastFrameReferenceHint: els.comfyDebugLastFrameReferenceHint?.textContent || '',
      };
    }

    function saveCurrentComfyDebugUiState() {
      if (!comfyDebugFormHydrated) return;
      if (!activeComfyDebugWorkflowId || !els.comfyDebugEndpoint) return;
      const stateKey = activeComfyDebugStateKey();
      const previous = comfyDebugStateByWorkflowId.get(stateKey) || {};
      comfyDebugStateByWorkflowId.set(stateKey, {
        ...previous,
        ...readComfyDebugFormState(),
      });
    }

    function writeComfyDebugFormState(state) {
      if (!state) return;
      renderComfyWorkflowModeOptions(activeComfyDebugWorkflow());
      if (els.comfyDebugEndpoint) els.comfyDebugEndpoint.value = state.endpoint || '';
      if (els.comfyDebugReference) els.comfyDebugReference.value = state.reference || '';
      if (els.comfyDebugMiddleFrameReference) els.comfyDebugMiddleFrameReference.value = state.middleFrameReference || '';
      if (els.comfyDebugLastFrameReference) els.comfyDebugLastFrameReference.value = state.lastFrameReference || '';
      if (els.comfyDebugMaskImage) els.comfyDebugMaskImage.value = state.maskImage || '';
      if (els.comfyDebugAudioFile) els.comfyDebugAudioFile.value = state.audioFile || '';
      if (els.comfyDebugSeed) els.comfyDebugSeed.value = state.seed || '';
      if (els.comfyDebugWidth) els.comfyDebugWidth.value = state.width || '';
      if (els.comfyDebugHeight) els.comfyDebugHeight.value = state.height || '';
      if (els.comfyDebugDuration) els.comfyDebugDuration.value = state.duration || '';
      if (els.comfyDebugFps) els.comfyDebugFps.value = state.fps || '';
      if (els.comfyDebugWorkflowMode && state.workflowMode) setIfExists(els.comfyDebugWorkflowMode, state.workflowMode);
      if (els.comfyDebugPrompt) els.comfyDebugPrompt.value = state.prompt || '';
      if (els.comfyDebugNegative) els.comfyDebugNegative.value = state.negative || '';
      if (els.comfyDebugNodeInfoList) els.comfyDebugNodeInfoList.value = sanitizeComfyVisualNodeInfoList(state.nodeInfoList || '[]');
      if (els.comfyDebugPollTimeout) setIfExists(els.comfyDebugPollTimeout, String(state.pollTimeout || '3600'));
      if (els.comfyDebugAssetReference) els.comfyDebugAssetReference.value = state.assetReference || '';
      if (els.comfyDebugMiddleFrameAssetReference) els.comfyDebugMiddleFrameAssetReference.value = state.middleFrameAssetReference || '';
      if (els.comfyDebugLastFrameAssetReference) els.comfyDebugLastFrameAssetReference.value = state.lastFrameAssetReference || '';
      if (els.comfyDebugReferenceFile) els.comfyDebugReferenceFile.value = '';
      if (els.comfyDebugMiddleFrameReferenceFile) els.comfyDebugMiddleFrameReferenceFile.value = '';
      if (els.comfyDebugMiddleFrameReferenceFile) els.comfyDebugMiddleFrameReferenceFile.value = '';
      if (els.comfyDebugLastFrameReferenceFile) els.comfyDebugLastFrameReferenceFile.value = '';
      if (els.comfyDebugReferenceHint) {
        els.comfyDebugReferenceHint.textContent = state.referenceHint || '可直接输入路径、选择素材库资产，或上传本地参考图/视频。';
      }
      if (els.comfyDebugLastFrameReferenceHint) {
        els.comfyDebugLastFrameReferenceHint.textContent = state.lastFrameReferenceHint || '首尾帧视频需要第二张尾帧图。';
      }
      updateComfyImageTaskHint();
      updateComfyDebugReferencePreviews();
      updateComfyDebugMediaFields();
      comfyDebugFormHydrated = true;
    }

    function defaultComfyDebugStateForWorkflow(workflow, mode = activeComfyDebugWorkflowMode) {
      const savedConfig = workflow ? getComfyWorkflowLibraryItemById(workflow.id) : null;
      normalizeComfyDebugWorkflowSavedConfig(savedConfig, workflow);
      const selectedMode = String(mode || workflow?.modes?.[0]?.value || savedConfig?.defaultWorkflowMode || '');
      const modeConfig = getComfyWorkflowModeConfig(workflow, selectedMode, true) || savedConfig || {};
      return {
        endpoint: modeConfig.endpoint || workflow?.default_endpoint || '',
        reference: modeConfig.defaultReference || '',
        middleFrameReference: modeConfig.defaultMiddleFrameReference || '',
        lastFrameReference: modeConfig.defaultLastFrameReference || '',
        maskImage: '',
        audioFile: '',
        seed: modeConfig.defaultSeed || '',
        width: String(modeConfig.defaultWidth || workflow?.default_width || ''),
        height: String(modeConfig.defaultHeight || workflow?.default_height || ''),
        duration: String(modeConfig.defaultDuration || workflow?.default_duration || ''),
        fps: String(modeConfig.defaultFps || workflow?.default_fps || ''),
        workflowMode: selectedMode,
        prompt: modeConfig.defaultPrompt || '',
        negative: modeConfig.defaultNegative || '',
        nodeInfoList: modeConfig.nodeInfoList || workflow?.default_node_info || '[]',
        pollTimeout: String(modeConfig.pollTimeout || workflow?.poll_timeout_seconds || workflow?.default_poll_timeout || '3600'),
        assetReference: modeConfig.defaultAssetReference || '',
        middleFrameAssetReference: modeConfig.defaultMiddleFrameAssetReference || '',
        lastFrameAssetReference: modeConfig.defaultLastFrameAssetReference || '',
        middleFrameReferenceHint: modeConfig.defaultMiddleFrameReferenceHint || '',
        lastFrameReferenceHint: modeConfig.defaultLastFrameReferenceHint || '',
        referenceHint: modeConfig.defaultReferenceHint || '可直接输入路径、选择素材库资产，或上传本地参考图/视频。',
        results: [],
        running: false,
        runId: '',
        status: '',
        error: '',
      };
    }

    function setActiveComfyDebugWorkflow(id, forceLoad = true, mode = '') {
      saveCurrentComfyDebugUiState();
      const workflow = comfyDebugWorkflows.find(item => item.id === id) || comfyDebugWorkflows[0] || null;
      if (!workflow) return null;
      activeComfyDebugWorkflowId = workflow.id;
      activeComfyDebugWorkflowMode = String(mode || activeComfyDebugWorkflowMode || workflow.modes?.[0]?.value || '');
      if (!workflow.modes?.some(entry => entry?.value === activeComfyDebugWorkflowMode)) activeComfyDebugWorkflowMode = workflow.modes?.[0]?.value || '';
      const stateKey = activeComfyDebugStateKey();
      if (!comfyDebugStateByWorkflowId.has(stateKey)) {
        const legacyState = comfyDebugStateByWorkflowId.get(workflow.id);
        comfyDebugStateByWorkflowId.set(stateKey, legacyState ? { ...legacyState, workflowMode: activeComfyDebugWorkflowMode } : defaultComfyDebugStateForWorkflow(workflow, activeComfyDebugWorkflowMode));
        if (legacyState) comfyDebugStateByWorkflowId.delete(workflow.id);
      } else {
        comfyDebugStateByWorkflowId.set(stateKey, normalizeComfyDebugWorkflowState(comfyDebugStateByWorkflowId.get(stateKey), workflow));
      }
      if (forceLoad) {
        writeComfyDebugFormState(comfyDebugStateByWorkflowId.get(stateKey));
        renderComfyDebugStatePreview(workflow);
      } else {
        applyComfyDebugWorkflowDefaults(workflow, false);
      }
      updateComfyDebugMediaFields();
      syncComfyDebugRunButton();
      return workflow;
    }

    function sanitizeComfyVisualNodeInfoList(raw) {
      const text = String(raw || '').trim();
      if (!text || text === '[]') return '[]';
      try {
        const parsed = JSON.parse(text);
        if (!Array.isArray(parsed)) return text;
        const cleaned = parsed.filter(item => {
          if (!item || typeof item !== 'object') return true;
          const value = String(item.fieldValue ?? '');
          const nodeId = String(item.nodeId ?? '');
          return !['{{voice_text}}', '{{subtitle_srt}}', '{{subtitle_style}}'].includes(value)
            && !['5101', '5102', '5103'].includes(nodeId);
        }).map(item => {
          if (!item || typeof item !== 'object') return item;
          const nodeId = String(item.nodeId ?? '');
          const fieldName = String(item.fieldName ?? '').toLowerCase();
          const fieldValue = String(item.fieldValue ?? '');
          if (nodeId === '2612' && fieldName === 'text' && ['{{prompt}}', '{{video_prompt}}', '{{image_prompt}}'].includes(fieldValue)) {
            return { ...item, fieldValue: '{{negative_prompt}}' };
          }
          if (['length', 'frames', 'frame_count', 'num_frames'].includes(fieldName) && fieldValue === '{{duration}}') {
            return { ...item, fieldValue: '{{frame_count}}' };
          }
          return item;
        });
        return JSON.stringify(cleaned, null, 2);
      } catch {
        return text
          .replace(/\\{\\{voice_text\\}\\}/g, '')
          .replace(/\\{\\{subtitle_srt\\}\\}/g, '')
          .replace(/\\{\\{subtitle_style\\}\\}/g, '');
      }
    }

    function getSelectedComfyWorkflowPreset() {
      const selectedId = els.comfyWorkflowPreset.value || DEFAULT_COMFY_WORKFLOW_PRESET_ID;
      return comfyWorkflowLibrary.find(item => item.id === selectedId) || comfyWorkflowLibrary[0] || null;
    }

    function renderComfyWorkflowLibrary() {
      if (!comfyWorkflowLibrary.length) {
        comfyWorkflowLibrary = normalizeComfyWorkflowLibrary([]);
      }
      els.comfyWorkflowPreset.innerHTML = '';
      comfyWorkflowLibrary.forEach(item => {
        const option = document.createElement('option');
        option.value = item.id;
        option.textContent = item.name;
        els.comfyWorkflowPreset.appendChild(option);
      });
      const selected = getSelectedComfyWorkflowPreset();
      if (selected) els.comfyWorkflowPresetNote.value = els.comfyWorkflowPresetNote.value || selected.purpose;
      renderComfyWorkflowLibraryList();
    }

    function renderComfyWorkflowLibraryList() {
      if (!els.comfyWorkflowLibraryList) return;
      els.comfyWorkflowLibraryList.innerHTML = '';
      const item = getSelectedComfyWorkflowPreset();
      if (!item) return;
      const row = document.createElement('div');
      row.className = 'reference-item active';
      const endpoint = item.endpoint ? item.endpoint : '未配置接口';
      const nodeText = item.nodeInfoList && item.nodeInfoList !== '[]' ? '已配置节点映射' : '未配置节点映射';
      row.innerHTML = `
        <div class="reference-info">
          <div class="reference-name">${escapeHtml(item.name)}</div>
          <div class="muted small">${escapeHtml(item.purpose || '')}</div>
          <div class="muted small">${escapeHtml(endpoint)} · ${nodeText} · ${escapeHtml(item.pollTimeout || '3600')} 秒</div>
        </div>
      `;
      els.comfyWorkflowLibraryList.appendChild(row);
    }

    function loadSelectedComfyWorkflowPreset(showMessage = true) {
      const item = getSelectedComfyWorkflowPreset();
      if (!item) return;
      els.comfyWorkflowPresetNote.value = item.purpose || '';
      els.comfyWorkflowEndpoint.value = item.endpoint || '';
      els.comfyNodeInfoList.value = item.nodeInfoList || '[]';
      setIfExists(els.comfyPollTimeout, item.pollTimeout || '3600');
      applyComfyProviderDefaults();
      renderComfyParameterMapper();
      renderComfyWorkflowLibraryList();
      saveSettings();
      if (showMessage) setStatus(`已加载 ComfyUI 工作流：${item.name}`);
    }

    function applySelectedComfyWorkflowPreset() {
      loadSelectedComfyWorkflowPreset(true);
    }

    function saveSelectedComfyWorkflowPreset() {
      const item = getSelectedComfyWorkflowPreset();
      if (!item) return;
      item.purpose = els.comfyWorkflowPresetNote.value.trim() || item.purpose;
      item.endpoint = els.comfyWorkflowEndpoint.value.trim();
      item.nodeInfoList = sanitizeComfyVisualNodeInfoList(els.comfyNodeInfoList.value.trim() || '[]');
      els.comfyNodeInfoList.value = item.nodeInfoList;
      item.pollTimeout = els.comfyPollTimeout.value || '3600';
      renderComfyWorkflowLibraryList();
      saveSettings();
      clearComfyApiImportState();
      setStatus(`已保存 ComfyUI 工作流槽位：${item.name}`);
    }

    function clearComfyApiImportState() {
      comfyParameterCandidates = [];
      if (els.comfyApiWorkflowFile) els.comfyApiWorkflowFile.value = '';
      renderComfyParameterMapper();
    }

    function resetSelectedComfyWorkflowPreset() {
      const item = getSelectedComfyWorkflowPreset();
      const defaults = DEFAULT_COMFY_WORKFLOW_LIBRARY.find(defaultItem => defaultItem.id === item?.id);
      if (!item || !defaults) return;
      item.purpose = defaults.purpose;
      item.endpoint = '';
      item.nodeInfoList = defaults.nodeInfoList || '[]';
      item.pollTimeout = defaults.pollTimeout;
      applySelectedComfyWorkflowPreset();
      renderComfyWorkflowLibraryList();
      setStatus(`已重置 ComfyUI 工作流槽位：${item.name}`);
    }

    function getComfyWorkflowLibraryPayload() {
      return comfyWorkflowLibrary.map(item => ({
        id: item.id,
        name: item.name,
        purpose: item.purpose,
        material_types: Array.isArray(item.materialTypes) ? item.materialTypes : [],
        endpoint: item.endpoint || '',
        node_info_list_json: item.nodeInfoList || '[]',
        poll_timeout_seconds: Number(item.pollTimeout || 3600),
        default_width: item.defaultWidth || '',
        default_height: item.defaultHeight || '',
        default_reference: item.defaultReference || '',
        default_seed: item.defaultSeed || '',
        default_duration: item.defaultDuration || '',
        default_fps: item.defaultFps || '',
        default_middle_frame_reference: item.defaultMiddleFrameReference || '',
        default_middle_frame_asset_reference: item.defaultMiddleFrameAssetReference || '',
        default_middle_frame_reference_hint: item.defaultMiddleFrameReferenceHint || '',
        default_last_frame_reference: item.defaultLastFrameReference || '',
        default_last_frame_asset_reference: item.defaultLastFrameAssetReference || '',
        default_last_frame_reference_hint: item.defaultLastFrameReferenceHint || '',
        default_workflow_mode: item.defaultWorkflowMode || '',
        default_image_task_type: item.defaultImageTaskType || '',
        default_prompt: item.defaultPrompt || '',
        default_negative: item.defaultNegative || '',
        default_asset_reference: item.defaultAssetReference || '',
        default_reference_hint: item.defaultReferenceHint || '',
        mode_configs: Object.fromEntries(Object.entries(item.modeConfigs || {}).map(([mode, config]) => [mode, {
          endpoint: config.endpoint || '',
          node_info_list_json: config.nodeInfoList || '[]',
          poll_timeout_seconds: Number(config.pollTimeout || 3600),
          default_width: config.defaultWidth || '',
          default_height: config.defaultHeight || '',
          default_reference: config.defaultReference || '',
          default_middle_frame_reference: config.defaultMiddleFrameReference || '',
          default_last_frame_reference: config.defaultLastFrameReference || '',
          default_seed: config.defaultSeed || '',
          default_duration: config.defaultDuration || '',
          default_fps: config.defaultFps || '',
          default_prompt: config.defaultPrompt || '',
          default_negative: config.defaultNegative || '',
        }])),
        debug_workflow: Boolean(item.debugWorkflow),
        endpoint_configured: Boolean(item.endpoint),
        node_mapping_configured: Boolean(item.nodeInfoList && item.nodeInfoList !== '[]'),
      }));
    }

    function setIfExists(control, value) {
      if (!value) return;
      const values = Array.from(control.options || []).map(option => option.value);
      if (!values.length || values.includes(value)) control.value = value;
    }

    function bindSettingsPersistence() {
      [
        els.workflow,
        els.productTemplate,
        els.provider,
        els.model,
        els.customModel,
        els.taskTitle,
        els.apiKey,
        els.baseUrl,
        els.modelTimeout,
        els.localModelPreset,
        els.localModelName,
        els.useMemory,
        els.inheritTask,
        els.inheritMode,
        els.useKnowledge,
        els.autoProductionMode,
        els.comfyDebugGate,
        els.composeTool,
        els.finalVideoName,
        els.comfyApiKey,
        els.comfyBaseUrl,
        els.comfyWorkflowEndpoint,
        els.comfyWorkflowPreset,
        els.comfyWorkflowPresetNote,
        els.comfyNodeInfoList,
        els.comfyPollTimeout,
        els.assetQualityGate,
        els.assetMaxAttempts,
        els.assetMinScore,
        els.voiceMode,
        els.voicePreset,
        els.voiceReferenceAudioPath,
        els.voiceReferenceText,
        els.voiceCommandTemplate,
        els.voiceTimeout,
        els.imageTool,
        els.imagePositivePrompt,
        els.imageModel,
        els.imageSize,
        els.imageCount,
        els.imageStyle,
        els.imageQuality,
        els.imageApiKey,
        els.imageBaseUrl,
        els.imageWorkflowEndpoint,
        els.imageInstanceType,
        els.imageNodeInfoList,
        els.imagePollTimeout,
        els.imageNegativePrompt,
        els.imageConsistency,
        els.imageSeed,
        els.imageGuidance,
        els.imageSteps,
        els.imageDenoise,
        els.imageSampler,
        els.imageControl,
        els.videoTool,
        els.videoPositivePrompt,
        els.videoModel,
        els.videoAspect,
        els.videoDuration,
        els.videoStyle,
        els.videoPromptNotes,
        els.videoApiKey,
        els.videoBaseUrl,
        els.videoWorkflowEndpoint,
        els.videoNodeInfoList,
        els.videoPollTimeout,
        els.videoNegativePrompt,
        els.videoSeed,
        els.videoFps,
        els.videoMotionStrength,
        els.videoCameraMotion,
        els.videoResolution,
        els.videoGuidance,
        els.videoFrames,
        els.videoImageStrength,
        els.videoCameraPath,
        els.videoAudioNotes,
        els.videoAdvancedParams,
        els.referenceRole,
        els.referenceNote,
      ].forEach(control => {
        control.addEventListener('change', saveSettings);
        control.addEventListener('input', saveSettings);
      });
      els.voiceMode.addEventListener('change', syncVoiceCommandTemplateForMode);
    }

    function applyImageProviderDefaults() {
      if (els.imageTool.value !== 'runninghub') return;
      if (!els.imageBaseUrl.value.trim()) {
        els.imageBaseUrl.value = 'https://www.runninghub.cn/openapi/v2';
      }
      if (!els.imageWorkflowEndpoint.value.trim()) {
        els.imageWorkflowEndpoint.value = '/run/workflow/2048294089858228226';
      }
      if (!els.imageNodeInfoList.value.trim()) {
        els.imageNodeInfoList.value = '[]';
      }
      saveSettings();
    }

    function applyVideoProviderDefaults() {
      if (els.videoTool.value !== 'runninghub') return;
      if (!els.videoBaseUrl.value.trim()) {
        els.videoBaseUrl.value = 'https://www.runninghub.cn/openapi/v2';
      }
      if (!els.videoWorkflowEndpoint.value.trim()) {
        els.videoWorkflowEndpoint.value = '/run/ai-app/2066043648160133122';
      }
      if (!els.videoNodeInfoList.value.trim()) {
        els.videoNodeInfoList.value = '[]';
      }
      saveSettings();
    }

    function applyComfyProviderDefaults() {
      if (els.composeTool.value !== 'runninghub' && els.autoProductionMode.value !== 'comfy_full') return;
      if (!els.comfyBaseUrl.value.trim()) {
        els.comfyBaseUrl.value = 'https://www.runninghub.cn/openapi/v2';
      }
      if (!els.comfyNodeInfoList.value.trim()) {
        els.comfyNodeInfoList.value = '[]';
      }
      saveSettings();
    }

    function isComfyConnection(value) {
      return Array.isArray(value) && value.length === 2 && (typeof value[0] === 'string' || typeof value[0] === 'number') && typeof value[1] === 'number';
    }

    function isMappableComfyValue(value) {
      return value === null || ['string', 'number', 'boolean'].includes(typeof value);
    }

    function guessComfySource(candidate, textIndex) {
      const type = candidate.classType.toLowerCase();
      const field = candidate.fieldName.toLowerCase();
      const value = String(candidate.value ?? '').trim();
      const title = String(candidate.title || '').toLowerCase();
      const nodeId = String(candidate.nodeId || '');
      if (type.includes('cliptextencode') && field === 'text') {
        const haystack = (title + ' ' + value).toLowerCase();
        const looksNegative = nodeId === '2612'
          || title.includes('negative')
          || haystack.includes('negative prompt')
          || haystack.includes('bad hands')
          || haystack.includes('watermark')
          || haystack.includes('identity drift')
          || haystack.includes('low quality')
          || haystack.includes('deformed');
        return looksNegative || textIndex > 0 ? '{{negative_prompt}}' : '{{prompt}}';
      }
      if (type.includes('loadimage') && field === 'image') return '{{reference_image}}';
      if (field.includes('prompt') && field.includes('negative')) return '{{negative_prompt}}';
      if (field.includes('prompt')) return '{{prompt}}';
      return 'fixed';
    }

    function comfySourceLabel(value) {
      const labels = {
        fixed: '固定值',
        '{{prompt}}': '主提示词',
        '{{negative_prompt}}': '负向提示词',
        '{{image_prompt}}': '生图提示词',
        '{{video_prompt}}': '视频提示词',
        '{{reference_image}}': '参考图文件名/URL',
        '{{input_base_image}}': '语义槽位：主底图',
        '{{input_middle_frame}}': '语义槽位：中帧',
        '{{input_last_frame}}': '语义槽位：尾帧',
        '{{input_mask_image}}': '语义槽位：修复蒙版',
        '{{input_reference_style}}': '语义槽位：风格参考',
        '{{input_audio_file}}': '语义槽位：口型音频',
        '{{payload}}': '完整参数包',
      };
      return labels[value] || value;
    }

    function parseComfyManualValue(raw, original) {
      if (typeof original === 'number') {
        const parsed = Number(raw);
        return Number.isFinite(parsed) ? parsed : original;
      }
      if (typeof original === 'boolean') {
        return String(raw).trim().toLowerCase() === 'true';
      }
      if (raw === 'null') return null;
      return raw;
    }

    function extractComfyApiCandidates(data) {
      const prompt = findComfyApiPromptObject(data);
      if (!prompt) {
        if (data && typeof data === 'object' && Array.isArray(data.nodes)) {
          throw new Error('导入的是 ComfyUI 画布 workflow JSON，不是 API JSON。画布节点缺少可靠 fieldName，无法生成 RunningHub nodeInfoList；请在 ComfyUI 导出 API 格式 JSON。');
        }
        throw new Error('没有在 JSON 中找到 ComfyUI API prompt。请导入 ComfyUI API 格式，或包含 prompt/workflow/api 字段的 RunningHub API JSON。');
      }
      const entries = Object.entries(prompt);
      const candidates = [];
      let textIndex = 0;
      for (const [nodeId, node] of entries) {
        const inputs = node.inputs || {};
        for (const [fieldName, value] of Object.entries(inputs)) {
          if (isComfyConnection(value) || !isMappableComfyValue(value)) continue;
          const candidate = {
            id: `${nodeId}.${fieldName}`,
            nodeId: String(nodeId),
            classType: String(node.class_type || ''),
            title: String(node._meta?.title || node.title || ''),
            fieldName,
            value,
            source: 'fixed',
            enabled: false,
          };
          candidate.source = guessComfySource(candidate, textIndex);
          candidate.enabled = candidate.source !== 'fixed' || ['width', 'height', 'seed', 'steps', 'cfg', 'denoise', 'batch_size'].includes(fieldName);
          if (candidate.classType.toLowerCase().includes('cliptextencode') && fieldName === 'text') textIndex += 1;
          candidates.push(candidate);
        }
      }
      const priority = ['text', 'image', 'width', 'height', 'batch_size', 'seed', 'steps', 'cfg', 'denoise'];
      candidates.sort((a, b) => {
        const ai = priority.indexOf(a.fieldName);
        const bi = priority.indexOf(b.fieldName);
        return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi) || Number(a.nodeId) - Number(b.nodeId);
      });
      return candidates;
    }

    function isComfyApiPromptObject(value) {
      if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
      const entries = Object.entries(value);
      if (!entries.length) return false;
      return entries.every(([nodeId, node]) => /^\d+$/.test(String(nodeId)) && node && typeof node === 'object' && !Array.isArray(node) && node.class_type);
    }

    function findComfyApiPromptObject(data) {
      if (isComfyApiPromptObject(data)) return data;
      const seen = new Set();
      const preferredKeys = ['prompt', 'workflow', 'api', 'api_json', 'apiJson', 'template', 'graph'];
      const walk = value => {
        if (typeof value === 'string') {
          const trimmed = value.trim();
          if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
            try {
              return walk(JSON.parse(trimmed));
            } catch {
              return null;
            }
          }
          return null;
        }
        if (!value || typeof value !== 'object' || seen.has(value)) return null;
        seen.add(value);
        if (isComfyApiPromptObject(value)) return value;
        if (!Array.isArray(value)) {
          for (const key of preferredKeys) {
            const found = walk(value[key]);
            if (found) return found;
          }
        }
        const children = Array.isArray(value) ? value : Object.values(value);
        for (const child of children) {
          const found = walk(child);
          if (found) return found;
        }
        return null;
      };
      return walk(data);
    }

    function renderComfyParameterMapper() {
      if (!comfyParameterCandidates.length) {
        els.comfyParameterMapper.innerHTML = '<div class="muted small">导入 ComfyUI API JSON 后，这里会显示可传参节点。</div>';
        return;
      }
      els.comfyParameterMapper.innerHTML = '';
      const head = document.createElement('div');
      head.className = 'muted small comfy-parameter-head';
      head.textContent = `已识别 ${comfyParameterCandidates.length} 个可传参字段。勾选要传给 RunningHub 的参数，系统会自动生成 nodeInfoList。`;
      const panel = document.createElement('div');
      panel.className = 'comfy-parameter-panel';
      panel.appendChild(head);
      const sourceOptions = ['fixed', '{{prompt}}', '{{negative_prompt}}', '{{image_prompt}}', '{{video_prompt}}', '{{input_base_image}}', '{{input_middle_frame}}', '{{input_last_frame}}', '{{input_mask_image}}', '{{input_reference_style}}', '{{input_audio_file}}', '{{reference_image}}', '{{payload}}'];
      comfyParameterCandidates.forEach((candidate, index) => {
        const item = document.createElement('div');
        item.className = 'comfy-parameter-row';
        const left = document.createElement('label');
        left.className = 'comfy-parameter-left';
        const line = document.createElement('span');
        line.className = 'comfy-parameter-name';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = candidate.enabled;
        checkbox.onchange = () => {
          candidate.enabled = checkbox.checked;
          updateComfyNodeInfoFromCandidates();
        };
        line.appendChild(checkbox);
        line.append(` #${candidate.nodeId} ${candidate.classType}.${candidate.fieldName}`);
        const meta = document.createElement('span');
        meta.className = 'muted small comfy-parameter-value';
        meta.textContent = `当前值：${String(candidate.value ?? '').slice(0, 80)}`;
        left.appendChild(line);
        left.appendChild(meta);

        const select = document.createElement('select');
        sourceOptions.forEach(value => {
          const option = document.createElement('option');
          option.value = value;
          option.textContent = comfySourceLabel(value);
          select.appendChild(option);
        });
        select.value = candidate.source;
        select.onchange = () => {
          candidate.source = select.value;
          updateComfyNodeInfoFromCandidates();
        };

        const input = document.createElement('input');
        input.value = String(candidate.value ?? '');
        input.placeholder = '固定值';
        input.oninput = () => {
          candidate.value = parseComfyManualValue(input.value, candidate.value);
          updateComfyNodeInfoFromCandidates();
        };

        item.appendChild(left);
        item.appendChild(select);
        item.appendChild(input);
        panel.appendChild(item);
      });
      els.comfyParameterMapper.appendChild(panel);
      updateComfyNodeInfoFromCandidates();
    }

    function updateComfyNodeInfoFromCandidates() {
      const nodeInfo = buildNodeInfoFromComfyCandidates(comfyParameterCandidates);
      els.comfyNodeInfoList.value = JSON.stringify(nodeInfo, null, 2);
      saveSettings();
    }

    function buildNodeInfoFromComfyCandidates(candidates, onlyEnabled = true) {
      return candidates
        .filter(candidate => !onlyEnabled || candidate.enabled)
        .map(candidate => ({
          nodeId: candidate.nodeId,
          fieldName: candidate.fieldName,
          fieldValue: candidate.source === 'fixed' ? candidate.value : candidate.source,
        }));
    }

    function findEndpointInImportedJson(data) {
      const seen = new Set();
      const keys = ['endpoint', 'workflow_endpoint', 'workflowEndpoint', 'api', 'path', 'url'];
      const walk = value => {
        if (!value || typeof value !== 'object' || seen.has(value)) return '';
        seen.add(value);
        for (const key of keys) {
          const candidate = value[key];
          if (typeof candidate === 'string') {
            const trimmed = candidate.trim();
            const match = trimmed.match(/\/run\/(?:workflow|ai-app)\/[A-Za-z0-9_-]+/);
            if (match) return match[0];
            if (trimmed.startsWith('/run/workflow/') || trimmed.startsWith('/run/ai-app/')) return trimmed;
          }
        }
        for (const child of Object.values(value)) {
          const found = walk(child);
          if (found) return found;
        }
        return '';
      };
      return walk(data);
    }

    function nodeInfoFromImportedJson(data) {
      if (Array.isArray(data)) return data;
      if (!data || typeof data !== 'object') throw new Error('JSON 顶层必须是对象或 nodeInfoList 数组。');
      const direct = data.nodeInfoList || data.node_info_list || data.node_info_list_json || data.nodeInfo || data.node_info;
      if (Array.isArray(direct)) return direct;
      if (typeof direct === 'string' && direct.trim()) return JSON.parse(direct);
      const candidates = extractComfyApiCandidates(data);
      return buildNodeInfoFromComfyCandidates(candidates, false);
    }

    async function analyzeComfyApiWorkflowFile() {
      const file = els.comfyApiWorkflowFile.files && els.comfyApiWorkflowFile.files[0];
      if (!file) return;
      try {
        const text = await file.text();
        const data = JSON.parse(text);
        comfyParameterCandidates = extractComfyApiCandidates(data);
        renderComfyParameterMapper();
        setStatus(`已识别 ComfyUI API JSON：${file.name}`);
      } catch (err) {
        comfyParameterCandidates = [];
        renderComfyParameterMapper();
        setStatus(err.message, true);
      }
    }

    async function analyzeComfyDebugApiWorkflowFile() {
      const file = els.comfyDebugApiWorkflowFile?.files && els.comfyDebugApiWorkflowFile.files[0];
      if (!file) return;
      try {
        const text = await file.text();
        const data = JSON.parse(text);
        const nodeInfo = nodeInfoFromImportedJson(data);
        els.comfyDebugNodeInfoList.value = JSON.stringify(nodeInfo, null, 2);
        const endpoint = findEndpointInImportedJson(data);
        if (endpoint && els.comfyDebugEndpoint) {
          els.comfyDebugEndpoint.value = endpoint;
        }
        saveCurrentComfyDebugUiState();
        saveSettings();
        setStatus(endpoint
          ? `已导入调试 API JSON：${file.name}，识别 ${nodeInfo.length} 个可传参字段，并识别 Endpoint`
          : `已导入调试 API JSON：${file.name}，识别 ${nodeInfo.length} 个可传参字段；未识别 Endpoint，可手动填写或留空使用槽位配置`,
          false);
      } catch (err) {
        setStatus(err.message || '调试 API JSON 识别失败', true);
      }
    }

    function renderLocalModelPresets() {
      const current = els.localModelPreset.value;
      els.localModelPreset.innerHTML = '<option value="">不使用本地预设</option>';
      for (const preset of localModelPresets) {
        const option = document.createElement('option');
        option.value = preset.id;
        option.textContent = preset.name || preset.id;
        els.localModelPreset.appendChild(option);
      }
      setIfExists(els.localModelPreset, current);
      renderLocalModelNames();
    }

    function renderLocalModelNames() {
      const current = els.localModelName.value;
      const preset = localModelPresets.find(item => item.id === els.localModelPreset.value);
      els.localModelName.innerHTML = '';
      if (!preset) {
        els.localModelName.innerHTML = '<option value="">先选择本地模型服务</option>';
        return;
      }
      const models = preset.models || [];
      for (const modelName of models) {
        const option = document.createElement('option');
        option.value = modelName;
        option.textContent = modelName;
        els.localModelName.appendChild(option);
      }
      if (!models.length) {
        els.localModelName.innerHTML = '<option value="">请手动输入模型名</option>';
      }
      setIfExists(els.localModelName, current);
    }

    function applyLocalModelPreset() {
      const preset = localModelPresets.find(item => item.id === els.localModelPreset.value);
      renderLocalModelNames();
      if (!preset) {
        saveSettings();
        return;
      }
      els.provider.value = 'openai';
      els.baseUrl.value = preset.base_url || '';
      els.apiKey.value = preset.api_key || 'local';
      els.model.value = 'custom';
      const modelName = els.localModelName.value || (preset.models || [])[0] || '';
      els.customModel.value = modelName;
      syncCustomModelState(false);
      saveSettings();
    }

    function applyLocalModelName() {
      if (els.localModelName.value) {
        els.model.value = 'custom';
        els.customModel.value = els.localModelName.value;
        syncCustomModelState(false);
      }
      saveSettings();
    }

    function applyLocalOfflineMode() {
      els.provider.value = 'openai';
      els.apiKey.value = 'local';
      els.baseUrl.value = OLLAMA_BASE_URL;
      els.modelTimeout.value = '900';
      setIfExists(els.localModelPreset, 'ollama');
      renderLocalModelNames();
      setIfExists(els.localModelName, DEFAULT_LOCAL_MODEL);
      els.model.value = 'custom';
      els.customModel.value = DEFAULT_LOCAL_MODEL;
      syncCustomModelState(false);
      saveSettings();
      setStatus(`已切换到本地离线模式：${DEFAULT_LOCAL_MODEL}`);
    }

    async function testModelConnection() {
      const model = els.model.value === 'custom' ? els.customModel.value.trim() : els.model.value;
      if (!model) {
        setStatus('请先选择或填写模型名', true);
        return;
      }
      setStatus('正在测试模型接口');
      try {
        const result = await api('/api/test-model', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            api_key: els.apiKey.value.trim(),
            base_url: els.baseUrl.value.trim(),
            model,
          }),
        });
        setStatus(`模型接口可用：${result.model}`);
      } catch (err) {
        setStatus(`模型接口不可用：${err.message}`, true);
      }
    }

    async function ensureLocalModelReady(model) {
      const isOllama = els.provider.value === 'openai' && els.baseUrl.value.trim().replace(/\/$/, '') === OLLAMA_BASE_URL;
      if (!isOllama) return;
      setStatus(`正在检测本地模型：${model}`);
      await api('/api/test-model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          api_key: els.apiKey.value.trim() || 'local',
          base_url: OLLAMA_BASE_URL,
          model,
        }),
      });
    }

    async function loadKnowledgeList() {
      const data = await api('/api/knowledge');
      els.knowledgeList.innerHTML = '';
      if (!data.files.length) {
        els.knowledgeList.innerHTML = '<div class="muted small">my_knowledge_base 暂无知识文件</div>';
        return;
      }
      for (const file of data.files) {
        const item = document.createElement('div');
        item.className = 'reference-item';
        const info = document.createElement('div');
        info.className = 'reference-info';
        const name = document.createElement('div');
        name.className = 'reference-name';
        name.textContent = file.name;
        const meta = document.createElement('div');
        meta.className = 'muted small';
        meta.textContent = `${Math.max(1, Math.round(file.size / 1024))} KB · ${file.mtime}`;
        info.appendChild(name);
        info.appendChild(meta);
        item.appendChild(info);
        els.knowledgeList.appendChild(item);
      }
    }

    async function loadSystemHealth() {
      setHealthStatus('正在检查系统状态');
      const data = await api('/api/system-health');
      renderSystemHealth(data.checks || []);
      const errors = (data.checks || []).filter(item => item.status === 'error').length;
      const warns = (data.checks || []).filter(item => item.status === 'warn').length;
      if (errors) {
        setHealthStatus(`发现 ${errors} 项异常`, true);
      } else if (warns) {
        setHealthStatus(`发现 ${warns} 项提醒`);
      } else {
        setHealthStatus('系统状态正常');
      }
    }

    function renderSystemHealth(checks) {
      els.healthGrid.innerHTML = '';
      for (const check of checks) {
        const card = document.createElement('button');
        card.type = 'button';
        card.className = `health-card ${check.status || 'warn'}`;
        const title = document.createElement('strong');
        title.textContent = check.name || '';
        const state = document.createElement('div');
        state.className = 'health-state';
        state.textContent = check.label || check.status || '';
        const detail = document.createElement('div');
        detail.className = 'muted small';
        detail.textContent = check.detail || '';
        card.appendChild(title);
        card.appendChild(state);
        card.appendChild(detail);
        els.healthGrid.appendChild(card);
      }
    }

    async function uploadKnowledgeFile() {
      const file = (els.knowledgeFile.files || [])[0];
      if (!file) {
        setStatus('请选择 .md/.txt/.json/.csv 知识文件', true);
        return;
      }
      setStatus('正在上传知识文件');
      try {
        const contentBase64 = await fileToBase64(file);
        const result = await api('/api/upload-knowledge', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: file.name,
            content_base64: contentBase64,
          }),
        });
        els.knowledgeFile.value = '';
        setStatus(`知识文件已上传：${result.name}`);
        await loadKnowledgeList();
      } catch (err) {
        setStatus(err.message, true);
      }
    }

    function renderReferenceFiles() {
      els.referenceList.innerHTML = '';
      if (!selectedReferenceFiles.length) {
        els.referenceList.innerHTML = '<div class="muted small">未选择参考图</div>';
        return;
      }
      selectedReferenceFiles.forEach((file, index) => {
        if (!referencePreviewUrls.has(file)) {
          referencePreviewUrls.set(file, URL.createObjectURL(file));
        }
        const item = document.createElement('div');
        item.className = 'reference-item';
        const preview = document.createElement('img');
        preview.className = 'reference-preview';
        preview.src = referencePreviewUrls.get(file);
        preview.alt = file.name;
        const info = document.createElement('div');
        info.className = 'reference-info';
        const name = document.createElement('div');
        name.className = 'reference-name';
        name.textContent = file.name;
        const meta = document.createElement('div');
        meta.className = 'muted small';
        meta.textContent = `${Math.max(1, Math.round(file.size / 1024))} KB`;
        const remove = document.createElement('button');
        remove.className = 'icon-btn danger';
        remove.type = 'button';
        remove.title = '移除参考图';
        remove.textContent = '×';
        remove.onclick = () => {
          const previewUrl = referencePreviewUrls.get(file);
          if (previewUrl) URL.revokeObjectURL(previewUrl);
          referencePreviewUrls.delete(file);
          selectedReferenceFiles.splice(index, 1);
          renderReferenceFiles();
        };
        info.appendChild(name);
        info.appendChild(meta);
        item.appendChild(preview);
        item.appendChild(info);
        item.appendChild(remove);
        els.referenceList.appendChild(item);
      });
    }

    function clearReferenceFiles() {
      referencePreviewUrls.forEach(url => URL.revokeObjectURL(url));
      referencePreviewUrls = new Map();
      selectedReferenceFiles = [];
      els.referenceImages.value = '';
      renderReferenceFiles();
    }

    function fileToBase64(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          const result = String(reader.result || '');
          resolve(result.includes(',') ? result.split(',')[1] : result);
        };
        reader.onerror = () => reject(reader.error || new Error('读取参考图失败'));
        reader.readAsDataURL(file);
      });
    }

    async function uploadReferenceImages() {
      const uploaded = [];
      for (const file of selectedReferenceFiles) {
        const contentBase64 = await fileToBase64(file);
        const result = await api('/api/upload-reference-image', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: file.name,
            content_base64: contentBase64,
            role: els.referenceRole.value,
            note: els.referenceNote.value.trim(),
          }),
        });
        uploaded.push(result);
      }
      return uploaded;
    }

    async function uploadVoiceReferenceAudio() {
      const file = els.voiceReferenceFile.files && els.voiceReferenceFile.files[0];
      if (!file) return els.voiceReferenceAudioPath.value.trim();
      const contentBase64 = await fileToBase64(file);
      const result = await api('/api/upload-voice-sample', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: file.name,
          content_base64: contentBase64,
        }),
      });
      els.voiceReferenceAudioPath.value = result.stored_path || '';
      saveSettings();
      return els.voiceReferenceAudioPath.value.trim();
    }

    function defaultVoxCPM2CommandTemplate() {
      return '';
    }

    function normalizeVoiceTimeout(value) {
      const allowed = new Set(['180', '300', '600', '900', '1800']);
      const text = String(value || '').trim();
      if (!text) return '300';
      if (allowed.has(text)) return text;
      const seconds = Number(text);
      if (!Number.isFinite(seconds)) return '300';
      if (seconds <= 180) return '180';
      if (seconds <= 300) return '300';
      if (seconds <= 600) return '600';
      if (seconds <= 900) return '900';
      if (seconds <= 1800) return '1800';
      if (seconds <= 3600) return '3600';
      return '5400';
    }

    function syncVoiceCommandTemplateForMode() {
      const current = els.voiceCommandTemplate.value.trim();
      const knownDefaults = [
        '',
        'voxcpm clone --text-file {text_file} --reference-audio {reference_audio} --output {output_file}',
        'voxcpm tts --text-file {text_file} --voice {voice_preset} --output {output_file}',
      ];
      if (knownDefaults.includes(current)) {
        els.voiceCommandTemplate.value = defaultVoxCPM2CommandTemplate();
      }
      saveSettings();
    }

    function selectedVoicePresetLabel() {
      const option = els.voicePreset?.selectedOptions?.[0];
      return option ? option.textContent.trim() : '';
    }

    function syncCustomModelState(focusWhenCustom = true) {
      const custom = els.model.value === 'custom';
      els.customModel.disabled = !custom;
      if (custom && focusWhenCustom) els.customModel.focus();
    }

    async function loadTasks() {
      const data = await api('/api/tasks');
      if (!data.tasks.length) {
        els.taskList.innerHTML = '<div class="muted small">暂无任务输出</div>';
        syncInheritTaskOptions([]);
        return [];
      }
      els.taskList.innerHTML = '';
      syncInheritTaskOptions(data.tasks);
      const tasks = [...data.tasks].sort((a, b) => {
        if (activeRunTaskName && a.name === activeRunTaskName) return -1;
        if (activeRunTaskName && b.name === activeRunTaskName) return 1;
        return 0;
      });
      for (const task of tasks) {
        const btn = document.createElement('button');
        btn.className = `item ${selectedTask === task.name ? 'active' : ''}`;
        const title = task.task_title || task.workflow || task.name;
        const meta = task.task_title ? `${task.workflow || ''} / ${task.name}` : task.name;
        btn.innerHTML = `<span class="item-main"><span class="item-title">${title}</span><span class="item-meta">${meta}</span></span><span class="icon-btn danger" title="删除任务" aria-label="删除任务">×</span>`;
        btn.onclick = () => selectTask(task.name);
        btn.querySelector('.icon-btn').onclick = (event) => {
          event.stopPropagation();
          deleteTask(task.name);
        };
        els.taskList.appendChild(btn);
      }
      return data.tasks;
    }

    function syncInheritTaskOptions(tasks) {
      const current = els.inheritTask.value;
      els.inheritTask.innerHTML = '<option value="">不继承</option>';
      for (const task of tasks) {
        const option = document.createElement('option');
        option.value = task.name;
        option.textContent = `${task.task_title || task.workflow || task.name} / ${task.name}`;
        els.inheritTask.appendChild(option);
      }
      setIfExists(els.inheritTask, current);
    }

    async function deleteTask(name) {
      if (!confirm(`确定删除任务输出？\n\n${name}\n\n素材库中来源于该任务的素材也会同步删除。`)) return;
      setStatus('正在删除任务');
      try {
        const result = await api('/api/delete-task', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name }),
        });
        if (selectedTask === name) {
          selectedTask = null;
          selectedFile = null;
          selectedTaskSummary = {};
          selectedTaskStatus = null;
          selectedTaskAllowedActions = [];
          els.viewerTitle.textContent = '未选择任务';
          els.viewerMeta.textContent = '运行后会在这里查看输出文件';
          els.fileTabs.innerHTML = '';
          els.fileContent.value = '选择左侧任务，或运行一个新任务。';
          renderOutputOverview(null);
          syncOutputButtons();
        }
        const removedAssets = Number(result?.removed_assets || 0);
        setStatus(removedAssets ? `任务已删除，并清理 ${removedAssets} 个素材库素材` : '任务已删除');
        await Promise.all([loadTasks(), loadAssetLibrary()]);
      } catch (err) {
        setStatus(err.message, true);
      }
    }

    function setStaffStatus(text, isError = false, showPopup = true) {
      els.staffStatus.textContent = text;
      els.staffStatus.classList.toggle('error', isError);
      if (showPopup) showToast(text, isError);
    }

    async function loadStaffList() {
      const data = await api('/api/staff');
      const keyword = (els.staffFilter.value || '').trim().toLowerCase();
      const allStaff = data.staff || [];
      const showArchived = Boolean(els.showArchivedStaff?.checked);
      const staffItems = allStaff.filter(staff => {
        if (!showArchived && !isActiveLongVideoStaff(staff)) return false;
        const text = `${staff.name || ''} ${staff.display_name || ''} ${staff.role || ''}`.toLowerCase();
        return !keyword || text.includes(keyword);
      });
      els.staffList.innerHTML = '';
      const archivedCount = allStaff.filter(staff => !isActiveLongVideoStaff(staff)).length;
      setStaffStatus(
        `长视频员工 ${allStaff.length - archivedCount} 位${showArchived ? `，归档 ${archivedCount} 位` : ''}${keyword ? `，筛选出 ${staffItems.length} 位` : ''}`,
        false,
        false
      );
      if (!staffItems.length) {
        els.staffList.innerHTML = '<div class="muted small">暂无匹配员工</div>';
        return;
      }
      for (const staff of staffItems) {
        const btn = document.createElement('button');
        btn.className = `staff-card ${selectedStaff === staff.name ? 'active' : ''}`;
        const title = document.createElement('strong');
        title.textContent = staff.display_name || staff.name;
        const meta = document.createElement('span');
        meta.className = 'muted small staff-meta';
        meta.textContent = isActiveLongVideoStaff(staff) ? staff.name : `${staff.name} · 归档`;
        btn.appendChild(title);
        btn.appendChild(meta);
        if (staff.role) {
          const role = document.createElement('span');
          role.className = 'small staff-role';
          role.textContent = staff.role;
          btn.appendChild(role);
        }
        btn.onclick = () => selectStaff(staff.name);
        els.staffList.appendChild(btn);
      }
    }

    async function selectStaff(name) {
      selectedStaff = name;
      const data = await api(`/api/staff-detail?name=${encodeURIComponent(name)}`);
      els.staffName.value = data.name;
      els.staffAgentMd.value = data.agent_md || '';
      els.staffFlowRule.value = data.flow_rule_json || '{}';
      els.deleteStaffBtn.disabled = false;
      setStaffStatus(`已选择：${name}`);
      await loadStaffList();
    }

    function defaultStaffAgentMd(name) {
      return `---\nname: ${name.replace(/^\\d+_/, '')}\ndescription: 请填写这个数字员工的职责。\nemoji: 🧩\ncolor: blue\n---\n\n# ${name.replace(/^\\d+_/, '')}\n\n## 核心职责\n\n- 请填写职责 1。\n- 请填写职责 2。\n\n## 输出格式\n\n请始终输出中文 Markdown。\n`;
    }

    function defaultStaffFlowRule(name) {
      return JSON.stringify({
        agent_id: name,
        agent_name: name.replace(/^\\d+_/, ''),
        role: 'custom_staff',
        inputs: ['用户需求'],
        outputs: ['员工输出'],
        handoff_to: [],
        quality_gate: ['输出清晰', '可交给下游继续使用'],
      }, null, 2);
    }

    function newStaff() {
      const name = prompt('请输入员工文件夹名，例如：20_销售话术专员');
      if (!name) return;
      selectedStaff = null;
      els.staffName.value = name.trim();
      els.staffAgentMd.value = defaultStaffAgentMd(name.trim());
      els.staffFlowRule.value = defaultStaffFlowRule(name.trim());
      els.deleteStaffBtn.disabled = true;
      setStaffStatus('正在编辑新员工，点击“保存员工”写入');
    }

    async function saveStaff() {
      const name = els.staffName.value.trim();
      if (!name) {
        setStaffStatus('员工文件夹名不能为空', true);
        return;
      }
      try {
        JSON.parse(els.staffFlowRule.value || '{}');
      } catch (err) {
        setStaffStatus(`flow_rule.json 不是合法 JSON：${err.message}`, true);
        return;
      }
      try {
        const result = await api('/api/save-staff', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name,
            agent_md: els.staffAgentMd.value,
            flow_rule_json: els.staffFlowRule.value,
          }),
        });
        selectedStaff = result.name;
        setStaffStatus(`已保存：${result.name}`);
        await loadStaffList();
        await loadConfig();
      } catch (err) {
        setStaffStatus(err.message, true);
      }
    }

    async function deleteStaff() {
      if (!selectedStaff) return;
      if (!confirm(`确定删除这个数字员工？\n\n${selectedStaff}\n\n这会删除 my_custom_staff 下对应文件夹。`)) return;
      try {
        await api('/api/delete-staff', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: selectedStaff }),
        });
        selectedStaff = null;
        els.staffName.value = '';
        els.staffAgentMd.value = '';
        els.staffFlowRule.value = '';
        els.deleteStaffBtn.disabled = true;
        setStaffStatus('员工已删除');
        await loadStaffList();
        await loadConfig();
      } catch (err) {
        setStaffStatus(err.message, true);
      }
    }

    function setWorkflowEditorStatus(text, isError = false, showPopup = true) {
      els.workflowEditorStatus.textContent = text;
      els.workflowEditorStatus.classList.toggle('error', isError);
      if (showPopup) showToast(text, isError);
    }

    async function loadWorkflowList() {
      const data = await api('/api/workflows');
      const showArchived = Boolean(els.showArchivedWorkflows?.checked);
      staffOptions = (data.staff || staffOptions).filter(staff => showArchived || isActiveLongVideoStaff(staff));
      const allWorkflows = data.workflows || [];
      const workflowItems = allWorkflows.filter(workflow => showArchived || isActiveLongVideoWorkflow(workflow));
      els.workflowList.innerHTML = '';
      const archivedCount = allWorkflows.filter(workflow => !isActiveLongVideoWorkflow(workflow)).length;
      setWorkflowEditorStatus(
        `长视频工作流 ${allWorkflows.length - archivedCount} 个${showArchived ? `，归档 ${archivedCount} 个` : ''}`,
        false,
        false
      );
      if (!workflowItems.length) {
        els.workflowList.innerHTML = '<div class="muted small">暂无工作流</div>';
        return;
      }
      for (const workflow of workflowItems) {
        const btn = document.createElement('button');
        btn.className = `staff-card ${selectedWorkflow === workflow.stem ? 'active' : ''}`;
        const title = document.createElement('strong');
        title.textContent = workflow.name || workflow.stem;
        const file = document.createElement('span');
        file.className = 'muted small';
        file.textContent = isActiveLongVideoWorkflow(workflow)
          ? (workflow.file || `${workflow.stem}.json`)
          : `${workflow.file || `${workflow.stem}.json`} · 归档`;
        const description = document.createElement('span');
        description.className = 'muted small';
        description.textContent = workflow.description || '';
        btn.appendChild(title);
        btn.appendChild(file);
        btn.appendChild(description);
        btn.onclick = () => selectWorkflow(workflow.stem);
        els.workflowList.appendChild(btn);
      }
    }

    async function selectWorkflow(name) {
      selectedWorkflow = name;
      const data = await api(`/api/workflow-detail?name=${encodeURIComponent(name)}`);
      const workflow = data.workflow || {};
      els.workflowFile.value = data.file || `${data.name}.json`;
      els.workflowName.value = workflow.name || data.name || '';
      els.workflowDescription.value = workflow.description || '';
      workflowEditorBase = workflow;
      workflowEditorSteps = normalizeWorkflowSteps(workflow.steps || []);
      els.deleteWorkflowBtn.disabled = false;
      renderWorkflowSteps();
      setWorkflowEditorStatus(`已选择：${data.file || data.name}`);
      await loadWorkflowList();
    }

    function normalizeWorkflowSteps(steps) {
      return steps.map((step, index) => ({
        step: index + 1,
        agent: String(step.agent || step.agent_id || '').trim(),
        task: String(step.task || step.instruction || '').trim(),
        output: String(step.output || step.expected_output || '').trim(),
      }));
    }

    function newWorkflow() {
      selectedWorkflow = null;
      const stamp = new Date().toISOString().slice(0, 10).replaceAll('-', '');
      els.workflowFile.value = `workflow_新工作流_${stamp}`;
      els.workflowName.value = '新工作流';
      els.workflowDescription.value = '';
      workflowEditorBase = {};
      workflowEditorSteps = [];
      els.deleteWorkflowBtn.disabled = true;
      renderWorkflowSteps();
      setWorkflowEditorStatus('正在编辑新工作流，点击“保存工作流”写入文件');
      loadWorkflowList().catch(err => setWorkflowEditorStatus(err.message, true));
    }

    function addWorkflowStep() {
      workflowEditorSteps.push({
        step: workflowEditorSteps.length + 1,
        agent: staffOptions[0] || '',
        task: '',
        output: '',
      });
      renderWorkflowSteps();
    }

    function moveWorkflowStep(index, delta) {
      const next = index + delta;
      if (next < 0 || next >= workflowEditorSteps.length) return;
      const current = workflowEditorSteps[index];
      workflowEditorSteps[index] = workflowEditorSteps[next];
      workflowEditorSteps[next] = current;
      renderWorkflowSteps();
    }

    function deleteWorkflowStep(index) {
      workflowEditorSteps.splice(index, 1);
      renderWorkflowSteps();
    }

    function renderWorkflowSteps() {
      els.workflowSteps.innerHTML = '';
      if (!workflowEditorSteps.length) {
        els.workflowSteps.innerHTML = '<div class="muted small">暂无步骤，点击“新增步骤”开始组装。</div>';
        return;
      }
      workflowEditorSteps.forEach((step, index) => {
        step.step = index + 1;
        const item = document.createElement('div');
        item.className = 'workflow-step';

        const head = document.createElement('div');
        head.className = 'workflow-step-head';
        const title = document.createElement('strong');
        title.textContent = `第 ${index + 1} 步`;
        const actions = document.createElement('div');
        actions.className = 'row';
        const up = document.createElement('button');
        up.type = 'button';
        up.textContent = '上移';
        up.disabled = index === 0;
        up.onclick = () => moveWorkflowStep(index, -1);
        const down = document.createElement('button');
        down.type = 'button';
        down.textContent = '下移';
        down.disabled = index === workflowEditorSteps.length - 1;
        down.onclick = () => moveWorkflowStep(index, 1);
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'danger';
        remove.textContent = '删除步骤';
        remove.onclick = () => deleteWorkflowStep(index);
        actions.appendChild(up);
        actions.appendChild(down);
        actions.appendChild(remove);
        head.appendChild(title);
        head.appendChild(actions);

        const grid = document.createElement('div');
        grid.className = 'workflow-step-grid';

        const agentLabel = document.createElement('label');
        agentLabel.textContent = '数字员工';
        const agentSelect = document.createElement('select');
        if (!staffOptions.length) {
          const option = document.createElement('option');
          option.value = '';
          option.textContent = '暂无员工';
          agentSelect.appendChild(option);
        } else {
          for (const staff of staffOptions) {
            const option = document.createElement('option');
            option.value = staff;
            option.textContent = staff;
            agentSelect.appendChild(option);
          }
        }
        agentSelect.value = step.agent;
        agentSelect.onchange = () => { step.agent = agentSelect.value; };
        agentLabel.appendChild(agentSelect);

        const taskLabel = document.createElement('label');
        taskLabel.textContent = '任务说明';
        const taskInput = document.createElement('input');
        taskInput.value = step.task;
        taskInput.placeholder = '这一位员工要完成什么';
        taskInput.oninput = () => { step.task = taskInput.value; };
        taskLabel.appendChild(taskInput);

        const outputLabel = document.createElement('label');
        outputLabel.textContent = '输出物';
        const outputInput = document.createElement('input');
        outputInput.value = step.output;
        outputInput.placeholder = '例如 需求拆解.md / 分镜脚本.md';
        outputInput.oninput = () => { step.output = outputInput.value; };
        outputLabel.appendChild(outputInput);

        grid.appendChild(agentLabel);
        grid.appendChild(taskLabel);
        grid.appendChild(outputLabel);
        item.appendChild(head);
        item.appendChild(grid);
        els.workflowSteps.appendChild(item);
      });
    }

    function workflowPayloadFromEditor() {
      const file = els.workflowFile.value.trim();
      const name = els.workflowName.value.trim();
      if (!file) throw new Error('工作流文件名不能为空');
      if (!name) throw new Error('工作流名称不能为空');
      if (!workflowEditorSteps.length) throw new Error('工作流至少需要 1 个步骤');
      const steps = workflowEditorSteps.map((step, index) => {
        const agent = String(step.agent || '').trim();
        const task = String(step.task || '').trim();
        const output = String(step.output || '').trim();
        if (!agent) throw new Error(`第 ${index + 1} 步未选择数字员工`);
        if (!task) throw new Error(`第 ${index + 1} 步任务说明不能为空`);
        if (!output) throw new Error(`第 ${index + 1} 步输出物不能为空`);
        return { step: index + 1, agent, task, output };
      });
      return {
        file,
        workflow: {
          ...workflowEditorBase,
          name,
          description: els.workflowDescription.value.trim(),
          steps,
        },
      };
    }

    async function saveWorkflow() {
      try {
        const payload = workflowPayloadFromEditor();
        const result = await api('/api/save-workflow', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        selectedWorkflow = result.name;
        setWorkflowEditorStatus(`已保存：${result.file}`);
        await loadConfig();
        await selectWorkflow(result.name);
      } catch (err) {
        setWorkflowEditorStatus(err.message, true);
      }
    }

    async function deleteWorkflow() {
      if (!selectedWorkflow) return;
      if (!confirm(`确定删除这个工作流？\n\n${selectedWorkflow}\n\n这会删除 my_workflows 下对应 JSON 文件。`)) return;
      try {
        await api('/api/delete-workflow', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: selectedWorkflow }),
        });
        selectedWorkflow = null;
        workflowEditorSteps = [];
        workflowEditorBase = {};
        els.workflowFile.value = '';
        els.workflowName.value = '';
        els.workflowDescription.value = '';
        els.deleteWorkflowBtn.disabled = true;
        renderWorkflowSteps();
        setWorkflowEditorStatus('工作流已删除');
        await loadConfig();
        await loadWorkflowList();
      } catch (err) {
        setWorkflowEditorStatus(err.message, true);
      }
    }

    async function selectTask(name) {
      selectedTask = name;
      showView('output');
      selectedFile = null;
      await loadTasks();
      const data = await api(`/api/task?name=${encodeURIComponent(name)}`);
      selectedTaskSummary = data.summary || {};
      selectedTaskStatus = canonicalTaskStatus(data);
      selectedTaskAllowedActions = Array.isArray(selectedTaskStatus?.allowed_actions)
        ? selectedTaskStatus.allowed_actions
        : Array.isArray(data.allowed_actions) ? data.allowed_actions : [];
      els.viewerTitle.textContent = data.summary.task_title || data.summary.workflow || name;
      els.viewerMeta.textContent = name;
      currentTaskFiles = data.files || [];
      renderFiles(currentTaskFiles);
      renderOutputOverview(data);
      syncOutputButtons();
      const first = preferredInitialTaskFile(data);
      if (first) await openFile(first);
    }

    async function refreshSelectedTaskDetail(options = {}) {
      if (!selectedTask) return;
      const data = await api(`/api/task?name=${encodeURIComponent(selectedTask)}`);
      selectedTaskSummary = data.summary || {};
      selectedTaskStatus = canonicalTaskStatus(data);
      selectedTaskAllowedActions = Array.isArray(selectedTaskStatus?.allowed_actions)
        ? selectedTaskStatus.allowed_actions
        : Array.isArray(data.allowed_actions) ? data.allowed_actions : [];
      els.viewerTitle.textContent = data.summary.task_title || data.summary.workflow || selectedTask;
      els.viewerMeta.textContent = selectedTask;
      currentTaskFiles = data.files || [];
      renderFiles(currentTaskFiles);
      renderOutputOverview(data);
      syncOutputButtons();
      if (options.openMissingFile && selectedFile && !currentTaskFiles.includes(selectedFile)) {
        const first = preferredInitialTaskFile(data);
        if (first) await openFile(first);
      }
    }

    async function refreshActiveRunTaskDetail(job) {
      if (!job?.task_name || selectedTask !== job.task_name) return;
      const now = Date.now();
      if (now - lastTaskDetailRefreshAt < 2000) return;
      lastTaskDetailRefreshAt = now;
      try {
        await refreshSelectedTaskDetail({ openMissingFile: false });
      } catch (err) {
        // The task directory may not exist during the first seconds after a run starts.
      }
    }

    function prepareOutputForPendingRun(title) {
      selectedTask = null;
      selectedFile = null;
      selectedTaskSummary = {};
      selectedTaskStatus = null;
      selectedTaskAllowedActions = [];
      currentTaskFiles = [];
      els.viewerTitle.textContent = title || '正在创建任务';
      els.viewerMeta.textContent = '任务启动后会自动在这里显示进度、步骤输出和最终产物。';
      els.fileTabs.innerHTML = '';
      els.fileContent.value = '任务正在启动，后续步骤输出会自动显示在这里。';
      renderOutputOverview(null);
      syncOutputButtons();
    }

    function stepOutputFileForStep(stepNo, files = currentTaskFiles) {
      const safeStep = Number(stepNo || 0);
      if (!safeStep) return '';
      const prefix = `step_${String(safeStep).padStart(2, '0')}_`;
      return (files || []).find(file => file.startsWith(prefix) && file.endsWith('/output.md')) || '';
    }

    function awaitingConfirmationStep(summary = selectedTaskSummary) {
      if (!summary || !summary.awaiting_confirmation) return 0;
      return Number(summary.awaiting_confirmation_step || summary.blocked_step || summary.resume_step || 0);
    }

    function activeComfyDebugGate(status = selectedTaskStatus) {
      const debugStatus = status?.comfy_debug && typeof status.comfy_debug === 'object' ? status.comfy_debug : {};
      return Boolean(debugStatus.enabled && !debugStatus.complete && Number(debugStatus.total || 0) > 0);
    }

    function selectedTaskState() {
      return String(
        selectedTaskSummary?.status
        || selectedTaskStatus?.state
        || selectedTaskSummary?.state
        || ''
      ).toLowerCase();
    }

    function selectedTaskIsStopped() {
      return ['cancelled', 'canceled', 'completed'].includes(selectedTaskState());
    }

    function preferredInitialTaskFile(data) {
      const files = data?.files || [];
      const summary = data?.summary || {};
      const confirmStep = awaitingConfirmationStep(summary);
      if (confirmStep) {
        const stepFile = stepOutputFileForStep(confirmStep, files);
        if (stepFile) return stepFile;
      }
      const visibleFiles = visibleTaskFiles(files);
      return files.find(file => file.endsWith('final_output.md')) || visibleFiles[0] || files[0] || '';
    }

    function preferredStepOutputFromJob(job) {
      if (job?.rerun_result?.file) return job.rerun_result.file;
      const stepNo = Number(job?.awaiting_confirmation_step || job?.current_step || job?.completed_steps || job?.step_count || 0);
      return stepOutputFileForStep(stepNo);
    }

    function preferredCompletedTaskFile(files = currentTaskFiles) {
      return (files || []).find(file => file.endsWith('final_output.md'))
        || (files || []).find(file => file.endsWith('auto_production.md'))
        || visibleTaskFiles(files || [])[0]
        || (files || [])[0]
        || '';
    }

    async function selectTaskAndOpenJobOutput(job) {
      if (!job?.task_name) return;
      await selectTask(job.task_name);
      const preferred = job.status === 'completed' && !job.rerun_result
        ? preferredCompletedTaskFile()
        : preferredStepOutputFromJob(job);
      if (preferred) await openFile(preferred);
    }

    async function selectActiveRunTask(job) {
      if (!job?.task_name || activeRunTaskName === job.task_name) return;
      activeRunTaskName = job.task_name;
      await loadTasks();
      if (selectedTask !== job.task_name) {
        await selectTask(job.task_name);
      }
    }

    function renderStepConfirmBar() {
      const stepNo = awaitingConfirmationStep();
      const stepFile = stepOutputFileForStep(stepNo);
      const shouldShow = Boolean(selectedTask && stepNo);
      els.stepConfirmBar.hidden = !shouldShow;
      if (!shouldShow) {
        els.confirmStepContinueBtn.disabled = true;
        els.confirmStepRerunBtn.disabled = true;
        return;
      }
      els.stepConfirmTitle.textContent = `${stepFile ? stepFileLabel(stepFile) : `第 ${stepNo} 步`} 已完成，等待确认`;
      const comfyGateActive = activeComfyDebugGate();
      els.stepConfirmHint.textContent = comfyGateActive
        ? '当前停在 ComfyUI 调试门禁。请先在下方“ComfyUI 调试队列”按顺序运行并确认所有组，完成后再继续主流程。'
        : stepFile && selectedFile === stepFile
          ? '请检查下方输出，确认无误后继续下一步。'
          : stepFile
            ? '当前任务正在等待确认；可先打开对应步骤输出检查，也可以直接确认继续。'
            : '当前任务正在等待确认，但暂未定位到步骤输出文件；可刷新任务输出或直接确认继续。';
      els.confirmStepContinueBtn.textContent = els.workflowAdvanceMode.value === 'auto'
        ? '确认并自动跑完后续步骤'
        : '确认并继续下一步';
      els.confirmStepContinueBtn.disabled = Boolean(currentRunId) || comfyGateActive;
      els.confirmStepRerunBtn.disabled = Boolean(currentRunId);
    }

    function canonicalTaskStatus(data) {
      const status = data?.task_status && typeof data.task_status === 'object' ? data.task_status : {};
      return {
        schema_version: status.schema_version || 0,
        state: status.state || data?.task_state || '',
        workflow: status.workflow || {},
        steps: Array.isArray(status.steps) ? status.steps : [],
        production: status.production || { jobs: data?.production_jobs || [] },
        comfy_debug: status.comfy_debug || data?.comfy_debug || {},
        assets: status.assets || data?.assets || {},
        allowed_actions: Array.isArray(status.allowed_actions) ? status.allowed_actions : (data?.allowed_actions || []),
        diagnostics: Array.isArray(status.diagnostics) ? status.diagnostics : [],
      };
    }

    function renderOutputOverview(data) {
      els.outputSummaryGrid.hidden = true;
      els.outputSummaryGrid.innerHTML = '';
      if (!data) {
        renderProductionJobs([]);
        renderTaskComfyDebugPanel({});
        els.stepOutputMeta.textContent = '0 个步骤';
        els.stepOutputList.innerHTML = '<div class="muted small">选择任务后显示每个员工的输出。</div>';
        els.assetOutputMeta.textContent = '未生成';
        els.assetOutputList.classList.remove('asset-gallery');
        assetPreviewItems = [];
        assetPreviewTaskName = "";
        closeAssetLightbox();
        els.assetOutputList.innerHTML = '<div class="muted small">运行后只显示图片和视频素材。</div>';
        els.packageOutputMeta.textContent = '未生成';
        els.packageOutputList.innerHTML = '<div class="muted small">点击“导出产品包”后显示可交付文件。</div>';
        renderStepConfirmBar();
        clearVideoPreview();
        return;
      }

      const status = canonicalTaskStatus(data);
      const files = data.files || [];
      const summary = data.summary || {};
      const statusSteps = Array.isArray(status.steps) ? status.steps : [];
      const stepFiles = statusSteps
        .filter(step => step && step.has_output && step.output_file)
        .map(step => step.output_file);
      const fallbackStepFiles = files.filter(file => /^step_\d+_.*\/output\.md$/.test(file));
      const visibleStepFiles = stepFiles.length ? stepFiles : fallbackStepFiles;
      const packageFiles = files.filter(file => file.startsWith('export_package/') && !file.endsWith('/'));
      const assetItems = structuredAssetItems({ ...data, assets: status.assets });
      const packageReady = packageFiles.length ? `${packageFiles.length} 个文件` : '未生成';
      const videoFile = preferredVideoFile(files);
      renderProductionJobs(status.production?.jobs || data.production_jobs || [], status.diagnostics || []);
      renderTaskComfyDebugPanel(status.comfy_debug || {});
      renderVideoPreview(data.name, files);

      els.stepOutputMeta.textContent = `${visibleStepFiles.length} 个步骤`;
      els.stepOutputList.innerHTML = '';
      if (!visibleStepFiles.length) {
        els.stepOutputList.innerHTML = '<div class="muted small">暂无步骤输出。先运行工作流，或检查 task_output 目录。</div>';
      } else {
        const confirmStep = awaitingConfirmationStep(summary);
        for (const file of visibleStepFiles) {
          const stepNo = stepNumberFromFile(file);
          const statusStep = statusSteps.find(step => Number(step.step) === stepNo);
          const subtitle = statusStep?.needs_confirmation || (confirmStep && stepNo === confirmStep)
            ? '当前需要确认'
            : (productionStatusLabel(statusStep?.status || '') || '查看本步骤结果');
          els.stepOutputList.appendChild(outputFileButton(file, stepFileLabel(file), subtitle));
        }
      }
      renderStepConfirmBar();

      els.assetOutputMeta.textContent = assetItems.length ? `${assetItems.length} 个图片/视频` : '未生成';
      els.assetOutputList.innerHTML = '';
      if (!assetItems.length) {
        assetPreviewItems = [];
        assetPreviewTaskName = "";
        els.assetOutputList.classList.remove('asset-gallery');
        closeAssetLightbox();
        els.assetOutputList.innerHTML = '<div class="muted small">还没有可显示的图片/视频素材。若使用 prompt_only 模式，通常只会生成提示词和生产清单。</div>';
      } else {
        renderAssetGallery(data.name, assetItems);
      }

      els.packageOutputMeta.textContent = packageReady;
      els.packageOutputList.innerHTML = '';
      if (!packageFiles.length) {
        els.packageOutputList.innerHTML = '<div class="muted small">还没有产品包。点击右上角“导出产品包”生成可交付文件。</div>';
      } else {
        const priority = ['long_video_final.mp4', 'final_video.mp4', 'README.md', 'final_output.md', '视频制作包.md', '语音字幕制作包.md', 'ComfyUI生图参数包.json', 'ComfyUI生视频参数包.json', '剪辑成片执行方案.md', 'manifest.json'];
        packageFiles.sort((a, b) => {
          const an = a.split('/').pop();
          const bn = b.split('/').pop();
          const ai = priority.indexOf(an);
          const bi = priority.indexOf(bn);
          if (ai !== -1 || bi !== -1) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
          return a.localeCompare(b);
        });
        for (const file of packageFiles) {
          els.packageOutputList.appendChild(outputFileButton(file, file.replace('export_package/', ''), file));
        }
      }
    }

    function renderProductionJobs(jobs, diagnostics = []) {
      const list = Array.isArray(jobs) ? jobs.filter(job => job && job.id) : [];
      const diagnosticList = Array.isArray(diagnostics) ? diagnostics.filter(item => item && item.message) : [];
      els.outputSummaryGrid.innerHTML = '';
      els.outputSummaryGrid.hidden = !list.length && !diagnosticList.length;
      if (!list.length && !diagnosticList.length) return;
      for (const item of diagnosticList) {
        const card = document.createElement('div');
        card.className = 'output-card';
        const label = document.createElement('div');
        label.className = 'label';
        label.textContent = item.level === 'error' ? '诊断：错误' : item.level === 'warn' ? '诊断：提醒' : '诊断';
        const value = document.createElement('div');
        value.className = 'value';
        value.textContent = item.code || item.level || 'diagnostic';
        const detail = document.createElement('div');
        detail.className = 'muted small';
        detail.textContent = item.message || '';
        card.appendChild(label);
        card.appendChild(value);
        card.appendChild(detail);
        els.outputSummaryGrid.appendChild(card);
      }
      for (const job of list) {
        const card = document.createElement('div');
        card.className = 'output-card';
        const label = document.createElement('div');
        label.className = 'label';
        label.textContent = job.label || job.id;
        const value = document.createElement('div');
        value.className = 'value';
        value.textContent = productionStatusLabel(job.status);
        const detail = document.createElement('div');
        detail.className = 'muted small';
        const outputCount = Array.isArray(job.outputs) ? job.outputs.filter(Boolean).length : 0;
        const dependencyText = Array.isArray(job.depends_on) && job.depends_on.length ? `依赖：${job.depends_on.join(', ')}` : '';
        const attemptText = Number(job.attempts || 0) > 1 ? `尝试 ${job.attempts} 次` : '';
        const cacheText = job.cache_hit ? '缓存命中' : '';
        detail.textContent = [outputCount ? `${outputCount} 个输出` : compactPath(job.detail || ''), dependencyText, attemptText, cacheText].filter(Boolean).join(' · ');
        card.appendChild(label);
        card.appendChild(value);
        if (detail.textContent) card.appendChild(detail);
        const retryAction = productionRetryAction(job.id, job.status);
        if (retryAction) {
          const action = document.createElement('button');
          action.type = 'button';
          action.className = 'secondary small';
          action.textContent = retryAction;
          action.disabled = !selectedTask || workflowInteractionLocked;
          action.onclick = () => retryProductionJob(job.id);
          card.appendChild(action);
        }
        els.outputSummaryGrid.appendChild(card);
      }
    }

    function renderTaskComfyDebugPanel(debugStatus) {
      const status = debugStatus && typeof debugStatus === 'object' ? debugStatus : {};
      const items = Array.isArray(status.items) ? status.items : [];
      const enabled = Boolean(status.enabled || items.length);
      const taskStopped = selectedTaskIsStopped();
      els.taskComfyDebugPanel.hidden = !enabled;
      els.taskComfyDebugList.innerHTML = '';
      els.taskComfyDebugList.classList.toggle('task-comfy-debug-list', enabled);
      if (!enabled) {
        els.taskComfyDebugMeta.textContent = '未启用';
        return;
      }
      const approved = Number(status.approved || 0);
      const total = Number(status.total || items.length || 0);
      const stageLabel = status.stage === 'image' ? '生图阶段' : status.stage === 'video' ? '视频阶段' : '完整队列';
      els.taskComfyDebugMeta.textContent = total ? `${stageLabel} · ${approved}/${total} 已确认` : '等待生成 ComfyUI 参数包';
      if (!items.length) {
        els.taskComfyDebugList.innerHTML = '<div class="muted small">当前任务还没有可调试的 ComfyUI 队列。先运行到 06/07 视觉物料步骤。</div>';
        return;
      }
      const currentId = status.current_item_id || '';
      for (const item of items) {
        const row = document.createElement('div');
        row.className = 'task-comfy-debug-item';
        if (item.id === currentId) row.classList.add('active');
        if (item.status === 'approved') row.classList.add('is-approved');
        const left = document.createElement('div');
        left.className = 'task-comfy-debug-main';
        const title = document.createElement('strong');
        title.className = 'task-comfy-debug-title';
        title.textContent = `${String(item.order || item.index || '').padStart(2, '0')} · ${item.workflow_name || item.workflow_id || 'ComfyUI'}`;
        const detail = document.createElement('div');
        detail.className = 'muted small';
        const itemStage = item.stage === 'image' ? '图片' : item.stage === 'video' ? '视频' : item.source || '';
        const itemMode = item.workflow_mode || item.asset_tag || 'default';
        const fileCount = Number(item.file_count || (Array.isArray(item.files) ? item.files.length : 0));
        const groupProgress = item.group ? ` · ${Number(item.completed_count || 0)}/${Number(item.child_count || 0)} 项` : '';
        const itemError = item.error ? ` · ${item.error}` : '';
        detail.textContent = `${itemStage} · ${itemMode} · ${productionStatusLabel(item.status || 'pending')}${groupProgress}${fileCount ? ` · ${fileCount} 个素材` : ''}${itemError}`;
        left.appendChild(title);
        left.appendChild(detail);
        if (item.group) {
          const totalChildren = Number(item.child_count || 0);
          const completedChildren = Number(item.completed_count || 0);
          const percent = totalChildren ? Math.max(0, Math.min(100, Math.round((completedChildren / totalChildren) * 100))) : 0;
          const progress = document.createElement('div');
          progress.className = 'task-comfy-debug-progress';
          progress.setAttribute('aria-label', `调试进度 ${completedChildren}/${totalChildren}`);
          const bar = document.createElement('span');
          bar.style.setProperty('--task-comfy-debug-progress', `${percent}%`);
          progress.appendChild(bar);
          left.appendChild(progress);
        }
        const actions = document.createElement('div');
        actions.className = 'task-comfy-debug-actions';
        const runBtn = document.createElement('button');
        runBtn.type = 'button';
        runBtn.className = 'secondary small';
        const itemStatus = String(item.status || '').toLowerCase();
        const canRunOutOfTurn = ['failed', 'completed', 'success', 'approved'].includes(itemStatus);
        const debugButtonLocked = Boolean(currentRunId && ['queued', 'running'].includes(currentRunStatus));
        runBtn.textContent = itemStatus === 'running' ? '运行中' : (canRunOutOfTurn ? '重新运行本组' : '运行当前组');
        runBtn.disabled = taskStopped || debugButtonLocked || itemStatus === 'running' || (item.id !== currentId && !canRunOutOfTurn);
        runBtn.onclick = () => runTaskComfyDebugItem(item.id);
        actions.appendChild(runBtn);
        const approveBtn = document.createElement('button');
        approveBtn.type = 'button';
        approveBtn.className = 'primary small';
        approveBtn.textContent = itemStatus === 'approved' ? '已满意' : '满意，下一组';
        approveBtn.disabled = taskStopped || debugButtonLocked || item.id !== currentId || !['completed', 'success'].includes(itemStatus);
        approveBtn.onclick = () => approveTaskComfyDebugItem(item.id);
        actions.appendChild(approveBtn);
        row.appendChild(left);
        row.appendChild(actions);
        els.taskComfyDebugList.appendChild(row);
      }
    }

    async function runTaskComfyDebugItem(itemId) {
      if (!selectedTask || !itemId) return;
      if (selectedTaskIsStopped()) {
        setStatus('任务已终止或完成，请先点击继续任务后再操作 ComfyUI 调试队列。', true);
        return;
      }
      const { productionConfig } = collectProductionConfig();
      try {
        const job = await api('/api/task-comfy-debug-run', {
          method: 'POST',
          body: JSON.stringify({
            task: selectedTask,
            item_id: itemId,
            api_key: els.comfyApiKey.value.trim(),
            base_url: els.comfyBaseUrl.value.trim(),
            workflow_library: getComfyWorkflowLibraryPayload(),
            production_config: productionConfig,
          }),
        });
        setStatus(`ComfyUI 调试已启动：${job.run_id || ''}`);
        if (job.run_id) pollComfyDebugRunForTask(job.run_id);
        await refreshSelectedTaskDetail({ openMissingFile: false });
      } catch (err) {
        setStatus(err.message, true);
      }
    }

    async function pollComfyDebugRunForTask(runId) {
      if (!runId) return;
      try {
        const job = await api(`/api/run-status?id=${encodeURIComponent(runId)}`);
        if (selectedTask) await refreshSelectedTaskDetail({ openMissingFile: false });
        if (['queued', 'running'].includes(job.status)) {
          setTimeout(() => pollComfyDebugRunForTask(runId), 2000);
        }
      } catch (err) {
        setStatus(err.message, true);
      }
    }

    async function approveTaskComfyDebugItem(itemId) {
      if (!selectedTask || !itemId) return;
      if (selectedTaskIsStopped()) {
        setStatus('任务已终止或完成，请先点击继续任务后再确认 ComfyUI 调试队列。', true);
        return;
      }
      try {
        await api('/api/task-comfy-debug-confirm', {
          method: 'POST',
          body: JSON.stringify({ task: selectedTask, item_id: itemId }),
        });
        await refreshSelectedTaskDetail({ openMissingFile: false });
        setStatus('已确认当前 ComfyUI 调试项，可以继续下一项。');
      } catch (err) {
        setStatus(err.message, true);
      }
    }

    function productionRetryAction(jobId, status = '') {
      if (jobId === 'material') return '重试素材';
      if (jobId === 'tts') return '重试配音';
      if (jobId === 'ffmpeg') return '重新合成';
      return ['failed', 'blocked', 'quality_failed'].includes(String(status || '').toLowerCase()) ? '重试节点' : '';
    }

    function productionStatusLabel(status) {
      const text = String(status || '').toLowerCase();
      if (!text || text === 'not_configured') return '未启用';
      if (text === 'pending') return '等待中';
      if (text === 'running') return '运行中';
      if (text === 'completed') return '已完成';
      if (text === 'awaiting_confirmation') return '等待确认';
      if (text === 'awaiting_comfyui_debug') return '等待调试';
      if (text === 'approved') return '已满意';
      if (text === 'blocked') return '已阻塞';
      if (text === 'success' || text === 'final_video_generated') return '成功';
      if (text === 'skipped') return '已跳过';
      if (text === 'partial_success') return '部分成功';
      if (text.includes('failed') || text === 'failed') return '失败';
      return status;
    }

    function compactPath(value) {
      const text = String(value || '');
      if (!text) return '';
      return text.split(/[\\/]/).slice(-2).join('/');
    }

    function structuredAssetItems(data) {
      const assets = data?.assets || {};
      const images = Array.isArray(assets.images) ? assets.images.map(item => ({ ...item, kind: 'image' })) : [];
      const videos = Array.isArray(assets.videos) ? assets.videos.map(item => ({ ...item, kind: 'video' })) : [];
      const structured = images.concat(videos).filter(item => item && item.file);
      if (structured.length) return structured;
      return generatedAssetFiles(data?.files || []).map(file => ({
        file,
        label: assetFileLabel(file),
        name: String(file).split('/').pop(),
        kind: isImageFile(file) ? 'image' : 'video',
      }));
    }

    function generatedAssetFiles(files) {
      const list = Array.isArray(files) ? files : [];
      const assetPrefixes = [
        'generated_images/',
        'video_clips/',
        'comfyui/',
      ];
      const assetNames = new Set([
        'long_video_final.mp4',
        'final_video.mp4',
      ]);
      return list
        .filter(file => {
          const name = String(file || '');
          if (!name || name.startsWith('export_package/') || /^step_\d+_/.test(name)) return false;
          if (!isImageFile(name) && !isVideoFile(name)) return false;
          return assetNames.has(name)
            || assetPrefixes.some(prefix => name.startsWith(prefix))
            || name.includes('/material_')
            || /comfyui_result_\d+/i.test(name);
        })
        .sort((a, b) => assetSortKey(a).localeCompare(assetSortKey(b), undefined, { numeric: true }));
    }

    function assetSortKey(file) {
      const name = String(file || '');
      const order = [
        ['long_video_final.mp4', '00_'],
        ['final_video.mp4', '00_'],
        ['generated_images/', '10_'],
        ['video_clips/', '20_'],
        ['comfyui/', '60_'],
      ];
      const found = order.find(([prefix]) => name === prefix || name.startsWith(prefix));
      return (found ? found[1] : '99_') + name;
    }

    function renderAssetGallery(taskName, assetItems) {
      const galleryItems = assetItems.map(item => {
        const file = typeof item === 'string' ? item : item.file;
        return {
          ...item,
          file,
          label: typeof item === 'string' ? assetFileLabel(file) : (item.label || assetFileLabel(file)),
          name: typeof item === 'string' ? String(file).split('/').pop() : (item.name || String(file).split('/').pop()),
          kind: isImageFile(file) ? 'image' : 'video',
        };
      }).filter(item => item.file && (isImageFile(item.file) || isVideoFile(item.file)));
      els.assetOutputList.classList.add('asset-gallery');
      els.assetOutputList.innerHTML = '';
      galleryItems.forEach((item, index) => {
        els.assetOutputList.appendChild(assetGalleryCard(taskName, item, index, galleryItems));
      });
    }

    function assetGalleryCard(taskName, item, index, previewItems = null) {
      const card = document.createElement('div');
      card.tabIndex = 0;
      card.role = 'button';
      card.className = 'asset-card';
      card.dataset.file = item.file;
      const favorited = item.library || isAssetFavorited(taskName, item);
      if (favorited) card.classList.add('is-favorited');
      const media = document.createElement('div');
      media.className = 'asset-card-media';
      const previewUrl = assetItemUrl(taskName, item);
      if (isImageFile(item.file)) {
        const img = document.createElement('img');
        img.loading = 'lazy';
        img.alt = item.label || assetFileLabel(item.file);
        img.src = previewUrl;
        media.appendChild(img);
      } else {
        const video = document.createElement('video');
        video.muted = true;
        video.playsInline = true;
        video.preload = 'metadata';
        video.src = previewUrl;
        media.appendChild(video);
      }
      const kind = document.createElement('span');
      kind.className = 'asset-card-kind';
      kind.textContent = isImageFile(item.file) ? '图片' : '视频';
      const title = document.createElement('span');
      title.className = 'asset-card-title';
      title.textContent = item.label || assetFileLabel(item.file);
      const subtitle = document.createElement('span');
      subtitle.className = 'asset-card-subtitle';
      subtitle.textContent = item.file;
      const tags = normalizeAssetTags(item.tags);
      const tagRow = document.createElement('span');
      tagRow.className = 'asset-tag-row';
      tags.slice(0, 4).forEach(tag => {
        const chip = document.createElement('span');
        chip.className = 'asset-chip';
        chip.textContent = assetTagLabel(tag);
        tagRow.appendChild(chip);
      });
      card.appendChild(media);
      card.appendChild(kind);
      if (favorited) {
        const badge = document.createElement('span');
        badge.className = 'asset-card-badge';
        badge.textContent = '已收藏';
        card.appendChild(badge);
      }
      card.appendChild(title);
      card.appendChild(subtitle);
      if (tags.length) card.appendChild(tagRow);
      card.onclick = () => openAssetLightboxFromItems(taskName, previewItems || [item], previewItems ? index : 0);
      card.onkeydown = event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          openAssetLightboxFromItems(taskName, previewItems || [item], previewItems ? index : 0);
        }
      };
      return card;
    }

    function assetSourceKey(taskName, item) {
      const sourceTask = item?.source_task || taskName || '';
      const sourceFile = item?.source_file || item?.file || '';
      return `${sourceTask}::${sourceFile}`;
    }

    function isAssetFavorited(taskName, item) {
      if (!Array.isArray(assetLibraryItems) || !item?.file) return false;
      const key = assetSourceKey(taskName, item);
      return assetLibraryItems.some(existing => assetSourceKey('', existing) === key);
    }

    function findFavoritedAsset(taskName, item) {
      if (!Array.isArray(assetLibraryItems) || !item?.file) return null;
      if (item.library && item.id) return item;
      const key = assetSourceKey(taskName, item);
      return assetLibraryItems.find(existing => assetSourceKey('', existing) === key) || null;
    }

    function normalizeAssetTags(tags) {
      return (Array.isArray(tags) ? tags : String(tags || '').split(','))
        .map(tag => String(tag || '').trim())
        .filter(Boolean)
        .filter((tag, index, arr) => arr.indexOf(tag) === index);
    }

    function assetTagLabel(tag) {
      const found = ASSET_CATEGORY_TAGS.find(item => item.value === tag);
      if (found) return found.label;
      if (tag === 'image') return '图片';
      if (tag === 'video') return '视频';
      return tag;
    }

    function assetPrimaryCategory(item) {
      const tags = normalizeAssetTags(item?.tags);
      return ASSET_CATEGORY_TAGS.find(option => tags.includes(option.value))?.value || '';
    }

    function assetMatchesTag(item, tag) {
      if (!tag) return true;
      return normalizeAssetTags(item?.tags).includes(tag);
    }

    function renderAssetTagFilters() {
      const selects = [els.assetLibraryTagFilter, els.comfyDebugAssetTagFilter].filter(Boolean);
      selects.forEach(select => {
        const current = select.value;
        select.innerHTML = '';
        const all = document.createElement('option');
        all.value = '';
        all.textContent = select === els.assetLibraryTagFilter ? '全部标签' : '全部素材';
        select.appendChild(all);
        const baseItems = select === els.assetLibraryTagFilter
          ? assetLibraryItems.filter(item => assetMatchesLibrarySection(item))
          : assetLibraryItems;
        ASSET_CATEGORY_TAGS.forEach(tag => {
          const count = baseItems.filter(item => assetMatchesTag(item, tag.value)).length;
          if (select === els.assetLibraryTagFilter && !count) return;
          const option = document.createElement('option');
          option.value = tag.value;
          option.textContent = count ? `${tag.label} (${count})` : tag.label;
          select.appendChild(option);
        });
        select.value = [...select.options].some(option => option.value === current) ? current : '';
      });
    }

    function assetLibrarySectionDefinition(section = assetLibrarySection) {
      return ASSET_LIBRARY_SECTIONS.find(item => item.value === section) || ASSET_LIBRARY_SECTIONS[0];
    }

    function assetLibrarySectionForItem(item) {
      const tags = normalizeAssetTags(item?.tags);
      if (tags.some(tag => assetLibrarySectionDefinition('character').tags.includes(tag))) return 'character';
      if (tags.some(tag => assetLibrarySectionDefinition('product').tags.includes(tag))) return 'product';
      if (tags.some(tag => assetLibrarySectionDefinition('reference').tags.includes(tag))) return 'reference';
      return 'material';
    }

    function assetMatchesLibrarySection(item, section = assetLibrarySection) {
      if (!section || section === 'all') return true;
      return assetLibrarySectionForItem(item) === section;
    }

    function assetLibraryCategoryOptions() {
      return [
        { value: 'scene', label: '素材 / 场景' },
        { value: 'broll', label: '素材 / B-roll' },
        { value: 'bgm', label: '素材 / BGM 配乐' },
        { value: 'cover', label: '素材 / 封面' },
        { value: 'person', label: '角色 / 人物' },
        { value: 'character_base', label: '角色 / 基础图' },
        { value: 'character_turnaround', label: '角色 / 多视图' },
        { value: 'product', label: '商品 / 产品' },
        { value: 'product_base', label: '商品 / 基础图' },
        { value: 'product_turnaround', label: '商品 / 多视图' },
        { value: 'style_reference', label: '参考 / 风格' },
        { value: 'style', label: '参考 / 风格旧标签' },
        { value: 'keyframe', label: '参考 / 关键帧' },
        { value: 'reference', label: '参考 / 通用参考' },
        { value: 'i2v_first_frame', label: '参考 / 首帧视频' },
        { value: 'i2v_first_last_frame', label: '参考 / 首尾帧视频' },
        { value: 'i2v_first_middle_last_frame', label: '参考 / 首中尾帧视频' },
        { value: 'live_to_anime', label: '参考 / 真人转动漫' },
        { value: 'motion_transfer', label: '参考 / 动作迁移' },
        { value: 'talking_image', label: '参考 / 图片说话' },
      ];
    }

    function defaultAssetCategoryForSection(section = assetLibrarySection) {
      if (section === 'character') return 'person';
      if (section === 'product') return 'product';
      if (section === 'reference') return 'reference';
      return 'scene';
    }

    function renderAssetLibraryCategorySelect(select, current = '') {
      if (!select) return;
      const value = current || defaultAssetCategoryForSection();
      select.innerHTML = '';
      assetLibraryCategoryOptions().forEach(item => {
        const option = document.createElement('option');
        option.value = item.value;
        option.textContent = item.label;
        select.appendChild(option);
      });
      if ([...select.options].some(option => option.value === value)) {
        select.value = value;
      }
    }

    function formatAssetLibraryTime(value) {
      const number = Number(value || 0);
      if (!Number.isFinite(number) || number <= 0) return '未知时间';
      try {
        return new Date(number * 1000).toLocaleString();
      } catch {
        return '未知时间';
      }
    }

    function assetLibraryKindLabel(item) {
      if (item?.kind === 'audio' || isAudioFile(item?.file || '')) return '音频';
      return (item?.kind === 'video' || isVideoFile(item?.file || '')) ? '视频' : '图片';
    }

    function assetLibrarySelectedItem() {
      return assetLibraryItems.find(item => String(item.id || '') === String(selectedAssetLibraryId || '')) || null;
    }

    function assetLibraryFilteredItems() {
      const selectedTag = els.assetLibraryTagFilter?.value || '';
      return assetLibraryItems.filter(item => assetMatchesLibrarySection(item) && assetMatchesTag(item, selectedTag));
    }

    function assetLibraryAddLabel() {
      return assetLibrarySectionDefinition().addLabel || '新增资产';
    }

    function setAssetLibraryDetailDirty(dirty) {
      assetLibraryDetailDirty = Boolean(dirty);
      if (els.assetLibraryDetailSaveBtn) {
        els.assetLibraryDetailSaveBtn.disabled = !assetLibraryDetailDirty || !selectedAssetLibraryId;
      }
      if (els.assetLibraryDetailMeta) {
        const item = assetLibrarySelectedItem();
        const base = item ? `${assetLibraryKindLabel(item)} · ${assetTagLabel(assetPrimaryCategory(item)) || '未分类'} · ${formatAssetLibraryTime(item.updated_at || item.created_at || item.mtime)}` : '';
        els.assetLibraryDetailMeta.textContent = assetLibraryDetailDirty ? `${base} · 有未保存修改` : base;
      }
    }

    function confirmDiscardAssetLibraryDetailChanges() {
      if (!assetLibraryDetailDirty) return true;
      return window.confirm('资产详情有未保存修改，确定放弃这些修改吗？');
    }

    function comfyImageTaskDefinition(value) {
      return COMFY_IMAGE_TASK_TYPES.find(item => item.value === value) || COMFY_IMAGE_TASK_TYPES[0];
    }

    function imageTaskDefinitionForWorkflow(workflow) {
      const savedConfig = workflow ? getComfyWorkflowLibraryItemById(workflow.id) : null;
      const selectedMode = els.comfyDebugWorkflowMode?.value || '';
      const mode = selectedMode
        || workflow?.default_image_task_type
        || savedConfig?.defaultImageTaskType
        || workflow?.default_task_type
        || 'character_generation';
      return comfyImageTaskDefinition(mode);
    }

    function normalizeComfyDebugWorkflowDefinition(workflow) {
      const item = { ...(workflow || {}) };
      if (item.id === '04_keyframe') {
        item.default_task_type = 'keyframe';
        item.default_control_mode = 'none';
        item.default_image_task_type = 'keyframe';
        item.asset_tag = 'keyframe';
        item.modes = [{
          value: 'keyframe',
          label: '关键帧',
          asset_tag: 'keyframe',
          task_type: 'keyframe',
          control_mode: 'none',
          requires_reference: false,
          required_inputs: [],
          outputs: ['output_final_image'],
        }];
      }
      return item;
    }

    function usesComfyDebug480pDefaults(workflow) {
      const match = String(workflow?.id || '').match(/^(\d{2})/);
      const index = match ? Number(match[1]) : 0;
      return index >= 1 && index <= 10;
    }

    function normalizedComfyDebug480pSize(width, height, workflow) {
      const rawWidth = String(width || '').trim();
      const rawHeight = String(height || '').trim();
      if (!usesComfyDebug480pDefaults(workflow)) {
        return { width: rawWidth, height: rawHeight };
      }
      const numericWidth = Number(rawWidth || 0);
      const numericHeight = Number(rawHeight || 0);
      const legacyPairs = new Set([
        '1920x1080', '1080x1920', '1280x720', '720x1280',
        '1024x576', '576x1024', '960x544', '544x960',
      ]);
      const isLegacy = legacyPairs.has(`${numericWidth}x${numericHeight}`);
      if (rawWidth && rawHeight && !isLegacy) {
        return { width: rawWidth, height: rawHeight };
      }
      const portrait = numericHeight > numericWidth;
      return portrait
        ? { width: '480', height: '848' }
        : {
            width: String(workflow?.default_width || 848),
            height: String(workflow?.default_height || 480),
          };
    }

    function comfyDebugModeDisablesReference(workflow = activeComfyDebugWorkflow()) {
      const mode = selectedWorkflowModeDefinition(workflow);
      if (!mode || Boolean(mode.requires_reference)) return false;
      return ['none', 'broll', 'transition'].includes(String(mode.control_mode || '').trim().toLowerCase());
    }

    function normalizeComfyDebugWorkflowSavedConfig(item, workflow) {
      if (!item || !workflow) return;
      const normalizedSize = normalizedComfyDebug480pSize(item.defaultWidth, item.defaultHeight, workflow);
      item.defaultWidth = normalizedSize.width;
      item.defaultHeight = normalizedSize.height;
      if (workflow.id === '01_base_asset_image') {
        item.defaultReference = '';
        item.defaultAssetReference = '';
        item.defaultReferenceHint = '';
      }
      if (workflow.id === '04_keyframe') {
        item.defaultWorkflowMode = 'keyframe';
        item.defaultImageTaskType = 'keyframe';
        item.defaultReference = '';
        item.defaultAssetReference = '';
        if (String(item.defaultReferenceHint || '').includes('需要参考')) {
          item.defaultReferenceHint = '';
        }
      } else if (!item.defaultWorkflowMode && Array.isArray(workflow.modes) && workflow.modes.length === 1) {
        item.defaultWorkflowMode = workflow.modes[0].value || '';
      }
      if (!item.defaultImageTaskType) {
        item.defaultImageTaskType = workflow.default_image_task_type || workflow.default_task_type || '';
      }
      if (!item.modeConfigs || typeof item.modeConfigs !== 'object') item.modeConfigs = {};
      workflowModesForWorkflow(workflow).forEach(mode => {
        const config = item.modeConfigs[mode.value] || normalizeComfyModeConfig({}, item);
        const size = normalizedComfyDebug480pSize(config.defaultWidth, config.defaultHeight, workflow);
        config.defaultWidth = size.width;
        config.defaultHeight = size.height;
        if (!mode.requires_reference) {
          config.defaultReference = '';
          config.defaultMiddleFrameReference = '';
          config.defaultLastFrameReference = '';
          config.defaultAssetReference = '';
        }
        item.modeConfigs[mode.value] = config;
      });
    }

    function normalizeComfyDebugWorkflowState(state, workflow) {
      const next = { ...(state || {}) };
      const normalizedSize = normalizedComfyDebug480pSize(next.width, next.height, workflow);
      next.width = normalizedSize.width;
      next.height = normalizedSize.height;
      if (workflow?.id === '01_base_asset_image') {
        next.reference = '';
        next.assetReference = '';
        next.referenceHint = '';
      }
      if (workflow?.id === '04_keyframe') {
        next.workflowMode = 'keyframe';
        next.reference = '';
        next.assetReference = '';
        if (String(next.referenceHint || '').includes('需要参考')) {
          next.referenceHint = '';
        }
      }
      return next;
    }

    function workflowModesForWorkflow(workflow) {
      return Array.isArray(workflow?.modes) && workflow.modes.length ? workflow.modes : [];
    }

    function selectedWorkflowModeDefinition(workflow = activeComfyDebugWorkflow()) {
      const modes = workflowModesForWorkflow(workflow);
      if (!modes.length) return null;
      const selected = els.comfyDebugWorkflowMode?.value || modes[0].value;
      return modes.find(item => item.value === selected) || modes[0];
    }

    function renderComfyWorkflowModeOptions(workflow = activeComfyDebugWorkflow()) {
      if (!els.comfyDebugWorkflowMode) return;
      const modes = workflowModesForWorkflow(workflow);
      els.comfyDebugWorkflowMode.innerHTML = '';
      if (!modes.length) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = '当前工作流无子类型';
        els.comfyDebugWorkflowMode.appendChild(option);
        els.comfyDebugWorkflowMode.disabled = true;
        return;
      }
      const current = els.comfyDebugWorkflowMode.value || modes[0].value;
      modes.forEach(mode => {
        const option = document.createElement('option');
        option.value = mode.value;
        option.textContent = mode.requires_reference ? mode.label + '（需参考）' : mode.label;
        els.comfyDebugWorkflowMode.appendChild(option);
      });
      setIfExists(els.comfyDebugWorkflowMode, current);
      els.comfyDebugWorkflowMode.disabled = modes.length <= 1;
    }

    function updateComfyImageTaskHint() {
      const selected = activeComfyDebugWorkflow();
      const isImageWorkflow = !selected || selected.type === 'image';
      const modeDef = selectedWorkflowModeDefinition(selected);
      const def = modeDef ? {
        label: modeDef.label,
        taskType: modeDef.task_type,
        controlMode: modeDef.control_mode,
        requiresReference: Boolean(modeDef.requires_reference),
      } : imageTaskDefinitionForWorkflow(selected);
      if (els.comfyDebugReferenceHint && isImageWorkflow) {
        const current = els.comfyDebugReferenceHint.textContent || '';
        const modeHint = def.label + '：task_type=' + def.taskType + '，control_mode=' + def.controlMode + (def.requiresReference ? '，需要参考图' : '，可不传参考图');
        if (!current.includes('task_type=')) {
          els.comfyDebugReferenceHint.textContent = current + '｜' + modeHint;
        }
      }
    }

    function updateComfyDebugMediaFields() {
      const selected = activeComfyDebugWorkflow();
      const isVideoWorkflow = selected?.type === 'video';
      if (els.comfyDebugDurationField) els.comfyDebugDurationField.style.display = isVideoWorkflow ? '' : 'none';
      if (els.comfyDebugFpsField) els.comfyDebugFpsField.style.display = isVideoWorkflow ? '' : 'none';
      if (!isVideoWorkflow) {
        if (els.comfyDebugDuration) els.comfyDebugDuration.value = '';
        if (els.comfyDebugFps) els.comfyDebugFps.value = '';
      }
      const mode = selectedWorkflowModeDefinition(selected);
      const requiredInputs = Array.isArray(mode?.required_inputs) ? mode.required_inputs : [];
      if (els.comfyDebugMaskImageField) els.comfyDebugMaskImageField.hidden = !requiredInputs.includes('input_mask_image');
      if (els.comfyDebugAudioFileField) els.comfyDebugAudioFileField.hidden = !requiredInputs.includes('input_audio_file');
      updateComfyDebugFrameCountHint();
      updateComfyDebugReferencePreviews();
    }

    function computedComfyDebugFrameCount() {
      const duration = Number(String(els.comfyDebugDuration?.value || '').trim());
      const fps = Number(String(els.comfyDebugFps?.value || '').trim());
      if (!Number.isFinite(duration) || !Number.isFinite(fps) || duration <= 0 || fps <= 0) return '';
      return String(Math.max(1, Math.round(duration * fps)));
    }

    function updateComfyDebugFrameCountHint() {
      if (!els.comfyDebugFrameCountHint) return;
      const selected = activeComfyDebugWorkflow();
      if (selected?.type !== 'video') {
        els.comfyDebugFrameCountHint.textContent = '';
        return;
      }
      const frameCount = computedComfyDebugFrameCount();
      const duration = String(els.comfyDebugDuration?.value || '').trim();
      const fps = String(els.comfyDebugFps?.value || '').trim();
      els.comfyDebugFrameCountHint.textContent = frameCount
        ? `将提交：${duration} 秒 · ${fps} fps · ${frameCount} 帧（nodeInfo 用 {{frame_count}} 才会生效）`
        : '请输入秒数和 FPS；帧数会自动按 秒数 × FPS 计算。';
    }

    async function updateAssetMetadata(assetId, tags, note, name = '') {
      const id = String(assetId || '').trim();
      if (!id) return;
      try {
        const result = await api('/api/update-asset-metadata', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id, tags: normalizeAssetTags(tags), note: String(note || '').trim(), name: String(name || '').trim() }),
        });
        const updated = result.asset || null;
        if (updated) {
          assetLibraryItems = assetLibraryItems.map(item => String(item.id || '') === String(updated.id || '') ? updated : item);
        }
        renderAssetTagFilters();
        renderAssetLibrary();
        renderComfyDebugAssetReferenceOptions();
        setStatus('素材信息已保存', false);
      } catch (err) {
        setStatus(err.message || '素材信息保存失败', true);
        throw err;
      }
    }

    async function favoriteAsset(taskName, item) {
      if (!taskName || !item?.file) return;
      try {
        const result = await api('/api/favorite-asset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task: taskName,
            file: item.file,
            label: item.label || assetFileLabel(item.file),
            tags: normalizeAssetTags([item.kind || (isImageFile(item.file) ? 'image' : 'video'), ...(item.tags || [])]),
          }),
        });
        setStatus(`已收藏到素材库：${result.asset?.name || item.file}`, false);
        await loadAssetLibrary();
        if (selectedTask === taskName) await refreshSelectedTaskDetail({ silent: true, preserveFile: true });
        renderAssetLightbox();
      } catch (err) {
        setStatus(err.message, true);
        renderAssetLightbox();
      }
    }

    async function unfavoriteAsset(taskName, item) {
      const existing = findFavoritedAsset(taskName, item);
      if (!existing) {
        await loadAssetLibrary();
        renderAssetLightbox();
        return;
      }
      try {
        await api('/api/unfavorite-asset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            id: existing.id || '',
            task: existing.source_task || taskName || '',
            file: existing.source_file || item.file || '',
          }),
        });
        setStatus(`已取消收藏：${existing.name || existing.file || item.file}`, false);
        await loadAssetLibrary();
        if (item.library) {
          assetPreviewItems.splice(assetPreviewIndex, 1);
          if (!assetPreviewItems.length) {
            closeAssetLightbox();
            renderAssetLibrary();
            return;
          }
          assetPreviewIndex = Math.min(assetPreviewIndex, assetPreviewItems.length - 1);
          renderAssetLibrary();
        }
        if (selectedTask === taskName) await refreshSelectedTaskDetail({ silent: true, preserveFile: true });
        renderAssetLightbox();
      } catch (err) {
        setStatus(err.message, true);
        renderAssetLightbox();
      }
    }

    async function loadAssetLibrary() {
      if (!els.assetLibraryGrid) return;
      try {
        const data = await api('/api/asset-library');
        assetLibraryItems = Array.isArray(data.assets) ? data.assets : [];
        renderAssetTagFilters();
        renderAssetLibrary();
        renderComfyDebugAssetReferenceOptions();
        if (els.assetLibraryStatus) {
          els.assetLibraryStatus.textContent = assetLibraryItems.length ? `${assetLibraryItems.length} 个可复用素材` : '素材库为空';
          els.assetLibraryStatus.classList.remove('error');
        }
      } catch (err) {
        if (els.assetLibraryStatus) {
          els.assetLibraryStatus.textContent = err.message;
          els.assetLibraryStatus.classList.add('error');
        }
      }
    }

    function setComfyDebugReference(value = '', hint = '') {
      if (els.comfyDebugReference) els.comfyDebugReference.value = value || '';
      if (els.comfyDebugReferenceHint) {
        els.comfyDebugReferenceHint.textContent = hint || '可直接输入路径、选择素材库资产，或上传本地参考图/视频。';
      }
      updateComfyImageTaskHint();
      updateComfyDebugReferencePreviews();
      updateComfyDebugUploadStates();
    }

    function setComfyDebugLastFrameReference(value = '', hint = '') {
      if (els.comfyDebugLastFrameReference) els.comfyDebugLastFrameReference.value = value || '';
      if (els.comfyDebugLastFrameReferenceHint) {
        els.comfyDebugLastFrameReferenceHint.textContent = hint || '首尾帧视频需要第二张尾帧图。';
      }
      updateComfyDebugReferencePreviews();
      updateComfyDebugUploadStates();
    }

    function setComfyDebugMiddleFrameReference(value = '', hint = '') {
      if (els.comfyDebugMiddleFrameReference) els.comfyDebugMiddleFrameReference.value = value || '';
      if (els.comfyDebugMiddleFrameReferenceHint) {
        els.comfyDebugMiddleFrameReferenceHint.textContent = hint || '首中尾帧视频需要第二张中间帧图。';
      }
      updateComfyDebugReferencePreviews();
      updateComfyDebugUploadStates();
    }

    function isFirstLastFrameMode() {
      const workflow = activeComfyDebugWorkflow();
      const mode = selectedWorkflowModeDefinition(workflow)?.value || els.comfyDebugWorkflowMode?.value || workflow?.id || '';
      return String(mode).includes('first_last') || String(mode).includes('first_middle_last') || String(workflow?.id || '').includes('first_last') || String(workflow?.id || '').includes('first_middle_last');
    }

    function comfyDebugNodeInfoText() {
      return String(els.comfyDebugNodeInfoList?.value || '').trim();
    }

    function comfyDebugReferenceSupport() {
      if (comfyDebugModeDisablesReference()) {
        return { hasReference: false, hasMiddleFrame: false, hasLastFrame: false };
      }
      const text = comfyDebugNodeInfoText();
      const hasReference = /\{\{\s*(reference_image|reference_image_[1-4]|has_reference_image|has_reference_image_[1-4])\s*\}\}/i.test(text);
      const hasMiddleFrame = /\{\{\s*(middle_frame_image|mid_frame_image|has_middle_frame_image)\s*\}\}/i.test(text);
      const hasLastFrame = /\{\{\s*(last_frame_image|end_frame_image|has_last_frame_image)\s*\}\}/i.test(text);
      return { hasReference, hasMiddleFrame, hasLastFrame };
    }

    function referencePreviewUrl(value) {
      const raw = String(value || '').trim();
      if (!raw) return '';
      const normalized = raw.replace(/\\/g, '/');
      const libraryPrefix = 'my_workspace/my_asset_library/';
      const libraryFile = normalized.startsWith(libraryPrefix) ? normalized.slice(libraryPrefix.length) : '';
      if (libraryFile) {
        const asset = assetLibraryItems.find(item => String(item.file || '').replace(/\\/g, '/') === libraryFile);
        if (asset?.id) return assetLibraryMediaUrl(asset.id);
      }
      const workspaceReferencePrefix = 'my_workspace/my_reference_images/';
      if (normalized.startsWith('my_reference_images/') || normalized.startsWith(workspaceReferencePrefix)) {
        const referenceFile = normalized.startsWith(workspaceReferencePrefix)
          ? normalized.slice('my_workspace/'.length)
          : normalized;
        return `/api/reference-media?file=${encodeURIComponent(referenceFile)}`;
      }
      return '';
    }

    function compactReferenceName(value) {
      const text = String(value || '').replace(/\\/g, '/').trim();
      return text.split('/').filter(Boolean).pop() || text || '已选择文件';
    }

    function setComfyUploadState(fileLabel, stateBox, stateName, value, prefix = '已选择') {
      const hasValue = Boolean(String(value || '').trim());
      if (fileLabel) fileLabel.hidden = hasValue;
      if (stateBox) stateBox.hidden = !hasValue;
      if (stateName) stateName.textContent = hasValue ? `${prefix}：${compactReferenceName(value)}` : '';
    }

    function updateComfyDebugUploadStates() {
      setComfyUploadState(
        els.comfyDebugReferenceFileLabel,
        els.comfyDebugReferenceUploadState,
        els.comfyDebugReferenceUploadName,
        els.comfyDebugReference?.value || '',
        '已选择'
      );
      setComfyUploadState(
        els.comfyDebugMiddleFrameReferenceFileLabel,
        els.comfyDebugMiddleFrameUploadState,
        els.comfyDebugMiddleFrameUploadName,
        els.comfyDebugMiddleFrameReference?.value || '',
        '已选择中帧'
      );
      setComfyUploadState(
        els.comfyDebugLastFrameReferenceFileLabel,
        els.comfyDebugLastFrameUploadState,
        els.comfyDebugLastFrameUploadName,
        els.comfyDebugLastFrameReference?.value || '',
        '已选择尾帧'
      );
    }

    function renderComfyReferencePreview(target, value, emptyText) {
      if (!target) return;
      const raw = String(value || '').trim();
      const url = referencePreviewUrl(raw);
      const kind = raw && url && isVideoFile(raw) ? 'video' : raw && url ? 'image' : 'empty';
      if (target.dataset.previewRaw === raw && target.dataset.previewUrl === url && target.dataset.previewKind === kind) {
        return;
      }
      target.dataset.previewRaw = raw;
      target.dataset.previewUrl = url;
      target.dataset.previewKind = kind;
      target.innerHTML = '';
      if (!raw || !url) {
        const empty = document.createElement('span');
        empty.className = 'empty';
        empty.textContent = raw ? '已选择，暂无缩略图预览' : emptyText;
        target.appendChild(empty);
        return;
      }
      if (isVideoFile(raw)) {
        const video = document.createElement('video');
        video.src = url;
        video.controls = false;
        video.muted = true;
        video.playsInline = true;
        video.preload = 'metadata';
        target.appendChild(video);
      } else {
        const img = document.createElement('img');
        img.src = url;
        img.alt = raw.split('/').pop() || 'reference';
        img.loading = 'lazy';
        img.decoding = 'async';
        target.appendChild(img);
      }
    }

    function updateComfyDebugReferencePreviews() {
      const referenceValue = els.comfyDebugReference?.value || '';
      const middleFrameValue = els.comfyDebugMiddleFrameReference?.value || '';
      const lastFrameValue = els.comfyDebugLastFrameReference?.value || '';
      const support = comfyDebugReferenceSupport();
      const showReference = support.hasReference;
      const showMiddleFrame = support.hasMiddleFrame;
      const showLastFrame = support.hasLastFrame;
      if (els.comfyDebugReferencePathField) els.comfyDebugReferencePathField.hidden = !showReference;
      if (els.comfyDebugAssetTagFilterField) els.comfyDebugAssetTagFilterField.hidden = !showReference;
      if (els.comfyDebugStartFrameCard) els.comfyDebugStartFrameCard.hidden = !showReference;
      if (els.comfyDebugReferenceGrid) els.comfyDebugReferenceGrid.hidden = !(showReference || showMiddleFrame || showLastFrame);
      if (els.comfyDebugMiddleFrameCard) els.comfyDebugMiddleFrameCard.hidden = !showMiddleFrame;
      if (els.comfyDebugLastFrameCard) els.comfyDebugLastFrameCard.hidden = !showLastFrame;
      renderComfyReferencePreview(els.comfyDebugReferencePreview, referenceValue, '首帧参考图');
      renderComfyReferencePreview(els.comfyDebugMiddleFramePreview, middleFrameValue, '中帧参考图');
      renderComfyReferencePreview(els.comfyDebugLastFramePreview, lastFrameValue, '尾帧参考图');
      if (els.comfyDebugReferencePreviewMeta) {
        els.comfyDebugReferencePreviewMeta.textContent = referenceValue ? (referenceValue.split('/').pop() || '已选择参考') : '未选择参考';
      }
      if (!showReference && referenceValue && els.comfyDebugReference) {
        els.comfyDebugReference.value = '';
        if (els.comfyDebugAssetReference) els.comfyDebugAssetReference.value = '';
      }
      if (!showMiddleFrame && middleFrameValue && els.comfyDebugMiddleFrameReference) {
        els.comfyDebugMiddleFrameReference.value = '';
      }
      if (!showLastFrame && lastFrameValue && els.comfyDebugLastFrameReference) {
        els.comfyDebugLastFrameReference.value = '';
      }
      updateComfyDebugUploadStates();
    }

    function renderComfyDebugAssetReferenceOptions() {
      if (!els.comfyDebugAssetReference) return;
      const currentValue = els.comfyDebugAssetReference.value;
      const currentMiddleFrameValue = els.comfyDebugMiddleFrameAssetReference?.value || '';
      const currentLastFrameValue = els.comfyDebugLastFrameAssetReference?.value || '';
      const selectedTag = els.comfyDebugAssetTagFilter?.value || '';
      const referenceAssets = assetLibraryItems.filter(item => {
        const file = String(item.file || '');
        const kind = String(item.kind || '').toLowerCase();
        return file
          && (kind === 'image' || isImageFile(file))
          && assetMatchesTag(item, selectedTag);
      });
      els.comfyDebugAssetReference.innerHTML = '';
      const defaultOption = document.createElement('option');
      defaultOption.value = '';
      defaultOption.textContent = referenceAssets.length ? '不使用素材库参考' : '素材库暂无可选图片';
      els.comfyDebugAssetReference.appendChild(defaultOption);
      referenceAssets.forEach(item => {
        const file = String(item.file || '');
        const option = document.createElement('option');
        option.value = file.startsWith('my_workspace/') ? file : `my_workspace/my_asset_library/${file}`;
        const tagText = normalizeAssetTags(item.tags).map(assetTagLabel).join('/');
        option.textContent = `图片 · ${item.name || assetFileLabel(file)}${tagText ? ` · ${tagText}` : ''}`;
        els.comfyDebugAssetReference.appendChild(option);
      });
      if ([...els.comfyDebugAssetReference.options].some(option => option.value === currentValue)) {
        els.comfyDebugAssetReference.value = currentValue;
      }
      if (els.comfyDebugMiddleFrameAssetReference) {
        els.comfyDebugMiddleFrameAssetReference.innerHTML = '';
        const middleDefault = document.createElement('option');
        middleDefault.value = '';
        middleDefault.textContent = referenceAssets.length ? '选择中帧素材' : '素材库暂无可选图片';
        els.comfyDebugMiddleFrameAssetReference.appendChild(middleDefault);
        referenceAssets.filter(item => isImageFile(item.file || '')).forEach(item => {
          const file = String(item.file || '');
          const option = document.createElement('option');
          option.value = file.startsWith('my_workspace/') ? file : `my_workspace/my_asset_library/${file}`;
          const tagText = normalizeAssetTags(item.tags).map(assetTagLabel).join('/');
          option.textContent = `${item.name || assetFileLabel(file)}${tagText ? ` ? ${tagText}` : ''}`;
          els.comfyDebugMiddleFrameAssetReference.appendChild(option);
        });
        if ([...els.comfyDebugMiddleFrameAssetReference.options].some(option => option.value === currentMiddleFrameValue)) {
          els.comfyDebugMiddleFrameAssetReference.value = currentMiddleFrameValue;
        }
      }
      if (els.comfyDebugLastFrameAssetReference) {
        els.comfyDebugLastFrameAssetReference.innerHTML = '';
        const lastDefault = document.createElement('option');
        lastDefault.value = '';
        lastDefault.textContent = referenceAssets.length ? '不使用尾帧素材' : '素材库暂无可选图片';
        els.comfyDebugLastFrameAssetReference.appendChild(lastDefault);
        referenceAssets.filter(item => isImageFile(item.file || '')).forEach(item => {
          const file = String(item.file || '');
          const option = document.createElement('option');
          option.value = file.startsWith('my_workspace/') ? file : `my_workspace/my_asset_library/${file}`;
          const tagText = normalizeAssetTags(item.tags).map(assetTagLabel).join('/');
          option.textContent = `${item.name || assetFileLabel(file)}${tagText ? ` · ${tagText}` : ''}`;
          els.comfyDebugLastFrameAssetReference.appendChild(option);
        });
        if ([...els.comfyDebugLastFrameAssetReference.options].some(option => option.value === currentLastFrameValue)) {
          els.comfyDebugLastFrameAssetReference.value = currentLastFrameValue;
        }
      }
      updateComfyDebugReferencePreviews();
    }

    async function uploadComfyDebugReferenceFile() {
      const file = els.comfyDebugReferenceFile?.files && els.comfyDebugReferenceFile.files[0];
      if (!file) return;
      if (!isImageFile(file.name || '') && !String(file.type || '').startsWith('image/')) {
        if (els.comfyDebugReferenceFile) els.comfyDebugReferenceFile.value = '';
        setStatus('参考图只能上传图片文件，不能上传视频。', true);
        return;
      }
      try {
        if (els.comfyDebugReferenceHint) els.comfyDebugReferenceHint.textContent = `正在上传：${file.name}`;
        const contentBase64 = await fileToBase64(file);
        const result = await api('/api/upload-comfy-debug-reference', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: file.name,
            content_base64: contentBase64,
          }),
        });
        if (els.comfyDebugAssetReference) els.comfyDebugAssetReference.value = '';
        setComfyDebugReference(result.stored_path || '', `已上传：${file.name}`);
        saveCurrentComfyDebugUiState();
        saveSettings();
        setStatus(`已上传参考文件：${result.stored_path}`, false);
      } catch (err) {
        setComfyDebugReference(els.comfyDebugReference?.value || '', err.message || '参考文件上传失败');
        setStatus(err.message || '参考文件上传失败', true);
      }
    }

    async function uploadComfyDebugMiddleFrameReferenceFile() {
      const file = els.comfyDebugMiddleFrameReferenceFile?.files && els.comfyDebugMiddleFrameReferenceFile.files[0];
      if (!file) return;
      if (!isImageFile(file.name || '') && !String(file.type || '').startsWith('image/')) {
        if (els.comfyDebugMiddleFrameReferenceFile) els.comfyDebugMiddleFrameReferenceFile.value = '';
        setStatus('中帧参考文件必须是图片', true);
        return;
      }
      try {
        if (els.comfyDebugMiddleFrameReferenceHint) els.comfyDebugMiddleFrameReferenceHint.textContent = `正在上传中帧：${file.name}`;
        const contentBase64 = await fileToBase64(file);
        const result = await api('/api/upload-comfy-debug-reference', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: file.name,
            content_base64: contentBase64,
          }),
        });
        if (els.comfyDebugMiddleFrameAssetReference) els.comfyDebugMiddleFrameAssetReference.value = '';
        setComfyDebugMiddleFrameReference(result.stored_path || '', `已上传中帧：${file.name}`);
        saveCurrentComfyDebugUiState();
        saveSettings();
        setStatus(`中帧参考已上传：${result.stored_path}`, false);
      } catch (err) {
        setComfyDebugMiddleFrameReference(els.comfyDebugMiddleFrameReference?.value || '', err.message || '中帧上传失败');
        setStatus(err.message || '中帧上传失败', true);
      }
    }

    async function uploadComfyDebugLastFrameReferenceFile() {
      const file = els.comfyDebugLastFrameReferenceFile?.files && els.comfyDebugLastFrameReferenceFile.files[0];
      if (!file) return;
      if (!isImageFile(file.name || '') && !String(file.type || '').startsWith('image/')) {
        if (els.comfyDebugLastFrameReferenceFile) els.comfyDebugLastFrameReferenceFile.value = '';
        setStatus('尾帧只能上传图片文件，不能上传视频。', true);
        return;
      }
      try {
        if (els.comfyDebugLastFrameReferenceHint) els.comfyDebugLastFrameReferenceHint.textContent = `正在上传：${file.name}`;
        const contentBase64 = await fileToBase64(file);
        const result = await api('/api/upload-comfy-debug-reference', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: file.name,
            content_base64: contentBase64,
          }),
        });
        if (els.comfyDebugMiddleFrameAssetReference) els.comfyDebugMiddleFrameAssetReference.value = '';
      if (els.comfyDebugLastFrameAssetReference) els.comfyDebugLastFrameAssetReference.value = '';
        setComfyDebugLastFrameReference(result.stored_path || '', `已上传尾帧：${file.name}`);
        saveCurrentComfyDebugUiState();
        saveSettings();
        setStatus(`已上传尾帧文件：${result.stored_path}`, false);
      } catch (err) {
        setComfyDebugLastFrameReference(els.comfyDebugLastFrameReference?.value || '', err.message || '尾帧文件上传失败');
        setStatus(err.message || '尾帧文件上传失败', true);
      }
    }

    function appendAssetMetadataEditor(card, item) {
      if (!card || !item?.id) return;
      const editor = document.createElement('div');
      editor.className = 'asset-meta-editor';
      editor.onclick = event => event.stopPropagation();
      editor.onkeydown = event => event.stopPropagation();

      const category = document.createElement('select');
      const empty = document.createElement('option');
      empty.value = '';
      empty.textContent = '未分类';
      category.appendChild(empty);
      ASSET_CATEGORY_TAGS.forEach(tag => {
        const option = document.createElement('option');
        option.value = tag.value;
        option.textContent = tag.label;
        category.appendChild(option);
      });
      category.value = assetPrimaryCategory(item);

      const note = document.createElement('input');
      note.placeholder = '备注用途，例如：主角头像 / 办公场景 / 封面风格';
      note.value = item.note || '';

      const save = document.createElement('button');
      save.type = 'button';
      save.textContent = '保存';
      save.onclick = async event => {
        event.preventDefault();
        event.stopPropagation();
        const mediaTag = item.kind || (isImageFile(item.file) ? 'image' : (isAudioFile(item.file) ? 'audio' : 'video'));
        const tags = normalizeAssetTags([mediaTag, category.value]);
        await updateAssetMetadata(item.id, tags, note.value);
      };

      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'asset-delete-btn';
      remove.textContent = '删除';
      remove.onclick = async event => {
        event.preventDefault();
        event.stopPropagation();
        const label = item.name || item.label || item.file || 'asset';
        if (!window.confirm('确定删除这个素材吗？\\n' + label)) return;
        remove.disabled = true;
        remove.textContent = '删除中...';
        await unfavoriteAsset('', item);
      };

      editor.appendChild(category);
      editor.appendChild(note);
      editor.appendChild(save);
      editor.appendChild(remove);
      card.appendChild(editor);
    }

    function renderAssetLibrary() {
      if (!els.assetLibraryGrid) return;
      els.assetLibraryGrid.innerHTML = '';
      renderAssetLibraryTabs();
      renderAssetTagFilters();
      const filteredItems = assetLibraryFilteredItems();
      els.assetLibraryGrid.appendChild(assetLibraryAddCard());
      const previewItems = filteredItems.map(item => ({
        ...item,
        library: true,
        label: item.name || assetFileLabel(item.file),
        kind: item.kind || (isImageFile(item.file) ? 'image' : (isAudioFile(item.file) ? 'audio' : 'video')),
      }));
      previewItems.forEach((item, index) => {
        const card = assetLibraryCard(item, index, previewItems);
        card.onkeydown = event => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            selectAssetLibraryItem(item.id);
          }
        };
        els.assetLibraryGrid.appendChild(card);
      });
      if (!assetLibraryItems.length) {
        const empty = document.createElement('div');
        empty.className = 'muted small';
        empty.textContent = '暂无收藏素材。到“任务输出”的已生成素材里点击“收藏复用”，或从这里导入本地图片/视频。';
        els.assetLibraryGrid.appendChild(empty);
      } else if (!filteredItems.length) {
        const empty = document.createElement('div');
        empty.className = 'muted small';
        empty.textContent = '当前分类或标签下没有素材。';
        els.assetLibraryGrid.appendChild(empty);
      }
      if (selectedAssetLibraryId && !assetLibraryItems.some(item => String(item.id || '') === String(selectedAssetLibraryId))) {
        closeAssetLibraryDetail({ force: true });
      } else if (selectedAssetLibraryId) {
        renderAssetLibraryDetail(assetLibrarySelectedItem());
      }
    }

    function renderAssetLibraryTabs() {
      const buttons = Array.from(els.assetLibraryTabs?.querySelectorAll('[data-asset-section]') || []);
      buttons.forEach(button => {
        const section = button.dataset.assetSection || 'all';
        const count = section === 'all'
          ? assetLibraryItems.length
          : assetLibraryItems.filter(item => assetMatchesLibrarySection(item, section)).length;
        const label = assetLibrarySectionDefinition(section).label || section;
        button.textContent = count ? `${label} ${count}` : label;
        button.classList.toggle('active', section === assetLibrarySection);
      });
    }

    function assetLibraryAddCard() {
      const card = document.createElement('div');
      card.className = 'asset-library-card asset-library-add';
      card.onclick = () => openAssetImportModal();
      const media = document.createElement('span');
      media.className = 'asset-library-media';
      const plus = document.createElement('span');
      plus.className = 'asset-library-plus';
      plus.textContent = '+';
      media.appendChild(plus);
      const title = document.createElement('span');
      title.className = 'asset-library-card-title';
      title.textContent = assetLibraryAddLabel();
      const meta = document.createElement('span');
      meta.className = 'asset-library-card-meta';
      meta.textContent = '本地导入 / 跳转生成';
      card.appendChild(media);
      card.appendChild(title);
      card.appendChild(meta);
      return card;
    }

    function assetLibraryCard(item, index, previewItems) {
      const card = document.createElement('div');
      card.tabIndex = 0;
      card.role = 'button';
      card.className = 'asset-library-card';
      card.classList.toggle('active', String(item.id || '') === String(selectedAssetLibraryId || ''));
      const media = document.createElement('div');
      media.className = 'asset-library-media';
      const previewUrl = assetLibraryMediaUrl(item.id);
      if (assetLibraryKindLabel(item) === '图片') {
        const img = document.createElement('img');
        img.loading = 'lazy';
        img.alt = item.name || assetFileLabel(item.file);
        img.src = previewUrl;
        media.appendChild(img);
      } else if (assetLibraryKindLabel(item) === '视频') {
        const video = document.createElement('video');
        video.muted = true;
        video.playsInline = true;
        video.preload = 'metadata';
        video.src = previewUrl;
        media.appendChild(video);
      } else {
        const audio = document.createElement('audio');
        audio.controls = true;
        audio.preload = 'metadata';
        audio.src = previewUrl;
        media.appendChild(audio);
      }
      const actions = document.createElement('span');
      actions.className = 'asset-library-card-actions';
      const preview = document.createElement('button');
      preview.type = 'button';
      preview.title = '打开预览';
      preview.textContent = '⌕';
      preview.onclick = event => {
        event.stopPropagation();
        openAssetLightboxFromItems('', previewItems, index);
      };
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.title = '删除';
      remove.textContent = '×';
      remove.onclick = async event => {
        event.stopPropagation();
        await deleteAssetLibraryItem(item);
      };
      actions.appendChild(preview);
      actions.appendChild(remove);
      const title = document.createElement('span');
      title.className = 'asset-library-card-title';
      title.textContent = item.name || assetFileLabel(item.file);
      const meta = document.createElement('span');
      meta.className = 'asset-library-card-meta';
      meta.textContent = `${assetLibraryKindLabel(item)} · ${formatAssetLibraryTime(item.updated_at || item.created_at || item.mtime)}`;
      const tags = document.createElement('span');
      tags.className = 'asset-tag-row';
      normalizeAssetTags(item.tags).slice(0, 3).forEach(tag => {
        const chip = document.createElement('span');
        chip.className = 'asset-chip';
        chip.textContent = assetTagLabel(tag);
        tags.appendChild(chip);
      });
      card.appendChild(media);
      card.appendChild(actions);
      card.appendChild(title);
      card.appendChild(meta);
      if (tags.children.length) card.appendChild(tags);
      card.onclick = () => selectAssetLibraryItem(item.id);
      return card;
    }

    function selectAssetLibraryItem(assetId) {
      if (!confirmDiscardAssetLibraryDetailChanges()) return;
      selectedAssetLibraryId = String(assetId || '');
      assetLibraryDetailDirty = false;
      renderAssetLibrary();
      renderAssetLibraryDetail(assetLibrarySelectedItem());
    }

    function renderAssetLibraryDetail(item) {
      if (!els.assetLibraryDetail) return;
      if (!item) {
        els.assetLibraryDetail.hidden = true;
        return;
      }
      els.assetLibraryDetail.hidden = false;
      if (els.assetLibraryDetailPreview) {
        els.assetLibraryDetailPreview.innerHTML = '';
        const url = assetLibraryMediaUrl(item.id);
        if (assetLibraryKindLabel(item) === '图片') {
          const img = document.createElement('img');
          img.className = 'asset-detail-media';
          img.alt = item.name || assetFileLabel(item.file);
          img.src = url;
          img.onload = () => fitAssetDetailPreviewMedia(img);
          if (img.complete && img.naturalWidth) fitAssetDetailPreviewMedia(img);
          els.assetLibraryDetailPreview.appendChild(img);
        } else if (assetLibraryKindLabel(item) === '视频') {
          const video = document.createElement('video');
          video.className = 'asset-detail-media';
          video.controls = true;
          video.preload = 'metadata';
          video.src = url;
          video.onloadedmetadata = () => fitAssetDetailPreviewMedia(video);
          els.assetLibraryDetailPreview.appendChild(video);
        } else {
          const audio = document.createElement('audio');
          audio.className = 'asset-detail-media';
          audio.controls = true;
          audio.preload = 'metadata';
          audio.src = url;
          els.assetLibraryDetailPreview.appendChild(audio);
        }
      }
      if (els.assetLibraryDetailName) els.assetLibraryDetailName.value = item.name || assetFileLabel(item.file);
      renderAssetLibraryCategorySelect(els.assetLibraryDetailCategory, assetPrimaryCategory(item));
      if (els.assetLibraryDetailNote) els.assetLibraryDetailNote.value = item.note || '';
      setAssetLibraryDetailDirty(false);
    }

    function fitAssetDetailPreviewMedia(media) {
      if (!media || !els.assetLibraryDetailPreview) return;
      const stageRect = els.assetLibraryDetailPreview.getBoundingClientRect();
      const stageWidth = Math.max(1, stageRect.width - 16);
      const stageHeight = Math.max(1, stageRect.height - 16);
      const naturalWidth = Number(media.naturalWidth || media.videoWidth || 0);
      const naturalHeight = Number(media.naturalHeight || media.videoHeight || 0);
      if (!naturalWidth || !naturalHeight) return;
      const stageRatio = stageWidth / stageHeight;
      const mediaRatio = naturalWidth / naturalHeight;
      media.classList.remove('fit-height', 'fit-width');
      if (mediaRatio <= stageRatio) {
        media.classList.add('fit-height');
      } else {
        media.classList.add('fit-width');
      }
    }

    function closeAssetLibraryDetail(options = {}) {
      if (!options.force && !confirmDiscardAssetLibraryDetailChanges()) return;
      selectedAssetLibraryId = '';
      assetLibraryDetailDirty = false;
      if (els.assetLibraryDetail) els.assetLibraryDetail.hidden = true;
      renderAssetLibrary();
    }

    function handleAssetLibraryBlankClick(event) {
      if (document.body.dataset.view !== 'assets') return;
      if (!els.assetLibraryDetail || els.assetLibraryDetail.hidden || !selectedAssetLibraryId) return;
      const target = event.target;
      if (!target || !(target instanceof Element)) return;
      if (target.closest('#assetLibraryDetail')) return;
      if (target.closest('.asset-library-card')) return;
      if (target.closest('#assetImportModal')) return;
      if (target.closest('#assetLightbox')) return;
      if (target.closest('button, input, select, textarea, a, label, summary')) return;
      closeAssetLibraryDetail();
    }

    async function saveAssetLibraryDetail() {
      const item = assetLibrarySelectedItem();
      if (!item) return;
      const mediaTag = item.kind || (isImageFile(item.file) ? 'image' : (isAudioFile(item.file) ? 'audio' : 'video'));
      const category = els.assetLibraryDetailCategory?.value || defaultAssetCategoryForSection();
      await updateAssetMetadata(
        item.id,
        normalizeAssetTags([mediaTag, category]),
        els.assetLibraryDetailNote?.value || '',
        els.assetLibraryDetailName?.value || ''
      );
      selectedAssetLibraryId = String(item.id || '');
      assetLibraryDetailDirty = false;
      renderAssetLibraryDetail(assetLibrarySelectedItem());
    }

    async function deleteAssetLibraryItem(item = assetLibrarySelectedItem()) {
      if (!item) return;
      if (!confirmDiscardAssetLibraryDetailChanges()) return;
      const label = item.name || item.label || item.file || 'asset';
      if (!window.confirm('确定删除这个素材吗？\n' + label)) return;
      await unfavoriteAsset('', item);
      if (String(selectedAssetLibraryId || '') === String(item.id || '')) {
        selectedAssetLibraryId = '';
        assetLibraryDetailDirty = false;
        if (els.assetLibraryDetail) els.assetLibraryDetail.hidden = true;
      }
      renderAssetLibrary();
    }

    function openAssetImportModal() {
      if (!els.assetImportModal) return;
      if (els.assetImportTitle) els.assetImportTitle.textContent = assetLibraryAddLabel();
      if (els.assetImportFile) els.assetImportFile.value = '';
      if (els.assetImportName) els.assetImportName.value = '';
      if (els.assetImportNote) els.assetImportNote.value = '';
      renderAssetLibraryCategorySelect(els.assetImportCategory, defaultAssetCategoryForSection());
      if (els.assetImportStatus) els.assetImportStatus.textContent = '';
      els.assetImportModal.hidden = false;
    }

    function closeAssetImportModal() {
      if (els.assetImportModal) els.assetImportModal.hidden = true;
    }

    async function importAssetLibraryFile() {
      const file = els.assetImportFile?.files && els.assetImportFile.files[0];
      if (!file) {
        if (els.assetImportStatus) els.assetImportStatus.textContent = '请选择一个图片、视频或音频文件。';
        return;
      }
      if (!isImageFile(file.name || '') && !isVideoFile(file.name || '') && !isAudioFile(file.name || '') && !String(file.type || '').match(/^(image|video|audio)\//)) {
        if (els.assetImportStatus) els.assetImportStatus.textContent = '只支持图片、视频或音频文件。';
        return;
      }
      try {
        if (els.assetImportSaveBtn) els.assetImportSaveBtn.disabled = true;
        if (els.assetImportStatus) els.assetImportStatus.textContent = `正在导入：${file.name}`;
        const category = els.assetImportCategory?.value || defaultAssetCategoryForSection();
        const mediaTag = (isImageFile(file.name || '') || String(file.type || '').startsWith('image/'))
          ? 'image'
          : ((isAudioFile(file.name || '') || String(file.type || '').startsWith('audio/')) ? 'audio' : 'video');
        const contentBase64 = await fileToBase64(file);
        const result = await api('/api/import-asset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: file.name,
            content_base64: contentBase64,
            name: els.assetImportName?.value || '',
            note: els.assetImportNote?.value || '',
            tags: normalizeAssetTags([mediaTag, category]),
          }),
        });
        closeAssetImportModal();
        await loadAssetLibrary();
        if (result.asset?.id) selectAssetLibraryItem(result.asset.id);
        setStatus(`已导入素材：${result.asset?.name || file.name}`, false);
      } catch (err) {
        if (els.assetImportStatus) els.assetImportStatus.textContent = err.message || '导入失败';
        setStatus(err.message || '导入失败', true);
      } finally {
        if (els.assetImportSaveBtn) els.assetImportSaveBtn.disabled = false;
      }
    }

    function goComfyDebugFromAssetLibrary() {
      closeAssetImportModal();
      showView('comfyDebug');
    }

    function normalizeAssetPreviewItems(items) {
      return (Array.isArray(items) ? items : []).map(item => {
        const file = typeof item === 'string' ? item : item?.file;
        if (!file) return null;
        return {
          ...(typeof item === 'string' ? {} : item),
          file,
          label: typeof item === 'string' ? assetFileLabel(file) : (item.label || assetFileLabel(file)),
          name: typeof item === 'string' ? String(file).split('/').pop() : (item.name || String(file).split('/').pop()),
          kind: isImageFile(file) ? 'image' : (isAudioFile(file) ? 'audio' : 'video'),
        };
      }).filter(item => item && item.file && (isImageFile(item.file) || isVideoFile(item.file) || isAudioFile(item.file)));
    }

    function openAssetLightboxFromItems(taskName, items, index = 0) {
      const nextItems = normalizeAssetPreviewItems(items);
      if (!nextItems.length) return;
      assetPreviewTaskName = taskName || "";
      assetPreviewItems = nextItems;
      openAssetLightbox(index);
    }

    function openAssetLightbox(index = 0) {
      if (!assetPreviewItems.length || !els.assetLightbox) return;
      assetPreviewIndex = Math.max(0, Math.min(assetPreviewItems.length - 1, Number(index) || 0));
      els.assetLightbox.hidden = false;
      renderAssetLightbox();
    }

    function closeAssetLightbox() {
      if (!els.assetLightbox) return;
      els.assetLightbox.hidden = true;
      if (els.assetLightboxStage) els.assetLightboxStage.innerHTML = '';
    }

    function handleAssetLightboxBackgroundClick(event) {
      if (!els.assetLightbox || els.assetLightbox.hidden) return;
      if (event.defaultPrevented) return;
      const path = typeof event.composedPath === 'function' ? event.composedPath() : [];
      const clickedInteractive = path.some(node => {
        const tag = String(node?.tagName || '').toLowerCase();
        const classList = node?.classList;
        return tag === 'button'
          || tag === 'img'
          || tag === 'video'
          || tag === 'audio'
          || classList?.contains?.('asset-lightbox-head')
          || classList?.contains?.('asset-lightbox-foot');
      });
      if (clickedInteractive) return;
      closeAssetLightbox();
    }

    function moveAssetLightbox(delta) {
      if (!assetPreviewItems.length || els.assetLightbox?.hidden) return;
      if (assetPreviewItems.length <= 1) return;
      assetPreviewIndex = (assetPreviewIndex + delta + assetPreviewItems.length) % assetPreviewItems.length;
      renderAssetLightbox();
    }

    function renderAssetLightbox() {
      const item = assetPreviewItems[assetPreviewIndex];
      if (!item || !els.assetLightboxStage) return;
      const url = assetItemUrl(assetPreviewTaskName, item);
      els.assetLightboxStage.innerHTML = '';
      if (isImageFile(item.file)) {
        const img = document.createElement('img');
        img.className = 'asset-lightbox-media';
        img.decoding = 'async';
        img.alt = item.label || assetFileLabel(item.file);
        img.src = url;
        img.onload = () => fitAssetLightboxMedia(img);
        els.assetLightboxStage.appendChild(img);
        if (img.complete && img.naturalWidth) fitAssetLightboxMedia(img);
      } else if (isVideoFile(item.file)) {
        const video = document.createElement('video');
        video.className = 'asset-lightbox-media';
        video.src = url;
        video.controls = true;
        video.autoplay = true;
        video.playsInline = true;
        video.preload = 'metadata';
        video.tabIndex = 0;
        video.onkeydown = event => {
          if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
            event.preventDefault();
            event.stopPropagation();
            moveAssetLightbox(event.key === 'ArrowLeft' ? -1 : 1);
          }
        };
        video.onloadedmetadata = () => fitAssetLightboxMedia(video);
        els.assetLightboxStage.appendChild(video);
      } else {
        const audio = document.createElement('audio');
        audio.className = 'asset-lightbox-media';
        audio.src = url;
        audio.controls = true;
        audio.autoplay = true;
        audio.preload = 'metadata';
        els.assetLightboxStage.appendChild(audio);
      }
      els.assetLightboxTitle.textContent = item.label || assetFileLabel(item.file);
      els.assetLightboxMeta.textContent = item.file;
      els.assetLightboxCounter.textContent = `${assetPreviewIndex + 1} / ${assetPreviewItems.length}`;
      els.assetLightboxPrevBtn.disabled = assetPreviewItems.length <= 1;
      els.assetLightboxNextBtn.disabled = assetPreviewItems.length <= 1;
      els.assetLightboxOpenBtn.onclick = () => window.open(url, '_blank', 'noopener');
      if (els.assetLightboxFavoriteBtn) {
        const favorited = item.library || isAssetFavorited(assetPreviewTaskName, item);
        els.assetLightboxFavoriteBtn.hidden = false;
        els.assetLightboxFavoriteBtn.disabled = Boolean(!item.library && !assetPreviewTaskName);
        els.assetLightboxFavoriteBtn.textContent = favorited ? '取消收藏' : '收藏复用';
        els.assetLightboxFavoriteBtn.onclick = () => {
          if (els.assetLightboxFavoriteBtn.disabled) return;
          els.assetLightboxFavoriteBtn.disabled = true;
          els.assetLightboxFavoriteBtn.textContent = favorited ? '取消中...' : '收藏中...';
          if (favorited) {
            unfavoriteAsset(assetPreviewTaskName, item);
          } else {
            favoriteAsset(assetPreviewTaskName, item);
          }
        };
      }
    }

    function fitAssetLightboxMedia(media) {
      if (!media || !els.assetLightboxStage) return;
      const stageRect = els.assetLightboxStage.getBoundingClientRect();
      const stageWidth = Math.max(1, stageRect.width - 20);
      const stageHeight = Math.max(1, stageRect.height - 20);
      const naturalWidth = Number(media.naturalWidth || media.videoWidth || 0);
      const naturalHeight = Number(media.naturalHeight || media.videoHeight || 0);
      if (!naturalWidth || !naturalHeight) return;
      const stageRatio = stageWidth / stageHeight;
      const mediaRatio = naturalWidth / naturalHeight;
      media.classList.remove('fit-height', 'fit-width');
      if (mediaRatio <= stageRatio) {
        media.classList.add('fit-height');
      } else {
        media.classList.add('fit-width');
      }
    }

    function assetFileButton(taskName, asset) {
      const file = typeof asset === 'string' ? asset : asset.file;
      const label = typeof asset === 'string' ? assetFileLabel(file) : (asset.label || assetFileLabel(file));
      const btn = outputFileButton(file, assetFileLabel(file), assetFileSubtitle(file));
      btn.querySelector('.output-link-title').textContent = label;
      if (isImageFile(file) || isVideoFile(file)) {
        btn.onclick = () => window.open(mediaUrl(taskName, file), '_blank', 'noopener');
        if (isImageFile(file)) {
          const img = document.createElement('img');
          img.className = 'asset-thumb';
          img.loading = 'lazy';
          img.alt = assetFileLabel(file);
          img.src = mediaUrl(taskName, file);
          btn.insertBefore(img, btn.firstChild);
        }
      }
      return btn;
    }

    function mediaUrl(taskName, file) {
      return `/api/media?task=${encodeURIComponent(taskName)}&file=${encodeURIComponent(file)}`;
    }

    function assetLibraryMediaUrl(id) {
      return `/api/asset-library-media?id=${encodeURIComponent(id)}`;
    }

    function assetItemUrl(taskName, item) {
      if (item?.library && item.id) return assetLibraryMediaUrl(item.id);
      return mediaUrl(taskName, item.file);
    }

    function isMediaFile(file) {
      return /\.(mp4|mov|webm|m4v|mp3|wav|aac|m4a|png|jpg|jpeg|jpe|jfif|pjpeg|pjp|webp|bmp|dib|gif|tif|tiff|avif|heic|heif)$/i.test(String(file || ''));
    }

    function isImageFile(file) {
      return /\.(png|jpg|jpeg|jpe|jfif|pjpeg|pjp|webp|bmp|dib|gif|tif|tiff|avif|heic|heif)$/i.test(String(file || ''));
    }

    function isVideoFile(file) {
      return /\.(mp4|mov|webm|m4v)$/i.test(String(file || ''));
    }

    function isAudioFile(file) {
      return /\.(mp3|wav|aac|m4a|flac|ogg)$/i.test(String(file || ''));
    }

    function assetFileLabel(file) {
      const name = String(file || '');
      if (name === 'long_video_final.mp4' || name === 'final_video.mp4') return '最终视频';
      if (name.startsWith('generated_images/')) return `图片素材 · ${name.split('/').pop()}`;
      if (name.startsWith('video_clips/')) return `视频素材 · ${name.split('/').pop()}`;
      if (name.startsWith('comfyui/')) return `ComfyUI 素材 · ${name.split('/').pop()}`;
      return name;
    }

    function assetFileSubtitle(file) {
      if (isMediaFile(file)) return '点击打开媒体预览';
      return file;
    }

    function outputFileButton(file, title, subtitle) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `output-link ${selectedFile === file ? 'active' : ''}`;
      btn.dataset.file = file;
      const main = document.createElement('span');
      main.className = 'output-link-title';
      main.textContent = title;
      const sub = document.createElement('span');
      sub.className = 'muted small output-link-subtitle';
      sub.textContent = subtitle;
      btn.appendChild(main);
      btn.appendChild(sub);
      btn.onclick = () => openFile(file);
      return btn;
    }

    function stepFileLabel(file) {
      const match = String(file).match(/^step_(\d+)_(.*)\/output\.md$/);
      if (!match) return file;
      const agent = match[2].replaceAll('_', ' ');
      return `${Number(match[1])}. ${agent}`;
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[char]));
    }

    function renderFiles(files) {
      els.fileTabs.innerHTML = '';
      const visibleFiles = visibleTaskFiles(files);
      if (!visibleFiles.length) {
        els.fileTabs.innerHTML = '<span class="muted small">暂无可查看文件</span>';
        return;
      }
      for (const file of visibleFiles) {
        const btn = document.createElement('button');
        btn.textContent = compactTaskFileLabel(file);
        btn.title = file;
        btn.dataset.file = file;
        btn.className = selectedFile === file ? 'active' : '';
        btn.onclick = () => openFile(file);
        els.fileTabs.appendChild(btn);
      }
    }

    function renderVideoPreview(taskName, files) {
      const videoFile = preferredVideoFile(files || []);
      if (!videoFile) {
        clearVideoPreview();
        return;
      }
      els.videoPreviewBox.hidden = false;
      els.videoPreviewMeta.textContent = videoFile;
      els.videoPreview.src = `/api/media?task=${encodeURIComponent(taskName)}&file=${encodeURIComponent(videoFile)}`;
    }

    function clearVideoPreview() {
      els.videoPreview.removeAttribute('src');
      els.videoPreview.load();
      els.videoPreviewMeta.textContent = '';
      els.videoPreviewBox.hidden = true;
    }

    function preferredVideoFile(files) {
      const list = Array.isArray(files) ? files : [];
      return list.find(file => /(^|\/)(long_video_final|final_video)\.mp4$/i.test(String(file || ''))) || '';
    }

    function visibleTaskFiles(files) {
      const list = Array.isArray(files) ? files : [];
      if (els.showDebugFiles.checked) return list;
      return list.filter(isPrimaryTaskTabFile);
    }

    function isPrimaryTaskTabFile(file) {
      const name = String(file || '');
      return name === 'input.md' || name === 'final_output.md';
    }

    function compactTaskFileLabel(file) {
      const name = String(file || '');
      if (name === 'input.md') return '原始需求';
      if (name === 'final_output.md') return '最终汇总';
      if (name === 'production_manifest.json') return '自动生成清单';
      if (name === 'auto_production.md') return '自动生成说明';
      if (name === 'final_video.mp4' || name === 'long_video_final.mp4') return '最终视频';
      if (/^step_\d+_.*\/output\.md$/.test(name)) return stepFileLabel(name);
      if (name.startsWith('export_package/')) return name.replace('export_package/', '产品包/');
      return name;
    }

    async function openFile(file) {
      if (!selectedTask) return;
      if (isMediaFile(file)) {
        window.open(mediaUrl(selectedTask, file), '_blank', 'noopener');
        return;
      }
      selectedFile = file;
      const data = await api(`/api/file?task=${encodeURIComponent(selectedTask)}&file=${encodeURIComponent(file)}`);
      els.fileContent.value = data.content;
      for (const btn of els.fileTabs.querySelectorAll('button')) {
        btn.classList.toggle('active', btn.dataset.file === file);
      }
      for (const btn of document.querySelectorAll('.output-link')) {
        btn.classList.toggle('active', btn.dataset.file === file);
      }
      renderStepConfirmBar();
      syncOutputButtons();
    }

    function syncOutputButtons() {
      const hasTask = Boolean(selectedTask);
      const hasFile = Boolean(selectedTask && selectedFile);
      const running = Boolean(currentRunId && ['queued', 'running'].includes(currentRunStatus));
      const confirmStep = awaitingConfirmationStep();
      const isConfirmingCurrentStep = Boolean(confirmStep && selectedFile === stepOutputFileForStep(confirmStep));
      const isAwaitingStepConfirmation = Boolean(confirmStep);
      const comfyGateActive = activeComfyDebugGate();
      const taskStopped = selectedTaskIsStopped();
      const actionSet = new Set(selectedTaskAllowedActions || []);
      const hasStructuredActions = actionSet.size > 0;
      els.saveFileBtn.disabled = running || !hasFile;
      els.rebuildFinalBtn.disabled = running || !hasTask || (hasStructuredActions && !actionSet.has('rebuild_final'));
      els.exportTaskBtn.disabled = running || !hasTask || (hasStructuredActions && !actionSet.has('export'));
      els.resumeTaskBtn.hidden = false;
      els.resumeTaskBtn.disabled = running || !hasTask || (hasStructuredActions && !actionSet.has('resume'));
      els.resumeTaskBtn.textContent = els.workflowAdvanceMode.value === 'step_confirm' ? '继续下一步' : '继续任务';
      if (els.outputCancelRunBtn) {
        const canCancel = !taskStopped && (running || actionSet.has('cancel'));
        els.outputCancelRunBtn.hidden = !canCancel;
        els.outputCancelRunBtn.disabled = !canCancel;
      }
      els.rerunStepBtn.disabled = running || !hasFile || !stepNumberFromFile(selectedFile) || (hasStructuredActions && !actionSet.has('rerun_step'));
      els.confirmStepContinueBtn.disabled = taskStopped || running || comfyGateActive || !isAwaitingStepConfirmation || (hasStructuredActions && !actionSet.has('confirm_step'));
      els.confirmStepRerunBtn.disabled = taskStopped || running || !isConfirmingCurrentStep || (hasStructuredActions && !actionSet.has('rerun_step'));
    }

    function stepNumberFromFile(file) {
      const match = String(file || '').match(/^step_(\d+)_.*\/output\.md$/);
      return match ? Number(match[1]) : 0;
    }

    async function saveCurrentFile() {
      if (!selectedTask || !selectedFile) return;
      setStatus('正在保存当前输出文件');
      try {
        await api('/api/save-file', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task: selectedTask,
            file: selectedFile,
            content: els.fileContent.value,
          }),
        });
        setStatus(`已保存：${selectedFile}`);
      } catch (err) {
        setStatus(err.message, true);
      }
    }

    async function rebuildFinalOutput() {
      if (!selectedTask) return;
      setStatus('正在重建最终汇总');
      try {
        const result = await api('/api/rebuild-final-output', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task: selectedTask }),
        });
        setStatus(`已重建：${result.file}`);
        await selectTask(selectedTask);
        await openFile('final_output.md');
      } catch (err) {
        setStatus(err.message, true);
      }
    }

    async function rerunCurrentStep() {
      if (!selectedTask || !selectedFile) return;
      const step = stepNumberFromFile(selectedFile);
      if (!step) return;
      const model = els.model.value === 'custom' ? els.customModel.value.trim() : els.model.value;
      if (els.model.value === 'custom' && !model) {
        setStatus('请输入自定义模型名', true);
        return;
      }
      if (!confirm(`确定重跑第 ${step} 步？\n\n系统会覆盖该步骤 output.md，并基于当前各步骤输出重建 final_output.md。`)) return;
      setStatus(`正在重跑第 ${step} 步`);
      els.rerunStepBtn.disabled = true;
      autoFocusOutputDuringRun = true;
      setWorkflowInteractionLocked(true);
      showStartupProgress(`重跑第 ${step} 步`);
      showView('output');
      try {
        await ensureLocalModelReady(model);
        const result = await api('/api/rerun-step', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task: selectedTask,
            step,
            provider: els.provider.value,
            model,
            api_key: els.apiKey.value.trim(),
            base_url: els.baseUrl.value.trim(),
            timeout: Number(els.modelTimeout.value || 900),
          }),
        });
        setStatus(`重跑任务已开始：第 ${step} 步`);
        trackRun(result.run_id);
        renderProgress(result);
        showView('output');
        await pollRunStatus(result.run_id);
      } catch (err) {
        setStatus(err.message, true);
        setWorkflowInteractionLocked(false);
        setRunButtonProgress(0);
      } finally {
        syncOutputButtons();
      }
    }

    async function resumeSelectedTask(options = {}) {
      if (!selectedTask) return;
      const model = els.model.value === 'custom' ? els.customModel.value.trim() : els.model.value;
      if (els.model.value === 'custom' && !model) {
        setStatus('请输入自定义模型名', true);
        return;
      }
      const resumeLabel = els.workflowAdvanceMode.value === 'step_confirm' ? '继续下一步' : '继续任务';
      const resumeStatusText = els.workflowAdvanceMode.value === 'auto'
        ? '已切换为全自动，正在继续后续步骤'
        : '正在继续下一步';
      if (!options.skipConfirm && !confirm(`确定${resumeLabel}？\n\n系统会从第一个失败、缺少 output.md 或输出为空的步骤继续执行，并写回当前任务目录。`)) return;
      saveSettings();
      resetProgress();
      autoFocusOutputDuringRun = true;
      setWorkflowInteractionLocked(true);
      showStartupProgress('继续中');
      showView('output');
      setStatus(resumeStatusText);
      els.resumeTaskBtn.disabled = true;
      try {
        await ensureLocalModelReady(model);
        const { productionConfig } = await collectProductionConfig();
        const result = await api('/api/resume-task', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task: selectedTask,
            provider: els.provider.value,
            model,
            api_key: els.apiKey.value.trim(),
            base_url: els.baseUrl.value.trim(),
            timeout: Number(els.modelTimeout.value || 900),
            production_config: productionConfig,
            image_api_key: '',
            image_base_url: '',
            video_api_key: '',
            video_base_url: '',
            comfy_api_key: els.comfyApiKey.value.trim(),
            comfy_base_url: els.comfyBaseUrl.value.trim(),
          }),
        });
        setStatus(`任务已开始${resumeLabel}`);
        trackRun(result.run_id);
        renderProgress(result);
        showView('output');
        await pollRunStatus(result.run_id);
        if (selectedTask) {
          await selectTask(selectedTask);
        }
      } catch (err) {
        setStatus(err.message, true);
        setWorkflowInteractionLocked(false);
        setRunButtonProgress(0);
        syncOutputButtons();
      }
    }

    async function exportCurrentTask() {
      if (!selectedTask) return;
      setStatus('正在导出产品包');
      try {
        const result = await api('/api/export-task', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task: selectedTask,
            template: 'long_video',
          }),
        });
        setStatus(`已导出产品包：${result.export_dir}`);
        await selectTask(selectedTask);
      } catch (err) {
        setStatus(err.message, true);
      }
    }

    async function retryProductionJob(jobId) {
      if (!selectedTask) return;
      const actionLabel = productionRetryAction(jobId) || '重试生产任务';
      if (!confirm(`确定${actionLabel}？\n\n系统会复用当前任务目录里的生产包，并按系统配置重新执行该生产分支。`)) return;
      saveSettings();
      resetProgress();
      autoFocusOutputDuringRun = true;
      setWorkflowInteractionLocked(true);
      showStartupProgress(actionLabel);
      showView('output');
      setStatus(`${actionLabel}已开始`);
      try {
        const { productionConfig } = await collectProductionConfig();
        const result = await api('/api/retry-production-job', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task: selectedTask,
            job: jobId,
            production_config: productionConfig,
            image_api_key: '',
            image_base_url: '',
            video_api_key: '',
            video_base_url: '',
            comfy_api_key: els.comfyApiKey.value.trim(),
            comfy_base_url: els.comfyBaseUrl.value.trim(),
          }),
        });
        trackRun(result.run_id);
        renderProgress(result);
        await pollRunStatus(result.run_id);
        if (selectedTask) {
          await selectTask(selectedTask);
        }
      } catch (err) {
        setStatus(err.message, true);
        setWorkflowInteractionLocked(false);
        setRunButtonProgress(0);
        syncOutputButtons();
      }
    }

    async function collectProductionConfig() {
      const voiceReferenceAudio = els.voiceMode.value === 'voxcpm2' ? await uploadVoiceReferenceAudio() : '';
      const voiceProvider = els.voiceMode.value === 'windows_sapi'
        ? 'windows_sapi'
        : (['voxcpm2', 'preset'].includes(els.voiceMode.value) ? 'voxcpm2' : '');
      const imageConfig = {
        tool: 'prompt_only',
        positive_prompt: '',
        model: '',
        size: '',
        count_per_shot: '',
        style: '',
        quality: '',
        negative_prompt: '',
        consistency: '',
        seed: '',
        guidance_scale: '',
        steps: '',
        denoise_strength: '',
        sampler: '',
        control: '',
        api_key_provided: false,
        base_url_provided: false,
        workflow_endpoint: '',
        instance_type: '',
        node_info_list_json: '',
        poll_timeout_seconds: 900,
      };
      const videoConfig = {
        tool: 'prompt_only',
        positive_prompt: '',
        model: '',
        aspect_ratio: '',
        duration: '',
        style: '',
        prompt_notes: '',
        negative_prompt: '',
        seed: '',
        fps: '',
        motion_strength: '',
        camera_motion: '',
        resolution: '',
        guidance_scale: '',
        frames: '',
        image_strength: '',
        camera_path: '',
        audio_notes: '',
        advanced_params: '',
        api_key_provided: false,
        base_url_provided: false,
        workflow_endpoint: '',
        node_info_list_json: '',
        poll_timeout_seconds: 1800,
      };
      const productionConfig = {
        mode: els.autoProductionMode.value,
        workflow_advance_mode: els.workflowAdvanceMode.value,
        step_confirmation: els.workflowAdvanceMode.value === 'step_confirm',
        comfy_debug_gate: {
          enabled: els.comfyDebugGate.value === 'on',
          order: 'debug_workflow_order',
        },
        image_config: imageConfig,
        video_config: videoConfig,
        voice_config: {
          mode: els.voiceMode.value,
          provider: voiceProvider,
          voice_preset: els.voicePreset.value,
          voice_preset_name: selectedVoicePresetLabel(),
          reference_audio: voiceReferenceAudio,
          reference_text: els.voiceReferenceText.value.trim(),
          command_template: els.voiceCommandTemplate.value.trim() || defaultVoxCPM2CommandTemplate(),
          timeout_seconds: Number(els.voiceTimeout.value || 3600),
        },
        compose_config: {
          tool: els.composeTool.value,
          execution_mode: els.autoProductionMode.value,
          final_video_name: els.finalVideoName.value.trim() || 'final_video.mp4',
          api_key_provided: Boolean(els.comfyApiKey.value.trim()),
          base_url_provided: Boolean(els.comfyBaseUrl.value.trim()),
          base_url: els.comfyBaseUrl.value.trim(),
          workflow_endpoint: els.comfyWorkflowEndpoint.value.trim(),
          node_info_list_json: els.comfyNodeInfoList.value.trim(),
          poll_timeout_seconds: Number(els.comfyPollTimeout.value || 3600),
          workflow_preset_id: getSelectedComfyWorkflowPreset()?.id || '',
          workflow_preset_name: getSelectedComfyWorkflowPreset()?.name || '',
          workflow_preset_purpose: els.comfyWorkflowPresetNote.value.trim(),
          workflow_library: getComfyWorkflowLibraryPayload(),
        },
        quality_config: {
          enabled: els.assetQualityGate.value === 'on',
          max_attempts: Number(els.assetMaxAttempts.value || 2),
          min_score: Number(els.assetMinScore.value || 70),
          min_file_size_kb: 64,
        },
      };
      return { imageConfig, videoConfig, productionConfig };
    }

    function applyProductTemplate(fillSample = false) {
      setIfExists(els.productTemplate, 'long_video');
      const template = PRODUCT_TEMPLATES.long_video;
      if (!template) return;
      setIfExists(els.workflow, template.workflow);
      if (fillSample) els.taskTitle.value = template.taskTitle || '';
      setIfExists(els.autoProductionMode, template.autoProductionMode);
      setIfExists(els.imageSize, template.imageSize);
      setIfExists(els.videoAspect, template.videoAspect);
      setIfExists(els.videoDuration, template.videoDuration);
      if (fillSample) els.userInput.value = template.sample || '';
      saveSettings();
    }

    function assetLibraryContextText() {
      const items = Array.isArray(assetLibraryItems) ? assetLibraryItems.slice(0, 30) : [];
      if (!items.length) return '';
      const lines = items.map((item, index) => {
        const path = 'my_workspace/my_asset_library/' + item.file;
        const tags = normalizeAssetTags(item.tags).map(assetTagLabel).join('/');
        const note = String(item.note || '').trim();
        return `${index + 1}. ${item.name || item.file} | ${item.kind || ''} | 标签:${tags || '无'} | 备注:${note || '无'} | ${path}`;
      });
      return [
        '',
        '## 可复用素材库',
        '优先复用以下已收藏的好素材；只有素材库不能覆盖的画面才新生成。需要参考图/参考视频时，可在 image_prompts/video_prompts 的 reference_image 字段引用这些路径。',
        ...lines,
      ].join('\n');
    }

    async function loadComfyDebugWorkflows() {
      if (!els.comfyDebugWorkflowList) return;
      try {
        const data = await api('/api/comfy-debug-workflows');
        comfyDebugWorkflows = Array.isArray(data.workflows)
          ? data.workflows.map(normalizeComfyDebugWorkflowDefinition)
          : [];
        ensureComfyDebugWorkflowsInLibrary();
        comfyDebugWorkflows.forEach(workflow => {
          normalizeComfyDebugWorkflowSavedConfig(getComfyWorkflowLibraryItemById(workflow.id), workflow);
        });
        saveSettings();
        if (!activeComfyDebugWorkflowId && comfyDebugWorkflows.length) {
          activeComfyDebugWorkflowId = comfyDebugWorkflows[0].id;
        }
        if (activeComfyDebugWorkflowId) {
          setActiveComfyDebugWorkflow(activeComfyDebugWorkflowId, true, activeComfyDebugWorkflowMode);
        }
        renderComfyDebugWorkflows();
        resumeComfyDebugPolls();
        if (els.comfyDebugStatus && !activeComfyDebugState()?.running) {
          els.comfyDebugStatus.textContent = `${comfyDebugWorkflows.length} 个调试模块`;
          els.comfyDebugStatus.classList.remove('error');
        }
      } catch (err) {
        if (els.comfyDebugStatus) {
          els.comfyDebugStatus.textContent = err.message;
          els.comfyDebugStatus.classList.add('error');
        }
      }
    }

    function formatComfyDebugElapsed(seconds) {
      const total = Math.max(0, Math.floor(Number(seconds) || 0));
      const hours = Math.floor(total / 3600);
      const minutes = Math.floor((total % 3600) / 60);
      const secs = total % 60;
      if (hours > 0) return `${hours}小时${String(minutes).padStart(2, '0')}分`;
      if (minutes > 0) return `${minutes}分${String(secs).padStart(2, '0')}秒`;
      return `${secs}秒`;
    }

    function comfyDebugElapsedSeconds(state = {}, job = null) {
      const explicit = Number(state.elapsedSeconds || state.elapsed_seconds || job?.elapsed_seconds || 0);
      if (explicit > 0) return explicit;
      const started = Number(state.startedAt || state.started_at || job?.started_at || job?.created_at || 0);
      if (!started) return 0;
      const finished = Number(state.finishedAt || state.finished_at || job?.finished_at || 0);
      const end = finished || (Date.now() / 1000);
      return Math.max(0, end - started);
    }

    function comfyDebugElapsedLabel(state = {}, job = null) {
      const seconds = comfyDebugElapsedSeconds(state, job);
      return seconds > 0 ? `耗时 ${formatComfyDebugElapsed(seconds)}` : '';
    }

    function comfyDebugTimingFromJob(job = {}, fallback = {}) {
      const startedAt = Number(job.started_at || job.created_at || fallback.startedAt || fallback.started_at || 0);
      const finishedAt = Number(job.finished_at || fallback.finishedAt || fallback.finished_at || 0);
      const elapsedSeconds = Number(job.elapsed_seconds || fallback.elapsedSeconds || fallback.elapsed_seconds || 0);
      return { startedAt, finishedAt, elapsedSeconds };
    }

    function refreshComfyDebugElapsedDisplay() {
      let hasRunning = false;
      for (const state of comfyDebugStateByWorkflowId.values()) {
        if (state?.running) {
          hasRunning = true;
          break;
        }
      }
      if (!hasRunning) {
        if (comfyDebugElapsedTimer) {
          clearInterval(comfyDebugElapsedTimer);
          comfyDebugElapsedTimer = null;
        }
        return;
      }
      renderComfyDebugWorkflows({ refreshForm: false });
      const activeState = comfyDebugStateByWorkflowId.get(activeComfyDebugStateKey()) || {};
      if (activeState.running) {
        renderComfyDebugRunningAsync(activeComfyDebugWorkflow(), activeState);
      }
    }

    function ensureComfyDebugElapsedTimer() {
      if (comfyDebugElapsedTimer) return;
      comfyDebugElapsedTimer = setInterval(refreshComfyDebugElapsedDisplay, 1000);
    }

    function renderComfyDebugWorkflows(options = {}) {
      const refreshForm = options.refreshForm !== false;
      if (!els.comfyDebugWorkflowList) return;
      els.comfyDebugWorkflowList.innerHTML = '';
      if (!comfyDebugWorkflows.length) {
        els.comfyDebugWorkflowList.innerHTML = '<div class="muted small">暂无调试工作流。</div>';
        return;
      }
      const leaves = [];
      comfyDebugWorkflows.forEach(workflow => {
        const modes = workflowModesForWorkflow(workflow);
        modes.forEach(mode => leaves.push({ workflow, mode }));
      });
      COMFY_DEBUG_CAPABILITY_GROUPS.forEach(group => {
        const groupLeaves = leaves
          .filter(leaf => group.modes.includes(leaf.mode.value))
          .sort((left, right) => group.modes.indexOf(left.mode.value) - group.modes.indexOf(right.mode.value));
        if (!groupLeaves.length) return;
        const isCollapsed = comfyDebugCollapsedCapabilityGroups.has(group.id);
        const groupNode = document.createElement('section');
        groupNode.className = `comfy-debug-tree-group ${isCollapsed ? 'collapsed' : ''}`;
        groupNode.dataset.capability = group.id;
        const groupTitle = document.createElement('button');
        groupTitle.type = 'button';
        groupTitle.className = 'comfy-debug-tree-group-title';
        groupTitle.dataset.comfyCapabilityToggle = group.id;
        groupTitle.setAttribute('aria-expanded', String(!isCollapsed));
        groupTitle.innerHTML = `<span>${escapeHtml(group.label)} <span class="muted">${groupLeaves.length}</span></span><span class="comfy-debug-tree-group-toggle" aria-hidden="true">▼</span>`;
        groupTitle.onclick = () => {
          if (comfyDebugCollapsedCapabilityGroups.has(group.id)) {
            comfyDebugCollapsedCapabilityGroups.delete(group.id);
          } else {
            comfyDebugCollapsedCapabilityGroups.add(group.id);
          }
          renderComfyDebugWorkflows({ refreshForm: false });
          saveSettings();
        };
        groupNode.appendChild(groupTitle);
        const children = document.createElement('div');
        children.className = 'comfy-debug-tree-children';
        children.hidden = isCollapsed;
        groupLeaves.forEach(({ workflow: item, mode }) => {
        const modeConfig = getComfyWorkflowModeConfig(item, mode.value, true) || {};
        const stateKey = comfyDebugLeafKey(item.id, mode.value);
        const runState = comfyDebugStateByWorkflowId.get(stateKey) || {};
        const isConfigured = Boolean(modeConfig.endpoint && modeConfig.nodeInfoList && modeConfig.nodeInfoList !== '[]');
        const isActiveEditor = activeComfyDebugWorkflowId === item.id && activeComfyDebugWorkflowMode === mode.value;
        const card = document.createElement('button');
        card.type = 'button';
        card.className = `comfy-debug-card comfy-debug-tree-leaf ${isActiveEditor ? 'active editing' : ''}`;
        card.dataset.workflowId = item.id;
        card.dataset.workflowMode = mode.value;
        card.dataset.comfyLeafKey = stateKey;
        const head = document.createElement('div');
        head.className = 'comfy-debug-card-head';
        const marker = document.createElement('span');
        marker.className = 'comfy-debug-select-marker';
        marker.textContent = isActiveEditor ? '●' : '○';
        marker.setAttribute('aria-hidden', 'true');
        const titleWrap = document.createElement('div');
        titleWrap.className = 'comfy-debug-card-title';
        titleWrap.innerHTML = `
          <strong>${escapeHtml(mode.label || mode.value)}</strong>
          <span class="muted small">${escapeHtml(item.purpose || '')}</span>
        `;
        head.appendChild(marker);
        head.appendChild(titleWrap);
        const type = document.createElement('span');
        type.className = `comfy-debug-type ${isConfigured ? 'configured' : ''}`;
        type.textContent = `${mode.value === 'i2v_first_last_frame' ? '兼容模式 · ' : ''}${item.type || 'workflow'}${isConfigured ? ' · 已配置' : ''}`;
        card.appendChild(head);
        card.appendChild(type);
        const status = document.createElement('span');
        const statusKind = runState.running ? 'running' : (runState.status === 'completed' ? 'completed' : (runState.error || runState.status === 'failed' ? 'failed' : 'idle'));
        status.className = `comfy-debug-run-state ${statusKind}`;
        if (statusKind === 'running') {
          const elapsed = comfyDebugElapsedLabel(runState);
          status.textContent = `运行中${runState.runId ? ' · ' + String(runState.runId).slice(-8) : ''}${elapsed ? ' · ' + elapsed : ''}`;
        } else if (statusKind === 'completed') {
          const count = Array.isArray(runState.results) ? runState.results.length : 0;
          const elapsed = comfyDebugElapsedLabel(runState);
          status.textContent = `运行完成${count ? ' · ' + count + ' 个结果' : ''}${elapsed ? ' · ' + elapsed : ''}`;
        } else if (statusKind === 'failed') {
          status.textContent = '运行失败';
        } else {
          status.textContent = '未运行';
        }
        card.appendChild(status);
        card.onclick = () => {
          setActiveComfyDebugWorkflow(item.id, true, mode.value);
          renderComfyDebugWorkflows();
          saveSettings();
        };
        children.appendChild(card);
        });
        groupNode.appendChild(children);
        els.comfyDebugWorkflowList.appendChild(groupNode);
      });
      const active = activeComfyDebugWorkflow();
      const activeMode = selectedWorkflowModeDefinition(active);
      const group = COMFY_DEBUG_CAPABILITY_GROUPS.find(entry => entry.id === comfyDebugCapabilityForMode(activeMode?.value));
      if (els.comfyDebugSelectedMeta) els.comfyDebugSelectedMeta.textContent = active ? `当前：${group?.label || '其他'} / ${activeMode?.label || active.name || active.id}` : '单选调试';
      if (active && refreshForm) applyComfyDebugWorkflowDefaults(active, false);
    }

    function applyComfyDebugWorkflowDefaults(item, force = false) {
      if (!item) return;
      const savedConfig = getComfyWorkflowLibraryItemById(item.id);
      const modeConfig = getComfyWorkflowModeConfig(item, activeComfyDebugWorkflowMode, true) || savedConfig || {};
      const endpoint = modeConfig.endpoint || item.default_endpoint || '';
      const nodeInfo = modeConfig.nodeInfoList || item.default_node_info || '[]';
      const width = modeConfig.defaultWidth || item.default_width || '';
      const height = modeConfig.defaultHeight || item.default_height || '';
      const duration = modeConfig.defaultDuration || item.default_duration || '';
      const fps = modeConfig.defaultFps || item.default_fps || '';
      const pollTimeout = modeConfig.pollTimeout || item.poll_timeout_seconds || item.default_poll_timeout || '3600';
      if (force || !els.comfyDebugWidth.value.trim()) els.comfyDebugWidth.value = width;
      if (force || !els.comfyDebugHeight.value.trim()) els.comfyDebugHeight.value = height;
      if (force || !els.comfyDebugDuration.value.trim()) els.comfyDebugDuration.value = duration;
      if (force || !els.comfyDebugFps.value.trim()) els.comfyDebugFps.value = fps;
      if (force || !els.comfyDebugEndpoint.value.trim()) els.comfyDebugEndpoint.value = endpoint;
      if (force || !els.comfyDebugNodeInfoList.value.trim()) els.comfyDebugNodeInfoList.value = nodeInfo;
      if (force || !els.comfyDebugPollTimeout.value) setIfExists(els.comfyDebugPollTimeout, String(pollTimeout));
      updateComfyDebugMediaFields();
      els.comfyDebugEndpoint.placeholder = endpoint || '/run/workflow/xxx 或 /run/ai-app/xxx';
      els.comfyDebugNodeInfoList.placeholder = nodeInfo || '[]';
    }

    function saveActiveComfyDebugWorkflowConfig(showMessage = true) {
      const workflow = activeComfyDebugWorkflow();
      if (!workflow) {
        setStatus('请先在左侧选择一个调试工作流', true);
        return false;
      }
      ensureComfyDebugWorkflowsInLibrary();
      let item = getComfyWorkflowLibraryItemById(workflow.id);
      if (!item) {
        item = {
          id: workflow.id,
          name: workflow.name || workflow.id,
          purpose: workflow.purpose || '',
          materialTypes: workflow.type ? [workflow.type] : [],
          endpoint: '',
          nodeInfoList: '[]',
          pollTimeout: '3600',
          defaultReference: '',
          defaultMiddleFrameReference: '',
          defaultLastFrameReference: '',
          defaultSeed: '',
          defaultDuration: '',
          defaultFps: '',
          defaultPrompt: '',
          defaultNegative: '',
          defaultAssetReference: '',
          defaultMiddleFrameAssetReference: '',
          defaultLastFrameAssetReference: '',
          defaultReferenceHint: '',
          defaultMiddleFrameReferenceHint: '',
          defaultLastFrameReferenceHint: '',
          debugWorkflow: true,
        };
        comfyWorkflowLibrary.push(item);
      }
      item.name = workflow.name || item.name || workflow.id;
      item.purpose = workflow.purpose || item.purpose || '';
      item.materialTypes = workflow.type ? [workflow.type] : item.materialTypes || [];
      const mode = activeComfyDebugWorkflowMode || els.comfyDebugWorkflowMode?.value || workflow.modes?.[0]?.value || '';
      if (!item.modeConfigs || typeof item.modeConfigs !== 'object') item.modeConfigs = {};
      const modeConfig = item.modeConfigs[mode] || normalizeComfyModeConfig({}, item);
      modeConfig.endpoint = els.comfyDebugEndpoint.value.trim();
      modeConfig.nodeInfoList = sanitizeComfyVisualNodeInfoList(els.comfyDebugNodeInfoList.value.trim() || '[]');
      modeConfig.pollTimeout = els.comfyDebugPollTimeout.value || '3600';
      modeConfig.defaultWidth = els.comfyDebugWidth.value.trim();
      modeConfig.defaultHeight = els.comfyDebugHeight.value.trim();
      modeConfig.defaultReference = els.comfyDebugReference.value.trim();
      modeConfig.defaultMiddleFrameReference = els.comfyDebugMiddleFrameReference?.value.trim() || '';
      modeConfig.defaultLastFrameReference = els.comfyDebugLastFrameReference?.value.trim() || '';
      modeConfig.defaultSeed = els.comfyDebugSeed.value.trim();
      modeConfig.defaultDuration = workflow.type === 'video' ? els.comfyDebugDuration.value.trim() : '';
      modeConfig.defaultFps = workflow.type === 'video' ? els.comfyDebugFps.value.trim() : '';
      item.defaultWorkflowMode = mode;
      item.defaultImageTaskType = workflow.default_image_task_type || item.defaultImageTaskType || workflow.default_task_type || '';
      modeConfig.defaultPrompt = els.comfyDebugPrompt.value;
      modeConfig.defaultNegative = els.comfyDebugNegative.value;
      modeConfig.defaultAssetReference = els.comfyDebugAssetReference.value || '';
      modeConfig.defaultMiddleFrameAssetReference = els.comfyDebugMiddleFrameAssetReference?.value || '';
      modeConfig.defaultLastFrameAssetReference = els.comfyDebugLastFrameAssetReference?.value || '';
      modeConfig.defaultReferenceHint = els.comfyDebugReferenceHint.textContent || '';
      modeConfig.defaultMiddleFrameReferenceHint = els.comfyDebugMiddleFrameReferenceHint?.textContent || '';
      modeConfig.defaultLastFrameReferenceHint = els.comfyDebugLastFrameReferenceHint?.textContent || '';
      item.modeConfigs[mode] = modeConfig;
      item.debugWorkflow = true;
      els.comfyDebugNodeInfoList.value = modeConfig.nodeInfoList;
      const stateKey = comfyDebugLeafKey(workflow.id, mode);
      const previousState = comfyDebugStateByWorkflowId.get(stateKey) || {};
      comfyDebugStateByWorkflowId.set(stateKey, {
        ...previousState,
        ...readComfyDebugFormState(),
        results: Array.isArray(previousState.results) ? previousState.results : [],
      });
      saveSettings();
      if (showMessage) {
        renderComfyDebugWorkflows();
        renderComfyWorkflowLibrary();
        setStatus(`已保存调试工作流配置：${item.name}`);
      }
      return true;
    }

    function autoSaveActiveComfyDebugWorkflowConfig() {
      if (!activeComfyDebugWorkflow()) return;
      saveActiveComfyDebugWorkflowConfig(false);
    }

    async function runComfyDebug() {
      const selected = activeComfyDebugWorkflow();
      if (!selected) {
        setStatus('请选择一个 ComfyUI 调试工作流', true);
        return;
      }
      const prompt = els.comfyDebugPrompt.value.trim();
      if (!prompt) {
        setStatus('请输入调试提示词', true);
        return;
      }
      const workflowModeDef = selectedWorkflowModeDefinition(selected);
      const imageTaskDef = workflowModeDef ? {
        value: workflowModeDef.value,
        label: workflowModeDef.label,
        taskType: workflowModeDef.task_type,
        controlMode: workflowModeDef.control_mode,
        requiresReference: Boolean(workflowModeDef.requires_reference),
        assetTag: workflowModeDef.asset_tag || selected.asset_tag || selected.id,
      } : imageTaskDefinitionForWorkflow(selected);
      const referenceValue = els.comfyDebugReference.value.trim();
      const lastFrameValue = els.comfyDebugLastFrameReference?.value.trim() || '';
      const referenceSupport = comfyDebugReferenceSupport();
      const submitReferenceValue = referenceSupport.hasReference ? referenceValue : '';
      const submitLastFrameValue = referenceSupport.hasLastFrame ? lastFrameValue : '';
      if (referenceSupport.hasReference && imageTaskDef.requiresReference && !referenceValue) {
        setStatus(imageTaskDef.label + ' 需要先选择或上传参考图', true);
        return;
      }
      if (referenceSupport.hasLastFrame && (!submitReferenceValue || !submitLastFrameValue)) {
        setStatus('首尾帧视频需要同时选择首帧和尾帧两张图', true);
        return;
      }
      saveActiveComfyDebugWorkflowConfig(false);
      els.runComfyDebugBtn.disabled = true;
      els.runComfyDebugBtn.classList.add('run-progress');
      els.runComfyDebugBtn.style.setProperty('--run-progress', '35%');
      els.runComfyDebugBtn.textContent = '运行中...';
      els.comfyDebugStatus.textContent = `调试中：${selected.name || selected.id}`;
      els.comfyDebugStatus.classList.remove('error');
      if (els.comfyDebugResultMeta) els.comfyDebugResultMeta.textContent = '已提交请求，等待结果...';
      renderComfyDebugRunning(selected);
      setStatus(`ComfyUI 调试已开始：${selected.name || selected.id}`, false);
      try {
        const data = await api('/api/comfy-debug-run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            workflows: [selected.id],
            api_key: els.comfyDebugApiKey.value.trim() || els.comfyApiKey.value.trim(),
            base_url: els.comfyDebugBaseUrl.value.trim() || els.comfyBaseUrl.value.trim(),
            endpoint: els.comfyDebugEndpoint.value.trim(),
            node_info_list_json: els.comfyDebugNodeInfoList.value.trim(),
            workflow_library: getComfyWorkflowLibraryPayload(),
            poll_timeout_seconds: Number(els.comfyDebugPollTimeout.value || 3600),
            prompt,
            negative_prompt: els.comfyDebugNegative.value.trim(),
            reference_image: submitReferenceValue,
            last_frame_image: submitLastFrameValue,
            task_type: imageTaskDef.taskType || '',
            control_mode: imageTaskDef.controlMode || '',
            image_task_mode: selected.type === 'image' ? imageTaskDef.value : '',
            video_task_mode: selected.type === 'video' ? imageTaskDef.value : '',
            workflow_mode: workflowModeDef?.value || '',
            asset_tag: imageTaskDef.assetTag || selected.asset_tag || selected.id,
            seed: els.comfyDebugSeed.value.trim(),
            width: els.comfyDebugWidth.value.trim(),
            height: els.comfyDebugHeight.value.trim(),
            duration: selected.type === 'video' ? els.comfyDebugDuration.value.trim() : '',
            fps: selected.type === 'video' ? els.comfyDebugFps.value.trim() : '',
            frame_count: selected.type === 'video' ? computedComfyDebugFrameCount() : '',
          }),
        });
        comfyDebugLastResults = Array.isArray(data.results) ? data.results : [];
      const stateKey = activeComfyDebugStateKey();
      const currentState = comfyDebugStateByWorkflowId.get(stateKey) || readComfyDebugFormState();
      comfyDebugStateByWorkflowId.set(stateKey, {
        ...currentState,
        ...readComfyDebugFormState(),
        results: compactComfyDebugResults(comfyDebugLastResults),
      });
      saveSettings();
        renderComfyDebugResults(comfyDebugLastResults, data);
        els.comfyDebugStatus.textContent = `调试完成：${comfyDebugLastResults.length} 个结果`;
        els.runComfyDebugBtn.style.setProperty('--run-progress', '100%');
        setStatus('ComfyUI 调试完成', false);
      } catch (err) {
        els.comfyDebugStatus.textContent = err.message;
        els.comfyDebugStatus.classList.add('error');
        if (els.comfyDebugResultMeta) els.comfyDebugResultMeta.textContent = '运行失败';
        if (els.comfyDebugResults) {
          els.comfyDebugResults.innerHTML = `
            <div class="comfy-debug-result">
              <div class="comfy-debug-result-head">
                <strong>运行失败</strong>
                <span class="comfy-debug-type">error</span>
              </div>
              <div class="comfy-debug-log">${escapeHtml(err.message || '未知错误')}</div>
            </div>
          `;
        }
        setStatus(err.message, true);
      } finally {
        els.runComfyDebugBtn.disabled = false;
        setTimeout(() => {
          els.runComfyDebugBtn.classList.remove('run-progress');
          els.runComfyDebugBtn.style.removeProperty('--run-progress');
          els.runComfyDebugBtn.textContent = '运行当前工作流';
        }, 500);
      }
    }

    function renderComfyDebugRunning(workflow) {
      if (!els.comfyDebugResults) return;
      const endpoint = els.comfyDebugEndpoint.value.trim() || '使用当前工作流保存的接口地址';
      els.comfyDebugResults.innerHTML = `
        <div class="comfy-debug-running">
          <div class="comfy-debug-running-bar"></div>
          <strong>正在调用：${escapeHtml(workflow?.name || workflow?.id || '当前工作流')}</strong>
          <div class="muted small">${escapeHtml(endpoint)}</div>
          <div class="muted small">已提交请求，正在等待 RunningHub / ComfyUI 返回结果。期间不要重复点击运行。</div>
        </div>
      `;
      if (els.comfyDebugResultMeta) els.comfyDebugResultMeta.textContent = '运行中，等待结果返回...';
    }

    function renderComfyDebugResults(results, runMeta = null) {
      if (!els.comfyDebugResults) return;
      els.comfyDebugResults.innerHTML = '';
      if (!results.length) {
        els.comfyDebugResults.innerHTML = '<div class="muted small">暂无调试结果。</div>';
        if (els.comfyDebugResultMeta) els.comfyDebugResultMeta.textContent = '暂无结果';
        return;
      }
      let mediaCount = 0;
      results.forEach(result => {
        const card = document.createElement('div');
        card.className = 'comfy-debug-result';
        const files = Array.isArray(result.files) ? result.files : [];
        mediaCount += files.length;
        card.innerHTML = `
          <div class="comfy-debug-result-head">
            <div>
              <strong>${escapeHtml(result.name || result.id || '调试结果')}</strong>
              <div class="muted small">${escapeHtml(result.status || '')} · ${escapeHtml(result.endpoint || '')}</div>
            </div>
            <span class="comfy-debug-type">${escapeHtml(result.type || '')}</span>
          </div>
        `;
        const gallery = document.createElement('div');
        gallery.className = 'asset-gallery';
        if (files.length) {
          const previewItems = files.map(file => ({
            file,
            label: file.split('/').pop(),
            kind: isImageFile(file) ? 'image' : 'video',
            tags: [result.asset_tag || result.id || 'comfy_debug', isImageFile(file) ? 'image' : 'video'],
          }));
          files.forEach((file, index) => {
            const item = previewItems[index];
            const taskName = result.task || '__comfy_debug__';
            const card = assetGalleryCard(taskName, item, index, previewItems);
            card.onclick = () => {
              openAssetLightboxFromItems(taskName, previewItems, index);
            };
            card.onkeydown = event => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                openAssetLightboxFromItems(taskName, previewItems, index);
              }
            };
            gallery.appendChild(card);
          });
        } else {
          gallery.innerHTML = '<div class="muted small">没有下载到可预览媒体。请检查 RunningHub 返回、节点输出和 nodeInfoList。</div>';
        }
        const log = document.createElement('div');
        log.className = 'comfy-debug-log';
        log.textContent = result.error || JSON.stringify(result.manifest || {}, null, 2).slice(0, 3000);
        card.appendChild(gallery);
        const libraryAssets = Array.isArray(result.library_assets) ? result.library_assets : [];
        if (libraryAssets.length) {
          const libraryMeta = document.createElement('div');
          libraryMeta.className = 'muted small';
          const okCount = libraryAssets.filter(item => item && !item.error).length;
          const errorCount = libraryAssets.length - okCount;
          libraryMeta.textContent = errorCount
            ? `素材库：已入库 ${okCount} 个，${errorCount} 个失败`
            : `素材库：已自动入库 ${okCount} 个`;
          card.appendChild(libraryMeta);
        }
        card.appendChild(log);
        els.comfyDebugResults.appendChild(card);
      });
      const elapsed = comfyDebugElapsedLabel(runMeta || {});
      if (els.comfyDebugResultMeta) els.comfyDebugResultMeta.textContent = `${results.length} 个工作流 · ${mediaCount} 个媒体${elapsed ? ' · ' + elapsed : ''}`;
    }

    async function runWorkflow() {
      setIfExists(els.productTemplate, 'long_video');
      setIfExists(els.workflow, LONG_VIDEO_WORKFLOW_STEM);
      await loadAssetLibrary();
      const rawInput = els.userInput.value.trim();
      const input = `${rawInput}${assetLibraryContextText()}`.trim();
      if (!input) {
        setStatus('请输入原始需求', true);
        return;
      }
      const model = els.model.value === 'custom' ? els.customModel.value.trim() : els.model.value;
      if (els.model.value === 'custom' && !model) {
        setStatus('请输入自定义模型名', true);
        return;
      }
      const titleFromInput = (rawInput || input).replace(/\s+/g, '').slice(0, 18);
      els.taskTitle.value = els.taskTitle.value.trim() || (titleFromInput ? `${titleFromInput}长视频` : '长视频任务');
      els.runBtn.disabled = true;
      saveSettings();
      resetProgress();
      autoFocusOutputDuringRun = true;
      setWorkflowInteractionLocked(true);
      showStartupProgress('启动中');
      setStatus('工作流运行中');
      prepareOutputForPendingRun(els.taskTitle.value.trim() || '正在创建长视频任务');
      showView('output');
      try {
        await ensureLocalModelReady(model);
        const referenceImages = await uploadReferenceImages();
        const { imageConfig, videoConfig, productionConfig } = await collectProductionConfig();
        const result = await api('/api/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            workflow: els.workflow.value,
            task_title: els.taskTitle.value.trim(),
            input,
            provider: els.provider.value,
            model,
            api_key: els.apiKey.value.trim(),
            base_url: els.baseUrl.value.trim(),
            timeout: Number(els.modelTimeout.value || 900),
            memory_scope: els.useMemory.value,
            use_knowledge: els.useKnowledge.value === 'on',
            inherit_task: els.inheritTask.value,
            inherit_mode: els.inheritMode.value,
            production_config: productionConfig,
            image_config: imageConfig,
            video_config: videoConfig,
            reference_images: referenceImages,
            image_api_key: '',
            image_base_url: '',
            video_api_key: '',
            video_base_url: '',
            comfy_api_key: els.comfyApiKey.value.trim(),
            comfy_base_url: els.comfyBaseUrl.value.trim(),
          }),
        });
        setStatus('工作流已开始，正在执行第 1 步');
        trackRun(result.run_id);
        renderProgress(result);
        showView('output');
        await selectActiveRunTask(result);
        await pollRunStatus(result.run_id);
      } catch (err) {
        setStatus(err.message, true);
        els.runBtn.disabled = false;
        setWorkflowInteractionLocked(false);
        setRunButtonProgress(0);
      } finally {
      }
    }

    els.runBtn.onclick = runWorkflow;
    els.cancelRunBtn.onclick = cancelCurrentRun;
    els.outputCancelRunBtn.onclick = cancelCurrentRun;
    els.assetLightboxCloseBtn.onclick = event => {
      event.preventDefault();
      event.stopPropagation();
      closeAssetLightbox();
    };
    els.assetLightboxPrevBtn.onclick = event => {
      event.preventDefault();
      event.stopPropagation();
      moveAssetLightbox(-1);
    };
    els.assetLightboxNextBtn.onclick = event => {
      event.preventDefault();
      event.stopPropagation();
      moveAssetLightbox(1);
    };
    els.assetLightbox.onclick = handleAssetLightboxBackgroundClick;
    document.addEventListener('keydown', event => {
      if (!els.assetLightbox || els.assetLightbox.hidden) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        closeAssetLightbox();
      }
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        moveAssetLightbox(-1);
      }
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        moveAssetLightbox(1);
      }
    }, true);
    window.addEventListener('resize', () => {
      if (!els.assetLightbox || els.assetLightbox.hidden) return;
      const media = els.assetLightboxStage?.querySelector('img, video');
      if (media) fitAssetLightboxMedia(media);
    });
    window.addEventListener('pagehide', pauseCurrentRunOnExit);
    window.addEventListener('beforeunload', pauseCurrentRunOnExit);
    els.comfyWorkflowPreset.onchange = () => {
      loadSelectedComfyWorkflowPreset(true);
    };
    els.applyComfyWorkflowPresetBtn.onclick = applySelectedComfyWorkflowPreset;
    els.saveComfyWorkflowPresetBtn.onclick = saveSelectedComfyWorkflowPreset;
    els.resetComfyWorkflowPresetBtn.onclick = resetSelectedComfyWorkflowPreset;
    navButtons.forEach(button => {
      button.onclick = () => {
        if (document.body.dataset.view === 'assets' && button.dataset.viewTarget !== 'assets' && !confirmDiscardAssetLibraryDetailChanges()) {
          return;
        }
        autoFocusOutputDuringRun = false;
        showView(button.dataset.viewTarget);
      };
    });
    els.refreshTasks.onclick = loadTasks;
    els.saveFileBtn.onclick = saveCurrentFile;
    els.rebuildFinalBtn.onclick = rebuildFinalOutput;
    els.rerunStepBtn.onclick = rerunCurrentStep;
    els.resumeTaskBtn.onclick = resumeSelectedTask;
    els.confirmStepContinueBtn.onclick = () => resumeSelectedTask({ skipConfirm: true });
    els.confirmStepRerunBtn.onclick = rerunCurrentStep;
    els.exportTaskBtn.onclick = exportCurrentTask;
    els.refreshStaffBtn.onclick = loadStaffList;
    els.staffFilter.oninput = () => loadStaffList().catch(err => setStaffStatus(err.message, true));
    els.showArchivedStaff.onchange = () => loadStaffList().catch(err => setStaffStatus(err.message, true));
    els.newStaffBtn.onclick = newStaff;
    els.saveStaffBtn.onclick = saveStaff;
    els.deleteStaffBtn.onclick = deleteStaff;
    els.refreshWorkflowsBtn.onclick = loadWorkflowList;
    els.showArchivedWorkflows.onchange = () => loadWorkflowList().catch(err => setWorkflowEditorStatus(err.message, true));
    els.newWorkflowBtn.onclick = newWorkflow;
    els.addWorkflowStepBtn.onclick = addWorkflowStep;
    els.saveWorkflowBtn.onclick = saveWorkflow;
    els.deleteWorkflowBtn.onclick = deleteWorkflow;
    els.localModelPreset.onchange = applyLocalModelPreset;
    els.localModelName.onchange = applyLocalModelName;
    els.imageTool.onchange = () => {
      applyImageProviderDefaults();
      saveSettings();
    };
    els.videoTool.onchange = () => {
      applyVideoProviderDefaults();
      saveSettings();
    };
    els.composeTool.onchange = () => {
      applyComfyProviderDefaults();
      saveSettings();
    };
    els.workflowAdvanceMode.onchange = () => {
      saveSettings();
      renderStepConfirmBar();
      syncOutputButtons();
    };
    els.comfyDebugGate.onchange = saveSettings;
    els.showDebugFiles.onchange = () => {
      renderFiles(currentTaskFiles);
    };
    function activeComfyDebugState() {
      const workflow = activeComfyDebugWorkflow();
      return workflow ? comfyDebugStateByWorkflowId.get(activeComfyDebugStateKey()) : null;
    }

    function syncComfyDebugRunButton() {
      if (!els.runComfyDebugBtn) return;
      const state = activeComfyDebugState();
      const running = Boolean(state?.running);
      els.runComfyDebugBtn.disabled = running;
      if (running) {
        els.runComfyDebugBtn.classList.add('run-progress');
        els.runComfyDebugBtn.style.setProperty('--run-progress', state?.status === 'running' ? '65%' : '35%');
        els.runComfyDebugBtn.textContent = '运行中...';
      } else {
        els.runComfyDebugBtn.classList.remove('run-progress');
        els.runComfyDebugBtn.style.removeProperty('--run-progress');
        els.runComfyDebugBtn.textContent = '运行当前工作流';
      }
    }

    function renderComfyDebugStatePreview(workflow) {
      const state = workflow ? comfyDebugStateByWorkflowId.get(activeComfyDebugStateKey()) : null;
      if (state?.running) {
        renderComfyDebugRunningAsync(workflow, state);
      } else if (state?.error) {
        renderComfyDebugError(state.error);
      } else {
        renderComfyDebugResults(state?.results || []);
      }
    }

    function renderComfyDebugError(message) {
      if (els.comfyDebugStatus) {
        els.comfyDebugStatus.textContent = message || '运行失败';
        els.comfyDebugStatus.classList.add('error');
      }
      if (els.comfyDebugResultMeta) els.comfyDebugResultMeta.textContent = '运行失败';
      if (els.comfyDebugResults) {
        els.comfyDebugResults.innerHTML = `
          <div class="comfy-debug-result">
            <div class="comfy-debug-result-head">
              <strong>运行失败</strong>
              <span class="comfy-debug-type">error</span>
            </div>
            <div class="comfy-debug-log">${escapeHtml(message || '未知错误')}</div>
          </div>
        `;
      }
    }

    function renderComfyDebugRunningAsync(workflow, state = null) {
      if (!els.comfyDebugResults) return;
      const endpoint = state?.endpoint || els.comfyDebugEndpoint.value.trim() || '使用当前工作流保存的接口地址';
      const runId = state?.runId ? `任务：${state.runId}` : '正在提交任务';
      const statusText = state?.status || 'running';
      const elapsedText = comfyDebugElapsedLabel(state);
      els.comfyDebugResults.innerHTML = `
        <div class="comfy-debug-running">
          <div class="comfy-debug-running-bar"></div>
          <strong>正在调用：${escapeHtml(workflow?.name || workflow?.id || '当前工作流')}</strong>
          <div class="muted small">${escapeHtml(endpoint)}</div>
          <div class="muted small">${escapeHtml(runId)} · ${escapeHtml(statusText)}${elapsedText ? ' · ' + escapeHtml(elapsedText) : ''}</div>
          <div class="muted small">已转为后台任务，页面会自动轮询结果。你可以切换左侧其他工作流继续调试。</div>
        </div>
      `;
      if (els.comfyDebugResultMeta) els.comfyDebugResultMeta.textContent = `运行中，等待结果返回${elapsedText ? ' · ' + elapsedText : '...'}`;
    }

    function comfyDebugModePayload(selected, overrides = {}) {
      if (!selected) throw new Error('请选择一个 ComfyUI 调试工作流');
      const prompt = String(overrides.prompt ?? els.comfyDebugPrompt.value).trim();
      if (!prompt) throw new Error('请输入调试提示词');
      const workflowModeDef = selectedWorkflowModeDefinition(selected);
      const imageTaskDef = workflowModeDef ? {
        value: workflowModeDef.value,
        label: workflowModeDef.label,
        taskType: workflowModeDef.task_type,
        controlMode: workflowModeDef.control_mode,
        requiresReference: Boolean(workflowModeDef.requires_reference),
        assetTag: workflowModeDef.asset_tag || selected.asset_tag || selected.id,
      } : imageTaskDefinitionForWorkflow(selected);
      const referenceSupport = comfyDebugReferenceSupport();
      const referenceValue = String(overrides.reference_image ?? els.comfyDebugReference.value).trim();
      const middleFrameValue = String(overrides.middle_frame_image ?? (els.comfyDebugMiddleFrameReference?.value || '')).trim();
      const lastFrameValue = String(overrides.last_frame_image ?? (els.comfyDebugLastFrameReference?.value || '')).trim();
      const submitReferenceValue = referenceSupport.hasReference ? referenceValue : '';
      const submitMiddleFrameValue = referenceSupport.hasMiddleFrame ? middleFrameValue : '';
      const submitLastFrameValue = referenceSupport.hasLastFrame ? lastFrameValue : '';
      if (referenceSupport.hasReference && imageTaskDef.requiresReference && !submitReferenceValue) {
        throw new Error(`${imageTaskDef.label || '当前模式'} 需要参考图`);
      }
      if (referenceSupport.hasMiddleFrame && (!submitReferenceValue || !submitMiddleFrameValue || !submitLastFrameValue)) {
        throw new Error('首中尾帧模式必须同时提供首帧、中帧和尾帧');
      }
      if (!referenceSupport.hasMiddleFrame && referenceSupport.hasLastFrame && (!submitReferenceValue || !submitLastFrameValue)) {
        throw new Error('首尾帧模式必须同时提供首帧和尾帧');
      }
      const requiredInputs = Array.isArray(workflowModeDef?.required_inputs) ? workflowModeDef.required_inputs : [];
      const semanticValues = {
        input_base_image: submitReferenceValue,
        input_middle_frame: submitMiddleFrameValue,
        input_last_frame: submitLastFrameValue,
        input_mask_image: els.comfyDebugMaskImage?.value.trim() || '',
        input_audio_file: els.comfyDebugAudioFile?.value.trim() || '',
      };
      const missingInputs = requiredInputs.filter(slot => !semanticValues[slot]);
      if (missingInputs.length) throw new Error(`当前子模式缺少必填输入：${missingInputs.join(', ')}`);
      validateComfyDebugSemanticContract(requiredInputs, els.comfyDebugEndpoint.value.trim(), els.comfyDebugNodeInfoList.value.trim());
      return {
        workflows: [selected.id],
        api_key: els.comfyDebugApiKey.value.trim() || els.comfyApiKey.value.trim(),
        base_url: els.comfyDebugBaseUrl.value.trim() || els.comfyBaseUrl.value.trim(),
        endpoint: els.comfyDebugEndpoint.value.trim(),
        node_info_list_json: els.comfyDebugNodeInfoList.value.trim(),
        workflow_library: getComfyWorkflowLibraryPayload(),
        poll_timeout_seconds: Number(els.comfyDebugPollTimeout.value || 3600),
        prompt,
        negative_prompt: els.comfyDebugNegative.value.trim(),
        reference_image: submitReferenceValue,
        middle_frame_image: submitMiddleFrameValue,
        last_frame_image: submitLastFrameValue,
        input_base_image: submitReferenceValue,
        input_middle_frame: submitMiddleFrameValue,
        input_last_frame: submitLastFrameValue,
        input_mask_image: els.comfyDebugMaskImage?.value.trim() || '',
        input_audio_file: els.comfyDebugAudioFile?.value.trim() || '',
        task_type: imageTaskDef.taskType || '',
        control_mode: imageTaskDef.controlMode || '',
        image_task_mode: selected.type === 'image' ? imageTaskDef.value : '',
        video_task_mode: selected.type === 'video' ? imageTaskDef.value : '',
        workflow_mode: workflowModeDef?.value || '',
        asset_tag: imageTaskDef.assetTag || selected.asset_tag || selected.id,
        seed: String(overrides.seed ?? els.comfyDebugSeed.value).trim(),
        width: els.comfyDebugWidth.value.trim(),
        height: els.comfyDebugHeight.value.trim(),
        duration: selected.type === 'video' ? els.comfyDebugDuration.value.trim() : '',
        fps: selected.type === 'video' ? els.comfyDebugFps.value.trim() : '',
        frame_count: selected.type === 'video' ? computedComfyDebugFrameCount() : '',
      };
    }

    function validateComfyDebugSemanticContract(requiredInputs, endpoint, nodeInfoText) {
      if (!String(endpoint || '').startsWith('/run/')) return;
      const text = String(nodeInfoText || '').trim();
      if (!text || text === '[]') throw new Error('RunningHub 子模式尚未配置 nodeInfoList');
      const aliases = {
        input_base_image: ['{{input_base_image}}', '{{reference_image}}'],
        input_middle_frame: ['{{input_middle_frame}}', '{{middle_frame_image}}'],
        input_last_frame: ['{{input_last_frame}}', '{{last_frame_image}}'],
        input_mask_image: ['{{input_mask_image}}', '{{mask_image}}'],
        input_reference_style: ['{{input_reference_style}}', '{{reference_style}}'],
        input_audio_file: ['{{input_audio_file}}', '{{audio_file}}'],
      };
      const missingMappings = requiredInputs.filter(slot => !(aliases[slot] || [`{{${slot}}}`]).some(token => text.includes(token)));
      if (missingMappings.length) throw new Error(`nodeInfoList 缺少语义槽位映射：${missingMappings.join(', ')}`);
    }

    function startComfyDebugWorkflowRunState(selected, statusText) {
      const stateKey = activeComfyDebugStateKey();
      const existingState = comfyDebugStateByWorkflowId.get(stateKey) || {};
      const startedAt = Date.now() / 1000;
      const startState = {
        ...existingState,
        ...readComfyDebugFormState(),
        running: true,
        runId: '',
        status: 'starting',
        startedAt,
        finishedAt: 0,
        elapsedSeconds: 0,
        error: '',
      };
      comfyDebugStateByWorkflowId.set(stateKey, startState);
      ensureComfyDebugElapsedTimer();
      syncComfyDebugRunButton();
      renderComfyDebugWorkflows({ refreshForm: false });
      if (els.comfyDebugStatus) {
        els.comfyDebugStatus.textContent = statusText || `调试中：${selected.name || selected.id}`;
        els.comfyDebugStatus.classList.remove('error');
      }
      renderComfyDebugRunningAsync(selected, startState);
      return startState;
    }

    function pollComfyDebugRun(stateKey, runId) {
      if (!stateKey || !runId) {
        markComfyDebugRunFailed(stateKey, '后端没有返回运行任务 ID');
        return;
      }
      if (comfyDebugPollTimers.has(stateKey)) {
        clearTimeout(comfyDebugPollTimers.get(stateKey));
        comfyDebugPollTimers.delete(stateKey);
      }
      const tick = async () => {
        try {
          const job = await api(`/api/run-status?id=${encodeURIComponent(runId)}`);
          const state = comfyDebugStateByWorkflowId.get(stateKey) || {};
          if (state.runId && state.runId !== runId) return;
          const status = job.status || '';
          const timing = comfyDebugTimingFromJob(job, state);
          comfyDebugStateByWorkflowId.set(stateKey, {
            ...state,
            ...timing,
            running: !['completed', 'failed', 'cancelled', 'paused'].includes(status),
            status,
            error: status === 'failed' ? (job.error || '运行失败') : '',
          });
          renderComfyDebugWorkflows({ refreshForm: false });
          if (activeComfyDebugStateKey() === stateKey) {
            renderComfyDebugStatePreview(activeComfyDebugWorkflow());
            syncComfyDebugRunButton();
          }
          if (status === 'completed') {
            const result = job.result || {};
            const results = Array.isArray(result.results) ? result.results : (Array.isArray(job.results) ? job.results : []);
            comfyDebugLastResults = results;
            const finalState = comfyDebugStateByWorkflowId.get(stateKey) || {};
            const finalTiming = comfyDebugTimingFromJob(job, finalState);
            comfyDebugStateByWorkflowId.set(stateKey, {
              ...finalState,
              ...finalTiming,
              running: false,
              status,
              error: '',
              results: compactComfyDebugResults(results),
            });
            comfyDebugPollTimers.delete(stateKey);
            await loadAssetLibrary().catch(() => {});
            saveSettings();
            renderComfyDebugWorkflows({ refreshForm: false });
            if (activeComfyDebugStateKey() === stateKey) {
              renderComfyDebugResults(results, comfyDebugStateByWorkflowId.get(stateKey));
              if (els.comfyDebugStatus) {
                const elapsed = comfyDebugElapsedLabel(comfyDebugStateByWorkflowId.get(stateKey));
                els.comfyDebugStatus.textContent = `调试完成：${results.length} 个结果${elapsed ? ' · ' + elapsed : ''}`;
                els.comfyDebugStatus.classList.remove('error');
              }
              setStatus('ComfyUI 调试完成', false);
              syncComfyDebugRunButton();
            }
            return;
          }
          if (['failed', 'cancelled', 'paused'].includes(status)) {
            const message = job.error || job.message || `运行结束：${status}`;
            markComfyDebugRunFailed(stateKey, message);
            comfyDebugPollTimers.delete(stateKey);
            return;
          }
          comfyDebugPollTimers.set(stateKey, setTimeout(tick, 1800));
        } catch (err) {
          markComfyDebugRunFailed(stateKey, err.message || '轮询运行状态失败');
          comfyDebugPollTimers.delete(stateKey);
        }
      };
      comfyDebugPollTimers.set(stateKey, setTimeout(tick, 800));
    }

    function resumeComfyDebugPolls() {
      for (const [stateKey, state] of comfyDebugStateByWorkflowId.entries()) {
        if (state?.running && state?.runId && !comfyDebugPollTimers.has(stateKey)) {
          ensureComfyDebugElapsedTimer();
          pollComfyDebugRun(stateKey, state.runId);
        }
      }
      syncComfyDebugRunButton();
    }

    function markComfyDebugRunFailed(stateKey, message) {
      const state = comfyDebugStateByWorkflowId.get(stateKey) || {};
      const finishedAt = state.finishedAt || Date.now() / 1000;
      const elapsedSeconds = state.elapsedSeconds || comfyDebugElapsedSeconds({ ...state, finishedAt });
      comfyDebugStateByWorkflowId.set(stateKey, {
        ...state,
        running: false,
        status: 'failed',
        finishedAt,
        elapsedSeconds,
        error: message || '运行失败',
      });
      saveSettings();
      renderComfyDebugWorkflows({ refreshForm: false });
      if (activeComfyDebugStateKey() === stateKey) {
        renderComfyDebugError(message);
        syncComfyDebugRunButton();
      }
      setStatus(message || 'ComfyUI 调试失败', true);
    }

    async function runComfyDebugAsync() {
      const selected = activeComfyDebugWorkflow();
      if (!selected) {
        setStatus('请选择一个 ComfyUI 调试工作流', true);
        return;
      }

      let payload;
      try {
        payload = comfyDebugModePayload(selected);
      } catch (err) {
        const message = err?.message || '调试参数不完整';
        setStatus(message, true);
        if (els.comfyDebugStatus) {
          els.comfyDebugStatus.textContent = message;
          els.comfyDebugStatus.classList.add('error');
        }
        return;
      }

      saveActiveComfyDebugWorkflowConfig(false);
      const runStateKey = activeComfyDebugStateKey();
      const startState = startComfyDebugWorkflowRunState(
        selected,
        `正在提交：${selected.name || selected.id}`,
      );
      setStatus(`ComfyUI 调试已开始：${selected.name || selected.id}`, false);

      try {
        const job = await api('/api/comfy-debug-run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const runId = String(job?.run_id || '').trim();
        if (!runId) throw new Error('后端没有返回调试任务 ID');

        const stateKey = runStateKey;
        const currentState = comfyDebugStateByWorkflowId.get(stateKey) || startState;
        const timing = comfyDebugTimingFromJob(job, currentState);
        comfyDebugStateByWorkflowId.set(stateKey, {
          ...currentState,
          ...timing,
          running: true,
          runId,
          status: job.status || 'queued',
          endpoint: payload.endpoint || '',
          error: '',
        });
        saveSettings();
        renderComfyDebugWorkflows({ refreshForm: false });
        if (activeComfyDebugStateKey() === stateKey) {
          renderComfyDebugStatePreview(selected);
          syncComfyDebugRunButton();
        }
        pollComfyDebugRun(stateKey, runId);
      } catch (err) {
        markComfyDebugRunFailed(runStateKey, err?.message || 'ComfyUI 调试提交失败');
      }
    }

    els.autoProductionMode.onchange = () => {
      if (els.autoProductionMode.value === 'comfy_full') {
        els.composeTool.value = 'ffmpeg';
      }
      applyComfyProviderDefaults();
      saveSettings();
    };
    els.comfyApiWorkflowFile.onchange = analyzeComfyApiWorkflowFile;
    els.localOfflineBtn.onclick = applyLocalOfflineMode;
    els.testModelBtn.onclick = testModelConnection;
    els.uploadKnowledgeBtn.onclick = uploadKnowledgeFile;
    els.refreshHealthBtn.onclick = loadSystemHealth;
    els.productTemplate.onchange = () => applyProductTemplate(false);
    els.refreshAssetLibraryBtn.onclick = loadAssetLibrary;
    if (els.assetLibraryTabs) {
      els.assetLibraryTabs.onclick = event => {
        const button = event.target?.closest?.('[data-asset-section]');
        if (!button) return;
        if (!confirmDiscardAssetLibraryDetailChanges()) return;
        assetLibrarySection = button.dataset.assetSection || 'all';
        selectedAssetLibraryId = '';
        assetLibraryDetailDirty = false;
        if (els.assetLibraryDetail) els.assetLibraryDetail.hidden = true;
        renderAssetLibrary();
      };
    }
    if (els.assetLibraryDetailCloseBtn) els.assetLibraryDetailCloseBtn.onclick = () => closeAssetLibraryDetail();
    if (els.assetLibraryDetailSaveBtn) els.assetLibraryDetailSaveBtn.onclick = saveAssetLibraryDetail;
    if (els.assetLibraryDetailDeleteBtn) els.assetLibraryDetailDeleteBtn.onclick = () => deleteAssetLibraryItem();
    if (els.assetLibraryDetailOpenBtn) {
      els.assetLibraryDetailOpenBtn.onclick = () => {
        const item = assetLibrarySelectedItem();
        if (!item) return;
        const items = assetLibraryFilteredItems().map(asset => ({
          ...asset,
          library: true,
          label: asset.name || assetFileLabel(asset.file),
          kind: asset.kind || (isImageFile(asset.file) ? 'image' : 'video'),
        }));
        const index = Math.max(0, items.findIndex(asset => String(asset.id || '') === String(item.id || '')));
        openAssetLightboxFromItems('', items.length ? items : [item], index);
      };
    }
    [els.assetLibraryDetailName, els.assetLibraryDetailCategory, els.assetLibraryDetailNote].filter(Boolean).forEach(control => {
      control.addEventListener('input', () => setAssetLibraryDetailDirty(true));
      control.addEventListener('change', () => setAssetLibraryDetailDirty(true));
    });
    if (els.assetImportCloseBtn) els.assetImportCloseBtn.onclick = closeAssetImportModal;
    if (els.assetImportSaveBtn) els.assetImportSaveBtn.onclick = importAssetLibraryFile;
    if (els.assetImportComfyBtn) els.assetImportComfyBtn.onclick = goComfyDebugFromAssetLibrary;
    if (els.assetImportModal) {
      els.assetImportModal.onclick = event => {
        if (event.target === els.assetImportModal) closeAssetImportModal();
      };
    }
    document.addEventListener('click', handleAssetLibraryBlankClick);
    els.refreshComfyDebugBtn.onclick = loadComfyDebugWorkflows;
    els.runComfyDebugBtn.onclick = runComfyDebugAsync;
    els.comfyDebugApiWorkflowFile.onchange = analyzeComfyDebugApiWorkflowFile;
    [
      els.comfyDebugEndpoint,
      els.comfyDebugReference,
      els.comfyDebugMaskImage,
      els.comfyDebugAudioFile,
      els.comfyDebugWorkflowMode,
      els.comfyDebugSeed,
      els.comfyDebugWidth,
      els.comfyDebugHeight,
      els.comfyDebugDuration,
      els.comfyDebugFps,
      els.comfyDebugPrompt,
      els.comfyDebugNegative,
      els.comfyDebugNodeInfoList,
      els.comfyDebugPollTimeout,
    ].forEach(control => {
      if (!control) return;
      control.addEventListener('input', () => {
        if (control === els.comfyDebugDuration || control === els.comfyDebugFps) updateComfyDebugFrameCountHint();
        if (control === els.comfyDebugNodeInfoList) updateComfyDebugReferencePreviews();
        saveCurrentComfyDebugUiState();
        saveSettings();
      });
      control.addEventListener('change', () => {
        if (control === els.comfyDebugDuration || control === els.comfyDebugFps) updateComfyDebugFrameCountHint();
        if (control === els.comfyDebugNodeInfoList) updateComfyDebugReferencePreviews();
        saveCurrentComfyDebugUiState();
        saveSettings();
        autoSaveActiveComfyDebugWorkflowConfig();
      });
      control.addEventListener('blur', () => {
        saveCurrentComfyDebugUiState();
        saveSettings();
        autoSaveActiveComfyDebugWorkflowConfig();
      });
    });
    els.comfyDebugAssetReference.onchange = () => {
      const value = els.comfyDebugAssetReference.value;
      if (value && els.comfyDebugReferenceFile) els.comfyDebugReferenceFile.value = '';
      setComfyDebugReference(value, value ? `已选择素材库参考：${value}` : '');
      saveCurrentComfyDebugUiState();
      saveSettings();
      autoSaveActiveComfyDebugWorkflowConfig();
    };
    if (els.comfyDebugMiddleFrameAssetReference) {
      els.comfyDebugMiddleFrameAssetReference.onchange = () => {
        const value = els.comfyDebugMiddleFrameAssetReference.value;
        if (value && els.comfyDebugMiddleFrameReferenceFile) els.comfyDebugMiddleFrameReferenceFile.value = '';
        setComfyDebugMiddleFrameReference(value, value ? `素材库中帧：${value.split('/').pop()}` : '');
        saveCurrentComfyDebugUiState();
        saveSettings();
        autoSaveActiveComfyDebugWorkflowConfig();
      };
    }
    if (els.comfyDebugLastFrameAssetReference) {
      els.comfyDebugLastFrameAssetReference.onchange = () => {
        const value = els.comfyDebugLastFrameAssetReference.value;
        if (value && els.comfyDebugLastFrameReferenceFile) els.comfyDebugLastFrameReferenceFile.value = '';
        setComfyDebugLastFrameReference(value, value ? `已选择尾帧素材：${value.split('/').pop()}` : '');
        saveCurrentComfyDebugUiState();
        saveSettings();
        autoSaveActiveComfyDebugWorkflowConfig();
      };
    }
    if (els.assetLibraryTagFilter) {
      els.assetLibraryTagFilter.onchange = () => {
        renderAssetLibrary();
      };
    }
    if (els.comfyDebugAssetTagFilter) {
      els.comfyDebugAssetTagFilter.onchange = () => {
        if (els.comfyDebugAssetReference) els.comfyDebugAssetReference.value = '';
        if (els.comfyDebugMiddleFrameAssetReference) els.comfyDebugMiddleFrameAssetReference.value = '';
        if (els.comfyDebugLastFrameAssetReference) els.comfyDebugLastFrameAssetReference.value = '';
        renderComfyDebugAssetReferenceOptions();
      };
    }
    if (els.comfyDebugWorkflowMode) {
      els.comfyDebugWorkflowMode.onchange = () => {
        updateComfyImageTaskHint();
        updateComfyDebugReferencePreviews();
        saveCurrentComfyDebugUiState();
        saveSettings();
        autoSaveActiveComfyDebugWorkflowConfig();
      };
    }
    els.comfyDebugReferenceFile.onchange = uploadComfyDebugReferenceFile;
    if (els.comfyDebugMiddleFrameReferenceFile) {
      els.comfyDebugMiddleFrameReferenceFile.onchange = uploadComfyDebugMiddleFrameReferenceFile;
    }
    if (els.comfyDebugLastFrameReferenceFile) {
      els.comfyDebugLastFrameReferenceFile.onchange = uploadComfyDebugLastFrameReferenceFile;
    }
    if (els.comfyDebugReferenceReuploadBtn) {
      els.comfyDebugReferenceReuploadBtn.onclick = () => {
        if (els.comfyDebugReferenceFile) {
          els.comfyDebugReferenceFile.value = '';
          els.comfyDebugReferenceFile.click();
        }
      };
    }
    if (els.comfyDebugMiddleFrameReuploadBtn) {
      els.comfyDebugMiddleFrameReuploadBtn.onclick = () => {
        if (els.comfyDebugMiddleFrameReferenceFile) {
          els.comfyDebugMiddleFrameReferenceFile.value = '';
          els.comfyDebugMiddleFrameReferenceFile.click();
        }
      };
    }
    if (els.comfyDebugLastFrameReuploadBtn) {
      els.comfyDebugLastFrameReuploadBtn.onclick = () => {
        if (els.comfyDebugLastFrameReferenceFile) {
          els.comfyDebugLastFrameReferenceFile.value = '';
          els.comfyDebugLastFrameReferenceFile.click();
        }
      };
    }
    els.clearComfyDebugReferenceBtn.onclick = () => {
      if (els.comfyDebugAssetReference) els.comfyDebugAssetReference.value = '';
      if (els.comfyDebugLastFrameAssetReference) els.comfyDebugLastFrameAssetReference.value = '';
      if (els.comfyDebugReferenceFile) els.comfyDebugReferenceFile.value = '';
      if (els.comfyDebugLastFrameReferenceFile) els.comfyDebugLastFrameReferenceFile.value = '';
      setComfyDebugReference('', '');
      setComfyDebugMiddleFrameReference('', '');
      setComfyDebugLastFrameReference('', '');
      saveCurrentComfyDebugUiState();
      saveSettings();
      autoSaveActiveComfyDebugWorkflowConfig();
    };
    els.model.onchange = () => {
      syncCustomModelState();
      saveSettings();
    };
    els.sampleBtn.onclick = () => {
      fillLongVideoSample();
    };
    els.longVideoSampleBtn.onclick = () => {
      fillLongVideoSample();
    };

    function fillLongVideoSample() {
      els.productTemplate.value = 'long_video';
      applyProductTemplate(true);
      setIfExists(els.workflow, LONG_VIDEO_WORKFLOW_STEM);
      els.userInput.value = '我要做一条 12-18 分钟的长视频，主题是“中小企业如何用 AI 员工工作流平台降低重复劳动”。目标平台是 B 站和视频号，目标观众是中小企业老板、运营负责人和想做 AI 自动化服务的人。视频要专业、清楚、有案例感，结构包括痛点、平台演示、落地步骤、成本和风险、最后引导私信咨询。可用素材包括管理台录屏、工作流输出截图、本人配音和少量 AI 示意图；不要夸大收益，不承诺具体增长结果。';
      els.taskTitle.value = 'AI员工工作流平台长视频';
      els.autoProductionMode.value = 'comfy_full';
      els.workflowAdvanceMode.value = 'auto';
      els.composeTool.value = 'ffmpeg';
      els.finalVideoName.value = 'long_video_final.mp4';
      els.referenceRole.value = '视觉风格参考';
      els.referenceNote.value = '用于统一长视频中的人物形象、平台界面、封面和案例画面风格';
      saveSettings();
    }
    els.gameSampleBtn.onclick = () => {
      fillLongVideoSample();
    };
    els.clearSettingsBtn.onclick = () => {
      if (!confirm('确定清除本浏览器保存的 API Key、Base URL、模型和工作流配置？')) return;
      localStorage.removeItem(SETTINGS_KEY);
      els.productTemplate.value = 'long_video';
      setIfExists(els.workflow, LONG_VIDEO_WORKFLOW_STEM);
      els.provider.value = 'auto';
      els.model.value = 'gpt-5.5';
      els.customModel.value = '';
      els.taskTitle.value = '';
      els.apiKey.value = '';
      els.baseUrl.value = '';
      els.modelTimeout.value = '900';
      els.localModelPreset.value = '';
      renderLocalModelNames();
      els.useMemory.value = 'video_output';
      els.useKnowledge.value = 'off';
      els.inheritTask.value = '';
      els.inheritMode.value = 'final_output';
      els.workflowAdvanceMode.value = 'auto';
      els.autoProductionMode.value = 'off';
      els.composeTool.value = 'ffmpeg';
      els.finalVideoName.value = 'long_video_final.mp4';
      els.comfyApiKey.value = '';
      els.comfyBaseUrl.value = '';
      els.comfyWorkflowEndpoint.value = '';
      els.comfyNodeInfoList.value = '[]';
      els.comfyPollTimeout.value = '3600';
      els.voiceMode.value = 'off';
      els.voicePreset.value = 'warm_female';
      els.voiceReferenceAudioPath.value = '';
      els.voiceReferenceText.value = '';
      els.voiceCommandTemplate.value = defaultVoxCPM2CommandTemplate();
      els.voiceTimeout.value = '3600';
      els.imageTool.value = 'prompt_only';
      els.imagePositivePrompt.value = '';
      els.imageModel.value = '';
      els.imageSize.value = '16:9';
      els.imageCount.value = '1';
      els.imageStyle.value = '';
      els.imageQuality.value = 'standard';
      els.imageApiKey.value = '';
      els.imageBaseUrl.value = '';
      els.imageNegativePrompt.value = '';
      els.imageConsistency.value = '';
      els.videoTool.value = 'prompt_only';
      els.videoPositivePrompt.value = '';
      els.videoModel.value = '';
      els.videoAspect.value = '16:9';
      els.videoDuration.value = 'custom';
      els.videoStyle.value = '';
      els.videoPromptNotes.value = '';
      els.videoApiKey.value = '';
      els.videoBaseUrl.value = '';
      els.referenceRole.value = '视觉风格参考';
      els.referenceNote.value = '';
      clearReferenceFiles();
      syncCustomModelState(false);
      setStatus('已清除本地保存配置');
    };
    els.referenceImages.onchange = () => {
      referencePreviewUrls.forEach(url => URL.revokeObjectURL(url));
      referencePreviewUrls = new Map();
      selectedReferenceFiles = Array.from(els.referenceImages.files || []);
      renderReferenceFiles();
    };
    bindSettingsPersistence();
    bindButtonClickFeedback();
    moveConfigSections();
    document.addEventListener('DOMContentLoaded', moveConfigSections);
    window.addEventListener('load', moveConfigSections);
    renderReferenceFiles();
    renderOutputOverview(null);

    (async function init() {
      try {
        await loadConfig();
        restoreSettings();
        await loadAssetLibrary();
        await loadComfyDebugWorkflows();
        await loadTasks();
        await loadStaffList();
        await loadWorkflowList();
        await loadKnowledgeList();
        await loadSystemHealth();
        await restoreActiveRun();
      } catch (err) {
        setStatus(err.message, true);
      }
    })();
  </script>
</body>
</html>
"""


class WorkflowWebHandler(BaseHTTPRequestHandler):
    server_version = "MyWorkflowWeb/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_html(INDEX_HTML)
            elif parsed.path == "/api/config":
                self._send_json(self._config())
            elif parsed.path == "/api/system-health":
                self._send_json(self._system_health())
            elif parsed.path == "/api/tasks":
                self._send_json({"tasks": self._tasks()})
            elif parsed.path == "/api/asset-library":
                self._send_json({"assets": self._asset_library()})
            elif parsed.path == "/api/comfy-debug-workflows":
                self._send_json({"workflows": self._comfy_debug_workflows()})
            elif parsed.path == "/api/knowledge":
                self._send_json({"files": self._knowledge_files()})
            elif parsed.path == "/api/staff":
                self._send_json({"staff": self._staff_list()})
            elif parsed.path == "/api/staff-detail":
                query = parse_qs(parsed.query)
                self._send_json(self._staff_detail(self._single(query, "name")))
            elif parsed.path == "/api/workflows":
                self._send_json({"workflows": self._workflow_list(), "staff": [item["name"] for item in self._staff_list()]})
            elif parsed.path == "/api/workflow-detail":
                query = parse_qs(parsed.query)
                self._send_json(self._workflow_detail(self._single(query, "name")))
            elif parsed.path == "/api/task":
                query = parse_qs(parsed.query)
                self._send_json(self._task_detail(self._single(query, "name")))
            elif parsed.path == "/api/task-status":
                query = parse_qs(parsed.query)
                detail = self._task_detail(self._single(query, "name"))
                self._send_json({"name": detail["name"], "task_status": detail["task_status"]})
            elif parsed.path == "/api/task-comfy-debug-plan":
                query = parse_qs(parsed.query)
                task_dir = self._safe_task_dir(self._single(query, "name"))
                self._send_json(self._task_comfy_debug_status(task_dir))
            elif parsed.path == "/api/file":
                query = parse_qs(parsed.query)
                self._send_json(self._file_content(self._single(query, "task"), self._single(query, "file")))
            elif parsed.path == "/api/media":
                query = parse_qs(parsed.query)
                self._send_media(self._single(query, "task"), self._single(query, "file"))
            elif parsed.path == "/api/asset-library-media":
                query = parse_qs(parsed.query)
                self._send_asset_library_media(self._single(query, "id"))
            elif parsed.path == "/api/reference-media":
                query = parse_qs(parsed.query)
                self._send_reference_media(self._single(query, "file"))
            elif parsed.path == "/api/run-status":
                query = parse_qs(parsed.query)
                self._send_json(self._run_status(self._single(query, "id")))
            elif parsed.path == "/api/active-run":
                self._send_json(self._active_run())
            else:
                self.send_error(404)
        except Exception as exc:
            self._send_error(exc)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/delete-task":
                self._send_json(self._delete_task(str(payload.get("name") or "").strip()))
                return

            if parsed.path == "/api/upload-reference-image":
                self._send_json(self._upload_reference_image(payload))
                return

            if parsed.path == "/api/upload-comfy-debug-reference":
                self._send_json(self._upload_comfy_debug_reference(payload))
                return

            if parsed.path == "/api/upload-voice-sample":
                self._send_json(self._upload_voice_sample(payload))
                return

            if parsed.path == "/api/upload-knowledge":
                self._send_json(self._upload_knowledge(payload))
                return

            if parsed.path == "/api/favorite-asset":
                self._send_json(self._favorite_asset(payload))
                return

            if parsed.path == "/api/unfavorite-asset":
                self._send_json(self._unfavorite_asset(payload))
                return

            if parsed.path == "/api/update-asset-metadata":
                self._send_json(self._update_asset_metadata(payload))
                return

            if parsed.path == "/api/import-asset":
                self._send_json(self._import_asset(payload))
                return

            if parsed.path == "/api/comfy-debug-run":
                self._send_json(self._start_comfy_debug(payload))
                return

            if parsed.path == "/api/task-comfy-debug-run":
                self._send_json(self._start_task_comfy_debug(payload))
                return

            if parsed.path == "/api/task-comfy-debug-confirm":
                self._send_json(self._confirm_task_comfy_debug(payload))
                return

            if parsed.path == "/api/test-model":
                self._send_json(self._test_model(payload))
                return

            if parsed.path == "/api/save-staff":
                self._send_json(self._save_staff(payload))
                return

            if parsed.path == "/api/delete-staff":
                self._delete_staff(str(payload.get("name") or "").strip())
                self._send_json({"ok": True})
                return

            if parsed.path == "/api/save-workflow":
                self._send_json(self._save_workflow(payload))
                return

            if parsed.path == "/api/delete-workflow":
                self._delete_workflow(str(payload.get("name") or "").strip())
                self._send_json({"ok": True})
                return

            if parsed.path == "/api/save-file":
                self._send_json(self._save_file(payload))
                return

            if parsed.path == "/api/rebuild-final-output":
                self._send_json(self._rebuild_final_output(str(payload.get("task") or "").strip()))
                return

            if parsed.path == "/api/rerun-step":
                self._send_json(self._rerun_step(payload))
                return

            if parsed.path == "/api/retry-production-job":
                self._send_json(self._retry_production_job(payload))
                return

            if parsed.path == "/api/resume-task":
                self._send_json(self._resume_task(payload))
                return

            if parsed.path == "/api/cancel-run":
                self._send_json(self._cancel_run(payload))
                return

            if parsed.path == "/api/pause-run":
                self._send_json(self._pause_run(payload))
                return

            if parsed.path == "/api/export-task":
                self._send_json(self._export_task(payload))
                return

            if parsed.path != "/api/run":
                self.send_error(404)
                return

            workflow = str(payload.get("workflow") or "").strip()
            task_title = str(payload.get("task_title") or "").strip()
            user_input = str(payload.get("input") or "").strip()
            memory_scope = str(payload.get("memory_scope") or "").strip()
            if not memory_scope and bool(payload.get("use_memory")):
                memory_scope = "all"
            use_knowledge = bool(payload.get("use_knowledge"))
            inherit_task = str(payload.get("inherit_task") or "").strip()
            inherit_mode = str(payload.get("inherit_mode") or "final_output").strip()
            if memory_scope == "all":
                user_input = self._append_long_term_memory(user_input)
            if use_knowledge:
                user_input = self._append_knowledge_base(user_input)
            if inherit_task:
                user_input = self._append_inherited_task(user_input, inherit_task, inherit_mode)
            production_config = payload.get("production_config") or {}
            if memory_scope == "video_output" and isinstance(production_config, dict):
                production_config["video_memory_context"] = self._long_term_memory_context()
            if isinstance(production_config, dict):
                production_image_config = production_config.get("image_config")
                if isinstance(production_image_config, dict):
                    production_image_config["api_key"] = str(payload.get("image_api_key") or "").strip()
                    production_image_config["base_url"] = str(payload.get("image_base_url") or "").strip()
                production_video_config = production_config.get("video_config")
                if isinstance(production_video_config, dict):
                    production_video_config["api_key"] = str(payload.get("video_api_key") or "").strip()
                    production_video_config["base_url"] = str(payload.get("video_base_url") or "").strip()
                production_compose_config = production_config.get("compose_config")
                if isinstance(production_compose_config, dict):
                    production_compose_config["api_key"] = str(payload.get("comfy_api_key") or "").strip()
                    production_compose_config["base_url"] = str(payload.get("comfy_base_url") or "").strip()
            image_config = payload.get("image_config") or {}
            if isinstance(image_config, dict) and str(image_config.get("positive_prompt") or "").strip():
                user_input = self._append_image_config(user_input, image_config)
            video_config = payload.get("video_config") or {}
            if isinstance(video_config, dict) and str(video_config.get("positive_prompt") or "").strip():
                user_input = self._append_video_config(user_input, video_config)
            if isinstance(production_config, dict):
                compose_config = production_config.get("compose_config") or {}
                if isinstance(compose_config, dict) and compose_config:
                    user_input = self._append_comfyui_config(user_input, production_config, compose_config)
            reference_images = payload.get("reference_images") or []
            if reference_images:
                user_input = self._append_reference_images(user_input, reference_images)
            provider = str(payload.get("provider") or "auto").strip()
            model = str(payload.get("model") or "").strip() or None
            api_key = str(payload.get("api_key") or "").strip() or None
            base_url = str(payload.get("base_url") or "").strip() or None
            timeout = int(payload.get("timeout") or 0) or None

            if not workflow:
                raise ValueError("workflow is required")
            if not user_input:
                raise ValueError("input is required")

            run_id = uuid4().hex
            job = {
                "run_id": run_id,
                "status": "queued",
                "workflow": workflow,
                "task_title": task_title,
                "workflow_name": "",
                "created_at": time.time(),
                "updated_at": time.time(),
                "total_steps": 0,
                "completed_steps": 0,
                "steps": [],
                "cancel_requested": False,
                "pause_requested": False,
            }
            with RUN_JOBS_LOCK:
                RUN_JOBS[run_id] = job

            worker = threading.Thread(
                target=self._run_workflow_job,
                args=(run_id, workflow, user_input, task_title, production_config, provider, model, api_key, base_url, timeout),
                daemon=True,
            )
            worker.start()
            self._send_json(job)
        except Exception as exc:
            self._send_error(exc)

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def _config(self) -> dict:
        import os

        workflows = self._workflow_list()
        staff = [path.name for path in sorted(STAFF_ROOT.iterdir()) if path.is_dir()]
        return {
            "workflows": workflows,
            "staff": staff,
            "local_model_presets": self._local_model_presets(),
            "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
            "base_url_configured": bool(os.getenv("OPENAI_BASE_URL")),
            "default_model": os.getenv("OPENAI_MODEL") or "gpt-5.5",
            "default_base_url": os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        }

    @staticmethod
    def _local_model_presets() -> list[dict]:
        if not LOCAL_MODEL_PRESETS.exists():
            return []
        data = json.loads(LOCAL_MODEL_PRESETS.read_text(encoding="utf-8"))
        presets = data.get("presets") if isinstance(data, dict) else data
        return presets if isinstance(presets, list) else []

    def _system_health(self) -> dict:
        ollama_models = self._ollama_model_names()
        checks = [
            self._health_check("Python 运行时", "ok", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"),
            self._path_check("工作区目录", WORKSPACE_ROOT, must_be_writable=False),
            self._path_check("任务输出目录", OUTPUT_ROOT, must_be_writable=True),
            self._path_check("知识库目录", KNOWLEDGE_ROOT, must_be_writable=True),
            self._path_check("动作工作区", WORKSPACE_ROOT / "my_action_workspace", must_be_writable=True),
        ]

        bundled = WORKSPACE_ROOT.parent / "runtime" / "ollama" / "ollama.exe"
        installed = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
        ollama_path = shutil.which("ollama")
        if bundled.exists():
            checks.append(self._health_check("Ollama 命令", "ok", str(bundled)))
        elif ollama_path:
            checks.append(self._health_check("Ollama 命令", "ok", ollama_path))
        elif installed.exists():
            checks.append(self._health_check("Ollama 命令", "ok", str(installed)))
        else:
            checks.append(self._health_check("Ollama 命令", "warn", "未在 runtime/ollama/ollama.exe、PATH 或系统安装目录找到；可先安装 Ollama 或放入 runtime/ollama/"))

        checks.append(self._ollama_service_check(ollama_models))
        if "qwen3:8b-q4_K_M" in ollama_models:
            checks.append(self._health_check("推荐本地模型", "ok", "qwen3:8b-q4_K_M 已可用"))
        else:
            checks.append(self._health_check("推荐本地模型", "warn", "未发现 qwen3:8b-q4_K_M；可运行 start_local.ps1 自动拉取"))

        ffmpeg_bundled = WORKSPACE_ROOT.parent / "runtime" / "ffmpeg" / "bin" / "ffmpeg.exe"
        ffmpeg_alt = WORKSPACE_ROOT.parent / "runtime" / "ffmpeg" / "ffmpeg.exe"
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_bundled.exists():
            checks.append(self._health_check("FFmpeg 命令", "ok", str(ffmpeg_bundled)))
        elif ffmpeg_alt.exists():
            checks.append(self._health_check("FFmpeg 命令", "ok", str(ffmpeg_alt)))
        elif ffmpeg_path:
            checks.append(self._health_check("FFmpeg 命令", "ok", ffmpeg_path))
        else:
            checks.append(self._health_check("FFmpeg 命令", "warn", "未在 runtime/ffmpeg/bin/ffmpeg.exe 或 PATH 找到；本地自动成片会跳过，但制作包仍会生成"))

        voxcpm_bundled = WORKSPACE_ROOT.parent / "runtime" / "tts" / "venv" / "Scripts" / "voxcpm.exe"
        voxcpm_runner = WORKSPACE_ROOT / "my_codex_core" / "voxcpm2_tts_runner.py"
        voxcpm_path = shutil.which("voxcpm")
        if voxcpm_bundled.exists() and voxcpm_runner.exists():
            checks.append(self._health_check("VoxCPM2 本地配音", "ok", str(voxcpm_bundled)))
        elif voxcpm_path:
            checks.append(self._health_check("VoxCPM2 本地配音", "ok", voxcpm_path))
        else:
            checks.append(self._health_check("VoxCPM2 本地配音", "warn", "未发现 runtime/tts/venv/Scripts/voxcpm.exe；可运行 install_voxcpm2_runtime.ps1 安装"))

        return {
            "checks": checks,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        }

    @staticmethod
    def _health_check(name: str, status: str, detail: str) -> dict:
        labels = {"ok": "正常", "warn": "提醒", "error": "异常"}
        return {"name": name, "status": status, "label": labels.get(status, status), "detail": detail}

    def _path_check(self, name: str, path: Path, must_be_writable: bool) -> dict:
        try:
            path.mkdir(parents=True, exist_ok=True)
            if must_be_writable:
                marker = path / f".health_{uuid4().hex[:8]}"
                marker.write_text("ok", encoding="utf-8")
                marker.unlink()
            return self._health_check(name, "ok", str(path))
        except Exception as exc:
            return self._health_check(name, "error", f"{path}: {exc}")

    def _ollama_model_names(self) -> list[str]:
        req = urllib_request.Request("http://127.0.0.1:11434/v1/models", method="GET")
        try:
            with urllib_request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            models = data.get("data") if isinstance(data, dict) else []
            return [str(item.get("id") or item.get("name") or "") for item in models if isinstance(item, dict)]
        except Exception:
            return []

    def _ollama_service_check(self, names: list[str] | None = None) -> dict:
        if names is None:
            names = self._ollama_model_names()
        req = urllib_request.Request("http://127.0.0.1:11434/v1/models", method="GET")
        try:
            with urllib_request.urlopen(req, timeout=3):
                pass
            detail = "已连接 http://127.0.0.1:11434/v1"
            if names:
                detail += "；模型：" + ", ".join(names[:5])
            else:
                detail += "；暂未发现模型，可运行 start_local.ps1 自动拉取默认模型"
            return self._health_check("Ollama 模型服务", "ok", detail)
        except Exception as exc:
            return self._health_check("Ollama 模型服务", "warn", f"未连接 http://127.0.0.1:11434/v1；{exc}")

    def _workflow_list(self) -> list[dict]:
        if not WORKFLOW_ROOT.exists():
            return []

        workflows = []
        for path in sorted(WORKFLOW_ROOT.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
            workflows.append(
                {
                    "stem": path.stem,
                    "file": path.name,
                    "name": data.get("name") or path.stem,
                    "description": data.get("description") or "",
                    "archived": not self._is_long_video_workflow(path.stem, data.get("name") or ""),
                }
            )
        return workflows

    @staticmethod
    def _is_long_video_workflow(stem: str, name: str = "") -> bool:
        text = f"{stem} {name}"
        return stem == "workflow_长视频全流程" or "长视频全流程" in text

    def _workflow_detail(self, name: str) -> dict:
        path = self._safe_workflow_path(name, must_exist=True)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Workflow JSON must be an object")
        return {"name": path.stem, "file": path.name, "workflow": data}

    def _save_workflow(self, payload: dict) -> dict:
        file_name = str(payload.get("file") or "").strip()
        workflow = payload.get("workflow")
        if not isinstance(workflow, dict):
            raise ValueError("workflow must be a JSON object")

        name = str(workflow.get("name") or "").strip()
        description = str(workflow.get("description") or "").strip()
        steps = workflow.get("steps")
        if not name:
            raise ValueError("Workflow name cannot be empty")
        if not isinstance(steps, list) or not steps:
            raise ValueError("Workflow must contain at least one step")

        staff_names = {item["name"] for item in self._staff_list()}
        normalized_steps = []
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise ValueError(f"Step {index} must be a JSON object")
            agent = str(step.get("agent") or step.get("agent_id") or "").strip()
            task = str(step.get("task") or step.get("instruction") or "").strip()
            output = str(step.get("output") or step.get("expected_output") or "").strip()
            if not agent:
                raise ValueError(f"Step {index} agent cannot be empty")
            if staff_names and agent not in staff_names:
                raise ValueError(f"Step {index} agent does not exist: {agent}")
            if not task:
                raise ValueError(f"Step {index} task cannot be empty")
            if not output:
                raise ValueError(f"Step {index} output cannot be empty")
            normalized_steps.append({"step": index, "agent": agent, "task": task, "output": output})

        path = self._safe_workflow_path(file_name, must_exist=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = dict(workflow)
        data["name"] = name
        data["description"] = description
        data["steps"] = normalized_steps
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"ok": True, "name": path.stem, "file": path.name}

    def _delete_workflow(self, name: str) -> None:
        path = self._safe_workflow_path(name, must_exist=True)
        path.unlink()

    def _staff_list(self) -> list[dict]:
        if not STAFF_ROOT.exists():
            return []

        staff = []
        for path in sorted(STAFF_ROOT.iterdir()):
            if not path.is_dir():
                continue
            rule_path = path / "flow_rule.json"
            rule = {}
            if rule_path.exists():
                try:
                    rule = json.loads(rule_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    rule = {}
            staff.append(
                {
                    "name": path.name,
                    "display_name": rule.get("agent_name") or path.name,
                    "agent_id": rule.get("agent_id") or path.name,
                    "role": rule.get("role") or "",
                    "archived": not self._is_long_video_staff(path.name),
                }
            )
        return staff

    @staticmethod
    def _is_long_video_staff(name: str) -> bool:
        return name.startswith(("01_", "03_", "04_", "05_", "06_", "07_", "20_", "22_", "23_"))

    def _staff_detail(self, name: str) -> dict:
        staff_dir = self._safe_staff_dir(name, must_exist=True)
        agent_path = staff_dir / "agent.md"
        rule_path = staff_dir / "flow_rule.json"
        rule_text = rule_path.read_text(encoding="utf-8", errors="replace") if rule_path.exists() else "{}"
        if rule_text.strip():
            json.loads(rule_text)
        return {
            "name": staff_dir.name,
            "agent_md": agent_path.read_text(encoding="utf-8", errors="replace") if agent_path.exists() else "",
            "flow_rule_json": rule_text,
        }

    def _save_staff(self, payload: dict) -> dict:
        name = str(payload.get("name") or "").strip()
        agent_md = str(payload.get("agent_md") or "").strip()
        flow_rule_json = str(payload.get("flow_rule_json") or "{}").strip()
        if not agent_md:
            raise ValueError("agent.md cannot be empty")
        rule = json.loads(flow_rule_json)
        staff_dir = self._safe_staff_dir(name, must_exist=False)
        staff_dir.mkdir(parents=True, exist_ok=True)
        if not isinstance(rule, dict):
            raise ValueError("flow_rule.json must be a JSON object")
        rule.setdefault("agent_id", staff_dir.name)
        rule.setdefault("agent_name", staff_dir.name)
        (staff_dir / "agent.md").write_text(agent_md.rstrip() + "\n", encoding="utf-8")
        (staff_dir / "flow_rule.json").write_text(
            json.dumps(rule, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {"ok": True, "name": staff_dir.name}

    def _delete_staff(self, name: str) -> None:
        staff_dir = self._safe_staff_dir(name, must_exist=True)
        staff_root = STAFF_ROOT.resolve()
        if staff_dir == staff_root:
            raise ValueError("Refusing to delete staff root")

        for path in sorted(staff_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        staff_dir.rmdir()

    def _run_status(self, run_id: str) -> dict:
        with RUN_JOBS_LOCK:
            job = RUN_JOBS.get(run_id)
            if not job:
                raise FileNotFoundError(f"Run not found: {run_id}")
            return json.loads(json.dumps(job, ensure_ascii=False))

    def _active_run(self) -> dict:
        with RUN_JOBS_LOCK:
            jobs = sorted(
                RUN_JOBS.values(),
                key=lambda item: float(item.get("updated_at") or 0),
                reverse=True,
            )
            for status_group in ({"queued", "running"}, {"paused"}):
                for job in jobs:
                    if job.get("debug_type") == "comfy_debug":
                        continue
                    if job.get("status") in status_group:
                        return {"run": json.loads(json.dumps(job, ensure_ascii=False))}
        return {"run": None}

    def _cancel_run(self, payload: dict) -> dict:
        return self._stop_run(payload, paused=False)

    def _pause_run(self, payload: dict) -> dict:
        return self._stop_run(payload, paused=True)

    def _stop_run(self, payload: dict, paused: bool) -> dict:
        run_id = str(payload.get("run_id") or payload.get("id") or "").strip()
        task_name = str(payload.get("task_name") or payload.get("task") or "").strip()
        with RUN_JOBS_LOCK:
            job = RUN_JOBS.get(run_id) if run_id else None
            if not job and task_name:
                for candidate in RUN_JOBS.values():
                    if str(candidate.get("task_name") or "") == task_name or Path(str(candidate.get("task_dir") or "")).name == task_name:
                        job = candidate
                        run_id = str(candidate.get("run_id") or run_id)
                        break
            if not job:
                if not run_id and not task_name:
                    raise ValueError("run_id or task_name is required")
                raise FileNotFoundError(f"Run not found: {run_id or task_name}")
            status = str(job.get("status") or "")
            if status in {"completed", "failed", "cancelled"} or (paused and status == "paused"):
                job["updated_at"] = time.time()
                job["message"] = "任务已停止"
                return json.loads(json.dumps(job, ensure_ascii=False))
            job["cancel_requested"] = True
            job["pause_requested"] = bool(paused)
            job["status"] = "paused" if paused else "cancelled"
            job["awaiting_confirmation"] = False
            job.pop("awaiting_confirmation_step", None)
            job["error"] = "任务已暂停，可稍后继续" if paused else "任务已终止"
            job["current_message"] = "任务已暂停，可稍后继续" if paused else "任务已终止，可从任务输出继续"
            for step in job.get("steps", []):
                if step.get("status") == "active":
                    step["status"] = "error"
                    step["message"] = "已暂停" if paused else "已终止"
            self._append_detail_event(
                job,
                {
                    "step": job.get("current_step") or 1,
                    "message": "用户暂停任务" if paused else "用户终止任务",
                    "kind": "error",
                },
            )
            job["updated_at"] = time.time()
            job["message"] = "任务已暂停" if paused else "任务已终止"
            task_dir_text = str(job.get("task_dir") or "").strip()
            if task_dir_text:
                summary_path = Path(task_dir_text) / "run_summary.json"
                if summary_path.is_file():
                    try:
                        summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
                    except Exception:
                        summary = {}
                    if isinstance(summary, dict):
                        summary["awaiting_confirmation"] = False
                        summary.pop("awaiting_confirmation_step", None)
                        summary.pop("blocked_step", None)
                        summary["status"] = "paused" if paused else "cancelled"
                        summary["blocked_reason"] = "任务已暂停，可继续任务" if paused else ""
                        summary["updated_at"] = time.time()
                        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            return json.loads(json.dumps(job, ensure_ascii=False))

    @staticmethod
    def _stop_requested(run_id: str) -> str:
        with RUN_JOBS_LOCK:
            job = RUN_JOBS.get(run_id)
            if not job or not job.get("cancel_requested"):
                return ""
            return "paused" if job.get("pause_requested") else "cancelled"

    @classmethod
    def _progress_callback_for_run(cls, run_id: str):
        def callback(event: dict) -> None:
            stop_status = cls._stop_requested(run_id)
            if stop_status == "paused":
                raise RuntimeError("用户已暂停任务")
            if stop_status == "cancelled":
                raise RuntimeError("用户已终止任务")
            cls._apply_progress(run_id, event)
        return callback

    @staticmethod
    def _update_job(run_id: str, updates: dict) -> None:
        with RUN_JOBS_LOCK:
            job = RUN_JOBS.get(run_id)
            if not job:
                return
            job.update(updates)
            job["updated_at"] = time.time()

    @classmethod
    def _apply_progress(cls, run_id: str, event: dict) -> None:
        with RUN_JOBS_LOCK:
            job = RUN_JOBS.get(run_id)
            if not job:
                return
            kind = event.get("event")
            if kind == "started":
                total = int(event.get("total_steps") or 0)
                job.update(
                    {
                        "status": "running",
                        "workflow_name": event.get("workflow_name") or job.get("workflow_name", ""),
                        "task_title": event.get("task_title") or job.get("task_title", ""),
                        "task_dir": event.get("task_dir", ""),
                        "task_name": Path(event.get("task_dir", "")).name if event.get("task_dir") else "",
                        "total_steps": total,
                        "completed_steps": 0,
                        "steps": [
                            {"step": step_no, "status": "pending", "agent_id": "", "agent_name": ""}
                            for step_no in range(1, total + 1)
                        ],
                        "production_events": [],
                        "production_message": "",
                        "detail_events": [],
                        "current_step": 0,
                        "current_message": "任务已开始，准备执行第 1 步",
                    }
                )
            elif kind == "step_started":
                step_no = int(event.get("step") or 0)
                cls._set_step(job, step_no, event, "active")
                message = f"正在执行第 {step_no} 步：{event.get('agent_name') or event.get('agent_id') or ''}"
                job["current_step"] = step_no
                job["current_message"] = message
                cls._append_detail_event(job, {"step": step_no, "message": message, "kind": "active"})
            elif kind == "step_update":
                step_no = int(event.get("step") or 0)
                cls._set_step(job, step_no, event, "active")
                message = event.get("message", "")
                job["current_step"] = step_no
                job["current_message"] = f"第 {step_no} 步：{message}" if message else f"第 {step_no} 步执行中"
                cls._append_detail_event(job, {"step": step_no, "message": job["current_message"], "kind": "active"})
            elif kind == "step_completed":
                step_no = int(event.get("step") or 0)
                cls._set_step(job, step_no, event, "done")
                job["completed_steps"] = max(int(job.get("completed_steps") or 0), step_no)
                message = event.get("message") or f"第 {step_no} 步完成"
                job["current_step"] = step_no
                job["current_message"] = message
                cls._append_detail_event(job, {"step": step_no, "message": message, "kind": "done"})
            elif kind == "step_error":
                step_no = int(event.get("step") or 0)
                cls._set_step(job, step_no, event, "error")
                message = event.get("message") or f"第 {step_no} 步失败"
                job["current_step"] = step_no
                job["current_message"] = message
                cls._append_detail_event(job, {"step": step_no, "message": message, "kind": "error"})
            elif kind == "checkpoint":
                step_no = int(event.get("step") or 0)
                cls._set_step(job, step_no, event, "done")
                job["status"] = "paused"
                job["pause_requested"] = False
                job["cancel_requested"] = False
                job["completed_steps"] = max(int(job.get("completed_steps") or 0), step_no)
                job["current_step"] = step_no
                job["awaiting_confirmation"] = True
                job["awaiting_confirmation_step"] = step_no
                job["error"] = event.get("message") or f"第 {step_no} 步已完成，等待确认"
                job["current_message"] = job["error"]
                cls._append_detail_event(job, {"step": step_no, "message": job["error"], "kind": "done"})
            elif kind == "production_update":
                events = job.setdefault("production_events", [])
                item = {
                    "stage": event.get("stage", "production"),
                    "message": event.get("message", ""),
                    "status": event.get("status", ""),
                    "job_status": event.get("job_status", ""),
                    "error": event.get("error", ""),
                    "updated_at": time.time(),
                }
                for key in (
                    "total_jobs",
                    "completed_jobs",
                    "success_count",
                    "failed_count",
                    "downloaded_count",
                    "quality_score",
                    "attempt",
                    "max_attempts",
                    "current_job",
                    "remote_status",
                    "endpoint",
                    "task_id",
                    "taskId",
                    "job_type",
                    "job_index",
                    "job_count",
                    "material_name",
                    "material_type",
                    "output_file",
                    "output_type",
                    "downloaded_file",
                    "url",
                    "provider",
                ):
                    if key in event:
                        item[key] = event.get(key)
                events.append(item)
                if len(events) > 80:
                    del events[:-80]
                job["production_message"] = event.get("message", "")
                job["current_message"] = event.get("message", "")
                cls._append_detail_event(job, {"step": 0, "message": event.get("message", ""), "kind": "active"})
            elif kind == "completed":
                if job.get("awaiting_confirmation"):
                    job["status"] = "paused"
                    job["current_message"] = job.get("current_message") or "当前步骤已完成，等待确认"
                    job["updated_at"] = time.time()
                    return
                if cls._has_running_remote_jobs(job):
                    job["status"] = "running"
                    job["current_message"] = "RunningHub 远程任务仍在运行，继续等待素材结果"
                    job["updated_at"] = time.time()
                    return
                production_status = str(event.get("production_status") or "off").strip().lower()
                if cls._is_failed_production_status(production_status):
                    failed_step = cls._production_failure_step(job, production_status)
                    cls._set_step(job, failed_step, {"step": failed_step, "message": f"自动生成失败：{production_status}"}, "error")
                    job.update(
                        {
                            "status": "failed",
                            "workflow_name": event.get("workflow_name") or job.get("workflow_name", ""),
                            "task_title": event.get("task_title") or job.get("task_title", ""),
                            "task_dir": event.get("task_dir") or job.get("task_dir", ""),
                            "task_name": Path(event.get("task_dir", "")).name if event.get("task_dir") else job.get("task_name", ""),
                            "provider": event.get("provider", ""),
                            "step_count": event.get("step_count", 0),
                            "final_output": event.get("final_output", ""),
                            "production_manifest": event.get("production_manifest", ""),
                            "production_status": event.get("production_status", "off"),
                            "error": f"自动生成失败：{event.get('production_status', 'off')}",
                            "production_message": f"自动生成失败：{event.get('production_status', 'off')}",
                            "current_message": f"自动生成失败：{event.get('production_status', 'off')}",
                        }
                    )
                    cls._append_detail_event(job, {"step": failed_step, "message": job["error"], "kind": "error"})
                    job["updated_at"] = time.time()
                    return
                job.update(
                    {
                        "status": "completed",
                        "workflow_name": event.get("workflow_name") or job.get("workflow_name", ""),
                        "task_title": event.get("task_title") or job.get("task_title", ""),
                        "task_dir": event.get("task_dir") or job.get("task_dir", ""),
                        "task_name": Path(event.get("task_dir", "")).name if event.get("task_dir") else job.get("task_name", ""),
                        "provider": event.get("provider", ""),
                        "step_count": event.get("step_count", 0),
                        "completed_steps": event.get("step_count", job.get("completed_steps", 0)),
                        "final_output": event.get("final_output", ""),
                        "production_manifest": event.get("production_manifest", ""),
                        "production_status": event.get("production_status", "off"),
                        "production_message": f"自动生成：{event.get('production_status', 'off')}",
                        "current_message": f"任务完成，自动生成：{event.get('production_status', 'off')}",
                    }
                )
            job["updated_at"] = time.time()

    @staticmethod
    def _has_running_remote_jobs(job: dict) -> bool:
        for event in job.get("production_events", [])[-20:]:
            status = str(event.get("remote_status") or event.get("job_status") or event.get("status") or "").strip().lower()
            if status in {"queued", "running", "pending"}:
                return True
        return False

    @staticmethod
    def _is_failed_production_status(status: str) -> bool:
        if not status or status == "off":
            return False
        failed_keywords = ("failed", "partial", "quality_failed", "adapter_failed", "timeout")
        return any(keyword in status for keyword in failed_keywords)

    @staticmethod
    def _production_failure_step(job: dict, production_status: str) -> int:
        text = production_status.lower()
        if "comfy" in text or "quality" in text:
            for step in job.get("steps", []):
                name = f"{step.get('agent_id', '')} {step.get('agent_name', '')} {step.get('task', '')}".lower()
                if "comfy" in name or "素材" in name or "material" in name:
                    return int(step.get("step") or 0) or 1
        if "tts" in text or "audio" in text:
            for step in job.get("steps", []):
                name = f"{step.get('agent_id', '')} {step.get('agent_name', '')} {step.get('task', '')}".lower()
                if "tts" in name or "语音" in name or "字幕" in name:
                    return int(step.get("step") or 0) or 1
        return int(job.get("current_step") or 0) or len(job.get("steps", [])) or 1

    @staticmethod
    def _set_step(job: dict, step_no: int, event: dict, status: str) -> None:
        if step_no <= 0:
            return
        steps = job.setdefault("steps", [])
        while len(steps) < step_no:
            steps.append({"step": len(steps) + 1, "status": "pending", "agent_id": "", "agent_name": ""})
        steps[step_no - 1].update(
            {
                "step": step_no,
                "status": status,
                "agent_id": event.get("agent_id", ""),
                "agent_name": event.get("agent_name", ""),
                "task": event.get("task", ""),
                "expected_output": event.get("expected_output", ""),
                "output_path": event.get("output_path", ""),
                "message": event.get("message", ""),
                "elapsed_seconds": event.get("elapsed_seconds", ""),
            }
        )

    @staticmethod
    def _append_detail_event(job: dict, event: dict) -> None:
        events = job.setdefault("detail_events", [])
        item = dict(event)
        item["updated_at"] = time.time()
        events.append(item)
        if len(events) > 80:
            del events[:-80]

    def _run_workflow_job(
        self,
        run_id: str,
        workflow: str,
        user_input: str,
        task_title: str,
        production_config: dict,
        provider: str,
        model: str | None,
        api_key: str | None,
        base_url: str | None,
        timeout: int | None,
    ) -> None:
        try:
            self._update_job(run_id, {"status": "running"})
            engine = WorkflowEngine(
                WORKSPACE_ROOT,
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
            )
            engine.run(
                workflow,
                user_input,
                task_title=task_title,
                production_config=production_config,
                progress_callback=self._progress_callback_for_run(run_id),
            )
        except WorkflowCheckpointPause as exc:
            with RUN_JOBS_LOCK:
                job = RUN_JOBS.get(run_id)
                if job:
                    job["status"] = "paused"
                    job["pause_requested"] = False
                    job["cancel_requested"] = False
                    job["error"] = str(exc)
                    job["current_message"] = str(exc)
                    job["updated_at"] = time.time()
        except Exception as exc:
            with RUN_JOBS_LOCK:
                job = RUN_JOBS.get(run_id)
                if job:
                    paused = bool(job.get("pause_requested"))
                    stopped = bool(job.get("cancel_requested"))
                    job["status"] = "paused" if paused else "cancelled" if stopped else "failed"
                    job["error"] = str(exc)
                    job["traceback"] = traceback.format_exc()
                    for step in job.get("steps", []):
                        if step.get("status") == "active":
                            step["status"] = "error"
                            if paused:
                                step["message"] = "用户已暂停"
                            elif stopped:
                                step["message"] = "用户已终止"
                    job["updated_at"] = time.time()

    def _run_resume_job(
        self,
        run_id: str,
        task_dir: Path,
        production_config: dict,
        provider: str,
        model: str | None,
        api_key: str | None,
        base_url: str | None,
        timeout: int | None,
    ) -> None:
        try:
            self._update_job(run_id, {"status": "running"})
            engine = WorkflowEngine(
                WORKSPACE_ROOT,
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
            )
            engine.resume(
                task_dir,
                production_config=production_config,
                progress_callback=self._progress_callback_for_run(run_id),
            )
        except WorkflowCheckpointPause as exc:
            with RUN_JOBS_LOCK:
                job = RUN_JOBS.get(run_id)
                if job:
                    job["status"] = "paused"
                    job["pause_requested"] = False
                    job["cancel_requested"] = False
                    job["error"] = str(exc)
                    job["current_message"] = str(exc)
                    job["updated_at"] = time.time()
        except Exception as exc:
            with RUN_JOBS_LOCK:
                job = RUN_JOBS.get(run_id)
                if job:
                    paused = bool(job.get("pause_requested"))
                    stopped = bool(job.get("cancel_requested"))
                    job["status"] = "paused" if paused else "cancelled" if stopped else "failed"
                    job["error"] = str(exc)
                    job["traceback"] = traceback.format_exc()
                    for step in job.get("steps", []):
                        if step.get("status") == "active":
                            step["status"] = "error"
                            if paused:
                                step["message"] = "用户已暂停"
                            elif stopped:
                                step["message"] = "用户已终止"
                    job["updated_at"] = time.time()

    def _run_rerun_step_job(
        self,
        run_id: str,
        task_dir: Path,
        step: int,
        provider: str,
        model: str | None,
        api_key: str | None,
        base_url: str | None,
        timeout: int | None,
    ) -> None:
        try:
            self._update_job(run_id, {"status": "running"})
            engine = WorkflowEngine(
                WORKSPACE_ROOT,
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
            )
            result = engine.rerun_step(
                task_dir,
                step,
                progress_callback=self._progress_callback_for_run(run_id),
            )
            with RUN_JOBS_LOCK:
                job = RUN_JOBS.get(run_id)
                if job:
                    job["rerun_result"] = result
                    job["output_file"] = result.get("file", "")
                    job["updated_at"] = time.time()
        except Exception as exc:
            with RUN_JOBS_LOCK:
                job = RUN_JOBS.get(run_id)
                if job:
                    paused = bool(job.get("pause_requested"))
                    stopped = bool(job.get("cancel_requested"))
                    job["status"] = "paused" if paused else "cancelled" if stopped else "failed"
                    job["error"] = str(exc)
                    job["traceback"] = traceback.format_exc()
                    for item in job.get("steps", []):
                        if item.get("status") == "active":
                            item["status"] = "error"
                            if paused:
                                item["message"] = "用户已暂停"
                            elif stopped:
                                item["message"] = "用户已终止"
                    job["updated_at"] = time.time()

    def _run_retry_production_job(
        self,
        run_id: str,
        task_dir: Path,
        retry_job: str,
        production_config: dict,
    ) -> None:
        try:
            self._update_job(
                run_id,
                {
                    "status": "running",
                    "current_message": f"正在重试生产任务：{retry_job}",
                    "production_events": [],
                    "production_message": "",
                },
            )
            manifest = retry_production_job(
                task_dir,
                retry_job,
                production_config=production_config,
                progress_callback=self._progress_callback_for_run(run_id),
            )
            production_status = str(manifest.get("status") or "").strip()
            final_event = {
                "event": "completed",
                "workflow_name": "",
                "task_title": "",
                "task_dir": str(task_dir),
                "step_count": 0,
                "final_output": str(task_dir / "final_output.md") if (task_dir / "final_output.md").is_file() else "",
                "production_manifest": str(task_dir / "production_manifest.json"),
                "production_status": production_status,
            }
            self._apply_progress(run_id, final_event)
            with RUN_JOBS_LOCK:
                job = RUN_JOBS.get(run_id)
                if job:
                    job["production_retry"] = True
                    job["production_retry_job"] = retry_job
                    job["production_status"] = production_status
                    job["production_manifest"] = str(task_dir / "production_manifest.json")
                    job["current_message"] = f"生产任务重试完成：{retry_job} / {production_status}"
                    job["updated_at"] = time.time()
        except Exception as exc:
            with RUN_JOBS_LOCK:
                job = RUN_JOBS.get(run_id)
                if job:
                    paused = bool(job.get("pause_requested"))
                    stopped = bool(job.get("cancel_requested"))
                    job["status"] = "paused" if paused else "cancelled" if stopped else "failed"
                    job["error"] = str(exc)
                    job["traceback"] = traceback.format_exc()
                    job["production_retry"] = True
                    job["production_retry_job"] = retry_job
                    job["production_status"] = f"{retry_job}_retry_failed"
                    job["current_message"] = f"生产任务重试失败：{retry_job} / {exc}"
                    self._append_detail_event(job, {"step": 0, "message": job["current_message"], "kind": "error"})
                    job["updated_at"] = time.time()

    def _tasks(self) -> list[dict]:
        if not OUTPUT_ROOT.exists():
            return []

        tasks = []
        for path in sorted(OUTPUT_ROOT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.is_dir():
                continue
            summary_path = path / "run_summary.json"
            summary = {}
            if summary_path.exists():
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
                except json.JSONDecodeError:
                    summary = {}
            tasks.append(
                {
                    "name": path.name,
                    "task_title": summary.get("task_title") or "",
                    "workflow": summary.get("workflow") or path.name,
                    "provider": summary.get("provider") or "",
                    "mtime": path.stat().st_mtime,
                }
            )
        return tasks

    def _task_detail(self, name: str) -> dict:
        task_dir = self._safe_task_dir(name)
        files = []
        for path in sorted(task_dir.rglob("*")):
            if path.is_file():
                files.append(path.relative_to(task_dir).as_posix())

        summary_path = task_dir / "run_summary.json"
        summary = {}
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                summary = {}
        production_jobs = self._production_jobs(task_dir)
        assets = self._task_assets(task_dir, files)
        comfy_debug = self._task_comfy_debug_status(task_dir)
        task_state = self._task_state(summary, files, comfy_debug)
        allowed_actions = self._allowed_task_actions(task_state, summary, files)
        task_status = self._task_status(task_dir, name, summary, files, task_state, allowed_actions, assets, production_jobs)
        return {
            "name": name,
            "summary": summary,
            "files": files,
            "task_state": task_state,
            "allowed_actions": allowed_actions,
            "assets": assets,
            "production_jobs": production_jobs,
            "comfy_debug": comfy_debug,
            "task_status": task_status,
        }

    @staticmethod
    def _production_jobs(task_dir: Path) -> list[dict]:
        manifest_path = task_dir / "production_manifest.json"
        if not manifest_path.is_file():
            return []
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return [
                {
                    "id": "production",
                    "label": "自动生产",
                    "status": "failed",
                    "detail": "production_manifest.json is invalid JSON",
                    "outputs": [],
                }
            ]
        if not isinstance(manifest, dict):
            return []

        composition = manifest.get("composition") if isinstance(manifest.get("composition"), dict) else {}
        audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
        image_generation = manifest.get("image_generation") if isinstance(manifest.get("image_generation"), dict) else {}
        video_generation = manifest.get("video_generation") if isinstance(manifest.get("video_generation"), dict) else {}

        mode = str(manifest.get("mode") or "").strip()
        if mode == "comfy_full":
            material_status = str(composition.get("comfyui_adapter_status") or composition.get("adapter_status") or "not_configured")
        else:
            material_status = str(image_generation.get("adapter_status") or video_generation.get("adapter_status") or "not_configured")
        material_outputs = []
        for key in ("downloaded_files",):
            values = composition.get("comfyui_downloaded_files") or composition.get(key) or image_generation.get(key) or video_generation.get(key) or []
            if isinstance(values, list):
                material_outputs.extend(str(value) for value in values if value)
        material_detail = composition.get("comfyui_adapter_manifest") or composition.get("adapter_manifest") or image_generation.get("adapter_manifest") or video_generation.get("adapter_manifest") or ""

        tts_status = str(audio.get("adapter_status") or "not_configured")
        tts_outputs = [str(audio.get("voiceover_audio_file") or "")] if audio.get("voiceover_audio_file") else []
        tts_detail = audio.get("adapter_manifest") or audio.get("voice_text_reason") or ""

        ffmpeg_status = str(composition.get("local_ffmpeg_status") or (composition.get("adapter_status") if composition.get("local_ffmpeg_manifest") else "") or "not_configured")
        manifest_files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        final_video = composition.get("final_video_file") or manifest_files.get("final_video") or ""
        ffmpeg_outputs = [str(final_video)] if final_video else []
        ffmpeg_detail = composition.get("local_ffmpeg_manifest") or composition.get("target_file") or ""

        jobs = [
            {
                "id": "material",
                "label": "素材生成/匹配",
                "status": material_status,
                "detail": str(material_detail or ""),
                "outputs": material_outputs,
            },
            {
                "id": "tts",
                "label": "配音",
                "status": tts_status,
                "detail": str(tts_detail or ""),
                "outputs": tts_outputs,
            },
            {
                "id": "ffmpeg",
                "label": "合成",
                "status": ffmpeg_status,
                "detail": str(ffmpeg_detail or ""),
                "outputs": ffmpeg_outputs,
            },
        ]
        node_labels = {
            "local_tts": "08 本地配音",
            "subtitle_build": "08 字幕生成",
            "bgm_select": "08 BGM 匹配",
            "ffmpeg_compose": "08 音画合成",
            "format_export": "08 格式导出",
        }
        production_nodes = manifest.get("production_nodes") if isinstance(manifest.get("production_nodes"), list) else []
        for node in production_nodes:
            if not isinstance(node, dict) or not node.get("job_id"):
                continue
            node_id = str(node.get("job_id"))
            jobs.append(
                {
                    "id": node_id,
                    "label": node_labels.get(node_id) or str(node.get("mode") or node_id),
                    "status": "success" if node.get("status") == "cached" else str(node.get("status") or "pending"),
                    "detail": str(node.get("blocked_reason") or node.get("error") or ("缓存命中" if node.get("cache_hit") else "")),
                    "outputs": [str(value) for value in (node.get("outputs") or []) if value],
                    "depends_on": [str(value) for value in (node.get("depends_on") or []) if value],
                    "attempts": int(node.get("attempts") or 1),
                    "cache_hit": bool(node.get("cache_hit")),
                    "stage": str(node.get("stage") or ""),
                }
            )
        return jobs

    @classmethod
    def _task_status(
        cls,
        task_dir: Path,
        task_name: str,
        summary: dict,
        files: list[str],
        task_state: str,
        allowed_actions: list[str],
        assets: dict,
        production_jobs: list[dict],
    ) -> dict:
        active_job = cls._active_job_for_task(task_name, task_dir)
        steps = cls._task_status_steps(task_dir, summary, files, active_job)
        production = cls._task_status_production(task_dir, summary, production_jobs)
        comfy_debug = cls._task_comfy_debug_status(task_dir)
        diagnostics = cls._task_status_diagnostics(task_dir, summary, files, task_state, steps, production)
        workflow_state = cls._task_status_workflow(summary, steps, active_job, task_state)
        return {
            "schema_version": 1,
            "state": task_state,
            "workflow": workflow_state,
            "steps": steps,
            "production": production,
            "comfy_debug": comfy_debug,
            "assets": assets,
            "allowed_actions": allowed_actions,
            "diagnostics": diagnostics,
        }

    @staticmethod
    def _active_job_for_task(task_name: str, task_dir: Path) -> dict | None:
        with RUN_JOBS_LOCK:
            jobs = sorted(
                RUN_JOBS.values(),
                key=lambda item: float(item.get("updated_at") or 0),
                reverse=True,
            )
            for job in jobs:
                if job.get("status") not in {"queued", "running", "paused"}:
                    continue
                if str(job.get("task_name") or "") == task_name or str(job.get("task_dir") or "") == str(task_dir):
                    return json.loads(json.dumps(job, ensure_ascii=False))
        return None

    @classmethod
    def _task_status_steps(cls, task_dir: Path, summary: dict, files: list[str], active_job: dict | None) -> list[dict]:
        workflow_steps = cls._workflow_steps_for_task(task_dir)
        output_files = {file: task_dir / file for file in files if re.match(r"^step_\d+_.*/output\.md$", file)}
        by_step: dict[int, dict] = {}
        for file, path in output_files.items():
            step_no = cls._step_number_from_file(file)
            if step_no:
                by_step.setdefault(step_no, {})["output_file"] = file
                by_step[step_no]["has_output"] = True
                by_step[step_no]["size"] = path.stat().st_size if path.is_file() else 0
                by_step[step_no]["mtime"] = path.stat().st_mtime if path.is_file() else 0

        max_step = max(
            [int(item.get("step") or 0) for item in workflow_steps if isinstance(item, dict)] + list(by_step.keys()) + [0]
        )
        active_step = int((active_job or {}).get("current_step") or 0)
        completed_steps = int((active_job or {}).get("completed_steps") or summary.get("step_count") or 0)
        awaiting_step = int(summary.get("awaiting_confirmation_step") or 0) if summary.get("awaiting_confirmation") else 0
        blocked_step = int(summary.get("blocked_step") or 0) if summary.get("blocked_reason") else 0

        steps: list[dict] = []
        for index in range(1, max_step + 1):
            workflow_step = next((item for item in workflow_steps if int(item.get("step") or 0) == index), {})
            metadata = cls._step_metadata(task_dir, index)
            output_info = by_step.get(index, {})
            has_output = bool(output_info.get("has_output"))
            if awaiting_step == index:
                status = "awaiting_confirmation"
            elif blocked_step == index:
                status = "blocked"
            elif active_step == index and (active_job or {}).get("status") in {"queued", "running"}:
                status = "running"
            elif has_output or index <= completed_steps:
                status = "completed"
            else:
                status = "pending"
            agent = metadata.get("agent_id") or workflow_step.get("agent") or ""
            title = metadata.get("agent_name") or agent or f"Step {index}"
            steps.append(
                {
                    "step": index,
                    "status": status,
                    "agent": agent,
                    "title": title,
                    "task": metadata.get("task") or workflow_step.get("task") or "",
                    "expected_output": metadata.get("expected_output") or workflow_step.get("output") or "",
                    "output_file": output_info.get("output_file", ""),
                    "has_output": has_output,
                    "size": output_info.get("size", 0),
                    "mtime": output_info.get("mtime", 0),
                    "needs_confirmation": awaiting_step == index,
                    "blocked_reason": str(summary.get("blocked_reason") or "") if blocked_step == index else "",
                }
            )
        return steps

    @staticmethod
    def _workflow_steps_for_task(task_dir: Path) -> list[dict]:
        workflow_path = task_dir / "workflow.json"
        if not workflow_path.is_file():
            return []
        try:
            workflow = json.loads(workflow_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return []
        steps = workflow.get("steps") if isinstance(workflow, dict) else []
        return steps if isinstance(steps, list) else []

    @staticmethod
    def _step_metadata(task_dir: Path, step_no: int) -> dict:
        pattern = f"step_{step_no:02d}_*/metadata.json"
        matches = sorted(task_dir.glob(pattern))
        if not matches:
            return {}
        try:
            data = json.loads(matches[0].read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _step_number_from_file(file: str) -> int:
        match = re.match(r"^step_(\d+)_", str(file or ""))
        return int(match.group(1)) if match else 0

    @classmethod
    def _task_status_production(cls, task_dir: Path, summary: dict, production_jobs: list[dict]) -> dict:
        manifest_path = task_dir / "production_manifest.json"
        manifest: dict = {}
        manifest_error = ""
        if manifest_path.is_file():
            try:
                loaded = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                manifest = loaded if isinstance(loaded, dict) else {}
            except json.JSONDecodeError as exc:
                manifest_error = str(exc)
        history = manifest.get("production_job_history") if isinstance(manifest.get("production_job_history"), list) else []
        status = str(manifest.get("status") or summary.get("production_status") or "off")
        allowed_retries = []
        for job in production_jobs:
            job_id = str(job.get("id") or "")
            if job_id in {"material", "tts", "ffmpeg"}:
                allowed_retries.append(job_id)
        return {
            "mode": str(manifest.get("mode") or "off"),
            "status": status,
            "manifest_file": str(manifest_path) if manifest_path.is_file() else "",
            "manifest_error": manifest_error,
            "jobs": production_jobs,
            "history": history[-10:],
            "allowed_retries": allowed_retries,
        }

    @classmethod
    def _task_status_workflow(cls, summary: dict, steps: list[dict], active_job: dict | None, task_state: str) -> dict:
        if active_job:
            run_status = str(active_job.get("status") or "")
            current_step = int(active_job.get("current_step") or 0)
            completed_steps = int(active_job.get("completed_steps") or 0)
            message = str(active_job.get("current_message") or active_job.get("error") or "")
        else:
            run_status = task_state
            current_step = int(summary.get("blocked_step") or summary.get("resume_step") or summary.get("resume_from_step") or 0)
            completed_steps = len([step for step in steps if step.get("status") == "completed"])
            message = str(summary.get("blocked_reason") or "")
        return {
            "run_status": run_status,
            "current_step": current_step,
            "completed_steps": completed_steps,
            "total_steps": len(steps),
            "awaiting_confirmation": bool(summary.get("awaiting_confirmation")),
            "awaiting_confirmation_step": int(summary.get("awaiting_confirmation_step") or 0),
            "blocked_reason": str(summary.get("blocked_reason") or ""),
            "message": message,
        }

    @classmethod
    def _task_status_diagnostics(
        cls,
        task_dir: Path,
        summary: dict,
        files: list[str],
        task_state: str,
        steps: list[dict],
        production: dict,
    ) -> list[dict]:
        diagnostics: list[dict] = []
        if not steps and files:
            diagnostics.append({"level": "warn", "code": "missing_workflow_steps", "message": "任务有文件，但没有可识别的工作流步骤。"})
        if production.get("manifest_error"):
            diagnostics.append({"level": "error", "code": "invalid_production_manifest", "message": production["manifest_error"]})
        production_status = str(production.get("status") or "").lower()
        if cls._is_failed_production_status(production_status):
            diagnostics.append({"level": "error", "code": "production_failed", "message": f"自动生产失败：{production.get('status')}"})
        if summary.get("blocked_reason"):
            diagnostics.append({"level": "warn", "code": "task_blocked", "message": str(summary.get("blocked_reason"))})
        if task_state == "completed" and not any(file in {"long_video_final.mp4", "final_video.mp4"} for file in files):
            diagnostics.append({"level": "info", "code": "no_final_media", "message": "任务已有文本结果，但尚未发现最终视频文件。"})
        missing_outputs = [step["step"] for step in steps if step.get("status") in {"completed", "awaiting_confirmation"} and not step.get("has_output")]
        if missing_outputs:
            diagnostics.append({"level": "warn", "code": "missing_step_outputs", "message": f"步骤状态已完成但缺少 output.md：{missing_outputs}"})
        return diagnostics

    @classmethod
    def _task_state(cls, summary: dict, files: list[str], comfy_debug: dict | None = None) -> str:
        if summary.get("awaiting_confirmation"):
            return "awaiting_confirmation"
        summary_status = str(summary.get("status") or "").strip().lower()
        if summary_status in {"cancelled", "canceled"}:
            return "cancelled"
        blocked_reason = str(summary.get("blocked_reason") or "").strip()
        if blocked_reason:
            return "blocked"
        production_status = str(summary.get("production_status") or "").strip().lower()
        if production_status.startswith("awaiting_comfyui_"):
            debug_status = comfy_debug if isinstance(comfy_debug, dict) else {}
            if debug_status.get("enabled") and debug_status.get("complete"):
                return "partial"
            return "blocked"
        if production_status and cls._is_failed_production_status(production_status):
            return "failed"
        if (summary.get("final_output") or "final_output.md" in files) and summary:
            return "completed"
        if any(file.startswith("step_") for file in files):
            return "partial"
        return "empty"

    @staticmethod
    def _allowed_task_actions(task_state: str, summary: dict, files: list[str]) -> list[str]:
        actions = {"export"}
        if "final_output.md" in files or any(file.startswith("step_") for file in files):
            actions.add("rebuild_final")
        if any(file.startswith("step_") and file.endswith("/output.md") for file in files):
            actions.add("rerun_step")
        if task_state in {"awaiting_confirmation", "blocked", "failed", "partial", "cancelled"}:
            actions.add("resume")
        if summary.get("awaiting_confirmation"):
            actions.add("confirm_step")
            actions.add("cancel")
        if task_state in {"awaiting_confirmation", "blocked"}:
            actions.add("cancel")
        return sorted(actions)

    @staticmethod
    def _task_assets(task_dir: Path, files: list[str]) -> dict:
        images = []
        videos = []
        is_comfy_debug = task_dir.name == COMFY_DEBUG_TASK
        for file in files:
            path = task_dir / file
            if not path.is_file():
                continue
            if not WorkflowWebHandler._is_visible_media_asset(file, include_nested=is_comfy_debug):
                continue
            item = {
                "file": file,
                "name": Path(file).name,
                "label": WorkflowWebHandler._asset_label(file),
                "size": path.stat().st_size,
                "mtime": path.stat().st_mtime,
            }
            suffix = path.suffix.lower()
            if suffix in IMAGE_EXTENSIONS:
                images.append(item)
            elif suffix in VIDEO_EXTENSIONS:
                videos.append(item)
        images.sort(key=lambda item: WorkflowWebHandler._asset_sort_key(item["file"]))
        videos.sort(key=lambda item: WorkflowWebHandler._asset_sort_key(item["file"]))
        return {
            "images": images,
            "videos": videos,
            "counts": {"images": len(images), "videos": len(videos), "total": len(images) + len(videos)},
        }

    @staticmethod
    def _is_visible_media_asset(file: str, include_nested: bool = False) -> bool:
        name = str(file or "")
        if not name or name.startswith("export_package/") or name.startswith("step_"):
            return False
        suffix = Path(name).suffix.lower()
        if suffix not in MEDIA_EXTENSIONS:
            return False
        if include_nested:
            return True
        if name in {"long_video_final.mp4", "final_video.mp4"}:
            return True
        return name.startswith(("generated_images/", "video_clips/", "comfyui/"))

    @staticmethod
    def _asset_sort_key(file: str) -> str:
        name = str(file or "")
        order = [
            ("long_video_final.mp4", "00_"),
            ("final_video.mp4", "00_"),
            ("generated_images/", "10_"),
            ("video_clips/", "20_"),
            ("comfyui/", "60_"),
            ("reference_keyframe/", "65_"),
            ("identity_consistency/", "66_"),
            ("segment_i2v/", "67_"),
        ]
        for prefix, rank in order:
            if name == prefix or name.startswith(prefix):
                return rank + name
        return "99_" + name

    @staticmethod
    def _asset_label(file: str) -> str:
        name = str(file or "")
        if name in {"long_video_final.mp4", "final_video.mp4"}:
            return "最终视频"
        if name.startswith("generated_images/"):
            return f"图片素材 · {Path(name).name}"
        if name.startswith("video_clips/"):
            return f"视频素材 · {Path(name).name}"
        if name.startswith("comfyui/"):
            return f"ComfyUI 素材 · {Path(name).name}"
        if "comfyui_result" in Path(name).name or "/material_" in name:
            return f"ComfyUI 调试素材 · {Path(name).name}"
        return Path(name).name

    def _file_content(self, task: str, file_name: str) -> dict:
        target, _ = self._safe_task_file(task, file_name, must_exist=True)
        self._ensure_editable_file(target)
        return {"file": file_name, "content": target.read_text(encoding="utf-8", errors="replace")}

    def _send_media(self, task: str, file_name: str) -> None:
        target, _ = self._safe_task_file(task, file_name, must_exist=True)
        suffix = target.suffix.lower()
        allowed = {
            ".mp4",
            ".mov",
            ".webm",
            ".m4v",
            ".mp3",
            ".wav",
            ".aac",
            ".m4a",
            *IMAGE_EXTENSIONS,
        }
        if suffix not in allowed:
            raise ValueError(f"Unsupported media file type: {suffix}")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _asset_library(self) -> list[dict]:
        ASSET_LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
        items = self._read_asset_library_index()
        changed = False
        cleaned: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                changed = True
                continue
            asset_id = str(item.get("id") or "").strip()
            file_name = str(item.get("file") or "").strip()
            if not asset_id or not file_name:
                changed = True
                continue
            path = (ASSET_LIBRARY_ROOT / file_name).resolve()
            if not path.is_file() or not self._is_relative_to(path, ASSET_LIBRARY_ROOT):
                changed = True
                continue
            item = dict(item)
            item["size"] = path.stat().st_size
            item["mtime"] = path.stat().st_mtime
            suffix = path.suffix.lower()
            item["kind"] = "image" if suffix in IMAGE_EXTENSIONS else ("audio" if suffix in AUDIO_EXTENSIONS else "video")
            cleaned.append(item)
        cleaned.sort(key=lambda item: float(item.get("created_at") or item.get("mtime") or 0), reverse=True)
        if changed:
            self._write_asset_library_index(cleaned)
        return cleaned

    @classmethod
    def _task_comfy_debug_status(cls, task_dir: Path) -> dict:
        payload_path = task_dir / "comfyui" / "comfyui_payload.json"
        manifest_path = task_dir / "production_manifest.json"
        state_path = task_dir / "comfyui" / "manual_debug_state.json"
        manifest = cls._read_json_file(manifest_path)
        payload = cls._read_json_file(payload_path)
        state = cls._read_json_file(state_path)
        composition = manifest.get("composition") if isinstance(manifest.get("composition"), dict) else {}
        production_status = str(manifest.get("status") or "").strip()
        stage = str(composition.get("manual_debug_stage") or "").strip().lower()
        if not stage:
            if "image" in production_status:
                stage = "image"
            elif "video" in production_status:
                stage = "video"
            else:
                stage = "all"
        leaf_items = cls._task_comfy_debug_items(payload, state, stage=stage)
        items = cls._task_comfy_debug_groups(leaf_items)
        enabled = bool(items) and (
            production_status.startswith("awaiting_comfyui_")
            or production_status in {"comfyui_manual_approved", "comfyui_image_manual_approved", "comfyui_video_manual_approved"}
            or bool(composition.get("manual_debug_enabled"))
        )
        approved = len([item for item in items if item.get("status") == "approved"])
        current = next((item for item in items if item.get("status") != "approved"), None)
        return {
            "enabled": enabled,
            "state_file": str(state_path) if payload_path.is_file() else "",
            "payload_file": str(payload_path) if payload_path.is_file() else "",
            "total": len(items),
            "approved": approved,
            "complete": bool(items and approved >= len(items)),
            "stage": stage,
            "current_item_id": current.get("id") if current else "",
            "items": items,
        }

    @classmethod
    def _task_comfy_debug_items(cls, payload: dict, state: dict, stage: str = "all") -> list[dict]:
        if not isinstance(payload, dict):
            return []
        workflows = cls._comfy_debug_workflows()
        workflow_by_id = {str(item.get("id") or ""): item for item in workflows}
        workflow_order = {str(item.get("id") or ""): index for index, item in enumerate(workflows, 1)}
        state_items = state.get("items") if isinstance(state.get("items"), dict) else {}
        raw_items: list[dict] = []
        stage = str(stage or "all").strip().lower()
        source_specs = []
        if stage in {"all", "image"}:
            source_specs.append(("image_prompts", "01_base_asset_image", "image"))
        if stage in {"all", "video"}:
            source_specs.append(("video_prompts", "06_i2v_first_frame", "video"))
        for source_key, default_workflow, item_stage in source_specs:
            values = payload.get(source_key)
            if not isinstance(values, list):
                continue
            for source_index, raw in enumerate(values, 1):
                entry = raw if isinstance(raw, dict) else {"prompt": str(raw)}
                workflow_id = cls._infer_debug_workflow_id(entry, default_workflow, workflow_by_id)
                workflow_mode = str(entry.get("workflow_mode") or entry.get("image_task_mode") or entry.get("video_task_mode") or entry.get("task_type") or entry.get("asset_tag") or "").strip()
                source_id = str(entry.get("id") or entry.get("shot_id") or entry.get("scene_id") or f"{source_key}_{source_index:03d}").strip()
                item_id = f"{workflow_id}:{workflow_mode or 'default'}:{source_id}"
                item_state = state_items.get(item_id) if isinstance(state_items.get(item_id), dict) else {}
                run_id = str(item_state.get("run_id") or "")
                run_job = cls._run_status_optional(run_id) if run_id else {}
                status = str(item_state.get("status") or "pending")
                files = [str(file) for file in (item_state.get("files") or []) if file]
                item_error = str(item_state.get("error") or "")
                if status == "approved" and item_state.get("prompt_version") != 2:
                    status = "failed"
                    item_error = "该素材由旧版调试提示词生成，可能把用途说明画进图里，请重新运行当前项"
                if status == "running" and run_id and not run_job:
                    status = "failed"
                    item_error = item_error or "ComfyUI 调试运行状态已丢失，请重新运行当前项"
                elif run_job:
                    job_status = str(run_job.get("status") or "")
                    result_error = cls._comfy_debug_job_error(run_job)
                    result_files = cls._files_from_comfy_debug_job(run_job)
                    if status == "approved" and result_error:
                        status = "failed"
                        files = result_files
                        item_error = result_error
                    if job_status in {"queued", "running"} and status != "approved":
                        status = "running"
                    elif job_status == "completed" and status != "approved":
                        files = result_files
                        status = "failed" if result_error else "completed"
                        item_error = result_error
                    elif job_status == "failed" and status != "approved":
                        status = "failed"
                        item_error = result_error or str(run_job.get("error") or "")
                if status in {"approved", "completed", "success"} and not files:
                    status = "failed"
                    item_error = item_error or "ComfyUI 调试没有生成图片/视频素材"
                workflow = workflow_by_id.get(workflow_id) or {}
                raw_items.append(
                    {
                        "id": item_id,
                        "workflow_id": workflow_id,
                        "workflow_name": workflow.get("name") or workflow_id,
                        "workflow_mode": workflow_mode,
                        "asset_tag": str(entry.get("asset_tag") or ""),
                        "source": source_key,
                        "stage": item_stage,
                        "source_index": source_index,
                        "material_id": source_id,
                        "prompt": cls._prompt_from_material_item(entry),
                        "reference_image": cls._reference_from_material_item(entry),
                        "middle_frame_image": cls._middle_frame_from_material_item(entry),
                        "last_frame_image": cls._last_frame_from_material_item(entry),
                        "reference_images": cls._reference_images_from_material_item(entry),
                        "width": cls._debug_material_value(entry, "width"),
                        "height": cls._debug_material_value(entry, "height"),
                        "duration": cls._debug_material_value(entry, "duration") if item_stage == "video" else "",
                        "fps": cls._debug_material_value(entry, "fps") if item_stage == "video" else "",
                        "status": status,
                        "run_id": run_id,
                        "files": files,
                        "error": item_error,
                        "_workflow_order": cls._debug_queue_order(workflow_id, workflow_mode, entry, workflow_order),
                    }
                )
        cls._normalize_shared_debug_run_files(raw_items)
        raw_items.sort(key=lambda item: (item["_workflow_order"], item["source_index"], item["id"]))
        for index, item in enumerate(raw_items, 1):
            item["order"] = index
            item.pop("_workflow_order", None)
        return raw_items

    @staticmethod
    def _normalize_shared_debug_run_files(items: list[dict]) -> None:
        groups: dict[tuple[str, str, str, str], list[dict]] = {}
        for item in items:
            run_id = str(item.get("run_id") or "").strip()
            files = [str(file) for file in (item.get("files") or []) if file]
            if not run_id or len(files) <= 1:
                continue
            key = (
                run_id,
                str(item.get("workflow_id") or ""),
                str(item.get("workflow_mode") or ""),
                str(item.get("source") or ""),
            )
            groups.setdefault(key, []).append(item)
        for siblings in groups.values():
            if len(siblings) <= 1:
                continue
            siblings.sort(key=lambda item: (int(item.get("source_index") or 0), str(item.get("id") or "")))
            shared_files = [str(file) for file in (siblings[0].get("files") or []) if file]
            if len(shared_files) < len(siblings):
                continue
            if not all([str(file) for file in (item.get("files") or []) if file] == shared_files for item in siblings):
                continue
            for index, item in enumerate(siblings):
                item["files"] = [shared_files[index]]

    @staticmethod
    def _task_comfy_debug_groups(items: list[dict]) -> list[dict]:
        groups: list[dict] = []
        group_map: dict[str, dict] = {}
        status_rank = {"running": 0, "failed": 1, "pending": 2, "completed": 3, "success": 3, "approved": 4}
        for item in items:
            workflow_id = str(item.get("workflow_id") or "")
            workflow_mode = str(item.get("workflow_mode") or item.get("asset_tag") or "default")
            group_id = f"group:{workflow_id}:{workflow_mode}"
            group = group_map.get(group_id)
            if not group:
                group = {
                    "id": group_id,
                    "group": True,
                    "workflow_id": workflow_id,
                    "workflow_name": item.get("workflow_name") or workflow_id,
                    "workflow_mode": workflow_mode,
                    "asset_tag": item.get("asset_tag") or "",
                    "source": item.get("source") or "",
                    "stage": item.get("stage") or "",
                    "children": [],
                    "files": [],
                    "error": "",
                    "_sort": item.get("order") or 999,
                }
                group_map[group_id] = group
                groups.append(group)
            group["children"].append(item)
            group["files"].extend([file for file in (item.get("files") or []) if file])
        for index, group in enumerate(groups, 1):
            children = group.get("children") or []
            statuses = [str(child.get("status") or "pending") for child in children]
            errors = [str(child.get("error") or "") for child in children if child.get("error")]
            if any(status == "running" for status in statuses):
                status = "running"
            elif any(status == "failed" for status in statuses):
                status = "failed"
            elif children and all(status == "approved" for status in statuses):
                status = "approved"
            elif children and all(status in {"completed", "success", "approved"} for status in statuses):
                status = "completed"
            else:
                status = "pending"
            group["status"] = status
            group["error"] = "；".join(errors[:3])
            group["order"] = index
            group["child_count"] = len(children)
            group["completed_count"] = len([child for child in children if str(child.get("status") or "") in {"completed", "success", "approved"} or [file for file in (child.get("files") or []) if file]])
            group["approved_count"] = len([child for child in children if str(child.get("status") or "") == "approved"])
            group["file_count"] = len(group.get("files") or [])
            group.pop("_sort", None)
        groups.sort(key=lambda group: (group.get("children") or [{}])[0].get("order") or 999)
        for index, group in enumerate(groups, 1):
            group["order"] = index
        return groups

    @staticmethod
    def _debug_queue_order(workflow_id: str, workflow_mode: str, entry: dict, workflow_order: dict) -> int:
        key = str(workflow_mode or entry.get("asset_tag") or entry.get("task_type") or workflow_id or "").strip()
        preferred = {
            "01_base_asset_image": 10,
            "character_base": 10,
            "product_base": 11,
            "scene_base": 12,
            "02_turnaround": 20,
            "character_turnaround": 20,
            "product_turnaround": 21,
            "03_style_cover_image": 30,
            "style_reference": 30,
            "cover_key_visual": 31,
            "04_keyframe": 40,
            "keyframe": 40,
            "05_image_repair_cutout": 50,
            "image_inpaint_fix": 50,
            "background_remove": 51,
            "06_i2v": 60,
            "06_i2v_first_frame": 60,
            "i2v_first_frame": 60,
            "06_i2v_first_last_frame": 61,
            "i2v_first_last_frame": 61,
            "07_live_to_anime": 70,
            "live_to_anime": 70,
            "08_motion_transfer": 80,
            "motion_transfer": 80,
            "09_talking_image": 90,
            "talking_image": 90,
            "10_broll_transition_video": 100,
            "broll_scene_video": 100,
            "empty_transition_video": 101,
            "11_video_enhance": 110,
            "video_upscale": 110,
            "frame_interpolation": 111,
            "video_deflicker_stabilize": 112,
            "12_video_inpaint_fix": 120,
            "video_inpaint_fix": 120,
        }
        return preferred.get(key, preferred.get(workflow_id, workflow_order.get(workflow_id, 999)))

    @staticmethod
    def _read_json_file(path: Path) -> dict:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _infer_debug_workflow_id(entry: dict, default_workflow: str, workflow_by_id: dict) -> str:
        explicit = str(entry.get("workflow_id") or entry.get("workflow") or "").strip()
        if explicit in workflow_by_id:
            return explicit
        mode = str(entry.get("workflow_mode") or entry.get("image_task_mode") or entry.get("video_task_mode") or entry.get("task_type") or entry.get("asset_tag") or "").strip()
        for workflow_id, workflow in workflow_by_id.items():
            modes = workflow.get("modes") if isinstance(workflow.get("modes"), list) else []
            if any(isinstance(item, dict) and str(item.get("value") or "") == mode for item in modes):
                return workflow_id
        return explicit or default_workflow

    @staticmethod
    def _prompt_from_material_item(entry: dict) -> str:
        for key in ("positive", "positive_prompt", "image_prompt", "video_prompt", "visual_prompt", "prompt", "description"):
            value = str(entry.get(key) or "").strip()
            if value:
                return WorkflowWebHandler._clean_material_prompt(value)
        return json.dumps(entry, ensure_ascii=False)

    @staticmethod
    def _clean_material_prompt(value: str) -> str:
        text_value = str(value or "").strip()
        if not text_value:
            return ""
        try:
            parsed = json.loads(text_value)
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            for key in ("positive", "positive_prompt", "image_prompt", "video_prompt", "visual_prompt", "prompt", "description"):
                candidate = str(parsed.get(key) or "").strip()
                if candidate:
                    return candidate
        return text_value

    @staticmethod
    def _debug_material_value(entry: dict, key: str) -> str:
        if not isinstance(entry, dict):
            return ""
        value = entry.get(key)
        if value is None:
            return ""
        return str(value).strip()

    @classmethod
    def _debug_dimension_payload(cls, item: dict) -> dict:
        if not isinstance(item, dict):
            return {}
        result = {}
        for key in ("width", "height", "duration", "fps"):
            value = cls._debug_material_value(item, key)
            if value:
                result[key] = value
        return result

    @staticmethod
    def _reference_from_material_item(entry: dict) -> str:
        for key in ("reference_image", "first_frame_image", "reference_video", "reference_audio"):
            value = str(entry.get(key) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _last_frame_from_material_item(entry: dict) -> str:
        for key in ("last_frame_image", "end_frame_image"):
            value = str(entry.get(key) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _middle_frame_from_material_item(entry: dict) -> str:
        for key in ("middle_frame_image", "mid_frame_image", "middle_keyframe", "middle_frame"):
            value = str(entry.get(key) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _reference_images_from_material_item(entry: dict) -> list[dict | str]:
        if not isinstance(entry, dict):
            return []
        values = entry.get("reference_images")
        if isinstance(values, list):
            return [item for item in values if item]
        return []

    @staticmethod
    def _run_status_optional(run_id: str) -> dict:
        with RUN_JOBS_LOCK:
            job = RUN_JOBS.get(run_id)
            return json.loads(json.dumps(job, ensure_ascii=False)) if job else {}

    @staticmethod
    def _files_from_comfy_debug_job(job: dict) -> list[str]:
        files: list[str] = []
        for result in job.get("results") or []:
            if isinstance(result, dict):
                result_files = [str(file) for file in (result.get("files") or []) if file]
                if result_files and str(result.get("task") or "") != COMFY_DEBUG_TASK:
                    files.extend(result_files)
                    continue
                manifest = result.get("manifest") if isinstance(result.get("manifest"), dict) else {}
                downloaded = manifest.get("downloaded_files") if isinstance(manifest.get("downloaded_files"), list) else []
                if downloaded:
                    files.extend(str(file) for file in downloaded if file)
                else:
                    files.extend(str(COMFY_DEBUG_ROOT / str(file)) for file in (result.get("files") or []) if file)
        return files

    @staticmethod
    def _comfy_debug_job_error(job: dict) -> str:
        if not isinstance(job, dict):
            return ""
        if str(job.get("status") or "") == "failed" and job.get("error"):
            return str(job.get("error") or "")
        results = job.get("results") if isinstance(job.get("results"), list) else []
        if not results:
            return str(job.get("error") or "")
        errors = []
        has_success = False
        has_files = False
        for result in results:
            if not isinstance(result, dict):
                continue
            status = str(result.get("status") or "").strip().lower()
            files = [file for file in (result.get("files") or []) if file]
            if files:
                has_files = True
            if status in {"success", "completed", "partial_success"}:
                has_success = True
            if status in {"failed", "error"} or result.get("error"):
                errors.append(str(result.get("error") or status or "ComfyUI debug failed"))
        if errors:
            return "；".join(errors)
        if has_success and not has_files:
            return "ComfyUI 调试完成但没有下载到图片/视频素材"
        return ""

    @classmethod
    def _comfy_debug_workflows(cls) -> list[dict]:
        workflows = [dict(item) for item in COMFY_DEBUG_WORKFLOWS]
        seen = set()
        unique = []
        for item in workflows:
            item_id = str(item.get("id") or "").strip()
            if not item_id or item_id in seen:
                continue
            default_node_info = str(item.get("default_node_info") or "").strip()
            if not default_node_info or default_node_info == "[]":
                item["default_node_info"] = cls._default_comfy_debug_node_info(item_id)
            seen.add(item_id)
            unique.append(item)
        return unique

    @classmethod
    def _default_comfy_debug_node_info(cls, workflow_id: str) -> str:
        preset_map = {
            "01_character_base": "01_image_z_image_turbo/runninghub_node_info_list_preset.json",
            "02_product_base": "01_image_z_image_turbo/runninghub_node_info_list_preset.json",
            "03_scene_base": "01_image_z_image_turbo/runninghub_node_info_list_preset.json",
            "06_style_reference": "01_image_z_image_turbo/runninghub_node_info_list_preset.json",
            "07_keyframe": "04_keyframe_image/runninghub_node_info_list_preset.json",
            "08_cover_key_visual": "04_keyframe_image/runninghub_node_info_list_preset.json",
            "01_base_asset_image": "01_image_z_image_turbo/runninghub_node_info_list_preset.json",
            "02_turnaround": "01_image_z_image_turbo/runninghub_node_info_list_preset.json",
            "03_style_cover_image": "04_keyframe_image/runninghub_node_info_list_preset.json",
            "04_keyframe": "04_keyframe_image/runninghub_node_info_list_preset.json",
            "06_i2v_first_frame": "02_ltx_video_2_3/runninghub_node_info_list_preset.json",
            "06_i2v_first_last_frame": "06_i2v_first_last_frame_ltx_2_3/runninghub_node_info_list_preset.json",
            "06_i2v_first_middle_last_frame": "06_i2v_first_middle_last_frame_ltx_2_3/runninghub_node_info_list_preset.json",
            "10_broll_transition_video": "04_broll_material/runninghub_node_info_list_preset.json",
        }
        return cls._read_workflow_library_text(preset_map.get(str(workflow_id or "").strip(), ""))

    @staticmethod
    def _read_workflow_library_text(relative_path: object) -> str:
        path_text = str(relative_path or "").strip()
        if not path_text:
            return "[]"
        root = WORKSPACE_ROOT / "comfyui_workflows" / "workflow_library"
        target = (root / path_text).resolve()
        if not target.is_file() or not WorkflowWebHandler._is_relative_to(target, root):
            return "[]"
        return target.read_text(encoding="utf-8-sig", errors="replace").strip() or "[]"

    def _start_task_comfy_debug(self, payload: dict) -> dict:
        task_name = str(payload.get("task") or "").strip()
        item_id = str(payload.get("item_id") or "").strip()
        if not task_name or not item_id:
            raise ValueError("task and item_id are required")
        task_dir = self._safe_task_dir(task_name)
        status = self._task_comfy_debug_status(task_dir)
        items = status.get("items") if isinstance(status.get("items"), list) else []
        current_id = str(status.get("current_item_id") or "")
        item = next((entry for entry in items if isinstance(entry, dict) and entry.get("id") == item_id), None)
        if not item:
            raise ValueError("ComfyUI debug item not found")
        item_status = str(item.get("status") or "").lower()
        can_rerun = item_status in {"failed", "completed", "success", "approved"}
        if item_id != current_id and not can_rerun:
            raise ValueError("请按 ComfyUI 调试队列顺序运行当前项")
        if item.get("group"):
            return self._start_task_comfy_debug_group(task_dir, status, item, payload)
        debug_payload = {
            "workflows": [item.get("workflow_id")],
            "workflow_mode": item.get("workflow_mode") or "",
            "prompt": item.get("prompt") or "",
            "material_id": item.get("material_id") or item_id,
            "reference_image": self._resolve_task_comfy_debug_reference(task_dir, status, item.get("reference_image") or ""),
            "middle_frame_image": self._resolve_task_comfy_debug_reference(task_dir, status, item.get("middle_frame_image") or ""),
            "last_frame_image": self._resolve_task_comfy_debug_reference(task_dir, status, item.get("last_frame_image") or ""),
            "reference_images": self._resolve_task_comfy_debug_references(task_dir, status, item.get("reference_images") or []),
            "api_key": str(payload.get("api_key") or "").strip(),
            "base_url": str(payload.get("base_url") or "").strip(),
            "workflow_library": payload.get("workflow_library") if isinstance(payload.get("workflow_library"), list) else [],
            "output_task": task_name,
            "output_subdir": "comfyui/manual_debug",
        }
        debug_payload.update(self._debug_dimension_payload(item))
        if not debug_payload["prompt"]:
            raise ValueError("当前调试项缺少 prompt")
        job = self._start_comfy_debug(debug_payload)
        self._update_task_comfy_debug_state(
            task_dir,
            item_id,
            {
                "status": "running",
                "run_id": job.get("run_id") or "",
                "started_at": time.time(),
                "workflow_id": item.get("workflow_id") or "",
                "workflow_mode": item.get("workflow_mode") or "",
                "prompt_version": 2,
                "files": [],
                "error": "",
            },
        )
        return job

    def _start_task_comfy_debug_group(self, task_dir: Path, status: dict, group: dict, payload: dict) -> dict:
        children = [child for child in (group.get("children") or []) if isinstance(child, dict)]
        runnable = [
            child
            for child in children
            if str(child.get("status") or "").lower() in {"pending", "failed", "completed", "success", "approved"}
        ]
        if not runnable:
            raise ValueError("当前组没有可运行的调试项")
        self._delete_task_comfy_debug_group_files(task_dir, children)
        run_id = "comfy_debug_group_" + uuid4().hex
        now = time.time()
        job = {
            "run_id": run_id,
            "status": "queued",
            "workflow": COMFY_DEBUG_TASK,
            "workflow_name": group.get("workflow_name") or group.get("workflow_id") or "ComfyUI Group",
            "task_title": "ComfyUI Debug Group",
            "task_name": task_dir.name,
            "created_at": now,
            "updated_at": now,
            "total_steps": len(runnable),
            "completed_steps": 0,
            "current_step": 1,
            "current_message": "ComfyUI debug group queued",
            "steps": [],
            "cancel_requested": False,
            "pause_requested": False,
            "debug_type": "comfy_debug",
            "active_workflow_id": group.get("workflow_id") or "",
            "result": None,
            "results": [],
            "error": "",
        }
        with RUN_JOBS_LOCK:
            RUN_JOBS[run_id] = job
        for child in runnable:
            self._update_task_comfy_debug_state(
                task_dir,
                str(child.get("id") or ""),
                {
                    "status": "running",
                    "run_id": run_id,
                    "started_at": now,
                    "workflow_id": child.get("workflow_id") or "",
                    "workflow_mode": child.get("workflow_mode") or "",
                    "prompt_version": 2,
                    "files": [],
                    "error": "",
                },
            )
        worker = threading.Thread(target=self._run_task_comfy_debug_group_job, args=(run_id, task_dir, status, runnable, payload), daemon=True)
        worker.start()
        return json.loads(json.dumps(job, ensure_ascii=False))

    @staticmethod
    def _delete_task_comfy_debug_group_files(task_dir: Path, items: list[dict]) -> None:
        task_root = task_dir.resolve()
        for item in items:
            for file in item.get("files") or []:
                try:
                    target = (task_dir / str(file)).resolve()
                    if target.is_file() and WorkflowWebHandler._is_relative_to(target, task_root):
                        target.unlink()
                except Exception:
                    continue

    def _run_task_comfy_debug_group_job(self, run_id: str, task_dir: Path, status: dict, items: list[dict], payload: dict) -> None:
        all_results: list[dict] = []
        errors: list[str] = []
        for index, item in enumerate(items, 1):
            item_id = str(item.get("id") or "")
            self._update_job(
                run_id,
                {
                    "status": "running",
                    "current_step": index,
                    "completed_steps": index - 1,
                    "current_message": f"Running ComfyUI debug item {index}/{len(items)}",
                },
            )
            debug_payload = {
                "workflows": [item.get("workflow_id")],
                "workflow_mode": item.get("workflow_mode") or "",
                "prompt": item.get("prompt") or "",
                "material_id": item.get("material_id") or item_id,
                "reference_image": self._resolve_task_comfy_debug_reference(task_dir, status, item.get("reference_image") or ""),
                "middle_frame_image": self._resolve_task_comfy_debug_reference(task_dir, status, item.get("middle_frame_image") or ""),
                "last_frame_image": self._resolve_task_comfy_debug_reference(task_dir, status, item.get("last_frame_image") or ""),
                "reference_images": self._resolve_task_comfy_debug_references(task_dir, status, item.get("reference_images") or []),
                "api_key": str(payload.get("api_key") or "").strip(),
                "base_url": str(payload.get("base_url") or "").strip(),
                "workflow_library": payload.get("workflow_library") if isinstance(payload.get("workflow_library"), list) else [],
                "output_task": task_dir.name,
                "output_subdir": "comfyui/manual_debug",
            }
            debug_payload.update(self._debug_dimension_payload(item))
            try:
                result = self._run_comfy_debug(debug_payload)
                result_job = {"status": "completed", "results": result.get("results", []) if isinstance(result, dict) else []}
                result_error = self._comfy_debug_job_error(result_job)
                files = self._files_from_comfy_debug_job(result_job)
                friendly_files: list[str] = []
                for result_entry in result.get("results", []) if isinstance(result, dict) else []:
                    if not isinstance(result_entry, dict):
                        continue
                    for asset in result_entry.get("friendly_assets") or []:
                        if isinstance(asset, dict) and asset.get("friendly_file"):
                            friendly_files.append(str(asset.get("friendly_file") or ""))
                all_results.extend(result.get("results", []) if isinstance(result, dict) else [])
                self._update_task_comfy_debug_state(
                    task_dir,
                    item_id,
                    {
                        "status": "failed" if result_error else "completed",
                        "run_id": run_id,
                        "completed_at": time.time(),
                        "files": files,
                        "friendly_files": friendly_files,
                        "error": result_error,
                        "workflow_id": item.get("workflow_id") or "",
                        "workflow_mode": item.get("workflow_mode") or "",
                        "prompt_version": 2,
                    },
                )
                if result_error:
                    errors.append(f"{item_id}: {result_error}")
            except Exception as exc:
                errors.append(f"{item_id}: {exc}")
                self._update_task_comfy_debug_state(
                    task_dir,
                    item_id,
                    {
                        "status": "failed",
                        "run_id": run_id,
                        "completed_at": time.time(),
                        "files": [],
                        "error": str(exc),
                        "workflow_id": item.get("workflow_id") or "",
                        "workflow_mode": item.get("workflow_mode") or "",
                        "prompt_version": 2,
                    },
                )
        final_error = "；".join(errors[:5])
        self._update_job(
            run_id,
            {
                "status": "failed" if final_error else "completed",
                "completed_steps": len(items) - len(errors),
                "current_step": len(items),
                "current_message": final_error or "ComfyUI debug group completed",
                "results": all_results,
                "result": {"ok": not bool(final_error), "task": task_dir.name, "results": all_results},
                "error": final_error,
            },
        )

    def _confirm_task_comfy_debug(self, payload: dict) -> dict:
        task_name = str(payload.get("task") or "").strip()
        item_id = str(payload.get("item_id") or "").strip()
        if not task_name or not item_id:
            raise ValueError("task and item_id are required")
        task_dir = self._safe_task_dir(task_name)
        status = self._task_comfy_debug_status(task_dir)
        items = status.get("items") if isinstance(status.get("items"), list) else []
        current_id = str(status.get("current_item_id") or "")
        if item_id != current_id:
            raise ValueError("请按 ComfyUI 调试队列顺序确认当前项")
        item = next((entry for entry in items if isinstance(entry, dict) and entry.get("id") == item_id), None)
        if not item:
            raise ValueError("ComfyUI debug item not found")
        if item.get("group"):
            children = [child for child in (item.get("children") or []) if isinstance(child, dict)]
            if not children:
                raise ValueError("当前组没有调试项")
            not_ready = [child for child in children if str(child.get("status") or "") not in {"completed", "success", "approved"} or child.get("error") or not [file for file in (child.get("files") or []) if file]]
            if not_ready:
                raise ValueError("当前组还有未成功生成素材的调试项，不能确认满意")
            for child in children:
                self._update_task_comfy_debug_state(
                    task_dir,
                    str(child.get("id") or ""),
                    {
                        "status": "approved",
                        "approved_at": time.time(),
                        "run_id": child.get("run_id") or "",
                        "files": child.get("files") or [],
                        "workflow_id": child.get("workflow_id") or "",
                        "workflow_mode": child.get("workflow_mode") or "",
                        "prompt_version": 2,
                    },
                )
            return self._task_comfy_debug_status(task_dir)
        if str(item.get("status") or "") not in {"completed", "success", "approved"}:
            raise ValueError("当前调试项尚未成功完成，不能确认满意")
        if item.get("error"):
            raise ValueError(f"当前调试项失败：{item.get('error')}")
        if not [file for file in (item.get("files") or []) if file]:
            raise ValueError("当前调试项没有生成图片/视频素材，不能确认满意")
        self._update_task_comfy_debug_state(
            task_dir,
            item_id,
            {
                "status": "approved",
                "approved_at": time.time(),
                "run_id": item.get("run_id") or "",
                "files": item.get("files") or [],
                "workflow_id": item.get("workflow_id") or "",
                "workflow_mode": item.get("workflow_mode") or "",
                "prompt_version": 2,
            },
        )
        return self._task_comfy_debug_status(task_dir)

    @staticmethod
    def _update_task_comfy_debug_state(task_dir: Path, item_id: str, patch: dict) -> None:
        state_path = task_dir / "comfyui" / "manual_debug_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig")) if state_path.is_file() else {}
        except Exception:
            state = {}
        if not isinstance(state, dict):
            state = {}
        items = state.setdefault("items", {})
        if not isinstance(items, dict):
            items = {}
            state["items"] = items
        current = items.get(item_id) if isinstance(items.get(item_id), dict) else {}
        current.update(patch)
        items[item_id] = current
        state["updated_at"] = time.time()
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _resolve_task_comfy_debug_reference(task_dir: Path, status: dict, reference_text: str) -> str:
        text_value = str(reference_text or "").strip()
        if not text_value:
            return ""
        parts = [part.strip() for part in re.split(r"[,，;；\n]+", text_value) if part.strip()]
        if not parts:
            return text_value
        items = status.get("items") if isinstance(status.get("items"), list) else []
        resolved: list[str] = []
        for part in parts:
            match = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict)
                    and item.get("status") == "approved"
                    and (
                        str(item.get("material_id") or "") == part
                        or str(item.get("id") or "") == part
                        or str(item.get("id") or "").endswith(":" + part)
                    )
                ),
                None,
            )
            files = [str(file) for file in ((match or {}).get("files") or []) if file]
            if files:
                candidate = task_dir / files[0]
                resolved.append(str(candidate if candidate.is_file() else files[0]))
            else:
                alias = WorkflowWebHandler._task_relative_reference_alias(task_dir, part)
                resolved.append(alias or part)
        return ", ".join(resolved)

    @staticmethod
    def _task_relative_reference_alias(task_dir: Path, value: str) -> str:
        text = str(value or "").strip()
        if not text or text.startswith(("http://", "https://", "data:image/")):
            return ""
        normalized = text.replace("\\", "/").lstrip("/")
        aliases = [normalized]
        if normalized.startswith("comfyui_manual_debug/"):
            aliases.append("comfyui/manual_debug/" + normalized[len("comfyui_manual_debug/") :])
        if normalized.startswith("comfyui/manual_debug/"):
            aliases.append("comfyui_manual_debug/" + normalized[len("comfyui/manual_debug/") :])
        for candidate_text in aliases:
            candidate = (task_dir / candidate_text).resolve()
            try:
                if candidate.is_file() and WorkflowWebHandler._is_relative_to(candidate, task_dir.resolve()):
                    return str(candidate)
            except OSError:
                continue
        return ""

    @classmethod
    def _resolve_task_comfy_debug_references(cls, task_dir: Path, status: dict, references: list) -> list[dict | str]:
        if not isinstance(references, list):
            return []
        resolved: list[dict | str] = []
        for item in references:
            if isinstance(item, str):
                value = cls._resolve_task_comfy_debug_reference(task_dir, status, item)
                if value:
                    resolved.append(value)
                continue
            if isinstance(item, dict):
                copied = dict(item)
                source_value = ""
                source_key = ""
                for key in ("image", "reference_image", "path", "file", "url"):
                    value = str(copied.get(key) or "").strip()
                    if value:
                        source_value = value
                        source_key = key
                        break
                if source_value:
                    copied[source_key or "image"] = cls._resolve_task_comfy_debug_reference(task_dir, status, source_value)
                    resolved.append(copied)
        return resolved

    def _start_comfy_debug(self, payload: dict) -> dict:
        workflow_ids = payload.get("workflows") if isinstance(payload.get("workflows"), list) else []
        selected_ids = [str(item).strip() for item in workflow_ids if str(item).strip()]
        if not selected_ids:
            raise ValueError("Please select one ComfyUI debug workflow")
        if not str(payload.get("prompt") or "").strip():
            raise ValueError("prompt is required")
        run_id = "comfy_debug_" + uuid4().hex
        workflow_name = selected_ids[0]
        workflows = {item["id"]: item for item in self._comfy_debug_workflows()}
        if selected_ids[0] in workflows:
            workflow_name = str(workflows[selected_ids[0]].get("name") or workflow_name)
        now = time.time()
        job = {
            "run_id": run_id,
            "status": "queued",
            "workflow": COMFY_DEBUG_TASK,
            "workflow_name": workflow_name,
            "task_title": "ComfyUI Debug",
            "task_name": COMFY_DEBUG_TASK,
            "created_at": now,
            "updated_at": now,
            "started_at": 0,
            "finished_at": 0,
            "elapsed_seconds": 0,
            "total_steps": 1,
            "completed_steps": 0,
            "current_step": 1,
            "current_message": "ComfyUI debug job queued",
            "steps": [
                {
                    "step": 1,
                    "status": "pending",
                    "agent_id": "comfy_debug",
                    "agent_name": workflow_name,
                    "message": "Waiting to call RunningHub / ComfyUI",
                }
            ],
            "cancel_requested": False,
            "pause_requested": False,
            "debug_type": "comfy_debug",
            "active_workflow_id": selected_ids[0],
            "result": None,
            "results": [],
            "error": "",
        }
        with RUN_JOBS_LOCK:
            RUN_JOBS[run_id] = job
        worker = threading.Thread(target=self._run_comfy_debug_job, args=(run_id, payload), daemon=True)
        worker.start()
        return json.loads(json.dumps(job, ensure_ascii=False))

    def _run_comfy_debug_job(self, run_id: str, payload: dict) -> None:
        started_at = time.time()
        self._update_job(
            run_id,
            {
                "status": "running",
                "started_at": started_at,
                "finished_at": 0,
                "elapsed_seconds": 0,
                "current_message": "Calling RunningHub / ComfyUI",
                "steps": [
                    {
                        "step": 1,
                        "status": "active",
                        "agent_id": "comfy_debug",
                        "agent_name": "ComfyUI Debug",
                        "message": "Waiting for RunningHub / ComfyUI result",
                    }
                ],
            },
        )
        try:
            result = self._run_comfy_debug(payload)
            result_job = {"status": "completed", "results": result.get("results", []) if isinstance(result, dict) else []}
            result_error = self._comfy_debug_job_error(result_job)
            finished_at = time.time()
            elapsed_seconds = round(float(result.get("elapsed_seconds", finished_at - started_at)) if isinstance(result, dict) else finished_at - started_at, 1)
            self._update_job(
                run_id,
                {
                    "status": "failed" if result_error else "completed",
                    "completed_steps": 0 if result_error else 1,
                    "finished_at": finished_at,
                    "elapsed_seconds": elapsed_seconds,
                    "current_message": result_error or "ComfyUI debug completed",
                    "result": result,
                    "results": result.get("results", []) if isinstance(result, dict) else [],
                    "error": result_error,
                    "steps": [
                        {
                            "step": 1,
                            "status": "error" if result_error else "done",
                            "agent_id": "comfy_debug",
                            "agent_name": "ComfyUI Debug",
                            "message": result_error or "Completed",
                        }
                    ],
                },
            )
        except Exception as exc:
            finished_at = time.time()
            self._update_job(
                run_id,
                {
                    "status": "failed",
                    "completed_steps": 0,
                    "finished_at": finished_at,
                    "elapsed_seconds": round(finished_at - started_at, 1),
                    "current_message": str(exc),
                    "error": str(exc),
                    "steps": [
                        {
                            "step": 1,
                            "status": "error",
                            "agent_id": "comfy_debug",
                            "agent_name": "ComfyUI Debug",
                            "message": str(exc),
                        }
                    ],
                },
            )

    def _run_comfy_debug(self, payload: dict) -> dict:
        started_at = time.time()
        workflow_ids = payload.get("workflows") if isinstance(payload.get("workflows"), list) else []
        selected_ids = [str(item).strip() for item in workflow_ids if str(item).strip()]
        if not selected_ids:
            raise ValueError("请选择至少一个调试工作流")
        api_key = str(payload.get("api_key") or "").strip()
        base_url = str(payload.get("base_url") or "").strip() or "https://www.runninghub.cn/openapi/v2"
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("prompt is required")
        endpoint_override = str(payload.get("endpoint") or "").strip()
        node_info_override = str(payload.get("node_info_list_json") or "").strip()
        poll_timeout = self._safe_int(payload.get("poll_timeout_seconds"), default=3600, minimum=60, maximum=7200)
        reference_image = str(payload.get("reference_image") or "").strip()
        middle_frame_image = str(payload.get("middle_frame_image") or payload.get("mid_frame_image") or "").strip()
        last_frame_image = str(payload.get("last_frame_image") or "").strip()
        mask_image = str(payload.get("input_mask_image") or payload.get("mask_image") or "").strip()
        audio_file = str(payload.get("input_audio_file") or payload.get("audio_file") or "").strip()
        reference_images_input = payload.get("reference_images") if isinstance(payload.get("reference_images"), list) else []
        has_any_reference = bool(reference_image or middle_frame_image or last_frame_image or reference_images_input)
        seed = str(payload.get("seed") or "").strip()
        width = str(payload.get("width") or "").strip()
        height = str(payload.get("height") or "").strip()
        duration = str(payload.get("duration") or "").strip()
        fps = str(payload.get("fps") or "").strip()
        frame_count = str(payload.get("frame_count") or payload.get("frames") or "").strip()
        negative_prompt = str(payload.get("negative_prompt") or "").strip()
        task_type_override = str(payload.get("task_type") or "").strip()
        control_mode_override = str(payload.get("control_mode") or "").strip()
        image_task_mode = str(payload.get("image_task_mode") or "").strip()
        video_task_mode = str(payload.get("video_task_mode") or "").strip()
        workflow_mode = str(payload.get("workflow_mode") or "").strip()
        asset_tag_override = str(payload.get("asset_tag") or "").strip()
        workflows = {item["id"]: item for item in self._comfy_debug_workflows()}
        frontend_library = payload.get("workflow_library") if isinstance(payload.get("workflow_library"), list) else []
        for library_item in frontend_library:
            if not isinstance(library_item, dict):
                continue
            library_id = str(library_item.get("id") or "").strip()
            if not library_id:
                continue
            target = workflows.get(library_id)
            if not target:
                continue
            endpoint_value = str(library_item.get("endpoint") or "").strip()
            node_info_value = str(library_item.get("nodeInfoList") or library_item.get("node_info_list_json") or "").strip()
            poll_timeout_value = str(library_item.get("pollTimeout") or library_item.get("poll_timeout_seconds") or "").strip()
            image_task_type_value = str(library_item.get("defaultImageTaskType") or library_item.get("default_image_task_type") or "").strip()
            workflow_mode_value = str(library_item.get("defaultWorkflowMode") or library_item.get("default_workflow_mode") or "").strip()
            width_value = str(library_item.get("width") or library_item.get("defaultWidth") or library_item.get("default_width") or "").strip()
            height_value = str(library_item.get("height") or library_item.get("defaultHeight") or library_item.get("default_height") or "").strip()
            duration_value = str(library_item.get("duration") or library_item.get("defaultDuration") or library_item.get("default_duration") or "").strip()
            fps_value = str(library_item.get("fps") or library_item.get("defaultFps") or library_item.get("default_fps") or "").strip()
            mode_configs = library_item.get("modeConfigs") or library_item.get("mode_configs")
            mode_config = mode_configs.get(workflow_mode) if isinstance(mode_configs, dict) and isinstance(mode_configs.get(workflow_mode), dict) else None
            if mode_config:
                endpoint_value = str(mode_config.get("endpoint") or endpoint_value).strip()
                node_info_value = str(mode_config.get("nodeInfoList") or mode_config.get("node_info_list_json") or node_info_value).strip()
                poll_timeout_value = str(mode_config.get("pollTimeout") or mode_config.get("poll_timeout_seconds") or poll_timeout_value).strip()
                width_value = str(mode_config.get("defaultWidth") or mode_config.get("default_width") or width_value).strip()
                height_value = str(mode_config.get("defaultHeight") or mode_config.get("default_height") or height_value).strip()
                duration_value = str(mode_config.get("defaultDuration") or mode_config.get("default_duration") or duration_value).strip()
                fps_value = str(mode_config.get("defaultFps") or mode_config.get("default_fps") or fps_value).strip()
            if endpoint_value:
                target["default_endpoint"] = endpoint_value
            if node_info_value and node_info_value != "[]":
                target["default_node_info"] = node_info_value
            if poll_timeout_value:
                target["poll_timeout_seconds"] = poll_timeout_value
            if image_task_type_value:
                target["default_image_task_type"] = image_task_type_value
            if workflow_mode_value:
                target["default_workflow_mode"] = workflow_mode_value
            if width_value:
                target["default_width"] = width_value
            if height_value:
                target["default_height"] = height_value
            if duration_value:
                target["default_duration"] = duration_value
            if fps_value:
                target["default_fps"] = fps_value
        run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid4().hex[:8]
        output_task_name = str(payload.get("output_task") or "").strip()
        output_task_dir = self._safe_task_dir(output_task_name) if output_task_name else None
        output_subdir = str(payload.get("output_subdir") or "comfyui/manual_debug").strip().strip("/\\") or "comfyui/manual_debug"
        output_subdir_path = Path(*[part for part in Path(output_subdir).parts if part not in {"", "."}])
        if output_subdir_path.is_absolute() or ".." in output_subdir_path.parts:
            raise ValueError("invalid output_subdir")
        if output_task_dir:
            run_root = (output_task_dir / output_subdir_path / run_id).resolve()
            if not self._is_relative_to(run_root, output_task_dir):
                raise ValueError("invalid output_subdir")
            result_file_root = output_task_dir
            result_task_name = output_task_name
        else:
            run_root = COMFY_DEBUG_ROOT / run_id
            result_file_root = COMFY_DEBUG_ROOT
            result_task_name = COMFY_DEBUG_TASK
        run_root.mkdir(parents=True, exist_ok=True)
        results = []
        image_task_modes = {
            "character_generation": ("character_generation", "none", False),
            "product_generation": ("product_generation", "none", False),
            "scene_generation": ("scene_generation", "none", False),
            "character_turnaround": ("character_turnaround", "character_reference", True),
            "product_turnaround": ("product_turnaround", "product_reference", True),
            "keyframe": ("keyframe", "none", False),
            "cover_key_visual": ("cover_key_visual", "style_reference", False),
            "style_reference": ("style_reference", "none", False),
            "inpaint_fix": ("inpaint_fix", "mask_inpaint", True),
        }

        for workflow_id in selected_ids:
            item = workflows.get(workflow_id)
            if not item:
                results.append({"id": workflow_id, "name": workflow_id, "status": "failed", "error": "Unknown workflow", "files": []})
                continue
            endpoint = endpoint_override or str(item.get("default_endpoint") or "").strip()
            node_info = node_info_override or str(item.get("default_node_info") or "").strip() or "[]"
            output_dir = run_root / self._safe_asset_stem(workflow_id)
            output_dir.mkdir(parents=True, exist_ok=True)
            config = {
                "provider": "runninghub",
                "workflow_endpoint": endpoint,
                "node_info_list_json": node_info,
                "poll_timeout_seconds": poll_timeout,
                "tool": "runninghub",
                "instance_type": "default",
                "loop_material_prompts": False,
                "workflow_preset_id": workflow_id,
                "workflow_preset_name": item.get("name") or workflow_id,
            }
            task_type = str(item.get("default_task_type") or "").strip()
            control_mode = str(item.get("default_control_mode") or "").strip()
            job_type = "video" if str(item.get("type") or "").lower() == "video" else "image"
            mode = workflow_mode or image_task_mode or video_task_mode or str(item.get("default_workflow_mode") or item.get("default_image_task_type") or "").strip()
            mode_item = None
            if isinstance(item.get("modes"), list):
                mode_item = next((entry for entry in item["modes"] if isinstance(entry, dict) and str(entry.get("value") or "") == mode), None)
                if mode_item:
                    task_type = str(mode_item.get("task_type") or task_type)
                    control_mode = str(mode_item.get("control_mode") or control_mode)
                    if mode_item.get("requires_reference") and not has_any_reference:
                        raise ValueError(f"{mode} requires reference_image")
                    required_inputs = mode_item.get("required_inputs") if isinstance(mode_item.get("required_inputs"), list) else []
                    if "input_mask_image" in required_inputs and not mask_image:
                        raise ValueError(f"{mode} requires input_mask_image")
                    if "input_audio_file" in required_inputs and not audio_file:
                        raise ValueError(f"{mode} requires input_audio_file")
            if job_type == "image" and mode in image_task_modes:
                task_type, control_mode, requires_reference = image_task_modes[mode]
                if requires_reference and not has_any_reference:
                    raise ValueError(f"{mode} requires reference_image")
            if mode == "i2v_first_middle_last_frame" or workflow_id == "06_i2v_first_middle_last_frame":
                if not reference_image or not middle_frame_image or not last_frame_image:
                    raise ValueError("i2v_first_middle_last_frame requires reference_image, middle_frame_image and last_frame_image")
            elif mode == "i2v_first_last_frame" or workflow_id == "06_i2v_first_last_frame":
                if not reference_image or not last_frame_image:
                    raise ValueError("i2v_first_last_frame requires reference_image and last_frame_image")
            if task_type_override:
                task_type = task_type_override
            if control_mode_override:
                control_mode = control_mode_override
            if workflow_id == "04_keyframe" or mode == "keyframe":
                reference_image = ""
                middle_frame_image = ""
                last_frame_image = ""
                reference_images_input = []
            request_payload = {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "reference_image": reference_image,
                "middle_frame_image": middle_frame_image,
                "last_frame_image": last_frame_image,
                "input_base_image": reference_image,
                "input_middle_frame": middle_frame_image,
                "input_last_frame": last_frame_image,
                "input_mask_image": mask_image,
                "input_audio_file": audio_file,
                "reference_images": reference_images_input,
                "seed": seed,
                "width": width or item.get("default_width") or "",
                "height": height or item.get("default_height") or "",
                "task_type": task_type,
                "control_mode": control_mode,
                "image_task_mode": mode,
                "video_task_mode": mode if job_type == "video" else "",
                "workflow_mode": mode,
                "asset_tag": asset_tag_override or str((mode_item or {}).get("asset_tag") or item.get("asset_tag") or workflow_id),
                "image_task_type": task_type if job_type == "image" else "",
                "video_task_type": task_type if job_type == "video" else "",
            }
            if job_type == "video":
                request_payload["video_prompt"] = prompt
                request_payload["duration"] = duration or item.get("default_duration") or ""
                request_payload["fps"] = fps or item.get("default_fps") or ""
                request_payload["frame_count"] = frame_count
            else:
                request_payload["image_prompt"] = prompt
            try:
                adapter = CloudComfyUIAdapter(base_url, api_key, endpoint, progress_callback=None)
                search_dirs = [run_root]
                if output_task_dir:
                    search_dirs.append(output_task_dir)
                search_dirs.extend([COMFY_DEBUG_ROOT, OUTPUT_ROOT, WORKSPACE_ROOT])
                adapter._reference_search_dirs = search_dirs  # type: ignore[attr-defined]
                uploaded_reference_images = []
                for ref_item in reference_images_input:
                    ref_value = ""
                    if isinstance(ref_item, str):
                        ref_value = ref_item.strip()
                    elif isinstance(ref_item, dict):
                        for key in ("image", "reference_image", "path", "file", "url"):
                            value = str(ref_item.get(key) or "").strip()
                            if value:
                                ref_value = value
                                break
                    if ref_value:
                        ref_value = self._ensure_comfy_safe_reference_file(ref_value)
                        uploaded_reference_images.append(adapter._reference_image_value(ref_value))  # type: ignore[attr-defined]
                if not uploaded_reference_images and reference_image:
                    safe_reference_image = self._ensure_comfy_safe_reference_file(reference_image)
                    uploaded_reference_images.append(adapter._reference_image_value(safe_reference_image))  # type: ignore[attr-defined]
                if middle_frame_image:
                    safe_middle_frame_image = self._ensure_comfy_safe_reference_file(middle_frame_image)
                    uploaded_middle_frame = adapter._reference_image_value(safe_middle_frame_image)  # type: ignore[attr-defined]
                    if uploaded_middle_frame and uploaded_middle_frame not in uploaded_reference_images:
                        uploaded_reference_images.append(uploaded_middle_frame)
                if last_frame_image:
                    safe_last_frame_image = self._ensure_comfy_safe_reference_file(last_frame_image)
                    uploaded_last_frame = adapter._reference_image_value(safe_last_frame_image)  # type: ignore[attr-defined]
                    if uploaded_last_frame and uploaded_last_frame not in uploaded_reference_images:
                        uploaded_reference_images.append(uploaded_last_frame)
                if mask_image:
                    safe_mask_image = self._ensure_comfy_safe_reference_file(mask_image)
                    request_payload["input_mask_image"] = adapter._reference_image_value(safe_mask_image)  # type: ignore[attr-defined]
                if audio_file:
                    safe_audio_file = self._ensure_comfy_safe_reference_file(audio_file)
                    request_payload["input_audio_file"] = adapter._reference_media_value(safe_audio_file)  # type: ignore[attr-defined]
                if uploaded_reference_images:
                    uploaded_reference = uploaded_reference_images[0]
                    request_payload["reference_image"] = uploaded_reference
                    request_payload["reference_images"] = uploaded_reference_images
                    if middle_frame_image and len(uploaded_reference_images) > 1:
                        request_payload["middle_frame_image"] = uploaded_reference_images[1]
                    if last_frame_image and len(uploaded_reference_images) > 1:
                        request_payload["last_frame_image"] = uploaded_reference_images[-1]
                manifest = adapter.run(request_payload, config, output_dir)
                downloaded = [Path(path) for path in manifest.get("downloaded_files", []) if path]
                files = []
                for path in downloaded:
                    try:
                        resolved = path.resolve()
                        if resolved.is_file() and self._is_relative_to(resolved, result_file_root):
                            files.append(resolved.relative_to(result_file_root).as_posix())
                    except OSError:
                        continue
                library_assets = self._auto_favorite_comfy_debug_assets(
                    result_task_name,
                    files,
                    item.get("name") or workflow_id,
                    request_payload.get("asset_tag") or item.get("asset_tag") or workflow_id,
                )
                friendly_assets = self._sync_task_generated_assets(
                    result_task_name,
                    files,
                    workflow_id=workflow_id,
                    workflow_name=item.get("name") or workflow_id,
                    asset_tag=request_payload.get("asset_tag") or item.get("asset_tag") or workflow_id,
                    material_id=str(payload.get("material_id") or "").strip(),
                    run_id=run_id,
                )
                results.append(
                    {
                        "id": workflow_id,
                        "name": item.get("name") or workflow_id,
                        "type": item.get("type") or "",
                        "asset_tag": request_payload.get("asset_tag") or item.get("asset_tag") or workflow_id,
                        "status": manifest.get("status", "unknown"),
                        "endpoint": endpoint,
                        "task": result_task_name,
                        "files": files,
                        "library_assets": library_assets,
                        "friendly_assets": friendly_assets,
                        "manifest": manifest,
                    }
                )
            except Exception as exc:
                error_manifest = {
                    "id": workflow_id,
                    "name": item.get("name") or workflow_id,
                    "status": "failed",
                    "endpoint": endpoint,
                    "error": str(exc),
                }
                (output_dir / "debug_error.json").write_text(json.dumps(error_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                results.append({**error_manifest, "type": item.get("type") or "", "asset_tag": asset_tag_override or item.get("asset_tag") or workflow_id, "task": result_task_name, "files": []})

        finished_at = time.time()
        elapsed_seconds = round(finished_at - started_at, 1)
        manifest = {
            "run_id": run_id,
            "created_at": started_at,
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_seconds": elapsed_seconds,
            "results": results,
        }
        (run_root / "comfy_debug_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "ok": True,
            "run_id": run_id,
            "task": result_task_name,
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_seconds": elapsed_seconds,
            "results": results,
        }

    def _favorite_asset(self, payload: dict) -> dict:
        task = str(payload.get("task") or "").strip()
        file_name = str(payload.get("file") or "").strip()
        label = str(payload.get("label") or "").strip()
        tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
        source_path, _ = self._safe_task_file(task, file_name, must_exist=True)
        suffix = source_path.suffix.lower()
        if suffix not in MEDIA_EXTENSIONS:
            raise ValueError(f"Unsupported asset type: {suffix}")
        ASSET_LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
        clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
        folder_name = next((ASSET_LIBRARY_TAG_FOLDERS[tag] for tag in clean_tags if tag in ASSET_LIBRARY_TAG_FOLDERS), "uncategorized")
        target_dir = (ASSET_LIBRARY_ROOT / folder_name).resolve()
        if not self._is_relative_to(target_dir, ASSET_LIBRARY_ROOT):
            raise ValueError("Invalid asset library folder")
        target_dir.mkdir(parents=True, exist_ok=True)
        items = [existing for existing in self._read_asset_library_index() if isinstance(existing, dict)]
        for existing in items:
            if str(existing.get("source_task") or "") == task and str(existing.get("source_file") or "") == file_name:
                return {"ok": True, "asset": existing, "duplicate": True}
        asset_id = uuid4().hex
        safe_stem = self._safe_asset_stem(Path(file_name).stem or "asset")
        library_name = f"{asset_id}_{safe_stem}{suffix}"
        relative_library_name = f"{folder_name}/{library_name}"
        target = (target_dir / library_name).resolve()
        if not self._is_relative_to(target, ASSET_LIBRARY_ROOT):
            raise ValueError("Invalid asset path")
        shutil.copy2(source_path, target)
        item = {
            "id": asset_id,
            "file": relative_library_name,
            "name": label or self._asset_label(file_name),
            "source_task": task,
            "source_file": file_name,
            "kind": "image" if suffix in IMAGE_EXTENSIONS else ("audio" if suffix in AUDIO_EXTENSIONS else "video"),
            "tags": clean_tags,
            "created_at": time.time(),
            "size": target.stat().st_size,
            "mtime": target.stat().st_mtime,
        }
        items.insert(0, item)
        self._write_asset_library_index(items)
        return {"ok": True, "asset": item}

    def _auto_favorite_comfy_debug_assets(self, task: str, files: list[str], workflow_name: str, asset_tag: str) -> list[dict]:
        assets: list[dict] = []
        clean_tag = str(asset_tag or "comfy_debug").strip() or "comfy_debug"
        for file_name in files:
            suffix = Path(str(file_name or "")).suffix.lower()
            if suffix not in MEDIA_EXTENSIONS:
                continue
            kind_tag = "image" if suffix in IMAGE_EXTENSIONS else "video"
            label = f"{workflow_name} · {Path(file_name).name}"
            try:
                result = self._favorite_asset(
                    {
                        "task": task,
                        "file": file_name,
                        "label": label,
                        "tags": [kind_tag, clean_tag],
                    }
                )
                asset = result.get("asset") if isinstance(result, dict) else None
                if isinstance(asset, dict):
                    assets.append(asset)
            except Exception as exc:
                assets.append(
                    {
                        "source_task": task,
                        "source_file": file_name,
                        "error": str(exc),
                    }
                )
        return assets

    def _sync_task_generated_assets(
        self,
        task: str,
        files: list[str],
        *,
        workflow_id: str,
        workflow_name: str,
        asset_tag: str,
        material_id: str = "",
        run_id: str = "",
    ) -> list[dict]:
        if not task or task == COMFY_DEBUG_TASK:
            return []
        try:
            task_dir = self._safe_task_dir(task)
        except Exception:
            return []
        task_root = task_dir.resolve()
        index_root = (task_dir / "assets" / "generated").resolve()
        if not self._is_relative_to(index_root, task_root):
            return []
        copied: list[dict] = []
        index_items = self._read_task_generated_asset_index(index_root)
        seen_sources = {
            str(item.get("source_file") or "")
            for item in index_items
            if isinstance(item, dict)
        }
        for file_name in files:
            relative_file = str(file_name or "").strip().replace("\\", "/")
            suffix = Path(relative_file).suffix.lower()
            if not relative_file or suffix not in MEDIA_EXTENSIONS:
                continue
            try:
                source = (task_dir / relative_file).resolve()
            except OSError:
                continue
            if not source.is_file() or not self._is_relative_to(source, task_root):
                continue
            kind = "image" if suffix in IMAGE_EXTENSIONS else "video"
            category = self._generated_asset_category(kind, asset_tag, workflow_id)
            folder = (index_root / category).resolve()
            if not self._is_relative_to(folder, index_root):
                continue
            folder.mkdir(parents=True, exist_ok=True)
            source_hash = hashlib.sha1(relative_file.encode("utf-8", errors="ignore")).hexdigest()[:8]
            label_parts = [
                material_id,
                asset_tag,
                Path(relative_file).stem,
                source_hash,
            ]
            safe_name = self._safe_asset_stem("_".join(part for part in label_parts if part))
            target = (folder / f"{safe_name}{suffix}").resolve()
            if not self._is_relative_to(target, index_root):
                continue
            if source != target:
                shutil.copy2(source, target)
            friendly_file = target.relative_to(task_dir).as_posix()
            entry = {
                "friendly_file": friendly_file,
                "source_file": relative_file,
                "kind": kind,
                "category": category,
                "workflow_id": workflow_id,
                "workflow_name": workflow_name,
                "asset_tag": asset_tag,
                "material_id": material_id,
                "run_id": run_id,
                "size": target.stat().st_size,
                "mtime": target.stat().st_mtime,
                "synced_at": time.time(),
            }
            copied.append(entry)
            if relative_file not in seen_sources:
                index_items.insert(0, entry)
                seen_sources.add(relative_file)
            else:
                for existing in index_items:
                    if isinstance(existing, dict) and str(existing.get("source_file") or "") == relative_file:
                        existing.update(entry)
                        break
        if copied:
            self._write_task_generated_asset_index(index_root, index_items)
            self._write_task_generated_asset_readme(index_root)
        return copied

    @staticmethod
    def _generated_asset_category(kind: str, asset_tag: str, workflow_id: str) -> str:
        clean_tag = WorkflowWebHandler._safe_asset_stem(asset_tag or workflow_id or "other").lower()
        tag_aliases = {
            "character_generation": "character_base",
            "product_generation": "product_base",
            "scene_generation": "scene_base",
            "character_reference": "character_turnaround",
            "product_reference": "product_turnaround",
            "first_frame": "i2v_first_frame",
            "first_last_frame": "i2v_first_last_frame",
            "first_middle_last_frame": "i2v_first_middle_last_frame",
            "img2video": "i2v_first_frame",
            "first_last_frame_video": "i2v_first_last_frame",
            "first_middle_last_frame_video": "i2v_first_middle_last_frame",
            "txt2video": "broll_scene_video",
            "transition_video": "empty_transition_video",
        }
        clean_tag = tag_aliases.get(clean_tag, clean_tag)
        folder = ASSET_LIBRARY_TAG_FOLDERS.get(clean_tag, clean_tag)
        if folder.startswith("07_keyframe"):
            return "images/07_keyframe"
        if kind == "image":
            return f"images/{folder}"
        return f"videos/{folder}"

    @staticmethod
    def _read_task_generated_asset_index(index_root: Path) -> list[dict]:
        path = index_root / "asset_index.json"
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            return []
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return [item for item in data["items"] if isinstance(item, dict)]
        return []

    @staticmethod
    def _write_task_generated_asset_index(index_root: Path, items: list[dict]) -> None:
        index_root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated_at": time.time(),
            "items": items,
        }
        (index_root / "asset_index.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _write_task_generated_asset_readme(index_root: Path) -> None:
        text = (
            "# Generated Assets\n\n"
            "This folder is a human-friendly copy of generated task media.\n"
            "Original debug files stay in their source folders so task state remains valid.\n\n"
            "- images/: generated still assets and keyframes\n"
            "- videos/: generated video clips, first-frame clips, first/last-frame clips, and transitions\n"
            "- asset_index.json: mapping from friendly files back to original source files\n"
        )
        (index_root / "README.md").write_text(text, encoding="utf-8")

    def _import_asset(self, payload: dict) -> dict:
        filename = str(payload.get("filename") or "").strip()
        content_base64 = str(payload.get("content_base64") or "").strip()
        if not filename or not content_base64:
            raise ValueError("filename and content_base64 are required")
        suffix = Path(filename).suffix.lower()
        if suffix not in MEDIA_EXTENSIONS:
            raise ValueError(f"Unsupported asset type: {suffix}")
        file_bytes = base64.b64decode(content_base64, validate=True)
        if suffix in IMAGE_EXTENSIONS:
            file_bytes, suffix = self._normalize_comfy_image_bytes(file_bytes, suffix)
        tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
        clean_tags: list[str] = []
        for tag in tags:
            value = str(tag or "").strip()
            if value and value not in clean_tags:
                clean_tags.append(value)
        if not any(tag in {"image", "video", "audio"} for tag in clean_tags):
            clean_tags.insert(0, "image" if suffix in IMAGE_EXTENSIONS else ("audio" if suffix in AUDIO_EXTENSIONS else "video"))
        folder_name = next((ASSET_LIBRARY_TAG_FOLDERS[tag] for tag in clean_tags if tag in ASSET_LIBRARY_TAG_FOLDERS), "uncategorized")
        ASSET_LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
        target_dir = (ASSET_LIBRARY_ROOT / folder_name).resolve()
        if not self._is_relative_to(target_dir, ASSET_LIBRARY_ROOT):
            raise ValueError("Invalid asset library folder")
        target_dir.mkdir(parents=True, exist_ok=True)
        asset_id = uuid4().hex
        safe_stem = self._safe_asset_stem(Path(filename).stem or "asset")
        library_name = f"{asset_id}_{safe_stem}{suffix}"
        target = (target_dir / library_name).resolve()
        if not self._is_relative_to(target, ASSET_LIBRARY_ROOT):
            raise ValueError("Invalid asset path")
        target.write_bytes(file_bytes)
        item = {
            "id": asset_id,
            "file": f"{folder_name}/{library_name}",
            "name": str(payload.get("name") or "").strip() or Path(filename).stem or filename,
            "source_task": "",
            "source_file": filename,
            "kind": "image" if suffix in IMAGE_EXTENSIONS else ("audio" if suffix in AUDIO_EXTENSIONS else "video"),
            "tags": clean_tags,
            "note": str(payload.get("note") or "").strip(),
            "created_at": time.time(),
            "updated_at": time.time(),
            "size": target.stat().st_size,
            "mtime": target.stat().st_mtime,
        }
        items = [existing for existing in self._read_asset_library_index() if isinstance(existing, dict)]
        items.insert(0, item)
        self._write_asset_library_index(items)
        return {"ok": True, "asset": item}

    def _update_asset_metadata(self, payload: dict) -> dict:
        asset_id = str(payload.get("id") or "").strip()
        if not asset_id:
            raise ValueError("Missing asset id")
        tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
        name = str(payload.get("name") or "").strip()
        note = str(payload.get("note") or "").strip()
        clean_tags: list[str] = []
        for tag in tags:
            value = str(tag or "").strip()
            if value and value not in clean_tags:
                clean_tags.append(value)
        items = [existing for existing in self._read_asset_library_index() if isinstance(existing, dict)]
        updated: dict | None = None
        for item in items:
            if str(item.get("id") or "") == asset_id:
                old_file = str(item.get("file") or "").strip()
                if name:
                    item["name"] = name
                item["tags"] = clean_tags
                item["note"] = note
                item["updated_at"] = time.time()
                folder_name = next((ASSET_LIBRARY_TAG_FOLDERS[tag] for tag in clean_tags if tag in ASSET_LIBRARY_TAG_FOLDERS), "uncategorized")
                if old_file:
                    old_path = (ASSET_LIBRARY_ROOT / old_file).resolve()
                    if old_path.is_file() and self._is_relative_to(old_path, ASSET_LIBRARY_ROOT):
                        target_dir = (ASSET_LIBRARY_ROOT / folder_name).resolve()
                        if not self._is_relative_to(target_dir, ASSET_LIBRARY_ROOT):
                            raise ValueError("Invalid asset library folder")
                        target_dir.mkdir(parents=True, exist_ok=True)
                        new_path = (target_dir / old_path.name).resolve()
                        if old_path != new_path:
                            shutil.move(str(old_path), str(new_path))
                            item["file"] = f"{folder_name}/{old_path.name}"
                            item["size"] = new_path.stat().st_size
                            item["mtime"] = new_path.stat().st_mtime
                updated = item
                break
        if updated is None:
            raise FileNotFoundError(asset_id)
        self._write_asset_library_index(items)
        return {"ok": True, "asset": updated}

    def _unfavorite_asset(self, payload: dict) -> dict:
        asset_id = str(payload.get("id") or "").strip()
        task = str(payload.get("task") or "").strip()
        file_name = str(payload.get("file") or "").strip()
        items = [existing for existing in self._read_asset_library_index() if isinstance(existing, dict)]
        removed: list[dict] = []
        kept: list[dict] = []
        for item in items:
            matches_id = asset_id and str(item.get("id") or "") == asset_id
            matches_source = task and file_name and str(item.get("source_task") or "") == task and str(item.get("source_file") or "") == file_name
            if matches_id or matches_source:
                removed.append(item)
            else:
                kept.append(item)
        if not removed:
            return {"ok": True, "removed": 0}
        for item in removed:
            library_file = str(item.get("file") or "").strip()
            if not library_file:
                continue
            target = (ASSET_LIBRARY_ROOT / library_file).resolve()
            if target.is_file() and self._is_relative_to(target, ASSET_LIBRARY_ROOT):
                try:
                    target.unlink()
                except OSError:
                    pass
        self._write_asset_library_index(kept)
        return {"ok": True, "removed": len(removed), "assets": removed}

    def _send_asset_library_media(self, asset_id: str) -> None:
        asset_id = str(asset_id or "").strip()
        item = next((entry for entry in self._asset_library() if str(entry.get("id") or "") == asset_id), None)
        if not item:
            raise FileNotFoundError(asset_id)
        target = (ASSET_LIBRARY_ROOT / str(item.get("file") or "")).resolve()
        if not target.is_file() or not self._is_relative_to(target, ASSET_LIBRARY_ROOT):
            raise FileNotFoundError(asset_id)
        suffix = target.suffix.lower()
        if suffix not in MEDIA_EXTENSIONS:
            raise ValueError(f"Unsupported media file type: {suffix}")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_reference_media(self, file_name: str) -> None:
        name = str(file_name or "").replace("\\", "/").strip().lstrip("/")
        if name.startswith("my_workspace/"):
            name = name[len("my_workspace/") :]
        if name.startswith("my_reference_images/"):
            name = name[len("my_reference_images/") :]
        target = (REFERENCE_ROOT / name).resolve()
        if not target.is_file() or not self._is_relative_to(target, REFERENCE_ROOT):
            raise FileNotFoundError(file_name)
        suffix = target.suffix.lower()
        if suffix not in MEDIA_EXTENSIONS:
            raise ValueError(f"Unsupported reference media file type: {suffix}")
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _read_asset_library_index() -> list[dict]:
        if not ASSET_LIBRARY_INDEX.is_file():
            return []
        try:
            data = json.loads(ASSET_LIBRARY_INDEX.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    @staticmethod
    def _write_asset_library_index(items: list[dict]) -> None:
        ASSET_LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
        ASSET_LIBRARY_INDEX.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _safe_asset_stem(value: str) -> str:
        text = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", str(value or "").strip(), flags=re.UNICODE).strip("._")
        return (text or "asset")[:80]

    @staticmethod
    def _safe_int(value: object, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(float(str(value).strip()))
        except (TypeError, ValueError):
            number = default
        return max(minimum, min(maximum, number))

    @staticmethod
    def _normalize_comfy_image_bytes(file_bytes: bytes, suffix: str) -> tuple[bytes, str]:
        suffix = str(suffix or "").lower()
        if suffix not in IMAGE_EXTENSIONS or suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return file_bytes, suffix
        with Image.open(BytesIO(file_bytes)) as image:
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
            buffer = BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue(), ".png"

    def _ensure_comfy_safe_reference_file(self, value: str) -> str:
        text = str(value or "").strip()
        if not text or text.startswith(("http://", "https://", "data:image/")):
            return text
        normalized = text.replace("\\", "/").lstrip("/")
        if normalized.startswith("comfyui_manual_debug/"):
            normalized = "comfyui/manual_debug/" + normalized[len("comfyui_manual_debug/") :]
        if normalized.startswith("my_workspace/"):
            normalized = normalized[len("my_workspace/") :]
        candidate = (WORKSPACE_ROOT / normalized).resolve()
        if not candidate.is_file() or not self._is_relative_to(candidate, WORKSPACE_ROOT):
            return text
        suffix = candidate.suffix.lower()
        if suffix not in IMAGE_EXTENSIONS or suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return text
        converted_dir = REFERENCE_ROOT / "comfy_debug" / "converted"
        converted_dir.mkdir(parents=True, exist_ok=True)
        target = converted_dir / f"{self._safe_asset_stem(candidate.stem)[:80]}_{uuid4().hex[:8]}.png"
        converted_bytes, converted_suffix = self._normalize_comfy_image_bytes(candidate.read_bytes(), suffix)
        if converted_suffix != ".png":
            return text
        target.write_bytes(converted_bytes)
        return target.relative_to(WORKSPACE_ROOT).as_posix()

    def _save_file(self, payload: dict) -> dict:
        task = str(payload.get("task") or "").strip()
        file_name = str(payload.get("file") or "").strip()
        content = str(payload.get("content") or "")
        target, task_dir = self._safe_task_file(task, file_name, must_exist=True)
        self._ensure_editable_file(target)
        target.write_text(content.rstrip() + "\n", encoding="utf-8")
        return {"ok": True, "file": target.relative_to(task_dir).as_posix()}

    def _rebuild_final_output(self, task: str) -> dict:
        task_dir = self._safe_task_dir(task)
        workflow_path = task_dir / "workflow.json"
        input_path = task_dir / "input.md"
        if not workflow_path.is_file():
            raise FileNotFoundError("workflow.json")
        if not input_path.is_file():
            raise FileNotFoundError("input.md")
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        user_input = input_path.read_text(encoding="utf-8", errors="replace")
        step_outputs = WorkflowEngine._collect_step_outputs(workflow, task_dir)
        final_output = WorkflowEngine._build_final_output(workflow, user_input, step_outputs)
        final_path = task_dir / "final_output.md"
        final_path.write_text(final_output, encoding="utf-8")
        return {"ok": True, "file": final_path.relative_to(task_dir).as_posix()}

    def _rerun_step(self, payload: dict) -> dict:
        task = str(payload.get("task") or "").strip()
        step = int(payload.get("step") or 0)
        if step <= 0:
            raise ValueError("step is required")
        task_dir = self._safe_task_dir(task)
        summary = {}
        summary_path = task_dir / "run_summary.json"
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                summary = {}
        workflow = {}
        workflow_path = task_dir / "workflow.json"
        if workflow_path.exists():
            try:
                workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                workflow = {}
        total_steps = len(workflow.get("steps", [])) if isinstance(workflow.get("steps"), list) else 0
        run_id = uuid4().hex
        job = {
            "run_id": run_id,
            "status": "queued",
            "workflow": summary.get("workflow") or task,
            "task_title": summary.get("task_title") or "",
            "workflow_name": summary.get("workflow") or task,
            "task_dir": str(task_dir),
            "task_name": task_dir.name,
            "created_at": time.time(),
            "updated_at": time.time(),
            "total_steps": total_steps,
            "completed_steps": max(0, step - 1),
            "steps": [
                {"step": step_no, "status": "done" if step_no < step else "active" if step_no == step else "pending", "agent_id": "", "agent_name": ""}
                for step_no in range(1, total_steps + 1)
            ],
            "cancel_requested": False,
            "pause_requested": False,
            "rerun": True,
            "rerun_step": step,
        }
        with RUN_JOBS_LOCK:
            RUN_JOBS[run_id] = job

        worker = threading.Thread(
            target=self._run_rerun_step_job,
            args=(
                run_id,
                task_dir,
                step,
                str(payload.get("provider") or "auto").strip(),
                str(payload.get("model") or "").strip() or None,
                str(payload.get("api_key") or "").strip() or None,
                str(payload.get("base_url") or "").strip() or None,
                int(payload.get("timeout") or 0) or None,
            ),
            daemon=True,
        )
        worker.start()
        return job

    def _resume_task(self, payload: dict) -> dict:
        task = str(payload.get("task") or "").strip()
        task_dir = self._safe_task_dir(task)
        production_config = payload.get("production_config") or {}
        if isinstance(production_config, dict):
            production_image_config = production_config.get("image_config")
            if isinstance(production_image_config, dict):
                production_image_config["api_key"] = str(payload.get("image_api_key") or "").strip()
                production_image_config["base_url"] = str(payload.get("image_base_url") or "").strip()
            production_video_config = production_config.get("video_config")
            if isinstance(production_video_config, dict):
                production_video_config["api_key"] = str(payload.get("video_api_key") or "").strip()
                production_video_config["base_url"] = str(payload.get("video_base_url") or "").strip()
            production_compose_config = production_config.get("compose_config")
            if isinstance(production_compose_config, dict):
                production_compose_config["api_key"] = str(payload.get("comfy_api_key") or "").strip()
                production_compose_config["base_url"] = str(payload.get("comfy_base_url") or "").strip()
        summary = {}
        summary_path = task_dir / "run_summary.json"
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                summary = {}
        debug_status = self._task_comfy_debug_status(task_dir)
        if debug_status.get("enabled") and not debug_status.get("complete"):
            total = int(debug_status.get("total") or 0)
            approved = int(debug_status.get("approved") or 0)
            raise ValueError(f"ComfyUI 调试队列尚未全部确认（{approved}/{total}）。请先运行并确认下方调试队列，再继续主流程。")
        run_id = uuid4().hex
        job = {
            "run_id": run_id,
            "status": "queued",
            "workflow": summary.get("workflow") or task,
            "task_title": summary.get("task_title") or "",
            "workflow_name": summary.get("workflow") or task,
            "task_dir": str(task_dir),
            "task_name": task_dir.name,
            "created_at": time.time(),
            "updated_at": time.time(),
            "total_steps": 0,
            "completed_steps": 0,
            "steps": [],
            "cancel_requested": False,
            "pause_requested": False,
        }
        with RUN_JOBS_LOCK:
            RUN_JOBS[run_id] = job

        worker = threading.Thread(
            target=self._run_resume_job,
            args=(
                run_id,
                task_dir,
                production_config if isinstance(production_config, dict) else {},
                str(payload.get("provider") or "auto").strip(),
                str(payload.get("model") or "").strip() or None,
                str(payload.get("api_key") or "").strip() or None,
                str(payload.get("base_url") or "").strip() or None,
                int(payload.get("timeout") or 0) or None,
            ),
            daemon=True,
        )
        worker.start()
        return job

    def _retry_production_job(self, payload: dict) -> dict:
        task = str(payload.get("task") or "").strip()
        retry_job = str(payload.get("job_id") or payload.get("job") or "").strip()
        if not retry_job:
            raise ValueError("job or job_id is required")
        task_dir = self._safe_task_dir(task)
        production_config = payload.get("production_config") or {}
        if isinstance(production_config, dict):
            production_image_config = production_config.get("image_config")
            if isinstance(production_image_config, dict):
                production_image_config["api_key"] = str(payload.get("image_api_key") or "").strip()
                production_image_config["base_url"] = str(payload.get("image_base_url") or "").strip()
            production_video_config = production_config.get("video_config")
            if isinstance(production_video_config, dict):
                production_video_config["api_key"] = str(payload.get("video_api_key") or "").strip()
                production_video_config["base_url"] = str(payload.get("video_base_url") or "").strip()
            production_compose_config = production_config.get("compose_config")
            if isinstance(production_compose_config, dict):
                production_compose_config["api_key"] = str(payload.get("comfy_api_key") or "").strip()
                production_compose_config["base_url"] = str(payload.get("comfy_base_url") or "").strip()

        summary = {}
        summary_path = task_dir / "run_summary.json"
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                summary = {}

        run_id = uuid4().hex
        job = {
            "run_id": run_id,
            "status": "queued",
            "workflow": summary.get("workflow") or task,
            "task_title": summary.get("task_title") or "",
            "workflow_name": summary.get("workflow") or task,
            "task_dir": str(task_dir),
            "task_name": task_dir.name,
            "created_at": time.time(),
            "updated_at": time.time(),
            "total_steps": 0,
            "completed_steps": 0,
            "steps": [],
            "cancel_requested": False,
            "pause_requested": False,
            "production_retry": True,
            "production_retry_job": retry_job,
            "current_message": f"准备重试生产任务：{retry_job}",
        }
        with RUN_JOBS_LOCK:
            RUN_JOBS[run_id] = job

        worker = threading.Thread(
            target=self._run_retry_production_job,
            args=(run_id, task_dir, retry_job, production_config if isinstance(production_config, dict) else {}),
            daemon=True,
        )
        worker.start()
        return job

    def _export_task(self, payload: dict) -> dict:
        task = str(payload.get("task") or "").strip()
        template = str(payload.get("template") or "").strip()
        task_dir = self._safe_task_dir(task)
        export_dir = task_dir / "export_package"
        export_dir.mkdir(parents=True, exist_ok=True)

        summary = {}
        summary_path = task_dir / "run_summary.json"
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                summary = {}
        workflow_name = str(summary.get("workflow") or "")
        if not template:
            template = self._infer_export_template(workflow_name)

        final_output = self._read_task_text(task_dir, "final_output.md")
        input_text = self._read_task_text(task_dir, "input.md")
        step_outputs = self._read_all_step_outputs(task_dir)
        files = self._write_export_files(export_dir, template, workflow_name, input_text, final_output, step_outputs)
        return {
            "ok": True,
            "template": template,
            "export_dir": export_dir.relative_to(task_dir).as_posix(),
            "files": [path.relative_to(task_dir).as_posix() for path in files],
        }

    @staticmethod
    def _ensure_editable_file(path: Path) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "text/plain"
        editable_suffixes = {".json", ".md", ".txt", ".csv", ".srt", ".log"}
        if not content_type.startswith("text/") and path.suffix.lower() not in editable_suffixes:
            raise ValueError(f"Unsupported file type: {path.name}")

    @staticmethod
    def _infer_export_template(workflow_name: str) -> str:
        if "长视频" in workflow_name:
            return "long_video"
        if "小红书" in workflow_name:
            return "xiaohongshu"
        if "游戏" in workflow_name or "Steam" in workflow_name:
            return "game_steam"
        if "软件市场" in workflow_name:
            return "software_market"
        if "员工" in workflow_name or "平台" in workflow_name:
            return "agent_platform"
        return "long_video"

    @staticmethod
    def _read_task_text(task_dir: Path, relative: str) -> str:
        path = task_dir / relative
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    @staticmethod
    def _read_all_step_outputs(task_dir: Path) -> list[dict]:
        outputs = []
        for path in sorted(task_dir.glob("step_*/output.md")):
            step_match = path.parent.name.split("_", 2)
            outputs.append(
                {
                    "step": step_match[1] if len(step_match) > 1 else "",
                    "agent": step_match[2] if len(step_match) > 2 else path.parent.name,
                    "file": path.relative_to(task_dir).as_posix(),
                    "content": path.read_text(encoding="utf-8", errors="replace"),
                }
            )
        return outputs

    def _write_export_files(
        self,
        export_dir: Path,
        template: str,
        workflow_name: str,
        input_text: str,
        final_output: str,
        step_outputs: list[dict],
    ) -> list[Path]:
        written: list[Path] = []

        def write(name: str, content: str) -> None:
            path = export_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content.rstrip() + "\n", encoding="utf-8")
            written.append(path)

        manifest = {
            "template": template,
            "workflow": workflow_name,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "files": [],
        }

        write("README.md", self._export_readme(template, workflow_name))
        write("final_output.md", final_output or "# 最终输出\n\n暂无 final_output.md。\n")

        if template in {"short_video", "long_video"}:
            write("视频制作包.md", final_output)
            write("字幕.srt", self._extract_srt_from_text(final_output))
            write("镜头清单.csv", self._shot_csv(step_outputs))
            write("生图提示词.json", json.dumps(self._prompt_json(step_outputs, "06_"), ensure_ascii=False, indent=2))
            write("视频提示词.json", json.dumps(self._prompt_json(step_outputs, "07_"), ensure_ascii=False, indent=2))
            write("语音字幕制作包.md", self._agent_output_text(step_outputs, "20_"))
            write("ComfyUI生图参数包.json", json.dumps(self._prompt_json(step_outputs, "06_"), ensure_ascii=False, indent=2))
            write("ComfyUI生视频参数包.json", json.dumps(self._prompt_json(step_outputs, "07_"), ensure_ascii=False, indent=2))
            write("剪辑成片执行方案.md", self._agent_output_text(step_outputs, "22_"))
        elif template == "xiaohongshu":
            write("小红书文案.md", final_output)
            write("标题列表.txt", self._extract_lines(final_output, ["标题", "选题"]))
            write("封面文案.txt", self._extract_lines(final_output, ["封面"]))
            write("发布检查清单.md", self._checklist("小红书图文"))
        elif template == "game_steam":
            write("GDD.md", final_output)
            write("Unity开发任务清单.md", self._extract_lines(final_output, ["Unity", "开发", "任务", "架构"]))
            write("Steam商店页文案.md", self._extract_lines(final_output, ["Steam", "商店", "愿望单"]))
            write("测试发行清单.md", self._checklist("Steam 游戏"))
        elif template == "software_market":
            write("软件机会排行榜.md", final_output)
            write("MVP验证计划.md", self._extract_lines(final_output, ["MVP", "验证", "获客", "风险"]))
            write("商业化假设.md", self._extract_lines(final_output, ["商业化", "定价", "付费"]))
        elif template == "agent_platform":
            write("产品需求文档.md", final_output)
            write("员工管理方案.md", self._extract_lines(final_output, ["员工", "管理", "权限"]))
            write("工作流架构.md", self._extract_lines(final_output, ["工作流", "状态机", "上下文"]))
            write("技术落地清单.md", self._extract_lines(final_output, ["技术", "架构", "API", "本地"]))
        else:
            write("产品包.md", final_output)

        write("原始需求.md", input_text)
        manifest["files"] = [path.name for path in written]
        write("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        return written

    @staticmethod
    def _export_readme(template: str, workflow_name: str) -> str:
        return "\n".join(
            [
                "# 产品导出包",
                "",
                f"- 类型：{template}",
                f"- 工作流：{workflow_name or '未知'}",
                "- 用途：把工作流输出整理成可继续制作、复制或交付的文件。",
                "",
                "建议先检查 `final_output.md`，再按具体产品类型查看拆分文件。",
            ]
        )

    @staticmethod
    def _extract_srt_from_text(text: str) -> str:
        import re

        match = re.search(r"```srt\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip() + "\n"
        return "1\n00:00:00,000 --> 00:00:03,000\n请根据视频制作包补充字幕。\n"

    @staticmethod
    def _shot_csv(step_outputs: list[dict]) -> str:
        rows = ['step,agent,file,summary']
        for item in step_outputs:
            summary = " ".join(str(item.get("content", "")).split())[:160].replace('"', '""')
            rows.append(f'{item.get("step","")},{item.get("agent","")},{item.get("file","")},"{summary}"')
        return "\n".join(rows)

    @staticmethod
    def _prompt_json(step_outputs: list[dict], agent_prefix: str) -> list[dict]:
        return [
            {
                "step": item.get("step"),
                "agent": item.get("agent"),
                "source_file": item.get("file"),
                "content": item.get("content", ""),
            }
            for item in step_outputs
            if str(item.get("agent", "")).startswith(agent_prefix)
        ]

    @staticmethod
    def _agent_output_text(step_outputs: list[dict], agent_prefix: str) -> str:
        for item in step_outputs:
            if str(item.get("agent", "")).startswith(agent_prefix):
                return str(item.get("content", "")).strip() + "\n"
        return f"# {agent_prefix} 输出\n\n当前任务没有找到该员工输出。\n"

    @staticmethod
    def _extract_lines(text: str, keywords: list[str]) -> str:
        lines = []
        for line in text.splitlines():
            if any(keyword in line for keyword in keywords):
                lines.append(line)
        if not lines:
            return text[:4000] if text else "暂无可提取内容。"
        return "\n".join(lines)

    @staticmethod
    def _checklist(name: str) -> str:
        return "\n".join(
            [
                f"# {name}交付检查清单",
                "",
                "- [ ] 需求和目标用户清楚",
                "- [ ] 核心内容可直接复制使用",
                "- [ ] 风险和待确认项已标记",
                "- [ ] 文件命名和版本可追踪",
                "- [ ] 已人工复核最终交付内容",
            ]
        )

    @staticmethod
    def _append_image_config(user_input: str, image_config: dict) -> str:
        def value(key: str, default: str = "未填写") -> str:
            item = image_config.get(key)
            return str(item).strip() if item not in (None, "") else default

        return (
            f"{user_input}\n\n"
            "## 生图配置\n"
            f"- 正向提示词：{value('positive_prompt')}\n"
            "- 参考图：如用户上传参考图，请优先按参考图说明保持人物、产品、风格或构图一致。\n"
            "- 参数来源：尺寸、模型、seed、steps、CFG、采样器、负向词等由 ComfyUI/RunningHub 工作流或导入的 API JSON 节点映射配置，不需要在员工输出中重复询问。\n"
            "- 执行要求：当前阶段由 06_分镜生图设计师输出分镜总表、关键帧正向提示词、参考图使用策略和连续性控制说明；不要声称已经生成图片文件。\n"
        )

    @staticmethod
    def _append_video_config(user_input: str, video_config: dict) -> str:
        def value(key: str, default: str = "未填写") -> str:
            item = video_config.get(key)
            return str(item).strip() if item not in (None, "") else default

        return (
            f"{user_input}\n\n"
            "## 视频生成配置\n"
            f"- 正向提示词：{value('positive_prompt')}\n"
            "- 参考图：如用户上传参考图，请把它作为首帧、角色一致性、产品一致性或风格参考来规划。\n"
            "- 参数来源：模型、画幅、时长、运动强度、镜头、seed、FPS、分辨率、负向词等由视频/ComfyUI 工作流或导入的 API JSON 节点映射配置，不需要在员工输出中重复询问。\n"
            "- 执行要求：当前阶段由 06_分镜生图设计师输出分镜生图方案和 ComfyUI 生图参数包，07_视频生成执行员输出视频画面正向提示词、镜头清单和 ComfyUI 生视频参数包，20_语音字幕包装师输出 TTS、SRT、BGM 和音效方案；最终硬字幕、最终混音和最终导出交给 22_剪辑成片执行师，不要声称已经生成 mp4。\n"
        )

    @staticmethod
    def _append_comfyui_config(user_input: str, production_config: dict, compose_config: dict) -> str:
        def value(key: str, default: str = "未填写") -> str:
            item = compose_config.get(key)
            return str(item).strip() if item not in (None, "") else default

        mode = str(production_config.get("mode") or "off").strip()
        api_note = "已填写，运行时可调用，不保存密钥" if compose_config.get("api_key_provided") else "未填写"
        base_url_note = "已填写，运行时可调用，不保存地址到输出" if compose_config.get("base_url_provided") else "未填写"
        node_info = str(compose_config.get("node_info_list_json") or "").strip()
        node_note = "已填写节点映射 JSON" if node_info and node_info != "[]" else "未填写，需后续按实际 ComfyUI 节点补齐"
        library = compose_config.get("workflow_library")
        library_lines: list[str] = []
        if isinstance(library, list):
            for item in library:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or item.get("id") or "未命名工作流").strip()
                purpose = str(item.get("purpose") or "").strip()
                endpoint_status = "已配置接口" if item.get("endpoint_configured") else "未配置接口"
                node_status = "已配置节点映射" if item.get("node_mapping_configured") else "未配置节点映射"
                library_lines.append(f"  - {name}：{purpose}；{endpoint_status}；{node_status}")
        library_note = "\n".join(library_lines) if library_lines else "  - 未配置工作流库"
        return (
            f"{user_input}\n\n"
            "## ComfyUI 素材/预览配置\n"
            f"- 自动生成模式：{mode or 'off'}\n"
            f"- 剪辑/预览工具：{value('tool', 'ffmpeg')}\n"
            f"- 当前编辑槽位：{value('workflow_preset_name')}（仅用于管理台编辑，不决定运行时路由）\n"
            f"- ComfyUI 素材/预览工作流接口：{value('workflow_endpoint')}\n"
            f"- ComfyUI 平台密钥：{api_note}\n"
            f"- ComfyUI 平台接口地址：{base_url_note}\n"
            f"- 节点映射：{node_note}\n"
            f"- 轮询超时：{value('poll_timeout_seconds', '3600')} 秒\n"
            f"- 工作流库配置状态：\n{library_note}\n"
            "- 执行要求：06_分镜生图设计师和 07_视频生成执行员直接输出可映射到 ComfyUI/RunningHub 的画面素材参数包；运行时会根据素材类型自动选择工作流库里的生图或生视频槽位，而不是按当前下拉编辑槽位执行。AI 图片和视频只是片段素材，配音、SRT 字幕、最终硬字幕、最终混音和最终导出交给 20_语音字幕包装师与 22_剪辑成片执行师。\n"
        )

    def _upload_reference_image(self, payload: dict) -> dict:
        filename = str(payload.get("filename") or "").strip()
        content_base64 = str(payload.get("content_base64") or "").strip()
        role = str(payload.get("role") or "参考图").strip()
        note = str(payload.get("note") or "").strip()
        if not filename or not content_base64:
            raise ValueError("filename and content_base64 are required")

        suffix = Path(filename).suffix.lower()
        if suffix not in IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported image type: {suffix}")

        image_bytes = base64.b64decode(content_base64, validate=True)
        image_bytes, suffix = self._normalize_comfy_image_bytes(image_bytes, suffix)
        if len(image_bytes) > 12 * 1024 * 1024:
            raise ValueError("Reference image is too large; max size is 12 MB")

        REFERENCE_ROOT.mkdir(parents=True, exist_ok=True)
        safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in Path(filename).stem)[:80]
        target = REFERENCE_ROOT / f"{safe_stem}_{uuid4().hex[:8]}{suffix}"
        target.write_bytes(image_bytes)

        relative_path = target.relative_to(WORKSPACE_ROOT).as_posix()
        return {
            "filename": filename,
            "stored_path": relative_path,
            "role": role,
            "note": note,
            "size_bytes": len(image_bytes),
        }

    def _upload_comfy_debug_reference(self, payload: dict) -> dict:
        filename = str(payload.get("filename") or "").strip()
        content_base64 = str(payload.get("content_base64") or "").strip()
        if not filename or not content_base64:
            raise ValueError("filename and content_base64 are required")

        suffix = Path(filename).suffix.lower()
        if suffix not in IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported ComfyUI debug reference type: {suffix}")

        file_bytes = base64.b64decode(content_base64, validate=True)
        file_bytes, suffix = self._normalize_comfy_image_bytes(file_bytes, suffix)
        if len(file_bytes) > 200 * 1024 * 1024:
            raise ValueError("ComfyUI debug reference is too large; max size is 200 MB")

        upload_root = REFERENCE_ROOT / "comfy_debug"
        upload_root.mkdir(parents=True, exist_ok=True)
        safe_stem = self._safe_asset_stem(Path(filename).stem or "reference")[:80]
        target = upload_root / f"{safe_stem}_{uuid4().hex[:8]}{suffix}"
        target.write_bytes(file_bytes)

        return {
            "filename": filename,
            "stored_path": target.relative_to(WORKSPACE_ROOT).as_posix(),
            "size_bytes": len(file_bytes),
        }

    def _upload_voice_sample(self, payload: dict) -> dict:
        filename = str(payload.get("filename") or "").strip()
        content_base64 = str(payload.get("content_base64") or "").strip()
        if not filename or not content_base64:
            raise ValueError("filename and content_base64 are required")

        suffix = Path(filename).suffix.lower()
        allowed = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}
        if suffix not in allowed:
            raise ValueError(f"Unsupported voice sample type: {suffix}")

        audio_bytes = base64.b64decode(content_base64, validate=True)
        if len(audio_bytes) > 50 * 1024 * 1024:
            raise ValueError("Voice sample is too large; max size is 50 MB")

        VOICE_SAMPLE_ROOT.mkdir(parents=True, exist_ok=True)
        safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in Path(filename).stem)[:80]
        target = VOICE_SAMPLE_ROOT / f"{safe_stem}_{uuid4().hex[:8]}{suffix}"
        target.write_bytes(audio_bytes)

        relative_path = target.relative_to(WORKSPACE_ROOT).as_posix()
        return {
            "filename": filename,
            "stored_path": relative_path,
            "size_bytes": len(audio_bytes),
        }

    def _knowledge_files(self) -> list[dict]:
        if not KNOWLEDGE_ROOT.exists():
            return []

        files = []
        for path in sorted(KNOWLEDGE_ROOT.iterdir()):
            if not path.is_file() or path.name == ".gitignore":
                continue
            if path.suffix.lower() not in {".md", ".txt", ".json", ".csv"}:
                continue
            stat = path.stat()
            files.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                }
            )
        return files

    def _upload_knowledge(self, payload: dict) -> dict:
        filename = str(payload.get("filename") or "").strip()
        content_base64 = str(payload.get("content_base64") or "").strip()
        if not filename or not content_base64:
            raise ValueError("filename and content_base64 are required")

        suffix = Path(filename).suffix.lower()
        allowed = {".md", ".txt", ".json", ".csv"}
        if suffix not in allowed:
            raise ValueError(f"Unsupported knowledge file type: {suffix}")

        content_bytes = base64.b64decode(content_base64, validate=True)
        if len(content_bytes) > 5 * 1024 * 1024:
            raise ValueError("Knowledge file is too large; max size is 5 MB")
        content_bytes.decode("utf-8")

        KNOWLEDGE_ROOT.mkdir(parents=True, exist_ok=True)
        safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in Path(filename).stem)[:80]
        target = KNOWLEDGE_ROOT / f"{safe_stem}{suffix}"
        if target.exists():
            target = KNOWLEDGE_ROOT / f"{safe_stem}_{uuid4().hex[:8]}{suffix}"
        target.write_bytes(content_bytes)
        return {"ok": True, "name": target.name, "size_bytes": len(content_bytes)}

    def _test_model(self, payload: dict) -> dict:
        api_key = str(payload.get("api_key") or "").strip()
        base_url = str(payload.get("base_url") or "https://api.openai.com/v1").strip().rstrip("/")
        model = str(payload.get("model") or "").strip()
        if not api_key:
            raise ValueError("API Key is required for model test")
        if not base_url:
            raise ValueError("Base URL is required for model test")
        if not model:
            raise ValueError("model is required for model test")

        body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 8,
            }
        ).encode("utf-8")
        req = urllib_request.Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=20) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            raise ValueError(f"HTTP {exc.code}: {detail}") from exc
        except urllib_error.URLError as exc:
            raise ValueError(f"连接失败：{exc.reason}") from exc

        data = json.loads(raw)
        return {"ok": True, "model": model, "id": data.get("id", "")}

    @staticmethod
    def _append_reference_images(user_input: str, reference_images: list[dict]) -> str:
        lines = ["## 参考图", "以下参考图由管理台上传到本地，供 06_分镜生图设计师和 07_视频生成执行员作为角色/产品/风格参考："]
        for index, image in enumerate(reference_images, start=1):
            lines.extend(
                [
                    f"{index}. 文件名：{image.get('filename', '')}",
                    f"   - 本地路径：{image.get('stored_path', '')}",
                    f"   - 用途：{image.get('role', '参考图')}",
                    f"   - 说明：{image.get('note', '') or '无'}",
                ]
            )
        lines.append("执行要求：如果视频工具支持参考图或图生视频，应在镜头提示词中明确使用这些参考图保持人物、产品或视觉风格一致；不要声称已经分析图片内容。")
        return f"{user_input}\n\n" + "\n".join(lines) + "\n"

    def _delete_task(self, name: str) -> dict:
        task_dir = self._safe_task_dir(name)
        if task_dir == OUTPUT_ROOT.resolve():
            raise ValueError("Refusing to delete output root")

        removed_assets = self._delete_asset_library_items_for_task(name)

        for path in sorted(task_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        task_dir.rmdir()
        return {
            "ok": True,
            "task": name,
            "removed_assets": len(removed_assets),
            "removed_asset_ids": [str(item.get("id") or "") for item in removed_assets],
        }

    def _delete_asset_library_items_for_task(self, task_name: str) -> list[dict]:
        clean_task_name = str(task_name or "").strip()
        if not clean_task_name:
            return []
        items = [item for item in self._read_asset_library_index() if isinstance(item, dict)]
        removed = [item for item in items if str(item.get("source_task") or "").strip() == clean_task_name]
        if not removed:
            return []
        kept = [item for item in items if str(item.get("source_task") or "").strip() != clean_task_name]
        library_root = ASSET_LIBRARY_ROOT.resolve()
        for item in removed:
            library_file = str(item.get("file") or "").strip()
            if not library_file:
                continue
            target = (library_root / library_file).resolve()
            if target.is_file() and self._is_relative_to(target, library_root):
                try:
                    target.unlink()
                except OSError:
                    pass
        self._write_asset_library_index(kept)
        return removed

    def _append_long_term_memory(self, user_input: str) -> str:
        context = self._long_term_memory_context()
        if not context:
            return user_input
        return f"{user_input}\n\n## 长期记忆\n{context}\n"

    def _long_term_memory_context(self) -> str:
        if not MEMORY_ROOT.exists():
            return ""

        sections = []
        for path in sorted(MEMORY_ROOT.glob("*.md")):
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                sections.append(f"### {path.name}\n{content}")

        if not sections:
            return ""
        return "\n\n".join(sections)

    def _append_knowledge_base(self, user_input: str) -> str:
        if not KNOWLEDGE_ROOT.exists():
            return user_input

        sections = []
        remaining = 20000
        for path in sorted(KNOWLEDGE_ROOT.iterdir()):
            if not path.is_file() or path.name == ".gitignore":
                continue
            if path.suffix.lower() not in {".md", ".txt", ".json", ".csv"}:
                continue
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if not content:
                continue
            clipped = content[:remaining]
            sections.append(f"### {path.name}\n{clipped}")
            remaining -= len(clipped)
            if remaining <= 0:
                break

        if not sections:
            return user_input
        return f"{user_input}\n\n## 本地知识库\n" + "\n\n".join(sections) + "\n"

    def _append_inherited_task(self, user_input: str, task_name: str, inherit_mode: str) -> str:
        task_dir = self._safe_task_dir(task_name)
        files = ["final_output.md"]
        if inherit_mode == "input_and_final":
            files = ["input.md", "final_output.md"]

        sections = []
        for file_name in files:
            path = task_dir / file_name
            if path.exists() and path.is_file():
                content = path.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    sections.append(f"### {task_name}/{file_name}\n{content}")

        if not sections:
            return user_input
        return f"{user_input}\n\n## 继承历史任务记忆\n" + "\n\n".join(sections) + "\n"

    def _safe_task_dir(self, name: str) -> Path:
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError("Invalid task name")
        task_dir = (OUTPUT_ROOT / name).resolve()
        output_root = OUTPUT_ROOT.resolve()
        if not self._is_relative_to(task_dir, output_root) or not task_dir.is_dir():
            raise FileNotFoundError(name)
        return task_dir

    def _safe_task_file(self, task: str, file_name: str, must_exist: bool) -> tuple[Path, Path]:
        if not file_name or file_name.startswith("/") or file_name.startswith("\\"):
            raise ValueError("Invalid file name")
        task_dir = self._safe_task_dir(task)
        target = (task_dir / file_name).resolve()
        task_root = task_dir.resolve()
        if not self._is_relative_to(target, task_root):
            raise ValueError("Invalid task file path")
        if must_exist and not target.is_file():
            raise FileNotFoundError(file_name)
        return target, task_dir

    def _safe_workflow_path(self, name: str, must_exist: bool) -> Path:
        if not name:
            raise ValueError("Invalid workflow name")
        candidate = name.strip()
        if candidate.endswith(".json"):
            candidate = candidate[:-5]
        if not candidate or "/" in candidate or "\\" in candidate or candidate in {".", ".."}:
            raise ValueError("Invalid workflow name")
        path = (WORKFLOW_ROOT / f"{candidate}.json").resolve()
        workflow_root = WORKFLOW_ROOT.resolve()
        if not self._is_relative_to(path, workflow_root):
            raise ValueError("Invalid workflow path")
        if must_exist and not path.is_file():
            raise FileNotFoundError(name)
        return path

    def _safe_staff_dir(self, name: str, must_exist: bool) -> Path:
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError("Invalid staff name")
        staff_dir = (STAFF_ROOT / name).resolve()
        staff_root = STAFF_ROOT.resolve()
        if not self._is_relative_to(staff_dir, staff_root):
            raise ValueError("Invalid staff path")
        if must_exist and not staff_dir.is_dir():
            raise FileNotFoundError(name)
        return staff_dir

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    @staticmethod
    def _single(query: dict[str, list[str]], key: str) -> str:
        values = query.get(key)
        if not values:
            raise ValueError(f"Missing query parameter: {key}")
        return values[0]

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, exc: Exception) -> None:
        traceback.print_exc()
        self._send_json({"error": str(exc)}, status=400)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    import argparse

    parser = argparse.ArgumentParser(description="Start my_workspace visual workflow manager.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), WorkflowWebHandler)
    print(f"自媒体工作流管理台: http://{args.host}:{args.port}")
    print("按 Ctrl+C 停止服务")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
