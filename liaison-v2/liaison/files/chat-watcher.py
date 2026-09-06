#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chat-watcher.py — Liaison 聊天室哨兵（唯一常驻进程）
====================================================

职责（来自 mailbox 契约，四件）：
  1. 搬运进：聊天室里的用户新消息 → 原样追加到 chat-in.md（每条一段，带时间戳）
  2. 即时回：追加成功后立刻向房间广播一条自动应答（“收到，马上转给开发主力”）
  3. 唤醒：chat-in.md / outbox.md / status.md 有变化 → 按宿主机制唤醒通讯官
  4. 搬运出：chat-out.md 新增内容 → 推送到聊天室

它只做搬运、即时应答、叫醒和文件服务；分类、翻译、确认永远是通讯官的事。

技术特性
--------
- 纯 Python 标准库（≥3.8），零 pip 依赖，单文件
- WebSocket 服务端：手写 RFC6455 握手 + 帧编解码（文本帧 / ping / pong / 分片）
- HTTP 服务端：静态网页、图片、健康检查、房间列表、发送、上传、长轮询（备用通道），全 CORS
- 多房间：mailbox/rooms/<roomId>/（九文件契约 + images/ + room.json）
- 文件监听：mtime/size 轮询（1s），比 inotify 更可移植
- 心跳：服务端 25s ping，90s 无活动剔除死连接
- 增量历史：客户端连 WebSocket 时带 &since=<id> 只拉新消息
- 历史翻页：GET /history?room=&before=<id>&limit=（before 游标，向前翻页）
- mailbox 查看：GET /files?room=（九文件实时内容，教学演示用）
- 快照导出：GET /export?room=&format=md|zip（九文件 + 图片 打包留档/交接，下载附件）
- 全房导出：GET /export-all?format=zip|md（所有房间一起打包，管理员/交接视角，全局 token 鉴权）
- 安全：房间号即凭证；--token 鉴权（所有 WS/HTTP 端点，查询参数 ?token=）；路径穿越防护；图片类型白名单 + 大小限制 + sha1 去重
- 限流：每连接 10 秒内最多 15 条
- WAKE_MODE：none（宿主自己盯文件，默认）| signal-file（写 .wake 文件）
- --demo：内置“演示通讯官”bot 线程，模拟真实通讯官/主 agent 的完整闭环
  （大白话确认、分类成卡、待答问题、进度汇报、STOP 响应、DONE 总结），仅用于演示
  免打扰：POST /mute {room,on} 关/开 45s 进度汇报（仅演示模式）
  剧情包：房间 1001（修手机网页）/ 1002（做跑酷小游戏）各自独立语境与 ASK 问题
  彩蛋：1002 说「玩个游戏/石头剪刀布」进入 ASCII 石头剪刀布（连比分），「不玩了」结束
  彩蛋：1001 说「喝杯咖啡」触发 ASCII 手冲咖啡动画（磨豆→萃取→拉花）
  表情反应：c→s react{target,emoji} / POST /react；s→c reaction 帧；
           白名单 9 种（含 👀 = 看到了）；toggle 语义；room.json 持久化（重启不丢，重置时清空）
           演示 bot 会自动对用户消息点反应（模拟主 agent 已读+态度；编辑确认用 👀）
  消息撤回：c→s revoke{target} / POST /revoke；s→c revoke 帧；2 分钟内、仅自己的
           消息可撤；展示层遮蔽（chat-in.md 契约「只加不改」不动，room.json 元数据）
  消息编辑：c→s edit{target,text} / POST /edit；s→c edit 帧；2 分钟内、仅自己的
           消息可改（已撤回的不能再改）；新文本存 room.json 元数据（edited），
           输出视图替换展示 + edited 标记（「已编辑」角标）；幂等（文本没变不广播）
  编辑历史：v1.9.0 起 edited 元数据带 hist 版本链（原文+历次改前文本，单条最多 6 版）；
           edit 帧 / history / poll(editedMap) 均携带 hist；消息对象带 editHist 字段；
           前端点「已编辑」徽章可看版本时间线（原文永远留在 chat-in.md 可审计）
  已读回执：s→c ack_read{target,by} —— 通讯官读过用户消息后 ✓ 升级 ✓✓；
           演示 bot 在处理消息前先「看一眼」（~0.9s）再标记；room.json 持久化
  反应统计：GET /reactions?room=（单房间）/（全局聚合）—— 总数/各 emoji/最热消息
  剧情：暂停后说「继续」→ 恢复执行；咖啡彩蛋说「再来一杯」→ 双心拉花续杯
- --reset：POST /reset {room}（仅演示模式）重写模板、清图、广播 reset 帧

网络协议（JSON 文本帧）
----------------------
s→c: hello / history / chat / ack / pending / task / typing / status / error / pong /
     reset（房间重置）/ mute（演示免打扰开关）/ reaction（表情反应同步）/
     revoke（消息撤回同步）/ edit（消息编辑同步）/ ack_read（已读回执）
c→s: chat{text,tempId} / image{name,caption,dataBase64,tempId} / typing{on} / ping{t} /
     react{target,emoji} / revoke{target} / edit{target,text}

消息对象: {id:int, from:"user"|"liaison"|"system", text:str, ts:毫秒,
           client?:str, image?:{file:"<房间>/<文件名>", name, caption},
           edited?:bool(输出视图标记), read?:bool(输出视图标记),
           editTs?:毫秒(最后编辑时间), editHist?:[{text,ts}...](版本链，旧→新含原文)}

启动
----
  python3 chat-watcher.py [--host 0.0.0.0] [--port 8765] [--mailbox <dir>]
                          [--html <chatroom.html 路径>] [--demo]
                          [--create-room <id> --title <标题>] [--wake-mode none|signal-file]
                          [--token <口令>] [--log-file <路径>] [--print-patch]

对外地址（给用户的大白话）：
  浏览器直接打开  http://<ip>:<port>/   —— 就是聊天室（无需传文件）
  或者把 chatroom.html 发给用户，双击打开后填 http://<ip>:<port> 与房间号
"""

import argparse
import base64
import binascii
import hashlib
import io
import json
import os
import queue
import random
import re
import selectors
import signal
import socket
import struct
import sys
import threading
import time
import zipfile
import urllib.parse
from datetime import datetime

VERSION = "chat-watcher/1.9.0"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# ---------------------------------------------------------------- 常量开关
WAKE_MODE = "none"          # none | signal-file
POLL_INTERVAL = 1.0         # 文件轮询间隔（秒）
WS_PING_INTERVAL = 25.0     # 服务端心跳间隔
WS_IDLE_TIMEOUT = 90.0      # 连接空闲上限（收到任何帧即续期）
TICK = 0.15                 # 主循环 tick
RATE_WINDOW = 10.0          # 限流窗口
RATE_MAX = 15               # 窗口内最大消息数
TEXT_MAX = 8000             # 文字消息上限
REACT_EMOJIS = ("👍", "❤️", "😂", "😮", "😢", "🙏", "🎉", "🔥", "👀")  # 反应白名单（👀 = 看到了/在关注，编辑确认也用它）
REVOKE_WINDOW_MS = 2 * 60 * 1000  # 消息撤回时间窗（发送后 2 分钟内）
EDIT_WINDOW_MS = 2 * 60 * 1000    # 消息编辑时间窗（与撤回同窗口：微信同款习惯）
EDIT_MAX = 40                     # 编辑历史条数上限（room.json 只留最近 N 条，防无限膨胀）
EDIT_HIST_MAX = 6                 # 单条消息保留的版本链长度（原文+历次改前文本，最多 6 版）
IMAGE_MAX_BYTES = 6 * 1024 * 1024     # 图片原始字节上限
IMG_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
             "gif": "image/gif", "webp": "image/webp"}
ROOM_ID_RE = re.compile(r"^[a-z0-9]{3,16}$")
SEG_RE = re.compile(r"^##\s*\[([ULS])-?(\d+)\]\s*(.*)$")
ASK_RE = re.compile(r"^##\s*\[ASK-(\d+)\]")
REPLY_RE = re.compile(r"^-\s*ASK-(\d+)\s*\|", re.M)
IMG_BODY_RE = re.compile(r"^\[图片\s+images/([^\]\s]+)\]\s*(.*)$", re.S)

# ---------------------------------------------------------------- 九文件模板
TEMPLATES = {
    "chat-in.md": (
        "# chat-in · 聊天室 → 通讯官\n\n"
        "只有监控脚本能写本文件：只追加、不修改。\n"
        "监控脚本把用户在聊天室的原话逐条追加到本文件（每条一段，带时间戳），"
        "随即自动应答并唤醒通讯官处理。\n"
        "格式与纪律见 mailbox-format.md。\n\n"
        "段落格式（监控脚本写入）：\n\n"
        "    ## [U-<毫秒时间戳>] <ISO时间> <客户端短号>\n"
        "    <用户原话，可多行>\n\n"
        "图片消息正文一行：[图片 images/<房间号>/<文件名>] <说明>\n"
        "系统自动应答（同样只有监控脚本能写）：\n\n"
        "    ## [S-<毫秒时间戳>] <ISO时间> watcher\n"
        "    <自动应答>\n"
    ),
    "chat-out.md": (
        "# chat-out · 通讯官 → 聊天室\n\n"
        "只有通讯官能写本文件：只追加、不修改。\n"
        "通讯官把给用户的大白话消息追加到本文件末尾；监控脚本把新增内容推送到聊天室。\n"
        "格式与纪律见 mailbox-format.md。\n\n"
        "段落格式（通讯官写入）：\n\n"
        "    ## [L-<毫秒时间戳>] <ISO时间>\n"
        "    <给用户的大白话，可多行>\n\n"
        "时间戳用毫秒整数；监控脚本按时间戳把 chat-in/chat-out 归并成完整对话。\n"
    ),
    "inbox.md": (
        "# inbox · 通讯官 → 主agent\n\n"
        "只有通讯官能写本文件：只追加、不修改。\n"
        "用户确认后的消息卡片追加到末尾；主agent 在每个缝隙查收新卡片，"
        "处理结果写到 inbox-status.md。\n格式与纪律见 mailbox-format.md。\n\n"
        "    ## [MSG-001]\n"
        "    - 类型: 要求 | 反馈 | 建议 | 疑似bug | STOP\n"
        "    - 优先级: 高 | 中 | 低\n"
        '    - 用户原话: "……"\n'
        "    - 整理后: ……\n"
        "    - 投递时机: 下一缝隙 | 攒批\n"
    ),
    "inbox-status.md": (
        "# inbox-status · 主agent → 通讯官/用户\n\n"
        "只有主agent 能写本文件：只追加、不修改。\n"
        "每处理完一张卡片，追加一行：- MSG-xxx | 已处理 | 一句话处理结果\n"
        "同一编号以最后一行为准。格式与纪律见 mailbox-format.md。\n"
    ),
    "outbox.md": (
        "# outbox · 主agent → 通讯官\n\n"
        "只有主agent 能写本文件：只追加、不修改。\n"
        "需要用户知道或回答的条目追加到末尾；通讯官翻译、提问，"
        "答复写到 outbox-replies.md。\n格式与纪律见 mailbox-format.md。\n\n"
        "    ## [ASK-001]\n"
        "    - 问题: ……\n"
        "    - 需要用户回答: ……\n"
    ),
    "outbox-replies.md": (
        "# outbox-replies · 通讯官 → 主agent\n\n"
        "只有通讯官能写本文件：只追加、不修改。\n"
        "每条用户答复追加为一行：- ASK-xxx | 已答复 | 答复内容\n"
        "同一编号以最后一行为准。格式与纪律见 mailbox-format.md。\n"
    ),
    "status.md": (
        "# 状态\n- 当前: 初始化中\n- 阶段: 未开始\n- 阻塞: 无\n- 更新于: -\n"
    ),
}

# mailbox 文件查看顺序（/files 端点）
FILE_ORDER = [
    "chat-in.md", "chat-out.md", "inbox.md", "inbox-status.md",
    "outbox.md", "outbox-replies.md", "status.md", "room.json",
]

# 通讯官提示词补丁（--print-patch 输出，交付物 3）
PATCH_TEXT = """# 通讯官补丁 · 聊天文件段落格式（追加到 subagent-prompt.md 末尾）

## chat 文件写入格式（务必遵守）

给用户的大白话消息，追加到 `chat-out.md` 末尾，格式：

    ## [L-<毫秒时间戳>] <ISO时间>
    <给用户的大白话，可多行>

- 每条消息一个段落，`L-` 前缀 + 毫秒时间戳起头，随后是 ISO 时间。
- 只追加，不修改旧段落。监控脚本按时间戳把 chat-in / chat-out 归并成完整对话。
- 用户答复 ASK 时，除写 outbox-replies.md 外无需额外动作——监控脚本会自动同步待答横幅。
- 用户消息可能带一行引用前缀：`↩ 引用 <谁> <时间> 的「摘要」：`。
  这是用户在聊天室里引用了某条旧消息；答复时先看清引用的上下文再回，
  需要时可在回复里自然提及（例：明白，你指的是刚才那条关于 XX 的消息…）。
  引用行只是上下文，不是问题本身，无需单独答复。

## 唤醒方式

{wake_desc}
"""

WAKE_DESCS = {
    "none": "宿主自身用文件监视能力盯住本房间的 chat-in.md、outbox.md、status.md；"
            "文件一变即被唤醒。监控脚本保证写入立即落盘（flush+fsync）。",
    "signal-file": "有新事件时监控脚本会写 `.wake` 文件（JSON：event/ts）到本房间目录；"
                   "宿主侧观察到该文件变化即唤醒通讯官，随后可删除。",
}


def now_ms():
    return int(time.time() * 1000)


def iso_now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


LOG_FILE = None


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    if LOG_FILE:
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass


# ================================================================ WS 帧编解码
def ws_accept_key(key):
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


def ws_encode(opcode, payload: bytes) -> bytes:
    """服务端→客户端：不带掩码。"""
    frame = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        frame.append(n)
    elif n < 65536:
        frame.append(126)
        frame += struct.pack(">H", n)
    else:
        frame.append(127)
        frame += struct.pack(">Q", n)
    frame += payload
    return bytes(frame)


def ws_parse(buf: bytes):
    """增量解析一个帧。返回 (fin, opcode, payload, consumed)；
    数据不足返回 (None, None, None, 0)；帧过大抛 ValueError。"""
    if len(buf) < 2:
        return None, None, None, 0
    b1, b2 = buf[0], buf[1]
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    ln = b2 & 0x7F
    off = 2
    if ln == 126:
        if len(buf) < 4:
            return None, None, None, 0
        ln = struct.unpack(">H", buf[2:4])[0]
        off = 4
    elif ln == 127:
        if len(buf) < 10:
            return None, None, None, 0
        ln = struct.unpack(">Q", buf[2:10])[0]
        off = 10
    if ln > 9 * 1024 * 1024:
        raise ValueError("帧过大")
    mask = b""
    if masked:
        if len(buf) < off + 4:
            return None, None, None, 0
        mask = buf[off:off + 4]
        off += 4
    if len(buf) < off + ln:
        return None, None, None, 0
    payload = buf[off:off + ln]
    if masked and mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return fin, opcode, payload, off + ln


# ================================================================ 连接
class Conn:
    _seq = [0]

    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        Conn._seq[0] += 1
        self.cid = "c%d" % Conn._seq[0]
        self.kind = "http"          # http | ws | polling
        self.room = None            # Room 对象
        self.client = ""            # 客户端短号
        self.inbuf = b""
        self.outbuf = b""
        self.frag_op = None
        self.frag_buf = b""
        self.last_seen = time.time()
        self.last_ping_sent = 0.0
        self.rates = []
        self.req = None             # 已解析的 HTTP 请求头
        self.body_left = 0
        self.http_body = b""
        self.want_write = False
        self.closed = False
        self.poll_since = 0
        self.poll_deadline = 0.0

    def send_bytes(self, data: bytes):
        if self.closed:
            return
        self.outbuf += data
        self.flush()

    def flush(self):
        while self.outbuf and not self.closed:
            try:
                n = self.sock.send(self.outbuf[:65536])
                self.outbuf = self.outbuf[n:]
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                self.closed = True
                return

    def send_json(self, obj):
        self.send_bytes(ws_encode(1, json.dumps(obj, ensure_ascii=False).encode("utf-8")))


# ================================================================ 房间
class Room:
    def __init__(self, rid, base):
        self.id = rid
        self.base = base
        self.title = rid
        self.msgs = []               # [{id,from,text,ts,client?,image?}]
        self.conns = set()
        self.pending = []            # [{askId,question}]
        self.task = {"current": "初始化中", "stage": "未开始", "blocker": "无",
                     "done": False, "updated": ""}
        self.done_announced = False
        self.demo_muted = False      # 演示模式：免打扰（关掉每 45s 的进度汇报）
        self.reactions = {}          # {str(msgId): {emoji: [client,...]}}（room.json 持久化）
        self.revoked = {}            # {str(msgId): True}（展示层撤回标记，room.json 持久化）
        self.edited = {}             # {str(msgId): {text, ts, from}}（展示层编辑内容，room.json 持久化）
        self.read = {}               # {str(msgId): ts}（通讯官已读标记，room.json 持久化）
        self._filestate = {}
        meta = os.path.join(base, "room.json")
        if os.path.exists(meta):
            try:
                with open(meta, encoding="utf-8") as f:
                    d = json.load(f)
                self.title = d.get("title", rid)
                rx = d.get("reactions")
                if isinstance(rx, dict):
                    self.reactions = rx
                rv = d.get("revoked")
                if isinstance(rv, dict):
                    self.revoked = {k: True for k in rv}
                ed = d.get("edited")
                if isinstance(ed, dict):
                    # 兼容旧版无 edited 字段；每条 {text, ts}
                    self.edited = {k: {"text": str(v.get("text", "")),
                                        "ts": int(v.get("ts", 0))}
                                   for k, v in ed.items()
                                   if isinstance(v, dict) and v.get("text")}
                rd = d.get("read")
                if isinstance(rd, dict):
                    self.read = {k: True for k in rd}
            except (OSError, ValueError):
                pass
        self.rescan()

    def path(self, name):
        return os.path.join(self.base, name)

    def imgdir(self):
        return os.path.join(self.base, "images")

    # ---------- 表情反应 + 撤回（room.json 持久化；不碰九文件，契约零风险）
    def save_meta(self):
        """room.json 原子写入（临时文件 + rename，进程崩溃也不会写坏）。"""
        data = {"id": self.id, "title": self.title, "created": now_ms(),
                "reactions": self.reactions, "revoked": self.revoked,
                "edited": self.edited, "read": self.read}
        tmp = os.path.join(self.base, "room.json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path("room.json"))
        except OSError:
            pass

    def add_reaction(self, target, emoji, client):
        """toggle 语义：同一 client 同一 emoji 再点 = 取消。返回该消息最新 reactions。"""
        key = str(target)
        per_msg = self.reactions.setdefault(key, {})
        clients = per_msg.setdefault(emoji, [])
        if client in clients:
            clients.remove(client)
            if not clients:
                del per_msg[emoji]
        else:
            clients.append(client)
        if not per_msg:
            del self.reactions[key]
        self.save_meta()
        return self.reactions.get(key, {})

    def revoke(self, target, client, now=None):
        """展示层消息撤回（2 分钟内、仅自己的消息）。
        撤回不修改 chat-in.md（契约「只加不改」），只记 room.json 元数据；
        输出视图（history/poll）把文本遮蔽为占位。返回 None=成功，str=错误原因。"""
        now = now or now_ms()
        key = str(target)
        m = next((x for x in self.msgs if x["id"] == target), None)
        if not m:
            return "这条消息不存在"
        if m.get("from") != "user":
            return "只能撤回自己发的消息"
        if m.get("client") != client:
            return "只能撤回自己发的消息"
        if now - m.get("ts", 0) > REVOKE_WINDOW_MS:
            return "超过 2 分钟就撤不回啦（文件契约只加不改，太久的不动它）"
        if key in self.revoked:
            return None  # 幂等：本人重复撤回当成功（权限检查在前，越权者仍被拒）
        self.revoked[key] = True
        self.save_meta()
        return None

    def edit(self, target, text, client, now=None):
        """展示层消息编辑（2 分钟内、仅自己的消息、不能编辑已撤回的）。
        编辑不修改 chat-in.md（契约「只加不改」），新文本存 room.json 元数据；
        输出视图（history/poll）用新文本覆盖展示。
        返回 (err, changed)：err=None 且 changed=True → 成功且文本有变化（应广播）；
        err=None 且 changed=False → 幂等无变化（不广播、不喂 bot，避免重复帧与
        bot toggle 反应被二次抵消）；err=str → 拒绝原因。"""
        now = now or now_ms()
        key = str(target)
        text = (text or "").strip()
        m = next((x for x in self.msgs if x["id"] == target), None)
        if not m:
            return "这条消息不存在", False
        if m.get("from") != "user":
            return "只能编辑自己发的消息", False
        if m.get("client") != client:
            return "只能编辑自己发的消息", False
        if key in self.revoked:
            return "已撤回的消息不能再编辑", False
        if now - m.get("ts", 0) > EDIT_WINDOW_MS:
            return "超过 2 分钟就改不了啦（文件契约只加不改，太久的不动它）", False
        if not text:
            return "编辑后的内容不能是空的（想删掉用撤回）", False
        if len(text) > TEXT_MAX:
            return "改完的这条太长啦，拆成几条发吧", False
        # 幂等：文本没变化当成功（不重写元数据、不广播）
        cur_entry = self.edited.get(key, {})
        if text == cur_entry.get("text", m.get("text", "")):
            return None, False
        # 编辑历史：把「改前文本」入链（首编时链首为原文；遗留数据无 hist 时补种子）
        hist = list(cur_entry.get("hist", []))
        if not hist:
            orig = m.get("text", "")
            if orig and orig != cur_entry.get("text", ""):
                hist.append({"text": orig, "ts": m.get("ts", 0)})
        prev = cur_entry.get("text")
        if prev and prev != text:
            hist.append({"text": prev, "ts": cur_entry.get("ts", now)})
        self.edited[key] = {"text": text, "ts": now, "hist": hist[-EDIT_HIST_MAX:]}
        # 只保留最近 EDIT_MAX 条编辑记录（老的退出元数据，原文仍在 chat-in.md 可审计）
        if len(self.edited) > EDIT_MAX:
            for k in sorted(self.edited, key=lambda x: self.edited[x].get("ts", 0))[:len(self.edited) - EDIT_MAX]:
                del self.edited[k]
        self.save_meta()
        return None, True

    def mark_read(self, target, by="liaison"):
        """通讯官已读标记（✓✓ 回执）。幂等：已读过不再重复；返回 True=本次标记，False=已读。"""
        key = str(target)
        if key in self.read:
            return False
        self.read[key] = True
        self.save_meta()
        return True

    def _mask_msg(self, m):
        """已撤回/已编辑消息的输出视图（浅拷贝，遮蔽/替换文本，保留结构供前端渲染占位样式）。"""
        d = dict(m)
        key = str(d.get("id", ""))
        # 编辑覆盖（撤回优先级更高：撤回后编辑内容也不可见）
        ed = self.edited.get(key)
        if ed and key not in self.revoked:
            d["text"] = ed["text"]
            d["edited"] = True
            d["editTs"] = ed.get("ts", 0)
            # 编辑历史链（旧版→新版；含原文种子），供前端「已编辑」徽章点开时间线
            hist = list(ed.get("hist", []))
            orig = m.get("text", "")
            if orig and (not hist or hist[0].get("text") != orig):
                hist.insert(0, {"text": orig, "ts": m.get("ts", 0)})
            d["editHist"] = hist
        if key in self.read and d.get("from") == "user":
            d["read"] = True
        if key in self.revoked:
            d["text"] = "（这条消息已撤回）"
            d["revoked"] = True
            d.pop("edited", None)
            if isinstance(d.get("image"), dict):
                d["image"] = dict(d["image"], caption="（已撤回）")
        return d

    def public_msgs(self, limit=0):
        """输出消息视图：已撤回的遮蔽文本、已编辑的替换为新文本、已读的打标记
        （内存原文保留，主 agent 侧文件可审计）。"""
        out = [self._mask_msg(m) for m in self.msgs]
        return out[-limit:] if limit else out

    def reaction_stats(self):
        """汇总统计（/reactions 端点）：总数/各 emoji 计数/最热消息。"""
        by_emoji, top = {}, []
        for key, per in self.reactions.items():
            cnt = sum(len(v) for v in per.values())
            if not cnt:
                continue
            for e, v in per.items():
                by_emoji[e] = by_emoji.get(e, 0) + len(v)
            m = next((x for x in self.msgs if str(x["id"]) == key), None)
            snippet = (m.get("text", "") if m else "")[:24] or "（无文本）"
            top.append({"target": int(key), "count": cnt, "text": snippet})
        top.sort(key=lambda x: -x["count"])
        return {"total": sum(by_emoji.values()), "byEmoji": by_emoji, "top": top[:5]}

    # ---------- 解析历史（chat-in + chat-out 按时间戳归并）
    def _parse_chat_file(self, name):
        out = []
        try:
            with open(self.path(name), "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().split("\n")
        except OSError:
            return out
        prefix = "U" if name == "chat-in.md" else "L"
        cur = None
        for ln in lines:
            s = ln.strip()
            if s.startswith("##"):
                m = SEG_RE.match(s)
                if m and (m.group(1) == prefix or (prefix == "U" and m.group(1) == "S")):
                    if cur:
                        out.append(cur)
                    cur = {"kind": m.group(1), "ts": int(m.group(2)),
                           "meta": m.group(3).strip(), "body": []}
                    continue
            if cur is not None and ln.strip():
                cur["body"].append(ln.rstrip())
        if cur:
            out.append(cur)
        return out

    def rescan(self):
        ins = self._parse_chat_file("chat-in.md")
        outs = self._parse_chat_file("chat-out.md")
        merged = sorted(ins + outs, key=lambda s: (s["ts"], 0 if s["kind"] != "L" else 1))
        msgs = []
        for seg in merged:
            body = "\n".join(seg.get("body", [])).strip()
            if not body:
                continue
            who = "user" if seg["kind"] == "U" else (
                "system" if seg["kind"] == "S" else "liaison")
            m = {"id": len(msgs) + 1, "from": who, "text": body, "ts": seg["ts"]}
            if who == "user":
                # meta 形如 "ISO时间 客户端短号"，取末尾 token 为 client
                parts = seg.get("meta", "").split()
                if parts and re.match(r"^[a-zA-Z0-9\-]{1,12}$", parts[-1]):
                    m["client"] = parts[-1]
                im = IMG_BODY_RE.match(body)
                if im:
                    m["image"] = {"file": im.group(1),
                                  "name": os.path.basename(im.group(1)),
                                  "caption": im.group(2).strip()}
                    m["text"] = im.group(2).strip()
            msgs.append(m)
        self.msgs = msgs
        self.reparse_outbox()
        self.reparse_status()

    def last_id(self):
        return len(self.msgs)

    # ---------- 待答问题（outbox - outbox-replies 差集）
    def reparse_outbox(self):
        asks = []
        try:
            with open(self.path("outbox.md"), encoding="utf-8",
                      errors="replace") as f:
                txt = f.read()
        except OSError:
            txt = ""
        cur = None
        for ln in txt.split("\n"):
            m = ASK_RE.match(ln)  # 锚定行首，模板里的缩进示例不会被误认
            if m:
                if cur:
                    asks.append(cur)
                cur = {"askId": "ASK-%s" % m.group(1), "question": ""}
                continue
            if cur is not None:
                s = ln.strip()
                if s.startswith("- 需要用户回答:"):
                    cur["question"] = s.split(":", 1)[1].strip()
                elif s.startswith("- 问题:") and not cur["question"]:
                    cur["question"] = s.split(":", 1)[1].strip()
        if cur:
            asks.append(cur)
        answered = set()
        try:
            with open(self.path("outbox-replies.md"), encoding="utf-8",
                      errors="replace") as f:
                answered = set(REPLY_RE.findall(f.read()))
        except OSError:
            pass  # REPLY_RE 已锚定行首
        self.pending = [
            {"askId": a["askId"],
             "question": a["question"] or "（需要你回答一个问题）"}
            for a in asks if a["askId"].replace("ASK-", "") not in answered]

    def reparse_status(self):
        try:
            with open(self.path("status.md"), encoding="utf-8",
                      errors="replace") as f:
                txt = f.read()
        except OSError:
            txt = ""
        t = dict(self.task)
        for ln in txt.split("\n"):
            s = ln.strip()
            if s == "DONE":
                t["done"] = True
            elif s.startswith("- 当前:"):
                t["current"] = s.split(":", 1)[1].strip()
            elif s.startswith("- 阶段:"):
                t["stage"] = s.split(":", 1)[1].strip()
            elif s.startswith("- 阻塞:"):
                t["blocker"] = s.split(":", 1)[1].strip()
            elif s.startswith("- 更新于:"):
                t["updated"] = s.split(":", 1)[1].strip()
        self.task = t

    # ---------- 文件变化检测
    WATCHED = ("chat-in.md", "chat-out.md", "outbox.md", "outbox-replies.md",
               "status.md", "inbox.md", "inbox-status.md")

    def snapshot(self):
        state = {}
        for name in Room.WATCHED:
            try:
                st = os.stat(self.path(name))
                state[name] = (st.st_size, int(st.st_mtime * 1000))
            except OSError:
                state[name] = None
        return state

    def diff(self):
        """返回发生变化的文件名列表（并更新快照）。"""
        now = self.snapshot()
        changed = [n for n, st in now.items() if st != self._filestate.get(n)]
        self._filestate = now
        return changed

    # ---------- 追加 chat-in（唯一写入者：watcher）
    def append_chat_in(self, text, client, image=None):
        ts = now_ms()
        body = ("[图片 images/%s/%s] %s" % (self.id, image["file_name"],
                                            image.get("caption", ""))) if image else text
        seg = "## [U-%d] %s %s\n%s\n" % (ts, iso_now(), client or "-", body)
        self._append("chat-in.md", seg)
        self.rescan()
        return self.msgs[-1] if self.msgs else None

    def append_system(self, text):
        ts = now_ms()
        self._append("chat-in.md", "## [S-%d] %s watcher\n%s\n" % (ts, iso_now(), text))
        self.rescan()
        return self.msgs[-1] if self.msgs else None

    def _append(self, name, content):
        with open(self.path(name), "a", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())


# ================================================================ 演示通讯官 bot
class DemoBot(threading.Thread):
    """--demo 时运行。模拟「通讯官 + 主 agent」的完整闭环，仅演示用。
    只写通讯官/主 agent 名下文件（chat-out/inbox/outbox/status/…），
    与 watcher 只写 chat-in 的职责边界一致；主循环经文件轮询发现它的写入并广播。"""

    # 房间剧情包：不同演示房间用不同语境（默认回退 1001 的手机网页剧情）
    THEMES = {
        "1001": {
            "greet": ("你好呀！我是通讯官小连 🫡\n"
                      "你在这里说的每句话，我都会翻译成干活的人能懂的指令再转过去；"
                      "有进展我会随时用大白话向你汇报。\n"
                      "· 有急事 → 按红色「停！」按钮\n"
                      "· 发现不对劲 → 按「报个问题」\n"
                      "· 随便聊聊也行，我都在～"),
            "progress": [
                ("阶段小汇报：界面骨架搭好了，正在调手机上的排版。一切顺利，别担心～", "正在调整移动端排版"),
                ("进展：按钮和输入框都装好了，正在做颜色微调。", "正在调整配色"),
                ("小汇报：跑了一遍完整流程，没发现卡壳的地方。", "正在回归自测"),
                ("汇报：文档和收尾整理中，快到你验收的环节啦。", "文档整理中"),
            ],
            "stage_init": ("正在搭建项目骨架", "开工准备"),
            "ask": "界面主按钮你喜欢【绿色】还是【橙色】？",
            "ask_tech": "需要用户决策：主按钮配色方案（绿/橙二选一）",
            "stop_where": "调整手机排版",
            "image_reply": ("截图收到，我先看一眼…\n看起来是界面在手机上显示得有点挤，"
                            "我把图转给开发主力去调排版，有结果马上告诉你。"),
            "done": ("🎉 任务完成！\n最终总结：\n"
                     "1. 聊天室双向通信全链路跑通（你 ↔ 我 ↔ 开发主力）\n"
                     "2. 待答问题全部闭环，无遗留\n"
                     "3. 遗留事项：无\n\n感谢配合，随时可以再来找我。"),
        },
        "1002": {
            "greet": ("你好呀！我是通讯官小连 🎮\n"
                      "这局咱们要做一个跑酷小游戏，你说想要什么样的，"
                      "我翻译好就转给主力开发；每过一关我都会来跟你汇报～\n"
                      "· 有急事 → 按红色「停！」按钮\n"
                      "· 玩法拿不准 → 我会来问你\n"
                      "· 随时插话改需求也行，我都在！"),
            "progress": [
                ("阶段小汇报：主角能跑能跳了，手感还挺跟手，正在画障碍物素材～", "正在绘制关卡素材"),
                ("进展：金币、记分板都接上了，正在调难度曲线。", "正在调整难度"),
                ("小汇报：完整试玩了一遍，没发现卡死或穿墙的地方。", "正在试玩自测"),
                ("汇报：音效和打包收尾中，马上就能交给你试玩啦。", "音效与打包中"),
            ],
            "stage_init": ("正在搭建游戏原型关卡", "开工准备"),
            "ask": "主角的皮肤你喜欢【像素猫】还是【小机器人】？",
            "ask_tech": "需要用户决策：主角皮肤方案（像素猫/小机器人二选一）",
            "stop_where": "调整跳跃手感",
            "image_reply": ("截图收到，我先瞅瞅…\n看起来是金币的碰撞判定有点飘，"
                            "我把图转给主力开发去修碰撞盒，改好马上喊你验收。"),
            "done": ("🎉 游戏做完啦！\n最终总结：\n"
                     "1. 跑酷全流程跑通：起跑 → 跳跃 → 吃金币 → 结算（你 ↔ 我 ↔ 主力开发）\n"
                     "2. 待答问题全部闭环，无遗留\n"
                     "3. 遗留事项：无\n\n快去试试手感，想加新关卡随时来找我！"),
        },
    }

    # 彩蛋：石头剪刀布（1002 剧情专属；房间内存状态，重启后自然失效）
    RPS_HANDS = {
        "石头": ("✊", "石头"), "剪刀": ("✌️", "剪刀"), "布": ("✋", "布"),
        "rock": ("✊", "石头"), "scissors": ("✌️", "剪刀"), "paper": ("✋", "布"),
    }
    RPS_ASCII = {
        "石头": "\n  ██████\n ████████\n ████████\n  ██████\n   ✊ 石头",
        "剪刀": "\n   ✂ ───\n  ╱  ╲\n ▏    ▕\n  ╲  ╱\n   ✌️ 剪刀",
        "布": "\n ▔▔▔▔▔▔\n ▏      ▕\n ▏      ▕\n ▁▁▁▁▁▁\n   ✋ 布",
    }
    RPS_RESULT = {("石头", "剪刀"): "win", ("剪刀", "布"): "win", ("布", "石头"): "win"}

    # 彩蛋：等主力干活时给用户冲杯咖啡（1001 剧情专属，带 ASCII 拉花动画）
    COFFEE_STEPS = [
        ("先磨豆子～", "\n   ( ( ( (\n    ) ) ) )\n   ........\n  |  磨豆  |\n  ▔▔▔▔▔▔▔▔"),
        ("冲水萃取中…", "\n    ┃┃┃┃┃\n    ↓↓↓↓\n  ┌────────┐\n  │ ▒▒▒▒▒▒ │\n  │ 萃取中 │\n  └────────┘"),
        ("拉花ing…", "\n      ☕\n   ~ ~ ~\n  ( ( (\n   ) ) )\n  ▔▔▔▔▔▔▔▔\n   ♡ 拉花中"),
    ]
    COFFEE_DONE = "\n    ╭─────╮\n   │ ☕  │\n   │ ~♡~ │\n  ╰─────╯\n  一杯拉花拿铁 ♥\n  趁热喝，慢慢等主力干活～"

    def theme(self, room, key):
        pack = self.THEMES.get(room.id, self.THEMES["1001"])
        return pack[key]

    def __init__(self, watcher):
        super().__init__(daemon=True)
        self.w = watcher
        self.q = queue.Queue()
        self.last_progress = {}
        self.rps = {}  # rid → {"await": True, "score": [胜, 平, 负]}

    def feed(self, kind, room, payload=None):
        self.q.put((kind, room, payload))

    def react(self, room, target, emoji):
        """bot 给用户消息点反应（模拟「主 agent 已读 + 态度」）。
        经 bot_out 队列交主进程执行（同 typing，线程安全）。"""
        self.w.bot_out.put(("react", room.id, (target, emoji)))

    def _on_edit(self, room, ev):
        """用户编辑了消息：点个👀（看到了）+ 简短确认按新版本来（不重跑剧情，避免刷屏）。"""
        mid, text = ev.get("id"), ev.get("text", "")
        if not mid:
            return
        self.react(room, mid, "👀")
        brief = text.replace("\n", " ").strip()[:40]
        self.say(room, "看到你改了那条 👀 就按「%s」这个版本来～" % (brief or "新内容"),
                 delay=1.6)

    def run(self):
        while True:
            try:
                kind, room, payload = self.q.get(timeout=2.0)
            except queue.Empty:
                self._tick_progress()
                continue
            try:
                if kind == "chat":
                    self._on_chat(room, payload)
                elif kind == "init":
                    self._on_init(room)
                elif kind == "edit":
                    self._on_edit(room, payload)
            except Exception as e:  # 演示 bot 绝不能弄挂主进程
                log("[demo-bot] error: %r" % e)
            if self.q.empty():
                self._tick_progress()

    # ---------- 基础动作
    def say(self, room, text, delay=None):
        """打字动画 → 写 chat-out.md（主循环轮询发现后广播）。"""
        self.w.bot_out.put(("typing", room.id, True))
        time.sleep(delay if delay is not None else min(4.0, 1.0 + len(text) / 90.0))
        with open(room.path("chat-out.md"), "a", encoding="utf-8") as f:
            f.write("## [L-%d] %s\n%s\n" % (now_ms(), iso_now(), text))
            f.flush()
        self.w.bot_out.put(("typing", room.id, False))

    def next_msg_id(self, room):
        try:
            with open(room.path("inbox.md"), encoding="utf-8",
                      errors="replace") as f:
                ids = [int(x) for x in re.findall(
                    r"^##\s*\[MSG-(\d+)\]", f.read(), re.M)]
            return max(ids) if ids else 0
        except OSError:
            return 0

    def card(self, room, ctype, prio, raw, tidy, timing="下一缝隙"):
        n = self.next_msg_id(room) + 1
        seg = ('## [MSG-%03d]\n- 类型: %s\n- 优先级: %s\n- 用户原话: "%s"\n'
               "- 整理后: %s\n- 投递时机: %s\n") % (n, ctype, prio, raw[:200], tidy, timing)
        with open(room.path("inbox.md"), "a", encoding="utf-8") as f:
            f.write(seg)
            f.flush()

    def inbox_done(self, room, result):
        n = self.next_msg_id(room)
        with open(room.path("inbox-status.md"), "a", encoding="utf-8") as f:
            f.write("- MSG-%03d | 已处理 | %s\n" % (n, result))
            f.flush()

    def ask(self, room, question_for_user, question_tech):
        try:
            with open(room.path("outbox.md"), encoding="utf-8",
                      errors="replace") as f:
                ids = [int(x) for x in re.findall(
                    r"^##\s*\[ASK-(\d+)\]", f.read(), re.M)]
            n = (max(ids) if ids else 0) + 1
        except OSError:
            n = 1
        with open(room.path("outbox.md"), "a", encoding="utf-8") as f:
            f.write("## [ASK-%03d]\n- 问题: %s\n- 需要用户回答: %s\n"
                    % (n, question_tech, question_for_user))
            f.flush()
        return "ASK-%03d" % n

    def answer(self, room, ask_id, content):
        with open(room.path("outbox-replies.md"), "a", encoding="utf-8") as f:
            f.write("- %s | 已答复 | %s\n" % (ask_id, content))
            f.flush()

    def set_status(self, room, current=None, stage=None, blocker=None, done=False):
        t = room.task
        txt = ("# 状态\n- 当前: %s\n- 阶段: %s\n- 阻塞: %s\n- 更新于: %s\n%s"
               % (current or t["current"], stage or t["stage"],
                  blocker if blocker is not None else t["blocker"],
                  iso_now(), "\nDONE\n" if done else ""))
        with open(room.path("status.md"), "w", encoding="utf-8") as f:
            f.write(txt)
            f.flush()

    # ---------- 事件处理
    def _on_init(self, room):
        if not room._parse_chat_file("chat-out.md"):
            self.say(room, self.theme(room, "greet"), delay=1.2)
        if room.task["current"] == "初始化中":
            cur, stage = self.theme(room, "stage_init")
            self.set_status(room, current=cur, stage=stage)

    def _on_chat(self, room, ev):
        text = ev["text"]
        low = text.strip().lower()
        mid = ev.get("id")
        answered = re.match(r"^#回答\s+(ASK-\d+)\s*(.*)$", text.strip(), re.I)

        # 已读回执：先看一眼（模拟真人阅读节奏），再走剧情分支
        if mid:
            time.sleep(0.9)
            self.w.bot_out.put(("read", room.id, mid))

        if "结束演示" in low or low.strip() == "结束":
            if mid:
                self.react(room, mid, "🎉")
            self._on_done(room)
            return
        # ---- 「继续」：兑现「已停 ✅ 想继续了就说继续」的承诺（精确词匹配，避免误拦）
        if low.strip() in ("继续", "继续吧", "继续做", "继续干活", "接着做", "接着干",
                           "恢复", "恢复执行", "继续执行"):
            if mid:
                self.react(room, mid, "👍")
            self.card(room, "指令", "高", text, "用户要求恢复执行：从上次暂停点继续", "下一缝隙")
            self.say(room, "好嘞，这就喊开发主力从「%s」接着干！\n有进展我马上来汇报。"
                     % self.theme(room, "stop_where"))
            time.sleep(2.0)
            self.set_status(room, current="正在继续上次的工作", stage="恢复执行",
                            blocker="无")
            return
        # ---- 彩蛋：石头剪刀布（1002 剧情专属）
        st = self.rps.get(room.id)
        if st and st.get("await") and ("不玩" in low or "结束游戏" in low):
            self._rps_stop(room)
            return
        hand = self.RPS_HANDS.get(low.strip()) or \
            (self.RPS_HANDS.get(low.strip().rstrip("吧呢啊！!。")) if st and st.get("await") else None)
        if st and st.get("await") and hand:
            self._rps_round(room, hand[1])
            return
        if room.id in self.THEMES and room.id != "1001" and \
                any(k in low for k in ("玩个游戏", "石头剪刀布", "来玩", "猜拳", "小游戏玩")):
            if mid:
                self.react(room, mid, "🎉")  # 开局：兴奋
            self._rps_start(room)
            return
        # ---- 彩蛋：喝杯咖啡（1001 剧情专属，演示等待期小惊喜）
        if room.id == "1001" and \
                any(k in low for k in ("喝杯咖啡", "来杯咖啡", "咖啡", "冲杯咖啡")):
            if mid:
                self.react(room, mid, "❤️")
            self._coffee_start(room)
            return
        # ---- 彩蛋：续杯（兑现「想续杯再说一声再来一杯」的承诺）
        if room.id == "1001" and low.strip() in ("再来一杯", "续杯", "再来一杯咖啡", "续一杯"):
            if mid:
                self.react(room, mid, "❤️")
            self._coffee_refill(room)
            return
        if answered:
            ask_id, content = answered.group(1), answered.group(2).strip()
            if mid:
                self.react(room, mid, "👍")  # 答复被及时收到：点个赞
            self.say(room, "收到你的答复啦：「%s」\n这就转给开发主力，横幅马上撤下。"
                     % content[:60])
            self.answer(room, ask_id, content or "（用户确认）")
            return
        if ev.get("image"):
            self.card(room, "反馈", "中", "(图片) " + (ev.get("caption") or "截图"),
                      "用户上传截图，需查看并确认界面问题", "下一缝隙")
            if mid:
                self.react(room, mid, "😮")  # 收到图：表示看到了
            self.say(room, self.theme(room, "image_reply"))
            return
        if any(k in low for k in ("停", "暂停", "别做了", "停下")):
            if mid:
                self.react(room, mid, "🙏")  # 收到急件：诚恳表态
            self.card(room, "STOP", "高", text, "用户要求暂停：完成当前原子操作后立即停下",
                      "下一缝隙")
            self.say(room, "好嘞，马上喊停！\n开发主力做完手头这一步就会停下来，"
                           "稍等片刻我回报具体停在哪一步、为什么。")
            time.sleep(2.5)
            self.set_status(room, current="已按用户要求暂停", stage="暂停中",
                            blocker="等待用户指示")
            self.say(room, "已停 ✅ 停在「%s」这一步。想继续了就说「继续」，"
                           "想改方向也直接说。" % self.theme(room, "stop_where"))
            return
        if "必须" in low or "一定要" in low:
            if mid:
                self.react(room, mid, "👍")
            self.card(room, "要求", "高", text, "用户明确：该条为必须项，优先处理", "下一缝隙")
            self.say(room, "明白！这条按「必须」处理，优先级提到最高，"
                           "下一个缝隙（也就是做完手头这一步）就转给开发主力。")
            return
        if "看到的现象" in low:
            if mid:
                self.react(room, mid, "😮")  # bug 报告：引起重视
            self.card(room, "疑似bug", "高", text,
                      "疑似bug：已带三要素（现象/步骤/期待），待复现验证", "下一缝隙")
            self.say(room, "bug 报告收到！三样信息很齐全 👍\n我先转给开发主力复现；"
                           "如果复现不出来，我会回来找你要更多细节。")
            time.sleep(3.0)
            self.inbox_done(room, "已复现并修复，等待下一轮验证")
            self.say(room, "复现成功，改好了！你方便的话再按一次刚才的操作试试看。")
            return
        if low.startswith("建议") or any(k in low for k in ("要不", "最好", "可以考虑")):
            if mid:
                self.react(room, mid, "❤️")  # 好主意：喜欢
            self.card(room, "建议", "低", text, "用户建议：攒批到阶段边界吸收", "攒批")
            self.say(room, "好主意！我记成「建议」了，攒到阶段节点一起给开发主力看。")
            return
        # 普通消息 → 确认理解 + 每隔一条抛一个待答问题（演示横幅）
        self.card(room, "要求", "中", text, "（待确认）用户原始输入", "下一缝隙")
        snippet = text.strip().replace("\n", " ")[:40]
        # 自动反应：普通消息 50% 概率点个 👍（模拟已读，不每条都点避免审美疲劳）
        if mid and random.random() < 0.5:
            self.react(room, mid, "👍")
        self.say(room, "收到！我理解你的意思是「%s…」，对吗？\n"
                       "确认没歧义我就转给开发主力；理解偏了你直接纠正我。" % snippet)
        # 触发条件按「用户消息条数」而非消息 id：id 在「每条用户消息后必跟一条 S 段
        # 自动应答」的结构下恒为 3k+2，%3==0 永远不成立（QA 实测干净房间正常聊天
        # 10+ 条也等不到待答横幅）。第 2/4/6…条用户消息触发，保证演示节奏稳定可复现。
        n_user = sum(1 for m in room.msgs if m.get("from") == "user")
        if not room.pending and n_user and n_user % 2 == 0:
            time.sleep(1.2)
            q = self.theme(room, "ask")
            aid = self.ask(room, q, self.theme(room, "ask_tech"))
            self.say(room, "对了，开发主力托我问个事 👇\n【%s】\n"
                           "直接回复「#回答 %s 你的选择」就行。" % (q, aid))
        return

    # ---------- 彩蛋：等主力干活时冲杯咖啡
    def _coffee_start(self, room):
        self.say(room, "好呀！主力还在调排版，我给你冲杯咖啡 ☕\n"
                       "手冲需要点耐心，稍等一下下～", delay=0.8)
        for text, art in self.COFFEE_STEPS:
            time.sleep(1.1)
            self.say(room, text + art, delay=0.6)
        self.say(room, "好啦，你的咖啡 ☕\n%s\n（想续杯再说一声「再来一杯」）"
                       % self.COFFEE_DONE.strip(), delay=0.9)

    def _coffee_refill(self, room):
        """续杯：快速版（只走拉花+完成两步，杯子换个花样）。"""
        self.say(room, "续杯马上安排～☕", delay=0.7)
        time.sleep(0.9)
        self.say(room, "拉花ing…（这次试试双心拉花）"
                       "\n      ☕\n   ~ ~ ~\n  ( ( (\n   ) ) )\n  ▔▔▔▔▔▔▔▔\n   ♡♡ 拉花中", delay=0.9)
        self.say(room, "好啦，续杯完成 ☕\n"
                       "\n    ╭─────╮\n   │ ☕  │\n   │~♡♡~│\n  ╰─────╯\n"
                       "  双心拿铁 ♥♥\n  小心烫嘴，慢慢喝～", delay=0.9)

    def _rps_start(self, room):
        self.rps[room.id] = {"await": True, "score": [0, 0, 0]}
        self.say(room, "好耶！趁主力开发在调跳跃手感，咱们玩一把石头剪刀布 🎮\n"
                       "规则超简单：直接发【石头】/【剪刀】/【布】其中一个词，"
                       "我随机出拳、当场判胜负；连比分我帮你记着。\n"
                       "想结束就说「不玩了」。",
                 delay=1.0)

    def _rps_round(self, room, user_hand):
        st = self.rps.get(room.id) or {"await": True, "score": [0, 0, 0]}
        my_hand = random.choice(["石头", "剪刀", "布"])
        if user_hand == my_hand:
            verdict, idx, face = "平局！咱俩心有灵犀 🤝", 1, "😐"
        elif self.RPS_RESULT.get((user_hand, my_hand)) == "win":
            verdict, idx, face = "你赢啦！这把你厉害 🎉", 0, "😭"
        else:
            verdict, idx, face = "我赢咯～嘿嘿 😝", 2, "😎"
        try:
            st["score"][idx] += 1
        except (IndexError, TypeError):
            st["score"] = [0, 0, 0]
            st["score"][idx] += 1
        st["await"] = True
        self.rps[room.id] = st
        w, l, d = st["score"][0], st["score"][2], st["score"][1]
        self.say(room, "你出 %s%s\n我出 %s%s\n%s\n%s\n"
                       "—— 比分：你 %d 胜 / 我 %d 胜 / 平 %d ——\n"
                       "（继续出拳，或者说「不玩了」结束）"
                       % (user_hand, self.RPS_ASCII[user_hand],
                          my_hand, self.RPS_ASCII[my_hand], face, verdict, w, l, d))

    def _rps_stop(self, room):
        st = self.rps.pop(room.id, None)
        if st:
            w, l, d = st["score"][0], st["score"][2], st["score"][1]
            self.say(room, "好嘞，不玩啦～最终比分：你 %d 胜 / 我 %d 胜 / 平 %d。\n"
                           "玩得开心就好，正经事随时继续！🫡" % (w, l, d))

    def _on_done(self, room):
        self.set_status(room, current="演示流程结束", stage="收尾", done=True)
        self.say(room, self.theme(room, "done"))
        time.sleep(1.0)
        self.card(room, "反馈", "低", "结束演示", "用户主动结束演示流程", "攒批")

    def _tick_progress(self):
        for rid, room in list(self.w.rooms.items()):
            if room.task.get("done") or not room.conns:
                continue
            if getattr(room, "demo_muted", False):
                continue
            last = self.last_progress.get(rid, 0.0)
            if time.time() - last < 45:
                continue
            self.last_progress[rid] = time.time()
            prog = self.theme(room, "progress")
            text, cur = prog[int(time.time() / 45) % len(prog)]
            self.set_status(room, current=cur)
            self.say(room, text)


# ================================================================ 主服务
class Watcher:
    def __init__(self, args):
        self.args = args
        self.mailbox = os.path.abspath(args.mailbox)
        self.html_path = os.path.abspath(args.html) if args.html else \
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "chatroom.html")
        self.token = args.token
        self.demo = args.demo
        self.sel = selectors.DefaultSelector()
        self.rooms = {}
        self.conns = set()
        self.pollers = {}          # 长轮询等待者：Conn → {room, since, deadline}
        self.running = True
        self.started = time.time()
        self.bot = None
        self.bot_out = queue.Queue()
        os.makedirs(self.mailbox, exist_ok=True)
        self._load_rooms()

    # ------------------------------------------------ 房间管理
    def _load_rooms(self):
        rooms_dir = os.path.join(self.mailbox, "rooms")
        os.makedirs(rooms_dir, exist_ok=True)
        for name in sorted(os.listdir(rooms_dir)):
            p = os.path.join(rooms_dir, name)
            if os.path.isdir(p) and ROOM_ID_RE.match(name):
                r = Room(name, p)
                r._filestate = r.snapshot()
                r.done_announced = bool(r.task.get("done"))
                self.rooms[name] = r
                log("载入房间 %s（%s，%d 条消息，待答 %d）"
                    % (name, r.title, r.last_id(), len(r.pending)))

    def create_room(self, rid, title=None):
        if not ROOM_ID_RE.match(rid):
            raise ValueError("房间号只能是 3~16 位数字或小写字母")
        if rid in self.rooms:
            raise ValueError("房间 %s 已存在" % rid)
        base = os.path.join(self.mailbox, "rooms", rid)
        os.makedirs(base, exist_ok=True)
        for name, tpl in TEMPLATES.items():
            fp = os.path.join(base, name)
            if not os.path.exists(fp):
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(tpl)
        os.makedirs(os.path.join(base, "images"), exist_ok=True)
        with open(os.path.join(base, "room.json"), "w", encoding="utf-8") as f:
            json.dump({"id": rid, "title": title or rid, "created": iso_now()},
                      f, ensure_ascii=False, indent=2)
        r = Room(rid, base)
        r._filestate = r.snapshot()
        self.rooms[rid] = r
        log("创建房间 %s（%s）" % (rid, r.title))
        return r

    # ------------------------------------------------ 生命周期
    def write_pid(self):
        pid = {"pid": os.getpid(), "port": self.args.port, "host": self.args.host,
               "started": iso_now(), "version": VERSION}
        with open(os.path.join(self.mailbox, "watcher.pid"), "w", encoding="utf-8") as f:
            json.dump(pid, f, ensure_ascii=False, indent=2)

    def remove_pid(self):
        try:
            os.remove(os.path.join(self.mailbox, "watcher.pid"))
        except OSError:
            pass

    def start_demo(self):
        if not self.demo:
            return
        for rid, title in (("1001", "演示·修一个手机网页"), ("1002", "演示·做一个小游戏")):
            if rid not in self.rooms:
                try:
                    self.create_room(rid, title)
                except ValueError as e:
                    log("创建演示房间失败 %s: %s" % (rid, e))
        self.bot = DemoBot(self)
        self.bot.start()
        for room in list(self.rooms.values()):
            self.bot.feed("init", room)

    def listen(self):
        self.lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.lsock.bind((self.args.host, self.args.port))
        self.lsock.setblocking(False)
        self.lsock.listen(64)
        self.sel.register(self.lsock, selectors.EVENT_READ, ("listen", None))
        log("监听 http://%s:%d" % (self.args.host, self.args.port))

    def lan_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return "127.0.0.1"

    def banner(self):
        ip = self.lan_ip()
        out = [
            "=" * 60,
            " chat-watcher 已启动 · %s" % VERSION,
            "=" * 60,
            " 监听     : http://%s:%d" % (self.args.host, self.args.port),
            " 网页     : http://%s:%d/   ← 浏览器打开即是聊天室" % (ip, self.args.port),
            " 双击版   : chatroom.html 发给用户，地址填 http://%s:%d" % (ip, self.args.port),
        ]
        if self.rooms:
            rooms = "，".join("%s（%s）" % (k, v.title)
                             for k, v in list(self.rooms.items())[:8])
            out.append(" 已有房间 : %s" % rooms)
        else:
            out.append(" 已有房间 : （无）用 --create-room <房间号> 新建")
        out.append(" pid      : %d → %s" % (
            os.getpid(), os.path.join(self.mailbox, "watcher.pid")))
        if self.demo:
            out.append(" 演示模式 : 开（内置演示通讯官，模拟完整闭环）")
        out.append("=" * 60)
        print("\n".join(out), flush=True)

    # ------------------------------------------------ 主循环
    def run(self):
        last_poll = 0.0
        while self.running:
            events = self.sel.select(timeout=TICK)
            for key, mask in events:
                tag, conn = key.data
                if tag == "listen":
                    self._accept()
                else:
                    if mask & selectors.EVENT_WRITE:
                        self._on_writable(conn)
                    if (mask & selectors.EVENT_READ) and not conn.closed:
                        self._on_readable(conn)
                    self._sync_write_interest(conn)
            self._drain_bot_out()
            now = time.time()
            if now - last_poll >= POLL_INTERVAL:
                last_poll = now
                self._poll_files()
                self._check_timeouts()
            self._check_pollers()
        self._shutdown()

    def _sync_write_interest(self, c: Conn):
        """有积压数据则关注可写，无则只关注可读。"""
        if c.closed:
            return
        want = bool(c.outbuf)
        if want != c.want_write:
            c.want_write = want
            try:
                self.sel.modify(c.sock, selectors.EVENT_READ |
                                (selectors.EVENT_WRITE if want else 0), ("conn", c))
            except (KeyError, ValueError, OSError):
                pass

    def _on_writable(self, c: Conn):
        c.flush()

    def _accept(self):
        while True:
            try:
                sock, addr = self.lsock.accept()
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            sock.setblocking(False)
            c = Conn(sock, addr)
            self.conns.add(c)
            self.sel.register(sock, selectors.EVENT_READ, ("conn", c))
            c.want_write = False

    def _on_readable(self, c: Conn):
        if c.closed:
            return
        try:
            data = c.sock.recv(65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            self._drop(c)
            return
        if not data:
            self._drop(c)
            return
        c.last_seen = time.time()
        c.inbuf += data
        if c.kind == "ws":
            self._ws_read(c)
        elif c.kind == "http":
            self._http_read(c)

    def _drop(self, c: Conn):
        if c.closed:
            return
        c.closed = True
        try:
            self.sel.unregister(c.sock)
        except (KeyError, ValueError):
            pass
        try:
            c.sock.close()
        except OSError:
            pass
        self.conns.discard(c)
        if c in self.pollers:
            del self.pollers[c]
        if c.room:
            room, c.room = c.room, None
            room.conns.discard(c)
            self._broadcast(room, {"type": "status", "clients": len(room.conns)})
            log("房间 %s 断开 %s（剩 %d 个连接）"
                % (room.id, c.client or c.cid, len(room.conns)))

    # ------------------------------------------------ WebSocket
    def _try_ws_handshake(self, c: Conn, req) -> bool:
        headers = req.get("headers", {})
        if "websocket" not in headers.get("upgrade", "").lower():
            return False
        key = headers.get("sec-websocket-key")
        if not key:
            self._http_respond(c, 400, b'{"error":"missing key"}', "application/json")
            self._drop(c)
            return True
        # 先完成握手，再校验房间（这样客户端能收到人话错误帧）
        resp = ("HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                "Sec-WebSocket-Accept: %s\r\n\r\n" % ws_accept_key(key))
        c.kind = "ws"
        try:
            c.sock.sendall(resp.encode())
        except OSError:
            self._drop(c)
            return True
        qs = urllib.parse.parse_qs(req.get("query", ""))
        rid = (qs.get("room") or [""])[0].strip()
        client = (qs.get("client") or [""])[0].strip()[:12]
        token = (qs.get("token") or [""])[0]
        err = self._auth_room(rid, token)
        if err:
            c.send_json({"type": "error", "code": err[0], "message": err[1]})
            c.send_bytes(ws_encode(8, struct.pack(">H", 4001)))
            c.flush()
            self._drop(c)
            return True
        c.room = self.rooms[rid]
        c.client = client or c.cid
        c.room.conns.add(c)
        self._ws_on_open(c)
        return True

    def _auth_room(self, rid, token):
        if self.token and token != self.token:
            return ("forbidden", "口令不对，进不去")
        if not rid:
            return ("no-room", "缺少房间号")
        if not ROOM_ID_RE.match(rid):
            return ("bad-room", "房间号格式不对（3~16 位数字或小写字母）")
        if rid not in self.rooms:
            return ("no-room", "这个房间号不存在，问问 AI 是不是抄错了")
        return None

    def _ws_on_open(self, c: Conn):
        room = c.room
        qs = urllib.parse.parse_qs(c.req.get("query", "")) if c.req else {}
        try:
            since = int((qs.get("since") or ["-1"])[0])
        except ValueError:
            since = -1
        c.send_json({"type": "hello", "ok": True, "room": room.id, "title": room.title,
                     "server": VERSION, "clients": len(room.conns), "demo": self.demo,
                     "muted": getattr(room, "demo_muted", False)})
        msgs = room.public_msgs(200) if since < 0 else \
            ([m for m in room.public_msgs() if m["id"] > since] or room.public_msgs(60))
        c.send_json({"type": "history", "messages": msgs, "lastId": room.last_id(),
                     "total": len(room.msgs),
                     "pending": room.pending, "task": room.task,
                     "done": room.task.get("done", False),
                     "reactions": room.reactions, "revoked": list(room.revoked.keys()),
                     "edited": list(room.edited.keys()),
                     "read": list(room.read.keys())})
        self._broadcast(room, {"type": "status", "clients": len(room.conns)})
        log("房间 %s 新连接 %s（共 %d 个）" % (room.id, c.client, len(room.conns)))

    def _ws_read(self, c: Conn):
        while not c.closed:
            try:
                fin, op, payload, consumed = ws_parse(c.inbuf)
            except ValueError:
                self._ws_send_error(c, "too-big", "这条太大了，发不下")
                self._drop(c)
                return
            if fin is None:
                return
            c.inbuf = c.inbuf[consumed:]
            if op == 0:  # 分片续帧
                if c.frag_op is None:
                    self._drop(c)
                    return
                c.frag_buf += payload
                if len(c.frag_buf) > 9 * 1024 * 1024:
                    self._drop(c)
                    return
                if fin:
                    op, payload = c.frag_op, c.frag_buf
                    c.frag_op, c.frag_buf = None, b""
                    self._ws_frame(c, op, payload)
                continue
            if not fin:  # 分片起始帧
                c.frag_op, c.frag_buf = op, payload
                continue
            self._ws_frame(c, op, payload)

    def _ws_frame(self, c: Conn, op, payload: bytes):
        if op == 8:  # close
            try:
                c.send_bytes(ws_encode(8, payload[:2] or b"\x03\xe8"))
            except Exception:
                pass
            self._drop(c)
        elif op == 9:  # ping → pong
            c.send_bytes(ws_encode(10, payload))
        elif op == 10:  # pong
            pass
        elif op == 2:  # 二进制不支持
            self._ws_send_error(c, "binary", "这里只能发文字")
        elif op == 1:  # 文本
            try:
                obj = json.loads(payload.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._ws_send_error(c, "bad-json", "消息格式不对")
                return
            self._ws_handle(c, obj)

    def _ws_send_error(self, c, code, message):
        c.send_json({"type": "error", "code": code, "message": message})

    def _ws_handle(self, c: Conn, obj):
        t = obj.get("type")
        if t == "ping":
            c.send_json({"type": "pong", "t": obj.get("t")})
        elif t == "react":
            target = obj.get("target")
            emoji = str(obj.get("emoji") or "")
            if isinstance(target, int) and 0 < target <= c.room.last_id() \
                    and emoji in REACT_EMOJIS and c.client:
                rx = c.room.add_reaction(target, emoji, c.client)
                self._broadcast(c.room, {"type": "reaction", "target": target,
                                         "reactions": rx, "client": c.client})
            else:
                self._ws_send_error(c, "bad-react", "这个反应点不了（消息不存在或表情不支持）")
        elif t == "revoke":
            target = obj.get("target")
            if not isinstance(target, int) or not c.client:
                self._ws_send_error(c, "bad-revoke", "这条撤不了（参数不对）")
                return
            err = c.room.revoke(target, c.client)
            if err:
                self._ws_send_error(c, "bad-revoke", err)
                return
            self._broadcast(c.room, {"type": "revoke", "target": target,
                                      "ts": now_ms(), "client": c.client})
        elif t == "edit":
            target = obj.get("target")
            text = (obj.get("text") or "").strip()
            if not isinstance(target, int) or not c.client or not text:
                self._ws_send_error(c, "bad-edit", "这条改不了（参数不对）")
                return
            err, changed = c.room.edit(target, text, c.client)
            if err:
                self._ws_send_error(c, "bad-edit", err)
                return
            if not changed:
                return  # 幂等无变化：不广播、不喂 bot（重复帧会二次抵消 bot 的 toggle 反应）
            # 广播 edit 帧：所有客户端替换气泡文本 + 打「已编辑」标记（携带版本链）
            self._broadcast(c.room, {"type": "edit", "target": target,
                                      "text": text, "ts": now_ms(), "client": c.client,
                                      "hist": c.room.edited.get(str(target), {}).get("hist", [])})
            if self.demo and self.bot:
                self.bot.feed("edit", c.room, {"id": target, "text": text,
                                                "client": c.client})
        elif t == "typing":
            if c.room:
                self._broadcast(c.room, {"type": "typing", "who": "user",
                                         "on": bool(obj.get("on")), "client": c.client},
                                exclude=c)
        elif t == "chat":
            text = (obj.get("text") or "").strip()
            if not text:
                return
            if len(text) > TEXT_MAX:
                self._ws_send_error(c, "too-long", "这条消息太长啦，拆成几条发吧")
                return
            if not self._rate_ok(c):
                self._ws_send_error(c, "rate", "发得太快啦，歇一秒再发")
                return
            m = c.room.append_chat_in(text, c.client)
            self._broadcast(c.room, m)
            if obj.get("tempId"):
                c.send_json({"type": "ack", "tempId": obj["tempId"], "id": m["id"]})
            sysmsg = c.room.append_system("✓ 已收到，马上转给开发主力")
            self._broadcast(c.room, sysmsg)
            self._wake(c.room, "chat-in")
            if self.bot:
                self.bot.feed("chat", c.room, {"text": text, "client": c.client,
                                               "id": m["id"]})
        elif t == "image":
            if not self._rate_ok(c):
                self._ws_send_error(c, "rate", "发得太快啦，歇一秒再发")
                return
            m = self._ingest_user_image(c.room, obj, c.client)
            if isinstance(m, dict):
                self._broadcast(c.room, m)
                if obj.get("tempId"):
                    c.send_json({"type": "ack", "tempId": obj["tempId"], "id": m["id"]})
                self._wake(c.room, "chat-in")
                if self.bot:
                    self.bot.feed("chat", c.room, {
                        "text": m.get("text", ""), "client": c.client, "id": m["id"],
                        "image": True, "caption": m.get("text", "")})
            else:
                self._ws_send_error(c, "image", m)
        else:
            self._ws_send_error(c, "bad-type", "看不懂这条消息")

    def _rate_ok(self, c: Conn):
        now = time.time()
        c.rates = [t for t in c.rates if now - t < RATE_WINDOW]
        if len(c.rates) >= RATE_MAX:
            return False
        c.rates.append(now)
        return True

    # ------------------------------------------------ 图片入库
    def _ingest_user_image(self, room, obj, client):
        data_b64 = obj.get("dataBase64") or ""
        name = os.path.basename(obj.get("name") or "image.jpg").lower()
        caption = (obj.get("caption") or "").strip()[:500]
        if len(data_b64) > IMAGE_MAX_BYTES * 1.4:
            return "图片太大啦，拍近一点或者裁小一点再发"
        try:
            raw = base64.b64decode(data_b64, validate=False)
        except (binascii.Error, ValueError):
            return "图片数据坏了，重新选一张试试"
        if len(raw) > IMAGE_MAX_BYTES:
            return "图片太大啦（最多 6MB）"
        ext = name.rsplit(".", 1)[-1] if "." in name else "jpg"
        if ext not in IMG_TYPES:
            ext = "jpg"
        fname = "%s.%s" % (hashlib.sha1(raw).hexdigest()[:16], ext)
        os.makedirs(room.imgdir(), exist_ok=True)
        fp = os.path.join(room.imgdir(), fname)
        if not os.path.exists(fp):
            with open(fp, "wb") as f:
                f.write(raw)
        return room.append_chat_in(caption or "（图片）", client,
                                   image={"file_name": fname, "caption": caption})

    # ------------------------------------------------ 广播
    def _broadcast(self, room, obj, exclude=None):
        data = ws_encode(1, json.dumps(obj, ensure_ascii=False).encode("utf-8"))
        for c in list(room.conns):
            if c is exclude or c.closed:
                continue
            c.send_bytes(data)
        for w_c in list(self.pollers):
            if self.pollers[w_c]["room"] is room:
                self._finish_poll(w_c)

    # ------------------------------------------------ 文件轮询
    def _poll_files(self):
        for rid, room in list(self.rooms.items()):
            changed = room.diff()
            if not changed:
                continue
            known = len(room.msgs)
            room.rescan()
            for m in room.msgs[known:]:
                self._broadcast(room, m)
            if "outbox.md" in changed or "outbox-replies.md" in changed:
                self._broadcast(room, {"type": "pending", "asks": room.pending})
            if "status.md" in changed:
                if room.task.get("done") and not room.done_announced:
                    sysm = room.append_system("🎉 任务完成，总结马上来～")
                    self._broadcast(room, sysm)
                    room.done_announced = True
                self._broadcast(room, {"type": "task", "task": room.task,
                                       "done": room.task.get("done", False)})
                self._wake(room, "status")
            if "chat-in.md" in changed:
                self._wake(room, "chat-in")
            if "outbox.md" in changed:
                self._wake(room, "outbox")
            log("房间 %s 文件变化：%s（现 %d 条消息，待答 %d）"
                % (rid, ",".join(changed), room.last_id(), len(room.pending)))

    def _wake(self, room, event):
        if WAKE_MODE != "signal-file":
            return
        try:
            with open(os.path.join(room.base, ".wake"), "w", encoding="utf-8") as f:
                json.dump({"event": event, "room": room.id, "ts": now_ms()},
                          f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            pass

    def _check_timeouts(self):
        now = time.time()
        for c in list(self.conns):
            if c.closed:
                continue
            if c.kind == "ws":
                if now - c.last_seen > WS_IDLE_TIMEOUT:
                    self._drop(c)
                elif now - c.last_ping_sent > WS_PING_INTERVAL:
                    c.last_ping_sent = now
                    c.send_bytes(ws_encode(9, b"hb"))
            elif c.kind == "http" and now - c.last_seen > 30:
                self._drop(c)

    # ------------------------------------------------ 长轮询
    def _check_pollers(self):
        now = time.time()
        for c, w in list(self.pollers.items()):
            if w["room"].last_id() > w["since"] or now > w["deadline"]:
                self._finish_poll(c)

    def _finish_poll(self, c):
        w = self.pollers.pop(c, None)
        if w is None:
            return
        room = w["room"]
        if c.closed:
            return
        msgs = [room._mask_msg(m) for m in room.msgs if m["id"] > w["since"]]
        body = {"messages": msgs, "lastId": room.last_id(), "total": len(room.msgs),
                "pending": room.pending,
                "task": room.task, "done": room.task.get("done", False),
                "title": room.title, "clients": len(room.conns) + 1,
                "demo": self.demo, "muted": getattr(room, "demo_muted", False),
                "reactions": room.reactions, "revoked": list(room.revoked.keys()),
                "edited": list(room.edited.keys()),
                "editedMap": room.edited,
                "read": list(room.read.keys())}
        self._http_respond(c, 200, json.dumps(body, ensure_ascii=False).encode(),
                           "application/json")
        self._drop(c)

    # ------------------------------------------------ HTTP
    def _http_read(self, c: Conn):
        if c.body_left:
            need = c.body_left - len(c.http_body)
            if need:
                c.http_body += c.inbuf[:need]
                c.inbuf = c.inbuf[need:]
            if len(c.http_body) >= c.body_left:
                c.body_left = 0
                self._http_route(c)
            return
        if b"\r\n\r\n" not in c.inbuf:
            if len(c.inbuf) > 32768:
                self._http_respond(c, 431, b"headers too large", "text/plain")
                self._drop(c)
            return
        head, rest = c.inbuf.split(b"\r\n\r\n", 1)
        c.inbuf = rest
        try:
            lines = head.decode("latin1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {}
            for ln in lines[1:]:
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            u = urllib.parse.urlsplit(target)
            c.req = {"method": method.upper(), "target": target, "headers": headers,
                     "path": urllib.parse.unquote(u.path), "query": u.query}
            if "websocket" in headers.get("upgrade", "").lower():
                if self._try_ws_handshake(c, c.req):
                    return
            cl = int(headers.get("content-length") or 0)
            if cl > 12 * 1024 * 1024:
                self._http_respond(c, 413, b"body too large", "text/plain")
                self._drop(c)
                return
            if cl:
                c.body_left = cl
                c.http_body = b""
                take = min(cl, len(c.inbuf))
                c.http_body = c.inbuf[:take]
                c.inbuf = c.inbuf[take:]
                if len(c.http_body) >= cl:
                    c.body_left = 0
                    self._http_route(c)
                return
            self._http_route(c)
        except (ValueError, IndexError):
            self._http_respond(c, 400, b"bad request", "text/plain")
            self._drop(c)

    def _http_respond(self, c: Conn, code, body: bytes,
                      ctype="text/plain; charset=utf-8", extra=""):
        reason = {200: "OK", 204: "No Content", 400: "Bad Request", 403: "Forbidden",
                  404: "Not Found", 405: "Method Not Allowed", 413: "Payload Too Large",
                  426: "Upgrade Required", 431: "Request Header Fields Too Large"
                  }.get(code, "OK")
        head = ("HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
                "Access-Control-Allow-Origin: *\r\n"
                "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
                "Access-Control-Allow-Headers: Content-Type\r\n"
                "Cache-Control: no-store\r\nConnection: close\r\n%s\r\n"
                % (code, reason, ctype, len(body), extra))
        c.send_bytes(head.encode("latin1") + body)
        c.flush()

    def _http_route(self, c: Conn):
        req = c.req or {}
        path, method = req.get("path", "/"), req.get("method", "GET")
        qs = urllib.parse.parse_qs(req.get("query", ""))
        if method == "OPTIONS":
            self._http_respond(c, 204, b"")
            self._drop(c)
            return
        if path in ("/", "/index.html", "/chatroom.html"):
            self._serve_html(c)
            return
        if path == "/ws":
            self._http_respond(c, 426, "请用 WebSocket 连接本地址".encode("utf-8"),
                               "text/plain; charset=utf-8",
                               extra="Upgrade: websocket\r\n")
            self._drop(c)
            return
        if path == "/health":
            body = {"ok": True, "uptime": round(time.time() - self.started),
                    "rooms": len(self.rooms),
                    "connections": len([x for x in self.conns if x.kind == "ws"]),
                    "version": VERSION, "demo": self.demo}
            self._http_respond(c, 200, json.dumps(body).encode(), "application/json")
            self._drop(c)
            return
        if path == "/rooms":
            rooms = []
            for rid, r in self.rooms.items():
                last = r.msgs[-1] if r.msgs else None
                rooms.append({"id": rid, "title": r.title, "msgCount": r.last_id(),
                              "lastTs": last["ts"] if last else 0,
                              "lastText": (last.get("text") or "")[:60] if last else "",
                              "lastFrom": last["from"] if last else "",
                              "done": r.task.get("done", False),
                              "pending": len(r.pending), "conns": len(r.conns)})
            rooms.sort(key=lambda x: -x["lastTs"])
            self._http_respond(c, 200, json.dumps({"rooms": rooms},
                                                  ensure_ascii=False).encode(),
                               "application/json")
            self._drop(c)
            return
        if path == "/stats":
            stats = {"startedAt": self.started, "uptime": round(time.time() - self.started),
                     "rooms": [{"id": r.id, "title": r.title, "msgs": r.last_id(),
                                "conns": len(r.conns)} for r in self.rooms.values()]}
            self._http_respond(c, 200, json.dumps(stats, ensure_ascii=False).encode(),
                               "application/json")
            self._drop(c)
            return
        m = re.match(r"^/images/([^/]+)/([^/]+)$", path)
        if m and method == "GET":
            self._serve_image(c, m.group(1), m.group(2))
            return
        if path == "/poll" and method == "GET":
            self._start_poll(c, qs)
            return
        if path == "/send" and method == "POST":
            self._http_send(c, qs)
            return
        if path == "/upload" and method == "POST":
            self._http_upload(c, qs)
            return
        if path == "/files" and method == "GET":
            self._http_files(c, qs)
            return
        if path == "/history" and method == "GET":
            self._http_history(c, qs)
            return
        if path == "/mute" and method == "POST":
            self._http_mute(c, qs)
            return
        if path == "/reset" and method == "POST":
            self._http_reset(c, qs)
            return
        if path == "/react" and method == "POST":
            self._http_react(c, qs)
            return
        if path == "/revoke" and method == "POST":
            self._http_revoke(c, qs)
            return
        if path == "/edit" and method == "POST":
            self._http_edit(c, qs)
            return
        if path == "/reactions" and method == "GET":
            self._http_reactions(c, qs)
            return
        if path == "/export" and method == "GET":
            self._http_export(c, qs)
            return
        if path == "/export-all" and method == "GET":
            self._http_export_all(c, qs)
            return
        self._http_respond(c, 404, "没有这个页面。聊天室请打开根地址 /".encode("utf-8"),
                           "text/plain; charset=utf-8")
        self._drop(c)

    def _serve_html(self, c: Conn):
        try:
            with open(self.html_path, "rb") as f:
                data = f.read()
            self._http_respond(c, 200, data, "text/html; charset=utf-8",
                               extra="Cache-Control: no-cache\r\n")
        except OSError:
            self._http_respond(c, 404,
                               "chatroom.html 不在旁边（用 --html 指定路径）".encode("utf-8"),
                               "text/plain; charset=utf-8")
        self._drop(c)

    def _serve_image(self, c: Conn, rid, fname):
        if rid not in self.rooms or "/" in fname or ".." in fname:
            self._http_respond(c, 404, b"not found")
            self._drop(c)
            return
        ext = fname.rsplit(".", 1)[-1].lower()
        if ext not in IMG_TYPES:
            self._http_respond(c, 404, b"not found")
            self._drop(c)
            return
        fp = os.path.join(self.rooms[rid].base, "images", fname)
        try:
            with open(fp, "rb") as f:
                data = f.read()
            self._http_respond(c, 200, data, IMG_TYPES[ext],
                               extra="Cache-Control: public, max-age=86400, immutable\r\n")
        except OSError:
            self._http_respond(c, 404, b"not found")
        self._drop(c)

    def _start_poll(self, c: Conn, qs):
        rid = (qs.get("room") or [""])[0]
        token = (qs.get("token") or [""])[0]
        err = self._auth_room(rid, token)
        if err:
            self._http_respond(c, 403, json.dumps(
                {"error": err[1]}, ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        try:
            since = int((qs.get("since") or ["-1"])[0])
        except ValueError:
            since = -1
        try:
            wait = min(30.0, float((qs.get("wait") or ["25"])[0]))
        except ValueError:
            wait = 25.0
        room = self.rooms[rid]
        if since < 0:
            since = max(0, room.last_id() - 200)
        c.kind = "polling"
        self.pollers[c] = {"room": room, "since": since,
                           "deadline": time.time() + wait}
        self._check_pollers()

    def _http_send(self, c: Conn, qs):
        try:
            body = json.loads((c.http_body or b"{}").decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        rid = body.get("room") or (qs.get("room") or [""])[0]
        text = (body.get("text") or "").strip()
        client = (body.get("client") or (qs.get("client") or ["-"])[0])[:12]
        token = body.get("token") or (qs.get("token") or [""])[0]
        err = self._auth_room(rid, token)
        if err:
            self._http_respond(c, 403, json.dumps(
                {"error": err[1]}, ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        if not text or len(text) > TEXT_MAX:
            self._http_respond(c, 400, b'{"error":"text empty or too long"}',
                               "application/json")
            self._drop(c)
            return
        room = self.rooms[rid]
        m = room.append_chat_in(text, client)
        self._broadcast(room, m)
        sysmsg = room.append_system("✓ 已收到，马上转给开发主力")
        self._broadcast(room, sysmsg)
        self._wake(room, "chat-in")
        if self.bot:
            self.bot.feed("chat", room, {"text": text, "client": client, "id": m["id"]})
        self._http_respond(c, 200, json.dumps({"ok": True, "id": m["id"]},
                                              ensure_ascii=False).encode(),
                           "application/json")
        self._drop(c)

    def _http_upload(self, c: Conn, qs):
        try:
            body = json.loads((c.http_body or b"{}").decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        rid = body.get("room") or (qs.get("room") or [""])[0]
        token = body.get("token") or (qs.get("token") or [""])[0]
        err = self._auth_room(rid, token)
        if err:
            self._http_respond(c, 403, json.dumps(
                {"error": err[1]}, ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        room = self.rooms[rid]
        m = self._ingest_user_image(room, body, (body.get("client") or "-")[:12])
        if isinstance(m, dict):
            self._broadcast(room, m)
            self._wake(room, "chat-in")
            if self.bot:
                self.bot.feed("chat", room, {"text": m.get("text", ""),
                                             "client": body.get("client", "-"),
                                             "id": m["id"], "image": True,
                                             "caption": m.get("text", "")})
            self._http_respond(c, 200, json.dumps(
                {"ok": True, "id": m["id"], "image": m.get("image")},
                ensure_ascii=False).encode(), "application/json")
        else:
            self._http_respond(c, 400, json.dumps({"error": m},
                                                  ensure_ascii=False).encode(),
                               "application/json")
        self._drop(c)

    # ------------------------------------------------ mailbox 文件查看（/files）
    def _http_files(self, c: Conn, qs):
        rid = (qs.get("room") or [""])[0]
        token = (qs.get("token") or [""])[0]
        err = self._auth_room(rid, token)
        if err:
            self._http_respond(c, 403, json.dumps(
                {"error": err[1]}, ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        room = self.rooms[rid]
        limit = 6000
        files = []
        for name in FILE_ORDER:
            ent = {"name": name, "size": 0, "mtime": 0, "content": "", "truncated": False}
            try:
                st = os.stat(room.path(name))
                ent["size"] = st.st_size
                ent["mtime"] = int(st.st_mtime * 1000)
                with open(room.path(name), "r", encoding="utf-8",
                          errors="replace") as f:
                    txt = f.read(limit + 1)
                if len(txt) > limit:
                    ent["content"] = txt[:limit] + "\n…（太长已截断）"
                    ent["truncated"] = True
                else:
                    ent["content"] = txt
            except OSError:
                pass
            files.append(ent)
        body = {"room": rid, "title": room.title, "demo": self.demo,
                "msgCount": room.last_id(), "files": files}
        self._http_respond(c, 200, json.dumps(body, ensure_ascii=False).encode(),
                           "application/json")
        self._drop(c)

    # ------------------------------------------------ 九文件快照导出（/export）
    FILE_NOTES = {
        "chat-in.md": "用户 → 通讯官（用户说的话，watcher 落盘）",
        "chat-out.md": "通讯官 → 用户（大白话汇报）",
        "inbox.md": "消息卡片 → 主 agent（整理后的指令）",
        "inbox-status.md": "处理结果 ← 主 agent",
        "outbox.md": "待答问题 ← 主 agent（需要用户拍板）",
        "outbox-replies.md": "你的答复 → 主 agent",
        "status.md": "任务状态（阶段/阻塞/DONE）",
        "room.json": "房间元信息",
    }

    def _http_export(self, c: Conn, qs):
        """GET /export?room=&format=md|zip —— 九文件快照导出。
        md：单文档拼接（带目录与角色注释）；zip：九文件 + images/ 原样打包。
        下载文件名：liaison-<room>-snapshot-<yyyymmdd-HHMM>.<ext>"""
        rid = (qs.get("room") or [""])[0]
        token = (qs.get("token") or [""])[0]
        fmt = (qs.get("format") or ["md"])[0].lower()
        if fmt not in ("md", "zip"):
            fmt = "md"
        err = self._auth_room(rid, token)
        if err:
            self._http_respond(c, 403, json.dumps(
                {"error": err[1]}, ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        room = self.rooms[rid]
        stamp = time.strftime("%Y%m%d-%H%M")
        fname = "liaison-%s-snapshot-%s.%s" % (rid, stamp, fmt)
        if fmt == "zip":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                for name in FILE_ORDER:
                    try:
                        z.write(room.path(name), "mailbox/" + name)
                    except OSError:
                        pass  # 文件尚不存在 → 跳过
                img_dir = room.path("images")
                if os.path.isdir(img_dir):
                    for fn in sorted(os.listdir(img_dir)):
                        fp = os.path.join(img_dir, fn)
                        if os.path.isfile(fp):
                            try:
                                z.write(fp, "images/" + fn)
                            except OSError:
                                pass
            data = buf.getvalue()
            self._http_respond(
                c, 200, data, "application/zip",
                extra="Content-Disposition: attachment; filename=\"%s\"\r\n" % fname)
        else:
            parts = ["# Liaison 房间快照 · %s（%s）" % (rid, room.title),
                     "",
                     "- 导出时间：%s" % iso_now(),
                     "- 消息条数：%d" % room.last_id(),
                     "- 待答问题：%d" % len(room.pending),
                     "- 说明：本文件由 chat-watcher /export 生成，是 mailbox 九文件"
                     "在导出时刻的完整快照（教学/交接/留档用）。",
                     "",
                     "## 目录",
                     ""]
            for name in FILE_ORDER:
                note = self.FILE_NOTES.get(name, "")
                try:
                    size = os.stat(room.path(name)).st_size
                except OSError:
                    size = 0
                parts.append("- `%s`（%s，%d B）" % (name, note, size))
            parts.append("")
            for name in FILE_ORDER:
                try:
                    with open(room.path(name), "r", encoding="utf-8",
                              errors="replace") as f:
                        txt = f.read()
                except OSError:
                    txt = "（文件尚不存在）"
                parts.append("---")
                parts.append("")
                parts.append("## %s" % name)
                parts.append("")
                parts.append("> %s" % self.FILE_NOTES.get(name, ""))
                parts.append("")
                parts.append("```")
                parts.append(txt.rstrip("\n"))
                parts.append("```")
                parts.append("")
            data = "\n".join(parts).encode("utf-8")
            self._http_respond(
                c, 200, data, "text/markdown; charset=utf-8",
                extra="Content-Disposition: attachment; filename=\"%s\"\r\n" % fname)
        self._drop(c)

    # ------------------------------------------------ 多房间全量快照（/export-all）
    def _http_export_all(self, c: Conn, qs):
        """GET /export-all?format=zip|md&token= —— 所有房间一起打包（管理员/交接视角）。
        zip：rooms/<rid>/mailbox/* + rooms/<rid>/images/* + README.txt；
        md：每房一节拼接。全局 token 鉴权（不依赖房间号）。"""
        token = (qs.get("token") or [""])[0]
        if self.token and token != self.token:
            self._http_respond(c, 403, json.dumps(
                {"error": "口令不对，导出全部需要口令"}, ensure_ascii=False).encode(),
                "application/json")
            self._drop(c)
            return
        fmt = (qs.get("format") or ["zip"])[0].lower()
        if fmt not in ("md", "zip"):
            fmt = "zip"
        stamp = time.strftime("%Y%m%d-%H%M")
        fname = "liaison-all-rooms-%s.%s" % (stamp, fmt)
        rooms = sorted(self.rooms.values(), key=lambda r: r.id)
        if fmt == "zip":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                readme = ["# Liaison 全房间快照", "",
                          "- 导出时间：%s" % iso_now(),
                          "- watcher 版本：%s" % VERSION,
                          "- 房间数：%d" % len(rooms), "",
                          "## 房间目录"]
                for r in rooms:
                    readme.append("- `rooms/%s/`（%s，消息 %d 条，待答 %d）"
                                  % (r.id, r.title, r.last_id(), len(r.pending)))
                readme += ["", "每个房间包含 `mailbox/` 九文件与 `images/` 全部图片。", ""]
                z.writestr("README.txt", "\n".join(readme))
                for r in rooms:
                    prefix = "rooms/%s/" % r.id
                    z.writestr(prefix + "room.txt",
                               "房间：%s（%s）\n导出时间：%s\n消息条数：%d\n"
                               % (r.id, r.title, iso_now(), r.last_id()))
                    for name in FILE_ORDER:
                        try:
                            z.write(r.path(name), prefix + "mailbox/" + name)
                        except OSError:
                            pass
                    img_dir = r.path("images")
                    if os.path.isdir(img_dir):
                        for fn in sorted(os.listdir(img_dir)):
                            fp = os.path.join(img_dir, fn)
                            if os.path.isfile(fp):
                                try:
                                    z.write(fp, prefix + "images/" + fn)
                                except OSError:
                                    pass
            self._http_respond(
                c, 200, buf.getvalue(), "application/zip",
                extra="Content-Disposition: attachment; filename=\"%s\"\r\n" % fname)
        else:
            parts = ["# Liaison 全房间快照（%d 个房间）" % len(rooms), "",
                     "- 导出时间：%s" % iso_now(),
                     "- watcher 版本：%s" % VERSION, ""]
            for r in rooms:
                parts += ["", "# 房间 %s（%s）" % (r.id, r.title), "",
                          "消息 %d 条 · 待答 %d · 在线 %d" %
                          (r.last_id(), len(r.pending), len(r.conns)), ""]
                for name in FILE_ORDER:
                    try:
                        with open(r.path(name), "r", encoding="utf-8",
                                  errors="replace") as f:
                            txt = f.read()
                    except OSError:
                        txt = "（文件尚不存在）"
                    parts += ["## %s" % name, "",
                              "> %s" % self.FILE_NOTES.get(name, ""), "",
                              "```", txt.rstrip("\n"), "```", ""]
            self._http_respond(
                c, 200, "\n".join(parts).encode("utf-8"),
                "text/markdown; charset=utf-8",
                extra="Content-Disposition: attachment; filename=\"%s\"\r\n" % fname)
        self._drop(c)

    # ------------------------------------------------ 表情反应（/react）
    def _http_react(self, c: Conn, qs):
        """POST /react {room,target,emoji,client,token} —— 长轮询备胎模式下的反应通道。
        toggle 语义（同 client 同 emoji 再发 = 取消）；成功后广播 reaction 帧。"""
        try:
            body = json.loads((c.http_body or b"{}").decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        rid = str(body.get("room") or (qs.get("room") or [""])[0])
        token = str(body.get("token") or (qs.get("token") or [""])[0])
        client = str(body.get("client") or "")[:12] or c.cid
        err = self._auth_room(rid, token)
        if err:
            self._http_respond(c, 403, json.dumps(
                {"error": err[1]}, ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        try:
            target = int(body.get("target"))
        except (TypeError, ValueError):
            target = 0
        emoji = str(body.get("emoji") or "")
        room = self.rooms[rid]
        if not (isinstance(target, int) and 0 < target <= room.last_id()
                and emoji in REACT_EMOJIS):
            self._http_respond(c, 400, json.dumps(
                {"error": "这个反应点不了（消息不存在或表情不支持）"},
                ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        rx = room.add_reaction(target, emoji, client)
        self._broadcast(room, {"type": "reaction", "target": target,
                               "reactions": rx, "client": client})
        self._wake(room, "reaction")
        self._http_respond(c, 200, json.dumps(
            {"ok": True, "target": target, "reactions": rx},
            ensure_ascii=False).encode(), "application/json")
        self._drop(c)

    # ------------------------------------------------ 消息撤回（/revoke）
    def _http_revoke(self, c: Conn, qs):
        """POST /revoke {room,target,client,token} —— 长轮询备胎模式下的撤回通道。
        2 分钟内、仅自己发的消息；成功后广播 revoke 帧（客户端遮蔽气泡）。"""
        try:
            body = json.loads((c.http_body or b"{}").decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        rid = str(body.get("room") or (qs.get("room") or [""])[0])
        token = str(body.get("token") or (qs.get("token") or [""])[0])
        client = str(body.get("client") or "")[:12]
        err = self._auth_room(rid, token)
        if err:
            self._http_respond(c, 403, json.dumps(
                {"error": err[1]}, ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        try:
            target = int(body.get("target"))
        except (TypeError, ValueError):
            target = 0
        room = self.rooms[rid]
        err = room.revoke(target, client) if (target and client) else "这条撤不了（参数不对）"
        if err:
            self._http_respond(c, 400, json.dumps(
                {"error": err}, ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        self._broadcast(room, {"type": "revoke", "target": target,
                               "ts": now_ms(), "client": client})
        self._wake(room, "revoke")
        self._http_respond(c, 200, json.dumps(
            {"ok": True, "target": target}, ensure_ascii=False).encode(),
            "application/json")
        self._drop(c)

    # ------------------------------------------------ 消息编辑（/edit）
    def _http_edit(self, c: Conn, qs):
        """POST /edit {room,target,text,client,token} —— 长轮询备胎模式下的编辑通道。
        2 分钟内、仅自己发的消息、不能编辑已撤回的；成功后广播 edit 帧。"""
        try:
            body = json.loads((c.http_body or b"{}").decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        rid = str(body.get("room") or (qs.get("room") or [""])[0])
        token = str(body.get("token") or (qs.get("token") or [""])[0])
        client = str(body.get("client") or "")[:12]
        text = str(body.get("text") or "").strip()
        err = self._auth_room(rid, token)
        if err:
            self._http_respond(c, 403, json.dumps(
                {"error": err[1]}, ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        try:
            target = int(body.get("target"))
        except (TypeError, ValueError):
            target = 0
        room = self.rooms[rid]
        err, changed = room.edit(target, text, client) if (target and client and text) \
            else ("这条改不了（参数不对）", False)
        if err:
            self._http_respond(c, 400, json.dumps(
                {"error": err}, ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        if changed:
            self._broadcast(room, {"type": "edit", "target": target, "text": text,
                                   "ts": now_ms(), "client": client,
                                   "hist": room.edited.get(str(target), {}).get("hist", [])})
            self._wake(room, "edit")
            if self.demo and self.bot:
                self.bot.feed("edit", room, {"id": target, "text": text, "client": client})
        self._http_respond(c, 200, json.dumps(
            {"ok": True, "target": target}, ensure_ascii=False).encode(),
            "application/json")
        self._drop(c)

    def _http_reactions(self, c: Conn, qs):
        """GET /reactions?room=&token= —— 房间反应汇总（总数/各 emoji/最热消息）。
        不带 room → 全房间聚合（管理视角）。"""
        rid = (qs.get("room") or [""])[0]
        token = (qs.get("token") or [""])[0]
        rooms = []
        if rid:
            err = self._auth_room(rid, token)
            if err:
                self._http_respond(c, 403, json.dumps(
                    {"error": err[1]}, ensure_ascii=False).encode(),
                    "application/json")
                self._drop(c)
                return
            rooms = [self.rooms[rid]]
        else:
            # 全房间聚合需要全局 token（--token 未设时仅本机可访问）
            if self.token and token != self.token:
                self._http_respond(c, 403, json.dumps(
                    {"error": "口令不对，进不去"}, ensure_ascii=False).encode(),
                    "application/json")
                self._drop(c)
                return
            rooms = list(self.rooms.values())
        per_room, agg = {}, {}
        for r in rooms:
            s = r.reaction_stats()
            per_room[r.id] = s
            for e, n in s["byEmoji"].items():
                agg[e] = agg.get(e, 0) + n
        body = {"ok": True, "rooms": per_room,
                "total": sum(s["total"] for s in per_room.values()),
                "byEmoji": agg,
                "top": sorted((t | {"room": rid} for rid, s in per_room.items()
                               for t in s["top"]), key=lambda x: -x["count"])[:5]}
        self._http_respond(c, 200, json.dumps(body, ensure_ascii=False).encode(),
                           "application/json")
        self._drop(c)

    # ------------------------------------------------ 历史翻页（/history，before 游标）
    def _http_history(self, c: Conn, qs):
        """GET /history?room=&before=&limit=
        不带 before → 最新 limit 条；带 before → id < before 的最后 limit 条。
        响应：{room,title,total,messages,hasMore,lastId}"""
        rid = (qs.get("room") or [""])[0]
        token = (qs.get("token") or [""])[0]
        err = self._auth_room(rid, token)
        if err:
            self._http_respond(c, 403, json.dumps(
                {"error": err[1]}, ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        room = self.rooms[rid]
        try:
            before = int((qs.get("before") or ["0"])[0])
        except ValueError:
            before = 0
        try:
            limit = max(1, min(200, int((qs.get("limit") or ["50"])[0])))
        except ValueError:
            limit = 50
        total = len(room.msgs)
        if before <= 0:
            msgs = room.msgs[-limit:]
            has_more = total > limit
        else:
            older = [m for m in room.msgs if m["id"] < before]
            msgs = older[-limit:]
            has_more = len(older) > limit
        body = {"room": rid, "title": room.title, "total": total,
                "messages": msgs, "hasMore": has_more, "lastId": room.last_id()}
        self._http_respond(c, 200, json.dumps(body, ensure_ascii=False).encode(),
                           "application/json")
        self._drop(c)

    # ------------------------------------------------ 演示免打扰（/mute，仅 --demo）
    def _http_mute(self, c: Conn, qs):
        """POST /mute {room,on} → 关/开演示 bot 的每 45s 进度汇报。
        广播 {"type":"mute","on":…} 帧（含长轮询唤醒），hello 携带 muted。"""
        try:
            body = json.loads((c.http_body or b"{}").decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        rid = body.get("room") or (qs.get("room") or [""])[0]
        token = body.get("token") or (qs.get("token") or [""])[0]
        err = self._auth_room(rid, token)
        if err:
            self._http_respond(c, 403, json.dumps(
                {"error": err[1]}, ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        if not self.demo:
            self._http_respond(c, 403, json.dumps(
                {"error": "免打扰仅演示模式可用（--demo 启动）"},
                ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        room = self.rooms[rid]
        on = bool(body.get("on", True))
        room.demo_muted = on
        if on:
            # 立即清掉演示 bot 的计时，避免刚关扰又立刻补一条汇报
            self.bot.last_progress.pop(room.id, None)
        self._broadcast(room, {"type": "mute", "on": on})
        log("房间 %s 演示免打扰：%s" % (rid, "开" if on else "关"))
        self._http_respond(c, 200, json.dumps(
            {"ok": True, "room": rid, "muted": on},
            ensure_ascii=False).encode(), "application/json")
        self._drop(c)

    # ------------------------------------------------ 演示房间重置（/reset，仅 --demo）
    def _http_reset(self, c: Conn, qs):
        try:
            body = json.loads((c.http_body or b"{}").decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        rid = body.get("room") or (qs.get("room") or [""])[0]
        token = body.get("token") or (qs.get("token") or [""])[0]
        err = self._auth_room(rid, token)
        if err:
            self._http_respond(c, 403, json.dumps(
                {"error": err[1]}, ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        if not self.demo:
            self._http_respond(c, 403, json.dumps(
                {"error": "重置仅演示模式可用（--demo 启动）"},
                ensure_ascii=False).encode(), "application/json")
            self._drop(c)
            return
        room = self.rooms[rid]
        self.reset_room(room)
        self._http_respond(c, 200, json.dumps(
            {"ok": True, "room": rid, "lastId": room.last_id()},
            ensure_ascii=False).encode(), "application/json")
        self._drop(c)

    def reset_room(self, room):
        """把房间重置为初始状态：重写模板文件、清空图片与内存消息、重新问候。
        只在事件循环线程里调用（HTTP 路由），与 _poll_files 同线程无竞态。"""
        for name, tpl in TEMPLATES.items():
            with open(room.path(name), "w", encoding="utf-8") as f:
                f.write(tpl)
                f.flush()
                os.fsync(f.fileno())
        # 清掉已上传图片
        try:
            for fn in os.listdir(room.imgdir()):
                try:
                    os.remove(os.path.join(room.imgdir(), fn))
                except OSError:
                    pass
        except OSError:
            pass
        room.rescan()
        room._filestate = room.snapshot()
        room.done_announced = False
        room.demo_muted = False      # 重置同时清掉免打扰状态
        room.reactions = {}          # 清空表情反应
        room.revoked = {}            # 清空撤回标记
        room.edited = {}             # 清空编辑元数据
        room.read = {}               # 清空已读标记
        room.save_meta()
        # 通知所有在线客户端：房间已重置（WS 广播 + 唤醒长轮询等待者）
        frame = {"type": "reset", "room": room.id, "lastId": room.last_id()}
        data = ws_encode(1, json.dumps(frame, ensure_ascii=False).encode("utf-8"))
        for conn in list(room.conns):
            if not conn.closed:
                try:
                    conn.send_bytes(data)
                except Exception:
                    pass
        for w_c in list(self.pollers):
            if self.pollers[w_c]["room"] is room:
                self._finish_poll(w_c)
        log("房间 %s 已重置（演示模式）" % room.id)
        # 让演示 bot 重新自我介绍
        if self.bot:
            self.bot.last_progress.pop(room.id, None)
            self.bot.feed("init", room)

    # ------------------------------------------------ bot 输出（打字提示、自动反应等）
    def _drain_bot_out(self):
        for _ in range(64):
            try:
                kind, rid, val = self.bot_out.get_nowait()
            except queue.Empty:
                return
            room = self.rooms.get(rid)
            if not room:
                continue
            if kind == "typing":
                self._broadcast(room, {"type": "typing", "who": "liaison", "on": bool(val)})
            elif kind == "react":
                # 演示 bot 的自动反应（模拟主 agent 已读+态度），client 记为 liaison
                target, emoji = val
                if isinstance(target, int) and 0 < target <= room.last_id() \
                        and emoji in REACT_EMOJIS:
                    rx = room.add_reaction(target, emoji, "liaison")
                    self._broadcast(room, {"type": "reaction", "target": target,
                                           "reactions": rx, "client": "liaison"})
            elif kind == "read":
                # 已读回执：✓ 升级为 ✓✓（首次标记才广播，幂等）
                if isinstance(val, int) and room.mark_read(val):
                    self._broadcast(room, {"type": "ack_read", "target": val,
                                           "by": "liaison", "ts": now_ms()})

    # ------------------------------------------------ 关闭
    def _shutdown(self):
        log("正在关闭……")
        for c in list(self.conns):
            try:
                if c.kind == "ws" and not c.closed:
                    c.send_bytes(ws_encode(8, struct.pack(">H", 1001)))
            except Exception:
                pass
            self._drop(c)
        try:
            self.sel.close()
        except Exception:
            pass
        self.remove_pid()
        log("已退出。")


def main():
    global WAKE_MODE, LOG_FILE
    ap = argparse.ArgumentParser(description="Liaison 聊天室哨兵（chat-watcher）")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--mailbox", default="mailbox", help="mailbox 目录（绝对路径更稳）")
    ap.add_argument("--html", default=None, help="chatroom.html 路径（默认取脚本同目录）")
    ap.add_argument("--demo", action="store_true", help="开启演示模式（内置演示通讯官）")
    ap.add_argument("--create-room", metavar="ID", default=None, help="新建房间并退出")
    ap.add_argument("--title", default=None, help="新建房间的标题（配合 --create-room）")
    ap.add_argument("--wake-mode", choices=["none", "signal-file"], default="none")
    ap.add_argument("--token", default=None, help="访问口令（预留）")
    ap.add_argument("--log-file", default=None)
    ap.add_argument("--print-patch", action="store_true", help="打印通讯官提示词补丁后退出")
    args = ap.parse_args()

    if args.print_patch:
        print(PATCH_TEXT.format(wake_desc=WAKE_DESCS[args.wake_mode]))
        return

    WAKE_MODE = args.wake_mode
    LOG_FILE = args.log_file

    w = Watcher(args)
    if args.create_room:
        try:
            w.create_room(args.create_room, args.title)
            print("房间 %s 已建好" % args.create_room)
        except ValueError as e:
            print("建房失败：%s" % e)
            sys.exit(1)
        return

    w.write_pid()
    w.start_demo()
    try:
        w.listen()
    except OSError as e:
        print("启动失败：%r（端口被占用？）" % e)
        sys.exit(1)
    w.banner()

    def stop(sig, frm):
        w.running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    w.run()


if __name__ == "__main__":
    main()
