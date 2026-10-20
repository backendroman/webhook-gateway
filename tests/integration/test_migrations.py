import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from webhook_gateway.repositories.tables import metadata

pytestmark = pytest.mark.integration


async def test_migration_matches_metadata(engine: AsyncEngine) -> None:
    def _diff(conn: Connection) -> list[object]:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff: list[object] = compare_metadata(ctx, metadata)
        return diff

    async with engine.connect() as conn:
        diff = await conn.run_sync(_diff)
    assert diff == []
