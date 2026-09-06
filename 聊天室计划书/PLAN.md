# 计划书：liaison 聊天室接入（chatroom.html + chat-watcher.py）

> 读者：执行本计划的 AI。你要产出两个文件：`chatroom.html`（单文件网页聊天室）和 `chat-watcher.py`（监控脚本）。
> 本计划包含完整协议与数据格式；界面文案已逐字写好，照抄即可，不要自由发挥。

## 0. 必读前置

动手前必须先读 liaison skill 的两个文件（本计划不复述其细节，只引用）：

- `liaison/SKILL.md` — 六步流程；本计划交付的是第 3 步"聊天室接入"所需的两个真实文件。
- `liaison/mailbox-format.md` — mailbox 文件契约的**唯一权威**。两条铁律贯穿本计划：
  1. **一个文件一个写入者**；2. **只加不改**（status.md 例外，主 agent 覆盖更新）。

术语以工作区 `CONTEXT.md` 为准：通讯官、mailbox、缝隙、监控脚本(watcher)、搬运进、即时回、搬运出、唤醒、房间、会合点、大厅、绑定、入向/出向频道。

## 1. 场景与硬约束

- 用户**不会编程**。`chatroom.html` 双击（file://）打开即用；所有界面文字用大白话，禁止出现任何技术词（频道/协议/CORS/SSE/token 等一律不许上屏）。
- **AI 在云端运行**：mailbox、watcher、通讯官、主 agent 都在云端；聊天室在用户电脑的浏览器里。两侧只能各自**出站**连网，经**会合点**（公共消息中继）交换消息。
- agent 在聊天室**绑定**完成前无法给用户发任何消息。因此：房间名由**用户**在聊天室首屏自起，作为首条消息发出；watcher 用它完成**绑定**。AI 全程不需要、也不允许向用户投递任何东西。
- 用户手里的 html 就是 skill 包里的那一份（见 §9 交付方式），不是运行时生成的新文件。

## 2. 已敲定的决策（ADR 摘要，全文见 docs/adr/）

| # | 决策 | 要点 |
|---|------|------|
| 0001 | 公网会合点 | 默认 ntfy.sh（已实测：CORS `*`、SSE 订阅、JSON 轮询、免注册均可用）；地址是配置项，可换自建 |
| 0002 | 随机大厅+首消息绑定 | 构建时烘焙随机串 R（≥96 bit）进两个文件；watcher 守大厅，用户首条消息即房间名 |
| 0003 | v1 明文过会合点 | 不内置加密；保密靠地址不可猜；计划书附自建切换方法 |
| 0004 | 唤醒抽象接口 | watcher 打印 JSON 行到 stdout + 运行 `--notify-cmd` 外部命令；不接死任何宿主 |

## 3. 总体架构

```
用户电脑(浏览器, file://)            公网会合点(ntfy)              云端
┌──────────────┐   join    ┌────────────────────┐   ┌─────────────────┐
│ chatroom.html│ ────────→ │ 大厅 lia-<R>-lobby  │ ← │ chat-watcher.py │
│              │  chat     │ 入向 lia-<R>-<rid>-in│ → │  （唯一常驻进程） │
│              │ ────────→ │                    │   │   │ 读写mailbox  │
│              │  msg/ack  │ 出向 lia-<R>-<rid>-out│ ← │   ▼             │
└──────────────┘ ←──────── └────────────────────┘   │ mailbox/ 通讯官 主agent │
                                                    └─────────────────┘
```

- `<R>`：构建时生成的随机串，24 个小写十六进制字符，**两个文件里烘焙同一值**。
- `<rid>`：roomId，由房间名算出（§5.3，两端算法一致）。
- 入向/出向分离，镜像 mailbox"一个文件一个写入者"：聊天室只写入向、只读出向；watcher 反之。

## 4. 通信协议

### 4.1 频道命名（会合点 topic）

| 频道 | topic | 写入者 | 读者 |
|------|-------|--------|------|
| 大厅 | `lia-<R>-lobby` | 聊天室（仅 join） | watcher |
| 入向 | `lia-<R>-<rid>-in` | 聊天室 | watcher |
| 出向 | `lia-<R>-<rid>-out` | watcher | 聊天室 |

### 4.2 信封（所有频道上的消息体都是这个 JSON 字符串）

```json
{
  "v": 1,
  "type": "join | joined | chat | msg | ack | sys",
  "roomId": "r1a2b3c4d",
  "room": "房间显示名（仅 join 必填）",
  "id": "1757155200000-x7k2",
  "part": "1/1",
  "ts": 1757155200,
  "text": "..."
}
```

| 字段 | 说明 |
|------|------|
| `v` | 协议版本，恒 1 |
| `type` | `join` 聊天室→大厅求绑定；`joined` watcher→出向确认绑定；`chat` 用户消息（入向）；`msg` 通讯官消息（出向，源自 chat-out.md）；`ack` 即时回（出向，watcher 直发）；`sys` watcher 系统通知（出向） |
| `roomId` | 房间标识，join 之后所有消息必带 |
| `id` | 发送方生成：`<毫秒时间戳>-<4位随机>`，用于去重 |
| `part` | 分片 `i/n`；正文 >3500 字节时按 UTF-8 安全边界切片，同 `id` 连发，收齐拼接 |
| `ts` | 发送方 Unix 秒 |
| `text` | 内容 |

会合点单次消息上限约 4KB，信封开销预留后定 3500 字节分片阈值。

### 4.3 绑定时序

```
聊天室                                watcher
  │ 首屏拿到房间名（如"写小说"）            │ 启动即订阅大厅(SSE)
  │ 计算 rid = fnv1a("写小说")             │
  │ ── join{room,roomId} → 大厅 ─────────→ │ 收到 join：
  │ 随即订阅出向 lia-<R>-<rid>-out         │   1. 记录 rid→mailbox 目录
  │                                       │   2. 订阅入向 lia-<R>-<rid>-in
  │ ←──── joined ──── 出向 ────────────── │   3. 发 joined 到出向
  │ 进入聊天态                             │
  │ ── chat{text} → 入向 ───────────────→ │ 追加 chat-in.md → 发 ack 到出向 → 唤醒
  │ ←──── ack ─────────────────────────── │
  │ ←──── msg ─────────────────────────── │ (chat-out.md 新增时)
```

要点：

- 同一 `rid` 重复 join：幂等，重新回 `joined`（用户刷新/重开页面）。
- join 是**协议流量，不写 chat-in.md**；chat-in.md 只收 `chat`。
- 绑定前 watcher 不向出向发任何东西（此时没有房间可发）。
- 多房间：一个 watcher 可绑定多个 rid，全部路由到启动时指定的 mailbox 目录；频道按 rid 天然隔离，互不串话。

### 4.4 可靠性与重连

- 聊天室：优先 SSE 订阅出向（`GET /lia-<R>-<rid>-out/sse`）；SSE 断开时自动重连，重连间隔 1s→2s→5s→10s 封顶；连续失败 3 次降级为每 5 秒 `GET .../json?poll=1&since=<lastTs>` 轮询兜底，恢复后回到 SSE。所有收信按 `id` 去重。
- watcher：同样 SSE + 轮询兜底策略订阅大厅与各入向频道；发布用 `POST /<topic>`（body=信封 JSON 字符串，`Content-Type: text/plain; charset=utf-8`，避免浏览器预检）。
- 会合点消息缓存约 12 小时：watcher 短暂不在线，用户的消息不丢；界面要如实告知（文案见 §8.4）。

## 5. 数据格式

### 5.1 chat-in.md 追加格式（watcher 唯一写）

每条 `chat` 追加为一段，原话原样、多行保留：

```markdown
### 2026-09-06 17:40:12
用户原话，可以
跨越多行

```

（`### ` + watcher 本地时间 `YYYY-MM-DD HH:MM:SS`，空行收尾。追加用 `"a"` 模式，utf-8。）

### 5.2 chat-out.md 消息边界（watcher 读）

通讯官追加的每条消息之间以**单独一行 `---`** 分隔。watcher 缓存未闭合内容，见到 `---` 才发布前一段，避免半截消息上屏。

> 执行注意：liaison 的 `subagent-prompt.md` 未规定此分隔符。执行本计划时，在生成通讯官的指令里补一句"给用户的每条消息之间用单独一行 `---` 分隔"；若部署方不允许改，watcher 退化为按轮询块整体发布（接受偶发合并），并在 README 注释该降级。

### 5.3 roomId 算法（两端必须逐字节一致）

FNV-1a 32bit，对房间名 UTF-8 字节计算，输出 `r` + 8 位小写 hex：

```python
# Python
def room_id(name: str) -> str:
    h = 2166136261
    for b in name.encode("utf-8"):
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return "r%08x" % h
```

```js
// JavaScript
function roomId(name) {
  let h = 2166136261;
  for (const b of new TextEncoder().encode(name)) {
    h = Math.imul(h ^ b, 16777619) >>> 0;
  }
  return "r" + h.toString(16).padStart(8, "0");
}
```

（不用 SHA-1：避免依赖 `crypto.subtle`——各浏览器对 file:// 是否算安全上下文处理不一；roomId 只需稳定唯一，保密由 R 承担。）

### 5.4 唤醒事件（watcher → stdout JSON 行 + notify-cmd）

```json
{"event":"chat_in_appended","roomId":"r1a2b3c4d","mailbox":"/abs/path/mailbox","ts":1757155200}
```

事件枚举：`watcher_started`、`chat_in_appended`（追加 chat-in.md 后）、`outbox_appended`（outbox.md mtime 变化且内容增长）、`status_changed`、`done_detected`（status.md 出现单独一行 `DONE`，只发一次）、`error`（带 `detail`）。

通知方式两路并发：stdout 打印该 JSON 行；若启动参数含 `--notify-cmd "<cmd>"`，则异步执行该命令（事件 JSON 同时放入 argv[1] 与环境变量 `LIAISON_EVENT`，10 秒超时，失败只记 stderr 不中断主循环）。

## 6. chat-watcher.py 规格

### 6.1 职责边界（契约四件事，一件不多）

1. **搬运进**：入向频道 `chat` → 按 §5.1 追加 `chat-in.md`，随后触发 `chat_in_appended` 唤醒。
2. **即时回**：每条 `chat` 落盘后立刻向出向发 `ack`（默认 text=`收到，马上转给开发主力`，常量可改）。**直接发出向，不写 chat-out.md。**
3. **唤醒**：§5.4。通讯官不轮询，一切由 watcher 叫醒。
4. **搬运出**：轮询 `chat-out.md`（2 秒，mtime+字节偏移），新增闭合消息按 §5.2 切分后以 `msg` 发到出向。

**禁止**：不分类、不翻译、不确认（那是通讯官的活）；不写 chat-in.md 以外的任何 mailbox 文件；不实现聊天室 UI 逻辑。

### 6.2 运行环境与命令行

- 纯标准库（`urllib.request`/`json`/`threading`/`argparse`/`subprocess`/`os`/`sys`/`time`），Python ≥3.8，跨平台，零 pip 安装。
- 用法：

```bash
python chat-watcher.py [--mailbox ./mailbox] [--relay https://ntfy.sh] \
       [--notify-cmd "<cmd>"] [--lobby lia-<R>-lobby]
```

`--mailbox` 默认脚本同目录 `mailbox/`；`--relay`、`--lobby` 默认取文件头部烘焙常量（见 §9），CLI 仅用于测试覆盖。

### 6.3 线程模型（伪代码）

```
main:
  R, RELAY = baked constants (CLI override)
  mailbox = resolve(--mailbox); assert chat-in.md 等文件存在
  emit(watcher_started)
  start thread lobby_loop()            # SSE 订阅大厅
  start thread file_poll_loop()        # 2s 轮询 chat-out.md / outbox.md / status.md
  join threads; 捕获 KeyboardInterrupt 干净退出

lobby_loop():                          # 断线重连(退避) + poll 兜底
  for envelope in subscribe(RELAY, lobby_topic):
      if envelope.type == "join":
          rid = envelope.roomId
          if rid not in bindings:
              bindings[rid] = mailbox
              start thread room_in_loop(rid)   # 订阅入向
          publish(out_topic(rid), {type:"joined", roomId:rid, ...})

room_in_loop(rid):
  for envelope in subscribe(RELAY, in_topic(rid)):   # 去重 by id
      if envelope.type == "chat":
          append_chat_in(mailbox, envelope.text)     # §5.1
          publish(out_topic(rid), {type:"ack", ...})  # 即时回
          emit(chat_in_appended, rid)                # 唤醒

file_poll_loop():
  track offsets/mtime of chat-out.md, outbox.md, status.md
  chat-out.md 增长: 按 §5.2 切出闭合消息 → publish msg 到**所有已绑定房间**的出向
  outbox.md 增长 → emit(outbox_appended)
  status.md 变化 → emit(status_changed); 含 DONE 行 → emit(done_detected) 一次
```

注意：`msg` 广播到所有已绑定房间（v1 单 mailbox，所有房间看同一份 chat-out.md）。

### 6.4 SSE 客户端（stdlib 实现要点）

`urllib.request.urlopen(url, timeout=70)` 逐行读；空行/`: keepalive` 跳过；`data: ` 行 JSON 解析，取 `message` 字段（base64 标志时解码）；任何异常→退避重连（1/2/5/10s 封顶），重连前先 `json?poll=1&since=<lastTs>` 补漏。

## 7. chatroom.html 规格

### 7.1 硬要求

- 单个 .html 文件，零外部资源（不引任何 CDN/字体/框架），file:// 双击即用，Chrome/Edge/Firefox/Safari 现代版本可用。
- 只用 `fetch` + `EventSource` + `localStorage` + `TextEncoder`。
- 文件头部集中放烘焙常量（注释标明"生成时填写，勿改"）：`RELAY_BASE`、`LOBBY_TOPIC`、`R_SUFFIX`、`PROTOCOL_VERSION`。
- 全部界面文字从 §7.4 文案表照抄；出现任何技术词即验收失败。

### 7.2 两个屏

**屏 1 · 起名屏**（首次或 localStorage 无记录）：标题+说明+房间名输入框+开始按钮。用户确认后：计算 roomId → 发 join 到大厅 → 订阅出向 → 15 秒内收到 `joined` 进屏 2；超时进屏 2 但状态灯显"对面还没就位"（消息照常发，会合点会暂存）。

**屏 2 · 聊天屏**：顶部房间名+连接状态灯；中部消息流（用户右/通讯官左/系统灰）；底部意图快捷词+多行输入框+发送按钮。

### 7.3 面向 AI 对话的功能（约束 2）

1. **意图快捷词**（输入框上方一排小按钮，点击把前缀插进输入框，用户可继续写）：`【必须做】` `【提个建议】` `【这里不对】` `【出问题了】` `【先停一下】` —— 对应通讯官的 要求/建议/反馈/疑似bug/STOP 分类，帮用户把话说"对"。
2. **消息格式化**：``` 代码块等宽字体+单独底色+「复制」按钮；URL 自动变链接；其余纯文本原样换行。先 HTML 转义再渲染，杜绝注入。
3. **上下文提示**：每条 ack 灰条显示"已收到，正在转交"；通讯官消息与普通系统条样式分明；状态灯三色：绿"已接通"/黄"正在重连…"/红"连不上传话点"。
4. **历史不丢**：消息流按 roomId 存 localStorage（上限 200 条，超出裁最旧），刷新/重开自动恢复。
5. **发送体验**：Enter 发送、Shift+Enter 换行（提示字写在输入框下）；发送失败（网络异常）标红该气泡并提供"重发"。

### 7.4 界面文案表（逐字照抄）

| 位置 | 文案 |
|------|------|
| 起名屏标题 | 先给这次对话起个名字 |
| 起名屏说明 | 这个名字就是待会儿你和 AI 团队碰头的"房间名"。想一个自己记得住的，比如"写小说"或"周报"。 |
| 输入框占位 | 房间名，例如：写小说 |
| 开始按钮 | 开始 |
| 接通中 | 正在接通对面… |
| 已接通 | 已接通，可以说话了 |
| 对面未就位 | 对面还没就位。你的话会先存着，对面一上线就能看到。 |
| 断网 | 连不上传话点。请检查网络，本页会自动重试。 |
| 重连中 | 正在重连… |
| ack 灰条 | 已收到，正在转交 |
| 输入框占位（聊天） | 说点什么… |
| 输入框下提示 | Enter 发送，Shift+Enter 换行 |
| 快捷词 | 必须做 / 提个建议 / 这里不对 / 出问题了 / 先停一下 |
| 复制按钮 | 复制 / 已复制 |
| 页脚小字 | 这个页面和你说的每句话，都会原样转给 AI 团队。 |

## 8. 功能边界矩阵

| 能力 | chatroom.html | chat-watcher.py | 通讯官 | 主 agent |
|------|:---:|:---:|:---:|:---:|
| 用户输入渲染/发送 | ✅ | | | |
| 入向订阅+追加 chat-in.md | | ✅ | | |
| 即时回(ack) | | ✅ | | |
| chat-out.md 追加 | | | ✅ | |
| chat-out.md 搬运到出向 | | ✅ | | |
| outbox/status 监视+唤醒 | | ✅ | | |
| 分类/翻译/确认/成卡 | | | ✅ | |
| 写 inbox/outbox-replies | | | ✅ | |
| 写 outbox/inbox-status/status | | | | ✅ |
| 会合点地址/房间名知识 | ✅(烘焙) | ✅(烘焙) | 不需要 | 不需要 |

## 9. 交付与部署（执行 AI 照做）

1. 生成 24 位小写 hex 随机串 R（`secrets.token_hex(12)`）。
2. 写出 `chat-watcher.py`、`chatroom.html`，头部烘焙同一 R 派生的 `LOBBY_TOPIC=lia-<R>-lobby` 与 `RELAY_BASE=https://ntfy.sh`。
3. 用这两个文件**替换 liaison skill `files/` 下的同名占位文件**（运行时 mailbox 里的副本由 SKILL.md 第 2 步的复制动作产生）。
4. 把最终的 `chatroom.html` 经**本次会话已有的交付通道**给用户一次（例如当前对话附件/产物面板），请用户保存到电脑并双击使用。**这是一次性安装**：此后 AI 不再向用户发送任何东西，用户手里这份 html 反复使用。
5. 云端运行时：主 agent 按 SKILL.md 第 3 步启动 watcher（`--mailbox` 指向工作目录的 `mailbox/`，按宿主机制接好 `--notify-cmd`）。

## 10. 安全与隐私

- 消息**明文路过**公共会合点；保密性 = R 与 rid 不可猜。界面与交付说明中不对用户渲染此风险细节，但执行 AI 必须在本计划交接说明里向部署方讲清。
- 敏感任务：自建 ntfy（`docker run -p 80:80 binwiederhier/ntfy serve`），把两个文件头部 `RELAY_BASE` 改为自建地址即可，协议不变。
- 会合点缓存约 12 小时，过期消息不可补。

## 11. 验收清单（逐项可见再算过）

- T1 双击 html，出现起名屏，文案与 §7.4 逐字一致，无任何技术词。
- T2 输入"写小说"点开始，会合点大厅频道出现 `join`，其 roomId 与 §5.3 算法手算结果一致。
- T3 watcher 启动后收到 join，出向收到 `joined`，页面状态变"已接通"。
- T4 发"你好"，15 秒内页面出现灰条"已收到，正在转交"；`chat-in.md` 末尾新增 §5.1 格式的一段。
- T5 发一条 5KB 消息，分片到达且 chat-in.md 内拼接完整无乱码。
- T6 往 chat-out.md 追加"测试一\n\n---\n\n测试二\n\n---\n"，页面依次出现两条独立气泡。
- T7 改 outbox.md / status.md，stdout 出现对应 JSON 行；`--notify-cmd` 命令被执行且 `LIAISON_EVENT` 可取。
- T8 status.md 写入单独一行 `DONE`，`done_detected` 恰好触发一次。
- T9 杀掉 watcher 再发消息，页面提示"对面还没就位"；重启 watcher 后补收成功。
- T10 断开网络 30 秒再恢复，页面自动从"连不上传话点"恢复"已接通"，期间消息不丢不重。
- T11 全程 watcher 只写 chat-in.md（`git diff` 或文件哈希比对其余 mailbox 文件无变化）。
