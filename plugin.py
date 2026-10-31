"""森空岛（Skland）自动签到插件 — MaiBot v2 插件

功能：
- 用户通过指令或 LLM 工具绑定森空岛 token（支持明日方舟 / 终末地），一个 QQ 可绑定多个森空岛账号。
- 手动签到、查看签到状态、查看账号列表、按序号解绑。
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
from typing import Any, ClassVar, Dict, List, Mapping, Optional

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .admin_util import collect_admins, plain_id
from .skland_api import SklandAPI

# ==================== 常量 ====================

TZ = timezone(timedelta(hours=8))

# 配置版本（config_version）：与 _manifest.json 的 version 保持同步。
# config_version 用于检查配置文件（config.toml）是否需要更新：
# 插件升级后若配置结构发生变化，可对比该值触发配置迁移/重建。
SUPPORTED_CONFIG_VERSION = "1.2.0"

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


# token 等同于登录凭证：只有被识别为指令的消息不会进入大模型上下文，所以引导用户私聊、单独发指令
BIND_NOTICE = (
    "绑定注意：token 等同于账号登录凭证。\n"
    "请私聊机器人，单独发一条「/森空岛绑定 <token>」，前面不要加别的字，不要用回复或引用；"
    "不要在群里发，也不要转发含 token 的聊天记录，否则 token 会被群友看到，或作为聊天内容发给大模型。"
)


def build_help_text(token_url: str) -> str:
    """构造帮助/固定回复文案（链接来自配置）。"""
    return (
        build_token_tutorial(token_url)
        + "\n\n"
        + BIND_NOTICE
        + "\n\n可用指令：\n"
        "/森空岛 或 /森空岛帮助 —— 显示帮助与 token 获取教程\n"
        "/森空岛绑定 <token> —— 绑定 token（支持完整 JSON 或纯 Base64 字符串；多个账号分别绑定即可）\n"
        "/森空岛账号 —— 查看已绑定的账号及序号\n"
        "/森空岛解绑 [序号|全部] —— 解绑账号（只绑了一个时可省略序号）\n"
        "/森空岛签到 —— 为名下全部账号手动签到\n"
        "/森空岛状态 —— 查看名下全部账号的今日签到状态\n"
        "/森空岛自动签到 —— 开启 / 关闭自动签到（对名下全部账号生效）\n"
        "/森空岛定时 <时间> —— 设置自动签到时间（仅超级管理员，如：6点30分）\n"
        "/森空岛签到信息 —— 查看所有用户签到状态（仅超级管理员）\n"
        "/森空岛自动签到测试 —— 立即执行一次自动签到（仅超级管理员）\n\n"
        "也可以直接对机器人说「森空岛怎么用 / 帮我签到」，机器人会调用对应工具处理；"
        "绑定请用上面的指令，不要在对话里直接发 token。"
    )


# ==================== 配置模型 ====================


def _ui_i18n(en_label: str, en_hint: str = "") -> Dict[str, Any]:
    """字段级英文翻译（并入 json_schema_extra；WebUI 按 i18n[locale]['label'/'hint'] 取用）。"""
    entry: Dict[str, str] = {"label": en_label}
    if en_hint:
        entry["hint"] = en_hint
    return {"i18n": {"en": entry}}


class PluginSectionConfig(PluginConfigBase):
    """插件自身配置（plugin 配置节）。"""

    __ui_label__ = "插件"
    __ui_i18n__: ClassVar[Dict[str, Dict[str, str]]] = {
        "en": {"title": "Plugin", "description": "Plugin master switch."}
    }
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(
        default=True,
        description="是否启用插件",
        json_schema_extra={
            "label": "启用插件",
            "hint": "插件总开关",
            **_ui_i18n("Enable plugin", "Master switch of the plugin."),
        },
    )
    config_version: str = Field(
        default=SUPPORTED_CONFIG_VERSION,
        description="配置版本（与插件版本同步，用于检查配置文件是否需要更新）",
        json_schema_extra={
            "disabled": True,
            "hidden": True,
            "label": "配置版本",
            "hint": "配置版本，勿改",
            **_ui_i18n("Config version", "Internal config version. Do not edit."),
        },
    )


class AutoSignSectionConfig(PluginConfigBase):
    """自动签到设置（auto_sign 配置节）。"""

    __ui_label__ = "自动签到"
    __ui_i18n__: ClassVar[Dict[str, Dict[str, str]]] = {
        "en": {"title": "Auto Sign-in", "description": "Daily automatic sign-in schedule."}
    }
    __ui_icon__ = "alarm"
    __ui_order__ = 1

    time: str = Field(
        default="08:00",
        description="每日自动签到时间（北京时间 HH:MM）",
        json_schema_extra={
            "label": "自动签到时间",
            "hint": "每日自动签到时间",
            **_ui_i18n("Auto sign-in time", "Daily time in Beijing time (HH:MM)."),
        },
    )


class TokenSectionConfig(PluginConfigBase):
    """Token 获取（token 配置节）。"""

    __ui_label__ = "Token 获取"
    __ui_i18n__: ClassVar[Dict[str, Dict[str, str]]] = {
        "en": {"title": "Token Guide", "description": "Where to obtain the Skland token."}
    }
    __ui_icon__ = "link"
    __ui_order__ = 2

    get_url: str = Field(
        default=TOKEN_URL,
        description="森空岛 token 获取链接（浏览器打开后跳转登录，点击帖子内链接即可获取 token）",
        json_schema_extra={
            "label": "Token 获取链接",
            "hint": "森空岛Token获取链接",
            **_ui_i18n("Token guide URL", "URL explaining how to obtain the Skland token."),
        },
    )


class AdminSectionConfig(PluginConfigBase):
    """管理员设置（admin 配置节）。"""

    __ui_label__ = "管理员"
    __ui_i18n__: ClassVar[Dict[str, Dict[str, str]]] = {
        "en": {"title": "Administrators", "description": "Users allowed to run admin commands."}
    }
    __ui_icon__ = "shield"
    __ui_order__ = 3

    super_admins: list[str] = Field(
        default_factory=list,
        description="超级管理员 QQ 号列表（可执行管理指令）",
        json_schema_extra={
            "label": "超级管理员",
            "hint": "超级管理员QQ号",
            **_ui_i18n("Super admins", "QQ IDs allowed to run admin commands."),
        },
    )


class SklandSignConfig(PluginConfigBase):
    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    auto_sign: AutoSignSectionConfig = Field(default_factory=AutoSignSectionConfig)
    token: TokenSectionConfig = Field(default_factory=TokenSectionConfig)
    admin: AdminSectionConfig = Field(default_factory=AdminSectionConfig)


# ==================== 用户数据管理 ====================


class UserDataManager:
    """管理每个 QQ 用户绑定的森空岛账号（可绑定多个）与自动签到开关。

    文件格式（users.json，1.2.0 起）：
    {
      "123456789": {
        "auto_sign": true,
        "accounts": [
          {"token": "...", "nickname": "森空岛昵称", "role_uids": ["游戏角色 uid", "..."]}
        ],
        "token": "...", "nickname": "..."
      }
    }
    - auto_sign 按 QQ 用户统一开关，对其名下全部账号生效。
    - role_uids 是该森空岛账号下的游戏角色 uid，用于识别「重复绑定同一账号」。
    - 顶层 token / nickname 镜像第一个账号，降级到 1.1.x 时仍能签到第一个账号。
    读取时兼容：1.1.x 单账号格式 {"token", "auto_sign", "nickname"}，
    以及旧版（amiyabot 时代）纯字符串 value（视为 token）。
    """

    def __init__(self, data_file: str):
        self.data_file = data_file
        self._data: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self.load()

    @staticmethod
    def _normalize_account(value: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(value, Mapping) or not value.get("token"):
            return None
        return {
            "token": str(value["token"]),
            "nickname": str(value.get("nickname") or "").strip(),
            "role_uids": [str(u) for u in value.get("role_uids") or [] if u],
        }

    @classmethod
    def _normalize_user(cls, value: Any) -> Dict[str, Any]:
        """把任一历史格式的用户记录转换为当前格式。"""
        if isinstance(value, str):  # amiyabot 时代：value 即 token
            value = {"token": value}
        if not isinstance(value, Mapping):
            return {"auto_sign": False, "accounts": []}
        raw_accounts = value["accounts"] if isinstance(value.get("accounts"), list) else [value]  # 1.1.x 单账号
        accounts = [a for a in (cls._normalize_account(x) for x in raw_accounts) if a]
        return {"auto_sign": bool(value.get("auto_sign", False)), "accounts": accounts}

    def load(self) -> None:
        self._data = {}
        if not os.path.exists(self.data_file):
            return
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for uid, value in raw.items():
                user = self._normalize_user(value)
                if user["accounts"]:
                    self._data[str(uid)] = user
        except Exception:
            self._data = {}

    def _serialize(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for uid, user in self._data.items():
            record: Dict[str, Any] = {"auto_sign": user["auto_sign"], "accounts": user["accounts"]}
            if user["accounts"]:
                # 镜像第一个账号，降级到 1.1.x 时仍可读取
                record["token"] = user["accounts"][0]["token"]
                record["nickname"] = user["accounts"][0]["nickname"]
            out[uid] = record
        return out

    async def save(self) -> None:
        async with self._lock:
            # 在事件循环里序列化，避免写文件期间数据被其他协程修改
            data = json.dumps(self._serialize(), ensure_ascii=False, indent=2)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._save_sync, data)

    def _save_sync(self, data: str) -> None:
        os.makedirs(os.path.dirname(self.data_file), exist_ok=True)
        # token 属敏感数据：写文件时收紧权限到仅属主可读写（0o600）。
        # POSIX 下严格生效；Windows 下 os.open 的 mode 仅尽力生效（不做加密混淆）。
        try:
            fd = os.open(self.data_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        except OSError:
            fd = None  # 极少数文件系统不支持时回退为普通写入，保证功能可用
        if fd is not None:
            # os.open 的 mode 只在新建文件时生效；旧版本或迁移来的文件可能是 0o644，这里显式收紧
            try:
                os.fchmod(fd, 0o600)
            except (AttributeError, OSError):
                pass  # Windows 无 fchmod
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            return
        with open(self.data_file, "w", encoding="utf-8") as f:
            f.write(data)

    def get_accounts(self, user_id: str) -> List[Dict[str, Any]]:
        """用户名下已绑定的账号（副本）；未绑定时为空列表。"""
        user = self._data.get(str(user_id))
        return [dict(a) for a in user["accounts"]] if user else []

    def get_auto_sign(self, user_id: str) -> bool:
        user = self._data.get(str(user_id))
        return bool(user and user["auto_sign"])

    def account_label(self, user_id: str, index: int) -> str:
        """账号显示名：森空岛昵称，缺失时为「账号N」（index 从 0 开始）。"""
        accounts = self.get_accounts(user_id)
        name = accounts[index]["nickname"] if 0 <= index < len(accounts) else ""
        return name or f"账号{index + 1}"

    def get_nickname(self, user_id: str) -> str:
        """用户显示名：名下各账号的森空岛昵称（顿号分隔）；都没有时回退为 QQ 号。"""
        names = [a["nickname"] for a in self.get_accounts(user_id) if a["nickname"]]
        return "、".join(names) or str(user_id)

    async def add_account(
        self, user_id: str, token: str, nickname: str = "", role_uids: Optional[List[str]] = None
    ) -> tuple[int, bool]:
        """绑定一个森空岛账号，返回 (账号序号（从 0 开始）, 是否为新账号)。

        与已绑定账号 token 相同、或游戏角色 uid 有交集时视为同一账号，只更新 token 与昵称；
        绑定后默认开启该用户的自动签到。
        """
        uid = str(user_id)
        user = self._data.setdefault(uid, {"auto_sign": False, "accounts": []})
        new = {
            "token": token,
            "nickname": str(nickname or "").strip(),
            "role_uids": [str(u) for u in role_uids or [] if u],
        }
        for i, acc in enumerate(user["accounts"]):
            if acc["token"] == token or set(new["role_uids"]) & set(acc["role_uids"]):
                new["nickname"] = new["nickname"] or acc["nickname"]
                new["role_uids"] = new["role_uids"] or acc["role_uids"]
                user["accounts"][i] = new
                user["auto_sign"] = True
                await self.save()
                return i, False
        user["accounts"].append(new)
        user["auto_sign"] = True  # 绑定成功后默认开启自动签到
        await self.save()
        return len(user["accounts"]) - 1, True

    async def remove_account(self, user_id: str, index: int) -> Optional[Dict[str, Any]]:
        """解绑指定序号（从 0 开始）的账号；最后一个账号解绑后删除该用户的全部数据。"""
        uid = str(user_id)
        user = self._data.get(uid)
        if not user or not 0 <= index < len(user["accounts"]):
            return None
        removed = user["accounts"].pop(index)
        if not user["accounts"]:
            del self._data[uid]
        await self.save()
        return removed

    async def set_auto_sign(self, user_id: str, enabled: bool) -> None:
        user = self._data.get(str(user_id))
        if not user:
            return
        user["auto_sign"] = bool(enabled)
        await self.save()

    def accounts_missing_info(self) -> List[tuple[str, str]]:
        """缺少森空岛昵称或游戏角色 uid 的账号，返回 (QQ 号, token) 列表（兼容旧版数据自动补全）。"""
        return [
            (uid, acc["token"])
            for uid, user in self._data.items()
            for acc in user["accounts"]
            if not acc["nickname"] or not acc["role_uids"]
        ]

    async def set_account_info(self, user_id: str, token: str, nickname: str, role_uids: List[str]) -> None:
        """为 token 对应的账号补充昵称与角色 uid（不改变 token / auto_sign）。

        按 token 定位而不是按序号，避免补充期间用户解绑导致序号错位。
        """
        user = self._data.get(str(user_id))
        acc = next((a for a in user["accounts"] if a["token"] == token), None) if user else None
        if acc is None:
            return
        if nickname:
            acc["nickname"] = str(nickname).strip()
        if role_uids:
            acc["role_uids"] = [str(u) for u in role_uids if u]
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


def format_account_sign_lines(items: List[Dict[str, Any]]) -> List[str]:
    """把多个账号的签到结果格式化为文本行；绑定了多个账号时每个账号前加「【昵称】」标题。"""
    multi = len(items) > 1
    lines: List[str] = []
    for item in items:
        if multi:
            lines.append(f"【{item['label']}】")
        if item["error"]:
            lines.append(f"[X] 签到失败：{item['error']}")
        elif not item["results"]:
            lines.append("暂未查询到已绑定的游戏账号")
        else:
            lines.extend(format_sign_results(item["results"]))
    return lines


def format_status_line(status: Mapping[str, bool]) -> str:
    return (
        f"明日方舟：{'今日已签到' if status.get('arknights') else '今日未签到'}；"
        f"终末地：{'今日已签到' if status.get('endfield') else '今日未签到'}"
    )


def sanitize_error(exc: BaseException) -> str:
    """把异常转换为简短、可对外展示的原因（sanitize_error 风格）。

    去除 URL / IP / HTTP 状态码 / 响应体等内部细节，只保留首行可读原因；
    完整异常由调用方写入日志，本函数结果仅用于面向用户的消息与工具返回。
    """
    text = (str(exc) or "").strip()
    if not text:
        return "请求失败，请稍后重试"
    text = text.splitlines()[0]
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "", text)
    text = re.sub(r"HTTP\s*\d+", "", text, flags=re.IGNORECASE)
    # 去除被截进异常文本的响应体 / 结构化数据（贪婪匹配以覆盖嵌套结构）
    text = re.sub(r"\{.*\}", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    # 状态码被移除后残留的空括号
    text = re.sub(r"[（(]\s*[)）]", "", text)
    text = text.strip("：:，,- ").strip()
    if not text:
        return "请求失败，请稍后重试"
    return text[:100]


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
        self._last_backfill_date: Optional[str] = None

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
        # 兼容旧版数据：插件每次启动时后台为缺少森空岛昵称的用户自动补充昵称
        # （每日最多一次，不阻塞插件加载）
        self._last_backfill_date = self._load_backfill_date(data_dir)
        asyncio.create_task(self._backfill_nicknames())
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

    async def _backfill_nicknames(self, force: bool = False) -> None:
        """为旧版数据中缺少森空岛昵称（nickname）或游戏角色 uid（role_uids）的账号自动补充。

        通过账号已保存的 token 调用森空岛接口查询其绑定列表，写回第一个昵称与全部角色 uid
        （role_uids 用于识别重复绑定；不改变 token / auto_sign）。

        执行时机：
        - 插件每次启动（on_load）；
        - 每日自动签到执行时（_check_and_run 到达签到时间点）。

        防循环：用 last_nickname_backfill.json 记录最后一次执行日期，
        同一天内最多执行一次；查询失败的用户保持原样，留到次日再尝试，
        避免反复请求接口。
        """
        assert self._user_manager is not None

        data_dir = self._get_data_dir()
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        if not force and self._last_backfill_date == today:
            return
        self._last_backfill_date = today

        missing = self._user_manager.accounts_missing_info()
        if missing:
            self.ctx.logger.info("发现 %d 个账号缺少森空岛昵称或角色信息，开始自动补充", len(missing))
            filled = 0
            for uid, token in missing:
                try:
                    nickname, role_uids = await self._fetch_account_info(token)
                    if nickname or role_uids:
                        await self._user_manager.set_account_info(uid, token, nickname, role_uids)
                        filled += 1
                except Exception as e:
                    self.ctx.logger.warning(
                        "为用户 %s 的账号补充森空岛信息失败（token 可能已过期），留待次日重试：%s", uid, e
                    )
            if filled:
                self.ctx.logger.info("已为 %d 个账号补充森空岛信息", filled)

        self._save_backfill_date(data_dir, today)

    def _load_backfill_date(self, data_dir: str) -> Optional[str]:
        backfill_file = os.path.join(data_dir, "last_nickname_backfill.json")
        if os.path.exists(backfill_file):
            try:
                with open(backfill_file, "r", encoding="utf-8") as f:
                    return json.load(f).get("date")
            except Exception:
                pass
        return None

    def _save_backfill_date(self, data_dir: str, date_str: str) -> None:
        backfill_file = os.path.join(data_dir, "last_nickname_backfill.json")
        try:
            with open(backfill_file, "w", encoding="utf-8") as f:
                json.dump({"date": date_str}, f)
        except Exception as e:
            self.ctx.logger.warning("保存昵称补充日期失败：%s", e)

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
        # 每日自动签到执行时，顺带为仍缺少森空岛昵称的用户自动补充昵称
        # （每天只在此时间点执行一次，配合 last_nickname_backfill.json 防止循环）
        try:
            await self._backfill_nicknames(force=True)
        except Exception as e:
            self.ctx.logger.warning("自动签到前的昵称补充执行异常：%s", e)
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
        """为所有开启自动签到的用户的全部账号执行签到，仅记录日志，不推送消息。"""
        assert self._user_manager is not None
        for user_id in self._user_manager.all_user_ids():
            if not self._user_manager.get_auto_sign(user_id):
                continue
            for item in await self._sign_all_accounts(user_id):
                if item["error"]:
                    self.ctx.logger.warning("[森空岛] 用户 %s 账号 %s 自动签到异常：%s", user_id, item["label"], item["error"])
                elif not item["results"]:
                    self.ctx.logger.info("[森空岛] 用户 %s 账号 %s 无游戏账号", user_id, item["label"])
                else:
                    for line in format_sign_results(item["results"]):
                        self.ctx.logger.info("[森空岛] 用户 %s 账号 %s %s", user_id, item["label"], line)

    # ==================== 森空岛请求（按账号） ====================

    async def _fetch_account_info(self, token: str) -> tuple[str, List[str]]:
        """用 token 查询绑定列表，返回 (森空岛昵称, 全部游戏角色 uid)；token 无效时抛异常。"""
        api = SklandAPI()
        try:
            auth_code = await api.get_authorization(token)
            cred = await api.get_credential(auth_code)
            bindings = await api.get_binding_list(cred)
            return (bindings[0].nickname if bindings else ""), [b.uid for b in bindings]
        finally:
            await api.close()

    async def _sign_all_accounts(self, user_id: str) -> List[Dict[str, Any]]:
        """为用户名下每个账号执行签到；单个账号失败不影响其他账号。

        返回 [{"label": 账号显示名, "results": [SignInResult], "error": 对外可展示的原因或 None}]。
        """
        assert self._user_manager is not None
        items: List[Dict[str, Any]] = []
        for i, acc in enumerate(self._user_manager.get_accounts(user_id)):
            label = self._user_manager.account_label(user_id, i)
            api = SklandAPI()
            try:
                results, _ = await api.do_full_sign_in(acc["token"])
                items.append({"label": label, "results": results, "error": None})
            except Exception as e:
                self.ctx.logger.warning("用户 %s 账号 %s 签到失败：%s", user_id, label, e)
                items.append({"label": label, "results": [], "error": sanitize_error(e)})
            finally:
                await api.close()
        return items

    async def _status_all_accounts(self, user_id: str) -> List[Dict[str, Any]]:
        """查询用户名下每个账号的今日签到状态，返回 [{"label", "status", "error"}]。"""
        assert self._user_manager is not None
        items: List[Dict[str, Any]] = []
        for i, acc in enumerate(self._user_manager.get_accounts(user_id)):
            label = self._user_manager.account_label(user_id, i)
            api = SklandAPI()
            try:
                status, _ = await api.check_sign_in_status(acc["token"])
                items.append({"label": label, "status": status, "error": None})
            except Exception as e:
                self.ctx.logger.warning("用户 %s 账号 %s 查询签到状态失败：%s", user_id, label, e)
                items.append({"label": label, "status": {}, "error": sanitize_error(e)})
            finally:
                await api.close()
        return items

    def _format_status_text(self, user_id: str, items: List[Dict[str, Any]]) -> str:
        assert self._user_manager is not None
        auto_sign = "已开启" if self._user_manager.get_auto_sign(user_id) else "未开启"
        lines = [f"自动签到：{auto_sign}（每日 {self._get_auto_sign_time()}）"]
        if len(items) == 1:
            item = items[0]
            lines.insert(0, f"用户：{item['label']}")
            lines.extend(
                [f"查询失败：{item['error']}"] if item["error"] else format_status_line(item["status"]).split("；")
            )
        else:
            for item in items:
                detail = f"查询失败：{item['error']}" if item["error"] else format_status_line(item["status"])
                lines.append(f"【{item['label']}】{detail}")
        return "\n".join(lines)

    def _format_account_list(self, user_id: str) -> List[str]:
        assert self._user_manager is not None
        accounts = self._user_manager.get_accounts(user_id)
        lines = [f"{i}. {self._user_manager.account_label(user_id, i - 1)}" for i in range(1, len(accounts) + 1)]
        auto_sign = "已开启" if self._user_manager.get_auto_sign(user_id) else "未开启"
        lines.append(f"自动签到：{auto_sign}（每日 {self._get_auto_sign_time()}，对以上全部账号生效）")
        return lines

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

    async def _get_super_admin_ids(self) -> List[str]:
        """统一管理员判定（2026-09-29 二轮）：宿主管理员 ∪ 插件配置管理员。

        - 宿主侧来源：ctx.config.get("plugin.permission", ...)（与命令权限同源）；
          读取失败降级为仅插件配置（debug 日志，不抛异常）；
        - 两路来源经 plain_id 归一为纯数字 ID 后按 ID 去重（插件配置值允许
          携带 'qq:10001' 平台前缀，自动识别）；
        - 合并后为空时调用方应拒绝（fail-closed），与原行为一致。
        """
        try:
            perms = await self.ctx.config.get("plugin.permission", None)
        except Exception as e:
            self.ctx.logger.debug("读取宿主 plugin.permission 失败，降级为仅插件配置管理员：%s", e)
            perms = None
        return collect_admins(perms, self.config.admin.super_admins)

    async def _is_super_admin(self, user_id: str) -> bool:
        pid = plain_id(user_id)
        if not pid:
            return False
        admins = await self._get_super_admin_ids()
        return pid in admins

    async def _resolve_tool_user_id(self, requested_uid: str, kwargs: Any) -> tuple[Optional[str], Optional[str]]:
        """解析 LLM 工具的目标用户并做权限校验（防止代替任意用户操作）。

        规则：
        - 默认绑定当前消息发送者：user_id 为空或（归一后）等于发送者时，一律以发送者身份执行；
        - 指定其他用户（代他人绑定 / 签到 / 查状态 / 开关自动签到）仅限超级管理员
          （宿主管理员 ∪ admin.super_admins，纯 ID 去重判定）。

        返回 (uid, error)：允许执行时 error 为 None；拒绝时 uid 为 None、error 为原因。
        """
        sender_id = self._extract_user_id(kwargs.get("message", {}))
        uid = str(requested_uid or "").strip()
        uid_pid = plain_id(uid)
        sender_pid = plain_id(sender_id)
        if not uid or (sender_pid and uid_pid == sender_pid):
            if not sender_id:
                return None, "无法获取当前消息的发送者标识，请改用 /森空岛绑定 等指令完成操作"
            return sender_id, None
        if await self._is_super_admin(sender_id):
            return uid_pid or uid, None
        return None, "不允许代替其他用户操作，请让该用户本人向机器人发起请求"

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

    async def _send_help(self, stream_id: str) -> None:
        """发送帮助/固定回复信息。

        长内容使用「单条合并转发」方案返回（多条消息合并为一条转发气泡），
        避免刷屏；合并转发不可用时回退为单条普通文本。
        """
        text = build_help_text(self._get_token_url())
        # 拆成逻辑段：token 获取教程 + 可用指令列表（每行一条），合并为一条转发消息
        lines: List[str] = []
        for part in text.split("\n\n"):
            part = part.strip()
            if not part:
                continue
            if "可用指令：" in part:
                # 指令段拆成多行，每行一条，方便合并转发阅读
                header, _, body = part.partition("可用指令：")
                if header.strip():
                    lines.append(header.strip())
                lines.append("可用指令：")
                lines.extend(line.strip() for line in body.splitlines() if line.strip())
            else:
                lines.append(part)
        await self._send_forward_or_text(stream_id, "森空岛帮助", lines)

    # ==================== 指令 ====================

    @Command(
        "skland_help_command",
        description="森空岛签到帮助（含 token 获取教程与链接）",
        pattern=r"^/?森空岛(?:帮助)?$",
    )
    async def cmd_skland_help(self, **kwargs: Any) -> tuple[bool, str, int]:
        stream_id = str(kwargs.get("stream_id") or "")
        # 指令触发：固定回复（token 获取教程 + 指令列表），链接取自配置。
        # 长内容使用单条合并转发方案返回，避免刷屏。
        await self._send_help(stream_id)
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

        try:
            text = await self._bind_account(user_id, token)
        except Exception as e:
            self.ctx.logger.warning("用户 %s 绑定失败：%s", user_id, e)
            reason = sanitize_error(e)
            await self.ctx.send.text(f"绑定失败：{reason}", str(kwargs.get("stream_id") or ""))
            return False, f"绑定失败：{reason}", 1
        await self.ctx.send.text(text, str(kwargs.get("stream_id") or ""))
        return True, text, 2

    async def _bind_account(self, user_id: str, token: str) -> str:
        """绑定（或更新）一个账号，返回给用户的提示文案；token 无效时抛异常。"""
        nickname, role_uids = await self._fetch_account_info(token)
        nickname = nickname or "未知用户"
        assert self._user_manager is not None
        index, is_new = await self._user_manager.add_account(user_id, token, nickname, role_uids)
        total = len(self._user_manager.get_accounts(user_id))
        when = f"每天{self._get_auto_sign_time()}自动签到"
        if not is_new:
            return f"已更新账号 {nickname} 的 token（第 {index + 1} 个账号）\n{when}"
        if total == 1:
            return f"绑定成功！昵称：{nickname}\n{when}"
        return f"绑定成功！昵称：{nickname}（第 {index + 1} 个账号，共 {total} 个）\n{when}，对全部账号生效"

    @Command(
        "skland_accounts",
        description="查看已绑定的森空岛账号及序号",
        pattern=r"^/?森空岛账号\s*$",
    )
    async def cmd_skland_accounts(self, **kwargs: Any) -> tuple[bool, str, int]:
        user_id = self._extract_user_id(kwargs.get("message", {}))
        stream_id = str(kwargs.get("stream_id") or "")
        assert self._user_manager is not None
        if not self._user_manager.get_accounts(user_id):
            await self.ctx.send.text("您还未绑定 token，请使用「/森空岛绑定 <token>」", stream_id)
            return False, "未绑定 token", 1
        text = "已绑定的森空岛账号：\n" + "\n".join(self._format_account_list(user_id))
        await self.ctx.send.text(text, stream_id)
        return True, text, 2

    @Command(
        "skland_unbind",
        description="解绑森空岛账号（绑定多个账号时指定序号或「全部」）",
        pattern=r"^/?森空岛解绑\s*(?P<target>\S*)\s*$",
    )
    async def cmd_skland_unbind(self, **kwargs: Any) -> tuple[bool, str, int]:
        user_id = self._extract_user_id(kwargs.get("message", {}))
        stream_id = str(kwargs.get("stream_id") or "")
        target = str(kwargs.get("matched_groups", {}).get("target") or "").strip()
        assert self._user_manager is not None
        accounts = self._user_manager.get_accounts(user_id) if user_id else []
        if not accounts:
            await self.ctx.send.text("你还没有绑定任何森空岛账号", stream_id)
            return True, "无可解绑账号", 1

        if target in ("全部", "所有", "all") or (not target and len(accounts) == 1):
            await self._user_manager.delete(user_id)
            await self.ctx.send.text("解绑成功，已删除你的所有数据", stream_id)
            return True, "解绑成功", 1

        if not target:
            text = (
                f"你绑定了 {len(accounts)} 个账号，请指定要解绑的序号：\n"
                + "\n".join(self._format_account_list(user_id)[:-1])
                + "\n\n例：/森空岛解绑 2；全部解绑：/森空岛解绑 全部"
            )
            await self.ctx.send.text(text, stream_id)
            return False, "未指定序号", 1

        if not target.isdigit() or not 1 <= int(target) <= len(accounts):
            await self.ctx.send.text(f"序号无效，请填写 1~{len(accounts)}，或使用「/森空岛账号」查看序号", stream_id)
            return False, "序号无效", 1

        label = self._user_manager.account_label(user_id, int(target) - 1)
        await self._user_manager.remove_account(user_id, int(target) - 1)
        left = len(accounts) - 1
        text = f"已解绑：{label}" + (f"，还剩 {left} 个账号" if left else "，已删除你的所有数据")
        await self.ctx.send.text(text, stream_id)
        return True, text, 1

    @Command(
        "skland_sign",
        description="手动执行森空岛签到",
        pattern=r"^/?森空岛签到$",
    )
    async def cmd_skland_sign(self, **kwargs: Any) -> tuple[bool, str, int]:
        user_id = self._extract_user_id(kwargs.get("message", {}))
        assert self._user_manager is not None
        if not self._user_manager.get_accounts(user_id):
            await self.ctx.send.text("您还未绑定 token，请使用「/森空岛绑定 <token>」", str(kwargs.get("stream_id") or ""))
            return False, "未绑定 token", 1

        items = await self._sign_all_accounts(user_id)
        text = "\n".join(format_account_sign_lines(items))
        await self.ctx.send.text(text, str(kwargs.get("stream_id") or ""))
        ok = any(not item["error"] for item in items)
        return ok, text, 2 if ok else 1

    @Command(
        "skland_status",
        description="查看今日森空岛签到状态",
        pattern=r"^/?森空岛状态\s*$",
    )
    async def cmd_skland_status(self, **kwargs: Any) -> tuple[bool, str, int]:
        user_id = self._extract_user_id(kwargs.get("message", {}))
        assert self._user_manager is not None
        if not self._user_manager.get_accounts(user_id):
            await self.ctx.send.text("您还未绑定 token，请使用「/森空岛绑定 <token>」", str(kwargs.get("stream_id") or ""))
            return False, "未绑定 token", 1

        items = await self._status_all_accounts(user_id)
        text = self._format_status_text(user_id, items)
        await self.ctx.send.text(text, str(kwargs.get("stream_id") or ""))
        ok = any(not item["error"] for item in items)
        return ok, text, 2 if ok else 1

    @Command(
        "skland_auto_sign_command",
        description="开启 / 关闭自动签到",
        pattern=r"^/?森空岛自动签到\s*$",
    )
    async def cmd_skland_auto_sign(self, **kwargs: Any) -> tuple[bool, str, int]:
        user_id = self._extract_user_id(kwargs.get("message", {}))
        assert self._user_manager is not None
        accounts = self._user_manager.get_accounts(user_id)
        if not accounts:
            await self.ctx.send.text("您还未绑定 token，请先使用「/森空岛绑定 <token>」", str(kwargs.get("stream_id") or ""))
            return False, "未绑定 token", 1

        new_state = not self._user_manager.get_auto_sign(user_id)
        await self._user_manager.set_auto_sign(user_id, new_state)
        status = "已启用" if new_state else "已关闭"
        scope = f"，对名下 {len(accounts)} 个账号生效" if len(accounts) > 1 else ""
        await self.ctx.send.text(
            f"【自动签到 {status}】\n每日 {self._get_auto_sign_time()} 自动签到（结果仅记录日志，不推送）{scope}",
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
        if not await self._is_super_admin(user_id):
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
        if not await self._is_super_admin(user_id):
            await self.ctx.send.text("你没有权限执行此操作", str(kwargs.get("stream_id") or ""))
            return False, "无权限", 1

        assert self._user_manager is not None
        user_ids = self._user_manager.all_user_ids()
        if not user_ids:
            await self.ctx.send.text("暂无已绑定的用户", str(kwargs.get("stream_id") or ""))
            return True, "暂无用户", 1

        lines: List[str] = []
        for uid in user_ids:
            auto_sign = "自动" if self._user_manager.get_auto_sign(uid) else "手动"
            for item in await self._sign_all_accounts(uid):
                prefix = f"用户 {item['label']}（{auto_sign}）"
                if item["error"]:
                    lines.append(f"{prefix}：查询失败（{item['error']}）")
                    continue
                if not item["results"]:
                    lines.append(f"{prefix}：无游戏账号")
                    continue
                status_lines = []
                for r in item["results"]:
                    if r.success:
                        status_lines.append(f"{r.game}：成功（{'，'.join(r.awards) if r.awards else '无奖励'}）")
                    else:
                        err_lower = (r.error or "").lower()
                        if any(k in err_lower for k in ("已签到", "重复", "请勿重复", "今日已")):
                            status_lines.append(f"{r.game}：今日已签到")
                        else:
                            status_lines.append(f"{r.game}：失败（{r.error}）")
                lines.append(f"{prefix}：{'；'.join(status_lines)}")

        await self._send_forward_or_text(str(kwargs.get("stream_id") or ""), "森空岛签到状态", lines)
        return True, "签到信息已发送", 2

    @Command(
        "skland_auto_sign_test",
        description="立即执行一次自动签到（仅超级管理员）",
        pattern=r"^/?森空岛自动签到测试\s*$",
    )
    async def cmd_skland_auto_sign_test(self, **kwargs: Any) -> tuple[bool, str, int]:
        user_id = self._extract_user_id(kwargs.get("message", {}))
        if not await self._is_super_admin(user_id):
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
                "点击帖子中的链接即可获取 token。\n"
                "回答时务必提醒用户：{notice}不要把 token 直接发在对话里让你代为绑定。\n\n"
                "可用指令：\n"
                "/森空岛绑定 <token> —— 绑定 token（有多个森空岛账号时分别绑定）\n"
                "/森空岛账号 —— 查看已绑定的账号及序号\n"
                "/森空岛解绑 [序号|全部] —— 解绑账号\n"
                "/森空岛签到 —— 为全部账号手动签到\n"
                "/森空岛状态 —— 查看全部账号的今日签到状态\n"
                "/森空岛自动签到 —— 开启 / 关闭自动签到"
            ).format(url=token_url, purpose=TOKEN_URL_PURPOSE, link_note=link_note, notice=BIND_NOTICE.replace("\n", "")),
            "token_url": token_url,
            "token_url_purpose": TOKEN_URL_PURPOSE,
            "link_sent_to_user": link_sent,
        }

    @Tool(
        "skland_bind_token",
        brief_description="为用户绑定森空岛 token",
        detailed_description=(
            "当用户提供森空岛 token 并要求绑定（或要求开通签到）时调用。\n"
            "同一用户可以绑定多个森空岛账号（每次调用绑定一个）；再次绑定同一账号会更新它的 token。\n"
            "参数说明：\n"
            "- user_id：string，可选。要绑定 token 的用户 QQ 号；留空或填当前消息发送者即本人，"
            "代其他用户绑定仅限超级管理员。\n"
            "- token：string，必填。用户提供的森空岛 token，可以是完整 JSON "
            '（如 {"code":0,"data":{"content":"..."}}）或纯 Base64 字符串。'
        ),
        parameters=[
            ToolParameterInfo(
                name="user_id",
                param_type=ToolParamType.STRING,
                description="要绑定 token 的用户 QQ 号，留空默认为当前消息发送者；代他人绑定仅限超级管理员",
                required=False,
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
        resolved = await self._resolve_tool_user_id(user_id, kwargs)
        uid, perm_error = resolved
        if perm_error or uid is None:
            return {"success": False, "error": perm_error or "无法确定目标用户"}
        token_text = str(token or "").strip()
        if not token_text:
            return {"success": False, "error": "缺少 token 参数"}

        real_token = extract_token_from_text(token_text)
        if not real_token:
            return {"success": False, "error": "无法提取有效 token，请检查输入内容"}

        try:
            text = await self._bind_account(uid, real_token)
        except Exception as e:
            self.ctx.logger.warning("用户 %s 工具绑定失败：%s", uid, e)
            return {"success": False, "error": f"绑定失败：{sanitize_error(e)}"}
        assert self._user_manager is not None
        return {
            "success": True,
            "content": f"QQ:{uid} 绑定完成，已默认开启每日自动签到。请用自己的话把结果告诉用户：{text}",
            "user_id": uid,
            "accounts": [a["nickname"] for a in self._user_manager.get_accounts(uid)],
            "auto_sign_enabled": True,
        }

    @Tool(
        "skland_sign_in",
        brief_description="为用户执行森空岛签到",
        detailed_description=(
            "当用户明确要求执行森空岛签到（如「帮我签到」「森空岛签到」）时调用。\n"
            "参数说明：\n"
            "- user_id：string，可选。要执行签到的用户 QQ 号；留空或填当前消息发送者即本人，"
            "代其他用户签到仅限超级管理员。"
        ),
        parameters=[
            ToolParameterInfo(
                name="user_id",
                param_type=ToolParamType.STRING,
                description="要执行签到的用户 QQ 号，留空默认为当前消息发送者；代他人签到仅限超级管理员",
                required=False,
            ),
        ],
    )
    async def tool_skland_sign_in(self, user_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        uid, perm_error = await self._resolve_tool_user_id(user_id, kwargs)
        if perm_error or uid is None:
            return {"success": False, "error": perm_error or "无法确定目标用户"}
        assert self._user_manager is not None
        if not self._user_manager.get_accounts(uid):
            return {"success": False, "error": f"用户 {self._user_manager.get_nickname(uid)} 还未绑定 token，请先让用户绑定"}

        items = await self._sign_all_accounts(uid)
        if all(item["error"] for item in items):
            return {"success": False, "error": "签到失败：" + "；".join(f"{i['label']}：{i['error']}" for i in items)}
        return {
            "success": True,
            "content": f"用户 {self._user_manager.get_nickname(uid)} 的签到结果：\n" + "\n".join(format_account_sign_lines(items)),
            "user_id": uid,
            "accounts": [
                {
                    "account": item["label"],
                    "error": item["error"],
                    "results": [
                        {"game": r.game, "nickname": r.nickname, "success": r.success, "awards": r.awards, "error": r.error}
                        for r in item["results"]
                    ],
                }
                for item in items
            ],
        }

    @Tool(
        "skland_sign_status",
        brief_description="查询用户今日森空岛签到状态",
        detailed_description=(
            "当用户询问今日是否已签到、签到状态、绑定信息时调用。\n"
            "参数说明：\n"
            "- user_id：string，可选。要查询的用户 QQ 号；留空或填当前消息发送者即本人，"
            "查询其他用户仅限超级管理员。"
        ),
        parameters=[
            ToolParameterInfo(
                name="user_id",
                param_type=ToolParamType.STRING,
                description="要查询的用户 QQ 号，留空默认为当前消息发送者；查询他人仅限超级管理员",
                required=False,
            ),
        ],
    )
    async def tool_skland_sign_status(self, user_id: str = "", **kwargs: Any) -> Dict[str, Any]:
        uid, perm_error = await self._resolve_tool_user_id(user_id, kwargs)
        if perm_error or uid is None:
            return {"success": False, "error": perm_error or "无法确定目标用户"}
        assert self._user_manager is not None
        if not self._user_manager.get_accounts(uid):
            return {"success": False, "error": f"用户 {self._user_manager.get_nickname(uid)} 还未绑定 token"}

        items = await self._status_all_accounts(uid)
        if all(item["error"] for item in items):
            return {"success": False, "error": "查询失败：" + "；".join(f"{i['label']}：{i['error']}" for i in items)}
        return {
            "success": True,
            "content": self._format_status_text(uid, items),
            "user_id": uid,
            "auto_sign_enabled": self._user_manager.get_auto_sign(uid),
            "accounts": [{"account": i["label"], "status": i["status"], "error": i["error"]} for i in items],
        }

    @Tool(
        "skland_auto_sign",
        brief_description="开启或关闭用户的森空岛每日自动签到",
        detailed_description=(
            "当用户要求开启或关闭自动签到（如「帮我开启自动签到」）时调用。\n"
            "参数说明：\n"
            "- user_id：string，可选。目标用户 QQ 号；留空或填当前消息发送者即本人，"
            "操作其他用户仅限超级管理员。\n"
            "- enable：boolean，必填。true 开启自动签到，false 关闭。"
        ),
        parameters=[
            ToolParameterInfo(
                name="user_id",
                param_type=ToolParamType.STRING,
                description="目标用户 QQ 号，留空默认为当前消息发送者；操作他人仅限超级管理员",
                required=False,
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
        uid, perm_error = await self._resolve_tool_user_id(user_id, kwargs)
        if perm_error or uid is None:
            return {"success": False, "error": perm_error or "无法确定目标用户"}
        assert self._user_manager is not None
        if not self._user_manager.get_accounts(uid):
            return {"success": False, "error": f"用户 {self._user_manager.get_nickname(uid)} 还未绑定 token，无法开启自动签到"}

        await self._user_manager.set_auto_sign(uid, to_bool(enable))
        state = "已开启" if to_bool(enable) else "已关闭"
        return {
            "success": True,
            "content": f"用户 {self._user_manager.get_nickname(uid)} 的自动签到{state}（每日 {self._get_auto_sign_time()} 执行，结果仅记录日志）",
            "user_id": uid,
            "auto_sign_enabled": to_bool(enable),
        }


def create_plugin() -> SklandSignPlugin:
    return SklandSignPlugin()
