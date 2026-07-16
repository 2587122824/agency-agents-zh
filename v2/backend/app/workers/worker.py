from __future__ import annotations

import argparse
import socket
import time
from datetime import timedelta

from sqlalchemy.orm import Session

from ..db.models import (
    ProductionSnapshot,
    Project,
    ProjectEvent,
    WorkAttempt,
    WorkItem,
    utc_now,
)
from ..db.session import SessionLocal, create_schema
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


def _update_aggregate_state(session: Session, project: Project, snapshot: ProductionSnapshot) -> None:
    states = _work(session).snapshot_work_states(snapshot.id)
    if states and all(state == "completed" for state in states):
        snapshot.status = "execution_completed"
        project.status = "quality_review"
    elif "blocked" in states:
        snapshot.status = "execution_blocked"
        project.status = "blocked"
    else:
        project.status = "producing"


def _finish_blocked(session, item: WorkItem, attempt: WorkAttempt, code: str, detail: str) -> None:
    now = utc_now()
    item.status = "blocked"
    item.error = f"{code}: {detail}"
    item.finished_at = now
    item.row_version += 1
    attempt.state = "blocked"
    attempt.error_code = code
    attempt.error_detail = detail
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


def process_one(worker_id: str | None = None) -> bool:
    owner = worker_id or f"v2-worker:{socket.gethostname()}"
    with SessionLocal() as session:
        repository = _work(session)
        now = utc_now()
        candidates = repository.lease_candidates(now, limit=50)
        selected: WorkItem | None = None
        for item in candidates:
            parents = _required_parent_items(session, item)
            if any(parent.status == "blocked" for parent in parents):
                attempt = repository.attempt(item.current_attempt_id)
                if attempt and attempt.state == "created":
                    _finish_blocked(session, item, attempt, "DEPENDENCY_BLOCKED", "A required parent work item is blocked.")
                    project = repository.project(item.project_id)
                    snapshot = repository.snapshot(item.snapshot_id)
                    if project and snapshot:
                        _update_aggregate_state(session, project, snapshot)
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
                project.status = "review_required"
                _event(session, ProjectEvent(
                    project_id=project.id,
                    event_type="contract.validated",
                    message="Legacy local contract validation completed.",
                    data={"work_item_id": selected.id},
                ))
            session.commit()
            return True

        attempt = repository.attempt(selected.current_attempt_id)
        if not attempt or attempt.state != "created" or attempt.request_fingerprint != selected.request_fingerprint:
            return False
        if not repository.claim(selected, now):
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
        snapshot = repository.snapshot(item.snapshot_id) if item else None
        if not item or not attempt:
            return False
        if not project or not snapshot:
            _finish_blocked(session, item, attempt, "EXECUTION_AUTHORITY_MISSING", "Project or snapshot no longer exists.")
            session.commit()
            return True

        manifest = attempt.request_manifest
        if manifest.get("adapter_kind") == "mock":
            _finish_completed(session, item, attempt, {
                "schema_version": "mock-provider-response.v1",
                "result": "simulated",
                "request_fingerprint": attempt.request_fingerprint,
                "media_created": False,
                "provider_task_id": None,
            })
        elif item.kind == "assemble_timeline_contract" and manifest.get("adapter_kind") == "local":
            parents = _required_parent_items(session, item)
            _finish_completed(session, item, attempt, {
                "schema_version": "timeline-contract-result.v1",
                "result": "contract_assembled",
                "input_work_item_ids": [parent.id for parent in parents],
                "media_created": False,
            })
        else:
            _finish_blocked(
                session,
                item,
                attempt,
                "PROVIDER_ADAPTER_NOT_CONNECTED",
                f"Adapter {manifest.get('adapter_kind')!r} is not connected to the V2 worker.",
            )
        repository.flush()
        _update_aggregate_state(session, project, snapshot)
        _event(session, ProjectEvent(
            project_id=project.id,
            event_type="production.work_finished.v1",
            message="Production work item reached a terminal state.",
            data={
                "snapshot_id": snapshot.id,
                "work_item_id": item.id,
                "dag_node_id": item.dag_node_id,
                "status": item.status,
                "error_code": attempt.error_code,
            },
        ))
        session.commit()
        return True


def run(poll_seconds: float) -> None:
    create_schema()
    while True:
        if not process_one():
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agency Studio V2 worker")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    create_schema()
    if args.once:
        process_one()
        return
    run(args.poll_seconds)


if __name__ == "__main__":
    main()
