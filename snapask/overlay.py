"""截图遮罩：每屏一个全置顶无边框窗，拖拽框选；屏幕捕获与裁剪。

DPI 处理：mss 抓取的是物理像素；Qt 6 默认开启高 DPI 缩放，控件坐标
是逻辑像素。二者通过 devicePixelRatio 换算：物理 = 逻辑 × dpr。
裁剪、保存、剪贴板一律使用物理像素 1:1 输出。
"""
import mss
import PIL.Image
from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

_DIM = QColor(0, 0, 0, 110)          # 遮罩暗化层
_BORDER = QColor(0x18, 0x5F, 0xA5)   # 选区边框（蓝）


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
        d = self.dpr
        box = (
            round(rect.x() * d), round(rect.y() * d),
            round((rect.x() + rect.width()) * d),
            round((rect.y() + rect.height()) * d),
        )
        cropped = self.pil_image.crop(box)
        return _pil_to_qpixmap(cropped)


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
            target = sct.monitors[0]
            full = sct.grab(target)
            pil = PIL.Image.frombytes("RGB", full.size, full.rgb)
            pil = pil.crop((expect_left, expect_top,
                            expect_left + phys_w, expect_top + phys_h))
        else:
            shot = sct.grab(target)
            pil = PIL.Image.frombytes("RGB", shot.size, shot.rgb)
    return ScreenShot(_pil_to_qpixmap(pil, dpr), pil, dpr)


class SelectionOverlay(QWidget):
    """单屏全屏遮罩：拖拽框选，Esc/右键取消。"""

    finished = Signal(QRect)   # 逻辑坐标矩形（相对本屏）
    cancelled = Signal()

    def __init__(self, qscreen, shot: ScreenShot, parent=None):
        super().__init__(parent)
        self._screen = qscreen
        self.shot = shot
        self._origin = None
        self._current = None
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

    # ---- 绘制 ----
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.shot.pixmap)
        painter.fillRect(self.rect(), _DIM)
        if self._origin is not None and self._current is not None:
            sel = QRect(self._origin, self._current).normalized()
            painter.drawPixmap(sel, self.shot.pixmap, sel)  # 选区提亮
            painter.setPen(QPen(_BORDER, 1))
            painter.drawRect(sel.adjusted(0, 0, -1, -1))
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
        self._origin = event.position().toPoint()
        self._current = self._origin
        self.update()

    def mouseMoveEvent(self, event):
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._origin is None:
            return
        sel = QRect(self._origin, event.position().toPoint()).normalized()
        self._origin = self._current = None
        if sel.width() >= 5 and sel.height() >= 5:
            self.finished.emit(sel)
        else:
            self.cancelled.emit()
        self.close()

    # ---- 键盘 ----
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            self.close()
