from datetime import time

import pytest
from pydantic import ValidationError

from tg_signer.config import (
    ChooseOptionByImageAction,
    ClickKeyboardByTextAction,
    ReplyByCalculationProblemAction,
    SendDiceAction,
    SendTextAction,
    SignChatV2,
    SignChatV3,
    SignConfigV1,
    SignConfigV2,
    SignConfigV3,
)


class TestSignConfigV2ToCurrent:
    """测试 SignConfigV2.to_current 方法"""

    def test_convert_basic_chat(self):
        """测试基础聊天配置转换"""
        v2_config = SignConfigV2(
            chats=[
                SignChatV2(
                    chat_id=123,
                    sign_text="Hello",
                    delete_after=10,
                )
            ],
            sign_at="08:00",
            random_seconds=300,
        )

        v3_config = SignConfigV2.to_current(v2_config)

        assert isinstance(v3_config, SignConfigV3)
        assert v3_config.sign_at == "08:00"
        assert v3_config.random_seconds == 300
        assert len(v3_config.chats) == 1

        chat = v3_config.chats[0]
        assert chat.chat_id == 123
        assert chat.message_thread_id is None
        assert chat.delete_after == 10
        assert len(chat.actions) == 1
        assert isinstance(chat.actions[0], SendTextAction)
        assert chat.actions[0].text == "Hello"

    def test_convert_dice_chat(self):
        """测试 Dice 表情配置转换"""
        v2_config = SignConfigV2(
            chats=[
                SignChatV2(
                    chat_id=123,
                    sign_text="🎲",
                    as_dice=True,
                )
            ],
            sign_at="08:00",
        )

        v3_config = SignConfigV2.to_current(v2_config)
        action = v3_config.chats[0].actions[0]

        assert isinstance(action, SendDiceAction)
        assert action.dice == "🎲"

    def test_convert_complex_chat(self):
        """测试包含多种操作的复杂配置转换"""
        v2_config = SignConfigV2(
            chats=[
                SignChatV2(
                    chat_id=123,
                    sign_text="签到",
                    text_of_btn_to_click="点击",
                    choose_option_by_image=True,
                    has_calculation_problem=True,
                )
            ],
            sign_at="08:00",
        )

        v3_config = SignConfigV2.to_current(v2_config)
        actions = v3_config.chats[0].actions

        assert len(actions) == 4
        assert isinstance(actions[0], SendTextAction)
        assert actions[0].text == "签到"
        assert isinstance(actions[1], ClickKeyboardByTextAction)
        assert actions[1].text == "点击"
        assert isinstance(actions[2], ChooseOptionByImageAction)
        assert isinstance(actions[3], ReplyByCalculationProblemAction)

    def test_convert_multiple_chats(self):
        """测试多个聊天配置转换"""
        v2_config = SignConfigV2(
            chats=[
                SignChatV2(chat_id=1, sign_text="Chat1"),
                SignChatV2(chat_id=2, sign_text="Chat2"),
            ],
            sign_at="08:00",
        )

        v3_config = SignConfigV2.to_current(v2_config)

        assert len(v3_config.chats) == 2
        assert v3_config.chats[0].chat_id == 1
        assert v3_config.chats[0].actions[0].text == "Chat1"
        assert v3_config.chats[1].chat_id == 2
        assert v3_config.chats[1].actions[0].text == "Chat2"

    def test_convert_empty_actions(self):
        """测试空操作列表的情况"""
        v2_config = SignConfigV2(
            chats=[SignChatV2(chat_id=123, sign_text="")],
            sign_at="08:00",
        )

        v3_config = SignConfigV2.to_current(v2_config)

        assert len(v3_config.chats[0].actions) == 0

    def test_convert_from_v1(self):
        """测试从 V1 配置升级到 V3"""
        v1_config = SignConfigV1(
            chat_id=123,
            sign_text="Old config",
            sign_at=time(8, 0),
            random_seconds=300,
        )

        # 通过 V2 的 load 方法触发转换
        v3_config = SignConfigV2.to_current(v1_config)

        assert isinstance(v3_config, SignConfigV3)
        assert v3_config.sign_at == "08:00:00"  # time 对象被转换为字符串
        assert v3_config.random_seconds == 300
        assert len(v3_config.chats) == 1
        assert v3_config.chats[0].chat_id == 123
        assert v3_config.chats[0].message_thread_id is None
        assert v3_config.chats[0].actions[0].text == "Old config"

    def test_sign_chat_v3_with_message_thread_id(self):
        chat = SignChatV3(
            chat_id=-1001234567890,
            message_thread_id=1,
            actions=[SendTextAction(text="checkin")],
        )
        assert chat.message_thread_id == 1

    def test_sign_chat_v3_supports_username_chat_id(self):
        chat = SignChatV3(
            chat_id="@neo",
            actions=[SendTextAction(text="checkin")],
        )

        assert chat.chat_id == "@neo"

    def test_sign_chat_v3_coerces_numeric_string_chat_id_to_int(self):
        chat = SignChatV3(
            chat_id="-1001234567890",
            actions=[SendTextAction(text="checkin")],
        )

        assert chat.chat_id == -1001234567890
        assert isinstance(chat.chat_id, int)

    def test_sign_chat_v3_rejects_username_without_at_prefix(self):
        with pytest.raises(ValidationError):
            SignChatV3(
                chat_id="neo",
                actions=[SendTextAction(text="checkin")],
            )


class TestSignConfigV3LoadRecursiveChain:
    """回归：BaseJSONConfig.load 递归遍历完整版本链（V3->V2->V1）"""

    def test_v3_load_accepts_v1_dict_directly(self):
        v1_dict = {
            "chat_id": 123,
            "sign_text": "签到",
            "sign_at": "08:00",
            "random_seconds": 0,
        }
        result = SignConfigV3.load(v1_dict)
        assert result is not None
        config, from_old = result
        assert from_old is True
        assert isinstance(config, SignConfigV3)
        assert len(config.chats) == 1
        assert config.chats[0].chat_id == 123
        assert config.chats[0].actions == [SendTextAction(text="签到")]

    def test_v3_load_accepts_v2_dict_directly(self):
        v2_dict = {
            "chats": [{"chat_id": 456, "sign_text": "hello", "delete_after": 5}],
            "sign_at": "09:00",
            "random_seconds": 60,
            "sign_interval": 1,
        }
        result = SignConfigV3.load(v2_dict)
        assert result is not None
        config, from_old = result
        assert from_old is True
        assert isinstance(config, SignConfigV3)
        assert config.chats[0].chat_id == 456
        assert config.chats[0].actions == [SendTextAction(text="hello")]

    def test_v3_load_accepts_v3_dict_unmodified(self):
        v3_dict = {
            "chats": [
                {"chat_id": 789, "actions": [{"action": 1, "text": "hi"}]}
            ],
            "sign_at": "0 0 * * *",
            "sign_interval": 1,
        }
        result = SignConfigV3.load(v3_dict)
        assert result is not None
        config, from_old = result
        assert from_old is False
        assert config.chats[0].chat_id == 789

    def test_v3_load_returns_none_for_unknown_dict(self):
        assert SignConfigV3.load({"foo": "bar"}) is None

    def test_v2_load_accepts_v1_dict(self):
        v1_dict = {
            "chat_id": 12,
            "sign_text": "old",
            "sign_at": "10:00",
            "random_seconds": 0,
        }
        result = SignConfigV2.load(v1_dict)
        assert result is not None
        config, from_old = result
        assert from_old is True
        assert isinstance(config, SignConfigV2)
        assert config.chats[0].sign_text == "old"

    def test_v1_load_accepts_v1_dict(self):
        v1_dict = {
            "chat_id": 12,
            "sign_text": "old",
            "sign_at": "10:00",
            "random_seconds": 0,
        }
        result = SignConfigV1.load(v1_dict)
        assert result is not None
        config, from_old = result
        assert from_old is False
        assert isinstance(config, SignConfigV1)
        assert config.chat_id == 12
