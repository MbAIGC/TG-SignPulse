"""客户端 teardown：调用方被取消时也必须把连接关闭干净。"""

import asyncio
import pathlib

import pytest

import tg_signer.core as core


@pytest.mark.asyncio
async def test_close_client_by_name_completes_stop_when_caller_cancelled(tmp_path):
    started = asyncio.Event()
    stopped = asyncio.Event()

    class _FakeClient:
        is_connected = True

        async def clear_client_cache(self):
            return None

        async def stop(self):
            started.set()
            await asyncio.sleep(0.05)
            stopped.set()

    key = str(pathlib.Path(tmp_path).joinpath("acct_shield").resolve())
    client = _FakeClient()
    core._CLIENT_INSTANCES[key] = client
    core._CLIENT_REFS[key] = 0
    core._CLIENT_ASYNC_LOCKS[key] = asyncio.Lock()

    task = asyncio.create_task(
        core.close_client_by_name("acct_shield", workdir=tmp_path)
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # stop 被 shield 保护：取消外层调用不会留下半关闭的客户端
    await asyncio.wait_for(stopped.wait(), timeout=1)
    assert stopped.is_set()
    assert core._CLIENT_INSTANCES.get(key) is None
