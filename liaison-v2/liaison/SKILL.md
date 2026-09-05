---
name: liaison
description: 为长时间自动化任务建立用户与主 agent 之间的通讯缓冲层。（需先安装 matt skills）
argument-hint: "matt skills 压缩包路径 + 本次任务描述"
disable-model-invocation: true
---

# Liaison · 通讯官

为长时间自动化工作建立**通讯官**——一个拥有独立上下文的子 agent，充当用户（非程序员）与主 agent 之间的缓冲层：它把主 agent 的技术问题翻译成大白话送到用户面前，把用户随口说的话分类、确认后，在不打断开发的时机投递给主 agent。

**mailbox** 是 working directory 下 `mailbox/` 里的一组 markdown 文件，两个 agent 经它异步通信。**缝隙**是主 agent 查收 inbox 的时机——每完成一个原子任务之后。

## Process

### 1. 安装 matt skills

检查本机 skills 目录是否已有 `ask-matt`。已存在则跳过本步。

不存在则找到 matt skills 压缩包：用户给了路径直接用；没给先检查本 skill 压缩包的旁边（两个压缩包通常一起送达），再搜索 `/root/uploads`、`/workspace`、`/tmp` 下内含 `ask-matt` 的 zip；都没有再问用户。解压后把每个含 `SKILL.md` 的技能目录拷入本机 skills 目录。

完成标准：`ask-matt/SKILL.md` 在本机 skills 目录中可读，压缩包内其余技能均已拷入且各自带 `SKILL.md`。

### 2. 建立 mailbox

在 working directory 创建 `mailbox/`，把本 skill `files/` 下的九个文件原样复制进去：`inbox.md`、`inbox-status.md`、`outbox.md`、`outbox-replies.md`、`status.md`、`chat-in.md`、`chat-out.md`、`chatroom.html`、`chat-watcher.py`。

文件契约的唯一权威是 [`mailbox-format.md`](mailbox-format.md)：读它，按它行事。规则只住在那里，复制到别处就会产生两份真相。

完成标准：`mailbox/` 九文件就位，且你已读完 `mailbox-format.md`。

### 3. 聊天室接入（hard gate）

`chatroom.html` 与 `chat-watcher.py` 是**占位空文件**：真实聊天室由用户提供，监控脚本桥接聊天室与 `chat-in.md` / `chat-out.md`，并负责事件检测、按需唤醒通讯官（契约见 `mailbox-format.md`）。正式开工以接入完成为前提。

问用户一次："聊天室和监控脚本就位了吗？"

- 未就位——告知这是开工前提，停下，请用户完成接入后重新运行本 skill。
- 已就位——启动监控脚本（若用户尚未启动），请用户在聊天室发一条测试消息。

完成标准：测试消息出现在 `chat-in.md` 末尾。

### 4. 生成通讯官

以 long-running background 方式 spawn 子 agent（用宿主环境原生的 Task/Agent 类能力），指令为 [`subagent-prompt.md`](subagent-prompt.md) 全文，先把其中 `<SKILL目录>` 与 `<MAILBOX目录>` 两个占位符替换为真实绝对路径。通讯官是**事件驱动**的：由监控脚本唤醒，不自行轮询。

完成标准：通讯官已运行，且 `chat-out.md` 末尾出现它的就位报告。

### 5. ask-matt 路由

收集**态势报告**，交给已安装的 `ask-matt` 路由：读取其 `SKILL.md` 并套用其路由逻辑。态势报告包含：

> - **智能体与模型**：宿主 harness 名称、模型（可探测则自动探测，否则问用户）
> - **环境与沙箱**：OS、运行时版本、网络、预装软件、沙箱限制（自动探测）
> - **配置**：已安装 skills 列表、issue tracker 状态
> - **现状**：working directory 里已有什么
> - **用户要求**：本次任务目标与约束（问用户）

完成标准：拿到一条明确 flow（用哪些 skills、什么顺序、从哪个开始），并写入 `status.md` 的阶段字段。

### 6. 开工，遵守 mailbox 纪律

按 ask-matt 给的 flow 工作。细则全在 `mailbox-format.md`，骨架是五条：

- **缝隙查收**：每完成一个原子任务，先查 `inbox.md` 新卡片，再开始下一个；处理完在 `inbox-status.md` 追加一行结果。
- **经 outbox 问人**：需要用户决策的事追加进 `outbox.md`；通讯官的答复写在 `outbox-replies.md`，缝隙查收。等答复期间别干等——先做不依赖该答复的部分。
- **实时状态**：`status.md` 始终反映当前在做什么、卡在哪、走到哪个阶段。
- **STOP 最高优先**：`inbox.md` 出现 STOP 卡片时，完成手上原子操作后立即暂停，并在 `outbox.md` 说明停在哪、为什么。
- **只加不改**：mailbox 里你名下的文件只追加；处理结果和状态更新一律写新行，不修改旧内容。

完成标准：任务完成，`status.md` 写入单独一行 `DONE`。通讯官检测到 DONE 后自会向用户发最终总结并退出——你只管写 DONE。
