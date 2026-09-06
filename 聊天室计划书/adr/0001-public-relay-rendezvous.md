# 用公网会合点连接聊天室与云端 watcher

AI 在云端运行，mailbox 与 chat-watcher.py 在云端，chatroom.html 在用户电脑，localhost 直连不可能。决定：双方各自出站连接到公共消息中继（默认 ntfy.sh，实测 CORS 开放、SSE/轮询均可用、免注册），中继地址做成两个文件里的配置项，可切换自建 ntfy。放弃了隧道方案（ngrok 类需要账号配置，且 URL 每会话变化，违反"AI 不发送任何东西给用户"）和云端开端口方案（沙箱通常无公网入站）。
