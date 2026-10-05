"""cateye 管理员判定统一模块（参考实现，随插件复制分发）。

来源：cateye_common（2026-09-29 二轮统一，提炼自 cateye_scp_article/scp_core.py）。

统一规则（用户裁决）：
- 管理员 = 宿主管理员 ∪ 插件配置管理员，按纯 ID 去重；
- 宿主侧来源：`ctx.config.get("plugin.permission", ...)`（宿主插件权限名单，
  与命令权限同源）；读取失败降级为仅插件配置（debug 日志，不抛异常）；
- 输入默许裸 QQ 号：带平台前缀（'qq:10001'）与裸号（'10001'）视为同一人，
  文档无需强调此兼容行为。
"""

from __future__ import annotations

from typing import Any, Iterable, List


def plain_id(entry: Any) -> str:
    """任意形态的 ID 条目 → 纯数字 ID 字符串；无法解析返回空串。

    支持：'qq:10001' / '平台:10001' / '10001' / 数字 / {'user_id': ...} 等。
    """
    if isinstance(entry, dict):
        entry = entry.get("user_id") or entry.get("id") or ""
    if isinstance(entry, int):
        return str(entry)
    if not isinstance(entry, str):
        return ""
    s = entry.strip()
    if not s:
        return ""
    if ":" in s:
        s = s.rsplit(":", 1)[1].strip()
    return s if s.isdigit() else ""


def collect_admins(host_permissions: Any, config_admins: Any) -> List[str]:
    """合并宿主管理员与插件配置管理员，按纯 ID 去重。

    返回去重后的纯 ID 列表（宿主条目在前，顺序稳定）。
    """
    out: List[str] = []
    seen: set = set()
    for source in (host_permissions, config_admins):
        if isinstance(source, str):
            source = [source]
        if not isinstance(source, Iterable) or isinstance(source, (bytes, bytearray)):
            continue
        for entry in source:
            pid = plain_id(entry)
            if not pid or pid in seen:
                continue
            seen.add(pid)
            out.append(pid)
    return out


async def host_admins(ctx: Any) -> List[str]:
    """读取宿主插件权限名单；失败返回空列表（调用方降级为仅插件配置）。"""
    try:
        perms = await ctx.config.get("plugin.permission", None)
    except Exception:
        return []
    return [p for p in (plain_id(x) for x in (perms or [])) if p]
