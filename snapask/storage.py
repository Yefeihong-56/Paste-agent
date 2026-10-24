"""保存 PNG 与剪贴板。"""
import os
from datetime import datetime

from PySide6.QtGui import QGuiApplication, QPixmap


def default_filename() -> str:
    return datetime.now().strftime("截图_%Y%m%d_%H%M%S") + ".png"


def save_pixmap(pixmap: QPixmap, save_dir: str) -> str:
    """保存到本地，返回文件路径。"""
    os.makedirs(save_dir, exist_ok=True)
    path = os.path.join(save_dir, default_filename())
    pixmap.save(path, "PNG")
    return path


def copy_to_clipboard(pixmap: QPixmap) -> None:
    """复制 PNG 到系统剪贴板（保留 Snipaste 旧习惯）。

    说明：Qt 的 setImage 会同时提供 CF_DIB / PNG 等多种格式，
    微信、QQ、画图、Office 等均可直接粘贴；若后续遇到个别
    软件只能识别 CF_PNG 的情况，再在 M3 用 Win32 API 直写。
    """
    QGuiApplication.clipboard().setImage(pixmap.toImage())
