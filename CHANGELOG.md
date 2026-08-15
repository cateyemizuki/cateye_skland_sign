# Changelog

## [1.0.0] - 首个发布版本

以当前功能作为第一个正式发布版本，插件版本号重置为 `1.0.0`。

- 重构为 MaiBot Manifest v2 插件（`_manifest.json` + `plugin.py`），取代旧版 amiyabot 实现。
- 支持指令与 LLM 工具两种触发方式：`skland_help` / `skland_bind_token` / `skland_sign_in` / `skland_sign_status` / `skland_auto_sign`。
- token 获取改为链接方案：获取链接（默认 https://www.skland.com/article?id=6216915&c_c=COPY）存放在配置 `[token] get_url` 中，用户可自行修改；调用 `skland_help` 工具时插件独立向当前对话发送一条只含链接的消息，并把链接及作用返回给大模型由模型组织回答；`/森空岛` 指令触发时固定回复获取教程（不再使用二维码）。
- 支持明日方舟 / 终末地手动签到、每日定时自动签到（北京时间，默认 08:00）。
- 兼容 NapCat 适配器与 SnowLuma 适配器（文本 / 合并转发）。
- 插件目录更名为 `cateye_skland_sign`（以作者名称开头），插件 ID 为 `github.cateye.skland.sign`（Manifest 要求 id 以点号/横线分隔）。
- 用户数据存储于统一持久化目录 `data/plugins/cateye_skland_sign`（遵守官方建议，不使用插件目录下旧式 `data/` 目录；子文件夹固定为 `cateye_skland_sign`，不随插件 ID 变化），自动迁移旧数据（按 ID 派生目录 / 旧插件 ID 持久化目录 / 旧式插件目录 `data/users.json`）。
