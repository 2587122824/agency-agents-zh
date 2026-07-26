import hashlib
from types import SimpleNamespace
import wave

from v2.backend.app.workers import worker
from v2.backend.app.production.service import _compile_manifest
from v2.backend.app.quality.service import _deterministic_contract_findings, asset_waveform
import v2.backend.app.quality.service as quality_module
from v2.backend.app.delivery.renderer import (
    LocalFFmpegRenderer,
    LocalRenderAudioInput,
    LocalRenderInput,
    LocalRenderRequest,
    LocalRenderSubtitleInput,
)


class FakeRepository:
    def __init__(self, snapshot, assets, input_slots):
        self._snapshot = snapshot
        self.assets = assets
        self.input_slots = input_slots

    def snapshot(self, _snapshot_id):
        return self._snapshot

    def asset(self, asset_id):
        return self.assets.get(asset_id)

    def required_parent_input_slots(self, _item):
        return self.input_slots


def test_production_manifest_freezes_voiceover_and_subtitles_as_timeline_dependencies() -> None:
    plan = SimpleNamespace(
        id="plan-voiceover",
        creative_brief={
            "narrative_beats": [
                {"beat_code": "BEAT_01", "target_duration_ms": 2000},
                {"beat_code": "BEAT_02", "target_duration_ms": 3000},
            ],
            "script_segments": [
                {"segment_code": "SEG_01", "beat_code": "BEAT_01", "kind": "voiceover", "spoken_text": "第一段旁白。"},
                {"segment_code": "SEG_02", "beat_code": "BEAT_02", "kind": "dialogue", "spoken_text": "第二段对白。"},
            ],
        },
    )
    shots = [
        SimpleNamespace(
            id=f"shot-{index}",
            shot_code=f"SH-{index:03d}",
            sequence_number=index,
            duration_ms=duration,
            narrative_beat_code=f"BEAT_{index:02d}",
            brief_segment_codes=[f"SEG_{index:02d}"],
            shot_purpose="推进叙事",
            framing="medium",
            camera_angle="eye_level",
            camera_motion="static",
            subject_motion="speaking",
            continuity_relation="continuous",
            action="action",
            composition="composition",
            visual_prompt="visual",
            negative_prompt=None,
            guide_frame_prompts={},
            scene_entity_version_id=None,
            character_entity_version_ids=[],
            outfit_entity_version_ids=[],
            product_entity_version_ids=[],
            primary_reference_entity_version_id=None,
            face_visibility="not_visible",
            face_subject_entity_version_ids=[],
            text_policy="none",
            required_on_screen_text=[],
            new_information="new",
            generation_requirements={},
        )
        for index, duration in ((1, 2000), (2, 3000))
    ]
    video_workflow = SimpleNamespace(id="workflow-video", operation_kind="text_to_video_generation")
    routes = {shot.id: (None, video_workflow) for shot in shots}
    selection = {
        "video_spec_version_id": "video-spec",
        "tts_workflow_slot_version_id": "tts-slot",
        "audio_execution": {
            "voice": {"key": "steady_male", "display_name": "沉稳男声", "provider_voice_id": "longxiaocheng"},
            "speaking_rate": 1.1,
            "volume": 62,
            "target_duration_ms": 5000,
            "duration_tolerance_ms": 1200,
            "loudness_target_lufs": -16,
            "format": "wav",
            "sample_rate": 24000,
            "channels": 1,
        },
    }

    manifest = _compile_manifest(plan, shots, selection, {"format": "mp4"}, "voiceover", {}, routes)
    node_by_key = {node["node_key"]: node for node in manifest["nodes"]}
    timeline_parents = {
        edge["parent_node_key"]
        for edge in manifest["edges"]
        if edge["child_node_key"] == "project.timeline"
    }

    assert node_by_key["project.voiceover"]["kind"] == "generate_tts"
    assert node_by_key["project.voiceover"]["input_contract"]["voiceover_text"] == "第一段旁白。\n第二段对白。"
    assert node_by_key["project.voiceover"]["input_contract"]["voice"]["provider_voice_id"] == "longxiaocheng"
    assert node_by_key["project.voiceover"]["input_contract"]["speaking_rate"] == 1.1
    assert node_by_key["project.voiceover"]["input_contract"]["volume"] == 62
    assert node_by_key["project.voiceover"]["output_contract"] == {
        "media_type": "audio", "format": "wav", "sample_rate": 24000, "channels": 1,
    }
    assert node_by_key["project.subtitles"]["kind"] == "generate_subtitles"
    assert node_by_key["project.subtitles"]["input_contract"]["duration_ms"] == 5000
    assert [cue["text"] for cue in node_by_key["project.subtitles"]["input_contract"]["cues"]] == ["第一段旁白。", "第二段对白。"]
    assert {"project.voiceover", "project.subtitles"} <= timeline_parents

    silent_manifest = _compile_manifest(plan, shots, selection, {"format": "mp4"}, "off", {}, routes)
    assert all(node["kind"] not in {"generate_tts", "generate_subtitles"} for node in silent_manifest["nodes"])


def test_audio_duration_gate_uses_frozen_target_and_tolerance() -> None:
    node = SimpleNamespace(input_contract={"target_duration_ms": 5000, "duration_tolerance_ms": 1200})

    accepted = _deterministic_contract_findings(
        None,
        SimpleNamespace(asset_type="audio", duration_ms=6200),
        node,
    )
    blocked = _deterministic_contract_findings(
        None,
        SimpleNamespace(asset_type="audio", duration_ms=6201),
        node,
    )

    assert accepted == []
    assert [finding["code"] for finding in blocked] == ["AUDIO_DURATION_EXCEEDS_TARGET"]
    assert blocked[0]["evidence"] == {"actual_ms": 6201, "target_ms": 5000, "tolerance_ms": 1200}


def test_audio_waveform_is_deterministic_and_cached_by_content_hash(tmp_path, monkeypatch) -> None:
    audio_path = tmp_path / "voice.wav"
    with wave.open(str(audio_path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(1000)
        target.writeframes(b"\x00\x00" * 500 + b"\xff\x7f" * 500)
    content_hash = hashlib.sha256(audio_path.read_bytes()).hexdigest()
    asset = SimpleNamespace(id="asset-audio", asset_type="audio", content_hash=content_hash, uri="runtime://voice.wav")
    monkeypatch.setattr(quality_module, "RUNTIME_ROOT", tmp_path)
    monkeypatch.setattr(quality_module, "_require_asset", lambda *_args: asset)
    monkeypatch.setattr(quality_module, "_local_asset_path", lambda _uri: audio_path)

    first = asset_waveform(None, None, "asset-audio", 40)
    second = asset_waveform(None, None, "asset-audio", 40)

    assert first == second
    assert first["schema_version"] == "audio-waveform-cache.v1"
    assert first["duration_ms"] == 1000
    assert first["peaks"][:20] == [0.0] * 20
    assert all(peak == 1.0 for peak in first["peaks"][20:])
    assert (tmp_path / "cache" / "waveforms" / f"{content_hash}-40.json").is_file()


def test_provider_request_preserves_dependency_input_slots(monkeypatch) -> None:
    parents = [
        SimpleNamespace(
            id=f"work-{role}",
            kind="generate_keyframe",
            dag_node_id=f"node-{role}",
            current_attempt_id=f"attempt-{role}",
        )
        for role in ("start", "middle", "end")
    ]
    input_slots = {
        f"node-{role}": f"source_image.{role}"
        for role in ("start", "middle", "end")
    }
    assets = {
        f"asset-{role}": SimpleNamespace(
            snapshot_id="snapshot-1",
            dag_node_id=f"node-{role}",
            state="approved",
            content_hash=f"hash-{role}",
            provider_output_manifest={"asset_type": "image", "uri": f"runtime://{role}.png"},
        )
        for role in ("start", "middle", "end")
    }
    snapshot = SimpleNamespace(image_phase_approval_manifest={
        "assets": [
            {
                "dag_node_id": f"node-{role}",
                "asset_id": f"asset-{role}",
                "content_hash": f"hash-{role}",
            }
            for role in ("start", "middle", "end")
        ],
    })
    monkeypatch.setattr(worker, "_work", lambda _session: FakeRepository(snapshot, assets, input_slots))

    request = worker._provider_request(
        object(),
        SimpleNamespace(kind="generate_three_frame_i2v_clip", snapshot_id="snapshot-1", dag_node_id="node-video"),
        SimpleNamespace(request_fingerprint="f" * 64, request_manifest={"contract": "frozen"}),
        parents,
    )

    assert [item["input_slot"] for item in request.parent_outputs] == [
        "source_image.start", "source_image.middle", "source_image.end",
    ]
    assert request.parent_work_item_ids == ("work-start", "work-middle", "work-end")


def test_local_ffmpeg_renderer_normalizes_sample_aspect_ratio_and_segment_duration(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        (tmp_path / "output.mp4").write_bytes(b"rendered")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("v2.backend.app.delivery.renderer.subprocess.run", fake_run)
    request = LocalRenderRequest(
        ffmpeg_path=tmp_path / "ffmpeg.exe",
        inputs=(
            LocalRenderInput(tmp_path / "wide.mp4", 0, 1000),
            LocalRenderInput(tmp_path / "vertical.mp4", 250, 1250),
        ),
        output_path=tmp_path / "output.mp4",
        width=480,
        height=848,
        fps=24,
        video_encoder="libx264",
        preset="medium",
        crf=18,
    )

    LocalFFmpegRenderer().render(request)

    filter_graph = captured["command"][captured["command"].index("-filter_complex") + 1]
    assert filter_graph.count("setsar=1") == 2
    assert filter_graph.count("tpad=stop_mode=clone") == 2
    assert filter_graph.count("trim=duration=1.000") == 2
    assert "concat=n=2:v=1:a=0[outv]" in filter_graph
    assert "-n" in captured["command"]


def test_local_ffmpeg_renderer_mixes_timeline_audio(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_run(command, **_kwargs):
        captured["command"] = command
        (tmp_path / "mixed.mp4").write_bytes(b"rendered")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("v2.backend.app.delivery.renderer.subprocess.run", fake_run)
    request = LocalRenderRequest(
        ffmpeg_path=tmp_path / "ffmpeg.exe",
        inputs=(LocalRenderInput(tmp_path / "video.mp4", 0, 2000, transition_in_ms=300, transition_out_ms=400),),
        audio_inputs=(LocalRenderAudioInput(
            tmp_path / "voice.wav",
            100,
            1600,
            250,
            volume_envelope=((0, -6.0), (750, 0.0), (1500, -3.0)),
        ),),
        output_path=tmp_path / "mixed.mp4",
        width=480,
        height=848,
        fps=24,
        video_encoder="libx264",
        preset="medium",
        crf=18,
    )

    LocalFFmpegRenderer().render(request)

    command = captured["command"]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "atrim=start=0.100:end=1.600" in filter_graph
    assert "fade=t=in:st=0:d=0.300" in filter_graph
    assert "fade=t=out:st=1.600:d=0.400" in filter_graph
    assert "volume=eval=frame" in filter_graph
    assert "between(t,0.000,0.750)" in filter_graph
    assert "adelay=250:all=1[a0]" in filter_graph
    assert "amix=inputs=1:duration=longest" in filter_graph
    assert command[command.index("-map") + 1] == "[outv]"
    assert "[outa]" in command
    assert "-c:a" in command


def test_local_ffmpeg_renderer_burns_in_one_frozen_subtitle(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs.get("cwd")
        (tmp_path / "subtitled.mp4").write_bytes(b"rendered")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("v2.backend.app.delivery.renderer.subprocess.run", fake_run)
    request = LocalRenderRequest(
        ffmpeg_path=tmp_path / "ffmpeg.exe",
        inputs=(LocalRenderInput(tmp_path / "video.mp4", 0, 2000),),
        subtitle_input=LocalRenderSubtitleInput(tmp_path / "frozen subtitles.srt"),
        output_path=tmp_path / "subtitled.mp4",
        width=480,
        height=848,
        fps=24,
        video_encoder="libx264",
        preset="medium",
        crf=18,
    )

    LocalFFmpegRenderer().render(request)

    command = captured["command"]
    filter_graph = command[command.index("-filter_complex") + 1]
    assert "[outv]subtitles=filename='" in filter_graph
    assert "frozen subtitles.srt" in filter_graph
    assert "force_style='Alignment=2,MarginV=48,Outline=2,Shadow=0'[outvs]" in filter_graph
    assert command[command.index("-map") + 1] == "[outvs]"
    assert captured["cwd"] == tmp_path.resolve()
