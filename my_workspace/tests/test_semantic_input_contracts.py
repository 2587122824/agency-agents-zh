from __future__ import annotations

import sys
import tempfile
import unittest
import json
from pathlib import Path

from PIL import Image


WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "my_workspace"))

import web_app  # noqa: E402
from my_codex_core.cloud_comfyui_adapter import CloudComfyUIAdapter  # noqa: E402
from my_codex_core.local_tts_adapter import LocalTTSAdapter  # noqa: E402
from my_codex_core.local_ffmpeg_adapter import LocalFFmpegAdapter  # noqa: E402
from my_codex_core.workflow_engine import WorkflowEngine  # noqa: E402
from my_codex_core.production_plan_compiler import (  # noqa: E402
    _bind_first_source_video,
    _image_prompt_item,
    compile_production_plan,
)
from my_codex_core.production_entities import entity_context_for_ids, normalize_production_entities  # noqa: E402
from my_codex_core.production_pipeline import (  # noqa: E402
    _active_visual_jobs_for_mode,
    _clean_voice_text,
    _downgrade_unconfigured_visual_jobs_for_available_slots,
    _filter_skip_execution_visual_nodes,
    _packaging_dependency_blockers,
    _payload_for_material_type,
    _payload_has_required_mode,
    _extract_voice_text,
    _quality_check_voice_text,
    _quality_check_srt,
    _required_workflow_slots,
    _srt_from_voice_text,
)
from my_codex_core.production_output_validator import validate_production_output  # noqa: E402
from my_codex_core.production_output_validator import normalize_production_output_content  # noqa: E402
from my_codex_core.requirement_guard import declares_human_confirmation, validate_requirement_alignment  # noqa: E402
from my_codex_core.task_state_center import TaskStateCenter  # noqa: E402


class SemanticInputContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflows = {item["id"]: item for item in web_app.COMFY_DEBUG_WORKFLOWS}

    def test_comfy_debug_queue_requires_explicit_manual_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            comfy_dir = task_dir / "comfyui"
            comfy_dir.mkdir()
            (comfy_dir / "comfyui_payload.json").write_text(
                json.dumps({"image_prompts": [{"id": "shot_001", "prompt": "test frame"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            (task_dir / "production_manifest.json").write_text(
                json.dumps({"status": "awaiting_comfyui_debug", "composition": {}}, ensure_ascii=False),
                encoding="utf-8",
            )

            status = web_app.WorkflowWebHandler._task_comfy_debug_status(task_dir)
            self.assertFalse(status["enabled"])
            self.assertGreater(status["total"], 0)

            (task_dir / "production_manifest.json").write_text(
                json.dumps(
                    {
                        "status": "awaiting_comfyui_debug",
                        "composition": {"manual_debug_enabled": True},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            status = web_app.WorkflowWebHandler._task_comfy_debug_status(task_dir)
            self.assertTrue(status["enabled"])

    def test_no_voiceover_marker_is_not_tts_text(self) -> None:
        self.assertEqual(_clean_voice_text("（无旁白）\n"), "")
        extracted = _extract_voice_text('```json\n{"production_intents":{"audio":[{"intent":"generate_voiceover","voice_text":"（无旁白）"}]}}\n```')
        self.assertEqual(extracted.strip(), "（无旁白）")
        voice_quality = _quality_check_voice_text("（无旁白）\n")
        self.assertFalse(voice_quality["usable"])
        self.assertEqual(voice_quality["status"], "disabled")
        srt_quality = _quality_check_srt("1\n00:00:00,000 --> 00:00:02,000\n（无旁白）\n")
        self.assertFalse(srt_quality["usable"])
        self.assertEqual(srt_quality["status"], "disabled")

    def test_ffmpeg_ignores_stale_audio_when_tts_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio_dir = Path(tmp) / "audio"
            audio_dir.mkdir()
            stale_audio = audio_dir / "voiceover.wav"
            stale_audio.write_bytes(b"stale")
            adapter = LocalFFmpegAdapter(Path(tmp))
            found = adapter._find_audio_file(
                audio_dir,
                {"audio": {"adapter_status": "skipped", "voice_text_status": "disabled", "voiceover_audio_file": ""}},
            )
            self.assertIsNone(found)

    def test_audio_validator_accepts_explicitly_disabled_voice_and_subtitles(self) -> None:
        content = json.dumps(
            {
                "production_intents": {
                    "audio": [
                        {
                            "intent": "generate_voiceover",
                            "enabled": False,
                            "voice_text": "",
                            "target_duration_seconds": 12,
                        },
                        {
                            "intent": "build_subtitles",
                            "enabled": False,
                            "segments": [],
                        },
                        {
                            "intent": "select_bgm",
                            "enabled": False,
                        },
                    ]
                },
                "audio_package": {
                    "voiceover_text": "",
                    "subtitle_srt_draft": "",
                },
            },
            ensure_ascii=False,
        )

        result = validate_production_output(
            {"agent": "20_语音字幕包装师"},
            f"```json\n{content}\n```",
            {
                "core_topic": "不同高度跳下身体承重变化，竖屏12秒生产烟测",
                "original_requirement": "竖屏12秒生产烟测，只验证图片素材和本地图片轮播预览，不需要配音",
                "duration_seconds": 12,
            },
        )

        self.assertTrue(result["passed"], result["issues"])

    def test_requirement_guard_accepts_disabled_audio_package_without_topic_terms(self) -> None:
        content = json.dumps(
            {
                "production_intents": {
                    "audio": [
                        {"intent": "generate_voiceover", "enabled": False, "voice_text": ""},
                        {"intent": "build_subtitles", "enabled": False, "segments": []},
                        {"intent": "select_bgm", "enabled": False},
                    ]
                },
                "audio_package": {"voiceover_text": "", "subtitle_srt_draft": ""},
            },
            ensure_ascii=False,
        )

        result = validate_requirement_alignment(
            {
                "core_topic": "不同高度跳下身体承重变化，竖屏12秒生产烟测",
                "original_requirement": "不同高度跳下身体承重变化，竖屏12秒生产烟测，不需要配音",
                "duration_seconds": 12,
            },
            f"```json\n{content}\n```",
            5,
        )

        self.assertTrue(result["passed"], result["issues"])

    def test_requirement_guard_accepts_on_topic_audio_package_without_verbatim_topic(self) -> None:
        content = json.dumps(
            {
                "production_intents": {
                    "audio": [
                        {
                            "intent": "generate_voiceover",
                            "intent_id": "voiceover_main",
                            "voice_text": (
                                "又是被闹钟叫醒的一天。再睡五分钟……不行！要迟到了！冲啊！"
                                "地铁还是那么挤。开电脑，回消息，开会。又是重复的一天。"
                                "一杯咖啡续命。这十分钟，是我的。下班铃，我最爱的声音。"
                                "回家，瘫进沙发。你的一天，也是这样吗？"
                            ),
                            "target_duration_seconds": 58,
                        },
                        {
                            "intent": "build_subtitles",
                            "intent_id": "subtitle_main",
                            "subtitle_segments": [
                                {"start_time": "00:00:00,000", "end_time": "00:00:08,000", "text": "又是被闹钟叫醒的一天。"},
                                {"start_time": "00:00:50,000", "end_time": "00:00:59,800", "text": "下班回家，瘫进沙发。"},
                            ],
                        },
                    ]
                },
                "audio_package": {
                    "voiceover_text": "闹钟、地铁、开会、咖啡、下班回家，完整覆盖打工人日常。",
                },
            },
            ensure_ascii=False,
        )

        result = validate_requirement_alignment(
            {
                "core_topic": "主角小美打工人的一天vlog，竖屏1分钟",
                "original_requirement": "主角小美打工人的一天vlog，竖屏1分钟",
                "duration_seconds": 60,
            },
            f"```json\n{content}\n```",
            5,
        )

        self.assertTrue(result["passed"], result["issues"])

    def test_requirement_guard_rejects_unrelated_audio_package_even_with_duration(self) -> None:
        content = json.dumps(
            {
                "production_intents": {
                    "audio": [
                        {
                            "intent": "generate_voiceover",
                            "voice_text": "今天我们来到一家网红火锅店，看看招牌牛油锅和新品甜品到底值不值得排队。",
                            "target_duration_seconds": 60,
                        }
                    ]
                }
            },
            ensure_ascii=False,
        )

        result = validate_requirement_alignment(
            {
                "core_topic": "主角小美打工人的一天vlog，竖屏1分钟",
                "original_requirement": "主角小美打工人的一天vlog，竖屏1分钟",
                "duration_seconds": 60,
            },
            f"```json\n{content}\n```",
            5,
        )

        self.assertFalse(result["passed"])

    def test_video_validator_accepts_empty_video_intents_when_ai_video_disabled(self) -> None:
        content = json.dumps(
            {
                "production_intents": {"video": []},
                "video_prompts": [],
            },
            ensure_ascii=False,
        )

        result = validate_production_output(
            {"agent": "07_视频生成执行员"},
            f"```json\n{content}\n```",
            {
                "core_topic": "不同高度跳下身体承重变化，竖屏12秒生产烟测",
                "original_requirement": "只验证图片素材和本地图片轮播预览，不生成AI视频片段，不需要配音",
                "duration_seconds": 12,
            },
        )

        self.assertTrue(result["passed"], result["issues"])

    def test_requirement_guard_accepts_skipped_video_package_without_topic_terms(self) -> None:
        content = json.dumps(
            {
                "production_intents": {"video": []},
                "video_prompts": [],
            },
            ensure_ascii=False,
        )

        result = validate_requirement_alignment(
            {
                "core_topic": "不同高度跳下身体承重变化，竖屏12秒生产烟测",
                "original_requirement": "只验证图片素材和本地图片轮播预览，不生成AI视频片段，不需要配音",
                "duration_seconds": 12,
            },
            f"```json\n{content}\n```",
            6,
        )

        self.assertTrue(result["passed"], result["issues"])

    def test_compiler_filters_skip_execution_video_placeholders(self) -> None:
        plan = compile_production_plan(
            task_id="skip_video_placeholder_test",
            route_content=json.dumps({"production_type": "asset_only", "aspect_ratio": "9:16"}, ensure_ascii=False),
            image_content=json.dumps({"production_intents": {"image": []}}, ensure_ascii=False),
            video_content=json.dumps(
                {
                    "production_intents": {
                        "video": [
                            {
                                "intent": "generate_i2v_clip",
                                "intent_id": "skip_asset_only_video",
                                "duration_seconds": 0,
                                "motion_plan": "不生成AI视频，本意图不应执行",
                                "compatibility": {"skip_execution": True},
                            }
                        ]
                    },
                    "video_prompts": [
                        {
                            "job_id": "skip_asset_only_video",
                            "asset_tag": "skip_video_placeholder",
                            "prompt": "占位提示：不应执行",
                            "duration": 0,
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )

        payload = plan["compiled_payload"]
        self.assertFalse(payload.get("video_prompts"))
        self.assertFalse([job for job in plan["visual_jobs"] if job.get("type") == "video"])
        self.assertTrue(any("skip_asset_only_video" in note for note in plan["compile_notes"]))

    def test_compiler_clears_stale_existing_video_prompts_when_skip_filtered(self) -> None:
        stale_payload = {
            "video_prompts": [
                {
                    "job_id": "skip_asset_only_video",
                    "workflow_mode": "i2v_first_frame",
                    "prompt": "占位意图：不生成AI视频，此意图不应执行",
                }
            ]
        }
        plan = compile_production_plan(
            task_id="stale_skip_video_placeholder_test",
            route_content=json.dumps({"production_type": "asset_only", "aspect_ratio": "9:16"}, ensure_ascii=False),
            image_content=json.dumps({"production_intents": {"image": []}}, ensure_ascii=False),
            video_content=json.dumps({"production_intents": {"video": []}, "video_prompts": []}, ensure_ascii=False),
            existing_payload=stale_payload,
        )

        self.assertEqual(plan["compiled_payload"].get("video_prompts"), [])
        self.assertFalse([job for job in plan["visual_jobs"] if job.get("type") == "video"])

    def test_ffmpeg_manifest_filter_removes_skip_video_placeholders(self) -> None:
        manifest = {
            "production_nodes": [
                {
                    "job_id": "skip_asset_only_video",
                    "stage": "visual",
                    "status": "success",
                    "outputs": ["comfyui/job_skip_asset_only_video/comfyui_result_01.mp4"],
                },
                {
                    "job_id": "shot_001_keyframe",
                    "stage": "visual",
                    "status": "success",
                    "outputs": ["comfyui/job_shot_001_keyframe/comfyui_result_01.png"],
                },
            ]
        }

        filtered = _filter_skip_execution_visual_nodes(manifest)
        self.assertEqual(len(filtered["production_nodes"]), 1)
        self.assertEqual(filtered["production_nodes"][0]["job_id"], "shot_001_keyframe")

    def test_local_ffmpeg_filters_skip_placeholder_video_paths(self) -> None:
        self.assertTrue(
            LocalFFmpegAdapter._is_skip_placeholder_media_path(
                Path("comfyui/job_skip_asset_only_video/comfyui_result_01.mp4")
            )
        )
        self.assertFalse(
            LocalFFmpegAdapter._is_skip_placeholder_media_path(
                Path("comfyui/job_real_clip/comfyui_result_01.mp4")
            )
        )

    def test_local_ffmpeg_distributes_image_slideshow_to_target_duration(self) -> None:
        images = [Path(f"frame_{index:02d}.png") for index in range(13)]
        self.assertAlmostEqual(LocalFFmpegAdapter._image_still_duration(images, 12.0), 12.0 / 13.0, places=6)
        self.assertEqual(LocalFFmpegAdapter._image_still_duration(images, 0.0), 3.0)

    def test_normalizes_package_delivery_resolution_to_locked_delivery_size(self) -> None:
        content = json.dumps(
            {
                "production_intents": {
                    "package": [
                        {
                            "intent": "build_edit_timeline",
                            "intent_id": "edit_timeline_preview",
                            "timeline": [{"source_intent_id": "shot_001", "start_seconds": 0, "duration_seconds": 12}],
                        },
                        {
                            "intent": "apply_delivery_spec",
                            "intent_id": "delivery_spec_preview",
                            "delivery_resolution": "480x848",
                            "fps": 24,
                        },
                    ]
                },
                "delivery_spec": {
                    "format": "mp4",
                    "resolution": "480x848",
                    "fps": 24,
                },
                "missing_assets": [],
            },
            ensure_ascii=False,
        )

        lock = {
            "original_requirement": "竖屏12秒生产烟测，只验证图片素材和本地图片轮播预览",
            "duration_seconds": 12,
        }
        normalized = normalize_production_output_content({"agent": "22_剪辑成片执行师"}, f"```json\n{content}\n```", lock)
        self.assertTrue(normalized["changed"])
        self.assertIn('"resolution": "1080x1920"', normalized["content"])
        self.assertIn('"delivery_resolution": "1080x1920"', normalized["content"])

        result = validate_production_output({"agent": "22_剪辑成片执行师"}, normalized["content"], lock)
        self.assertTrue(result["passed"], result["issues"])

    def test_normalizes_package_timeline_small_duration_gap_to_locked_duration(self) -> None:
        content = json.dumps(
            {
                "production_intents": {
                    "package": [
                        {
                            "intent": "build_edit_timeline",
                            "intent_id": "edit_timeline_xiaomei_vlog",
                            "timeline": [
                                {"source_intent_id": "clip_001", "start_seconds": 0, "duration_seconds": 30},
                                {"source_intent_id": "clip_002", "start_seconds": 30, "duration_seconds": 28},
                            ],
                        },
                        {
                            "intent": "apply_delivery_spec",
                            "intent_id": "delivery_spec_xiaomei_vlog",
                            "delivery_resolution": "1080x1920",
                            "fps": 24,
                        },
                    ]
                },
                "edit_timeline": [
                    {"clip_id": "clip_001", "start_seconds": 0, "duration_seconds": 30},
                    {"clip_id": "clip_002", "start_seconds": 30, "duration_seconds": 28},
                ],
                "delivery_spec": {"resolution": "1080x1920", "fps": 24},
                "missing_assets": [],
            },
            ensure_ascii=False,
        )
        lock = {
            "original_requirement": "主角小美打工人的一天vlog，竖屏1分钟",
            "duration_seconds": 60,
        }

        normalized = normalize_production_output_content({"agent": "22_剪辑成片执行师"}, f"```json\n{content}\n```", lock)

        self.assertTrue(normalized["changed"])
        self.assertTrue(any(item["type"] == "locked_package_timeline_duration" for item in normalized["normalizations"]))
        result = validate_production_output({"agent": "22_剪辑成片执行师"}, normalized["content"], lock)
        self.assertTrue(result["passed"], result["issues"])

    def test_valid_detailed_package_timeline_overrides_short_compat_clips(self) -> None:
        content = json.dumps(
            {
                "production_intents": {
                    "package": [
                        {
                            "intent": "build_edit_timeline",
                            "intent_id": "edit_timeline_xiaomei_vlog",
                            "timeline": [
                                {"source_intent_id": "clip_001", "start_seconds": 0, "duration_seconds": 20},
                                {"source_intent_id": "clip_002", "start_seconds": 20, "duration_seconds": 22},
                                {"source_intent_id": "transition_fadeout", "start_seconds": 42, "duration_seconds": 18},
                            ],
                        },
                        {
                            "intent": "apply_delivery_spec",
                            "intent_id": "delivery_spec_xiaomei_vlog",
                            "delivery_resolution": "1080x1920",
                            "fps": 24,
                        },
                    ]
                },
                "edit_timeline": {
                    "clips": [
                        {"clip_id": "clip_001", "start_seconds": 0, "duration_seconds": 20},
                        {"clip_id": "clip_002", "start_seconds": 20, "duration_seconds": 22},
                    ]
                },
                "delivery_spec": {"resolution": "1080x1920", "fps": 24},
                "missing_assets": [],
            },
            ensure_ascii=False,
        )
        lock = {
            "original_requirement": "xiaomei food street vlog, 9:16 vertical, 60 seconds",
            "duration_seconds": 60,
        }

        result = validate_production_output({"agent": "22_剪辑成片执行师"}, f"```json\n{content}\n```", lock)

        self.assertTrue(result["passed"], result["issues"])

    def test_does_not_normalize_package_timeline_large_duration_gap(self) -> None:
        content = json.dumps(
            {
                "production_intents": {
                    "package": [
                        {
                            "intent": "build_edit_timeline",
                            "intent_id": "edit_timeline_short",
                            "timeline": [
                                {"source_intent_id": "clip_001", "start_seconds": 0, "duration_seconds": 20},
                                {"source_intent_id": "clip_002", "start_seconds": 20, "duration_seconds": 20},
                            ],
                        },
                        {
                            "intent": "apply_delivery_spec",
                            "intent_id": "delivery_spec_short",
                            "delivery_resolution": "1080x1920",
                            "fps": 24,
                        },
                    ]
                },
                "delivery_spec": {"resolution": "1080x1920", "fps": 24},
                "missing_assets": [],
            },
            ensure_ascii=False,
        )
        lock = {
            "original_requirement": "主角小美打工人的一天vlog，竖屏1分钟",
            "duration_seconds": 60,
        }

        normalized = normalize_production_output_content({"agent": "22_剪辑成片执行师"}, f"```json\n{content}\n```", lock)
        result = validate_production_output({"agent": "22_剪辑成片执行师"}, normalized["content"], lock)

        self.assertFalse(any(item["type"] == "locked_package_timeline_duration" for item in normalized["normalizations"]))
        self.assertFalse(result["passed"])

    def test_scene_library_fields_are_normalized_for_context(self) -> None:
        registry = normalize_production_entities(
            {
                "scenes": {
                    "scene_arena": {
                        "name": "露天竞技场",
                        "scene_master_image": "my_workspace/my_asset_library/03_scene_base/arena.png",
                        "scene_description": "线下露天竞技场舞台，观众席环绕。",
                        "fixed_layout": ["舞台在中央", "观众席围绕舞台"],
                        "lighting": ["傍晚暖色主光"],
                        "camera_allowed_changes": ["允许远景和中景切换"],
                        "forbidden_changes": ["不要改变舞台结构"],
                    }
                }
            }
        )

        scene = registry["scenes"]["scene_arena"]
        self.assertEqual(scene["scene_id"], "scene_arena")
        self.assertEqual(scene["scene_reference"], scene["scene_master_image"])
        context = entity_context_for_ids(
            {"characters": [], "styles": [], "products": [], "scenes": [scene]},
            scene_id="scene_arena",
        )
        self.assertEqual(context["scene"]["scene_master_image"], "my_workspace/my_asset_library/03_scene_base/arena.png")
        self.assertIn("舞台在中央", context["constraints"])
        self.assertIn("傍晚暖色主光", context["constraints"])
        self.assertIn("允许远景和中景切换", context["constraints"])
        self.assertIn("不要改变舞台结构", context["constraints"])

    def test_run_linked_assets_context_is_structured(self) -> None:
        text = web_app.WorkflowWebHandler._append_linked_assets(
            "做一个竞技场剧情短片",
            {
                "assets": [
                    {
                        "asset_id": "asset_scene_001",
                        "name": "竞技场母版",
                        "file": "my_workspace/my_asset_library/03_scene_base/arena.png",
                        "tags": ["scene_base"],
                    }
                ],
                "characters": [{"character_id": "hero", "name": "主角", "master_image": "hero.png"}],
                "scenes": [{"scene_id": "arena", "name": "竞技场", "scene_master_image": "arena.png"}],
            },
        )
        self.assertIn("## 关联资产上下文", text)
        self.assertIn('"linked_assets"', text)
        self.assertIn('"asset_id": "asset_scene_001"', text)
        self.assertIn('"character_id": "hero"', text)
        self.assertIn('"scene_id": "arena"', text)

    def test_task_state_does_not_offer_debug_queue_when_gate_is_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            center = TaskStateCenter(
                task_dir=task_dir,
                task_name="task_test",
                summary={"production_status": "awaiting_comfyui_debug"},
                files=[],
                comfy_debug_loader=lambda _task_dir: {"enabled": False, "complete": False},
            )
            state = center.build()
            self.assertNotIn("run_comfy_debug", state["allowed_actions"])
            self.assertNotEqual(state["next_action"].get("action"), "run_comfy_debug")

    def test_task_state_does_not_complete_without_final_media_after_employee_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            (task_dir / "final_output.md").write_text("# final text only\n", encoding="utf-8")
            (task_dir / "production_graph.json").write_text(
                json.dumps(
                    {
                        "jobs": [
                            {
                                "job_id": "asset_character_master",
                                "stage": "visual",
                                "mode": "character_base",
                                "workflow_id": "01_base_asset_image",
                                "depends_on": [],
                                "outputs": ["output_final_image"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            center = TaskStateCenter(
                task_dir=task_dir,
                task_name="task_test",
                summary={"status": "completed", "step_count": 9, "total_steps": 9, "final_output": str(task_dir / "final_output.md")},
                files=["final_output.md", "production_graph.json"],
            )

            state = center.build()

            self.assertNotEqual(state["state"], "completed")
            self.assertEqual(state["state"], "partial")
            self.assertTrue(any(item["code"] == "production_materials_in_progress" for item in state["diagnostics"]))

    def test_task_state_exposes_graph_jobs_before_manifest_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            job_dir = task_dir / "generated_images" / "job_asset_character_master"
            job_dir.mkdir(parents=True)
            (job_dir / "runninghub_task_state.json").write_text(
                json.dumps({"status": "RUNNING", "task_id": "remote_123"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (task_dir / "production_graph.json").write_text(
                json.dumps(
                    {
                        "jobs": [
                            {
                                "job_id": "asset_character_master",
                                "stage": "visual",
                                "mode": "character_base",
                                "workflow_id": "01_base_asset_image",
                                "depends_on": [],
                                "outputs": ["output_final_image"],
                            },
                            {
                                "job_id": "shot_001_keyframe",
                                "stage": "visual",
                                "mode": "identity_keyframe",
                                "workflow_id": "04_keyframe",
                                "depends_on": ["asset_character_master"],
                                "outputs": ["output_final_image"],
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            center = TaskStateCenter(
                task_dir=task_dir,
                task_name="task_test",
                summary={"status": "completed", "step_count": 9, "total_steps": 9, "final_output": str(task_dir / "final_output.md")},
                files=["final_output.md", "production_graph.json"],
            )

            state = center.build()

            self.assertEqual(state["state"], "running")
            self.assertTrue(state["production"]["graph_backed"])
            material = next(job for job in state["production"]["jobs"] if job["id"] == "material")
            self.assertEqual(material["status"], "running")
            node = next(job for job in state["production"]["jobs"] if job["id"] == "asset_character_master")
            self.assertEqual(node["status"], "running")

    def test_task_state_merges_graph_visual_jobs_when_manifest_has_packaging_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            job_dir = task_dir / "generated_images" / "job_asset_character_master"
            job_dir.mkdir(parents=True)
            (job_dir / "runninghub_task_state.json").write_text(
                json.dumps({"status": "SUCCESS", "task_id": "remote_123"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (job_dir / "frame.png").write_bytes(b"png")
            (task_dir / "production_manifest.json").write_text(
                json.dumps(
                    {
                        "status": "production_plan_ready",
                        "production_nodes": [
                            {"job_id": "local_tts", "stage": "08_audio_visual_packaging", "status": "pending"}
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (task_dir / "production_graph.json").write_text(
                json.dumps(
                    {
                        "jobs": [
                            {
                                "job_id": "asset_character_master",
                                "stage": "visual",
                                "mode": "character_base",
                                "workflow_id": "01_base_asset_image",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            state = TaskStateCenter(
                task_dir=task_dir,
                task_name="task_test",
                summary={"status": "completed", "step_count": 1, "total_steps": 1, "final_output": str(task_dir / "final_output.md")},
                files=["final_output.md", "production_graph.json", "production_manifest.json"],
            ).build()

            job_ids = {job["id"]: job for job in state["production"]["jobs"]}
            self.assertIn("local_tts", job_ids)
            self.assertIn("asset_character_master", job_ids)
            self.assertEqual(job_ids["asset_character_master"]["status"], "success")

    def test_task_state_labels_running_in_chinese(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            job_dir = task_dir / "generated_images" / "job_asset_character_master"
            job_dir.mkdir(parents=True)
            (job_dir / "runninghub_task_state.json").write_text(
                json.dumps({"status": "RUNNING", "task_id": "remote_123"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (task_dir / "production_graph.json").write_text(
                json.dumps({"jobs": [{"job_id": "asset_character_master", "stage": "visual"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            center = TaskStateCenter(
                task_dir=task_dir,
                task_name="task_test",
                summary={"status": "completed", "step_count": 1, "total_steps": 1, "final_output": str(task_dir / "final_output.md")},
                files=["final_output.md", "production_graph.json"],
            )

            self.assertEqual(center.build()["status_label"], "运行中")

    def test_resume_after_employee_completion_selects_material_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            (task_dir / "workflow.json").write_text(
                json.dumps({"steps": [{"agent": "01_a", "task": "a", "output": "a"}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            step_dir = task_dir / "step_01_01_a"
            step_dir.mkdir()
            (step_dir / "output.md").write_text("done\n", encoding="utf-8")
            (task_dir / "final_output.md").write_text("final text\n", encoding="utf-8")
            (task_dir / "run_summary.json").write_text(
                json.dumps({"status": "paused", "step_count": 1, "total_steps": 1}, ensure_ascii=False),
                encoding="utf-8",
            )
            (task_dir / "production_graph.json").write_text(
                json.dumps({"jobs": [{"job_id": "asset_character_master", "stage": "visual"}]}, ensure_ascii=False),
                encoding="utf-8",
            )

            self.assertEqual(web_app.WorkflowWebHandler._production_resume_job_for_task(task_dir), "material")

            (task_dir / "final_video.mp4").write_bytes(b"video")
            self.assertEqual(web_app.WorkflowWebHandler._production_resume_job_for_task(task_dir), "")

    def test_cloud_adapter_skips_failed_unreferenced_auxiliary_material(self) -> None:
        jobs = [
            {"job_id": "asset_character_turnaround", "mode": "character_turnaround", "workflow_id": "02_turnaround"},
            {"job_id": "shot_001_keyframe", "depends_on": ["asset_character_master"], "mode": "identity_keyframe"},
        ]

        self.assertTrue(
            CloudComfyUIAdapter._can_skip_failed_auxiliary_job(
                jobs[0],
                jobs,
                RuntimeError("RunningHub failed: torch.OutOfMemoryError"),
            )
        )

    def test_cloud_adapter_keeps_referenced_auxiliary_failure_blocking(self) -> None:
        jobs = [
            {"job_id": "asset_character_turnaround", "mode": "character_turnaround", "workflow_id": "02_turnaround"},
            {"job_id": "shot_001_keyframe", "depends_on": ["asset_character_turnaround"], "mode": "identity_keyframe"},
        ]

        self.assertFalse(
            CloudComfyUIAdapter._can_skip_failed_auxiliary_job(
                jobs[0],
                jobs,
                RuntimeError("RunningHub failed: torch.OutOfMemoryError"),
            )
        )

    def test_visual_prompt_policy_avoids_text_generation_cues(self) -> None:
        plan = compile_production_plan(
            task_id="task_text_guard",
            route_content=json.dumps({"production_type": "drama_story", "visual_style": "3D卡通动画"}, ensure_ascii=False),
            image_content=json.dumps(
                {
                    "production_intents": {
                        "image": [
                            {
                                "intent": "generate_base_asset",
                                "intent_id": "asset_scene_bedroom",
                                "asset_role": "scene",
                                "prompt": "温暖卧室，清晨光线，床头柜和闹钟。",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
        )

        item = plan["compiled_payload"]["image_prompts"][0]
        self.assertIn("不生成任何可读文字", item["prompt"])
        self.assertNotIn("标题、字幕和大段文字", item["prompt"])
        self.assertIn("visible text", item["negative_prompt"])

    def test_cover_title_layout_cue_becomes_clean_empty_space(self) -> None:
        plan = compile_production_plan(
            task_id="cover_title_guard",
            route_content='{"production_type":"drama_story"}',
            image_content=json.dumps(
                {
                    "production_intents": {
                        "image": [
                            {
                                "intent": "generate_cover_key_visual",
                                "intent_id": "cover_kv",
                                "prompt": "\u4e3b\u89d2\u5728\u5915\u9633\u4e0b\u5fae\u7b11\u3002\u6784\u56fe\u65f6\u4e0a1/3\u7559\u767d\uff0c\u7528\u4e8e\u53e0\u52a0\u6807\u9898\u3002",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
        )

        item = plan["compiled_payload"]["image_prompts"][0]
        self.assertIn("upper third clean empty space", item["prompt"])
        self.assertNotIn("\u7528\u4e8e\u53e0\u52a0\u6807\u9898", item["prompt"])
        self.assertNotIn("\u53e0\u52a0\u6807\u9898", item["prompt"])

    def test_runninghub_text_node_sanitizer_removes_file_tokens(self) -> None:
        rows = CloudComfyUIAdapter._sanitize_text_node_info_values(
            [
                {
                    "nodeId": "34",
                    "fieldName": "value",
                    "description": "当前镜头提示词",
                    "fieldValue": "让角色看向 openapi/abc123.png，并参考 job_asset_scene_bedroom 的构图。",
                },
                {
                    "nodeId": "2",
                    "fieldName": "image",
                    "description": "图生图参考图",
                    "fieldValue": "openapi/abc123.png",
                },
            ]
        )

        self.assertNotIn("openapi/abc123.png", rows[0]["fieldValue"])
        self.assertNotIn("job_asset_scene_bedroom", rows[0]["fieldValue"])
        self.assertEqual(rows[1]["fieldValue"], "openapi/abc123.png")

    def test_keyframe_modes_are_explicit_and_typed(self) -> None:
        modes = {item["value"]: item for item in self.workflows["04_keyframe"]["modes"]}
        self.assertEqual(modes["keyframe"]["required_inputs"], [])
        self.assertEqual(modes["identity_keyframe"]["required_inputs"], ["input_identity_image"])
        self.assertEqual(
            modes["identity_scene_keyframe"]["required_inputs"],
            ["input_identity_image", "input_scene_image"],
        )
        self.assertEqual(
            modes["pose_identity_keyframe"]["required_inputs"],
            ["input_identity_image", "input_pose_image"],
        )
        self.assertEqual(modes["multi_identity_keyframe"]["required_inputs"], ["character_references"])
        self.assertEqual(
            modes["multi_pose_identity_keyframe"]["required_inputs"],
            ["character_references", "input_pose_image"],
        )

    def test_keyframe_ui_group_lists_all_keyframe_modes(self) -> None:
        source = (WORKSPACE / "my_workspace" / "web_app.py").read_text(encoding="utf-8")
        image_to_image_group = next(
            line for line in source.splitlines() if "id: 'image_to_image'" in line
        )
        text_to_image_group = next(
            line for line in source.splitlines() if "id: 'text_to_image'" in line
        )
        self.assertIn("'keyframe'", text_to_image_group)
        self.assertIn("'identity_keyframe'", image_to_image_group)
        self.assertIn("'identity_scene_keyframe'", image_to_image_group)
        self.assertIn("'pose_identity_keyframe'", image_to_image_group)
        self.assertIn("'multi_identity_keyframe'", image_to_image_group)
        self.assertIn("'multi_pose_identity_keyframe'", image_to_image_group)
        self.assertIn("comfyDebugCharacterEntity", source)
        self.assertIn("comfyDebugIdentityAssetReference", source)
        self.assertIn("comfyDebugPoseAssetReference", source)
        self.assertNotIn("comfyDebugDenoise", source)
        self.assertIn("productionEntityTurnaround", source)

    def test_non_blocking_human_confirmation_bullet_is_not_declared(self) -> None:
        content = "## 人工确认（阻塞）\n- 无需人工确认，所有决策均在合理推断范围内。"
        self.assertFalse(declares_human_confirmation(content))

    def test_consistent_character_keyframe_presets_are_mode_specific(self) -> None:
        library = WORKSPACE / "my_workspace" / "comfyui_workflows" / "workflow_library" / "04_keyframe_image"
        identity_canvas = library / "consistent_character_identity_keyframe_canvas.json"
        pose_canvas = library / "consistent_character_pose_identity_keyframe_canvas.json"
        identity_nodeinfo = library / "consistent_character_identity_keyframe_nodeinfo.json"
        identity_scene_nodeinfo = library / "consistent_character_scene_keyframe_nodeinfo.json"
        pose_nodeinfo = library / "consistent_character_pose_identity_keyframe_nodeinfo.json"
        for path in (identity_canvas, pose_canvas, identity_nodeinfo, identity_scene_nodeinfo, pose_nodeinfo):
            self.assertTrue(path.is_file(), path)

        identity_rows = json.loads(identity_nodeinfo.read_text(encoding="utf-8"))
        identity_scene_rows = json.loads(identity_scene_nodeinfo.read_text(encoding="utf-8"))
        pose_rows = json.loads(pose_nodeinfo.read_text(encoding="utf-8"))
        self.assertIn("{{input_identity_image}}", json.dumps(identity_rows, ensure_ascii=False))
        self.assertNotIn("{{input_pose_image}}", json.dumps(identity_rows, ensure_ascii=False))
        self.assertIn("{{input_identity_image}}", json.dumps(identity_scene_rows, ensure_ascii=False))
        self.assertIn("{{input_scene_image}}", json.dumps(identity_scene_rows, ensure_ascii=False))
        identity_scene_by_node = {
            (str(row.get("nodeId")), str(row.get("fieldName"))): row.get("fieldValue")
            for row in identity_scene_rows
            if isinstance(row, dict)
        }
        self.assertEqual(identity_scene_by_node[("35", "image")], "{{input_identity_image}}")
        self.assertEqual(identity_scene_by_node[("22", "image")], "{{input_scene_image}}")
        self.assertEqual(identity_scene_by_node[("21", "prompt")], "{{prompt}}")
        self.assertEqual(identity_scene_by_node[("12", "denoise")], "{{denoise}}")
        self.assertEqual(identity_scene_by_node[("23", "denoise")], 0.2)
        self.assertNotIn(("25", "filename_prefix"), identity_scene_by_node)
        self.assertEqual(identity_scene_by_node[("33", "filename_prefix")], "identity_scene_keyframe")
        self.assertIn("{{input_identity_image}}", json.dumps(pose_rows, ensure_ascii=False))
        self.assertIn("{{input_pose_image}}", json.dumps(pose_rows, ensure_ascii=False))

        for canvas_path in (identity_canvas, pose_canvas):
            canvas = json.loads(canvas_path.read_text(encoding="utf-8"))
            node_ids = {node["id"] for node in canvas["nodes"]}
            dangling_links = [
                link for link in canvas.get("links", []) if link[1] not in node_ids or link[3] not in node_ids
            ]
            self.assertEqual(dangling_links, [])
            widget_placeholders = [
                node["id"]
                for node in canvas["nodes"]
                if any("{{" in str(value) for value in (node.get("widgets_values") or []))
            ]
            self.assertEqual(widget_placeholders, [])
            set_names = {
                str((node.get("widgets_values") or [""])[0])
                for node in canvas["nodes"]
                if node.get("type") == "SetNode"
            }
            missing_set_nodes = [
                str((node.get("widgets_values") or [""])[0])
                for node in canvas["nodes"]
                if node.get("type") == "GetNode"
                and str((node.get("widgets_values") or [""])[0]) not in set_names
            ]
            self.assertEqual(missing_set_nodes, [])
            anything_everywhere_inputs = {
                (node.get("inputs") or [{}])[0].get("name")
                for node in canvas["nodes"]
                if node.get("type") == "Anything Everywhere" and node.get("inputs")
            }
            self.assertTrue({"MODEL", "CLIP", "VAE"}.issubset(anything_everywhere_inputs))
            self.assertTrue({"PULIDFLUX", "FACEANALYSIS", "EVA_CLIP"}.issubset(anything_everywhere_inputs))

        workflows = {item["id"]: item for item in web_app.WorkflowWebHandler._comfy_debug_workflows()}
        modes = {item["value"]: item for item in workflows["04_keyframe"]["modes"]}
        self.assertIn("{{input_identity_image}}", modes["identity_keyframe"]["default_node_info"])
        self.assertIn("{{input_scene_image}}", modes["identity_scene_keyframe"]["default_node_info"])
        self.assertIn("{{input_pose_image}}", modes["pose_identity_keyframe"]["default_node_info"])

    def test_video_post_modes_require_source_video(self) -> None:
        for workflow_id in ("11_video_enhance", "12_video_inpaint_fix"):
            for mode in self.workflows[workflow_id]["modes"]:
                self.assertIn("input_source_video", mode["required_inputs"])
        self.assertEqual(self.workflows["06_i2v_first_last_frame"]["default_fps"], 24)
        self.assertEqual(self.workflows["06_i2v_first_middle_last_frame"]["asset_tag"], "i2v_first_middle_last_frame")

    def test_keyframe_with_reference_routes_to_identity_mode(self) -> None:
        item = _image_prompt_item(
            job_id="shot_001_keyframe",
            prompt="same worker in a realistic office",
            intent={"intent": "generate_keyframe", "reference_image": "character.png"},
            contract={},
            compatibility={},
            render={"working_width": 480, "working_height": 848},
            asset_tag="keyframe",
            resolved_entities={},
        )
        self.assertEqual(item["workflow_mode"], "identity_keyframe")
        self.assertEqual(item["input_identity_image"], "character.png")

    def test_character_asset_id_and_turnaround_link_to_keyframe_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            entity_path = root / "entities.json"
            library_path = root / "library.json"
            entity_path.write_text(
                json.dumps(
                    {
                        "characters": {
                            "hero": {
                                "character_id": "hero",
                                "name": "Hero",
                                "master_image": "asset_master",
                                "turnaround_images": ["asset_turnaround"],
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            library_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "asset_master",
                            "asset_id": "asset_master",
                            "file": "01_character_base/hero.png",
                            "kind": "image",
                            "tags": ["character_base"],
                            "character_id": "hero",
                            "approved": True,
                        },
                        {
                            "id": "asset_turnaround",
                            "asset_id": "asset_turnaround",
                            "file": "04_character_turnaround/hero_views.png",
                            "kind": "image",
                            "tags": ["character_turnaround"],
                            "character_id": "hero",
                            "approved": True,
                        },
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            image_content = json.dumps(
                {
                    "production_intents": {
                        "image": [
                            {
                                "intent": "generate_keyframe",
                                "intent_id": "hero_shot_001",
                                "character_id": "hero",
                                "prompt": "Hero walks through a realistic office",
                            }
                        ]
                    }
                }
            )
            plan = compile_production_plan(
                task_id="entity_link_test",
                route_content='{"production_type":"custom"}',
                image_content=image_content,
                entity_path=entity_path,
                asset_library_path=library_path,
            )
            item = plan["compiled_payload"]["image_prompts"][0]
            self.assertEqual(item["workflow_mode"], "identity_keyframe")
            self.assertEqual(
                item["input_identity_image"],
                "my_workspace/my_asset_library/01_character_base/hero.png",
            )
            self.assertIn(
                "my_workspace/my_asset_library/04_character_turnaround/hero_views.png",
                item["reference_images"],
            )

    def test_linked_character_master_routes_base_variant_to_identity_keyframe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            entity_path = root / "entities.json"
            library_path = root / "library.json"
            entity_path.write_text(
                json.dumps(
                    {
                        "characters": {
                            "hero": {
                                "character_id": "hero",
                                "name": "Hero",
                                "master_image": "asset_master",
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            library_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "asset_master",
                            "asset_id": "asset_master",
                            "file": "01_character_base/hero.png",
                            "kind": "image",
                            "tags": ["character_base"],
                            "character_id": "hero",
                            "approved": True,
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            image_content = json.dumps(
                {
                    "production_intents": {
                        "image": [
                            {
                                "intent": "generate_base_asset",
                                "intent_id": "hero_expression_frowning",
                                "asset_role": "character",
                                "character_id": "hero",
                                "prompt": "Hero frowning expression reference, same face and hair",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            )
            plan = compile_production_plan(
                task_id="linked_character_master_img2img_test",
                route_content='{"production_type":"custom"}',
                image_content=image_content,
                entity_path=entity_path,
                asset_library_path=library_path,
            )
            item = plan["compiled_payload"]["image_prompts"][0]
            self.assertEqual(item["workflow_id"], "04_keyframe")
            self.assertEqual(item["workflow_mode"], "identity_keyframe")
            self.assertEqual(item["control_mode"], "identity_reference")
            self.assertEqual(
                item["input_identity_image"],
                "my_workspace/my_asset_library/01_character_base/hero.png",
            )
            self.assertEqual(
                item["input_base_image"],
                "my_workspace/my_asset_library/01_character_base/hero.png",
            )
            self.assertEqual(
                item["reference_image"],
                "my_workspace/my_asset_library/01_character_base/hero.png",
            )
            self.assertEqual(item["denoise"], 1)
            self.assertNotEqual(item["workflow_mode"], "character_base")

    def test_three_frame_shot_uses_linked_character_master_image(self) -> None:
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_three_frame_shot",
                            "intent_id": "shot_meal_three_frame",
                            "character_id": "hero",
                            "scene_id": "dining_room",
                            "frame_set": [
                                {"role": "start", "prompt": "Hero hesitates beside the table."},
                                {"role": "middle", "prompt": "Hero leans closer to smell the food."},
                                {"role": "end", "prompt": "Hero smiles and starts eating."},
                            ],
                            "entity_usage": {
                                "character_reference_image": "my_workspace/my_asset_library/characters/hero.png",
                                "scene_reference_image": "my_workspace/my_asset_library/scenes/dining_room.png",
                            },
                        }
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="three_frame_linked_character_reference_test",
            route_content='{"production_type":"drama_story"}',
            image_content=image_content,
        )
        items = plan["compiled_payload"]["image_prompts"]
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertEqual(item["workflow_id"], "04_keyframe")
            self.assertEqual(item["workflow_mode"], "identity_scene_keyframe")
            self.assertEqual(item["control_mode"], "identity_scene_reference")
            self.assertEqual(
                item["input_identity_image"],
                "my_workspace/my_asset_library/characters/hero.png",
            )
            self.assertEqual(
                item["input_base_image"],
                "my_workspace/my_asset_library/characters/hero.png",
            )
            self.assertEqual(
                item["input_scene_image"],
                "my_workspace/my_asset_library/scenes/dining_room.png",
            )

    def test_three_frame_shot_uses_source_linked_assets_when_staff_omits_paths(self) -> None:
        linked_assets = {
            "linked_assets": {
                "characters": [
                    {
                        "character_id": "hero",
                        "name": "Hero",
                        "master_image": "my_workspace/my_asset_library/characters/hero.png",
                        "source_asset_id": "asset_hero",
                    }
                ],
                "scenes": [
                    {
                        "scene_id": "dining_room",
                        "name": "Dining room",
                        "scene_master_image": "my_workspace/my_asset_library/scenes/dining_room.png",
                        "source_asset_id": "asset_scene",
                    }
                ],
            }
        }
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_three_frame_shot",
                            "intent_id": "shot_meal_three_frame",
                            "character_id": "hero",
                            "scene_id": "dining_room",
                            "frame_set": [
                                {"role": "start", "prompt": "Hero hesitates beside the table."},
                                {"role": "middle", "prompt": "Hero leans closer to smell the food."},
                                {"role": "end", "prompt": "Hero smiles and starts eating."},
                            ],
                        }
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="three_frame_source_linked_assets_test",
            route_content='{"production_type":"drama_story"}',
            image_content=image_content,
            source_content="```json\n" + json.dumps(linked_assets, ensure_ascii=False) + "\n```",
        )
        items = plan["compiled_payload"]["image_prompts"]
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertEqual(item["workflow_mode"], "identity_scene_keyframe")
            self.assertEqual(
                item["input_identity_image"],
                "my_workspace/my_asset_library/characters/hero.png",
            )
            self.assertEqual(
                item["input_base_image"],
                "my_workspace/my_asset_library/characters/hero.png",
            )
            self.assertEqual(
                item["input_scene_image"],
                "my_workspace/my_asset_library/scenes/dining_room.png",
            )

    def test_linked_character_asset_without_entity_id_promotes_to_referenced_character(self) -> None:
        linked_assets = {
            "linked_assets": {
                "assets": [
                    {
                        "asset_id": "asset_xiaomei",
                        "name": "Xiaomei base image",
                        "file": "my_workspace/my_asset_library/01_character_base/xiaomei.png",
                        "kind": "image",
                        "tags": ["image", "character_base"],
                        "character_id": "",
                    }
                ],
                "characters": [],
                "scenes": [],
            }
        }
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_character_xiaomei_master",
                            "asset_role": "character",
                            "character_id": "character_xiaomei",
                            "prompt": "Generate Xiaomei base asset.",
                        },
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "shot_xiaomei_office",
                            "character_id": "character_xiaomei",
                            "prompt": "Xiaomei works at an office desk.",
                        },
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="linked_character_asset_without_entity_id_test",
            route_content='{"production_type":"drama_story"}',
            image_content=image_content,
            source_content="```json\n" + json.dumps(linked_assets, ensure_ascii=False) + "\n```",
        )
        master_path = "my_workspace/my_asset_library/01_character_base/xiaomei.png"
        character = plan["resolved_entities"]["characters"][0]
        self.assertEqual(character["character_id"], "character_xiaomei")
        self.assertEqual(character["master_image"], master_path)

        base_item, keyframe = plan["compiled_payload"]["image_prompts"][:2]
        self.assertEqual(base_item["workflow_id"], "04_keyframe")
        self.assertEqual(base_item["workflow_mode"], "identity_keyframe")
        self.assertEqual(base_item["control_mode"], "identity_reference")
        self.assertEqual(base_item["input_identity_image"], master_path)
        self.assertEqual(base_item["input_base_image"], master_path)
        self.assertEqual(base_item["denoise"], 1)
        self.assertEqual(keyframe["workflow_mode"], "identity_keyframe")
        self.assertEqual(keyframe["input_identity_image"], master_path)
        self.assertEqual(keyframe["input_base_image"], master_path)
        self.assertEqual(keyframe["denoise"], 1)

    def test_single_character_generic_keyframe_becomes_identity_anchor(self) -> None:
        reference_path = "my_workspace/my_asset_library/07_keyframe/ref_keyframe.png"
        linked_assets = {
            "linked_assets": {
                "assets": [
                    {
                        "asset_id": "asset_keyframe_ref",
                        "name": "Selected keyframe reference",
                        "file": reference_path,
                        "kind": "image",
                        "tags": ["image", "keyframe"],
                    }
                ],
                "characters": [],
                "scenes": [],
            }
        }
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "base_character_hero",
                            "asset_role": "character",
                            "character_id": "hero",
                            "prompt": "Generate the main character base image.",
                        },
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "keyframe_empty_room",
                            "character_id": "",
                            "prompt": "Empty living room wide shot, afternoon window light.",
                        },
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="generic_linked_keyframe_style_reference_test",
            route_content='{"production_type":"drama_story"}',
            image_content=image_content,
            source_content="```json\n" + json.dumps(linked_assets, ensure_ascii=False) + "\n```",
        )

        assignment = plan["reference_assignments"][0]
        self.assertEqual(assignment["role"], "identity_reference")
        self.assertEqual(assignment["character_id"], "hero")

        items = {item["job_id"]: item for item in plan["compiled_payload"]["image_prompts"]}
        base_item = items["base_character_hero"]
        self.assertEqual(base_item["workflow_id"], "04_keyframe")
        self.assertEqual(base_item["workflow_mode"], "identity_keyframe")
        self.assertEqual(base_item["control_mode"], "identity_reference")
        self.assertEqual(base_item["input_base_image"], reference_path)
        self.assertEqual(base_item["input_identity_image"], reference_path)
        self.assertEqual(base_item["identity_anchor"]["source"], "external_identity_anchor")

        empty_room = items["keyframe_empty_room"]
        self.assertEqual(empty_room["workflow_id"], "04_keyframe")
        self.assertEqual(empty_room["workflow_mode"], "keyframe")
        self.assertNotIn("input_reference_style", empty_room)
        self.assertNotIn("input_identity_image", empty_room)

        prompt_text = "\n".join(str(item.get("prompt") or "") for item in items.values())
        self.assertNotIn("asset_keyframe_ref", prompt_text)
        self.assertNotIn("ref_keyframe.png", prompt_text)

    def test_scene_base_does_not_inherit_linked_character_reference(self) -> None:
        linked_assets = {
            "linked_assets": {
                "assets": [
                    {
                        "asset_id": "asset_xiaomei",
                        "name": "Xiaomei base image",
                        "file": "my_workspace/my_asset_library/01_character_base/xiaomei.png",
                        "kind": "image",
                        "tags": ["image", "character_base"],
                        "character_id": "",
                    }
                ],
                "characters": [],
                "scenes": [],
            }
        }
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "base_asset_scene_office",
                            "asset_role": "scene",
                            "character_id": "character_xiaomei",
                            "scene_id": "scene_office",
                            "prompt": "Bright office desk background with monitor, keyboard, white coffee cup and plant.",
                        }
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="scene_base_linked_character_isolation_test",
            route_content='{"production_type":"drama_story"}',
            image_content=image_content,
            source_content="```json\n" + json.dumps(linked_assets, ensure_ascii=False) + "\n```",
        )

        item = plan["compiled_payload"]["image_prompts"][0]
        self.assertEqual(item["workflow_id"], "01_base_asset_image")
        self.assertEqual(item["workflow_mode"], "scene_base")
        self.assertEqual(item["character_id"], "")
        self.assertEqual(item["reference_images"], [])
        self.assertNotIn("input_base_image", item)
        self.assertNotIn("input_identity_image", item)
        self.assertIn("Background/location plate only", item["prompt"])

    def test_environment_keyframe_with_identity_lock_false_does_not_inherit_single_character(self) -> None:
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_xiaomei_master",
                            "asset_role": "character",
                            "character_id": "character_xiaomei",
                            "prompt": "Xiaomei portrait reference.",
                        },
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "keyframe_foodstall_skewers",
                            "character_id": "",
                            "scene_id": "scene_foodstreet",
                            "prompt": "Hotpot skewer stall wide shot, red oil pot boiling, steam and neon lights.",
                            "constraints": {"identity_lock": False, "style_lock": True},
                        },
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="environment_keyframe_identity_lock_false_test",
            route_content='{"production_type":"drama_story"}',
            image_content=image_content,
        )

        item = {row["job_id"]: row for row in plan["compiled_payload"]["image_prompts"]}["keyframe_foodstall_skewers"]
        self.assertEqual(item["workflow_mode"], "keyframe")
        self.assertEqual(item["character_id"], "")
        self.assertNotIn("input_base_image", item)
        self.assertNotIn("input_identity_image", item)
        self.assertFalse(
            any(
                override["intent_id"] == "keyframe_foodstall_skewers"
                and override["field"] == "character_id"
                for override in plan["parameter_overrides"]
            )
        )

    def test_background_assets_bind_keyframes_as_scene_references(self) -> None:
        linked_assets = {
            "linked_assets": {
                "assets": [
                    {
                        "asset_id": "asset_xiaomei",
                        "file": "my_workspace/my_asset_library/01_character_base/xiaomei.png",
                        "kind": "image",
                        "tags": ["image", "character_base"],
                        "character_id": "",
                    }
                ],
                "characters": [],
                "scenes": [],
            }
        }
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "base_scene_morning",
                            "asset_role": "background",
                            "scene_id": "scene_morning_home",
                            "prompt": "Messy bedroom background plate, morning light.",
                        },
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "base_bg_corridor",
                            "asset_role": "background",
                            "scene_id": "scene_morning_home",
                            "prompt": "Apartment corridor background plate.",
                        },
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "keyframe_shot_001",
                            "character_id": "character_xiaomei",
                            "scene_id": "scene_morning_home",
                            "prompt": "Xiaomei sits up on the messy bed, tired face, morning light.",
                        },
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="background_scene_keyframe_binding_test",
            route_content='{"production_type":"drama_story"}',
            image_content=image_content,
            source_content="```json\n" + json.dumps(linked_assets, ensure_ascii=False) + "\n```",
        )

        scene, corridor, keyframe = plan["compiled_payload"]["image_prompts"]
        self.assertEqual(scene["workflow_mode"], "scene_base")
        self.assertEqual(corridor["workflow_mode"], "scene_base")
        self.assertEqual(scene["character_id"], "")
        self.assertEqual(keyframe["workflow_mode"], "identity_scene_keyframe")
        self.assertEqual(keyframe["control_mode"], "identity_scene_reference")
        self.assertEqual(
            keyframe["input_identity_image"],
            "my_workspace/my_asset_library/01_character_base/xiaomei.png",
        )
        self.assertEqual(
            keyframe["input_bindings"]["input_scene_image"],
            {"from_job": "base_scene_morning", "output": "output_final_image"},
        )
        self.assertIn("base_scene_morning", keyframe["depends_on"])

    def test_linked_character_front_expression_is_not_turnaround(self) -> None:
        linked_assets = {
            "linked_assets": {
                "assets": [
                    {
                        "asset_id": "asset_xiaomei",
                        "name": "Xiaomei base image",
                        "file": "my_workspace/my_asset_library/01_character_base/xiaomei.png",
                        "kind": "image",
                        "tags": ["image", "character_base"],
                        "character_id": "",
                    }
                ],
                "characters": [],
                "scenes": [],
            }
        }
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "base_asset_xiaomei_expression_warm_smile",
                            "asset_role": "character",
                            "character_id": "character_xiaomei",
                            "prompt": "小美正面微笑表情，穿着浅蓝色衬衫，头发自然披肩，表情温暖亲切，背景淡出。",
                        }
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="linked_character_front_expression_test",
            route_content='{"production_type":"drama_story"}',
            image_content=image_content,
            source_content="```json\n" + json.dumps(linked_assets, ensure_ascii=False) + "\n```",
        )
        item = plan["compiled_payload"]["image_prompts"][0]
        self.assertEqual(item["workflow_id"], "04_keyframe")
        self.assertEqual(item["workflow_mode"], "identity_keyframe")
        self.assertEqual(item["control_mode"], "identity_reference")
        self.assertEqual(
            item["input_identity_image"],
            "my_workspace/my_asset_library/01_character_base/xiaomei.png",
        )
        self.assertEqual(
            item["input_base_image"],
            "my_workspace/my_asset_library/01_character_base/xiaomei.png",
        )
        self.assertEqual(item["denoise"], 1)

    def test_animal_reference_sheet_stays_on_character_base(self) -> None:
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_character_corgi_turnaround",
                            "asset_role": "character",
                            "character_id": "corgi_king",
                            "prompt": "柯基狗狗主角三视图，正面、侧面、背面，星球国王披风",
                        }
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="animal_reference_sheet_test",
            route_content='{"production_type":"custom"}',
            image_content=image_content,
        )
        item = plan["compiled_payload"]["image_prompts"][0]
        self.assertEqual(item["workflow_id"], "01_base_asset_image")
        self.assertEqual(item["workflow_mode"], "character_base")
        self.assertTrue(item["animal_character_reference_sheet"])
        self.assertIn("不要人型骨架", item["prompt"])
        self.assertIn("四足动物", item["prompt"])

    def test_animal_expression_sheet_uses_previous_reference_img2img(self) -> None:
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_character_corgi_turnaround",
                            "asset_role": "character",
                            "character_id": "corgi_king",
                            "prompt": "柯基狗狗主角三视图，正面、侧面、背面，星球国王披风",
                        },
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_character_corgi_emotions",
                            "asset_role": "character",
                            "character_id": "corgi_king",
                            "prompt": "柯基狗狗主角表情图，开心、惊讶、坚定、害怕",
                        },
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="animal_expression_sheet_test",
            route_content='{"production_type":"custom"}',
            image_content=image_content,
        )
        items = {item["job_id"]: item for item in plan["compiled_payload"]["image_prompts"]}
        expression = items["asset_character_corgi_emotions"]
        self.assertEqual(expression["workflow_id"], "04_keyframe")
        self.assertEqual(expression["workflow_mode"], "img2img_style_keyframe")
        self.assertEqual(expression["control_mode"], "img2img_style")
        self.assertEqual(
            expression["input_bindings"]["input_base_image"],
            {"from_job": "asset_character_corgi_turnaround", "output": "output_final_image"},
        )
        self.assertIn("asset_character_corgi_turnaround", expression["depends_on"])
        self.assertEqual(expression["denoise"], 1)
        self.assertIn("不变成人型", expression["prompt"])

    def test_human_expression_sheet_uses_previous_reference_img2img(self) -> None:
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_character_main_base",
                            "asset_role": "character",
                            "character_id": "character_main",
                            "prompt": "30-40岁中国男性角色母版图，土黄色旧夹克，深蓝工装裤",
                        },
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_character_expression_determined",
                            "asset_role": "character",
                            "character_id": "character_main",
                            "denoise": 0.6,
                            "prompt": "角色表情图：坚定、自信、笃定，下巴微扬，目光平视前方",
                        },
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="human_expression_sheet_test",
            route_content='{"production_type":"custom"}',
            image_content=image_content,
        )
        items = {item["job_id"]: item for item in plan["compiled_payload"]["image_prompts"]}
        expression = items["asset_character_expression_determined"]
        self.assertEqual(expression["workflow_id"], "04_keyframe")
        self.assertEqual(expression["workflow_mode"], "img2img_style_keyframe")
        self.assertEqual(
            expression["input_bindings"]["input_base_image"],
            {"from_job": "asset_character_main_base", "output": "output_final_image"},
        )
        self.assertEqual(expression["denoise"], 0.6)
        self.assertIn("不换脸", expression["prompt"])
        self.assertIn("不换衣服", expression["prompt"])

    def test_generated_expression_assets_bind_first_character_master(self) -> None:
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_piggy_worker_front",
                            "asset_role": "character",
                            "character_id": "piggy_worker",
                            "prompt": "Piggy worker front master, white shirt, red tie, blue pants.",
                        },
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_piggy_worker_expression_tired",
                            "asset_role": "expression",
                            "character_id": "piggy_worker",
                            "prompt": "Piggy worker tired expression, same outfit as master.",
                        },
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_piggy_worker_expression_happy",
                            "asset_role": "expression",
                            "character_id": "piggy_worker",
                            "prompt": "Piggy worker happy expression, same outfit as master.",
                        },
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="generated_expression_master_binding_test",
            route_content='{"production_type":"custom"}',
            image_content=image_content,
        )
        items = {item["job_id"]: item for item in plan["compiled_payload"]["image_prompts"]}
        tired = items["asset_piggy_worker_expression_tired"]
        happy = items["asset_piggy_worker_expression_happy"]
        for expression in (tired, happy):
            self.assertEqual(expression["workflow_id"], "04_keyframe")
            self.assertEqual(expression["workflow_mode"], "img2img_style_keyframe")
            self.assertEqual(
                expression["input_bindings"]["input_base_image"],
                {"from_job": "asset_piggy_worker_front", "output": "output_final_image"},
            )
            self.assertIn("asset_piggy_worker_front", expression["depends_on"])
        self.assertNotIn("asset_piggy_worker_expression_tired", happy["depends_on"])
        self.assertNotEqual(
            happy["input_bindings"]["input_base_image"]["from_job"],
            "asset_piggy_worker_expression_tired",
        )

    def test_linked_character_img2img_uses_concise_edit_prompt(self) -> None:
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "shot_01_hesitate_keyframe",
                            "character_id": "char_xiaomei",
                            "scene_id": "scene_dining",
                            "negative_prompt": "nudity, sexual content, gore, unsafe content, distorted body, distorted face, flicker, low quality",
                            "prompt": (
                                "platform-safe non-graphic video, fully clothed subjects, family-safe action tone, "
                                "\u5c0f\u7f8e\uff08character_id: char_xiaomei\uff09\u7ad9\u5728\u5173\u8054\u573a\u666f\uff08scene_id: scene_dining\uff09\u7684\u9910\u684c\u5de6\u4fa7\u3002"
                                "\u684c\u4e0a\u653e\u6709\u4e00\u7897\u70ed\u996d\uff0c\u84b8\u6c7d\u8885\u8885\u3002"
                                "\u5c0f\u7f8e\u76ee\u5149\u843d\u5728\u996d\u7897\u4e0a\uff0c\u8868\u60c5\u8fdf\u7591\u3002"
                                "\u670d\u88c5\u3001\u53d1\u578b\u3001\u4e94\u5b98\u4e0e\u89d2\u8272\u6bcd\u7248\u56fe\u5b8c\u5168\u4e00\u81f4\u3002"
                                "\u80cc\u666f\u4e3a\u5173\u8054\u573a\u666f\u56fa\u5b9a\u89c6\u89d2\u3002"
                                "\u7ad6\u5c4f9:16\uff0c\u5de5\u4f5c\u5c3a\u5bf8480x848\u3002 "
                                "\u53c2\u8003\u5173\u8054\u89d2\u8272\u6bcd\u7248\u56fe\uff0c\u5fc5\u987b\u4fdd\u6301\u540c\u4e00\u5f20\u8138\u3001\u540c\u4e00\u5e74\u9f84\u611f\u3001\u540c\u4e00\u53d1\u578b\u3001\u80a4\u8272\u3001\u4e94\u5b98\u6bd4\u4f8b\u3001\u8eab\u6750\u6bd4\u4f8b\u548c\u670d\u88c5\u4e3b\u7279\u5f81\uff1b"
                                "\u53ea\u6539\u53d8\u5f53\u524d\u955c\u5934\u8981\u6c42\u7684\u8868\u60c5\u3001\u52a8\u4f5c\u548c\u8f7b\u5fae\u72b6\u6001\uff0c\u4e0d\u968f\u673a\u6362\u4eba\u3002"
                            ),
                        }
                    ]
                }
            },
            ensure_ascii=False,
        )
        source_content = json.dumps(
            {
                "linked_assets": {
                    "characters": [
                        {
                            "character_id": "char_xiaomei",
                            "master_image": "my_workspace/my_asset_library/characters/xiaomei.png",
                        }
                    ],
                    "scenes": [
                        {
                            "scene_id": "scene_dining",
                            "scene_master_image": "my_workspace/my_asset_library/scenes/dining.png",
                        }
                    ],
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="linked_character_concise_edit_prompt_test",
            route_content='{"production_type":"custom"}',
            image_content=image_content,
            source_content=source_content,
        )
        item = plan["compiled_payload"]["image_prompts"][0]
        self.assertEqual(item["workflow_mode"], "identity_scene_keyframe")
        self.assertEqual(item["control_mode"], "identity_scene_reference")
        self.assertEqual(item["input_identity_image"], "my_workspace/my_asset_library/characters/xiaomei.png")
        self.assertEqual(item["input_base_image"], "my_workspace/my_asset_library/characters/xiaomei.png")
        self.assertEqual(item["input_scene_image"], "my_workspace/my_asset_library/scenes/dining.png")
        self.assertTrue(item["prompt"].startswith("\u8ba9\u56fe\u4e2d\u4eba\u7269"))
        self.assertIn("\u9910\u684c", item["prompt"])
        self.assertIn("\u70ed\u996d", item["prompt"])
        self.assertNotIn("character_id", item["prompt"])
        self.assertNotIn("scene_id", item["prompt"])
        self.assertNotIn("\u5de5\u4f5c\u5c3a\u5bf8", item["prompt"])
        self.assertNotIn("\u53c2\u8003\u5173\u8054\u89d2\u8272\u6bcd\u7248\u56fe", item["prompt"])
        self.assertEqual(item["negative_prompt"], "")
        self.assertIn("production_prompt_before_img2img_edit", item)

    def test_same_character_variant_and_unlabeled_protagonist_keyframe_bind_master(self) -> None:
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_character_protagonist_2008",
                            "asset_role": "character",
                            "character_id": "character_protagonist",
                            "prompt": "主角2008年版本，疲惫，穿旧格子衬衫和旧拖鞋",
                        },
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_character_protagonist_present",
                            "asset_role": "character",
                            "character_id": "character_protagonist",
                            "prompt": "主角逆袭后版本，同一张脸，穿深色商务休闲夹克",
                        },
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "shot_005_three_frame_end_frame",
                            "prompt": "特写：主角双眼完全睁开，瞳孔放大，表情震惊，旧数码相机质感",
                        },
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="same_character_generated_reference_test",
            route_content='{"production_type":"custom"}',
            image_content=image_content,
        )
        items = {item["job_id"]: item for item in plan["compiled_payload"]["image_prompts"]}
        variant = items["asset_character_protagonist_present"]
        self.assertEqual(variant["workflow_mode"], "img2img_style_keyframe")
        self.assertEqual(
            variant["input_bindings"]["input_base_image"],
            {"from_job": "asset_character_protagonist_2008", "output": "output_final_image"},
        )
        keyframe = items["shot_005_three_frame_end_frame"]
        self.assertEqual(keyframe["character_id"], "character_protagonist")
        self.assertEqual(keyframe["workflow_mode"], "identity_keyframe")
        self.assertEqual(
            keyframe["input_bindings"]["input_identity_image"],
            {"from_job": "asset_character_protagonist_2008", "output": "output_final_image"},
        )
        self.assertEqual(
            keyframe["input_bindings"]["input_base_image"],
            {"from_job": "asset_character_protagonist_2008", "output": "output_final_image"},
        )

    def test_cross_id_same_face_character_variant_binds_referenced_master(self) -> None:
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_char_loser_front",
                            "asset_role": "character",
                            "character_id": "char_main_loser",
                            "prompt": "30岁男性，面容疲惫，穿洗旧格子衬衫",
                        },
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_char_winner_front",
                            "asset_role": "character",
                            "character_id": "char_main_winner",
                            "prompt": "30岁男性，与char_main_loser同一面容，但气质不同，穿修身皮夹克",
                        },
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="cross_id_same_face_test",
            route_content='{"production_type":"custom"}',
            image_content=image_content,
        )
        items = {item["job_id"]: item for item in plan["compiled_payload"]["image_prompts"]}
        winner = items["asset_char_winner_front"]
        self.assertEqual(winner["workflow_mode"], "img2img_style_keyframe")
        self.assertEqual(
            winner["input_bindings"]["input_base_image"],
            {"from_job": "asset_char_loser_front", "output": "output_final_image"},
        )

    def test_multi_character_keyframe_compiles_character_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            entity_path, library_path = self._write_two_character_fixture(root)
            image_content = json.dumps(
                {
                    "production_intents": {
                        "image": [
                            {
                                "intent": "generate_keyframe",
                                "intent_id": "duo_shot_001",
                                "prompt": "Hero and mentor speak across a console",
                                "characters": [
                                    {"character_id": "hero", "role_in_frame": "lead", "position": "left", "identity_priority": 1},
                                    {"character_id": "mentor", "role_in_frame": "mentor", "position": "right", "identity_priority": 2},
                                ],
                            }
                        ]
                    }
                }
            )
            plan = compile_production_plan(
                task_id="multi_character_test",
                route_content='{"production_type":"custom"}',
                image_content=image_content,
                entity_path=entity_path,
                asset_library_path=library_path,
            )
            item = plan["compiled_payload"]["image_prompts"][0]
            self.assertEqual(item["workflow_mode"], "multi_identity_keyframe")
            self.assertEqual(item["control_mode"], "multi_identity_reference")
            self.assertEqual([entry["character_id"] for entry in item["character_references"]], ["hero", "mentor"])
            self.assertEqual(item["character_references"][0]["identity_image"], "my_workspace/my_asset_library/01_character_base/hero.png")
            self.assertEqual(item["character_references"][1]["position"], "right")

    def test_generated_character_masters_feed_multi_character_keyframe(self) -> None:
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_hero_front",
                            "asset_role": "character",
                            "character_id": "hero",
                            "prompt": "Hero full body reference",
                        },
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_mentor_front",
                            "asset_role": "character",
                            "character_id": "mentor",
                            "prompt": "Mentor full body reference",
                        },
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "duo_shot_generated_refs",
                            "prompt": "Hero and mentor face each other",
                            "characters": [
                                {"character_id": "hero", "position": "left"},
                                {"character_id": "mentor", "position": "right"},
                            ],
                        },
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="generated_multi_character_refs_test",
            route_content='{"production_type":"custom"}',
            image_content=image_content,
        )
        context = plan["global_context"]
        self.assertTrue(context["parameter_policy"]["locks"]["character_identity"]["enabled"])
        items = {item["job_id"]: item for item in plan["compiled_payload"]["image_prompts"]}
        item = items["duo_shot_generated_refs"]
        self.assertEqual(item["workflow_mode"], "multi_identity_keyframe")
        self.assertEqual(item["control_mode"], "multi_identity_reference")
        self.assertEqual(item["depends_on"], ["asset_hero_front", "asset_mentor_front"])
        self.assertEqual(
            item["character_references"][0]["identity_binding"],
            {"from_job": "asset_hero_front", "output": "output_final_image"},
        )
        self.assertEqual(
            item["character_references"][1]["identity_binding"],
            {"from_job": "asset_mentor_front", "output": "output_final_image"},
        )

    def test_generated_character_master_feeds_single_identity_keyframe(self) -> None:
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_hero_front",
                            "asset_role": "character",
                            "character_id": "hero",
                            "prompt": "Hero full body reference",
                        },
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "hero_closeup",
                            "character_id": "hero",
                            "prompt": "Hero close-up in the same outfit",
                        },
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="generated_single_character_ref_test",
            route_content='{"production_type":"custom"}',
            image_content=image_content,
        )
        items = {item["job_id"]: item for item in plan["compiled_payload"]["image_prompts"]}
        keyframe = items["hero_closeup"]
        self.assertEqual(keyframe["workflow_mode"], "identity_keyframe")
        self.assertEqual(
            keyframe["input_bindings"]["input_identity_image"],
            {"from_job": "asset_hero_front", "output": "output_final_image"},
        )
        self.assertEqual(
            keyframe["input_bindings"]["input_base_image"],
            {"from_job": "asset_hero_front", "output": "output_final_image"},
        )

    def test_linked_character_master_wins_over_generated_master(self) -> None:
        linked_assets = {
            "linked_assets": {
                "characters": [
                    {
                        "character_id": "hero",
                        "name": "Hero",
                        "master_image": "my_workspace/my_asset_library/characters/hero.png",
                    }
                ]
            }
        }
        image_content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_base_asset",
                            "intent_id": "asset_hero_front",
                            "asset_role": "character",
                            "character_id": "hero",
                            "prompt": "Hero alternate expression",
                        },
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "hero_keyframe",
                            "character_id": "hero",
                            "prompt": "Hero walks through the room",
                        },
                    ]
                }
            },
            ensure_ascii=False,
        )
        plan = compile_production_plan(
            task_id="linked_master_priority_test",
            route_content='{"production_type":"custom"}',
            image_content=image_content,
            source_content="```json\n" + json.dumps(linked_assets, ensure_ascii=False) + "\n```",
        )
        items = {item["job_id"]: item for item in plan["compiled_payload"]["image_prompts"]}
        keyframe = items["hero_keyframe"]
        self.assertEqual(keyframe["workflow_mode"], "identity_keyframe")
        self.assertEqual(keyframe["input_identity_image"], "my_workspace/my_asset_library/characters/hero.png")
        self.assertNotIn("input_identity_image", keyframe.get("input_bindings", {}))

    def test_character_reference_identity_binding_resolves_from_job_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "hero.png"
            Image.new("RGB", (8, 8), "red").save(image_path)
            adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
            job = {
                "job_id": "duo",
                "character_references": [
                    {
                        "character_id": "hero",
                        "identity_binding": {"from_job": "asset_hero_front", "output": "output_final_image"},
                    }
                ],
            }
            state = {
                "jobs": {
                    "asset_hero_front": {
                        "artifacts": [
                            {
                                "output_name": "output_final_image",
                                "path": str(image_path),
                            }
                        ]
                    }
                }
            }
            resolved_job, resolved_inputs, missing = adapter._apply_explicit_input_bindings(job, state)
            self.assertEqual(missing, [])
            self.assertEqual(resolved_job["character_references"][0]["identity_image"], str(image_path))
            self.assertEqual(resolved_inputs["character_references[1].identity_image"], str(image_path))

    def test_multi_character_keyframe_without_identity_images_falls_back_to_text_keyframe(self) -> None:
        item = _image_prompt_item(
            job_id="duo_shot_missing_refs",
            prompt="Hero and mentor speak across a console",
            intent={
                "intent": "generate_keyframe",
                "characters": [
                    {"character_id": "hero", "position": "left"},
                    {"character_id": "mentor", "position": "right"},
                ],
            },
            contract={},
            compatibility={},
            render={"working_width": 480, "working_height": 848},
            asset_tag="keyframe",
            resolved_entities={},
            notes=[],
        )
        self.assertEqual(item["workflow_id"], "04_keyframe")
        self.assertEqual(item["workflow_mode"], "keyframe")
        self.assertEqual(item["character_references"], [])

    def test_multi_character_pose_keyframe_uses_pose_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            entity_path, library_path = self._write_two_character_fixture(root)
            image_content = json.dumps(
                {
                    "production_intents": {
                        "image": [
                            {
                                "intent": "generate_keyframe",
                                "intent_id": "duo_pose_shot_001",
                                "prompt": "Hero and mentor stand back to back",
                                "pose_layout_image": "poses/duo_openpose.png",
                                "characters": [
                                    {"character_id": "hero", "position": "left"},
                                    {"character_id": "mentor", "position": "right"},
                                ],
                            }
                        ]
                    }
                }
            )
            plan = compile_production_plan(
                task_id="multi_pose_test",
                route_content='{"production_type":"custom"}',
                image_content=image_content,
                entity_path=entity_path,
                asset_library_path=library_path,
            )
            item = plan["compiled_payload"]["image_prompts"][0]
            self.assertEqual(item["workflow_mode"], "multi_pose_identity_keyframe")
            self.assertEqual(item["input_pose_image"], "poses/duo_openpose.png")

    def test_video_source_binding_uses_video_output(self) -> None:
        item = {"source_intent_ids": ["clip_001"], "input_bindings": {}, "depends_on": []}
        _bind_first_source_video(item, {"clip_001"})
        self.assertEqual(
            item["input_bindings"]["input_source_video"],
            {"from_job": "clip_001", "output": "output_final_video"},
        )
        self.assertIn("clip_001", item["depends_on"])

    def test_broll_with_visible_character_is_promoted_to_i2v(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            entity_path = root / "entities.json"
            library_path = root / "library.json"
            entity_path.write_text(
                json.dumps(
                    {
                        "characters": {
                            "corgi_king": {
                                "character_id": "corgi_king",
                                "name": "Corgi King",
                                "aliases": ["柯基国王"],
                                "master_image": "asset_corgi",
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            library_path.write_text(
                json.dumps(
                    [
                        {
                            "id": "asset_corgi",
                            "asset_id": "asset_corgi",
                            "file": "01_character_base/corgi.png",
                            "kind": "image",
                            "tags": ["character_base"],
                            "character_id": "corgi_king",
                            "approved": True,
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            video_content = json.dumps(
                {
                    "production_intents": {
                        "video": [
                            {
                                "intent": "generate_broll_clip",
                                "intent_id": "broll_palace",
                                "character_id": "corgi_king",
                                "prompt": "Corgi King 柯基国王走进外星宫殿，镜头扫过王座和发光星云",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            )
            plan = compile_production_plan(
                task_id="broll_character_sanitize_test",
                route_content='{"production_type":"custom"}',
                video_content=video_content,
                entity_path=entity_path,
                asset_library_path=library_path,
            )
            item = plan["compiled_payload"]["video_prompts"][0]
            self.assertEqual(item["workflow_id"], "06_i2v_first_frame")
            self.assertEqual(item["workflow_mode"], "i2v_first_frame")
            self.assertEqual(item["character_id"], "corgi_king")
            self.assertFalse(item.get("no_visible_characters", False))
            self.assertEqual(item["input_bindings"]["input_base_image"]["from_job"], "broll_palace_keyframe")
            self.assertIn("broll_palace_keyframe", {row["job_id"] for row in plan["compiled_payload"]["image_prompts"]})

    def test_i2v_missing_source_generates_keyframe_instead_of_broll(self) -> None:
        plan = compile_production_plan(
            task_id="i2v_missing_source_keyframe",
            route_content=json.dumps(
                {
                    "production_type": "drama_story",
                    "aspect_ratio": "9:16",
                    "global_context": {"characters": [{"character_id": "protagonist", "name": "主角"}]},
                },
                ensure_ascii=False,
            ),
            image_content=json.dumps(
                {
                    "production_intents": {
                        "image": [
                            {
                                "intent": "generate_base_asset",
                                "intent_id": "hero_master",
                                "asset_role": "character",
                                "character_id": "protagonist",
                                "prompt": "2008年普通打工人主角，真人纪实感，同一张脸。",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            video_content=json.dumps(
                {
                    "production_intents": {
                        "video": [
                            {
                                "intent": "generate_i2v_clip",
                                "intent_id": "clip_001",
                                "character_id": "protagonist",
                                "duration_seconds": 2,
                                "prompt": "主角从出租屋醒来，看着2008年的旧手机，真人复古纪实画面。",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
        )
        payload = plan["compiled_payload"]
        video_item = payload["video_prompts"][0]
        self.assertEqual(video_item["workflow_id"], "06_i2v_first_frame")
        self.assertEqual(video_item["workflow_mode"], "i2v_first_frame")
        self.assertEqual(video_item["duration"], 4)
        self.assertEqual(video_item["fps"], 24)
        self.assertIn("input_base_image", video_item["input_bindings"])
        self.assertEqual(video_item["input_bindings"]["input_base_image"]["from_job"], "clip_001_keyframe")
        self.assertIn("clip_001_keyframe", {row["job_id"] for row in payload["image_prompts"]})

    def test_legacy_i2v_binding_restores_missing_keyframe_after_image_recompile(self) -> None:
        plan = compile_production_plan(
            task_id="legacy_i2v_retry_keyframe_restore",
            route_content=json.dumps(
                {
                    "production_type": "custom",
                    "aspect_ratio": "9:16",
                },
                ensure_ascii=False,
            ),
            image_content=json.dumps(
                {
                    "production_intents": {
                        "image": [
                            {
                                "intent": "generate_base_asset",
                                "intent_id": "asset_person_stand",
                                "asset_role": "character",
                                "character_id": "person_outline",
                                "prompt": "简化人体轮廓站立母版。",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            video_content=json.dumps(
                {
                    "video_prompts": [
                        {
                            "job_id": "clip_03_b_roll",
                            "workflow_id": "06_i2v_first_frame",
                            "workflow_mode": "i2v_first_frame",
                            "character_id": "person_outline",
                            "prompt": "人体轮廓做简单下落演示动画。",
                            "input_bindings": {
                                "input_base_image": {
                                    "from_job": "clip_03_b_roll_keyframe",
                                    "output": "output_final_image",
                                }
                            },
                            "depends_on": ["clip_03_b_roll_keyframe"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            existing_payload={
                "image_prompts": [
                    {
                        "job_id": "stale_legacy_image",
                        "workflow_id": "04_keyframe",
                        "workflow_mode": "keyframe",
                        "prompt": "stale legacy image should be replaced by compiled image intents",
                    }
                ]
            },
        )
        payload = plan["compiled_payload"]
        image_ids = {row["job_id"] for row in payload["image_prompts"]}
        self.assertIn("asset_person_stand", image_ids)
        self.assertIn("clip_03_b_roll_keyframe", image_ids)
        self.assertNotIn("stale_legacy_image", image_ids)
        self.assertIn("clip_03_b_roll_keyframe", {row["job_id"] for row in plan["visual_jobs"] if row["type"] == "image"})

    def test_task_comfy_debug_resolves_input_binding_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir)
            comfy_dir = task_dir / "comfyui"
            comfy_dir.mkdir(parents=True)
            (comfy_dir / "comfyui_payload.json").write_text(
                json.dumps(
                    {
                        "image_prompts": [
                            {
                                "id": "asset_char_main_front",
                                "job_id": "asset_char_main_front",
                                "workflow_id": "01_base_asset_image",
                                "workflow_mode": "character_base",
                                "prompt": "主角母版",
                            },
                            {
                                "id": "shot_001_keyframe",
                                "job_id": "shot_001_keyframe",
                                "workflow_id": "04_keyframe",
                                "workflow_mode": "img2img_style_keyframe",
                                "prompt": "主角进入2008年街头",
                                "input_bindings": {
                                    "input_base_image": {
                                        "from_job": "asset_char_main_front",
                                        "output": "output_final_image",
                                    }
                                },
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (task_dir / "production_manifest.json").write_text(
                json.dumps(
                    {
                        "status": "awaiting_comfyui_image_debug",
                        "composition": {"manual_debug_stage": "image"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (comfy_dir / "manual_debug_state.json").write_text(
                json.dumps(
                    {
                        "items": {
                            "01_base_asset_image:character_base:asset_char_main_front": {
                                "status": "approved",
                                "files": ["comfyui/manual_debug/master.png"],
                                "prompt_version": 2,
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            status = web_app.WorkflowWebHandler._task_comfy_debug_status(task_dir)
            keyframe_group = next(item for item in status["items"] if item["id"] == "group:04_keyframe:img2img_style_keyframe")
            child = keyframe_group["children"][0]
            self.assertEqual(child["reference_image"], "asset_char_main_front")
            self.assertEqual(
                web_app.WorkflowWebHandler._resolve_task_comfy_debug_reference(
                    task_dir,
                    status,
                    child["reference_image"],
                ),
                "comfyui/manual_debug/master.png",
            )

    def test_task_comfy_debug_payload_includes_denoise(self) -> None:
        payload = web_app.WorkflowWebHandler._debug_dimension_payload(
            {
                "width": 480,
                "height": 848,
                "duration": 4,
                "fps": 24,
                "denoise": 0.68,
                "seed": 123,
            }
        )
        self.assertEqual(payload["denoise"], "0.68")
        self.assertEqual(payload["seed"], "123")

    def test_validator_treats_short_video_as_portrait_by_default(self) -> None:
        content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "shot_001_keyframe",
                            "prompt": "2008年街边早餐摊，真人实拍质感",
                        }
                    ]
                },
                "image_prompts": [
                    {
                        "task_type": "image",
                        "prompt": "2008年街边早餐摊，真人实拍质感",
                        "width": 480,
                        "height": 848,
                    }
                ],
            },
            ensure_ascii=False,
        )
        result = validate_production_output(
            {"agent": "06_分镜生图设计师"},
            f"```json\n{content}\n```",
            {"original_requirement": "30秒短视频，真人画风", "duration_seconds": 30},
        )
        self.assertTrue(result["passed"], result["issues"])
        self.assertEqual(result["expected_work_resolution"], "480x848")

    def test_validator_respects_explicit_landscape_short_video(self) -> None:
        content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "shot_001_keyframe",
                            "prompt": "2008年街边早餐摊，真人实拍质感",
                        }
                    ]
                },
                "image_prompts": [
                    {
                        "task_type": "image",
                        "prompt": "2008年街边早餐摊，真人实拍质感",
                        "width": 848,
                        "height": 480,
                    }
                ],
            },
            ensure_ascii=False,
        )
        result = validate_production_output(
            {"agent": "06_分镜生图设计师"},
            f"```json\n{content}\n```",
            {"original_requirement": "16:9横屏短视频，真人画风", "duration_seconds": 30},
        )
        self.assertTrue(result["passed"], result["issues"])
        self.assertEqual(result["expected_work_resolution"], "848x480")

    def test_validator_inherits_route_aspect_from_previous_outputs(self) -> None:
        content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "shot_001_keyframe",
                            "prompt": "小美坐在餐桌前准备吃饭",
                        }
                    ]
                },
                "image_prompts": [
                    {
                        "task_type": "image",
                        "prompt": "小美坐在餐桌前准备吃饭",
                        "width": 480,
                        "height": 848,
                    }
                ],
            },
            ensure_ascii=False,
        )
        previous = [
            {
                "agent": "01_需求拆解专员",
                "content": "```json\n"
                + json.dumps(
                    {
                        "production_type": "drama_story",
                        "target_platform": "抖音",
                        "aspect_ratio": "9:16",
                    },
                    ensure_ascii=False,
                )
                + "\n```",
            }
        ]
        result = validate_production_output(
            {"agent": "06_分镜生图设计师"},
            f"```json\n{content}\n```",
            {"original_requirement": "人必须吃饭", "duration_seconds": 0},
            previous_outputs=previous,
        )
        self.assertTrue(result["passed"], result["issues"])
        self.assertEqual(result["expected_work_resolution"], "480x848")

    def test_validator_original_portrait_requirement_overrides_wrong_route_aspect(self) -> None:
        content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "shot_001_keyframe",
                            "prompt": "Hero in a warm dining room.",
                        }
                    ]
                },
                "image_prompts": [
                    {
                        "task_type": "image",
                        "prompt": "Hero in a warm dining room.",
                        "width": 480,
                        "height": 848,
                    }
                ],
            },
            ensure_ascii=False,
        )
        previous = [
            {
                "agent": "01_route",
                "content": "```json\n"
                + json.dumps(
                    {
                        "production_type": "drama_story",
                        "target_platform": "douyin",
                        "aspect_ratio": "16:9",
                    },
                    ensure_ascii=False,
                )
                + "\n```",
            }
        ]
        result = validate_production_output(
            {"agent": "06_image"},
            f"```json\n{content}\n```",
            {"original_requirement": "10秒，9:16 vertical video", "duration_seconds": 10},
            previous_outputs=previous,
        )
        self.assertTrue(result["passed"], result["issues"])
        self.assertEqual(result["expected_work_resolution"], "480x848")

    def test_normalizes_staff_prompt_dimensions_to_locked_work_size(self) -> None:
        content = json.dumps(
            {
                "production_intents": {
                    "image": [
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "shot_001_keyframe",
                            "prompt": "Original pig worker on subway.",
                        },
                        {
                            "intent": "generate_keyframe",
                            "intent_id": "shot_002_keyframe",
                            "prompt": "Original pig worker in office.",
                        },
                    ]
                },
                "image_prompts": [
                    {
                        "task_type": "image",
                        "prompt": "Original pig worker on subway.",
                        "width": 1440,
                        "height": 848,
                        "resolution": "1440x848",
                    },
                    {
                        "task_type": "image",
                        "prompt": "Original pig worker in office.",
                        "width": 960,
                        "height": 1440,
                        "working_resolution": "960x1440",
                    },
                ],
            },
            ensure_ascii=False,
        )
        normalized = normalize_production_output_content(
            {"agent": "06_image"},
            f"```json\n{content}\n```",
            {"original_requirement": "猪猪侠打工人的一天，竖屏9:16，2分钟", "duration_seconds": 120},
        )
        self.assertTrue(normalized["changed"])
        normalized_content = normalized["content"]
        self.assertIn('"width": 480', normalized_content)
        self.assertIn('"height": 848', normalized_content)
        self.assertIn('"resolution": "480x848"', normalized_content)
        self.assertIn('"working_resolution": "480x848"', normalized_content)

        result = validate_production_output(
            {"agent": "06_image"},
            normalized_content,
            {"original_requirement": "猪猪侠打工人的一天，竖屏9:16，2分钟", "duration_seconds": 120},
        )
        self.assertTrue(result["passed"], result["issues"])

    def test_validator_accepts_three_frame_source_intent_binding(self) -> None:
        content = json.dumps(
            {
                "production_intents": {
                    "video": [
                        {
                            "intent": "generate_three_frame_i2v_clip",
                            "intent_id": "clip_shot_005_wakeup",
                            "source_intent_ids": ["shot_005_three_frame"],
                            "duration_seconds": 4,
                            "fps": 24,
                            "motion_plan": "闭眼到惊醒的首中尾三帧动作",
                        }
                    ]
                },
                "video_prompts": [
                    {
                        "task_type": "video",
                        "video_task_mode": "first_middle_last_frame",
                        "workflow_mode": "first_middle_last_frame",
                        "asset_tag": "clip_shot_005_wakeup",
                        "prompt": "闭眼到惊醒的首中尾三帧动作",
                        "duration": 4,
                        "fps": 24,
                        "width": 480,
                        "height": 848,
                    }
                ],
            },
            ensure_ascii=False,
        )
        previous = [
            {
                "agent": "06_分镜生图设计师",
                "content": json.dumps(
                    {
                        "production_intents": {
                            "image": [
                                {
                                    "intent": "generate_three_frame_shot",
                                    "intent_id": "shot_005_three_frame",
                                    "prompt": "主角醒来",
                                }
                            ]
                        },
                        "image_prompts": [],
                    },
                    ensure_ascii=False,
                ),
            }
        ]
        result = validate_production_output(
            {"agent": "07_视频生成执行员"},
            f"```json\n{content}\n```",
            {"original_requirement": "30秒短视频，真人画风", "duration_seconds": 30},
            previous_outputs=previous,
        )
        self.assertTrue(result["passed"], result["issues"])

    def test_validator_accepts_standalone_json_line_comments(self) -> None:
        content = """
```json
{
  "production_intents": {
    "image": [
      // asset section
      {
        "intent": "generate_keyframe",
        "intent_id": "shot_001_keyframe",
        "prompt": "2008年街边早餐摊，真人实拍质感"
      }
    ]
  },
  "image_prompts": [
    {
      "task_type": "image",
      "prompt": "2008年街边早餐摊，真人实拍质感",
      "width": 480,
      "height": 848
    }
  ]
}
```
"""
        result = validate_production_output(
            {"agent": "06_分镜生图设计师"},
            content,
            {"original_requirement": "30秒短视频，真人画风", "duration_seconds": 30},
        )
        self.assertTrue(result["passed"], result["issues"])

    def test_requirement_guard_accepts_2008_rebirth_paraphrase(self) -> None:
        result = validate_requirement_alignment(
            {
                "core_topic": "上辈子我勤恳打工一辈子，省吃俭用依旧平庸碌碌，眼睁睁错过所有暴富风口，遗憾终身",
                "duration_seconds": 30,
                "original_requirement": "30秒短视频，真人画风",
            },
            "本次任务的核心是：为一段30秒、9:16竖屏、关于打工人穿越回2008年逆袭的短视频，规划图片资产。画面覆盖房价、互联网、商机和时代机会。",
            4,
        )
        self.assertTrue(result["passed"], result["issues"])

    def test_unconfigured_talking_image_slot_is_optional(self) -> None:
        slots = _required_workflow_slots(
            [
                {"workflow_id": "09_talking_image", "mode": "talking_image", "type": "video"},
                {"workflow_id": "04_keyframe", "mode": "keyframe", "type": "image"},
            ]
        )
        self.assertEqual(slots, [{"workflow_id": "04_keyframe", "mode": "keyframe", "material_type": "image", "label": "04_keyframe / keyframe"}])

    def test_optional_talking_image_does_not_require_audio_gate(self) -> None:
        self.assertFalse(
            _payload_has_required_mode(
                {
                    "video_prompts": [
                        {
                            "workflow_id": "09_talking_image",
                            "mode": "talking_image",
                            "optional_when_unconfigured": True,
                        }
                    ]
                },
                "talking_image",
            )
        )

    def test_optional_talking_image_blocker_does_not_block_packaging(self) -> None:
        manifest = {
            "production_nodes": [
                {"job_id": "talking_image", "stage": "visual", "mode": "talking_image", "status": "blocked", "error": "missing wav"},
                {"job_id": "clip_001", "stage": "visual", "mode": "i2v_first_frame", "status": "success"},
            ]
        }
        self.assertEqual(_packaging_dependency_blockers(manifest, tts_enabled=False, material_enabled=True), [])
        talking_node = next(node for node in manifest["production_nodes"] if node["job_id"] == "talking_image")
        self.assertEqual(talking_node["status"], "skipped")

    def test_runninghub_comfy_full_allows_local_ffmpeg_composition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            adapter = LocalFFmpegAdapter(WORKSPACE / "my_workspace")
            result = adapter.run(
                root,
                {"comfyui": root / "missing_comfyui", "video_clips": root / "missing_video"},
                {"tool": "runninghub", "execution_mode": "comfy_full", "ffmpeg_path": str(root / "missing_ffmpeg.exe")},
                {"composition": {"target_file": str(root / "final.mp4")}, "files": {}},
            )
            self.assertEqual(result["status"], "skipped")
            self.assertNotIn("compose tool is not ffmpeg", result["reason"])

    def test_api_ready_visual_jobs_are_image_only(self) -> None:
        jobs = [
            {"job_id": "asset_1", "capability": "image_generate", "workflow_id": "04_keyframe"},
            {"job_id": "clip_1", "capability": "video_generate", "workflow_id": "06_i2v_first_frame"},
            {"job_id": "enhance_1", "capability": "video_enhance", "workflow_id": "11_video_enhance"},
        ]
        self.assertEqual([job["job_id"] for job in _active_visual_jobs_for_mode("api_ready", jobs)], ["asset_1"])
        self.assertEqual([job["job_id"] for job in _active_visual_jobs_for_mode("comfy_full", jobs)], ["asset_1", "clip_1", "enhance_1"])

    def test_api_ready_payload_removes_video_prompts(self) -> None:
        payload = {
            "image_prompts": [{"id": "image_1", "prompt": "frame"}],
            "video_prompts": [{"id": "clip_1", "prompt": "motion"}],
            "video_prompt": "motion",
            "global_context": {"characters": []},
        }
        filtered = _payload_for_material_type(payload, "image")
        self.assertIn("image_prompts", filtered)
        self.assertNotIn("video_prompts", filtered)
        self.assertNotIn("video_prompt", filtered)
        self.assertEqual(filtered["global_context"], {"characters": []})

    def test_api_adapter_skipped_is_failed_production_status(self) -> None:
        self.assertTrue(web_app.WorkflowWebHandler._is_failed_production_status("api_adapter_skipped"))

    def test_unconfigured_multi_identity_keyframe_downgrades_to_identity_keyframe(self) -> None:
        visual_jobs = [
            {
                "job_id": "shot_duo",
                "workflow_id": "04_keyframe",
                "workflow_mode": "multi_identity_keyframe",
                "mode": "multi_identity_keyframe",
                "image_task_mode": "multi_identity_keyframe",
                "control_mode": "multi_identity_reference",
            }
        ]
        payload = {
            "image_prompts": [
                {
                    "job_id": "shot_duo",
                    "workflow_id": "04_keyframe",
                    "workflow_mode": "multi_identity_keyframe",
                    "mode": "multi_identity_keyframe",
                    "image_task_mode": "multi_identity_keyframe",
                    "control_mode": "multi_identity_reference",
                }
            ]
        }
        notes: list[str] = []
        _downgrade_unconfigured_visual_jobs_for_available_slots(
            visual_jobs,
            payload,
            {
                "workflow_library": [
                    {
                        "id": "04_keyframe",
                        "mode_configs": {
                            "identity_keyframe": {
                                "endpoint": "/run/workflow/identity",
                                "node_info_list_json": "[{}]",
                            }
                        },
                    }
                ]
            },
            notes=notes,
        )
        self.assertEqual(visual_jobs[0]["workflow_mode"], "identity_keyframe")
        self.assertEqual(visual_jobs[0]["control_mode"], "identity_reference")
        self.assertEqual(payload["image_prompts"][0]["workflow_mode"], "identity_keyframe")
        self.assertEqual(payload["image_prompts"][0]["control_mode"], "identity_reference")
        self.assertIn("multi_identity_keyframe", notes[0])

    def test_local_ffmpeg_project_relative_output_path_not_nested(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            task_dir = Path(temp_dir) / "task_demo"
            adapter = LocalFFmpegAdapter(WORKSPACE / "my_workspace")
            output = adapter._resolve_output_path("my_workspace/my_task_output/task_demo/final.mp4", task_dir)
            self.assertEqual(output, (WORKSPACE / "my_workspace/my_task_output/task_demo/final.mp4").resolve())

    def test_local_ffmpeg_concat_list_uses_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video_file = root / "clip.mp4"
            video_file.write_bytes(b"fake")
            adapter = LocalFFmpegAdapter(WORKSPACE / "my_workspace")
            adapter._probe_media_duration = lambda _ffmpeg, _path: 4.0  # type: ignore[method-assign]
            command, _ = adapter._build_video_concat_command(
                ffmpeg_path="ffmpeg",
                task_dir=root,
                video_files=[video_file],
                image_files=[],
                audio_file=None,
                bgm_file=None,
                subtitles_file=None,
                subtitle_style="",
                output_width=1080,
                output_height=1920,
                output_fps=24,
                encoding_args=[],
                output_file=root / "final.mp4",
                target_duration_seconds=4,
            )
            self.assertEqual(command[command.index("-i") + 1], str((root / "local_ffmpeg_video_inputs.txt").resolve()))

    def test_video_filter_can_pad_to_target_duration(self) -> None:
        filter_text = LocalFFmpegAdapter._video_filter(None, "", 1080, 1920, 24, pad_end_seconds=8.5)
        self.assertIn("tpad=stop_mode=clone:stop_duration=8.500", filter_text)

    def test_video_concat_command_caps_to_target_duration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            video_file = root / "clip.mp4"
            video_file.write_bytes(b"fake")
            output_file = root / "final.mp4"
            adapter = LocalFFmpegAdapter(WORKSPACE / "my_workspace")
            adapter._probe_media_duration = lambda _ffmpeg, _path: 37.6  # type: ignore[method-assign]
            command, _ = adapter._build_video_concat_command(
                ffmpeg_path="ffmpeg",
                task_dir=root,
                video_files=[video_file],
                image_files=[],
                audio_file=None,
                bgm_file=None,
                subtitles_file=None,
                subtitle_style="",
                output_width=1080,
                output_height=1920,
                output_fps=24,
                encoding_args=["-c:v", "libx264"],
                output_file=output_file,
                target_duration_seconds=30.0,
            )
            self.assertIn("-t", command)
            self.assertEqual(command[command.index("-t") + 1], "30.000")
            self.assertNotIn("tpad=stop_mode=clone", " ".join(command))

    def test_srt_quality_rejects_overloaded_subtitle_entry(self) -> None:
        srt = (
            "1\n00:00:00,000 --> 00:00:02,300\n"
            "买房囤铺，入局新兴行业，短短几年从一无所有逆袭成亿万富豪。这辈子的翻盘，现在开始。\n"
        )
        result = _quality_check_srt(srt)
        self.assertFalse(result["usable"])
        self.assertEqual(result["status"], "overloaded")

    def test_voice_text_fallback_srt_splits_chinese_punctuation(self) -> None:
        srt = _srt_from_voice_text("上辈子，我勤恳打工一辈子。省吃俭用，依旧平庸碌碌。短短几年逆袭成亿万富豪。")
        self.assertGreaterEqual(srt.count("-->"), 3)
        self.assertIn("上辈子", srt)
        self.assertIn("短短几年", srt)

    def test_voiceover_alignment_skips_excessive_tempo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "voiceover.wav"
            audio.write_bytes(b"fake")
            subtitles = root / "subtitles.srt"
            subtitles.write_text(
                "1\n00:00:00,000 --> 00:00:01,000\n一句话\n\n"
                "2\n00:00:01,000 --> 00:00:02,000\n很长的收尾旁白\n",
                encoding="utf-8",
            )
            original_detect = LocalFFmpegAdapter._detect_silence_midpoints
            original_probe = LocalFFmpegAdapter._probe_media_duration
            try:
                LocalFFmpegAdapter._detect_silence_midpoints = staticmethod(lambda _ffmpeg, _audio: [1.0])  # type: ignore[method-assign]
                LocalFFmpegAdapter._probe_media_duration = staticmethod(lambda _ffmpeg, _path: 5.0)  # type: ignore[method-assign]
                aligned, result = LocalFFmpegAdapter._align_voiceover_to_subtitles(
                    ffmpeg_path="ffmpeg",
                    audio_file=audio,
                    subtitles_file=subtitles,
                    task_dir=root,
                )
            finally:
                LocalFFmpegAdapter._detect_silence_midpoints = original_detect  # type: ignore[method-assign]
                LocalFFmpegAdapter._probe_media_duration = original_probe  # type: ignore[method-assign]
            self.assertEqual(aligned, audio)
            self.assertEqual(result["status"], "skipped")
            self.assertIn("tempo above", result["reason"])

    def test_media_range_header_parsing(self) -> None:
        self.assertEqual(web_app.WorkflowWebHandler._parse_range_header("bytes=0-99", 1000), (0, 99))
        self.assertEqual(web_app.WorkflowWebHandler._parse_range_header("bytes=900-", 1000), (900, 999))
        self.assertEqual(web_app.WorkflowWebHandler._parse_range_header("bytes=-100", 1000), (900, 999))
        self.assertIsNone(web_app.WorkflowWebHandler._parse_range_header("bytes=1000-", 1000))
        self.assertIsNone(web_app.WorkflowWebHandler._parse_range_header("items=0-99", 1000))

    def test_asset_source_key_normalizes_paths(self) -> None:
        self.assertEqual(
            web_app.WorkflowWebHandler._asset_source_key(r"task_a\\", r"\\comfyui\\job\\out.png"),
            "task_a::comfyui/job/out.png",
        )

    def test_task_assets_include_favorite_state(self) -> None:
        original_index = web_app.ASSET_LIBRARY_INDEX
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_dir = root / "task_a"
            media = task_dir / "comfyui" / "job" / "out.png"
            media.parent.mkdir(parents=True)
            media.write_bytes(b"fake")
            library_index = root / "library.json"
            library_index.write_text(
                json.dumps(
                    [
                        {
                            "id": "asset_1",
                            "file": "07_keyframe/asset_1_out.png",
                            "source_task": "task_a",
                            "source_file": r"comfyui\\job\\out.png",
                            "tags": ["image", "keyframe"],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            try:
                web_app.ASSET_LIBRARY_INDEX = library_index
                assets = web_app.WorkflowWebHandler._task_assets(task_dir, ["comfyui/job/out.png"], task_name="task_a")
            finally:
                web_app.ASSET_LIBRARY_INDEX = original_index
            item = assets["images"][0]
            self.assertTrue(item["favorited"])
            self.assertEqual(item["library_asset_id"], "asset_1")
            self.assertEqual(item["source_file"], "comfyui/job/out.png")

    def test_task_assets_include_sanitized_request_info(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_dir = root / "task_a"
            media = task_dir / "generated_images" / "job_shot_001" / "out.png"
            media.parent.mkdir(parents=True)
            media.write_bytes(b"fake")
            (task_dir / "production_manifest.json").write_text(
                json.dumps(
                    {
                        "production_nodes": [
                            {
                                "job_id": "shot_001",
                                "outputs": ["generated_images/job_shot_001/out.png"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (task_dir / "production_plan.json").write_text(
                json.dumps(
                    {
                        "compiled_payload": {
                            "image_prompts": [
                                {
                                    "job_id": "shot_001",
                                    "prompt": "年轻女性坐在咖啡店，参考已有关键帧素材 'a509cdff82fe430c87d1246907e2b80c' 的风格。",
                                }
                            ]
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            assets = web_app.WorkflowWebHandler._task_assets(
                task_dir,
                ["generated_images/job_shot_001/out.png"],
                task_name="task_a",
            )
            item = assets["images"][0]
            self.assertEqual(item["producer_job_id"], "shot_001")
            self.assertIn("年轻女性坐在咖啡店", item["request_info"])
            self.assertNotIn("a509cdff82fe430c87d1246907e2b80c", item["request_info"])
            self.assertNotIn("nodeId", item["request_info"])

    def test_compiler_strips_asset_ids_and_paths_from_generation_prompts(self) -> None:
        leaked_asset_id = "a509cdff82fe430c87d1246907e2b80c"
        plan = compile_production_plan(
            task_id="sanitize_asset_id_prompt",
            route_content=json.dumps({"production_type": "drama_story"}, ensure_ascii=False),
            image_content=json.dumps(
                {
                    "production_intents": {
                        "image": [
                            {
                                "intent": "generate_keyframe",
                                "intent_id": "shot_001",
                                "prompt": (
                                    f"年轻女性主角半身母版图，参考已有关键帧素材 '{leaked_asset_id}' 的风格。"
                                    "文件 my_workspace/my_asset_library/07_keyframe/a509cdff82fe430c87d1246907e2b80c_comfyui_result_02.png。"
                                ),
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            video_content=json.dumps(
                {
                    "production_intents": {
                        "video": [
                            {
                                "intent": "generate_broll_clip",
                                "intent_id": "clip_001",
                                "prompt": f"咖啡店空镜，source_asset_id: {leaked_asset_id}",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
        )
        payload = plan["compiled_payload"]
        image_prompt = payload["image_prompts"][0]["prompt"]
        video_prompt = payload["video_prompts"][0]["prompt"]
        self.assertNotIn(leaked_asset_id, image_prompt)
        self.assertNotIn("my_workspace/my_asset_library", image_prompt)
        self.assertNotIn(leaked_asset_id, video_prompt)
        self.assertIn("参考关联素材的视觉风格", image_prompt)

    def test_adapter_replaces_typed_placeholders(self) -> None:
        adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
        config = {
            "node_info_list_json": (
                '[{"nodeId":"1","fieldName":"image","fieldValue":"{{input_identity_image}}"},'
                '{"nodeId":"2","fieldName":"video","fieldValue":"{{input_source_video}}"}]'
            )
        }
        payload = {"input_identity_image": "identity.png", "input_source_video": "source.mp4"}
        built = adapter._build_runninghub_payload(payload, config)
        values = [item["fieldValue"] for item in built["nodeInfoList"]]
        self.assertEqual(values, ["identity.png", "source.mp4"])

    def test_adapter_accepts_bare_nodeinfo_placeholders(self) -> None:
        adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
        config = {
            "node_info_list_json": (
                '[{"nodeId":"88","fieldName":"width","fieldValue": {{width}}},'
                '{"nodeId":"88","fieldName":"height","fieldValue": {{height}}}]'
            )
        }
        built = adapter._build_runninghub_payload({"width": 480, "height": 848}, config)
        values = [(item["fieldName"], item["fieldValue"]) for item in built["nodeInfoList"]]
        self.assertEqual(values, [("width", 480), ("height", 848)])

    def test_runninghub_uploads_semantic_input_base_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "base.png"
            Image.new("RGB", (8, 8), "red").save(image_path)
            adapter = CloudComfyUIAdapter("https://www.runninghub.cn/openapi/v2", "key", "/run/workflow/test")
            adapter._upload_runninghub_media = lambda path: f"uploaded/{Path(path).name}"  # type: ignore[method-assign]
            config = {
                "node_info_list_json": '[{"nodeId":"2","fieldName":"image","fieldValue":"{{input_base_image}}"}]'
            }
            built = adapter._build_runninghub_payload({"input_base_image": str(image_path)}, config)
            self.assertEqual(built["nodeInfoList"][0]["fieldValue"], "uploaded/base.png")

    def test_material_image_job_does_not_inherit_video_prompt_fields(self) -> None:
        adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
        payload = adapter._payload_for_material_job(
            {
                "video_prompts": [{"prompts": {"clip": {"positive": "video prompt"}}}],
                "video_prompt": "video prompt",
                "video_task_mode": "i2v_first_frame",
            },
            {
                "type": "image",
                "job_id": "shot_001",
                "mode": "img2img_style_keyframe",
                "prompt": "让图中人物坐在操场上吃饭",
            },
            1,
        )
        self.assertEqual(payload["prompt"], "让图中人物坐在操场上吃饭")
        self.assertNotIn("video_prompts", payload)
        self.assertNotIn("video_prompt", payload)
        prepared = adapter._prepare_runninghub_payload(payload)
        self.assertEqual(prepared["prompt"], "让图中人物坐在操场上吃饭")

    def test_material_job_requires_durable_downloaded_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
            adapter._run_generic = lambda payload, config, output_dir: {  # type: ignore[method-assign]
                "status": "submitted",
                "downloaded_files": [],
            }
            with self.assertRaisesRegex(ValueError, "durable local output files"):
                adapter._run_job_with_retries(
                    "generic",
                    {"prompt": "test"},
                    {},
                    Path(temp_dir),
                    "job_missing_download",
                )

    def test_material_gate_requires_each_job_downloaded_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = root / "cloud_comfyui_manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "status": "success",
                        "job_count": 1,
                        "success_count": 1,
                        "failed_count": 0,
                        "downloaded_files": [],
                        "jobs": [
                            {
                                "job_id": "image_001",
                                "status": "success",
                                "downloaded_files": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            production_manifest = {
                "composition": {
                    "adapter_status": "success",
                    "downloaded_files": ["missing.png"],
                    "adapter_manifest": str(manifest_path),
                }
            }

            self.assertFalse(WorkflowEngine._material_gate_passed(production_manifest))

    def test_keyframe_identity_modes_do_not_fall_back_to_generic_slot(self) -> None:
        preset = CloudComfyUIAdapter._workflow_library_preset_for_job(
            {
                "workflow_id": "04_keyframe",
                "mode": "identity_scene_keyframe",
                "type": "image",
            },
            {
                "workflow_library": [
                    {
                        "id": "04_keyframe",
                        "endpoint": "/run/workflow/generic-keyframe",
                        "node_info_list_json": '[{"nodeId":"63","fieldName":"text","fieldValue":"{{prompt}}"}]',
                    }
                ]
            },
        )

        self.assertIsNotNone(preset)
        self.assertEqual(preset["_matched_mode"], "identity_scene_keyframe")
        self.assertEqual(preset["endpoint"], "")
        self.assertEqual(preset["node_info_list_json"], "[]")

    def test_reference_image_job_sends_single_image_edit_prompt(self) -> None:
        adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
        source_prompt = (
            "\u732a\u732a\u6253\u5de5\u4eba7\u79cd\u57fa\u7840\u8868\u60c5\u72b6\u6001\u56fe\uff0c"
            "\u534a\u8eab\u6784\u56fe\uff0c\u7eaf\u8272\u80cc\u666f\uff0c"
            "\u5206\u522b\u4e3a\uff1a\u5f00\u5fc3\u3001\u56f0\u3001\u5d29\u6e83\u3001\u5f97\u610f\u3001\u60ca\u8bb6\u3001\u65e0\u5948\u3001\u75b2\u60eb\u3002"
            "\u4fdd\u6301\u540c\u4e00\u8138\u578b\u3001\u670d\u88c5\u548c\u4f53\u578b\u3002"
            "\u53c2\u8003\u4e0a\u4e00\u5f20\u89d2\u8272\u8bbe\u5b9a\u56fe\uff0c\u5fc5\u987b\u4fdd\u6301\u540c\u4e00\u4e2a\u4eba\u8138\u578b\u3001"
            "\u5e74\u9f84\u611f\u3001\u4e94\u5b98\u6bd4\u4f8b\u3001\u53d1\u578b\u3001\u80a4\u8272\u3001\u8eab\u6750\u6bd4\u4f8b\u548c\u670d\u88c5\u4e00\u81f4\uff1b"
            "\u53ea\u6539\u53d8\u8868\u60c5\u548c\u8f7b\u5fae\u52a8\u4f5c\uff0c\u4e0d\u6362\u8138\uff0c\u4e0d\u5e74\u8f7b\u5316\uff0c\u4e0d\u78e8\u76ae\uff0c\u4e0d\u6362\u8863\u670d\u3002"
        )
        payload = adapter._payload_for_material_job(
            {},
            {
                "type": "image",
                "job_id": "piggy_worker_expression_happy",
                "mode": "img2img_style_keyframe",
                "prompt": source_prompt,
                "reference_image": "character.png",
            },
            1,
        )
        self.assertNotIn("7\u79cd", payload["prompt"])
        self.assertNotIn("\u5206\u522b\u4e3a", payload["prompt"])
        self.assertIn("\u53ea\u8f93\u51fa\u4e00\u5f20\u5b8c\u6574\u5355\u56fe", payload["prompt"])

        built = adapter._build_runninghub_payload(
            payload,
            {"node_info_list_json": '[{"nodeId":"34","fieldName":"value","fieldValue":"{{prompt}}"}]'},
        )
        prompt_value = built["nodeInfoList"][0]["fieldValue"]
        self.assertEqual(prompt_value, payload["prompt"])
        self.assertNotIn("\u5206\u522b\u4e3a", prompt_value)
        self.assertNotIn("7\u79cd", prompt_value)

    def test_runninghub_prompt_nodes_strip_asset_artifacts(self) -> None:
        leaked_asset_id = "a509cdff82fe430c87d1246907e2b80c"
        adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
        built = adapter._build_runninghub_payload(
            {
                "prompt": (
                    f"年轻女性坐在咖啡店，参考已有关键帧素材 '{leaked_asset_id}' 的风格，"
                    "my_workspace/my_asset_library/07_keyframe/a509cdff82fe430c87d1246907e2b80c.png"
                )
            },
            {"node_info_list_json": '[{"nodeId":"63","fieldName":"text","fieldValue":"{{prompt}}"}]'},
        )
        prompt_value = built["nodeInfoList"][0]["fieldValue"]
        self.assertIn("年轻女性坐在咖啡店", prompt_value)
        self.assertNotIn(leaked_asset_id, prompt_value)
        self.assertNotIn("my_workspace/my_asset_library", prompt_value)
        self.assertNotIn("nodeId", prompt_value)

    def test_live_action_retro_prompts_get_quality_guardrails(self) -> None:
        plan = compile_production_plan(
            task_id="live_action_quality",
            route_content=json.dumps(
                {
                    "production_type": "drama_story",
                    "aspect_ratio": "9:16",
                    "style_id": "style_vintage_2008_live_action",
                },
                ensure_ascii=False,
            ),
            image_content=json.dumps(
                {
                    "production_intents": {
                        "image": [
                            {
                                "intent": "generate_keyframe",
                                "intent_id": "shot_001_keyframe",
                                "prompt": "2008年市井街头，真人电影级复古画质，主角站在房产中介门口。",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            video_content=json.dumps(
                {
                    "production_intents": {
                        "video": [
                            {
                                "intent": "generate_broll_clip",
                                "intent_id": "clip_001_street",
                                "prompt": "2008年街边小卖部和公交站牌，复古市井烟火。",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            video_config={"aspect_ratio": "9:16"},
        )
        image_item = plan["compiled_payload"]["image_prompts"][0]
        video_item = plan["compiled_payload"]["video_prompts"][0]
        self.assertIn("真人纪实质感", image_item["prompt"])
        self.assertIn("模糊不可读背景文字", image_item["prompt"])
        self.assertIn("gibberish text", image_item["negative_prompt"])
        self.assertIn("真人纪实质感", video_item["prompt"])
        self.assertIn("studio fashion shoot", video_item["negative_prompt"])

    def test_plain_live_action_prompts_do_not_get_retro_guardrails(self) -> None:
        plan = compile_production_plan(
            task_id="plain_live_action",
            route_content=json.dumps(
                {
                    "production_type": "commercial_story",
                    "aspect_ratio": "9:16",
                    "style_id": "modern_live_action",
                },
                ensure_ascii=False,
            ),
            image_content=json.dumps(
                {
                    "production_intents": {
                        "image": [
                            {
                                "intent": "generate_keyframe",
                                "intent_id": "shot_001_keyframe",
                                "prompt": "现代真人商业短视频，主角在城市咖啡店里看手机。",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            video_content=json.dumps({"production_intents": {"video": []}}, ensure_ascii=False),
            video_config={"aspect_ratio": "9:16"},
        )
        image_item = plan["compiled_payload"]["image_prompts"][0]
        self.assertNotIn("2008年前后中国城市生活细节", image_item["prompt"])
        self.assertNotIn("旧式招牌", image_item["prompt"])
        self.assertNotIn("真人纪实质感", image_item["prompt"])

    def test_visual_style_consistency_policy_is_generic(self) -> None:
        plan = compile_production_plan(
            task_id="generic_style_consistency",
            route_content=json.dumps(
                {
                    "production_type": "product_promo",
                    "aspect_ratio": "9:16",
                    "visual_style": "产品商业渲染，干净棚拍质感",
                },
                ensure_ascii=False,
            ),
            image_content=json.dumps(
                {
                    "production_intents": {
                        "image": [
                            {
                                "intent": "generate_base_asset",
                                "intent_id": "asset_product",
                                "asset_role": "product",
                                "prompt": "一只透明玻璃水杯放在白色台面上。",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            video_content=json.dumps(
                {
                    "production_intents": {
                        "video": [
                            {
                                "intent": "generate_broll_clip",
                                "intent_id": "clip_product_spin",
                                "prompt": "镜头缓慢环绕玻璃水杯，展示杯身高光。",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            video_config={"aspect_ratio": "9:16"},
        )

        image_item = plan["compiled_payload"]["image_prompts"][0]
        video_item = plan["compiled_payload"]["video_prompts"][0]
        style_lock = plan["parameter_policy"]["locks"]["style"]
        self.assertIn("全片视觉一致性约束", image_item["prompt"])
        self.assertIn("全片视觉一致性约束", video_item["prompt"])
        self.assertIn("mixed visual styles", image_item["negative_prompt"])
        self.assertIn("mixed visual styles", video_item["negative_prompt"])
        self.assertEqual(image_item["visual_style_blueprint"]["style_family"], "product_render")
        self.assertEqual(video_item["visual_style_blueprint"]["style_family"], "product_render")
        self.assertTrue(style_lock["enabled"])
        self.assertIn("全片视觉一致性约束", style_lock["positive_prompt"])

    def test_generated_scene_assets_bind_to_character_keyframes(self) -> None:
        plan = compile_production_plan(
            task_id="scene_bound_keyframes",
            route_content=json.dumps(
                {"production_type": "custom", "aspect_ratio": "9:16"},
                ensure_ascii=False,
            ),
            image_content=json.dumps(
                {
                    "production_intents": {
                        "image": [
                            {
                                "intent": "generate_base_asset",
                                "intent_id": "asset_character_stand",
                                "asset_role": "character",
                                "character_id": "character_jumper",
                                "prompt": "same character standing, gray shirt and navy pants",
                            },
                            {
                                "intent": "generate_base_asset",
                                "intent_id": "asset_scene_platform_low",
                                "asset_role": "scene",
                                "character_id": "character_jumper",
                                "scene_id": "scene_platform_base",
                                "prompt": "30cm low platform in a plain indoor room",
                            },
                            {
                                "intent": "generate_base_asset",
                                "intent_id": "asset_scene_platform_mid",
                                "asset_role": "scene",
                                "character_id": "character_jumper",
                                "scene_id": "scene_platform_medium",
                                "prompt": "60cm medium platform in the same plain indoor room",
                            },
                            {
                                "intent": "generate_three_frame_shot",
                                "intent_id": "shot_007_three_frame",
                                "character_id": "character_jumper",
                                "scene_id": "scene_platform_base",
                                "frame_set": [
                                    {"role": "start", "prompt": "character stands on the 30cm platform"},
                                    {"role": "middle", "prompt": "character jumps from the 30cm platform"},
                                    {"role": "end", "prompt": "character lands from the 30cm platform"},
                                ],
                            },
                            {
                                "intent": "generate_three_frame_shot",
                                "intent_id": "shot_009_three_frame",
                                "character_id": "character_jumper",
                                "scene_id": "scene_platform_medium",
                                "frame_set": [
                                    {"role": "start", "prompt": "character stands on the 60cm platform"},
                                    {"role": "middle", "prompt": "character jumps from the 60cm platform"},
                                    {"role": "end", "prompt": "character lands from the 60cm platform"},
                                ],
                            },
                            {
                                "intent": "generate_keyframe",
                                "intent_id": "keyframe_info_graph",
                                "character_id": "character_jumper",
                                "prompt": "infographic labels 30cm, 60cm and 1m with colored force bars",
                            },
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            video_content=json.dumps({"production_intents": {"video": []}}, ensure_ascii=False),
            video_config={"aspect_ratio": "9:16"},
        )

        image_items = {
            item["job_id"]: item
            for item in plan["compiled_payload"]["image_prompts"]
            if isinstance(item, dict) and item.get("job_id")
        }
        low_start = image_items["shot_007_three_frame_start_frame"]
        mid_start = image_items["shot_009_three_frame_start_frame"]

        self.assertEqual(low_start["workflow_mode"], "identity_scene_keyframe")
        self.assertEqual(low_start["control_mode"], "identity_scene_reference")
        self.assertEqual(low_start["input_bindings"]["input_scene_image"]["from_job"], "asset_scene_platform_low")
        self.assertIn("asset_scene_platform_low", low_start["depends_on"])

        self.assertEqual(mid_start["workflow_mode"], "identity_scene_keyframe")
        self.assertEqual(mid_start["input_bindings"]["input_scene_image"]["from_job"], "asset_scene_platform_mid")
        self.assertIn("asset_scene_platform_mid", mid_start["depends_on"])

        info_graph = image_items["keyframe_info_graph"]
        self.assertEqual(info_graph["workflow_mode"], "identity_keyframe")
        self.assertNotIn("input_scene_image", info_graph.get("input_bindings") or {})

    def test_voice_requirement_enables_voxcpm2_fallback(self) -> None:
        config = {"voice_config": {"mode": "off"}}
        updated = web_app.WorkflowWebHandler._ensure_voice_config_for_requirement(
            config,
            "30秒真人短视频，必须有中文旁白配音和字幕。",
        )
        self.assertEqual(updated["voice_config"]["mode"], "voxcpm2")
        self.assertEqual(updated["voice_config"]["provider"], "voxcpm2")

    def test_voxcpm2_timeout_falls_back_to_windows_sapi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            adapter = LocalTTSAdapter(workspace_root=WORKSPACE / "my_workspace")

            def fake_timeout(command: str, timeout: int):
                return None, True, "partial stdout", "partial stderr"

            def fake_sapi(voice_text: str, voice_config: dict, fallback_output_dir: Path):
                output_file = fallback_output_dir / "voiceover.wav"
                output_file.write_bytes(b"fallback wav")
                return {
                    "status": "success",
                    "provider": "windows_sapi",
                    "mode": "windows_sapi",
                    "downloaded_files": [str(output_file)],
                    "output_file": str(output_file),
                }

            adapter._run_shell_command = fake_timeout  # type: ignore[method-assign]
            adapter._run_windows_sapi = fake_sapi  # type: ignore[method-assign]

            result = adapter.run(
                "猪猪侠今天也要上班。",
                {"mode": "voxcpm2", "provider": "voxcpm2", "timeout_seconds": 30},
                output_dir,
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["provider"], "windows_sapi")
            self.assertEqual(result["fallback_from_provider"], "voxcpm2")
            self.assertIn("timed out", result["fallback_reason"])
            manifest = json.loads((output_dir / "local_tts_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["fallback_from_provider"], "voxcpm2")

    def test_long_voxcpm2_cpu_voiceover_uses_preemptive_sapi_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            adapter = LocalTTSAdapter(workspace_root=WORKSPACE / "my_workspace")
            shell_called = False

            def fail_if_shell_runs(command: str, timeout: int):
                nonlocal shell_called
                shell_called = True
                return None, True, "", ""

            def fake_sapi(voice_text: str, voice_config: dict, fallback_output_dir: Path):
                output_file = fallback_output_dir / "voiceover.wav"
                output_file.write_bytes(b"fallback wav")
                return {
                    "status": "success",
                    "provider": "windows_sapi",
                    "mode": "windows_sapi",
                    "downloaded_files": [str(output_file)],
                    "output_file": str(output_file),
                }

            adapter._run_shell_command = fail_if_shell_runs  # type: ignore[method-assign]
            adapter._run_windows_sapi = fake_sapi  # type: ignore[method-assign]

            result = adapter.run(
                "猪猪侠今天也要上班。" * 40,
                {
                    "mode": "voxcpm2",
                    "provider": "voxcpm2",
                    "preemptive_fallback_min_seconds": 1,
                    "target_duration_seconds": 120,
                },
                output_dir,
            )

            self.assertFalse(shell_called)
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["fallback_from_provider"], "voxcpm2")
            self.assertIn("skipped", result["fallback_reason"])

    def test_recommended_sapi_rate_overrides_default_zero_for_dense_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            adapter = LocalTTSAdapter(workspace_root=WORKSPACE / "my_workspace")
            captured_rate = None

            def fake_sapi(voice_text: str, voice_config: dict, fallback_output_dir: Path):
                nonlocal captured_rate
                captured_rate = voice_config.get("sapi_rate")
                output_file = fallback_output_dir / "voiceover.wav"
                output_file.write_bytes(b"fallback wav")
                return {
                    "status": "success",
                    "provider": "windows_sapi",
                    "mode": "windows_sapi",
                    "downloaded_files": [str(output_file)],
                    "output_file": str(output_file),
                    "rate": captured_rate,
                }

            adapter._run_windows_sapi = fake_sapi  # type: ignore[method-assign]
            result = adapter._fallback_after_voxcpm2_failure(
                "猪猪侠今天也要上班。" * 45,
                {
                    "mode": "voxcpm2",
                    "provider": "voxcpm2",
                    "sapi_rate": 0,
                    "target_duration_seconds": 120,
                },
                output_dir,
                {"status": "failed", "error": "timeout"},
            )

            self.assertEqual(result["status"], "success")
            self.assertEqual(captured_rate, 3)
            self.assertEqual(result["rate"], 3)

    def test_wav_duration_uses_actual_payload_when_header_size_is_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "placeholder_size.wav"
            sample_rate = 24000
            channels = 1
            bits_per_sample = 16
            bytes_per_sample = bits_per_sample // 8
            frame_count = int(sample_rate * 1.5)
            payload = b"\x00" * frame_count * channels * bytes_per_sample
            header = (
                b"RIFF"
                + (0x7FFFFFBF).to_bytes(4, "little")
                + b"WAVE"
                + b"fmt "
                + (16).to_bytes(4, "little")
                + (1).to_bytes(2, "little")
                + channels.to_bytes(2, "little")
                + sample_rate.to_bytes(4, "little")
                + (sample_rate * channels * bytes_per_sample).to_bytes(4, "little")
                + (channels * bytes_per_sample).to_bytes(2, "little")
                + bits_per_sample.to_bytes(2, "little")
                + b"data"
                + (0x7FFFFF9B).to_bytes(4, "little")
            )
            wav_path.write_bytes(header + payload)

            self.assertEqual(LocalTTSAdapter._wav_duration(wav_path), 1.5)

    def test_video_concat_uses_image_tail_before_padding_last_frame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            video = task_dir / "clip.mp4"
            image = task_dir / "keyframe.png"
            video.write_bytes(b"video")
            image.write_bytes(b"image")
            adapter = LocalFFmpegAdapter(workspace_root=WORKSPACE / "my_workspace")

            def fake_probe(ffmpeg_path: str, path: Path) -> float:
                if path == video:
                    return 20.0
                if path == task_dir / "local_ffmpeg_slideshow_tail.mp4":
                    return 10.0
                return 0.0

            def fake_tail(**kwargs):
                tail = task_dir / "local_ffmpeg_slideshow_tail.mp4"
                tail.write_bytes(b"tail")
                return tail, 10.0

            adapter._probe_media_duration = fake_probe  # type: ignore[method-assign]
            adapter._render_image_tail_video = fake_tail  # type: ignore[method-assign]

            command, input_files = adapter._build_video_concat_command(
                ffmpeg_path="ffmpeg",
                task_dir=task_dir,
                video_files=[video],
                image_files=[image],
                audio_file=None,
                bgm_file=None,
                subtitles_file=None,
                subtitle_style="",
                output_width=1080,
                output_height=1920,
                output_fps=24,
                encoding_args=["-c:v", "libx264"],
                output_file=task_dir / "final.mp4",
                target_duration_seconds=30.0,
            )

            concat_text = (task_dir / "local_ffmpeg_video_inputs.txt").read_text(encoding="utf-8")
            self.assertIn("clip.mp4", concat_text)
            self.assertIn("local_ffmpeg_slideshow_tail.mp4", concat_text)
            self.assertIn(image, input_files)
            self.assertFalse(any("tpad=stop_mode=clone" in part for part in command))

    def test_video_concat_pads_when_rendered_image_tail_is_shorter_than_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            video = task_dir / "clip.mp4"
            image = task_dir / "keyframe.png"
            tail = task_dir / "local_ffmpeg_slideshow_tail.mp4"
            video.write_bytes(b"video")
            image.write_bytes(b"image")
            adapter = LocalFFmpegAdapter(workspace_root=WORKSPACE / "my_workspace")

            def fake_probe(ffmpeg_path: str, path: Path) -> float:
                if path == video:
                    return 20.0
                if path == tail:
                    return 7.5
                return 0.0

            def fake_tail(**kwargs):
                tail.write_bytes(b"tail")
                return tail, fake_probe("", tail)

            adapter._probe_media_duration = fake_probe  # type: ignore[method-assign]
            adapter._render_image_tail_video = fake_tail  # type: ignore[method-assign]

            command, _input_files = adapter._build_video_concat_command(
                ffmpeg_path="ffmpeg",
                task_dir=task_dir,
                video_files=[video],
                image_files=[image],
                audio_file=None,
                bgm_file=None,
                subtitles_file=None,
                subtitle_style="",
                output_width=1080,
                output_height=1920,
                output_fps=24,
                encoding_args=["-c:v", "libx264"],
                output_file=task_dir / "final.mp4",
                target_duration_seconds=30.0,
            )

            video_filter = command[command.index("-vf") + 1]
            self.assertIn("tpad=stop_mode=clone:stop_duration=2.500", video_filter)

    def test_video_concat_target_duration_does_not_stop_at_short_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            video = task_dir / "clip.mp4"
            audio = task_dir / "voiceover.wav"
            video.write_bytes(b"video")
            audio.write_bytes(b"audio")
            adapter = LocalFFmpegAdapter(workspace_root=WORKSPACE / "my_workspace")
            adapter._probe_media_duration = lambda ffmpeg_path, path: 120.0  # type: ignore[method-assign]

            command, input_files = adapter._build_video_concat_command(
                ffmpeg_path="ffmpeg",
                task_dir=task_dir,
                video_files=[video],
                image_files=[],
                audio_file=audio,
                bgm_file=None,
                subtitles_file=None,
                subtitle_style="",
                output_width=1080,
                output_height=1920,
                output_fps=24,
                encoding_args=["-c:v", "libx264"],
                output_file=task_dir / "final.mp4",
                target_duration_seconds=120.0,
            )

            self.assertIn(audio, input_files)
            self.assertIn("-t", command)
            self.assertIn("120.000", command)
            self.assertNotIn("-shortest", command)

    def test_adapter_repairs_legacy_broll_ltx_node_info(self) -> None:
        repaired = CloudComfyUIAdapter._repair_known_runninghub_node_info(
            json.dumps(
                [
                    {"nodeId": "2483", "fieldName": "text", "fieldValue": "{{prompt}}"},
                    {"nodeId": "2612", "fieldName": "text", "fieldValue": "{{negative_prompt}}"},
                    {"nodeId": "3059", "fieldName": "width", "fieldValue": "{{width}}"},
                ]
            ),
            endpoint="/run/workflow/2071227330307125249",
            workflow_id="10_broll_transition_video",
            workflow_mode="broll_scene_video",
        )
        rows = json.loads(repaired)
        self.assertNotIn("2483", {row["nodeId"] for row in rows})
        self.assertIn({"nodeId": "73", "fieldName": "text", "fieldValue": "{{prompt}}"}, rows)
        self.assertIn({"nodeId": "43", "fieldName": "value", "fieldValue": "{{width}}"}, rows)

    def test_adapter_repairs_legacy_first_frame_i2v_ltx_node_info(self) -> None:
        repaired = CloudComfyUIAdapter._repair_known_runninghub_node_info(
            json.dumps(
                [
                    {"nodeId": "4923", "fieldName": "prompt", "fieldValue": "{{prompt}}"},
                    {"nodeId": "3059", "fieldName": "width", "fieldValue": "{{width}}"},
                    {"nodeId": "3059", "fieldName": "height", "fieldValue": "{{height}}"},
                    {"nodeId": "4978", "fieldName": "value", "fieldValue": "{{fps}}"},
                ]
            ),
            workflow_id="06_i2v_first_frame",
            workflow_mode="i2v_first_frame",
        )
        rows = json.loads(repaired)
        row_keys = {(row["nodeId"], row["fieldName"], row["fieldValue"]) for row in rows}
        self.assertNotIn("4923", {row["nodeId"] for row in rows})
        self.assertIn(("177", "text", "{{prompt}}"), row_keys)
        self.assertIn(("182", "text", "{{negative_prompt}}"), row_keys)
        self.assertIn(("193", "image", "{{reference_image}}"), row_keys)
        self.assertIn(("186", "value", "{{long_side}}"), row_keys)
        self.assertIn(("192", "value", "{{duration}}"), row_keys)
        self.assertIn(("154", "value", "{{fps}}"), row_keys)
        self.assertIn(("155", "noise_seed", "{{seed}}"), row_keys)
        self.assertIn(("158", "value", False), row_keys)
        self.assertIn(("216", "value", False), row_keys)
        self.assertNotIn("178", {row["nodeId"] for row in rows})
        self.assertIn(("232", "filename_prefix", "video/ltx2.3-i2v-first-frame"), row_keys)

    def test_adapter_repairs_current_first_frame_i2v_mapping_missing_llm_bypass(self) -> None:
        repaired = CloudComfyUIAdapter._repair_known_runninghub_node_info(
            json.dumps(
                [
                    {"nodeId": "177", "fieldName": "text", "fieldValue": "{{prompt}}"},
                    {"nodeId": "182", "fieldName": "text", "fieldValue": "{{negative_prompt}}"},
                    {"nodeId": "193", "fieldName": "image", "fieldValue": "{{reference_image}}"},
                    {"nodeId": "186", "fieldName": "value", "fieldValue": "{{long_side}}"},
                    {"nodeId": "192", "fieldName": "value", "fieldValue": "{{duration}}"},
                    {"nodeId": "154", "fieldName": "value", "fieldValue": "{{fps}}"},
                ]
            ),
            endpoint="/run/workflow/2071735603636563970",
            workflow_id="06_i2v_first_frame",
            workflow_mode="i2v_first_frame",
        )
        rows = json.loads(repaired)
        row_keys = {(row["nodeId"], row["fieldName"], row["fieldValue"]) for row in rows}
        self.assertIn(("216", "value", False), row_keys)
        self.assertNotIn("178", {row["nodeId"] for row in rows})
        self.assertIn(("195", "bypass", False), row_keys)
        self.assertIn(("197", "bypass", False), row_keys)

    def test_adapter_does_not_apply_first_frame_mapping_to_three_frame_endpoint(self) -> None:
        repaired = CloudComfyUIAdapter._repair_known_runninghub_node_info(
            json.dumps(
                [
                    {"nodeId": "177", "fieldName": "text", "fieldValue": "{{prompt}}"},
                    {"nodeId": "178", "fieldName": "prompt", "fieldValue": "{{prompt}}"},
                    {"nodeId": "193", "fieldName": "image", "fieldValue": "{{reference_image}}"},
                ]
            ),
            endpoint="/run/workflow/2072296894507872257",
            workflow_id="06_i2v_first_frame",
            workflow_mode="i2v_first_frame",
        )
        rows = json.loads(repaired)
        row_keys = {(row["nodeId"], row["fieldName"], row["fieldValue"]) for row in rows}
        self.assertNotIn("178", {row["nodeId"] for row in rows})
        self.assertIn(("447", "image", "{{input_base_image}}"), row_keys)
        self.assertIn(("448", "image", "{{input_middle_frame}}"), row_keys)
        self.assertIn(("449", "image", "{{input_last_frame}}"), row_keys)
        self.assertNotIn(("426", "seed", "{{seed}}"), row_keys)
        self.assertIn(("426", "preset_prompt", "Describe this image in detail."), row_keys)

    def test_adapter_repairs_three_frame_qwenvl_preset_prompt(self) -> None:
        repaired = CloudComfyUIAdapter._repair_known_runninghub_node_info(
            json.dumps(
                [
                    {"nodeId": "447", "fieldName": "image", "fieldValue": "{{input_base_image}}"},
                    {"nodeId": "448", "fieldName": "image", "fieldValue": "{{input_middle_frame}}"},
                    {"nodeId": "449", "fieldName": "image", "fieldValue": "{{input_last_frame}}"},
                    {"nodeId": "426", "fieldName": "seed", "fieldValue": "{{seed}}"},
                ]
            ),
            endpoint="/run/workflow/2072296894507872257",
            workflow_id="06_i2v_first_middle_last_frame",
            workflow_mode="i2v_first_middle_last_frame",
        )
        rows = json.loads(repaired)
        row_keys = {(row["nodeId"], row["fieldName"], row["fieldValue"]) for row in rows}
        self.assertNotIn(("426", "seed", "{{seed}}"), row_keys)
        self.assertIn(("426", "preset_prompt", "Describe this image in detail."), row_keys)

    def test_adapter_treats_207173_as_first_frame_endpoint(self) -> None:
        repaired = CloudComfyUIAdapter._repair_known_runninghub_node_info(
            json.dumps(
                [
                    {"nodeId": "177", "fieldName": "text", "fieldValue": "{{prompt}}"},
                    {"nodeId": "216", "fieldName": "value", "fieldValue": False},
                    {"nodeId": "193", "fieldName": "image", "fieldValue": "{{reference_image}}"},
                ]
            ),
            endpoint="/run/workflow/2071735603636563970",
            workflow_id="06_i2v_first_frame",
            workflow_mode="i2v_first_frame",
        )
        rows = json.loads(repaired)
        row_keys = {(row["nodeId"], row["fieldName"], row["fieldValue"]) for row in rows}
        self.assertNotIn("447", {row["nodeId"] for row in rows})
        self.assertIn(("216", "value", False), row_keys)
        self.assertIn(("177", "text", "{{prompt}}"), row_keys)
        self.assertNotIn("178", {row["nodeId"] for row in rows})
        self.assertIn(("193", "image", "{{reference_image}}"), row_keys)

    def test_runtime_config_repairs_three_frame_saved_node_info(self) -> None:
        config = {
            "workflow_library": [
                {
                    "id": "06_i2v_first_middle_last_frame",
                    "mode_configs": {
                        "i2v_first_middle_last_frame": {
                            "endpoint": "/run/workflow/2072296894507872257",
                            "node_info_list_json": json.dumps(
                                [
                                    {"nodeId": "447", "fieldName": "image", "fieldValue": "{{reference_image}}"},
                                    {"nodeId": "448", "fieldName": "image", "fieldValue": "{{reference_image}}"},
                                    {"nodeId": "449", "fieldName": "image", "fieldValue": "{{reference_image}}"},
                                ]
                            ),
                        }
                    },
                }
            ]
        }

        web_app.WorkflowWebHandler._repair_runtime_comfy_node_info(config)

        mode_config = config["workflow_library"][0]["mode_configs"]["i2v_first_middle_last_frame"]
        rows = json.loads(mode_config["node_info_list_json"])
        row_keys = {(row["nodeId"], row["fieldName"], row["fieldValue"]) for row in rows}
        self.assertIn(("447", "image", "{{reference_image}}"), row_keys)
        self.assertIn(("448", "image", "{{input_middle_frame}}"), row_keys)
        self.assertIn(("449", "image", "{{input_last_frame}}"), row_keys)

    def test_adapter_replaces_long_side_placeholder(self) -> None:
        adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
        payload = {
            "prompt": "slow push in",
            "reference_image": "first.png",
            "width": 480,
            "height": 848,
            "duration": 4,
            "fps": 24,
            "seed": 123,
        }
        config = {
            "node_info_list_json": (
                '[{"nodeId":"186","fieldName":"value","fieldValue":"{{long_side}}"},'
                '{"nodeId":"177","fieldName":"text","fieldValue":"{{prompt}}"}]'
            )
        }
        request = adapter._build_runninghub_payload(payload, config)
        rows = request["nodeInfoList"]
        self.assertIn({"nodeId": "186", "fieldName": "value", "fieldValue": 848}, rows)
        self.assertIn({"nodeId": "177", "fieldName": "text", "fieldValue": "slow push in"}, rows)

    def test_adapter_resolves_generated_character_master_reference(self) -> None:
        global_context = {
            "characters": [
                {
                    "character_id": "piggy_worker",
                    "generated_master_job_id": "asset_piggy_worker_front",
                    "master_image_binding": {"from_job": "asset_piggy_worker_front", "output": "output_final_image"},
                }
            ]
        }
        generated_map = {"asset_piggy_worker_front": "I:/tmp/master.png"}
        job = {"job_id": "asset_piggy_worker_expression_happy", "character_id": "piggy_worker"}
        self.assertEqual(
            CloudComfyUIAdapter._generated_master_reference_for_job(job, global_context, generated_map),
            "I:/tmp/master.png",
        )
        self.assertTrue(CloudComfyUIAdapter._job_has_generated_character_master(job, global_context))
        master_job = {"job_id": "asset_piggy_worker_front", "character_id": "piggy_worker"}
        self.assertEqual(
            CloudComfyUIAdapter._generated_master_reference_for_job(master_job, global_context, generated_map),
            "",
        )

    def test_adapter_replaces_identity_scene_image_placeholders(self) -> None:
        adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
        config = {
            "node_info_list_json": (
                '[{"nodeId":"35","fieldName":"image","fieldValue":"{{input_identity_image}}"},'
                '{"nodeId":"22","fieldName":"image","fieldValue":"{{input_scene_image}}"},'
                '{"nodeId":"12","fieldName":"denoise","fieldValue":"{{denoise}}"},'
                '{"nodeId":"21","fieldName":"prompt","fieldValue":"{{prompt}}"}]'
            )
        }
        payload = {
            "prompt": "put Xiaomei into the school playground",
            "input_identity_image": "characters/xiaomei.png",
            "input_scene_image": "scenes/playground.png",
            "workflow_mode": "identity_scene_keyframe",
            "seed": 123,
        }

        built = adapter._build_runninghub_payload(payload, config)

        values = {(item["nodeId"], item["fieldName"]): item["fieldValue"] for item in built["nodeInfoList"]}
        self.assertEqual(values[("35", "image")], "characters/xiaomei.png")
        self.assertEqual(values[("22", "image")], "scenes/playground.png")
        self.assertEqual(values[("12", "denoise")], 1.0)
        self.assertEqual(values[("21", "prompt")], "put Xiaomei into the school playground")
        self.assertNotIn("{{input_scene_image}}", json.dumps(built["nodeInfoList"], ensure_ascii=False))
        self.assertNotIn("{{denoise}}", json.dumps(built["nodeInfoList"], ensure_ascii=False))

    def test_debug_identity_validation_accepts_legacy_reference_image_slot(self) -> None:
        marker = "input_identity_image: ['{{input_identity_image}}', '{{identity_image}}', '{{reference_image}}', '{{reference_image_1}}']"
        self.assertIn(marker, web_app.INDEX_HTML)
        self.assertNotIn("throw new Error(`nodeInfoList 缺少语义槽位映射", web_app.INDEX_HTML)

    def test_debug_payload_uses_mode_default_denoise(self) -> None:
        marker = "denoise: String(overrides.denoise ?? imageTaskDef.defaultDenoise ?? workflowModeDef?.default_denoise ?? workflowModeDef?.defaultDenoise ?? '').trim()"
        self.assertIn(marker, web_app.INDEX_HTML)
        self.assertIn("defaultDenoise: workflowModeDef.default_denoise || workflowModeDef.defaultDenoise || ''", web_app.INDEX_HTML)

    def test_adapter_defaults_blank_img2img_denoise_node_info(self) -> None:
        adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
        config = {
            "node_info_list_json": (
                '[{"nodeId":"24","fieldName":"denoise","fieldValue":""},'
                '{"nodeId":"24","fieldName":"steps","fieldValue":8}]'
            )
        }
        payload = {"workflow_mode": "img2img_style_keyframe", "seed": 123}

        built = adapter._build_runninghub_payload(payload, config)

        values = {(item["nodeId"], item["fieldName"]): item["fieldValue"] for item in built["nodeInfoList"]}
        self.assertEqual(values[("24", "denoise")], 1.0)

    def test_adapter_never_sends_blank_denoise_node_info(self) -> None:
        adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
        config = {"node_info_list_json": '[{"nodeId":"24","fieldName":"denoise","fieldValue":""}]'}

        built = adapter._build_runninghub_payload({"seed": 123}, config)

        self.assertEqual(built["nodeInfoList"][0]["fieldValue"], 1.0)

    def test_adapter_replaces_multi_character_placeholders(self) -> None:
        adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
        config = {
            "node_info_list_json": (
                '[{"nodeId":"1","fieldName":"image","fieldValue":"{{character_reference_1}}"},'
                '{"nodeId":"2","fieldName":"image","fieldValue":"{{character_reference_2}}"},'
                '{"nodeId":"3","fieldName":"image","fieldValue":"{{character_reference_3}}"},'
                '{"nodeId":"4","fieldName":"text","fieldValue":"{{character_id_1}} {{character_position_2}}"}]'
            )
        }
        payload = {
            "character_references": [
                {"character_id": "hero", "identity_image": "hero.png", "position": "left"},
                {"character_id": "mentor", "identity_image": "mentor.png", "position": "right"},
            ]
        }
        built = adapter._build_runninghub_payload(payload, config)
        values = [(item["nodeId"], item["fieldValue"]) for item in built["nodeInfoList"]]
        self.assertEqual(values, [("1", "hero.png"), ("2", "mentor.png"), ("4", "hero right")])

    def test_turnaround_outputs_are_stitched_for_portrait_keyframe_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            files = self._make_turnaround_images(root, size=(40, 80))
            adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
            manifest = adapter._maybe_append_turnaround_sheet(
                {"task_type": "character_turnaround", "width": 480, "height": 848},
                {},
                root,
                {"status": "success", "downloaded_files": [str(path) for path in files]},
            )
            sheet = Path(manifest["downloaded_files"][0])
            self.assertTrue(sheet.name.endswith("_turnaround_sheet.png"))
            self.assertEqual(Path(manifest["turnaround_sheet"]["file"]), sheet)
            layout = manifest["turnaround_sheet"]["layout"]
            self.assertEqual(layout["strategy"], "portrait_priority_quadrants")
            self.assertEqual(layout["background"], (88, 24, 88, 255))
            self.assertEqual(layout["slots"][0]["role"], "main_front")
            self.assertEqual([slot["role"] for slot in layout["slots"][1:]], ["back_view", "left_side_view", "right_side_or_detail"])
            self.assertEqual({slot["w"] for slot in layout["slots"][1:]}, {layout["slots"][1]["w"]})
            self.assertEqual({slot["h"] for slot in layout["slots"][1:]}, {layout["slots"][1]["h"]})
            self.assertGreater(layout["slots"][0]["w"] * layout["slots"][0]["h"], layout["slots"][3]["w"] * layout["slots"][3]["h"])
            with Image.open(sheet) as image:
                self.assertGreater(image.height, image.width)
                self.assertEqual(image.getpixel((0, 0)), (88, 24, 88))

    def test_turnaround_outputs_are_stitched_for_landscape_keyframe_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            files = self._make_turnaround_images(root, size=(40, 80))
            adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
            manifest = adapter._maybe_append_turnaround_sheet(
                {"workflow_mode": "product_turnaround", "width": 848, "height": 480},
                {},
                root,
                {"status": "success", "downloaded_files": [str(path) for path in files]},
            )
            sheet = Path(manifest["downloaded_files"][0])
            layout = manifest["turnaround_sheet"]["layout"]
            self.assertEqual(layout["strategy"], "landscape_left_main_right_stack")
            self.assertEqual(layout["background"], (88, 24, 88, 255))
            self.assertEqual(layout["slots"][0]["role"], "main_front")
            self.assertEqual([slot["role"] for slot in layout["slots"][1:]], ["back_view", "left_side_view", "right_side_view"])
            self.assertEqual({slot["w"] for slot in layout["slots"][1:]}, {layout["slots"][1]["w"]})
            self.assertEqual({slot["h"] for slot in layout["slots"][1:]}, {layout["slots"][1]["h"]})
            self.assertGreater(layout["slots"][0]["h"], layout["slots"][1]["h"])
            with Image.open(sheet) as image:
                self.assertGreater(image.width, image.height)
                self.assertEqual(image.getpixel((0, 0)), (88, 24, 88))

    def test_turnaround_sheet_background_follows_source_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            warm_files = self._make_turnaround_images(root / "warm", size=(40, 80), colors=[(240, 232, 210), (230, 225, 215), (238, 230, 220), (232, 226, 218)])
            dark_files = self._make_turnaround_images(root / "dark", size=(40, 80), colors=[(35, 38, 42), (40, 42, 45), (32, 35, 40), (44, 42, 38)])
            adapter = CloudComfyUIAdapter("https://example.invalid", "key", "/run/workflow/test")
            warm_manifest = adapter._maybe_append_turnaround_sheet(
                {"task_type": "character_turnaround", "width": 480, "height": 848},
                {},
                root / "warm",
                {"status": "success", "downloaded_files": [str(path) for path in warm_files]},
            )
            dark_manifest = adapter._maybe_append_turnaround_sheet(
                {"task_type": "character_turnaround", "width": 480, "height": 848},
                {},
                root / "dark",
                {"status": "success", "downloaded_files": [str(path) for path in dark_files]},
            )
            self.assertGreater(warm_manifest["turnaround_sheet"]["layout"]["background"][0], 220)
            self.assertLess(dark_manifest["turnaround_sheet"]["layout"]["background"][0], 60)

    @staticmethod
    def _make_turnaround_images(root: Path, size: tuple[int, int], colors: list[str | tuple[int, int, int]] | None = None) -> list[Path]:
        root.mkdir(parents=True, exist_ok=True)
        files = []
        colors = colors or ["red", "green", "blue", "purple"]
        for index, color in enumerate(colors, start=1):
            path = root / f"view_{index}.png"
            Image.new("RGB", size, color).save(path)
            files.append(path)
        return files

    @staticmethod
    def _write_two_character_fixture(root: Path) -> tuple[Path, Path]:
        entity_path = root / "entities.json"
        library_path = root / "library.json"
        entity_path.write_text(
            json.dumps(
                {
                    "characters": {
                        "hero": {"character_id": "hero", "name": "Hero", "master_image": "asset_hero"},
                        "mentor": {"character_id": "mentor", "name": "Mentor", "master_image": "asset_mentor"},
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        library_path.write_text(
            json.dumps(
                [
                    {"id": "asset_hero", "asset_id": "asset_hero", "file": "01_character_base/hero.png", "kind": "image", "tags": ["character_base"], "character_id": "hero", "approved": True},
                    {"id": "asset_mentor", "asset_id": "asset_mentor", "file": "01_character_base/mentor.png", "kind": "image", "tags": ["character_base"], "character_id": "mentor", "approved": True},
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return entity_path, library_path


if __name__ == "__main__":
    unittest.main()
