"""SnapAsk 入口：托盘常驻 + F1 截图闭环（M1）。

运行：python main.py
流程：F1 -> 多屏遮罩框选 -> 松开即自动复制剪贴板 -> 迷你工具栏（保存/问AI占位/完成）
"""
import os
import sys

from PySide6.QtCore import QObject, QRect, Qt, Signal
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import config
import hotkey as hk
import overlay as ov
import storage
import toolbar as tb


def _make_icon() -> QIcon:
    """生成一个简单的应用图标（蓝底白字 S）。"""
    pix = QPixmap(32, 32)
    pix.fill(QColor(0x18, 0x5F, 0xA5))
    painter = QPainter(pix)
    painter.setPen(QColor(0xFF, 0xFF, 0xFF))
    from PySide6.QtGui import QFont
    font = QFont()
    font.setPixelSize(20)
    font.setWeight(500)
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
        self._toolbar: tb.MiniToolbar | None = None
        self._last_pixmap: QPixmap | None = None
        self.bridge = HotkeyBridge()
        self.bridge.triggered.connect(self.start_capture)

    # ---- 截图流程 ----
    def start_capture(self):
        if self._capturing:
            return
        self._capturing = True
        self._overlays = []
        for screen in QGuiApplication.screens():
            shot = ov.grab_screen(screen)
            w = ov.SelectionOverlay(screen, shot)
            w.finished.connect(self._on_finished)
            w.cancelled.connect(self._on_cancelled)
            self._overlays.append(w)

    def _close_overlays(self):
        for w in self._overlays:
            w.close()
        self._overlays = []
        self._capturing = False

    def _on_finished(self, rect: QRect):
        w = self.sender()
        pixmap = w.shot.crop_logical_rect(rect)
        self._last_pixmap = pixmap
        screen_geo = w.screen().geometry()
        anchor = QRect(screen_geo.topLeft() + rect.topLeft(), rect.size())
        self._close_overlays()
        if self.cfg.get("auto_copy_clipboard", True):
            storage.copy_to_clipboard(pixmap)
        self._toolbar = tb.MiniToolbar(anchor, screen_geo)
        self._toolbar.save_requested.connect(self._on_save)
        self._toolbar.show()

    def _on_cancelled(self):
        self._close_overlays()

    def _on_save(self):
        if self._last_pixmap is None:
            return
        path = storage.save_pixmap(self._last_pixmap, self.cfg["save_dir"])
        self.tray.showMessage("SnapAsk", f"已保存：{path}", msecs=3000)
        if self._toolbar is not None:
            self._toolbar.close()

    # ---- 托盘与生命周期 ----
    def setup_tray(self, app: QApplication):
        self.tray = QSystemTrayIcon(_make_icon(), app)
        menu = QMenu()
        act_cap = QAction("截图（%s）" % self.cfg["hotkey"].upper())
        act_cap.triggered.connect(self.start_capture)
        act_dir = QAction("打开保存目录")
        act_dir.triggered.connect(self._open_save_dir)
        act_quit = QAction("退出")
        act_quit.triggered.connect(app.quit)
        menu.addAction(act_cap)
        menu.addAction(act_dir)
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
