"""森空岛（Skland）自动签到插件 — MaiBot v2 插件

功能：
- 用户通过指令或 LLM 工具绑定森空岛 token（支持明日方舟 / 终末地）。
- 手动签到、查看签到状态、解绑。
- 每日定时自动签到（北京时间，默认 06:00，仅记录日志不推送）。
- LLM 工具：skland_help / skland_bind_token / skland_sign_in / skland_sign_status / skland_auto_sign。
- token 获取引导采用链接方案：skland_help 工具只把获取链接及链接作用返回给大模型，
  由大模型自行组织回答；指令触发时固定回复获取教程（不再使用二维码）。

skland_api.py 移植自 https://github.com/Azincc/astrbot_plugin_skland
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Mapping, Optional

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .skland_api import SklandAPI

# ==================== 常量 ====================

TZ = timezone(timedelta(hours=8))

# 统一持久化目录的子文件夹名（data/plugins/cateye_skland_sign）。
# 注意：manifest 的插件 ID 为 github.cateye.skland.sign（id 必须以点号/横线分隔），
# 但数据目录子文件夹固定使用与项目文件夹同名的 cateye_skland_sign，不随 ID 变化。
DATA_DIR_NAME = "cateye_skland_sign"

# token 获取链接（默认值）：浏览器打开后跳转到森空岛登录，点击帖子中的链接即可获取绑定 token。
# 用户可通过配置 [token] get_url 自行修改（见 TokenSectionConfig）。
TOKEN_URL = "https://www.skland.com/article?id=6216915&c_c=COPY"

# 供大模型理解链接作用的说明（工具返回给 LLM 用）
TOKEN_URL_PURPOSE = (
    "用浏览器打开此链接会跳转到森空岛官网登录页，登录后点击帖子中的链接即可获取绑定 token。"
)


def build_token_tutorial(token_url: str) -> str:
    """构造 token 获取教程文案（链接来自配置）。"""
    return f"""如何获取 Token：
1. 使用浏览器打开：{token_url}
2. 跳转到森空岛登录并登录账号
3. 点击帖子中的链接即可获取 token"""


def build_help_text(token_url: str) -> str:
    """构造帮助/固定回复文案（链接来自配置）。"""
    return (
        build_token_tutorial(token_url)
        + "\n\n可用指令：\n"
        "/森空岛 或 /森空岛帮助 —— 显示帮助与 token 获取教程\n"
        "/森空岛绑定 <token> —— 绑定 token（支持完整 JSON 或纯 Base64 字符串）\n"
        "/森空岛解绑 —— 解绑并停止自动签到\n"
        "/森空岛签到 —— 手动签到\n"
        "/森空岛状态 —— 查看今日签到状态\n"
        "/森空岛自动签到 —— 开启 / 关闭自动签到\n"
        "/森空岛定时 <时间> —— 设置自动签到时间（仅超级管理员，如：6点30分）\n"
        "/森空岛签到信息 —— 查看所有用户签到状态（仅超级管理员）\n"
        "/森空岛自动签到测试 —— 立即执行一次自动签到（仅超级管理员）\n\n"
        "也可以直接对机器人说「森空岛怎么用 / 帮我绑定 / 帮我签到」，机器人会调用对应工具处理。"
    )


# ==================== 配置模型 ====================


class PluginSectionConfig(PluginConfigBase):
    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="1.0.0", description="配置版本")


class AutoSignSectionConfig(PluginConfigBase):
    __ui_label__ = "自动签到"
    __ui_icon__ = "alarm"
    __ui_order__ = 1

    time: str = Field(default="08:00", description="每日自动签到时间（北京时间 HH:MM）")


class TokenSectionConfig(PluginConfigBase):
    __ui_label__ = "Token 获取"
    __ui_icon__ = "link"
    __ui_order__ = 2

    get_url: str = Field(
        default=TOKEN_URL,
        description="森空岛 token 获取链接（浏览器打开后跳转登录，点击帖子内链接即可获取 token）",
    )


class AdminSectionConfig(PluginConfigBase):
    __ui_label__ = "管理员"
    __ui_icon__ = "shield"
    __ui_order__ = 3

    super_admins: list[str] = Field(default_factory=list, description="超级管理员 QQ 号列表（可执行管理指令）")


class SklandSignConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    auto_sign: AutoSignSectionConfig = Field(default_factory=AutoSignSectionConfig)
    token: TokenSectionConfig = Field(default_factory=TokenSectionConfig)
    admin: AdminSectionConfig = Field(default_factory=AdminSectionConfig)


# ==================== 用户数据管理 ====================


class UserDataManager:
    """管理每个用户的 token 与自动签到开关。

    文件格式（users.json）：
    {
      "123456789": {"token": "...", "auto_sign": true}
    }
    兼容旧版（amiyabot 时代）格式：纯字符串 value 视为 token，
    dict 仅保留 token / auto_sign 字段。
    """

    def __init__(self, data_file: str):
        self.data_file = data_file
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self.load()

    def load(self) -> None:
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                for uid, value in raw.items():
                    if isinstance(value, str):
                        self._data[str(uid)] = {"token": value, "auto_sign": False}
                    elif isinstance(value, dict):
                        self._data[str(uid)] = {
                            "token": value.get("token"),
                            "auto_sign": bool(value.get("auto_sign", False)),
                        }
            except Exception:
                self._data = {}
        else:
            self._data = {}

    async def save(self) -> None:
        async with self._lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._save_sync)

    def _save_sync(self) -> None:
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        with open(self.data_file, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, user_id: str) -> Dict[str, Any]:
        return self._data.get(str(user_id), {"token": None, "auto_sign": False})

    async def set_token(self, user_id: str, token: str) -> None:
        uid = str(user_id)
        data = self.get(uid)
        data["token"] = token
        data["auto_sign"] = bool(data.get("auto_sign", False))
        self._data[uid] = data
        await self.save()

    async def set_auto_sign(self, user_id: str, enabled: bool) -> None:
        uid = str(user_id)
        data = self.get(uid)
        data["auto_sign"] = bool(enabled)
        self._data[uid] = data
        await self.save()

    async def delete(self, user_id: str) -> None:
        uid = str(user_id)
        if uid in self._data:
            del self._data[uid]
            await self.save()

    def all_user_ids(self) -> List[str]:
        return list(self._data.keys())


# ==================== 工具函数 ====================


def extract_token_from_text(text: str) -> Optional[str]:
    """从用户输入中提取 token（支持完整 JSON 或纯 Base64 字符串）。"""
    text = (text or "").strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and data.get("code") == 0:
            content = data.get("data", {}).get("content")
            if content and isinstance(content, str):
                return re.sub(r"[^a-zA-Z0-9+/=]", "", content)
    except json.JSONDecodeError:
        pass
    return re.sub(r"[^a-zA-Z0-9+/=]", "", text) or None


def parse_time_of_day(text: str) -> Optional[datetime]:
    """解析用户输入的时间字符串，返回今天/明天的 datetime（北京时间）。"""
    pattern = r"(?:点|:|\：)?\s*(\d{1,2})\s*(?:点|:|\：)?\s*(\d{1,2})?\s*分?"
    match = re.search(pattern, text or "")
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    now = datetime.now(TZ)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def calculate_next_run(target_time_str: str, base: Optional[datetime] = None) -> int:
    """根据目标时间字符串和基准时间计算下一次执行的时间戳（北京时间）。"""
    if base is None:
        base = datetime.now(TZ)
    hour, minute = map(int, target_time_str.split(":"))
    target = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= base:
        target += timedelta(days=1)
    return int(target.timestamp())


def format_sign_results(results: List[Any]) -> List[str]:
    """把 do_full_sign_in 的结果格式化为文本行（[OK]/[X]/[SKIP] 标记）。"""
    lines: List[str] = []
    for r in results:
        if r.success:
            awards = "，".join(r.awards) if r.awards else "无奖励"
            lines.append(f"[OK] {r.game}：{r.nickname}（获得：{awards}）")
        else:
            err_lower = (r.error or "").lower()
            if any(k in err_lower for k in ("已签到", "重复", "请勿重复", "今日已")):
                lines.append(f"[SKIP] {r.game}：{r.nickname}（今日已签到）")
            else:
                lines.append(f"[X] {r.game}：{r.nickname}（签到失败：{r.error}）")
    return lines


def to_bool(value: Any) -> bool:
    """把 LLM 传入的布尔参数规范化为 bool（兼容字符串 "true"/"false"）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "是", "对")
    return bool(value)


# ==================== 插件主体 ====================


class SklandSignPlugin(MaiBotPlugin):
    """森空岛自动签到插件。"""

    config_model = SklandSignConfig

    def __init__(self) -> None:
        super().__init__()
        self._plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self._user_manager: Optional[UserDataManager] = None
        self._next_run: Optional[int] = None
        self._scheduler_task: Optional[asyncio.Task] = None

    # ==================== 生命周期 ====================

    def _get_data_dir(self) -> str:
        """统一持久化数据目录：data/plugins/cateye_skland_sign。

        遵守官方建议：数据存放于统一持久化根目录 data/plugins/ 下。
        ctx.paths.data_dir 默认是 data/plugins/<plugin_id>（github.cateye.skland.sign），
        这里固定使用与项目文件夹同名的子文件夹 cateye_skland_sign。
        """
        plugins_root = os.path.dirname(str(self.ctx.paths.data_dir))  # .../data/plugins
        return os.path.join(plugins_root, DATA_DIR_NAME)

    async def on_load(self) -> None:
        data_dir = self._get_data_dir()
        os.makedirs(data_dir, exist_ok=True)
        self._migrate_legacy_data(data_dir)

        user_data_file = os.path.join(data_dir, "users.json")
        self._user_manager = UserDataManager(user_data_file)
        self._next_run = self._load_next_run(data_dir)
        self._start_scheduler()
        self.ctx.logger.info(
            "森空岛签到插件已加载，自动签到时间：%s", self._get_auto_sign_time()
        )

    async def on_unload(self) -> None:
        self._stop_scheduler()
        self.ctx.logger.info("森空岛签到插件已卸载")

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        del config_data, version
        if scope == "self":
            # 配置热重载：重启调度器以应用新的自动签到时间
            self._stop_scheduler()
            self._next_run = None
            self._start_scheduler()
            self.ctx.logger.info("森空岛签到插件配置已更新，自动签到时间：%s", self._get_auto_sign_time())

    # ==================== 数据迁移 ====================

    def _migrate_legacy_data(self, data_dir: str) -> None:
        """把旧数据迁移到统一持久化目录（data/plugins/cateye_skland_sign）。

        官方建议：插件数据必须存放于统一持久化目录 data/plugins/ 下，
        禁止使用插件目录下旧式 data/ 目录。迁移来源（按优先级）：
        1. 按插件 ID（github.cateye.skland.sign）派生的统一持久化目录（Host 可能已按 ID 迁移）；
        2. 旧插件 ID（maibot-community.skland-sign）对应的统一持久化目录（历史升级场景）；
        3. 插件目录下旧式 data/users.json（amiyabot 时代遗留）。
        """
        target = os.path.join(data_dir, "users.json")
        if os.path.exists(target):
            return

        plugins_root = os.path.dirname(data_dir)
        sources: List[str] = []
        for old_id in ("github.cateye.skland.sign", "maibot-community.skland-sign"):
            try:
                sources.append(os.path.join(plugins_root, old_id, "users.json"))
            except Exception:
                pass
        sources.append(os.path.join(self._plugin_dir, "data", "users.json"))

        for src in sources:
            if os.path.exists(src):
                try:
                    os.makedirs(data_dir, exist_ok=True)
                    import shutil

                    shutil.copy2(src, target)
                    self.ctx.logger.info("已迁移旧版用户数据：%s -> %s", src, target)
                    return
                except Exception as e:
                    self.ctx.logger.warning("旧版用户数据迁移失败：%s", e)

    # ==================== 定时调度 ====================

    def _start_scheduler(self) -> None:
        if not self.config.plugin.enabled:
            self.ctx.logger.info("插件已禁用，不启动自动签到调度器")
            return
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    def _stop_scheduler(self) -> None:
        if self._scheduler_task:
            self._scheduler_task.cancel()
            self._scheduler_task = None

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                await self._check_and_run()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.ctx.logger.error("自动签到调度异常：%s", e)
            await asyncio.sleep(60)

    async def _check_and_run(self) -> None:
        data_dir = self._get_data_dir()
        if self._next_run is None:
            self._next_run = self._load_next_run(data_dir)
        if self._next_run is None:
            self._next_run = calculate_next_run(self._get_auto_sign_time())
            self._save_next_run(data_dir, self._next_run)

        now = int(datetime.now(TZ).timestamp())
        if now < self._next_run:
            return

        self.ctx.logger.info(
            "到达自动签到时间 %s，开始为已开启自动签到的用户签到",
            datetime.fromtimestamp(self._next_run, tz=TZ).strftime("%Y-%m-%d %H:%M"),
        )
        try:
            await self._perform_auto_sign()
        except Exception as e:
            self.ctx.logger.error("自动签到执行异常：%s", e)
            return  # 异常时不更新时间，等待下次循环重试

        self._next_run = calculate_next_run(self._get_auto_sign_time())
        self._save_next_run(data_dir, self._next_run)
        self.ctx.logger.info(
            "下次自动签到时间已更新为 %s",
            datetime.fromtimestamp(self._next_run, tz=TZ).strftime("%Y-%m-%d %H:%M"),
        )

    def _load_next_run(self, data_dir: str) -> Optional[int]:
        next_file = os.path.join(data_dir, "next_sign.json")
        if os.path.exists(next_file):
            try:
                with open(next_file, "r", encoding="utf-8") as f:
                    return json.load(f).get("next_run")
            except Exception:
                pass
        return None

    def _save_next_run(self, data_dir: str, timestamp: int) -> None:
        next_file = os.path.join(data_dir, "next_sign.json")
        try:
            with open(next_file, "w", encoding="utf-8") as f:
                json.dump({"next_run": timestamp}, f)
        except Exception as e:
            self.ctx.logger.warning("保存下次签到时间失败：%s", e)

    def _get_auto_sign_time(self) -> str:
        """读取自动签到时间；优先 auto_sign.time，兼容旧版 auto_sign_hour 配置。"""
        time_str = self.config.auto_sign.time
        if time_str and isinstance(time_str, str) and re.match(r"\d{1,2}:\d{2}", time_str):
            return time_str
        raw = self.get_plugin_config_data()
        legacy_hour = raw.get("auto_sign_hour") if isinstance(raw, Mapping) else None
        if isinstance(legacy_hour, int) and 0 <= legacy_hour <= 23:
            return f"{legacy_hour:02d}:00"
        return "08:00"

    def _get_token_url(self) -> str:
        """读取 token 获取链接（配置 [token] get_url，允许用户自定义，默认 TOKEN_URL）。"""
        url = self.config.token.get_url
        if url and isinstance(url, str) and url.strip():
            return url.strip()
        return TOKEN_URL

    def _persist_auto_sign_time(self, time_str: str) -> bool:
        """把 auto_sign.time 写回插件目录下的 config.toml，交由 Runner 热重载。

        返回是否写入成功；失败时仍可通过内存配置 + next_sign.json 在本会话生效。
        """
        config_path = os.path.join(self._plugin_dir, "config.toml")
        try:
            import tomlkit

            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    doc = tomlkit.parse(f.read())
            else:
                doc = tomlkit.document()
            auto_sign = doc.setdefault("auto_sign", tomlkit.table())
            auto_sign["time"] = time_str
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(tomlkit.dumps(doc))
            return True
        except Exception as e:
            self.ctx.logger.warning("写入 config.toml 失败（不影响本次生效）：%s", e)
            return False

    async def _perform_auto_sign(self) -> None:
        """为所有开启自动签到的用户执行签到，仅记录日志，不推送消息。"""
        assert self._user_manager is not None
        for user_id in self._user_manager.all_user_ids():
            user_data = self._user_manager.get(user_id)
            if not user_data.get("auto_sign"):
                continue
            token = user_data.get("token")
            if not token:
                continue

            api = SklandAPI()
            try:
                results, _ = await api.do_full_sign_in(token)
                if not results:
                    self.ctx.logger.info("[森空岛] 用户 %s 无游戏账号", user_id)
                    continue
                for line in format_sign_results(results):
                    self.ctx.logger.info("[森空岛] 用户 %s %s", user_id, line)
            except Exception as e:
                self.ctx.logger.warning("[森空岛] 用户 %s 自动签到异常：%s", user_id, e)
            finally:
                await api.close()

    # ==================== 工具函数（消息/用户解析） ====================

    def _extract_user_id(self, message: Any) -> str:
        """从 Host 消息字典中提取发送者 QQ 号（兼容多适配器消息结构）。"""
        if not isinstance(message, Mapping):
            return ""
        message_info = message.get("message_info")
        if isinstance(message_info, Mapping):
            user_info = message_info.get("user_info")
            if isinstance(user_info, Mapping):
                uid = str(user_info.get("user_id") or "").strip()
                if uid:
                    return uid
        # 兜底路径
        sender = message.get("sender")
        if isinstance(sender, Mapping):
            uid = str(sender.get("user_id") or "").strip()
            if uid:
                return uid
        return str(message.get("user_id") or "").strip()

    def _is_super_admin(self, user_id: str) -> bool:
        admins = self.config.admin.super_admins
        return str(user_id) in [str(a) for a in admins]

    # ==================== 发送辅助 ====================

    async def _send_forward_or_text(self, stream_id: str, title: str, lines: List[str]) -> None:
        """长内容优先合并转发，失败则回退为普通文本。"""
        if len(lines) <= 1:
            await self.ctx.send.text("\n".join(lines) if lines else "（空）", stream_id)
            return
        try:
            messages = [
                {
                    "user_id": "0",
                    "nickname": title,
                    "segments": [{"type": "text", "content": line}],
                }
                for line in lines
            ]
            await self.ctx.send.forward(messages, stream_id)
        except Exception as e:
            self.ctx.logger.warning("合并转发失败，回退为普通文本：%s", e)
            text = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))
            await self.ctx.send.text(text, stream_id)

    # ==================== 指令 ====================

    @Command(
        "skland_help_command",
        description="森空岛签到帮助（含 token 获取教程与链接）",
        pattern=r"^/?森空岛(?:帮助)?$",
    )
    async def cmd_skland_help(self, **kwargs: Any) -> tuple[bool, str, int]:
        stream_id = str(kwargs.get("stream_id") or "")
        # 指令触发：固定回复（token 获取教程 + 指令列表），链接取自配置
        await self.ctx.send.text(build_help_text(self._get_token_url()), stream_id)
        return True, "帮助信息已发送", 1

    @Command(
        "skland_bind",
        description="绑定森空岛 token",
        pattern=r"^/?森空岛绑定\s*(?P<token>[\s\S]*)$",
    )
    async def cmd_skland_bind(self, **kwargs: Any) -> tuple[bool, str, int]:
        matched = kwargs.get("matched_groups", {})
        token_part = str(matched.get("token") or "").strip()
        if not token_part:
            await self.ctx.send.text("请提供 token，格式：/森空岛绑定 <token>", str(kwargs.get("stream_id") or ""))
            return False, "缺少 token 参数", 1

        token = extract_token_from_text(token_part)
        if not token:
            await self.ctx.send.text("无法提取有效 token，请检查输入内容", str(kwargs.get("stream_id") or ""))
            return False, "token 无效", 1

        user_id = self._extract_user_id(kwargs.get("message", {}))
        if not user_id:
            await self.ctx.send.text("无法获取你的用户标识，请重试", str(kwargs.get("stream_id") or ""))
            return False, "无法获取 user_id", 1

        api = SklandAPI()
        try:
            auth_code = await api.get_authorization(token)
            cred = await api.get_credential(auth_code)
            bindings = await api.get_binding_list(cred)
            nickname = bindings[0].nickname if bindings else "未知用户"
            assert self._user_manager is not None
            await self._user_manager.set_token(user_id, token)
            await self.ctx.send.text(
                f"绑定成功！昵称：{nickname}\n可使用「森空岛签到」或「森空岛自动签到」。",
                str(kwargs.get("stream_id") or ""),
            )
            return True, f"绑定成功：{nickname}", 2
        except Exception as e:
            await self.ctx.send.text(f"绑定失败：{e}", str(kwargs.get("stream_id") or ""))
            return False, f"绑定失败：{e}", 1
        finally:
            await api.close()

    @Command(
        "skland_unbind",
        description="解绑森空岛 token",
        pattern=r"^/?森空岛解绑\s*$",
    )
    async def cmd_skland_unbind(self, **kwargs: Any) -> tuple[bool, str, int]:
        user_id = self._extract_user_id(kwargs.get("message", {}))
        assert self._user_manager is not None
        if user_id:
            await self._user_manager.delete(user_id)
        await self.ctx.send.text("解绑成功，已删除你的所有数据", str(kwargs.get("stream_id") or ""))
        return True, "解绑成功", 1

    @Command(
        "skland_sign",
        description="手动执行森空岛签到",
        pattern=r"^/?森空岛签到$",
    )
    async def cmd_skland_sign(self, **kwargs: Any) -> tuple[bool, str, int]:
        user_id = self._extract_user_id(kwargs.get("message", {}))
        assert self._user_manager is not None
        user_data = self._user_manager.get(user_id)
        token = user_data.get("token")
        if not token:
            await self.ctx.send.text("您还未绑定 token，请使用「/森空岛绑定 <token>」", str(kwargs.get("stream_id") or ""))
            return False, "未绑定 token", 1

        api = SklandAPI()
        try:
            results, _ = await api.do_full_sign_in(token)
            if not results:
                await self.ctx.send.text("暂未查询到已绑定的游戏账号", str(kwargs.get("stream_id") or ""))
                return True, "无游戏账号", 1
            lines = format_sign_results(results)
            await self.ctx.send.text("\n".join(lines), str(kwargs.get("stream_id") or ""))
            return True, "\n".join(lines), 2
        except Exception as e:
            await self.ctx.send.text(f"签到失败：{e}", str(kwargs.get("stream_id") or ""))
            return False, f"签到失败：{e}", 1
        finally:
            await api.close()

    @Command(
        "skland_status",
        description="查看今日森空岛签到状态",
        pattern=r"^/?森空岛状态\s*$",
    )
    async def cmd_skland_status(self, **kwargs: Any) -> tuple[bool, str, int]:
        user_id = self._extract_user_id(kwargs.get("message", {}))
        assert self._user_manager is not None
        user_data = self._user_manager.get(user_id)
        token = user_data.get("token")
        if not token:
            await self.ctx.send.text("您还未绑定 token，请使用「/森空岛绑定 <token>」", str(kwargs.get("stream_id") or ""))
            return False, "未绑定 token", 1

        api = SklandAPI()
        try:
            status, nickname = await api.check_sign_in_status(token)
            auto_sign = "已开启" if user_data.get("auto_sign") else "未开启"
            text = (
                f"用户：{nickname or user_id}\n"
                f"自动签到：{auto_sign}（每日 {self._get_auto_sign_time()}）\n"
                f"明日方舟：{'今日已签到' if status.get('arknights') else '今日未签到'}\n"
                f"终末地：{'今日已签到' if status.get('endfield') else '今日未签到'}"
            )
            await self.ctx.send.text(text, str(kwargs.get("stream_id") or ""))
            return True, text, 2
        except Exception as e:
            await self.ctx.send.text(f"查询失败：{e}", str(kwargs.get("stream_id") or ""))
            return False, f"查询失败：{e}", 1
        finally:
            await api.close()

    @Command(
        "skland_auto_sign_command",
        description="开启 / 关闭自动签到",
        pattern=r"^/?森空岛自动签到\s*$",
    )
    async def cmd_skland_auto_sign(self, **kwargs: Any) -> tuple[bool, str, int]:
        user_id = self._extract_user_id(kwargs.get("message", {}))
        assert self._user_manager is not None
        user_data = self._user_manager.get(user_id)
        token = user_data.get("token")
        if not token:
            await self.ctx.send.text("您还未绑定 token，请先使用「/森空岛绑定 <token>」", str(kwargs.get("stream_id") or ""))
            return False, "未绑定 token", 1

        new_state = not bool(user_data.get("auto_sign", False))
        await self._user_manager.set_auto_sign(user_id, new_state)
        status = "已启用" if new_state else "已关闭"
        await self.ctx.send.text(
            f"【自动签到 {status}】\n每日 {self._get_auto_sign_time()} 自动签到（结果仅记录日志，不推送）",
            str(kwargs.get("stream_id") or ""),
        )
        return True, f"自动签到{status}", 2

    @Command(
        "skland_set_time",
        description="设置每日自动签到时间（仅超级管理员）",
        pattern=r"^/?森空岛定时\s*(?P<time>[\s\S]*)$",
    )
    async def cmd_skland_set_time(self, **kwargs: Any) -> tuple[bool, str, int]:
        user_id = self._extract_user_id(kwargs.get("message", {}))
        if not self._is_super_admin(user_id):
            await self.ctx.send.text("你没有权限执行此操作", str(kwargs.get("stream_id") or ""))
            return False, "无权限", 1

        matched = kwargs.get("matched_groups", {})
        time_part = str(matched.get("time") or "").strip()
        if not time_part:
            await self.ctx.send.text("请指定时间，例如：/森空岛定时 6点30分", str(kwargs.get("stream_id") or ""))
            return False, "缺少时间参数", 1

        target_dt = parse_time_of_day(time_part)
        if not target_dt:
            await self.ctx.send.text("时间格式无法识别，请使用「6点30分」「6:30」或「6：30」", str(kwargs.get("stream_id") or ""))
            return False, "时间格式错误", 1

        time_str = target_dt.strftime("%H:%M")
        # 持久化：写回 config.toml（Runner 会热重载并触发 on_config_update）
        self._persist_auto_sign_time(time_str)
        # 内存立即生效：更新原始配置并让 self.config 同步
        raw = self.get_plugin_config_data()
        raw.setdefault("auto_sign", {})["time"] = time_str
        self.set_plugin_config(raw)
        next_run = calculate_next_run(time_str)
        self._next_run = next_run
        self._save_next_run(self._get_data_dir(), next_run)

        await self.ctx.send.text(
            f"自动签到时间已设置为每日 {time_str}\n"
            f"下一次执行将在 {datetime.fromtimestamp(next_run, tz=TZ).strftime('%Y-%m-%d %H:%M')}",
            str(kwargs.get("stream_id") or ""),
        )
        return True, f"自动签到时间已设置为 {time_str}", 2

    @Command(
        "skland_sign_info",
        description="查看所有用户签到状态（仅超级管理员，合并转发）",
        pattern=r"^/?森空岛签到信息\s*$",
    )
    async def cmd_skland_sign_info(self, **kwargs: Any) -> tuple[bool, str, int]:
        user_id = self._extract_user_id(kwargs.get("message", {}))
        if not self._is_super_admin(user_id):
            await self.ctx.send.text("你没有权限执行此操作", str(kwargs.get("stream_id") or ""))
            return False, "无权限", 1

        assert self._user_manager is not None
        user_ids = self._user_manager.all_user_ids()
        if not user_ids:
            await self.ctx.send.text("暂无已绑定的用户", str(kwargs.get("stream_id") or ""))
            return True, "暂无用户", 1

        lines: List[str] = []
        for uid in user_ids:
            user_data = self._user_manager.get(uid)
            token = user_data.get("token")
            auto_sign = "自动" if user_data.get("auto_sign") else "手动"
            if not token:
                lines.append(f"用户 {uid}：未绑定 token")
                continue
            api = SklandAPI()
            try:
                results, nickname = await api.do_full_sign_in(token)
                if not results:
                    lines.append(f"用户 {nickname or uid}（{auto_sign}）：无游戏账号")
                else:
                    status_lines = []
                    for r in results:
                        if r.success:
                            status_lines.append(f"{r.game}：成功（{'，'.join(r.awards) if r.awards else '无奖励'}）")
                        else:
                            err_lower = (r.error or "").lower()
                            if any(k in err_lower for k in ("已签到", "重复", "请勿重复", "今日已")):
                                status_lines.append(f"{r.game}：今日已签到")
                            else:
                                status_lines.append(f"{r.game}：失败（{r.error}）")
                    lines.append(f"用户 {nickname or uid}（{auto_sign}）：{'；'.join(status_lines)}")
            except Exception as e:
                lines.append(f"用户 {uid}（{auto_sign}）：查询失败 {e}")
            finally:
                await api.close()

        await self._send_forward_or_text(str(kwargs.get("stream_id") or ""), "森空岛签到状态", lines)
        return True, "签到信息已发送", 2

    @Command(
        "skland_auto_sign_test",
        description="立即执行一次自动签到（仅超级管理员）",
        pattern=r"^/?森空岛自动签到测试\s*$",
    )
    async def cmd_skland_auto_sign_test(self, **kwargs: Any) -> tuple[bool, str, int]:
        user_id = self._extract_user_id(kwargs.get("message", {}))
        if not self._is_super_admin(user_id):
            await self.ctx.send.text("你没有权限执行此操作", str(kwargs.get("stream_id") or ""))
            return False, "无权限", 1

        await self._perform_auto_sign()
        await self.ctx.send.text("自动签到测试已执行，请查看控制台日志", str(kwargs.get("stream_id") or ""))
        return True, "自动签到测试已执行", 1

    # ==================== LLM 工具 ====================

    @Tool(
        "skland_help",
        brief_description="森空岛签到插件的使用帮助与 token 获取链接",
        detailed_description=(
            "当用户询问森空岛签到如何使用、如何绑定 token、如何获取 token、有哪些指令时调用本工具。\n"
            "本工具调用时会独立向当前对话发送一条只含 token 获取链接的消息（链接取自配置 [token] get_url）；"
            "同时会把链接、链接作用以及指令说明返回给你（大模型），请由你根据返回内容自行组织回复。\n"
            "token 获取方式：用浏览器打开链接会跳转到森空岛登录页，登录后点击帖子中的链接即可获取绑定 token。"
        ),
    )
    async def tool_skland_help(self, **kwargs: Any) -> Dict[str, Any]:
        token_url = self._get_token_url()
        stream_id = str(kwargs.get("stream_id") or "")

        # 独立构造一条信息：只发送 token 获取链接（无其他文字）
        link_sent = False
        if stream_id:
            try:
                link_sent = bool(await self.ctx.send.text(token_url, stream_id))
            except Exception as e:
                self.ctx.logger.warning("单独发送 token 获取链接失败：%s", e)
        else:
            self.ctx.logger.debug("skland_help 工具调用缺少 stream_id，无法独立发送链接")

        link_note = "已单独向当前对话发送一条只含链接的消息" if link_sent else "未能独立发送链接（缺少对话上下文），请直接把链接告知用户"
        return {
            "success": True,
            "content": (
                "森空岛 token 获取链接：{url}\n"
                "链接作用：{purpose}\n"
                "{link_note}。\n"
                "获取步骤：使用浏览器打开链接 → 跳转到森空岛登录并登录 → "
                "点击帖子中的链接即可获取 token。\n\n"
                "可用指令：\n"
                "/森空岛绑定 <token> —— 绑定 token\n"
                "/森空岛签到 —— 手动签到\n"
                "/森空岛状态 —— 查看今日签到状态\n"
                "/森空岛自动签到 —— 开启 / 关闭自动签到"
            ).format(url=token_url, purpose=TOKEN_URL_PURPOSE, link_note=link_note),
            "token_url": token_url,
            "token_url_purpose": TOKEN_URL_PURPOSE,
            "link_sent_to_user": link_sent,
        }

    @Tool(
        "skland_bind_token",
        brief_description="为用户绑定森空岛 token",
        detailed_description=(
            "当用户提供森空岛 token 并要求绑定（或要求开通签到）时调用。\n"
            "参数说明：\n"
            "- user_id：string，必填。要绑定 token 的用户 QQ 号。\n"
            "- token：string，必填。用户提供的森空岛 token，可以是完整 JSON "
            '（如 {"code":0,"data":{"content":"..."}}）或纯 Base64 字符串。'
        ),
        parameters=[
            ToolParameterInfo(
                name="user_id",
                param_type=ToolParamType.STRING,
                description="要绑定 token 的用户 QQ 号",
                required=True,
            ),
            ToolParameterInfo(
                name="token",
                param_type=ToolParamType.STRING,
                description="森空岛 token（完整 JSON 或纯 Base64 字符串）",
                required=True,
            ),
        ],
    )
    async def tool_skland_bind_token(self, user_id: str = "", token: str = "", **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        uid = str(user_id or "").strip()
        token_text = str(token or "").strip()
        if not uid:
            return {"success": False, "error": "缺少 user_id 参数"}
        if not token_text:
            return {"success": False, "error": "缺少 token 参数"}

        real_token = extract_token_from_text(token_text)
        if not real_token:
            return {"success": False, "error": "无法提取有效 token，请检查输入内容"}

        api = SklandAPI()
        try:
            auth_code = await api.get_authorization(real_token)
            cred = await api.get_credential(auth_code)
            bindings = await api.get_binding_list(cred)
            nickname = bindings[0].nickname if bindings else "未知用户"
            assert self._user_manager is not None
            await self._user_manager.set_token(uid, real_token)
            return {
                "success": True,
                "content": f"用户 {uid} 绑定成功，昵称：{nickname}。可回复用户已绑定成功，并提示可使用自动签到。",
                "user_id": uid,
                "nickname": nickname,
            }
        except Exception as e:
            return {"success": False, "error": f"绑定失败：{e}"}
        finally:
            await api.close()

    @Tool(
        "skland_sign_in",
        brief_description="为用户执行森空岛签到",
        detailed_description=(
            "当用户明确要求执行森空岛签到（如「帮我签到」「森空岛签到」）时调用。\n"
            "参数说明：\n"
            "- user_id：string，必填。要执行签到的用户 QQ 号。"
        ),
        parameters=[
            ToolParameterInfo(
                name="user_id",
                param_type=ToolParamType.STRING,
                description="要执行签到的用户 QQ 号",
                required=True,
            ),
        ],
    )
    async def tool_skland_sign_in(self, user_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        uid = str(user_id or "").strip()
        if not uid:
            return {"success": False, "error": "缺少 user_id 参数"}
        assert self._user_manager is not None
        user_data = self._user_manager.get(uid)
        token = user_data.get("token")
        if not token:
            return {"success": False, "error": f"用户 {uid} 还未绑定 token，请先让用户绑定"}

        api = SklandAPI()
        try:
            results, nickname = await api.do_full_sign_in(token)
            if not results:
                return {"success": True, "content": f"用户 {uid} 暂未查询到已绑定的游戏账号", "user_id": uid}
            lines = format_sign_results(results)
            return {
                "success": True,
                "content": f"用户 {nickname or uid} 的签到结果：\n" + "\n".join(lines),
                "user_id": uid,
                "nickname": nickname,
                "results": [
                    {
                        "game": r.game,
                        "nickname": r.nickname,
                        "success": r.success,
                        "awards": r.awards,
                        "error": r.error,
                    }
                    for r in results
                ],
            }
        except Exception as e:
            return {"success": False, "error": f"签到失败：{e}"}
        finally:
            await api.close()

    @Tool(
        "skland_sign_status",
        brief_description="查询用户今日森空岛签到状态",
        detailed_description=(
            "当用户询问今日是否已签到、签到状态、绑定信息时调用。\n"
            "参数说明：\n"
            "- user_id：string，必填。要查询的用户 QQ 号。"
        ),
        parameters=[
            ToolParameterInfo(
                name="user_id",
                param_type=ToolParamType.STRING,
                description="要查询的用户 QQ 号",
                required=True,
            ),
        ],
    )
    async def tool_skland_sign_status(self, user_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        uid = str(user_id or "").strip()
        if not uid:
            return {"success": False, "error": "缺少 user_id 参数"}
        assert self._user_manager is not None
        user_data = self._user_manager.get(uid)
        token = user_data.get("token")
        if not token:
            return {"success": False, "error": f"用户 {uid} 还未绑定 token"}

        api = SklandAPI()
        try:
            status, nickname = await api.check_sign_in_status(token)
            auto_sign = "已开启" if user_data.get("auto_sign") else "未开启"
            content = (
                f"用户：{nickname or uid}\n"
                f"自动签到：{auto_sign}（每日 {self._get_auto_sign_time()}）\n"
                f"明日方舟：{'今日已签到' if status.get('arknights') else '今日未签到'}\n"
                f"终末地：{'今日已签到' if status.get('endfield') else '今日未签到'}"
            )
            return {
                "success": True,
                "content": content,
                "user_id": uid,
                "nickname": nickname,
                "auto_sign_enabled": bool(user_data.get("auto_sign")),
                "status": status,
            }
        except Exception as e:
            return {"success": False, "error": f"查询失败：{e}"}
        finally:
            await api.close()

    @Tool(
        "skland_auto_sign",
        brief_description="开启或关闭用户的森空岛每日自动签到",
        detailed_description=(
            "当用户要求开启或关闭自动签到（如「帮我开启自动签到」）时调用。\n"
            "参数说明：\n"
            "- user_id：string，必填。目标用户 QQ 号。\n"
            "- enable：boolean，必填。true 开启自动签到，false 关闭。"
        ),
        parameters=[
            ToolParameterInfo(
                name="user_id",
                param_type=ToolParamType.STRING,
                description="目标用户 QQ 号",
                required=True,
            ),
            ToolParameterInfo(
                name="enable",
                param_type=ToolParamType.BOOLEAN,
                description="true 开启自动签到，false 关闭",
                required=True,
            ),
        ],
    )
    async def tool_skland_auto_sign(self, user_id: str = "", enable: bool = False, **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        uid = str(user_id or "").strip()
        if not uid:
            return {"success": False, "error": "缺少 user_id 参数"}
        assert self._user_manager is not None
        user_data = self._user_manager.get(uid)
        token = user_data.get("token")
        if not token:
            return {"success": False, "error": f"用户 {uid} 还未绑定 token，无法开启自动签到"}

        await self._user_manager.set_auto_sign(uid, to_bool(enable))
        state = "已开启" if to_bool(enable) else "已关闭"
        return {
            "success": True,
            "content": f"用户 {uid} 的自动签到{state}（每日 {self._get_auto_sign_time()} 执行，结果仅记录日志）",
            "user_id": uid,
            "auto_sign_enabled": to_bool(enable),
        }


def create_plugin() -> SklandSignPlugin:
    return SklandSignPlugin()
