"""截图遮罩：每屏一个全置顶无边框窗，支持框选 + 松手后调整（移动/缩放）。

交互（对齐 Snipaste 习惯）：
    按下拖拽      创建选区
    松开          进入调整态：选区冻结，显示 8 个缩放手柄，工具栏出现
    拖拽选区内    移动选区
    拖拽手柄      缩放选区（四角/四边）
    选区外按下    重新开始框选
    双击选区内    确认
    Enter         确认；Esc 取消

DPI 处理：mss 抓取物理像素；Qt 6 控件坐标为逻辑像素，
二者按 devicePixelRatio 换算，裁剪/保存/剪贴板一律输出物理像素 1:1。
"""
import mss
import PIL.Image
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

_DIM = QColor(0, 0, 0, 110)          # 遮罩暗化层
_BORDER = QColor(0x18, 0x5F, 0xA5)   # 选区边框（蓝）
_HANDLE_SIZE = 8                     # 手柄热区半径 px
_MIN_SIZE = 5                        # 选区最小边长

_HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
_CURSORS = {
    "nw": Qt.CursorShape.SizeFDiagCursor, "se": Qt.CursorShape.SizeFDiagCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor, "sw": Qt.CursorShape.SizeBDiagCursor,
    "n": Qt.CursorShape.SizeVerCursor, "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor, "w": Qt.CursorShape.SizeHorCursor,
    "move": Qt.CursorShape.SizeAllCursor,
}


def _pil_to_qpixmap(img: PIL.Image.Image, dpr: float = 1.0) -> QPixmap:
    """PIL RGB 图像 -> QPixmap（保留物理像素，设置 dpr 供 Qt 正确映射）。"""
    img = img.convert("RGB")
    w, h = img.size
    qimg = QImage(img.tobytes(), w, h, w * 3, QImage.Format.Format_RGB888).copy()
    pix = QPixmap.fromImage(qimg)
    if dpr != 1.0:
        pix.setDevicePixelRatio(dpr)
    return pix


class ScreenShot:
    """一次屏幕捕获结果（物理像素）。"""

    def __init__(self, pixmap: QPixmap, pil_image: PIL.Image.Image, dpr: float):
        self.pixmap = pixmap
        self.pil_image = pil_image
        self.dpr = dpr

    def crop_logical_rect(self, rect: QRect) -> QPixmap:
        """按逻辑坐标矩形裁剪，返回 1:1 物理像素 QPixmap。"""
        return _pil_to_qpixmap(self.crop_pil_logical(rect))

    def crop_pil_logical(self, rect: QRect) -> PIL.Image.Image:
        """按逻辑坐标矩形裁剪，返回 PIL 图像（供 LLM 编码/压缩使用）。"""
        d = self.dpr
        box = (
            round(rect.x() * d), round(rect.y() * d),
            round((rect.x() + rect.width()) * d),
            round((rect.y() + rect.height()) * d),
        )
        return self.pil_image.crop(box)


def grab_screen(qscreen) -> ScreenShot:
    """抓取指定 QScreen 的整屏图像。"""
    from PySide6.QtGui import QGuiApplication

    geo = qscreen.geometry()
    virt = QGuiApplication.primaryScreen().virtualGeometry()
    dpr = qscreen.devicePixelRatio()
    phys_w, phys_h = round(geo.width() * dpr), round(geo.height() * dpr)
    expect_left = round((geo.left() - virt.left()) * dpr)
    expect_top = round((geo.top() - virt.top()) * dpr)

    with mss.mss() as sct:
        target = None
        for mon in sct.monitors[1:]:
            if mon["width"] == phys_w and mon["height"] == phys_h:
                target = mon
                if mon["left"] == expect_left and mon["top"] == expect_top:
                    break
        if target is None:  # 极端情况回退：抓虚拟整屏再裁
            full = sct.grab(sct.monitors[0])
            pil = PIL.Image.frombytes("RGB", full.size, full.rgb)
            pil = pil.crop((expect_left, expect_top,
                            expect_left + phys_w, expect_top + phys_h))
        else:
            shot = sct.grab(target)
            pil = PIL.Image.frombytes("RGB", shot.size, shot.rgb)
    return ScreenShot(_pil_to_qpixmap(pil, dpr), pil, dpr)


class SelectionOverlay(QWidget):
    """单屏全屏遮罩，框选 + 松手后调整。"""

    finished = Signal(QRect)   # 确认（双击/Enter/工具栏"完成"）
    cancelled = Signal()       # 取消（Esc/右键）
    locked = Signal(QRect)     # 松开鼠标、选区进入调整态
    changed = Signal(QRect)    # 调整态中选区移动/缩放
    drafting = Signal()        # 调整态中在选区外按下，重新开始框选

    def __init__(self, qscreen, shot: ScreenShot, parent=None):
        super().__init__(parent)
        self._screen = qscreen
        self.shot = shot
        self._state = "idle"    # idle / dragging / adjusting
        self._rect: QRect | None = None
        self._origin: QPoint | None = None
        self._action: str | None = None   # move / 八个手柄名 / None
        self._grab_offset = QPoint()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setGeometry(qscreen.geometry())
        self.show()
        self.activateWindow()
        self.setFocus()

    # ---- 对外 ----
    def current_rect(self) -> QRect | None:
        return self._rect

    def is_adjusting(self) -> bool:
        return self._state == "adjusting"

    def confirm(self):
        """确认当前选区（调整态有效）。"""
        if self._state == "adjusting" and self._rect is not None:
            self.finished.emit(QRect(self._rect))
            self.close()

    # ---- 手柄几何 ----
    def _handle_centers(self, rect: QRect) -> dict:
        return {
            "nw": QPoint(rect.left(), rect.top()),
            "n": QPoint(rect.center().x(), rect.top()),
            "ne": QPoint(rect.right(), rect.top()),
            "e": QPoint(rect.right(), rect.center().y()),
            "se": QPoint(rect.right(), rect.bottom()),
            "s": QPoint(rect.center().x(), rect.bottom()),
            "sw": QPoint(rect.left(), rect.bottom()),
            "w": QPoint(rect.left(), rect.center().y()),
        }

    def _hit_handle(self, pos: QPoint, rect: QRect | None) -> str | None:
        if rect is None:
            return None
        tol = _HANDLE_SIZE
        for name, c in self._handle_centers(rect).items():
            if abs(pos.x() - c.x()) <= tol and abs(pos.y() - c.y()) <= tol:
                return name
        return None

    def _cursor_at(self, pos: QPoint):
        if self._state != "adjusting" or self._rect is None:
            return Qt.CursorShape.CrossCursor
        hit = self._hit_handle(pos, self._rect)
        if hit:
            return _CURSORS[hit]
        if self._rect.contains(pos):
            return Qt.CursorShape.SizeAllCursor
        return Qt.CursorShape.CrossCursor

    def _apply_action(self, pos: QPoint):
        r = QRect(self._rect)
        if self._action == "move":
            r.moveTopLeft(pos - self._grab_offset)
        else:
            a = self._action
            if "n" in a:
                r.setTop(pos.y())
            if "s" in a:
                r.setBottom(pos.y())
            if "w" in a:
                r.setLeft(pos.x())
            if "e" in a:
                r.setRight(pos.x())
            r = r.normalized()
            if r.width() < _MIN_SIZE or r.height() < _MIN_SIZE:
                return  # 过小则保持上一帧，避免抖动
        bounds = self.rect()
        r.moveLeft(max(0, min(r.left(), bounds.width() - r.width())))
        r.moveTop(max(0, min(r.top(), bounds.height() - r.height())))
        self._rect = r

    # ---- 绘制 ----
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.shot.pixmap)
        painter.fillRect(self.rect(), _DIM)
        if self._rect is not None:
            sel = QRect(self._rect)
            d = self.shot.dpr
            # 注意：QPixmap 设置了 devicePixelRatio 后，drawPixmap 的源矩形
            # 按物理像素解释（经实验测定），必须把逻辑选区换算成物理坐标，
            # 否则高 DPI 屏上选区内容会被放大显示。
            src = QRect(round(sel.x() * d), round(sel.y() * d),
                        round(sel.width() * d), round(sel.height() * d))
            painter.drawPixmap(sel, self.shot.pixmap, src)  # 选区内容 1:1 提亮
            painter.setPen(QPen(_BORDER, 1))
            painter.drawRect(sel.adjusted(0, 0, -1, -1))
            if self._state == "adjusting":
                for c in self._handle_centers(sel).values():
                    painter.fillRect(c.x() - 4, c.y() - 4, 8, 8,
                                     QColor(0xFF, 0xFF, 0xFF))
                    painter.setPen(QPen(_BORDER, 1))
                    painter.drawRect(c.x() - 4, c.y() - 4, 8, 8)
            label = f"{sel.width()} × {sel.height()}"
            fw, fh = 78, 20
            fx = min(sel.right() - fw + 1, self.width() - fw - 2)
            fy = sel.bottom() + 4
            if fy + fh > self.height():
                fy = sel.top() - fh - 4
            painter.fillRect(fx, max(fy, 2), fw, fh, QColor(0, 0, 0, 160))
            painter.setPen(QColor(0xFF, 0xFF, 0xFF))
            painter.drawText(fx + 6, max(fy, 2) + 14, label)
        painter.end()

    # ---- 鼠标 ----
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()
            self.close()
            return
        pos = event.position().toPoint()
        if self._state == "adjusting":
            hit = self._hit_handle(pos, self._rect)
            if hit:
                self._action = hit
            elif self._rect.contains(pos):
                self._action = "move"
                self._grab_offset = pos - self._rect.topLeft()
            else:  # 选区外按下：重新框选
                self._state = "dragging"
                self._rect = None
                self._origin = pos
                self.drafting.emit()
        else:
            self._state = "dragging"
            self._origin = pos
            self._rect = QRect(pos, pos)
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._state == "dragging" and self._origin is not None:
            self._rect = QRect(self._origin, pos).normalized()
            self.update()
        elif self._state == "adjusting":
            if self._action is None:
                self.setCursor(self._cursor_at(pos))
            else:
                before = QRect(self._rect)
                self._apply_action(pos)
                if self._rect != before:
                    self.update()
                    self.changed.emit(QRect(self._rect))

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        if self._state == "dragging" and self._origin is not None:
            sel = QRect(self._origin, pos).normalized()
            self._origin = None
            if sel.width() >= _MIN_SIZE and sel.height() >= _MIN_SIZE:
                self._rect = sel
                self._state = "adjusting"
                self._action = None
                self.update()
                self.locked.emit(QRect(sel))
            else:
                self.cancelled.emit()
                self.close()
        elif self._state == "adjusting":
            self._action = None

    def mouseDoubleClickEvent(self, event):
        if self._state == "adjusting" and self._rect.contains(
                event.position().toPoint()):
            self.confirm()

    def enterEvent(self, event):
        super().enterEvent(event)
        if self._state == "adjusting":
            self.setCursor(self._cursor_at(event.position().toPoint()))

    # ---- 键盘 ----
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.confirm()
