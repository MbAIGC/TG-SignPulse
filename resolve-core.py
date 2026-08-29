#!/usr/bin/env python3
"""Resolve all 23 conflict hunks in tg_signer/core.py per the sync decision table."""
import re

SRC = "tg_signer/core.py"
src = open(SRC, encoding="utf-8").read()

# split into conflict blocks and ordinary text
parts = []
pos = 0
order = 0
while True:
    m = re.search(r"<<<<<<< (ours|HEAD)\n", src[pos:])
    if not m:
        parts.append(("text", src[pos:]))
        break
    start = pos + m.start()
    parts.append(("text", src[pos:start]))
    end_marker = src.index(">>>>>>>", start)
    end_of_line = src.index("\n", end_marker)
    block = src[start:end_of_line + 1]
    # block = <<<<<<< HEAD\n ours \n=======\n theirs \n>>>>>>> <branch>\n
    body = block[block.index("\n") + 1:-1]  # strip marker line and trailing newline
    sep = body.rfind("\n=======\n")
    ours, theirs = body[:sep], body[sep + len("\n=======\n"):]
    marker_pos = theirs.rfind("\n>>>>>>>")
    if marker_pos != -1:
        theirs = theirs[:marker_pos]
    order += 1
    parts.append(("conflict", order, ours, theirs))
    pos = end_of_line + 1

print(f"parsed {order} conflicts")

def pick(order_no, ours, theirs, chosen):
    if chosen == "ours":
        return ours
    if chosen == "theirs":
        return theirs
    if chosen == "both-imports-typing":
        assert "Any," in ours and "Annotated," in theirs
        return "    Annotated,\n    Any,\n    Awaitable,\n"
    if chosen == "pydantic-clean":
        return "from pydantic import BaseModel, ConfigDict, Field, ValidationError\n"
    if chosen == "imports-ours-plus":
        # ours + new pyrogram names + theirs' local imports
        out = ours
        out = out.replace(
            "        Chat,\n        InlineKeyboardMarkup,",
            "        Chat,\n        Folder,\n        InlineKeyboardButton,\n        InlineKeyboardMarkup,",
        )
        out = out.replace(
            "    class ReplyKeyboardMarkup:  # type: ignore[no-redef]\n        keyboard = ()",
            "    class ReplyKeyboardMarkup:  # type: ignore[no-redef]\n        keyboard = ()\n\n"
            "    class Folder:  # type: ignore[no-redef]\n        pass\n\n"
            "    class InlineKeyboardButton:  # type: ignore[no-redef]\n        pass\n\n"
            "    class SQLiteStorage:  # type: ignore[no-redef]\n        pass",
        )
        out = out.replace(
            "from .utils import UserInput, atomic_write_json, atomic_write_text, print_to_user",
            "from .utils import (\n    UserInput,\n    atomic_write_json,\n    atomic_write_text,\n    get_now,\n    print_to_user,\n)\n"
            "from .utils import get_timezone as _get_timezone\nfrom .sign_record_store import SignRecordStore",
        )
        assert "Folder," in out and "get_now" in out
        return out
    if chosen == "login-hybrid":
        return _login_hybrid()
    if chosen == "run-sig":
        return ("        self,\n"
                "        num_of_dialogs: int = 0,\n"
                "        only_once: bool = False,\n"
                "        force_rerun: bool = False,\n"
                "        folder: Optional[str] = None,")
    if chosen == "run-sig-body":
        return ("        self,\n"
                "        num_of_dialogs: int = 0,\n"
                "        only_once: bool = False,\n"
                "        force_rerun: bool = False,\n"
                "        folder: Optional[str] = None,\n"
                "    ):\n"
                "        if self.user is None:\n"
                "            await self.login(num_of_dialogs, print_chat=True, folder=folder)")
    if chosen == "run-once-sig":
        return ("    async def run_once(self, num_of_dialogs: int = 0, folder: Optional[str] = None):\n"
                "        return await self.run(\n"
                "            num_of_dialogs,\n"
                "            only_once=True,\n"
                "            force_rerun=True,\n"
                "            folder=folder,\n"
                "        )")
    if chosen == "daily-loop-tail":
        return ("                for _route_key in list(self.context.chat_messages.keys()):\n"
                "                    self.context.chat_messages[_route_key].clear()\n"
                "                await asyncio.sleep(config.sign_interval)\n"
                "\n"
                "            if success_count == 0 and len(config.chats) > 0:\n"
                "                raise RuntimeError(\"所有会话均执行失败（详细请看运行日志）\")\n"
                "\n"
                "            sign_record[str(now.date())] = now.isoformat()\n"
                "            atomic_write_json(self.sign_record_file, sign_record)")
    if chosen == "hunk19":
        return _hunk19()
    if chosen == "run-tail-ours":
        # keep ours' if-only_once/cron/finally tail, only swap run_once def
        anchor = "    async def run_once(self, num_of_dialogs: int = 0):"
        idx = ours.find(anchor)
        assert idx != -1, "run_once anchor missing in ours side"
        new_def = ("    async def run_once(self, num_of_dialogs: int = 0, folder: Optional[str] = None):\n"
                   "        return await self.run(\n"
                   "            num_of_dialogs,\n"
                   "            only_once=True,\n"
                   "            force_rerun=True,\n"
                   "            folder=folder,\n"
                   "        )")
        return ours[:idx] + new_def
    if chosen == "monitor-run-sig":
        return ("    async def run(self, num_of_dialogs: int = 0, folder: Optional[str] = None):\n"
                "        if self.user is None:\n"
                "            await self.login(num_of_dialogs, print_chat=True, folder=folder)")
    raise ValueError(chosen)


_LOGIN_HYBRID = '''    async def login(
        self,
        num_of_dialogs=20,
        print_chat=True,
        folder: Optional[str] = None,
    ):
        self.log("开始登录...")
        app = self.app
        key = app.key
        lock = _LOGIN_ASYNC_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _LOGIN_ASYNC_LOCKS[key] = lock

        async with lock:
            me = _LOGIN_USERS.get(key)
            if me is None:
                async with app:
                    if not app.is_connected:
                        # 共享实例可能被外部 stop 过，防御性重连
                        await app.connect()
                    me = await self._call_telegram_api("users.GetFullUser", app.get_me)

                    async def load_latest_chats():
                        selected_folder = None
                        if folder is None:
                            chats = []
                            async for dialog in app.get_dialogs(limit=num_of_dialogs):
                                chats.append(dialog.chat)
                        else:
                            folders = await app.get_folders()
                            selected_folder = _select_chat_folder(folders, folder)
                            chats = _explicit_folder_chats(selected_folder)

                        latest_chats = [
                            {
                                "id": chat.id,
                                "title": chat.title,
                                "type": chat.type,
                                "username": chat.username,
                                "first_name": chat.first_name,
                                "last_name": chat.last_name,
                            }
                            for chat in chats
                        ]
                        return chats, latest_chats, selected_folder

                    (
                        chats,
                        latest_chats,
                        selected_folder,
                    ) = await self._call_telegram_api(
                        "messages.GetDialogFilters"
                        if folder is not None
                        else "messages.GetDialogs",
                        load_latest_chats,
                    )

                    if print_chat:
                        if selected_folder is not None:
                            print_to_user(
                                f"Folder: id: {selected_folder.id}, "
                                f"name: {selected_folder.name}"
                            )
                        for chat in chats:
                            print_to_user(readable_chat(chat))
                            if chat_has_forum_topics(chat):
                                try:
                                    topics = await asyncio.wait_for(
                                        self.get_forum_topics(chat.id, limit=20),
                                        timeout=5,
                                    )
                                    for topic in topics:
                                        print_to_user(f"  {readable_topic(topic)}")
                                except (asyncio.TimeoutError, errors.RPCError):
                                    # Keep login robust: many chats don't support
                                    # forum topics or the current account may not
                                    # have permissions to read them.
                                    pass

                    try:
                        atomic_write_json(
                            self.get_user_dir(me).joinpath("latest_chats.json"),
                            latest_chats,
                            indent=4,
                            default=Object.default,
                            ensure_ascii=False,
                        )
                    except Exception:
                        pass
                    await self._call_telegram_api(
                        "auth.ExportAuthorization", self.app.save_session_string
                    )
                _LOGIN_USERS[key] = me
            else:
                self.log("检测到同账号已完成登录初始化，复用已有会话信息")
            self.set_me(me)
'''

_HUNK19 = '''        text = _get_message_text(message)
        if text:
            self.log("检测到文本回复，尝试调用大模型进行计算题回答")
            self.log(f"问题: \\n{text}")
            option_to_btn = {}
            buttons = _get_inline_keyboard_buttons(message)
            if buttons:
                option_to_btn = {
                    _normalize_option_text(btn.text): btn for btn in buttons
                }
            query = text
            if option_to_btn:
                options = [btn.text for btn in option_to_btn.values()]
                query = (
                    f"{text}\\n\\n"
                    f"可选答案：{json.dumps(options, ensure_ascii=False)}\\n"
                    "请只从可选答案中选择最匹配的一项，并原样回复该选项文本。"
                )
            ai_kwargs = {}
            if (action.ai_prompt or "").strip():
                ai_kwargs["system_prompt"] = action.ai_prompt
            answer = await self.get_ai_tools().calculate_problem(query, **ai_kwargs)
            answer = answer.strip()
            self.log(f"回答为: {answer}")
            if option_to_btn:
                target_btn = option_to_btn.get(_normalize_option_text(answer))
                if not target_btn:
                    self.log("未找到匹配的按钮", level="WARNING")
                    return False
                if not target_btn.callback_data:
                    self.log("匹配的按钮没有 callback_data，无法点击", level="WARNING")
                    return False
                self.log(f"点击按钮: {target_btn.text}")
                await self.request_callback_answer(
                    self.app,
                    message.chat.id,
                    message.id,
                    target_btn.callback_data,
                )
                return True
            await self.send_message(
                message.chat.id,
                answer,
                message_thread_id=getattr(message, "message_thread_id", None),
            )
            return True
        return False

    def _find_previous_photo_message(self, messages: list[Message], message: Message):
        try:
            message_index = next(
                index for index, item in enumerate(messages) if item is message
            )
        except StopIteration:
            return None
        for previous_message in reversed(messages[:message_index]):
            if previous_message and previous_message.photo:
                return previous_message
        return None

    async def _choose_option_by_image(
        self,
        action: ChooseOptionByImageAction,
        message,
        previous_messages: list[Message] = None,
    ):
        buttons = _get_inline_keyboard_buttons(message)
        photo_message = message
        if buttons and not message.photo and previous_messages:
            photo_message = self._find_previous_photo_message(
                previous_messages, message
            )
        if buttons and photo_message and photo_message.photo:
            options = [btn.text for btn in buttons]
            self.log("检测到图片，尝试调用大模型进行图片识别并选择选项")
            image_buffer: BinaryIO = await self.app.download_media(
                photo_message.photo.file_id, in_memory=True
            )
            image_buffer.seek(0)
            image_bytes = image_buffer.read()
            query = _get_message_text(message) or "选择正确的选项"
            result_index = await self.get_ai_tools().choose_option_by_image(
                image_bytes,
                query,
                list(enumerate(options)),
            )
            if result_index < 0 or result_index >= len(buttons):
                self.log("图片识别返回的选项序号无效", level="WARNING")
                return False
            target_btn = buttons[result_index]
            self.log(f"选择结果为: {target_btn.text}")
            if not target_btn.callback_data:
                self.log("匹配的按钮没有 callback_data，无法点击", level="WARNING")
                return False
            await self.request_callback_answer(
                self.app,
                message.chat.id,
                message.id,
                target_btn.callback_data,
            )
            return True

    async def _reply_by_image_recognition(
        self, action: ReplyByImageRecognitionAction, message
    ):
        if not message.photo:
            return False
        self._log_received_target_message(message)
        self.log("AI 正在分析图片中的文字")
        image_buffer: BinaryIO = await self.app.download_media(
            message.photo.file_id, in_memory=True
        )
        try:
            image_buffer.seek(0)
            image_bytes = image_buffer.read()
        finally:
            if hasattr(image_buffer, "close"):
                image_buffer.close()
            image_buffer = None
            trim_memory()

        if (action.ai_prompt or "").strip():
            self.log("当前 AI 动作使用自定义提示词")
        text = await self.get_ai_tools().extract_text_by_image(
            image_bytes,
            system_prompt=action.ai_prompt,
        )
        del image_bytes
        text = (text or "").strip()
        if not text:
            self.log("AI 未识别到可发送文本", level="WARNING")
            return False
        self.log(f"AI 识别结果：{text}")
        await self.send_message(message.chat.id, text)
        return True

    async def _click_button_by_calculation_problem(
        self, action: ClickButtonByCalculationProblemAction, message
    ):
        if not message.text:
            return False
        self._log_received_target_message(message)
        self.log("AI 正在计算按钮答案")
        if (action.ai_prompt or "").strip():
            self.log("当前 AI 动作使用自定义提示词")
        answer = await self.get_ai_tools().calculate_problem(
            message.text,
            system_prompt=action.ai_prompt,
        )
        answer = (answer or "").strip()
        if not answer:
            self.log("AI 未返回可用于点击的答案", level="WARNING")
            return False
        self.log(f"AI 计算结果：{answer}")
        proxy_action = ClickKeyboardByTextAction(text=answer)
        return await self._click_keyboard_by_text(proxy_action, message)
'''


def _login_hybrid():
    return _LOGIN_HYBRID


def _hunk19():
    return _HUNK19


# decision table: order -> chosen
DECISIONS = {
    1: "both-imports-typing",
    2: "pydantic-clean",
    3: "imports-ours-plus",
    4: "ours",
    5: "login-hybrid",
    6: "theirs",
    7: "theirs",
    8: "theirs",
    9: "theirs",
    10: "ours",
    11: "ours",
    12: "run-sig",
    13: "run-sig",
    14: "run-sig-body",
    15: "ours",
    16: "daily-loop-tail",
    17: "run-tail-ours",
    18: "ours",
    19: "hunk19",
    20: "ours",
    21: "ours",
    22: "theirs",
    23: "monitor-run-sig",
}

out = []
for part in parts:
    if part[0] == "text":
        out.append(part[1])
    else:
        _, n, ours, theirs = part
        chosen = pick(n, ours, theirs, DECISIONS[n])
        if chosen and not chosen.endswith("\n"):
            chosen += "\n"
        out.append(chosen)

result = "".join(out)
assert "<<<<<<<" not in result and ">>>>>>>" not in result
open(SRC, "w", encoding="utf-8").write(result)
print("resolved all conflicts, markers left:", result.count("<<<<<<<"))