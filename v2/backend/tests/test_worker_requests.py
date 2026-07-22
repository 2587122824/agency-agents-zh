from types import SimpleNamespace

from v2.backend.app.workers import worker


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
