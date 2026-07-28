from types import SimpleNamespace

from v2.backend.app.production.planning_service import (
    _candidate_assignment_reason,
    _route_feasibility_errors,
    _validate_route_assignments,
)
from v2.backend.app.production.service import _compile_manifest, _node_seed, _validate_generation_capabilities


def workflow(*, tags: list[str], bindings: list[dict]):
    return SimpleNamespace(capability_tags=tags, node_info_list=bindings)


def shot(code: str, requirements: dict):
    return SimpleNamespace(id=f"id-{code}", shot_code=code, generation_requirements=requirements)


def test_production_planner_replaces_technical_english_reason_with_chinese_summary() -> None:
    reason = _candidate_assignment_reason(
        "Production profile enforces three_frame video motion strategy.",
        {
            "generation_requirements": {
                "reference_image_required": False,
                "identity_consistency_required": False,
                "precise_text_required": False,
            },
        },
        {"video_motion_strategy": "three_frame", "enforcement": "required"},
    )

    assert reason == "项目已选择首中尾三帧，当前视频方案支持三张关键帧共同控制动作。"
    assert "three_frame" not in reason


def test_production_planner_keeps_readable_chinese_reason() -> None:
    reason = "所选方案符合这个镜头的连续动作要求。"

    assert _candidate_assignment_reason(reason, {}, {}) == reason


def test_generation_capabilities_report_exact_shots_without_route_replacement() -> None:
    requirements = {
        "reference_image_required": True,
        "multi_frame_required": True,
        "identity_consistency_required": True,
        "precise_text_required": True,
    }
    shots = [shot("SH-001", requirements)]
    keyframe = workflow(tags=["text_to_image"], bindings=[{"value_source": "shot.visual_prompt"}])
    video = workflow(tags=["i2v", "first_frame"], bindings=[])
    errors: list[dict] = []

    _validate_generation_capabilities(shots, {"id-SH-001": None}, keyframe, video, errors)

    assert {item["code"] for item in errors} == {
        "REFERENCE_IMAGE_CAPABILITY_MISSING",
        "REQUIRED_REFERENCE_IMAGE_MISSING",
        "MULTI_FRAME_CAPABILITY_MISSING",
        "IDENTITY_CONSISTENCY_CAPABILITY_MISSING",
        "PRECISE_TEXT_CAPABILITY_MISSING",
    }
    assert all(item["shot_code"] == "SH-001" for item in errors)
    assert keyframe.capability_tags == ["text_to_image"]
    assert video.capability_tags == ["i2v", "first_frame"]


def test_generation_capabilities_accept_explicit_declared_support() -> None:
    requirements = {
        "reference_image_required": True,
        "multi_frame_required": True,
        "identity_consistency_required": True,
        "precise_text_required": True,
    }
    current = shot("SH-001", requirements)
    keyframe = workflow(tags=["precise_text"], bindings=[{"value_source": "reference_image.primary"}])
    video = workflow(tags=["multi_frame"], bindings=[])
    errors: list[dict] = []

    _validate_generation_capabilities([current], {current.id: {"content_hash": "known"}}, keyframe, video, errors)

    assert errors == []


def route_workflow(
    workflow_id: str,
    *,
    operation_kind: str,
    tags: list[str],
    bindings: list[dict],
    video_spec_id: str = "spec-1",
):
    return SimpleNamespace(
        id=workflow_id,
        operation_kind=operation_kind,
        status="published",
        supported_video_spec_ids=[video_spec_id],
        capability_tags=tags,
        node_info_list=bindings,
    )


def test_route_preflight_blocks_precise_pixel_text_before_model_invocation() -> None:
    current = SimpleNamespace(
        shot_code="SH-001",
        primary_reference_entity_version_id=None,
        generation_requirements={
            "reference_image_required": False,
            "multi_frame_required": False,
            "identity_consistency_required": False,
            "precise_text_required": True,
        },
    )
    workflows = [
        route_workflow(
            "image-1",
            operation_kind="image_generation",
            tags=["text_to_image"],
            bindings=[{"value_source": "shot.visual_prompt"}],
        ),
        route_workflow(
            "video-1",
            operation_kind="video_generation",
            tags=["first_frame"],
            bindings=[{"value_source": "source_image"}],
        ),
    ]

    errors = _route_feasibility_errors(
        [current], workflows, SimpleNamespace(id="spec-1"),
    )

    assert errors == [{
        "code": "PRODUCTION_PLAN_NO_FEASIBLE_ROUTE",
        "shot_code": "SH-001",
        "causes": ["PRODUCTION_PLAN_PRECISE_TEXT_CAPABILITY_MISSING"],
    }]


def test_route_preflight_keeps_final_overlay_text_out_of_generation_capabilities() -> None:
    current = SimpleNamespace(
        shot_code="SH-001",
        primary_reference_entity_version_id=None,
        generation_requirements={
            "reference_image_required": False,
            "multi_frame_required": False,
            "identity_consistency_required": False,
            "precise_text_required": False,
        },
    )
    workflows = [
        route_workflow(
            "image-1",
            operation_kind="image_generation",
            tags=["text_to_image"],
            bindings=[{"value_source": "shot.visual_prompt"}],
        ),
        route_workflow(
            "video-1",
            operation_kind="video_generation",
            tags=["first_frame"],
            bindings=[{"value_source": "source_image"}],
        ),
    ]

    assert _route_feasibility_errors(
        [current], workflows, SimpleNamespace(id="spec-1"),
    ) == []


def test_three_frame_route_compiles_three_distinct_parent_images() -> None:
    current = SimpleNamespace(
        id="shot-1", shot_code="SH-001", sequence_number=1, duration_ms=4000,
        narrative_beat_code="BEAT_01", brief_segment_codes=["SEG_01"],
        shot_purpose="develop", framing="medium", camera_angle="eye_level",
        camera_motion="tracking", subject_motion="moderate", continuity_relation="same_moment",
        scene_entity_version_id=None, character_entity_version_ids=[], outfit_entity_version_ids=[],
        product_entity_version_ids=[], primary_reference_entity_version_id=None,
        face_visibility="optional", face_subject_entity_version_ids=[], text_policy="forbidden",
        required_on_screen_text=None, composition="same composition", action="turn",
        visual_prompt="continuous turn", negative_prompt=None, new_information="turn state",
        generation_requirements={"reference_image_required": False, "multi_frame_required": True, "identity_consistency_required": False, "precise_text_required": False},
        guide_frame_prompts={"start": "facing front", "middle": "half turn", "end": "facing back"},
    )
    keyframe = SimpleNamespace(id="image-slot")
    video = SimpleNamespace(id="video-slot", operation_kind="multi_frame_video_generation")

    manifest = _compile_manifest(
        SimpleNamespace(id="plan-1"), [current], {"video_spec_version_id": "spec-1"}, {}, "off",
        {current.id: None}, {current.id: (keyframe, video)},
    )

    image_nodes = [node for node in manifest["nodes"] if node["kind"] == "generate_keyframe"]
    prompts = [node["input_contract"]["shot"]["visual_prompt"] for node in image_nodes]
    assert all("共同画面基础：continuous turn" in prompt for prompt in prompts)
    assert all("同一器具与道具" in prompt for prompt in prompts)
    assert all("景别=medium；镜头角度=eye_level；构图=same composition" in prompt for prompt in prompts)
    assert [prompt.rsplit("：", 1)[-1] for prompt in prompts] == [
        "facing front", "half turn", "facing back",
    ]
    assert len({node["input_contract"]["seed"] for node in image_nodes}) == 1
    assert image_nodes[0]["input_contract"]["seed"] == _node_seed("plan-1", "SH-001.keyframe")
    video_node = next(node for node in manifest["nodes"] if node["kind"] == "generate_three_frame_i2v_clip")
    assert video_node["input_contract"]["source_image_node_keys"] == [
        "SH-001.keyframe.start", "SH-001.keyframe.middle", "SH-001.keyframe.end",
    ]
    assert [edge["input_slot"] for edge in manifest["edges"][:3]] == [
        "source_image.start", "source_image.middle", "source_image.end",
    ]


def test_single_frame_shot_cannot_select_three_frame_video_route() -> None:
    current = SimpleNamespace(
        shot_code="SH-001",
        primary_reference_entity_version_id=None,
        guide_frame_prompts=None,
        generation_requirements={
            "reference_image_required": False,
            "multi_frame_required": False,
            "identity_consistency_required": False,
            "precise_text_required": False,
        },
    )
    keyframe = route_workflow(
        "image-1", operation_kind="image_generation", tags=["text_to_image"],
        bindings=[{"value_source": "shot.visual_prompt"}],
    )
    video = route_workflow(
        "video-3f", operation_kind="multi_frame_video_generation", tags=["multi_frame", "three_frame"],
        bindings=[
            {"value_source": "source_image.start"},
            {"value_source": "source_image.middle"},
            {"value_source": "source_image.end"},
        ],
    )

    errors = _validate_route_assignments(
        [{
            "shot_code": "SH-001",
            "keyframe_workflow_slot_version_id": "image-1",
            "video_workflow_slot_version_id": "video-3f",
        }],
        [current], [keyframe, video], SimpleNamespace(id="spec-1"), validate_reported_inputs=False,
    )

    assert {item["code"] for item in errors} == {"PRODUCTION_PLAN_MULTI_FRAME_NOT_REQUESTED"}
