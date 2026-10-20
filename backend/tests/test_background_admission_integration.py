"""Installation-wide PostgreSQL admission for heavy worker jobs."""

import asyncio

from datapipette.config import Settings
from datapipette.infrastructure.background_admission import PostgresBackgroundJobAdmission
from datapipette.infrastructure.database import create_database_engine


def test_background_admission_is_shared_across_engines_and_dynamic_limits() -> None:
    async def exercise() -> None:
        settings = Settings(environment="test")
        first_engine = create_database_engine(settings)
        second_engine = create_database_engine(settings)
        first = PostgresBackgroundJobAdmission(first_engine)
        second = PostgresBackgroundJobAdmission(second_engine)
        try:
            async with first.slot(1) as first_admitted:
                assert first_admitted
                async with second.slot(1) as blocked:
                    assert not blocked
                async with second.slot(2) as admitted_by_higher_revision:
                    assert admitted_by_higher_revision
            async with second.slot(1) as admitted_after_release:
                assert admitted_after_release
        finally:
            await first_engine.dispose()
            await second_engine.dispose()

    asyncio.run(exercise())


def test_lowered_limit_counts_jobs_in_slots_from_an_older_revision() -> None:
    async def exercise() -> None:
        settings = Settings(environment="test")
        engines = [create_database_engine(settings) for _ in range(3)]
        admissions = [PostgresBackgroundJobAdmission(engine) for engine in engines]
        first_context = admissions[0].slot(2)
        second_context = admissions[1].slot(2)
        try:
            assert await first_context.__aenter__()
            assert await second_context.__aenter__()
            await first_context.__aexit__(None, None, None)
            async with admissions[2].slot(1) as admitted_after_lowering:
                assert not admitted_after_lowering
        finally:
            await second_context.__aexit__(None, None, None)
            for engine in engines:
                await engine.dispose()

    asyncio.run(exercise())
