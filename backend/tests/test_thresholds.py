import hashlib

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.cqrs import record_metric, start_run
from app.database import Base
from app.errors import DomainError
from app.models import MetricAlert
from app.monitoring import (
    delete_threshold,
    list_alerts,
    list_thresholds,
    upsert_threshold,
)


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # JSONB not available on SQLite — remap via create_all with JSON
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(_type, compiler, **kw):
        return "JSON"

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def make_run(db, name="run-1"):
    return start_run(
        db,
        actor="researcher",
        project="proj",
        name=name,
        dataset_content_sha256=sha(f"ds-{name}"),
        code_commit_sha="abc1234",
        description=None,
    )


def alert_count(db) -> int:
    return len(list(db.scalars(select(MetricAlert)).all()))


def test_metric_within_bounds_no_alert(db):
    upsert_threshold(db, actor="researcher", metric_name="loss", lower_bound=None, upper_bound=1.0)
    run = make_run(db)
    record_metric(
        db, run_id=run.id, actor="researcher", name="loss", value=0.5, step=1,
        expected_version=run.version,
    )
    assert alert_count(db) == 0


def test_metric_above_upper_creates_alert(db):
    upsert_threshold(db, actor="researcher", metric_name="loss", lower_bound=None, upper_bound=1.0)
    run = make_run(db)
    record_metric(
        db, run_id=run.id, actor="researcher", name="loss", value=1.5, step=1,
        expected_version=run.version,
    )
    alerts = list_alerts(db)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["run_id"] == run.id
    assert alert["run_name"] == "run-1"
    assert alert["project"] == "proj"
    assert alert["metric_name"] == "loss"
    assert alert["value"] == 1.5
    assert alert["bound"] == "upper"
    assert alert["threshold_value"] == 1.0


def test_metric_below_lower_creates_alert(db):
    upsert_threshold(db, actor="researcher", metric_name="hit_rate", lower_bound=0.2, upper_bound=None)
    run = make_run(db)
    record_metric(
        db, run_id=run.id, actor="researcher", name="hit_rate", value=0.12, step=1,
        expected_version=run.version,
    )
    alerts = list_alerts(db)
    assert len(alerts) == 1
    assert alerts[0]["bound"] == "lower"
    assert alerts[0]["threshold_value"] == 0.2


def test_threshold_upsert_rescans_existing_metrics(db):
    """Acceptance scenario: metric recorded first, threshold tightened later."""
    run = make_run(db)
    run = record_metric(
        db, run_id=run.id, actor="researcher", name="tm_score", value=0.81, step=1,
        expected_version=run.version,
    )
    # Loose threshold: no alert.
    upsert_threshold(db, actor="researcher", metric_name="tm_score", lower_bound=0.5, upper_bound=0.9)
    assert alert_count(db) == 0
    # Tighten the upper bound: historical metric now violates -> alert appears.
    upsert_threshold(db, actor="researcher", metric_name="tm_score", lower_bound=0.5, upper_bound=0.75)
    alerts = list_alerts(db)
    assert len(alerts) == 1
    assert alerts[0]["bound"] == "upper"
    assert alerts[0]["threshold_value"] == 0.75
    assert alerts[0]["value"] == 0.81


def test_rescan_does_not_duplicate_alerts(db):
    run = make_run(db)
    record_metric(
        db, run_id=run.id, actor="researcher", name="loss", value=2.0, step=1,
        expected_version=run.version,
    )
    upsert_threshold(db, actor="researcher", metric_name="loss", lower_bound=None, upper_bound=1.0)
    upsert_threshold(db, actor="researcher", metric_name="loss", lower_bound=None, upper_bound=1.0)
    assert alert_count(db) == 1


def test_threshold_validation(db):
    with pytest.raises(DomainError):
        upsert_threshold(db, actor="researcher", metric_name="x", lower_bound=None, upper_bound=None)
    with pytest.raises(DomainError):
        upsert_threshold(db, actor="researcher", metric_name="x", lower_bound=2.0, upper_bound=1.0)


def test_list_and_delete_thresholds(db):
    upsert_threshold(db, actor="researcher", metric_name="loss", lower_bound=None, upper_bound=1.0)
    upsert_threshold(db, actor="researcher", metric_name="acc", lower_bound=0.8, upper_bound=None)
    names = [t.metric_name for t in list_thresholds(db)]
    assert names == ["acc", "loss"]

    delete_threshold(db, metric_name="loss")
    names = [t.metric_name for t in list_thresholds(db)]
    assert names == ["acc"]

    with pytest.raises(DomainError):
        delete_threshold(db, metric_name="missing")


def test_delete_threshold_keeps_existing_alerts(db):
    upsert_threshold(db, actor="researcher", metric_name="loss", lower_bound=None, upper_bound=1.0)
    run = make_run(db)
    run = record_metric(
        db, run_id=run.id, actor="researcher", name="loss", value=3.0, step=1,
        expected_version=run.version,
    )
    delete_threshold(db, metric_name="loss")
    assert alert_count(db) == 1
    # After deletion, new out-of-range metrics no longer alert.
    record_metric(
        db, run_id=run.id, actor="researcher", name="loss", value=4.0, step=2,
        expected_version=run.version,
    )
    assert alert_count(db) == 1
