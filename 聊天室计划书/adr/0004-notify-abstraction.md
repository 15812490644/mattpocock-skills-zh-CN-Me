# 唤醒通讯官走抽象接口，不绑定宿主机制

mailbox 契约规定 watcher 负责"唤醒"通讯官，但宿主机制（SendMessage、hook 等）在设计时未知。决定：watcher 检测到事件时打印一行 JSON 到 stdout，并运行 `--notify-cmd` 传入的外部命令（事件 JSON 放 argv[1] 与环境变量 LIAISON_EVENT），具体接线由部署方/执行 AI 现场完成。watcher 只管"发现"，不管"怎么叫"。
