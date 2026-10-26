"""SnapAsk 入口：托盘常驻 + F1 截图闭环（M1.5：选区可调整）。

运行：python main.py
流程：F1 -> 多屏遮罩框选 -> 松开进入调整态（移动/8向缩放，工具栏出现）
      -> 双击/Enter/"完成"确认 -> 自动复制剪贴板 -> 工具栏可保存
"""
import os
import subprocess
import sys

from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import config
import hotkey as hk
import overlay as ov
import qa_agent
import ask_panel
import storage
import toolbar as tb


def _make_icon() -> QIcon:
    """生成一个简单的应用图标（蓝底白字 S）。"""
    pix = QPixmap(32, 32)
    pix.fill(QColor(0x18, 0x5F, 0xA5))
    painter = QPainter(pix)
    painter.setPen(QColor(0xFF, 0xFF, 0xFF))
    font = QFont()
    font.setPixelSize(20)
    font.setWeight(QFont.Weight.Medium)  # Medium = 500；PySide6 6.x 起只接受枚举
    painter.setFont(font)
    painter.drawText(QRect(0, 0, 32, 32), Qt.AlignmentFlag.AlignCenter, "S")
    painter.end()
    return QIcon(pix)


class HotkeyBridge(QObject):
    """pynput 回调在子线程，经 Qt 信号投递到主线程安全地创建窗口。"""
    triggered = Signal()


class AppController(QObject):
    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self._capturing = False
        self._overlays: list[ov.SelectionOverlay] = []
        self._active_overlay: ov.SelectionOverlay | None = None
        self._toolbar: tb.MiniToolbar | None = None
        self._last_pixmap: QPixmap | None = None
        self._last_shot: ov.ScreenShot | None = None
        self._last_rect: QRect | None = None
        self._last_screen_geo: QRect | None = None
        self._panel: ask_panel.AskPanel | None = None
        self.bridge = HotkeyBridge()
        self.bridge.triggered.connect(self.start_capture)

    # ---- 截图流程 ----
    def start_capture(self):
        if self._capturing:
            return
        self._capturing = True
        self._close_panel()      # 新截图 = 新会话
        self._close_toolbar()
        self._overlays = []
        self._active_overlay = None
        for screen in QGuiApplication.screens():
            shot = ov.grab_screen(screen)
            w = ov.SelectionOverlay(screen, shot)
            w.locked.connect(self._on_locked)
            w.changed.connect(self._on_changed)
            w.drafting.connect(self._on_drafting)
            w.finished.connect(self._on_finished)
            w.cancelled.connect(self._on_cancelled)
            self._overlays.append(w)

    def _close_overlays(self):
        for w in self._overlays:
            w.close()
        self._overlays = []
        self._active_overlay = None
        self._capturing = False

    # ---- 选区状态 ----
    def _on_locked(self, rect: QRect):
        """松开鼠标：选区冻结进入调整态；关闭其他屏遮罩，弹出工具栏。"""
        w = self.sender()
        self._active_overlay = w
        self._last_shot = w.shot
        self._last_rect = QRect(rect)
        self._last_screen_geo = w.screen().geometry()
        for o in self._overlays:
            if o is not w:
                o.close()
        self._overlays = [w]
        self._show_toolbar(w, rect)

    def _on_drafting(self):
        """调整态中在选区外按下：重新框选，先收起工具栏。"""
        self._close_toolbar()

    def _on_changed(self, rect: QRect):
        """选区移动/缩放：工具栏跟随。"""
        w = self.sender()
        if self._toolbar is not None and self._active_overlay is w:
            screen_geo = w.screen().geometry()
            anchor = QRect(screen_geo.topLeft() + rect.topLeft(), rect.size())
            self._toolbar.reposition(anchor, screen_geo)
            self._toolbar.refresh()

    def _on_finished(self, rect: QRect):
        """确认选区：复制剪贴板，关闭遮罩，保留工具栏供保存/问 AI。"""
        w = self.sender()
        pixmap = w.shot.crop_logical_rect(rect)
        self._last_pixmap = pixmap
        self._last_shot = w.shot
        self._last_rect = QRect(rect)
        self._last_screen_geo = w.screen().geometry()
        if self.cfg.get("auto_copy_clipboard", True):
            storage.copy_to_clipboard(pixmap)
        self._close_overlays()
        if self._toolbar is not None:
            self._toolbar.refresh()

    def _on_cancelled(self):
        self._close_overlays()
        self._close_toolbar()

    # ---- 工具栏 ----
    def _show_toolbar(self, overlay: ov.SelectionOverlay, rect: QRect):
        screen_geo = overlay.screen().geometry()
        anchor = QRect(screen_geo.topLeft() + rect.topLeft(), rect.size())
        if self._toolbar is not None:
            self._toolbar.close()
        self._toolbar = tb.MiniToolbar(anchor, screen_geo)
        self._toolbar.confirm_requested.connect(self._on_confirm)
        self._toolbar.save_requested.connect(self._on_save)
        self._toolbar.ask_requested.connect(self._on_ask)
        self._toolbar.closed.connect(self._on_toolbar_closed)
        self._toolbar.show()

    def _on_toolbar_closed(self):
        self._toolbar = None

    # ---- 问 AI 面板（M2）----
    def _on_ask(self):
        # 若仍在调整态，先按当前选区确认（同步触发 _on_finished：复制剪贴板）
        w = self._active_overlay
        if w is not None and w.is_adjusting():
            w.confirm()
        shot, rect = self._last_shot, self._last_rect
        if shot is None or rect is None or self._last_screen_geo is None:
            return
        self._close_panel()
        self._panel = ask_panel.AskPanel(shot, rect, self._last_screen_geo)
        self._panel.closed.connect(self._on_panel_closed)
        self._panel.show()

    def _on_panel_closed(self):
        self._panel = None

    def _close_panel(self):
        if self._panel is not None:
            self._panel.close()
            self._panel = None

    def _on_confirm(self):
        if self._active_overlay is not None and self._active_overlay.is_adjusting():
            self._active_overlay.confirm()

    def _on_save(self):
        w = self._active_overlay
        pixmap = None
        if w is not None and w.is_adjusting():
            pixmap = w.shot.crop_logical_rect(w.current_rect())
        elif self._last_pixmap is not None:
            pixmap = self._last_pixmap
        if pixmap is None:
            return
        path = storage.save_pixmap(pixmap, self.cfg["save_dir"])
        self.tray.showMessage("SnapAsk", f"已保存：{path}", msecs=3000)
        self._close_toolbar()

    def _close_toolbar(self):
        if self._toolbar is not None:
            self._toolbar.close()
            self._toolbar = None

    # ---- 托盘与生命周期 ----
    def setup_tray(self, app: QApplication):
        self.tray = QSystemTrayIcon(_make_icon(), app)
        menu = QMenu()
        act_cap = QAction("截图（%s）" % self.cfg["hotkey"].upper())
        act_cap.triggered.connect(self.start_capture)
        act_dir = QAction("打开保存目录")
        act_dir.triggered.connect(self._open_save_dir)
        act_key = QAction("配置 API Key（.env）")
        act_key.triggered.connect(self._locate_env)
        act_quit = QAction("退出")
        act_quit.triggered.connect(app.quit)
        menu.addAction(act_cap)
        menu.addAction(act_dir)
        menu.addAction(act_key)
        menu.addSeparator()
        menu.addAction(act_quit)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("SnapAsk 截图问答")
        self.tray.show()
        if hk.is_hotkey_taken(self.cfg["hotkey"]):
            self.tray.showMessage(
                "SnapAsk",
                f"截图热键 {self.cfg['hotkey'].upper()} 已被其他程序占用，"
                "按键可能同时触发两个程序，可在设置中改绑（M3 上线）。",
                msecs=8000,
            )
        return self.tray

    def _open_save_dir(self):
        os.startfile(self.cfg["save_dir"])

    def _locate_env(self):
        """在资源管理器中定位 .env（不存在则从模板创建），引导用户填 Key。"""
        path = qa_agent.ensure_env_file()
        if os.path.exists(path):
            subprocess.run(["explorer", "/select," + path])

    def start_hotkey(self):
        self.listener = hk.start_listener(self.cfg["hotkey"], self.bridge.triggered.emit)

    def stop_hotkey(self):
        if getattr(self, "listener", None):
            self.listener.stop()


def main():
    cfg = config.load()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    controller = AppController(cfg)
    controller.setup_tray(app)
    controller.start_hotkey()

    app.aboutToQuit.connect(controller.stop_hotkey)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
