from types import SimpleNamespace

from v2.backend.app.workers import worker
from v2.backend.app.delivery.renderer import (
    LocalFFmpegRenderer,
    LocalRenderInput,
    LocalRenderRequest,
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
