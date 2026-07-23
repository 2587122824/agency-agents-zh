from __future__ import annotations

import argparse
import socket
import time
from datetime import timedelta

from sqlalchemy.orm import Session

from ..db.models import (
    DeliveryAttempt,
    ProductionSnapshot,
    Project,
    ProjectEvent,
    WorkAttempt,
    WorkItem,
    utc_now,
)
from ..delivery.renderer import LocalFFmpegRenderer, LocalRenderError
from ..delivery.service import (
    block_local_render,
    prepare_local_render,
    register_local_render_output,
)
from ..db.session import SessionLocal
from ..orchestration.project_transitions import (
    ProjectStateConflictError,
    ProjectStateTrigger,
    block_project,
    transition_project,
)
from ..providers import (
    ExternalProviderAdapter,
    ProviderAdapterError,
    ProviderAdapterRegistry,
    ProviderExecutionRequest,
    default_provider_registry,
)
from ..repositories import (
    SqlAlchemyEventRepository,
    SqlAlchemyWorkRepository,
    WorkRepository,
)


LEASE_SECONDS = 60


def _work(session: Session) -> WorkRepository:
    return SqlAlchemyWorkRepository(session)


def _event(session: Session, event: ProjectEvent) -> None:
    SqlAlchemyEventRepository(session).add(event)


def _required_parent_items(session: Session, item: WorkItem) -> list[WorkItem]:
    return _work(session).required_parent_items(item)


def _update_aggregate_state(
    session: Session,
    project: Project,
    snapshot: ProductionSnapshot,
    actor_id: str,
    current_item: WorkItem | None = None,
    current_attempt: WorkAttempt | None = None,
) -> None:
    states = _work(session).snapshot_work_states(snapshot.id)
    if states and all(state == "completed" for state in states):
        snapshot.status = "execution_completed"
        transition_project(
            session,
            project,
            ProjectStateTrigger.PRODUCTION_SETTLED,
            actor_type="system",
            actor_id=actor_id,
            event_data={"snapshot_id": snapshot.id},
        )
    elif "blocked" in states:
        snapshot.status = "execution_blocked"
        current_failed = current_attempt is not None and current_attempt.state == "blocked"
        block_project(
            session,
            project,
            reason_code=(
                current_attempt.error_code
                if current_failed and current_attempt.error_code
                else "SNAPSHOT_EXECUTION_BLOCKED"
            ),
            responsible_aggregate_type="work_item" if current_failed and current_item else "production_snapshot",
            responsible_aggregate_id=current_item.id if current_failed and current_item else snapshot.id,
            actor_type="system",
            actor_id=actor_id,
            event_data={"snapshot_id": snapshot.id},
        )
    else:
        transition_project(
            session,
            project,
            ProjectStateTrigger.PRODUCTION_PROGRESS,
            actor_type="system",
            actor_id=actor_id,
            event_data={"snapshot_id": snapshot.id},
        )


def _finish_blocked(
    session,
    item: WorkItem,
    attempt: WorkAttempt,
    code: str,
    detail: str,
    response_manifest: dict | None = None,
) -> None:
    now = utc_now()
    item.status = "blocked"
    item.error = f"{code}: {detail}"
    item.finished_at = now
    item.row_version += 1
    attempt.state = "blocked"
    attempt.error_code = code
    attempt.error_detail = detail
    if response_manifest is not None:
        attempt.response_manifest = response_manifest
    attempt.finished_at = now
    attempt.execution_lock_owner = None
    attempt.execution_lock_expires_at = None


def _finish_completed(session, item: WorkItem, attempt: WorkAttempt, response: dict) -> None:
    now = utc_now()
    item.status = "completed"
    item.error = None
    item.finished_at = now
    item.row_version += 1
    attempt.state = "completed"
    attempt.response_manifest = response
    attempt.finished_at = now
    attempt.execution_lock_owner = None
    attempt.execution_lock_expires_at = None


def _provider_request(
    session: Session,
    item: WorkItem,
    attempt: WorkAttempt,
    parents: list[WorkItem],
) -> ProviderExecutionRequest:
    outputs: list[dict] = []
    repository = _work(session)
    input_slots = repository.required_parent_input_slots(item)
    snapshot = repository.snapshot(item.snapshot_id)
    approval_assets = {
        record["dag_node_id"]: record
        for record in (snapshot.image_phase_approval_manifest or {}).get("assets", [])
        if isinstance(record, dict) and record.get("dag_node_id")
    } if snapshot else {}
    for parent in parents:
        approved_record = approval_assets.get(parent.dag_node_id)
        if parent.kind == "generate_keyframe":
            if not approved_record:
                raise ProviderAdapterError(
                    "IMAGE_PHASE_APPROVAL_MISSING",
                    "视频步骤的关键帧父节点尚未包含在已确认图片清单中。",
                )
            asset = repository.asset(str(approved_record.get("asset_id")))
            if (
                not asset
                or asset.snapshot_id != item.snapshot_id
                or asset.dag_node_id != parent.dag_node_id
                or asset.state not in {"approved", "used"}
                or asset.content_hash != approved_record.get("content_hash")
            ):
                raise ProviderAdapterError(
                    "IMAGE_PHASE_APPROVED_ASSET_INVALID",
                    "已确认关键帧素材的状态、归属或内容哈希发生变化。",
                )
            outputs.append({
                **asset.provider_output_manifest,
                "input_slot": input_slots.get(parent.dag_node_id),
            })
            continue
        parent_attempt = repository.attempt(parent.current_attempt_id)
        parent_outputs = (parent_attempt.response_manifest or {}).get("outputs") if parent_attempt else None
        if isinstance(parent_outputs, list):
            outputs.extend(
                {**output, "input_slot": input_slots.get(parent.dag_node_id)}
                for output in parent_outputs
                if isinstance(output, dict)
            )
    return ProviderExecutionRequest(
        work_kind=item.kind,
        request_fingerprint=attempt.request_fingerprint,
        request_manifest=attempt.request_manifest,
        parent_work_item_ids=tuple(parent.id for parent in parents),
        parent_outputs=tuple(outputs),
    )


def _record_terminal_event(
    session: Session,
    owner: str,
    project: Project,
    snapshot: ProductionSnapshot,
    item: WorkItem,
    attempt: WorkAttempt,
) -> None:
    _update_aggregate_state(session, project, snapshot, owner, item, attempt)
    _event(session, ProjectEvent(
        project_id=project.id,
        snapshot_id=snapshot.id,
        event_type="production.work_finished.v1",
        aggregate_type="work_attempt",
        aggregate_id=attempt.id,
        actor_type="worker",
        actor_id=owner,
        message="Production work item reached a terminal state.",
        data={
            "snapshot_id": snapshot.id,
            "work_item_id": item.id,
            "dag_node_id": item.dag_node_id,
            "status": item.status,
            "error_code": attempt.error_code,
        },
    ))


def process_one(
    worker_id: str | None = None,
    adapter_registry: ProviderAdapterRegistry | None = None,
    delivery_renderer: LocalFFmpegRenderer | None = None,
) -> bool:
    owner = worker_id or f"v2-worker:{socket.gethostname()}"
    adapters = adapter_registry or default_provider_registry()
    renderer = delivery_renderer or LocalFFmpegRenderer()
    with SessionLocal() as session:
        repository = _work(session)
        now = utc_now()

        polling = repository.poll_candidates(now, limit=50)
        if polling:
            selected = polling[0]
            attempt = repository.attempt(selected.current_attempt_id)
            if not attempt or not repository.claim_attempt(attempt, owner, now + timedelta(seconds=LEASE_SECONDS), now):
                session.rollback()
                return False
            session.commit()

            item = repository.work_item(selected.id)
            attempt = repository.attempt(selected.current_attempt_id)
            project = repository.project(item.project_id) if item else None
            snapshot = repository.snapshot(item.snapshot_id) if item else None
            if not item or not attempt or not project or not snapshot:
                return False
            if attempt.state == "submitting" and not attempt.provider_task_id:
                _finish_blocked(
                    session,
                    item,
                    attempt,
                    "PROVIDER_SUBMISSION_RECONCILIATION_REQUIRED",
                    "A prior external submission may have occurred before its task ID was persisted.",
                )
            else:
                adapter = adapters.resolve(attempt.request_manifest.get("adapter_kind"), item.kind)
                if adapter is None or not isinstance(adapter, ExternalProviderAdapter):
                    _finish_blocked(
                        session,
                        item,
                        attempt,
                        "PROVIDER_ADAPTER_NOT_CONNECTED",
                        "The persisted external provider adapter is not connected.",
                    )
                else:
                    parents = _required_parent_items(session, item)
                    try:
                        request = _provider_request(session, item, attempt, parents)
                    except ProviderAdapterError as exc:
                        _finish_blocked(session, item, attempt, exc.code, exc.detail, exc.response_manifest)
                    else:
                        # Keep provider I/O outside a database transaction. The
                        # persisted lease remains the execution authority.
                        session.commit()
                        try:
                            result = adapter.poll(request, str(attempt.provider_task_id))
                        except ProviderAdapterError as exc:
                            _finish_blocked(session, item, attempt, exc.code, exc.detail, exc.response_manifest)
                        else:
                            attempt.response_manifest = result.response_manifest
                            attempt.execution_lock_owner = None
                            attempt.execution_lock_expires_at = None
                            if result.state == "running":
                                attempt.state = "submitted"
                                poll_seconds = int(attempt.request_manifest.get("provider", {}).get("poll_interval_seconds") or 10)
                                item.available_at = utc_now() + timedelta(seconds=poll_seconds)
                            elif result.state == "failed":
                                _finish_blocked(
                                    session,
                                    item,
                                    attempt,
                                    result.error_code or "PROVIDER_TASK_FAILED",
                                    result.error_detail or "The provider task failed.",
                                )
                            else:
                                _finish_completed(session, item, attempt, result.response_manifest)
            if item.status in {"completed", "blocked"}:
                _record_terminal_event(session, owner, project, snapshot, item, attempt)
            session.commit()
            return True

        candidates = repository.lease_candidates(now, limit=50)
        selected: WorkItem | None = None
        for item in candidates:
            candidate_snapshot = repository.snapshot(item.snapshot_id) if item.snapshot_id else None
            if (
                candidate_snapshot
                and candidate_snapshot.image_phase_required
                and not candidate_snapshot.image_phase_approved_at
                and item.kind not in {"generate_keyframe", "render_delivery"}
            ):
                continue
            parents = _required_parent_items(session, item)
            if any(parent.status == "blocked" for parent in parents):
                attempt = repository.attempt(item.current_attempt_id)
                if attempt and attempt.state == "created":
                    _finish_blocked(session, item, attempt, "DEPENDENCY_BLOCKED", "A required parent work item is blocked.")
                    project = repository.project(item.project_id)
                    snapshot = repository.snapshot(item.snapshot_id)
                    if project and snapshot:
                        _update_aggregate_state(session, project, snapshot, owner, item, attempt)
                    session.commit()
                    return True
            if all(parent.status == "completed" for parent in parents):
                selected = item
                break
        if not selected:
            return False

        # Compatibility for the pre-snapshot V2 contract check. It is local-only
        # and cannot create provider work or become part of a production DAG.
        if selected.kind == "contract_validation" and not selected.snapshot_id:
            project = repository.project(selected.project_id)
            now = utc_now()
            selected.started_at = now
            selected.finished_at = now
            selected.status = "completed"
            selected.row_version += 1
            if project:
                try:
                    transition_project(
                        session,
                        project,
                        ProjectStateTrigger.LEGACY_VALIDATION_COMPLETED,
                        actor_type="system",
                        actor_id=owner,
                        event_data={"work_item_id": selected.id},
                    )
                except ProjectStateConflictError as exc:
                    selected.status = "blocked"
                    selected.error = f"LEGACY_PROJECT_STATE_INVALID: {exc}"
                    _event(session, ProjectEvent(
                        project_id=project.id,
                        event_type="work.blocked.v1",
                        aggregate_type="work_item",
                        aggregate_id=selected.id,
                        actor_type="worker",
                        actor_id=owner,
                        message="Legacy validation could not apply its declared project-state transition.",
                        data={"work_item_id": selected.id, "error_code": "LEGACY_PROJECT_STATE_INVALID"},
                    ))
                    session.commit()
                    return True
                _event(session, ProjectEvent(
                    project_id=project.id,
                    event_type="contract.validated.v1",
                    aggregate_type="work_item",
                    aggregate_id=selected.id,
                    actor_type="worker",
                    actor_id=owner,
                    message="Legacy local contract validation completed.",
                    data={"work_item_id": selected.id},
                ))
            session.commit()
            return True

        attempt = repository.attempt(selected.current_attempt_id)
        if not attempt or attempt.state != "created" or attempt.request_fingerprint != selected.request_fingerprint:
            return False
        selected_manifest = attempt.request_manifest or {}
        selected_adapter = adapters.resolve(selected_manifest.get("adapter_kind"), selected.kind)
        provider = selected_manifest.get("provider") or {}
        max_concurrency = provider.get("max_concurrency")
        if isinstance(selected_adapter, ExternalProviderAdapter) and isinstance(max_concurrency, int):
            claimed = repository.claim_with_provider_capacity(
                selected,
                now,
                str(selected_manifest.get("provider_key") or attempt.provider),
                max_concurrency,
            )
        else:
            claimed = repository.claim(selected, now)
        if not claimed:
            session.rollback()
            return False
        attempt.state = "running"
        attempt.started_at = now
        attempt.execution_lock_owner = owner
        attempt.execution_lock_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        session.commit()

        item = repository.work_item(selected.id)
        attempt = repository.attempt(selected.current_attempt_id)
        project = repository.project(item.project_id) if item else None
        snapshot = repository.snapshot(item.snapshot_id) if item and item.snapshot_id else None
        if not item or not attempt:
            return False

        if item.kind == "render_delivery":
            delivery_id = str((item.payload or {}).get("delivery_attempt_id") or "")
            delivery_attempt = session.get(DeliveryAttempt, delivery_id)
            if not delivery_attempt:
                _finish_blocked(
                    session,
                    item,
                    attempt,
                    "LOCAL_RENDER_AUTHORITY_MISSING",
                    "The persisted delivery attempt no longer exists.",
                )
                session.commit()
                return True
            snapshot = repository.snapshot(delivery_attempt.snapshot_id)
            if not project or not snapshot:
                _finish_blocked(
                    session,
                    item,
                    attempt,
                    "EXECUTION_AUTHORITY_MISSING",
                    "Project or delivery snapshot no longer exists.",
                )
                session.commit()
                return True
            try:
                delivery_attempt, render_request = prepare_local_render(
                    session,
                    project,
                    item,
                    attempt,
                )
            except LocalRenderError as exc:
                _finish_blocked(session, item, attempt, exc.code, exc.detail, exc.evidence)
                block_local_render(session, project, delivery_attempt, exc, actor_id=owner)
                session.commit()
                return True
            session.commit()
            try:
                render_result = renderer.render(render_request)
                asset = register_local_render_output(
                    session,
                    project,
                    delivery_attempt,
                    attempt,
                    render_request,
                    render_result,
                )
            except LocalRenderError as exc:
                render_request.output_path.unlink(missing_ok=True)
                _finish_blocked(session, item, attempt, exc.code, exc.detail, exc.evidence)
                block_local_render(session, project, delivery_attempt, exc, actor_id=owner)
            else:
                _finish_completed(
                    session,
                    item,
                    attempt,
                    {
                        "schema_version": "v2.delivery-render-response.v1",
                        "delivery_attempt_id": delivery_attempt.id,
                        "asset_id": asset.id,
                        "request_fingerprint": delivery_attempt.request_fingerprint,
                    },
                )
                _event(session, ProjectEvent(
                    project_id=project.id,
                    snapshot_id=snapshot.id,
                    event_type="delivery.work_finished.v1",
                    aggregate_type="work_attempt",
                    aggregate_id=attempt.id,
                    actor_type="worker",
                    actor_id=owner,
                    message="Local delivery render work reached a terminal state.",
                    data={
                        "delivery_attempt_id": delivery_attempt.id,
                        "work_item_id": item.id,
                        "status": item.status,
                    },
                ))
            session.commit()
            return True

        if not project or not snapshot:
            _finish_blocked(session, item, attempt, "EXECUTION_AUTHORITY_MISSING", "Project or snapshot no longer exists.")
            session.commit()
            return True

        manifest = attempt.request_manifest
        adapter = adapters.resolve(manifest.get("adapter_kind"), item.kind)
        if adapter is None:
            _finish_blocked(
                session,
                item,
                attempt,
                "PROVIDER_ADAPTER_NOT_CONNECTED",
                f"Adapter {manifest.get('adapter_kind')!r} is not connected to the V2 worker.",
            )
        else:
            parents = _required_parent_items(session, item)
            try:
                request = _provider_request(session, item, attempt, parents)
            except ProviderAdapterError as exc:
                _finish_blocked(session, item, attempt, exc.code, exc.detail, exc.response_manifest)
            else:
                if isinstance(adapter, ExternalProviderAdapter):
                    attempt.state = "submitting"
                    session.commit()
                    try:
                        submission = adapter.submit(request)
                    except ProviderAdapterError as exc:
                        _finish_blocked(session, item, attempt, exc.code, exc.detail, exc.response_manifest)
                    else:
                        attempt.provider_task_id = submission.provider_task_id
                        attempt.response_manifest = submission.response_manifest
                        attempt.state = "submitted"
                        attempt.submitted_at = utc_now()
                        attempt.execution_lock_owner = None
                        attempt.execution_lock_expires_at = None
                        poll_seconds = int(manifest.get("provider", {}).get("poll_interval_seconds") or 10)
                        item.available_at = utc_now() + timedelta(seconds=poll_seconds)
                else:
                    try:
                        response = adapter.execute(request)
                    except ProviderAdapterError as exc:
                        _finish_blocked(session, item, attempt, exc.code, exc.detail, exc.response_manifest)
                    else:
                        _finish_completed(session, item, attempt, response)
        repository.flush()
        if item.status in {"completed", "blocked"}:
            _record_terminal_event(session, owner, project, snapshot, item, attempt)
        session.commit()
        return True


def run(poll_seconds: float) -> None:
    while True:
        if not process_one():
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agency Studio V2 worker")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once:
        process_one()
        return
    run(args.poll_seconds)


if __name__ == "__main__":
    main()
