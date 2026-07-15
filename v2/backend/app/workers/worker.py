from __future__ import annotations

import argparse
import time

from sqlalchemy import select, update

from ..db.models import Project, ProjectEvent, WorkItem, utc_now
from ..db.session import SessionLocal, create_schema


def process_one() -> bool:
    with SessionLocal() as session:
        item = session.scalar(
            select(WorkItem).where(WorkItem.status == "queued").order_by(WorkItem.created_at).limit(1)
        )
        if not item:
            return False
        claimed = session.execute(
            update(WorkItem)
            .where(WorkItem.id == item.id, WorkItem.status == "queued")
            .values(status="in_progress", started_at=utc_now())
        )
        if claimed.rowcount != 1:
            session.rollback()
            return False
        session.commit()
        item = session.get(WorkItem, item.id)
        if not item:
            return False
        project = session.get(Project, item.project_id)
        if project:
            project.status = "in_progress"
        session.commit()

        if item.kind != "contract_validation":
            item.status = "blocked"
            item.error = f"No executor registered for work kind: {item.kind}"
            if project:
                project.status = "blocked"
        elif not project:
            item.status = "blocked"
            item.error = "Project no longer exists"
        else:
            item.status = "completed"
            project.status = "review_required"
            session.add(
                ProjectEvent(
                    project_id=project.id,
                    event_type="contract.validated",
                    message="结构化合同验证完成，等待人工审核",
                    data={"work_item_id": item.id},
                )
            )
        item.finished_at = utc_now()
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
