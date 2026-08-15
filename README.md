# 森空岛签到（MaiBot 插件）

森空岛（Skland）游戏自动签到插件，为麦麦（MaiBot）框架插件，支持**明日方舟**与**终末地**的每日签到。

用户绑定森空岛 token 后，可通过**指令**或**自然语言（LLM 工具）**完成手动签到、查看签到状态、开启每日自动签到。

本插件已验证兼容 **NapCat 适配器**（maibot-team.napcat-adapter）与 **SnowLuma 适配器**（MaiBot-SnowLuma-Adapter）两种 QQ 适配器。

## 功能特性

- **Token 绑定**：支持完整 JSON（`{"code":0,"data":{"content":"..."}}`）或纯 Base64 字符串两种输入格式。
- **手动签到**：指令 `/森空岛签到` 或对机器人说「帮我签到」。
- **自动签到**：开启后每天按配置时间（北京时间，默认 `08:00`）自动为所有已开启的用户签到，结果仅记录日志，不打扰用户。
- **签到状态查询**：查看今日各游戏是否已签到。
- **LLM 工具**：`skland_help` / `skland_bind_token` / `skland_sign_in` / `skland_sign_status` / `skland_auto_sign`，模型可自主判断调用。
- **Token 获取引导（链接方案）**：token 获取链接可在配置 `[token] get_url` 中自定义（默认森空岛官方帖子链接）；调用 `skland_help` 工具时插件会**独立向当前对话发送一条只含链接的消息**，同时把链接及作用返回给大模型，由大模型自行组织回答；指令触发时固定回复获取教程。

## 安装方式

1. 将本插件目录（含 `_manifest.json`、`plugin.py`、`skland_api.py` 等文件）放入 MaiBot 的 `plugins/` 目录。
2. 重启 MaiBot，或在 WebUI 插件中心安装。
3. 插件依赖 `httpx`、`pycryptodome`、`tomlkit`，已声明于 `_manifest.json`，Host 会自动安装。

> 兼容性声明：`host_application` `1.0.0 ~ 1.99.99`，`sdk` `2.0.0 ~ 2.99.99`（Manifest v2）。

## 配置说明

插件加载后由 Runner 在插件目录生成 `config.toml`，可在 WebUI 修改：

```toml
[plugin]
enabled = true
config_version = "1.0.0"

[auto_sign]
time = "08:00"          # 每日自动签到时间（北京时间 HH:MM，分钟粒度，默认 08:00）

[token]
get_url = "https://www.skland.com/article?id=6216915&c_c=COPY"   # token 获取链接（可自定义）

[admin]
super_admins = ["123456789"]   # 超级管理员 QQ 号列表（管理指令权限）
```

- `auto_sign.time` 也可由超级管理员通过指令 `/森空岛定时 <时间>` 修改（会写回 `config.toml` 并热重载）。
- `token.get_url` 为 token 获取链接，`skland_help` 工具与 `/森空岛` 指令均使用该链接（默认森空岛官方帖子链接，用户可自行替换）。
- 旧版配置 `auto_sign_hour`（整数小时）会被自动兼容读取。

## 使用说明

### 指令

| 指令 | 功能 | 权限 |
|------|------|------|
| `/森空岛`、`/森空岛帮助` | 固定回复：token 获取教程（含链接）+ 指令列表 | 所有人 |
| `/森空岛绑定 <token>` | 绑定 token（支持完整 JSON 或纯 Base64） | 所有人 |
| `/森空岛解绑` | 解绑并停止自动签到 | 所有人 |
| `/森空岛签到` | 手动签到 | 所有人 |
| `/森空岛状态` | 查看今日签到状态 | 所有人 |
| `/森空岛自动签到` | 开启 / 关闭自动签到 | 所有人 |
| `/森空岛定时 <时间>` | 设置自动签到时间，如「6点30分」「6:30」 | 仅超级管理员 |
| `/森空岛签到信息` | 查看所有用户签到状态（合并转发） | 仅超级管理员 |
| `/森空岛自动签到测试` | 立即执行一次自动签到 | 仅超级管理员 |

### LLM 工具（自然语言触发）

| 工具 | 触发场景 | 参数 |
|------|---------|------|
| `skland_help` | 用户询问使用方法 / 如何获取 token | 无（调用时插件独立发送一条只含链接的消息；链接与作用返回给模型，由模型组织回复） |
| `skland_bind_token` | 用户提供 token 要求绑定 | `user_id`、`token` |
| `skland_sign_in` | 用户明确要求「签到」 | `user_id` |
| `skland_sign_status` | 用户询问今日签到状态 | `user_id` |
| `skland_auto_sign` | 用户要求开启 / 关闭自动签到 | `user_id`、`enable` |

> 工具仅在用户「明确要求」时调用（见各工具描述约束）。

## 数据存储

- 插件 ID 为 `github.cateye.skland.sign`（Manifest 要求 id 以点号/横线分隔）；用户数据（token、自动签到开关）保存在统一持久化目录 `data/plugins/cateye_skland_sign/users.json`，该子文件夹固定为 `cateye_skland_sign`（与项目文件夹同名），不随插件 ID 变化，遵守官方建议、不使用插件目录下旧式 `data/` 目录。
- 首次加载时会自动迁移旧数据，按优先级：① 按插件 ID 派生的 `data/plugins/github.cateye.skland.sign/`；② 旧插件 ID（`maibot-community.skland-sign`）的持久化目录；③ 旧式插件目录 `data/users.json`（兼容纯字符串 token 旧格式）。
- **token 即登录凭证，请勿提交到公开仓库**，`.gitignore` 已包含 `/config.toml` 与 `/data/`。

## 目录结构

```
cateye_skland_sign/
├── _manifest.json      # 插件元信息（Manifest v2）
├── plugin.py           # 插件主体（配置 / 调度 / 指令 / LLM 工具）
├── skland_api.py       # 森空岛 API 客户端
├── logo.png            # 插件图标
├── README.md           # 本说明文档
├── COMMANDS.md         # 指令与触发词说明
├── CHANGELOG.md        # 更新日志
└── LICENSE             # MIT 许可证
```

## 免责声明

- 本插件仅用于个人学习与自动化签到用途，请遵守森空岛 / 鹰角网络相关服务条款，滥用导致的账号风险由使用者自行承担。
- token 属于敏感凭证，请妥善保管。

---

## 致谢与来源

本插件的 `skland_api.py`（森空岛设备指纹生成、鉴权与签到 API 客户端）移植自 [Azincc/astrbot_plugin_skland](https://github.com/Azincc/astrbot_plugin_skland) 项目，感谢原作者的工作。
