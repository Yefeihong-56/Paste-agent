"""选区下方迷你工具栏（保存 / 问 AI 占位 / 完成确认）。

出现在选区锁定（松手）后，随选区移动/缩放重新定位；
无交互自动收起。按钮不抢焦点，键盘事件始终回到遮罩层。
"""
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget


class MiniToolbar(QWidget):
    save_requested = Signal()
    confirm_requested = Signal()   # "完成"：确认当前选区
    closed = Signal()

    _AUTO_HIDE_MS = 8000  # 无交互自动收起

    def __init__(self, anchor_rect, screen_geo, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.btn_confirm = QPushButton("完成")
        self.btn_confirm.clicked.connect(self.confirm_requested)
        self.btn_save = QPushButton("保存")
        self.btn_save.clicked.connect(self.save_requested)
        self.btn_ask = QPushButton("问 AI")
        self.btn_ask.setEnabled(False)  # M2 接入问答后启用
        self.btn_ask.setToolTip("截图问答功能将在 M2 上线")
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.close)
        for b in (self.btn_confirm, self.btn_save, self.btn_ask, self.btn_close):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # 不抢遮罩层键盘焦点
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(b)

        self.adjustSize()
        self.reposition(anchor_rect, screen_geo)

        self._timer = QTimer(self, singleShot=True, interval=self._AUTO_HIDE_MS)
        self._timer.timeout.connect(self.close)

    def reposition(self, anchor_rect, screen_geo):
        """跟随选区重新定位：选区下边缘 +8px，左对齐；越界时上移/右移。"""
        x = anchor_rect.left()
        y = anchor_rect.bottom() + 8
        if x + self.width() > screen_geo.right():
            x = screen_geo.right() - self.width() - 4
        if y + self.height() > screen_geo.bottom():
            y = anchor_rect.top() - self.height() - 8
        self.move(x, y)

    def refresh(self):
        """重置自动收起计时（选区变化或点击按钮后调用）。"""
        if self.isVisible():
            self._timer.start()

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
