"""SnapAsk —— 自研截图 + 千问知识问答工具（M1：截图闭环）。

模块划分：
    config.py    配置读写（config.json 持久化）
    hotkey.py    全局热键注册（pynput）+ Win32 冲突探测
    overlay.py   多屏遮罩框选 + 屏幕捕获
    toolbar.py   选区下方迷你工具栏
    storage.py   保存 PNG / 复制到剪贴板
    main.py      入口：托盘 + 热键 + 流程编排
"""
