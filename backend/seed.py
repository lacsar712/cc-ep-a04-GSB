"""Seed demo runs: 2 completed + 1 running, plus demo metric thresholds."""

from __future__ import annotations

import hashlib
import time
from uuid import UUID

from sqlalchemy import select, text

from app.cqrs import attach_artifact, complete_run, record_metric, start_run
from app.database import Base, SessionLocal, engine
from app.models import MetricThreshold, RunProjection
from app.monitoring import upsert_threshold


def sha256_hex(text_value: str) -> str:
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()


def wait_for_db(max_attempts: int = 60) -> None:
    for i in range(max_attempts):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("Database not ready")


# Demo thresholds. Chosen so the seeded runs immediately produce visible alerts:
#   - run3 loss=1.84    > upper 1.5  -> upper-bound alert
#   - run2 hit_rate=0.12 < lower 0.2 -> lower-bound alert
#   - tm_score [0.5, 0.9] holds run1's 0.72/0.81, so no alert until someone
#     tightens the bound (the acceptance demo: lower upper to 0.75).
DEMO_THRESHOLDS = [
    ("tm_score", 0.5, 0.9),
    ("hit_rate", 0.2, None),
    ("loss", None, 1.5),
]


def seed_thresholds(db) -> None:
    created = 0
    for metric_name, lower, upper in DEMO_THRESHOLDS:
        existing = db.scalar(
            select(MetricThreshold).where(MetricThreshold.metric_name == metric_name)
        )
        if existing:
            continue
        upsert_threshold(
            db,
            actor="seed",
            metric_name=metric_name,
            lower_bound=lower,
            upper_bound=upper,
        )
        created += 1
    if created:
        print(f"Seeded {created} demo thresholds (alerts generated for out-of-bounds metrics)")
    else:
        print("Thresholds already present, skipped")


def seed_runs(db) -> None:
    # Completed run 1
    run1 = start_run(
        db,
        actor="researcher",
        project="protein-folding",
        name="AlphaFold baseline v1",
        dataset_content_sha256=sha256_hex("casp14-subset-v1"),
        code_commit_sha="a1b2c3d4e5f6789012345678abcdef0123456789"[:40],
        description="基线折叠实验，记录 TM-score",
        run_id=UUID("11111111-1111-1111-1111-111111111111"),
    )
    run1 = record_metric(
        db,
        run_id=run1.id,
        actor="researcher",
        name="tm_score",
        value=0.72,
        step=1,
        expected_version=run1.version,
    )
    run1 = record_metric(
        db,
        run_id=run1.id,
        actor="researcher",
        name="tm_score",
        value=0.81,
        step=2,
        expected_version=run1.version,
    )
    run1 = attach_artifact(
        db,
        run_id=run1.id,
        actor="researcher",
        name="structure.pdb",
        uri="s3://lab-artifacts/protein-folding/run1/structure.pdb",
        content_sha256=sha256_hex("structure-pdb-run1"),
        media_type="chemical/x-pdb",
        expected_version=run1.version,
    )
    complete_run(
        db,
        run_id=run1.id,
        actor="researcher",
        result_summary="基线完成，最终 TM-score=0.81",
        expected_version=run1.version,
    )

    # Completed run 2
    run2 = start_run(
        db,
        actor="researcher",
        project="drug-screen",
        name="Kinase panel screen #42",
        dataset_content_sha256=sha256_hex("kinase-panel-2024q3"),
        code_commit_sha="f0e1d2c3b4a5968778695a4b3c2d1e0f98765432",
        description="激酶抑制剂筛选批次",
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
    )
    run2 = record_metric(
        db,
        run_id=run2.id,
        actor="researcher",
        name="hit_rate",
        value=0.12,
        step=1,
        expected_version=run2.version,
    )
    run2 = attach_artifact(
        db,
        run_id=run2.id,
        actor="researcher",
        name="hits.csv",
        uri="s3://lab-artifacts/drug-screen/run42/hits.csv",
        content_sha256=sha256_hex("hits-csv-run42"),
        media_type="text/csv",
        expected_version=run2.version,
    )
    complete_run(
        db,
        run_id=run2.id,
        actor="researcher",
        result_summary="筛选完成，命中率 12%",
        expected_version=run2.version,
    )

    # Running run 3
    run3 = start_run(
        db,
        actor="researcher",
        project="protein-folding",
        name="Fine-tune with MSA augmentation",
        dataset_content_sha256=sha256_hex("casp14-msa-aug-v2"),
        code_commit_sha="9abc8def7a6543210fedcba9876543210abcdef0",
        description="进行中的增强 MSA 微调实验",
        run_id=UUID("33333333-3333-3333-3333-333333333333"),
    )
    record_metric(
        db,
        run_id=run3.id,
        actor="researcher",
        name="loss",
        value=1.84,
        step=10,
        expected_version=run3.version,
    )

    print("Seed completed: 2 completed runs + 1 running run")


def seed() -> None:
    wait_for_db()
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        existing = db.scalar(select(RunProjection).limit(1))
        if existing:
            print("Seed skipped: run data already present")
        else:
            seed_runs(db)
        # Thresholds are ensured independently so existing deployments pick
        # them up too; upsert re-scans recorded metrics and raises alerts.
        seed_thresholds(db)
    finally:
        db.close()


if __name__ == "__main__":
    seed()
