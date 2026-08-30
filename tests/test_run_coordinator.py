import asyncio

from backend.services.run_coordinator import RunCoordinator


def test_run_coordinator_allows_one_reservation_per_task():
    coordinator = RunCoordinator()

    async def scenario():
        first = await coordinator.try_reserve("acct", "task", source="api")
        second = await coordinator.try_reserve("acct", "task", source="scheduler")
        assert first is not None
        assert second is None
        assert coordinator.is_reserved("acct", "task")
        await coordinator.release(first)
        assert not coordinator.is_reserved("acct", "task")
        return await coordinator.try_reserve("acct", "task", source="manual")

    replacement = asyncio.run(scenario())
    assert replacement is not None
    assert replacement.source == "manual"
    assert replacement.run_id


def test_run_coordinator_different_tasks_can_reserve_concurrently():
    coordinator = RunCoordinator()

    async def scenario():
        return await asyncio.gather(
            coordinator.try_reserve("acct", "task-a", source="api"),
            coordinator.try_reserve("acct", "task-b", source="scheduler"),
        )

    first, second = asyncio.run(scenario())
    assert first is not None
    assert second is not None
    assert first.task_key != second.task_key
