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
from my_codex_core.production_plan_compiler import (  # noqa: E402
    _bind_first_source_video,
    _image_prompt_item,
    compile_production_plan,
)


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

    def test_keyframe_ui_group_lists_all_keyframe_modes(self) -> None:
        source = (WORKSPACE / "my_workspace" / "web_app.py").read_text(encoding="utf-8")
        group_line = next(
            line for line in source.splitlines() if "id: 'storyboard_keyframe'" in line
        )
        self.assertIn("'keyframe'", group_line)
        self.assertIn("'identity_keyframe'", group_line)
        self.assertIn("'pose_identity_keyframe'", group_line)
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

    def test_video_source_binding_uses_video_output(self) -> None:
        item = {"source_intent_ids": ["clip_001"], "input_bindings": {}, "depends_on": []}
        _bind_first_source_video(item, {"clip_001"})
        self.assertEqual(
            item["input_bindings"]["input_source_video"],
            {"from_job": "clip_001", "output": "output_final_video"},
        )
        self.assertIn("clip_001", item["depends_on"])

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
            self.assertEqual(manifest["turnaround_sheet"]["layout"]["columns"], 2)
            with Image.open(sheet) as image:
                self.assertGreater(image.height, image.width)

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
            self.assertEqual(manifest["turnaround_sheet"]["layout"]["columns"], 4)
            with Image.open(sheet) as image:
                self.assertGreater(image.width, image.height)

    @staticmethod
    def _make_turnaround_images(root: Path, size: tuple[int, int]) -> list[Path]:
        files = []
        colors = ["red", "green", "blue", "purple"]
        for index, color in enumerate(colors, start=1):
            path = root / f"view_{index}.png"
            Image.new("RGB", size, color).save(path)
            files.append(path)
        return files


if __name__ == "__main__":
    unittest.main()
