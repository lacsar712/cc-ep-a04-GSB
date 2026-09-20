from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import EventStore, MetricAlert, MetricThreshold, RunProjection


TERMINAL_STATUSES = {"completed", "aborted"}


class DomainError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class ConflictError(DomainError):
    def __init__(self, message: str = "版本冲突或终态不可变更"):
        super().__init__(message, status_code=409)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _append_event(
    db: Session,
    *,
    aggregate_id: UUID,
    version: int,
    event_type: str,
    payload: dict[str, Any],
    actor: str,
) -> EventStore:
    event = EventStore(
        id=uuid4(),
        aggregate_id=aggregate_id,
        version=version,
        event_type=event_type,
        payload_json=payload,
        occurred_at=_now(),
        actor=actor,
    )
    db.add(event)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError("乐观锁冲突：expected_version 与当前 version 不一致") from exc
    except Exception:
        db.rollback()
        raise
    return event


def _apply_event_to_projection(proj: RunProjection | None, event: EventStore) -> RunProjection:
    payload = event.payload_json
    if event.event_type == "RunStarted":
        return RunProjection(
            id=event.aggregate_id,
            project=payload["project"],
            name=payload["name"],
            status="running",
            version=event.version,
            dataset_content_sha256=payload["dataset_content_sha256"].lower(),
            code_commit_sha=payload["code_commit_sha"].lower(),
            description=payload.get("description"),
            started_at=event.occurred_at,
            finished_at=None,
            started_by=event.actor,
            metrics_json=[],
            artifacts_json=[],
            result_summary=None,
            abort_reason=None,
        )

    if proj is None:
        raise DomainError("投影不存在，无法应用事件")

    if event.event_type == "MetricRecorded":
        metrics = list(proj.metrics_json or [])
        metrics.append(
            {
                "name": payload["name"],
                "value": payload["value"],
                "step": payload["step"],
                "recorded_at": event.occurred_at.isoformat(),
                "actor": event.actor,
            }
        )
        proj.metrics_json = metrics
    elif event.event_type == "ArtifactAttached":
        artifacts = list(proj.artifacts_json or [])
        artifacts.append(
            {
                "name": payload["name"],
                "uri": payload["uri"],
                "content_sha256": payload["content_sha256"].lower(),
                "media_type": payload.get("media_type"),
                "attached_at": event.occurred_at.isoformat(),
                "actor": event.actor,
            }
        )
        proj.artifacts_json = artifacts
    elif event.event_type == "RunCompleted":
        proj.status = "completed"
        proj.result_summary = payload["result_summary"]
        proj.finished_at = event.occurred_at
    elif event.event_type == "RunAborted":
        proj.status = "aborted"
        proj.abort_reason = payload["reason"]
        proj.finished_at = event.occurred_at
    else:
        raise DomainError(f"未知事件类型: {event.event_type}")

    proj.version = event.version
    return proj


def _get_projection(db: Session, run_id: UUID) -> RunProjection | None:
    return db.get(RunProjection, run_id)


def _violation_direction(threshold: MetricThreshold, value: float) -> str | None:
    if threshold.lower_bound is not None and value < threshold.lower_bound:
        return "below"
    if threshold.upper_bound is not None and value > threshold.upper_bound:
        return "above"
    return None


def _add_alert(
    db: Session,
    *,
    run_id: UUID,
    metric_name: str,
    value: float,
    step: int,
    threshold: MetricThreshold,
    direction: str,
) -> MetricAlert:
    alert = MetricAlert(
        id=uuid4(),
        run_id=run_id,
        metric_name=metric_name,
        value=value,
        step=step,
        lower_bound=threshold.lower_bound,
        upper_bound=threshold.upper_bound,
        direction=direction,
        created_at=_now(),
    )
    db.add(alert)
    return alert


def _check_threshold_and_alert(
    db: Session,
    *,
    run_id: UUID,
    metric_name: str,
    value: float,
    step: int,
) -> None:
    threshold = db.scalar(
        select(MetricThreshold).where(MetricThreshold.metric_name == metric_name)
    )
    if threshold is None:
        return
    direction = _violation_direction(threshold, value)
    if direction:
        _add_alert(
            db,
            run_id=run_id,
            metric_name=metric_name,
            value=value,
            step=step,
            threshold=threshold,
            direction=direction,
        )


def _require_running(proj: RunProjection | None) -> RunProjection:
    if proj is None:
        raise DomainError("Run 不存在", status_code=404)
    if proj.status in TERMINAL_STATUSES:
        raise ConflictError("Run 已处于终态，不可再接受命令")
    if proj.status != "running":
        raise DomainError(f"当前状态 {proj.status} 不允许该命令")
    return proj


def _check_expected_version(proj: RunProjection | None, expected_version: int) -> None:
    current = 0 if proj is None else proj.version
    if expected_version != current:
        raise ConflictError(
            f"乐观锁冲突：expected_version={expected_version}, current_version={current}"
        )


def start_run(
    db: Session,
    *,
    actor: str,
    project: str,
    name: str,
    dataset_content_sha256: str,
    code_commit_sha: str,
    description: str | None,
    expected_version: int = 0,
    run_id: UUID | None = None,
) -> RunProjection:
    if expected_version != 0:
        raise ConflictError("新建 Run 的 expected_version 必须为 0")

    aggregate_id = run_id or uuid4()
    if _get_projection(db, aggregate_id) is not None:
        raise ConflictError("Run 已存在")

    event = _append_event(
        db,
        aggregate_id=aggregate_id,
        version=1,
        event_type="RunStarted",
        payload={
            "project": project,
            "name": name,
            "dataset_content_sha256": dataset_content_sha256.lower(),
            "code_commit_sha": code_commit_sha.lower(),
            "description": description,
        },
        actor=actor,
    )
    proj = _apply_event_to_projection(None, event)
    db.add(proj)
    db.commit()
    db.refresh(proj)
    return proj


def record_metric(
    db: Session,
    *,
    run_id: UUID,
    actor: str,
    name: str,
    value: float,
    step: int,
    expected_version: int,
) -> RunProjection:
    proj = _get_projection(db, run_id)
    _require_running(proj)
    _check_expected_version(proj, expected_version)

    event = _append_event(
        db,
        aggregate_id=run_id,
        version=expected_version + 1,
        event_type="MetricRecorded",
        payload={"name": name, "value": value, "step": step},
        actor=actor,
    )
    proj = _apply_event_to_projection(proj, event)
    _check_threshold_and_alert(db, run_id=run_id, metric_name=name, value=value, step=step)
    db.commit()
    db.refresh(proj)
    return proj


def attach_artifact(
    db: Session,
    *,
    run_id: UUID,
    actor: str,
    name: str,
    uri: str,
    content_sha256: str,
    media_type: str | None,
    expected_version: int,
) -> RunProjection:
    proj = _get_projection(db, run_id)
    _require_running(proj)
    _check_expected_version(proj, expected_version)

    event = _append_event(
        db,
        aggregate_id=run_id,
        version=expected_version + 1,
        event_type="ArtifactAttached",
        payload={
            "name": name,
            "uri": uri,
            "content_sha256": content_sha256.lower(),
            "media_type": media_type,
        },
        actor=actor,
    )
    proj = _apply_event_to_projection(proj, event)
    db.commit()
    db.refresh(proj)
    return proj


def complete_run(
    db: Session,
    *,
    run_id: UUID,
    actor: str,
    result_summary: str,
    expected_version: int,
) -> RunProjection:
    proj = _get_projection(db, run_id)
    _require_running(proj)
    _check_expected_version(proj, expected_version)

    event = _append_event(
        db,
        aggregate_id=run_id,
        version=expected_version + 1,
        event_type="RunCompleted",
        payload={"result_summary": result_summary},
        actor=actor,
    )
    proj = _apply_event_to_projection(proj, event)
    db.commit()
    db.refresh(proj)
    return proj


def abort_run(
    db: Session,
    *,
    run_id: UUID,
    actor: str,
    reason: str,
    expected_version: int,
) -> RunProjection:
    proj = _get_projection(db, run_id)
    _require_running(proj)
    _check_expected_version(proj, expected_version)

    event = _append_event(
        db,
        aggregate_id=run_id,
        version=expected_version + 1,
        event_type="RunAborted",
        payload={"reason": reason},
        actor=actor,
    )
    proj = _apply_event_to_projection(proj, event)
    db.commit()
    db.refresh(proj)
    return proj


def list_events(db: Session, run_id: UUID) -> list[EventStore]:
    stmt = (
        select(EventStore)
        .where(EventStore.aggregate_id == run_id)
        .order_by(EventStore.version.asc())
    )
    return list(db.scalars(stmt).all())


def _regenerate_alerts_for_metric(
    db: Session, metric_name: str, threshold: MetricThreshold
) -> None:
    """Alerts are a derived projection of (current thresholds x recorded metrics):
    rebuild this metric's alerts from all run projections."""
    db.execute(delete(MetricAlert).where(MetricAlert.metric_name == metric_name))
    runs = db.scalars(select(RunProjection)).all()
    for run in runs:
        for entry in run.metrics_json or []:
            if entry.get("name") != metric_name:
                continue
            direction = _violation_direction(threshold, entry["value"])
            if direction:
                _add_alert(
                    db,
                    run_id=run.id,
                    metric_name=metric_name,
                    value=entry["value"],
                    step=entry["step"],
                    threshold=threshold,
                    direction=direction,
                )


def upsert_threshold(
    db: Session,
    *,
    metric_name: str,
    lower_bound: float | None,
    upper_bound: float | None,
    actor: str,
) -> MetricThreshold:
    if lower_bound is None and upper_bound is None:
        raise DomainError("上下限至少填写一项")
    if lower_bound is not None and upper_bound is not None and lower_bound > upper_bound:
        raise DomainError("下限不能大于上限")

    threshold = db.scalar(
        select(MetricThreshold).where(MetricThreshold.metric_name == metric_name)
    )
    if threshold is None:
        threshold = MetricThreshold(
            id=uuid4(),
            metric_name=metric_name,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
            updated_by=actor,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(threshold)
    else:
        threshold.lower_bound = lower_bound
        threshold.upper_bound = upper_bound
        threshold.updated_by = actor
        threshold.updated_at = _now()
    db.flush()
    _regenerate_alerts_for_metric(db, metric_name, threshold)
    db.commit()
    db.refresh(threshold)
    return threshold


def delete_threshold(db: Session, *, metric_name: str) -> None:
    threshold = db.scalar(
        select(MetricThreshold).where(MetricThreshold.metric_name == metric_name)
    )
    if threshold is None:
        raise DomainError("阈值不存在", status_code=404)
    db.delete(threshold)
    db.execute(delete(MetricAlert).where(MetricAlert.metric_name == metric_name))
    db.commit()


def list_thresholds(db: Session) -> list[MetricThreshold]:
    stmt = select(MetricThreshold).order_by(MetricThreshold.metric_name.asc())
    return list(db.scalars(stmt).all())


def list_alerts(
    db: Session,
    *,
    run_id: UUID | None = None,
    metric_name: str | None = None,
) -> list[tuple[MetricAlert, RunProjection]]:
    stmt = (
        select(MetricAlert, RunProjection)
        .join(RunProjection, MetricAlert.run_id == RunProjection.id)
        .order_by(MetricAlert.created_at.desc())
    )
    if run_id is not None:
        stmt = stmt.where(MetricAlert.run_id == run_id)
    if metric_name:
        stmt = stmt.where(MetricAlert.metric_name == metric_name)
    return list(db.execute(stmt).all())


def rebuild_projection_from_events(db: Session, run_id: UUID) -> RunProjection | None:
    events = list_events(db, run_id)
    if not events:
        return None
    proj: RunProjection | None = None
    for event in events:
        proj = _apply_event_to_projection(proj, event)
    return proj
