from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "my_workspace"))

import web_app  # noqa: E402
from my_codex_core.cloud_comfyui_adapter import CloudComfyUIAdapter  # noqa: E402
from my_codex_core.production_plan_compiler import (  # noqa: E402
    _bind_first_source_video,
    _image_prompt_item,
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


if __name__ == "__main__":
    unittest.main()
