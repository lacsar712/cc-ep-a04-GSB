import hashlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.cqrs import (
    DomainError,
    delete_threshold,
    list_alerts,
    list_thresholds,
    record_metric,
    start_run,
    upsert_threshold,
)
from app.database import Base


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


def _make_run(db, name="n1"):
    return start_run(
        db,
        actor="researcher",
        project="p1",
        name=name,
        dataset_content_sha256=sha("ds-" + name),
        code_commit_sha="abc1234",
        description=None,
    )


def test_metric_within_threshold_no_alert(db):
    run = _make_run(db)
    upsert_threshold(db, metric_name="loss", lower_bound=None, upper_bound=1.0, actor="researcher")
    record_metric(
        db, run_id=run.id, actor="researcher", name="loss", value=0.5, step=1, expected_version=1
    )
    assert list_alerts(db) == []


def test_metric_above_upper_bound_alerts(db):
    run = _make_run(db)
    upsert_threshold(db, metric_name="loss", lower_bound=None, upper_bound=1.0, actor="researcher")
    record_metric(
        db, run_id=run.id, actor="researcher", name="loss", value=1.2, step=1, expected_version=1
    )
    alerts = list_alerts(db)
    assert len(alerts) == 1
    alert, proj = alerts[0]
    assert alert.run_id == run.id
    assert alert.metric_name == "loss"
    assert alert.value == 1.2
    assert alert.upper_bound == 1.0
    assert alert.direction == "above"
    assert proj.id == run.id


def test_metric_below_lower_bound_alerts(db):
    run = _make_run(db)
    upsert_threshold(db, metric_name="hit_rate", lower_bound=0.1, upper_bound=None, actor="researcher")
    record_metric(
        db, run_id=run.id, actor="researcher", name="hit_rate", value=0.05, step=1, expected_version=1
    )
    alerts = list_alerts(db)
    assert len(alerts) == 1
    assert alerts[0][0].direction == "below"


def test_unconfigured_metric_never_alerts(db):
    run = _make_run(db)
    record_metric(
        db, run_id=run.id, actor="researcher", name="acc", value=999.0, step=1, expected_version=1
    )
    assert list_alerts(db) == []


def test_upsert_threshold_backfills_alerts_for_existing_metrics(db):
    run = _make_run(db)
    run = record_metric(
        db, run_id=run.id, actor="researcher", name="tm_score", value=0.81, step=1, expected_version=1
    )
    assert list_alerts(db) == []

    # Lowering the upper bound after the fact surfaces the recorded breach.
    upsert_threshold(db, metric_name="tm_score", lower_bound=None, upper_bound=0.9, actor="researcher")
    assert list_alerts(db) == []
    upsert_threshold(db, metric_name="tm_score", lower_bound=None, upper_bound=0.75, actor="researcher")
    alerts = list_alerts(db)
    assert len(alerts) == 1
    assert alerts[0][0].value == 0.81
    assert alerts[0][0].upper_bound == 0.75


def test_upsert_threshold_regenerates_instead_of_duplicating(db):
    run = _make_run(db)
    record_metric(
        db, run_id=run.id, actor="researcher", name="loss", value=2.0, step=1, expected_version=1
    )
    upsert_threshold(db, metric_name="loss", lower_bound=None, upper_bound=1.0, actor="researcher")
    upsert_threshold(db, metric_name="loss", lower_bound=None, upper_bound=1.5, actor="researcher")
    alerts = list_alerts(db)
    assert len(alerts) == 1
    assert alerts[0][0].upper_bound == 1.5


def test_relaxing_threshold_clears_stale_alerts(db):
    run = _make_run(db)
    record_metric(
        db, run_id=run.id, actor="researcher", name="loss", value=2.0, step=1, expected_version=1
    )
    upsert_threshold(db, metric_name="loss", lower_bound=None, upper_bound=1.0, actor="researcher")
    assert len(list_alerts(db)) == 1
    upsert_threshold(db, metric_name="loss", lower_bound=None, upper_bound=3.0, actor="researcher")
    assert list_alerts(db) == []


def test_delete_threshold_removes_its_alerts(db):
    run = _make_run(db)
    record_metric(
        db, run_id=run.id, actor="researcher", name="loss", value=2.0, step=1, expected_version=1
    )
    upsert_threshold(db, metric_name="loss", lower_bound=None, upper_bound=1.0, actor="researcher")
    assert len(list_alerts(db)) == 1
    delete_threshold(db, metric_name="loss")
    assert list_alerts(db) == []
    assert list_thresholds(db) == []
    with pytest.raises(DomainError):
        delete_threshold(db, metric_name="loss")


def test_threshold_validation(db):
    with pytest.raises(DomainError):
        upsert_threshold(db, metric_name="x", lower_bound=None, upper_bound=None, actor="researcher")
    with pytest.raises(DomainError):
        upsert_threshold(db, metric_name="x", lower_bound=2.0, upper_bound=1.0, actor="researcher")


def test_list_alerts_filters(db):
    run1 = _make_run(db, name="r1")
    run2 = _make_run(db, name="r2")
    upsert_threshold(db, metric_name="loss", lower_bound=None, upper_bound=1.0, actor="researcher")
    record_metric(
        db, run_id=run1.id, actor="researcher", name="loss", value=2.0, step=1, expected_version=1
    )
    record_metric(
        db, run_id=run2.id, actor="researcher", name="loss", value=3.0, step=1, expected_version=1
    )
    assert len(list_alerts(db)) == 2
    assert len(list_alerts(db, run_id=run1.id)) == 1
    assert len(list_alerts(db, metric_name="loss")) == 2
    assert list_alerts(db, metric_name="other") == []
