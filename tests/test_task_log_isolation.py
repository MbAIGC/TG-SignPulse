"""执行日志隔离：并发账号/任务运行时日志不得串台。"""

import asyncio
import logging

import pytest

from backend.services.sign_tasks import (
    TaskLogHandler,
    _current_task_run,
)


def _logger() -> logging.Logger:
    test_logger = logging.getLogger("test-task-log-isolation")
    test_logger.setLevel(logging.INFO)
    test_logger.propagate = False
    return test_logger


def _line(account: str, task: str, text: str) -> str:
    return f"账户「{account}」- 任务「{task}」: {text}"


@pytest.mark.asyncio
async def test_concurrent_accounts_do_not_cross_talk():
    test_logger = _logger()
    buffers = {"acctA": [], "acctB": []}
    trimmed = {"acctA": 0, "acctB": 0}
    handlers = {
        name: TaskLogHandler(
            buffers[name],
            trimmed,
            (name, "daily"),
            account_name=name,
            task_name="daily",
            run_id=f"run-{name}",
        )
        for name in buffers
    }
    for handler in handlers.values():
        handler.setFormatter(logging.Formatter("%(message)s"))
        test_logger.addHandler(handler)

    async def run(account: str, own: int, other: int):
        token = _current_task_run.set((account, "daily", f"run-{account}"))
        try:
            for index in range(own):
                test_logger.info(_line(account, "daily", f"own-{index}"))
            # 另一个账号的日志行即使被本任务“看到”，也不能进本任务的缓冲
            test_logger.info(
                _line(
                    "acctB" if account == "acctA" else "acctA",
                    "daily",
                    f"other-{other}",
                )
            )
            # 无前缀的网络诊断日志按当前运行上下文归属
            test_logger.info("connection reset by peer")
        finally:
            _current_task_run.reset(token)

    try:
        await asyncio.gather(run("acctA", 2, 9), run("acctB", 3, 8))
    finally:
        for handler in handlers.values():
            test_logger.removeHandler(handler)

    assert [line for line in buffers["acctA"] if "own-" in line] == [
        _line("acctA", "daily", "own-0"),
        _line("acctA", "daily", "own-1"),
    ]
    assert len([line for line in buffers["acctA"] if "own-" in line]) == 2
    assert not any("acctB" in line for line in buffers["acctA"])
    assert not any("acctA" in line for line in buffers["acctB"])
    assert len([line for line in buffers["acctB"] if "own-" in line]) == 3
    # 无前缀日志各自归属本账号
    assert buffers["acctA"].count("connection reset by peer") == 1
    assert buffers["acctB"].count("connection reset by peer") == 1


@pytest.mark.asyncio
async def test_stale_run_of_same_task_is_rejected():
    test_logger = _logger()
    buffer = []
    handler = TaskLogHandler(
        buffer,
        {},
        ("acct", "daily"),
        account_name="acct",
        task_name="daily",
        run_id="run-new",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    test_logger.addHandler(handler)
    try:
        # 记录自带身份，但属于上一轮运行
        stale = logging.LogRecord(
            name="tg-signer",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="old",
            args=(),
            exc_info=None,
        )
        stale._sign_task_run = ("acct", "daily", "run-old")
        handler.handle(stale)

        fresh = logging.LogRecord(
            name="tg-signer",
            level=logging.INFO,
            pathname=__file__,
            lineno=2,
            msg=_line("acct", "daily", "new"),
            args=(),
            exc_info=None,
        )
        fresh._sign_task_run = ("acct", "daily", "run-new")
        handler.handle(fresh)
    finally:
        test_logger.removeHandler(handler)

    assert buffer == [_line("acct", "daily", "new")]


@pytest.mark.asyncio
async def test_unprefixed_log_is_dropped_without_matching_context():
    test_logger = _logger()
    buffer = []
    handler = TaskLogHandler(
        buffer,
        {},
        ("acct", "daily"),
        account_name="acct",
        task_name="daily",
        run_id="run-1",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    test_logger.addHandler(handler)
    try:
        # 没有任何身份信息，且当前上下文属于别的账号
        token = _current_task_run.set(("other", "daily", "run-x"))
        try:
            test_logger.info("connection reset by peer")
        finally:
            _current_task_run.reset(token)
    finally:
        test_logger.removeHandler(handler)

    assert buffer == []
