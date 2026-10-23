import pytest

from hololab.gateway.jobs import Job
from hololab.gateway.registry import JobsStore
from hololab.persistence.db import open_database


@pytest.mark.asyncio
async def test_identity_survives_reopen_and_cannot_be_replaced(tmp_path):
    path = tmp_path / "state.sqlite"
    db = await open_database(path)
    store = JobsStore(db)
    await store.create(
        Job(
            job_id="j",
            workflow_id="w",
            snapshot_id=None,
            algorithm_name="demo",
            algorithm_version="1.0",
        )
    )
    assert await store.get_code_identity("j") is None  # Old rows stay readable.
    record = {"hot": {"sha256": "original"}, "cold": {"baseline_at": 123}}
    await store.set_code_identity("j", record)
    await store.set_code_identity("j", record)
    with pytest.raises(ValueError, match="cannot be replaced"):
        await store.set_code_identity("j", {"hot": {"sha256": "new"}})
    await db.close()
    db = await open_database(path)
    try:
        assert await JobsStore(db).get_code_identity("j") == record
    finally:
        await db.close()
