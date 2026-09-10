# Changelog

## [1.1.1] - 配置项中文注释优化

- 为全部配置项补充了用户友好的中文注释与说明（悬停提示），完善配置节说明；插件功能与行为不变。

## [1.1.0] - 绑定体验与昵称兼容优化

- 绑定成功时获取并保存用户的森空岛昵称（`users.json` 新增 `nickname` 字段），用于状态查询、签到信息等消息提示显示森空岛名称。
- **旧版数据兼容**：自动为数据中缺少 `nickname` 字段的旧用户补充森空岛昵称（通过其已保存的 token 查询并写回，不改变 token 与自动签到开关；token 失效的用户保持原样、留待次日重试）。
- **昵称补充时机**：在**插件每次启动**（`on_load`）与**每日自动签到执行时**（到达签到时间点）各执行一次；用 `last_nickname_backfill.json` 记录最后执行日期，同一天内最多执行一次，防止反复请求接口造成死循环。
- **修复昵称为空**：终末地等账号的绑定层 `nickName` 字段可能为空，实际昵称在角色列表 `roles[].nickname` 中；`skland_api.py` 的 `get_binding_list` 增加兜底逻辑——绑定层昵称为空时取第一个角色的昵称，解决部分用户昵称为空的问题。
- 绑定成功后**默认开启自动签到**（原先默认关闭），绑定成功提示改为「绑定成功！昵称：<森空岛名称> / 每天<自动签到时间>自动签到」。
- 检查所有指令与 LLM 工具中展示用户昵称的位置：优先显示保存的森空岛名称，旧用户（无昵称数据）回退显示 QQ 号。
- LLM 工具保持注册与参数不变，`skland_bind_token` 绑定逻辑与返回内容同步以上变更。
- **配置版本约定**：`plugin.config_version` 与插件版本同步（新增 `SUPPORTED_CONFIG_VERSION` 常量，与 `_manifest.json` 的 `version` 一致），用于检查配置文件是否需要更新，UI 中隐藏不可修改（MaiBot 插件通用做法，已写入开发文档）。
- **帮助信息合并转发**：`/森空岛`、`/森空岛帮助` 的长内容帮助信息改为使用**单条合并转发**返回（`_send_help()`，教程与指令列表合并为一条转发气泡），避免刷屏；合并转发不可用时自动回退为单条普通文本。

## [1.0.0] - 首个发布版本

以当前功能作为第一个正式发布版本，插件版本号重置为 `1.0.0`。

- 重构为 MaiBot Manifest v2 插件（`_manifest.json` + `plugin.py`），取代旧版 amiyabot 实现。
- 支持指令与 LLM 工具两种触发方式：`skland_help` / `skland_bind_token` / `skland_sign_in` / `skland_sign_status` / `skland_auto_sign`。
- token 获取改为链接方案：获取链接（默认 https://www.skland.com/article?id=6216915&c_c=COPY）存放在配置 `[token] get_url` 中，用户可自行修改；调用 `skland_help` 工具时插件独立向当前对话发送一条只含链接的消息，并把链接及作用返回给大模型由模型组织回答；`/森空岛` 指令触发时固定回复获取教程（不再使用二维码）。
- 支持明日方舟 / 终末地手动签到、每日定时自动签到（北京时间，默认 08:00）。
- 兼容 NapCat 适配器与 SnowLuma 适配器（文本 / 合并转发）。
- 插件目录更名为 `cateye_skland_sign`（以作者名称开头），插件 ID 为 `github.cateye.skland.sign`（Manifest 要求 id 以点号/横线分隔）。
- 用户数据存储于统一持久化目录 `data/plugins/cateye_skland_sign`（遵守官方建议，不使用插件目录下旧式 `data/` 目录；子文件夹固定为 `cateye_skland_sign`，不随插件 ID 变化），自动迁移旧数据（按 ID 派生目录 / 旧插件 ID 持久化目录 / 旧式插件目录 `data/users.json`）。
