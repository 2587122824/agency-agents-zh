from types import SimpleNamespace

from v2.backend.app.workers import worker


class FakeRepository:
    def __init__(self, attempts, input_slots):
        self.attempts = attempts
        self.input_slots = input_slots

    def attempt(self, attempt_id):
        return self.attempts.get(attempt_id)

    def required_parent_input_slots(self, _item):
        return self.input_slots


def test_provider_request_preserves_dependency_input_slots(monkeypatch) -> None:
    parents = [
        SimpleNamespace(id=f"work-{role}", dag_node_id=f"node-{role}", current_attempt_id=f"attempt-{role}")
        for role in ("start", "middle", "end")
    ]
    input_slots = {
        f"node-{role}": f"source_image.{role}"
        for role in ("start", "middle", "end")
    }
    attempts = {
        f"attempt-{role}": SimpleNamespace(response_manifest={"outputs": [{"asset_type": "image", "uri": f"runtime://{role}.png"}]})
        for role in ("start", "middle", "end")
    }
    monkeypatch.setattr(worker, "_work", lambda _session: FakeRepository(attempts, input_slots))

    request = worker._provider_request(
        object(),
        SimpleNamespace(kind="generate_three_frame_i2v_clip", dag_node_id="node-video"),
        SimpleNamespace(request_fingerprint="f" * 64, request_manifest={"contract": "frozen"}),
        parents,
    )

    assert [item["input_slot"] for item in request.parent_outputs] == [
        "source_image.start", "source_image.middle", "source_image.end",
    ]
    assert request.parent_work_item_ids == ("work-start", "work-middle", "work-end")
