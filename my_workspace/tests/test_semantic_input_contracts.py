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
from my_codex_core.local_ffmpeg_adapter import LocalFFmpegAdapter  # noqa: E402
from my_codex_core.production_plan_compiler import (  # noqa: E402
    _bind_first_source_video,
    _image_prompt_item,
    compile_production_plan,
)
from my_codex_core.production_pipeline import (  # noqa: E402
    _packaging_dependency_blockers,
    _payload_has_required_mode,
    _required_workflow_slots,
)
from my_codex_core.production_output_validator import validate_production_output  # noqa: E402


class SemanticInputContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflows = {item["id"]: item for item in web_app.COMFY_DEBUG_WORKFLOWS}

    def test_keyframe_modes_are_explicit_and_typed(self) -> None:
        modes = {item["value"]: item for item in self.workflows["04_keyframe"]["modes"]}
        self.assertEqual(modes["keyframe"]["required_inputs"], [])
        self.assertEqual(modes["identity_keyframe"]["required_inputs"], ["input_identity_image"])
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
        self.assertIn("'pose_identity_keyframe'", image_to_image_group)
        self.assertIn("'multi_identity_keyframe'", image_to_image_group)
        self.assertIn("'multi_pose_identity_keyframe'", image_to_image_group)
        self.assertIn("comfyDebugCharacterEntity", source)
        self.assertIn("comfyDebugIdentityAssetReference", source)
        self.assertIn("comfyDebugPoseAssetReference", source)
        self.assertIn("productionEntityTurnaround", source)

    def test_consistent_character_keyframe_presets_are_mode_specific(self) -> None:
        library = WORKSPACE / "my_workspace" / "comfyui_workflows" / "workflow_library" / "04_keyframe_image"
        identity_canvas = library / "consistent_character_identity_keyframe_canvas.json"
        pose_canvas = library / "consistent_character_pose_identity_keyframe_canvas.json"
        identity_nodeinfo = library / "consistent_character_identity_keyframe_nodeinfo.json"
        pose_nodeinfo = library / "consistent_character_pose_identity_keyframe_nodeinfo.json"
        for path in (identity_canvas, pose_canvas, identity_nodeinfo, pose_nodeinfo):
            self.assertTrue(path.is_file(), path)

        identity_rows = json.loads(identity_nodeinfo.read_text(encoding="utf-8"))
        pose_rows = json.loads(pose_nodeinfo.read_text(encoding="utf-8"))
        self.assertIn("{{input_identity_image}}", json.dumps(identity_rows, ensure_ascii=False))
        self.assertNotIn("{{input_pose_image}}", json.dumps(identity_rows, ensure_ascii=False))
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
        self.assertLessEqual(expression["denoise"], 0.38)
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
        self.assertIn("不换脸", expression["prompt"])
        self.assertIn("不换衣服", expression["prompt"])

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

    def test_broll_sanitizes_character_names_to_environment_only(self) -> None:
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
            self.assertEqual(item["workflow_id"], "10_broll_transition_video")
            self.assertEqual(item["workflow_mode"], "broll_scene_video")
            self.assertEqual(item["character_id"], "")
            self.assertTrue(item["no_visible_characters"])
            self.assertEqual(item["broll_policy"], "environment_only")
            self.assertNotIn("Corgi King", item["prompt"])
            self.assertNotIn("柯基国王", item["prompt"])
            self.assertIn("不出现主角", item["prompt"])
            self.assertNotIn("character", item.get("entity_context", {}))

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

    def test_video_filter_can_pad_to_target_duration(self) -> None:
        filter_text = LocalFFmpegAdapter._video_filter(None, "", 1080, 1920, 24, pad_end_seconds=8.5)
        self.assertIn("tpad=stop_mode=clone:stop_duration=8.500", filter_text)

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
