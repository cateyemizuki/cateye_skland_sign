# 森空岛签到（MaiBot 插件）

森空岛（Skland）每日签到插件，适用于 MaiBot（Manifest v2，`plugin_type = "tool"`），支持明日方舟与终末地账号的签到，面向在 MaiBot 中使用森空岛、希望每天自动完成签到的用户。

用户绑定森空岛 token 后，可以通过聊天指令或自然语言（LLM 工具）手动签到、查看账号与状态、开关自动签到。一个 QQ 可以绑定多个森空岛账号；开启自动签到后，插件每天按北京时间在后台为名下全部账号签到，结果只写入日志，不向聊天推送。

## 功能

- **多账号**：同一个 QQ 可绑定多个森空岛账号，签到、状态查询与自动签到对名下全部账号生效；重复绑定同一账号（token 相同或角色 uid 有交集）不会新增记录，会更新该账号的 token 与昵称。
- **手动签到**：`/森空岛签到` 逐账号执行，某个账号失败不影响其他账号。
- **每日自动签到**：按全局配置的时间执行，仅处理已开启自动签到的用户，结果写入插件日志。
- **状态查询**：`/森空岛状态` 会先执行一次签到流程再判断今日状态（原因见下）。
- **LLM 工具**：`skland_help`、`skland_bind_token`、`skland_sign_in`、`skland_sign_status`、`skland_auto_sign`。
- **管理入口**：超级管理员可修改全局签到时间、遍历全员执行签到并汇总、立即执行一次自动签到流程。

## 安装与启用

1. 将整个插件目录放入 MaiBot 的 `plugins/` 目录，保留 `__init__.py`、`plugin.py`、`skland_api.py`、`admin_util.py`、`_manifest.json` 和 `logo.png`；插件以包形式加载，入口为 `plugin.py` 中的 `create_plugin()`。
2. 重启宿主，或在宿主提供的插件管理界面中加载。
3. 插件声明的 Python 依赖为 `httpx>=0.25.0`、`pycryptodome>=3.19.0`、`tomlkit>=0.13.3`，由宿主按 `_manifest.json` 中的 `python_package` 声明安装。

兼容区间以 `_manifest.json` 为准：

- 宿主 MaiBot：`1.0.0` ~ `1.3.99`
- SDK：`2.0.0` ~ `2.99.99`

插件声明能力 `send.text`、`send.forward`、`config.get`，分别用于发送文本、合并转发和读取宿主配置中的管理员名单。图标为 `logo.png`，宿主不支持时回退 `calendar-check`。不需要额外的数据库或本地服务；签到请求需要能访问森空岛相关接口。

插件配置位于插件目录的 `config.toml`，运行数据默认位于 `data/plugins/cateye_skland_sign/`。

## 配置

配置保存在插件目录的 `config.toml`，通常通过 WebUI 修改；也可以直接编辑该文件后重载插件。

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `plugin.enabled` | `true` | 插件开关。为 `false` 时不启动自动签到调度器。 |
| `plugin.config_version` | `1.2.1` | 内部配置版本，WebUI 中隐藏并禁用，请勿修改。 |
| `auto_sign.time` | `"08:00"` | 每日自动签到时刻，北京时间 `HH:MM`。全局设置，对所有用户生效。 |
| `token.get_url` | `https://www.skland.com/article?id=6216915&c_c=COPY` | token 获取链接，`/森空岛` 与 `skland_help` 使用该值；留空时回退默认链接。 |
| `admin.super_admins` | `[]` | 超级管理员 QQ 号列表，可填纯 QQ 号（`qq:` 前缀会被归一）。管理权限还会合并宿主 `plugin.permission` 名单，详见下文。 |

- `auto_sign_hour` 是旧版配置兼容项：当 `auto_sign.time` 不是 `H:MM` / `HH:MM` 形式时，插件读取该整数小时（0–23）并转换为 `HH:00`。新配置不需要设置。
- `auto_sign.time` 请填写 `00:00`–`23:59` 范围内的有效时间。该时间对所有用户共用，修改后影响全部已开启自动签到的账号。
- 通过 WebUI 保存配置后，宿主触发配置更新，插件会重建调度器；使用 `/森空岛定时` 时，插件会写回 `config.toml`、同步内存配置并更新 `next_sign.json`，立即生效。
- `token.get_url` 和 `admin.super_admins` 在每次使用时读取，修改后无需重建调度器。

管理员判定为宿主 `plugin.permission` 与 `admin.super_admins` 的并集，会归一为纯数字 ID 后去重。因此宿主侧已经授权的管理员无需重复填写；两处都为空时，所有管理指令一律拒绝。

## 使用

### 指令

以下指令均支持带或不带前导 `/`。

| 指令 | 说明 | 权限 |
|------|------|------|
| `/森空岛`、`/森空岛帮助` | 回复 token 获取教程、绑定安全提示和完整指令清单。内容较长时优先合并转发，失败时回退为单条文本。 | 所有人 |
| `/森空岛绑定 <token>` | 绑定一个森空岛账号；成功后默认开启自动签到。支持完整 JSON 或纯 Base64 token。 | 所有人 |
| `/森空岛账号` | 按序号列出已绑定账号的昵称、自动签到开关与每日执行时间。 | 所有人 |
| `/森空岛解绑 [序号\|全部]` | 解绑指定序号的账号；`全部`（也接受 `所有`、`all`）删除该 QQ 的全部数据。只绑定一个账号时可省略参数；绑定多个时省略会先列出账号。 | 所有人 |
| `/森空岛签到` | 为名下全部账号手动签到，逐账号输出各游戏结果；某个账号失败不影响其他账号。 | 所有人 |
| `/森空岛状态` | 查看名下全部账号的今日状态与自动签到开关。该指令内部会先执行一次完整签到流程，再根据返回文案判断是否已签到，因此每次查询等同于一次签到尝试。 | 所有人 |
| `/森空岛自动签到` | 开启或关闭自动签到；按 QQ 生效，对名下全部账号有效。 | 所有人 |
| `/森空岛定时 <时间>` | 设置每日自动签到时间，支持 `6点30分`、`6:30`、`6：30` 等写法；写入配置并回复下一次执行时间。 | 超级管理员 |
| `/森空岛签到信息` | 遍历所有已绑定用户，对每个账号实际执行签到并汇总结果；内容较长时合并转发。 | 超级管理员 |
| `/森空岛自动签到测试` | 立即执行一次自动签到流程；只处理已开启自动签到的用户，结果只写入插件日志。 | 超级管理员 |

示例：

```
/森空岛绑定 dXNlci10b2tlbi1zdHJpbmc=
/森空岛定时 6:30
```

其他常用写法：`/森空岛账号`、`/森空岛解绑 2`、`/森空岛签到`、`/森空岛状态`、`/森空岛自动签到`。

`/森空岛签到` 的结果使用三种标记：`[OK]` 表示成功，`[SKIP]` 表示今日已签到，`[X]` 表示失败。多账号时按「【昵称】」分段。例如：

```
[OK] 明日方舟：博士（获得：龙门币x1000）
[SKIP] 终末地：管理员（今日已签到）
```

网络或请求异常只会显示简短原因，完整异常写入插件日志；上游返回的业务提示用于判断是否已签到，未识别的失败原因会按原文显示。请求层对网络错误、超时、HTTP 429 与 5xx 最多重试 3 次，其余 4xx 直接失败。

### 自动签到

绑定成功后，自动签到默认开启。`/森空岛自动签到` 在开启与关闭之间切换，开关按 QQ 保存，对名下全部账号生效。

后台调度器每 60 秒检查一次；到达 `auto_sign.time` 后，为所有 `auto_sign` 为 `true` 的用户逐个账号执行签到，结果只写入插件日志。下次执行时间保存在 `next_sign.json`，重启后继续沿用。

`plugin.enabled` 为 `false` 时不启动调度器。超级管理员可用 `/森空岛自动签到测试` 立即执行一次同样的流程，同样只处理已开启自动签到的用户，聊天中只回复执行提示。

### LLM 工具

插件注册以下 5 个 LLM 工具，由模型在用户提出对应请求时调用：

| 工具 | 参数 | 用途 |
|------|------|------|
| `skland_help` | 无 | 回答使用方式、token 获取与绑定方法；调用时单独发送一条只含 token 获取链接的消息，并把链接、用途、获取步骤和常用指令清单交给模型组织回答。 |
| `skland_bind_token` | `user_id`（可选）、`token`（必填） | 绑定或更新一个账号，绑定成功后默认开启自动签到；返回账号昵称列表与 `auto_sign_enabled`。 |
| `skland_sign_in` | `user_id`（可选） | 为名下全部账号签到，返回每个账号的 `results`；对于已绑定且有权操作的用户，仅当所有账号都发生请求异常时返回 `success: false`，业务失败或无游戏账号时仍返回 `success: true`，详情在 `results`。 |
| `skland_sign_status` | `user_id`（可选） | 逐账号查询今日状态并返回自动签到开关；查询同样会触发一次签到流程。 |
| `skland_auto_sign` | `user_id`（可选）、`enable`（必填） | 开启或关闭自动签到；`enable` 为布尔值，字符串 `true`、`1`、`yes`、`是`、`对` 视为开启，其余视为关闭。 |

`skland_help` 会尽量单独发送链接；缺少会话上下文或发送失败时，会在返回内容中说明，由模型把链接告知用户。

`user_id` 留空或等于当前消息发送者时操作本人；指定其他用户时，仅超级管理员允许，否则返回「不允许代替其他用户操作」。

常见触发说法：「森空岛怎么用」「怎么获取 token」「帮我签到」「我今天签到了吗」「关掉自动签到」。绑定 token 建议直接使用指令，不要把 token 作为普通聊天内容发给模型。工具不提供账号列表、解绑、设置时间、全员签到信息等操作，这些使用指令完成。

## 绑定 token

### 获取 token

默认获取链接：`https://www.skland.com/article?id=6216915&c_c=COPY`。用浏览器打开后会跳转到森空岛登录页，登录后点击帖子中的链接即可获取绑定 token。链接可在 `token.get_url` 中替换，`/森空岛` 与 `skland_help` 都使用该配置值。

### 输入格式

`/森空岛绑定` 与 `skland_bind_token` 都接受完整 JSON 或纯 Base64 字符串：

```json
{"code":0,"data":{"content":"dXNlci10b2tlbi1zdHJpbmc="}}
```

```
dXNlci10b2tlbi1zdHJpbmc=
```

### 安全注意事项

- token 等同于账号登录凭证。请私聊机器人，单独发送一条 `/森空岛绑定 <token>`，开头不要加其他内容，不要使用回复或引用。
- 不要在群里发送 token，也不要转发含 token 的聊天记录。
- 不要通过自然语言让模型代为绑定：`skland_bind_token` 必须收到 token 才能调用，token 会进入对话上下文并发送给模型。
- 插件将 token 明文保存在宿主数据目录的 `users.json` 中，请限制该目录的访问权限，详见「数据存储」。

### 失效与重新绑定

token 失效时，对应账号的签到或查询会失败，其他账号继续执行，聊天中显示简短原因。

取得新 token 后再次发送 `/森空岛绑定 <token>` 即可。插件按「token 完全相同」或「游戏角色 uid 有交集」识别同一账号，命中时更新该账号的 token 与昵称，不会新增账号；绑定成功后该 QQ 的自动签到会重新开启。若账号已被解绑，重新绑定会作为新账号追加。

## 数据存储

### 位置与文件

默认目录为 `data/plugins/cateye_skland_sign/`。子目录名固定为 `cateye_skland_sign`，不随插件 ID `github.cateye.skland.sign` 变化。

| 文件 | 内容 |
|------|------|
| `users.json` | 每个 QQ 的自动签到开关与已绑定账号（token、森空岛昵称、游戏角色 uid）。 |
| `next_sign.json` | 下一次自动签到的时间戳。 |
| `last_nickname_backfill.json` | 最近一次账号信息补全的日期。 |

插件不在数据文件中保存每日签到结果；签到状态通过 `/森空岛状态` 实时查询（查询会触发签到流程）。

`users.json` 的结构示例：

```json
{
  "10001": {
    "auto_sign": true,
    "accounts": [
      {
        "token": "dXNlci10b2tlbi1zdHJpbmc=",
        "nickname": "博士",
        "role_uids": ["10000001"]
      }
    ],
    "token": "dXNlci10b2tlbi1zdHJpbmc=",
    "nickname": "博士"
  }
}
```

每个 QQ 一条记录，`accounts` 可包含多个账号；顶层 `token` / `nickname` 是第一个账号的镜像，用于降级到 1.1.x 时仍能读取第一个账号（降级后再次写入会丢失其余账号）。1.1.x 的单账号格式与更早的纯字符串 token 会在加载时自动转换。

### 迁移与补全

目标 `users.json` 不存在时，插件按以下顺序尝试复制第一个存在的文件：

1. `data/plugins/github.cateye.skland.sign/users.json`
2. `data/plugins/maibot-community.skland-sign/users.json`
3. 插件目录下的 `data/users.json`

插件启动时会尝试为缺少昵称或角色 uid 的账号补全信息，若当天已经执行过则跳过；每日自动签到执行前还会强制执行一次补全。补全通过账号已保存的 token 查询并写回昵称与角色 uid，不改变 token 与自动签到开关；查询失败的账号保持原数据，留待次日重试。

### 写入与清理

`users.json` 优先采用「临时文件 + `fsync` + `os.replace`」原子替换写入（个别文件系统不支持时回退原地写入），并尝试将权限收紧为仅属主可读写（0o600；Windows 下尽力而为）。文件解析失败时，会尝试先备份为 `users.json.corrupt-<时间戳>`，再从空数据启动；备份失败时只记录日志。

token 以明文存储，插件不做加密混淆。任何能读取该目录的进程都可以取得全部 token，请保护好宿主机的数据目录。

清理方式：

- `/森空岛解绑 全部` 删除该 QQ 的全部账号与开关数据。
- 要清空所有用户绑定并避免残留 token，先停止宿主，再删除整个 `data/plugins/cateye_skland_sign/` 目录；其中可能包含 `users.json`、写入用的 `users.json.tmp`、损坏备份 `users.json.corrupt-*`，以及 `next_sign.json`、`last_nickname_backfill.json`。
- 再删除可能被启动迁移复制的旧数据目录：`data/plugins/github.cateye.skland.sign/`、`data/plugins/maibot-community.skland-sign/`、插件目录下的 `data/`。这些目录中的 `users.json`、临时文件或备份同样可能含 token，需一并删除；否则目标 `users.json` 不存在时，旧 token 会被复制回来。
- 插件下次启动会以空数据运行，并在下次写入时重新生成 `users.json`。

## 目录结构

```
cateye_skland_sign/
├── _manifest.json      # 插件元信息（Manifest v2）
├── __init__.py         # 包入口
├── plugin.py           # 配置、调度、指令与 LLM 工具
├── skland_api.py       # 森空岛 API 客户端
├── admin_util.py       # 管理员 ID 归一与合并
├── logo.png            # 插件图标
├── README.md
├── CHANGELOG.md
└── LICENSE
```

`config.toml` 与用户数据在运行时生成，不包含在仓库中。

## 免责声明

本插件用于个人账号的每日自动签到。请遵守森空岛及鹰角网络的服务条款，使用自动化工具可能带来的账号风险由使用者自行承担。

## 致谢

- `skland_api.py` 的设备指纹生成、鉴权与签到请求实现移植自 [Azincc/astrbot_plugin_skland](https://github.com/Azincc/astrbot_plugin_skland)。
- 多账号支持由 [@Neomelt](https://github.com/Neomelt) 贡献（[PR #1](https://github.com/cateyemizuki/cateye_skland_sign/pull/1)）。

## 许可证

MIT License，版权归 cateye 所有。仓库：<https://github.com/cateyemizuki/cateye_skland_sign>，问题反馈：<https://github.com/cateyemizuki/cateye_skland_sign/issues>。
