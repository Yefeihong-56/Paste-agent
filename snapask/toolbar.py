"""选区下方迷你工具栏（M1：保存 / 问 AI 占位 / 完成）。"""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget


class MiniToolbar(QWidget):
    save_requested = Signal()
    ask_requested = Signal()
    closed = Signal()

    _AUTO_HIDE_MS = 8000  # 无交互自动收起

    def __init__(self, anchor_rect, screen_geo, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.btn_save = QPushButton("保存")
        self.btn_save.clicked.connect(self._on_save)
        self.btn_ask = QPushButton("问 AI")
        self.btn_ask.setEnabled(False)  # M2 接入问答后启用
        self.btn_ask.setToolTip("截图问答功能将在 M2 上线")
        self.btn_done = QPushButton("完成")
        self.btn_done.clicked.connect(self.close)
        for b in (self.btn_save, self.btn_ask, self.btn_done):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(b)

        self.adjustSize()
        # 定位：选区下边缘 +8px，左对齐；越界时上移/右移
        x = anchor_rect.left()
        y = anchor_rect.bottom() + 8
        if x + self.width() > screen_geo.right():
            x = screen_geo.right() - self.width() - 4
        if y + self.height() > screen_geo.bottom():
            y = anchor_rect.top() - self.height() - 8
        self.move(x, y)

        self._timer = QTimer(self, singleShot=True, interval=self._AUTO_HIDE_MS)
        self._timer.timeout.connect(self.close)

    def _on_save(self):
        self.save_requested.emit()

    def showEvent(self, event):
        super().showEvent(event)
        self._timer.start()

    def closeEvent(self, event):
        self._timer.stop()
        self.closed.emit()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
