"""Metric thresholds and out-of-bounds alerts.

Alerts are produced at two moments:

1. When a metric is recorded (``check_metric_against_thresholds`` — called from
   ``cqrs.record_metric`` inside the same transaction).
2. When a threshold is created or tightened (``upsert_threshold`` re-scans all
   recorded metrics of that name so historical violations surface immediately).

Dedup: an alert is unique per (run_id, metric_name, step, bound), so re-scans
never duplicate rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import DomainError
from app.models import MetricAlert, MetricThreshold, RunProjection


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _violated_bounds(threshold: MetricThreshold, value: float) -> list[tuple[str, float]]:
    violated: list[tuple[str, float]] = []
    if threshold.lower_bound is not None and value < threshold.lower_bound:
        violated.append(("lower", threshold.lower_bound))
    if threshold.upper_bound is not None and value > threshold.upper_bound:
        violated.append(("upper", threshold.upper_bound))
    return violated


def _alert_exists(db: Session, *, run_id: UUID, metric_name: str, step: int, bound: str) -> bool:
    stmt = select(MetricAlert.id).where(
        MetricAlert.run_id == run_id,
        MetricAlert.metric_name == metric_name,
        MetricAlert.step == step,
        MetricAlert.bound == bound,
    )
    return db.scalar(stmt) is not None


def _create_alert(
    db: Session,
    *,
    run_id: UUID,
    metric_name: str,
    value: float,
    step: int,
    bound: str,
    threshold_value: float,
    actor: str,
) -> MetricAlert | None:
    if _alert_exists(db, run_id=run_id, metric_name=metric_name, step=step, bound=bound):
        return None
    alert = MetricAlert(
        id=uuid4(),
        run_id=run_id,
        metric_name=metric_name,
        value=value,
        step=step,
        bound=bound,
        threshold_value=threshold_value,
        actor=actor,
        occurred_at=_now(),
    )
    db.add(alert)
    db.flush()
    return alert


def check_metric_against_thresholds(
    db: Session,
    *,
    run_id: UUID,
    actor: str,
    name: str,
    value: float,
    step: int,
) -> list[MetricAlert]:
    """Evaluate one freshly recorded metric point against its threshold (if any).

    Caller is responsible for committing; alerts share the metric's transaction.
    """
    threshold = db.scalar(
        select(MetricThreshold).where(MetricThreshold.metric_name == name)
    )
    if threshold is None:
        return []
    alerts: list[MetricAlert] = []
    for bound, threshold_value in _violated_bounds(threshold, value):
        alert = _create_alert(
            db,
            run_id=run_id,
            metric_name=name,
            value=value,
            step=step,
            bound=bound,
            threshold_value=threshold_value,
            actor=actor,
        )
        if alert is not None:
            alerts.append(alert)
    return alerts


def _rescan_threshold(db: Session, threshold: MetricThreshold) -> int:
    """Re-check every recorded metric with this name; create alerts for violations."""
    created = 0
    runs = db.scalars(select(RunProjection)).all()
    for run in runs:
        for metric in run.metrics_json or []:
            if metric.get("name") != threshold.metric_name:
                continue
            value = metric.get("value")
            if value is None:
                continue
            for bound, threshold_value in _violated_bounds(threshold, float(value)):
                alert = _create_alert(
                    db,
                    run_id=run.id,
                    metric_name=threshold.metric_name,
                    value=float(value),
                    step=int(metric.get("step", 0)),
                    bound=bound,
                    threshold_value=threshold_value,
                    actor=metric.get("actor") or threshold.updated_by,
                )
                if alert is not None:
                    created += 1
    return created


def list_thresholds(db: Session) -> list[MetricThreshold]:
    stmt = select(MetricThreshold).order_by(MetricThreshold.metric_name.asc())
    return list(db.scalars(stmt).all())


def upsert_threshold(
    db: Session,
    *,
    actor: str,
    metric_name: str,
    lower_bound: float | None,
    upper_bound: float | None,
) -> MetricThreshold:
    if lower_bound is None and upper_bound is None:
        raise DomainError("至少需设置上限或下限之一")
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
    _rescan_threshold(db, threshold)
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
    db.commit()


def list_alerts(db: Session, *, run_id: UUID | None = None) -> list[dict[str, Any]]:
    stmt = (
        select(MetricAlert, RunProjection)
        .join(RunProjection, RunProjection.id == MetricAlert.run_id, isouter=True)
        .order_by(MetricAlert.occurred_at.desc())
    )
    if run_id is not None:
        stmt = stmt.where(MetricAlert.run_id == run_id)
    rows = db.execute(stmt).all()
    return [
        {
            "id": alert.id,
            "run_id": alert.run_id,
            "run_name": proj.name if proj else None,
            "project": proj.project if proj else None,
            "metric_name": alert.metric_name,
            "value": alert.value,
            "step": alert.step,
            "bound": alert.bound,
            "threshold_value": alert.threshold_value,
            "actor": alert.actor,
            "occurred_at": alert.occurred_at,
        }
        for alert, proj in rows
    ]
