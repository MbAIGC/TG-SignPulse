"""机器人最后回复提取：必须按账号/任务前缀归属，且兼容当前与历史日志格式。"""

from backend.services.sign_tasks import SignTaskService


def _service() -> SignTaskService:
    return SignTaskService()


def test_extract_last_reply_respects_account_and_task_prefix():
    service = _service()
    logs = [
        "账户「acctA」- 任务「daily」: 收到回复：签到成功",
        "账户「acctB」- 任务「daily」: 收到回复：其他账号的回复",
    ]

    assert service._extract_last_reply(logs, "acctA", "daily") == "签到成功"
    # 前缀不匹配时必须返回空，不能把并发账号的回复当成本任务的结果
    assert service._extract_last_reply(logs, "acctC", "daily") == ""


def test_extract_last_reply_isolates_same_account_other_task():
    service = _service()
    logs = [
        "账户「acctA」- 任务「other」: 收到回复：别的任务",
        "账户「acctA」- 任务「daily」: 收到回复：本任务",
    ]

    assert service._extract_last_reply(logs, "acctA", "daily") == "本任务"


def test_extract_last_reply_returns_latest_and_truncates():
    service = _service()
    logs = [
        "账户「acctA」- 任务「daily」: 收到回复：第一次",
        "账户「acctA」- 任务「daily」: 收到回复：第二次",
    ]

    assert service._extract_last_reply(logs, "acctA", "daily") == "第二次"

    long_reply = "x" * 300
    truncated = service._extract_last_reply(
        [f"账户「acctA」- 任务「daily」: 收到回复：{long_reply}"],
        "acctA",
        "daily",
    )
    assert len(truncated) == 200
    assert truncated.endswith("...")


def test_extract_last_reply_supports_legacy_format_and_button_prompt():
    service = _service()
    legacy = (
        "账户「acctA」- 任务「daily」: 收到来自「bot」的消息: "
        "\nMessage: \n  text: 旧格式回复"
    )
    button_prompt = "账户「acctA」- 任务「daily」: 收到回复（按钮提示）：请先加入频道"

    assert service._extract_last_reply([legacy], "acctA", "daily") == "旧格式回复"
    assert (
        service._extract_last_reply([button_prompt], "acctA", "daily") == "请先加入频道"
    )


def test_extract_last_reply_ignores_lines_without_reply_marker():
    service = _service()
    logs = ["账户「acctA」- 任务「daily」: 发送文本: /checkin"]

    assert service._extract_last_reply(logs, "acctA", "daily") == ""
