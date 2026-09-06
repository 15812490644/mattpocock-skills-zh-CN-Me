# Liaison 聊天室接入

为 liaison skill 的 mailbox 接入真实聊天室（chatroom.html）与监控脚本（chat-watcher.py）的设计 context。目标：产出一份交给下一个 AI 执行的计划书。

## Language

**通讯官**:
拥有独立上下文的子 agent，充当用户（非程序员）与主 agent 之间的缓冲层：把主 agent 的技术问题翻译成大白话送给用户，把用户的话分类、确认后投递给主 agent。
_Avoid_: 子代理、小助手、bot

**mailbox**:
working directory 下 `mailbox/` 目录里的一组 markdown 文件，通讯官与主 agent 经它异步通信；文件契约的唯一权威是 liaison skill 的 `mailbox-format.md`。
_Avoid_: 信箱、消息目录

**缝隙**:
主 agent 查收 inbox 的时机——每完成一个原子任务之后、开始下一个之前。原子任务中途不收件。
_Avoid_: 间隙、检查点

**监控脚本 (watcher)**:
唯一常驻进程 `chat-watcher.py`，桥接聊天室与 mailbox，职责四件：搬运进、即时回、唤醒、搬运出。只做搬运与叫醒，不分类、不翻译、不确认。
_Avoid_: 守护进程、监听器、消息服务器

**搬运进**:
watcher 把聊天室里的用户新消息原样追加到 `chat-in.md`（每条一段，带时间戳）。

**即时回**:
watcher 检测到 `chat-in.md` 新消息后，直接往聊天室发一条自动应答（不写 `chat-out.md`，保持通讯官是其唯一写入者）；实质答复由通讯官随后补上。

**搬运出**:
watcher 把 `chat-out.md` 的新增内容推送到聊天室展示。

**唤醒**:
watcher 检测到 `chat-in.md` / `outbox.md` 新增或 `status.md` 变化后，按宿主机制叫醒通讯官；通讯官不轮询，一切由 watcher 叫醒。

**房间 (room)**:
一次 liaison 会话在聊天室侧的标识。房间名由用户在聊天室首屏自起，首条消息携带它发到大厅；watcher 据此匹配并连接对应的频道对。
_Avoid_: 会话、频道（"频道"专指入向/出向通道）

**会合点 (relay)**:
浏览器与 watcher 都能出站访问的公网消息中转服务（默认 ntfy.sh，地址是配置项，可换自建）。因为 AI 在云端运行，浏览器与 watcher 不共享 localhost，必须经会合点交换消息。
_Avoid_: 中继服务器、消息队列

**大厅 (lobby)**:
会合点上的约定公共频道，地址含构建时烘焙的随机串（两个文件同一份）。watcher 启动后守在大厅；用户首条消息（含房间名）发到大厅。

**绑定 (binding)**:
watcher 在大厅看到某房间名的首条消息后，接通该房间入向/出向频道、开始桥接的动作。绑定完成前，agent 一侧无法向该房间发任何消息。

**入向频道 / 出向频道**:
每个房间在会合点上的两条单向通道：入向 = 聊天室 → watcher（对应 `chat-in.md`），出向 = watcher → 聊天室（对应 `chat-out.md` + 即时回）。方向分离镜像 mailbox 的"一个文件一个写入者"铁律。

**一个文件一个写入者**:
mailbox 铁律——每个文件只有唯一角色能写，其余角色只读，没有例外。

**只加不改**:
日志类文件只追加，从不修改或删除旧内容；同一 id 出现多行时以最后一行为准。`status.md` 是唯一例外（主 agent 覆盖式更新，单一写入者）。
