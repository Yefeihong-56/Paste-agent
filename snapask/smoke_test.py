"""M1.5 冒烟测试：offscreen 模式下验证截图链路与选区交互状态机。

运行：QT_QPA_PLATFORM=offscreen python smoke_test.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import hotkey as hk
import overlay as ov
import storage
import toolbar as tb


def main():
    # 1) 配置
    cfg = config.load()
    assert cfg["hotkey"] == "f1" and cfg["auto_copy_clipboard"] is True
    print("[1] config OK")

    # 2) 热键解析与冲突探测
    assert hk.parse("alt+f1") == (0x0001, 0x70)
    assert hk.to_pynput_combo("f1") == "<f1>"
    print(f"[2] hotkey parse OK; F1 taken={hk.is_hotkey_taken('f1')}")

    # 3) Qt 离屏应用
    from PySide6.QtCore import QPoint, QRect, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)

    screen = QGuiApplication.primaryScreen()
    geo = screen.geometry()
    dpr = screen.devicePixelRatio()
    print(f"[3] screen {geo.width()}x{geo.height()} logical, dpr={dpr}")

    # 4) 捕获 -> 裁剪 -> 保存
    shot = ov.grab_screen(screen)
    full = shot.crop_logical_rect(geo)
    expect_w, expect_h = round(geo.width() * dpr), round(geo.height() * dpr)
    assert (full.width(), full.height()) == (expect_w, expect_h)
    saved_path = storage.save_pixmap(
        full, os.path.join(tempfile.gettempdir(), "snapask_smoke"))
    assert os.path.getsize(saved_path) > 1024
    print(f"[4] capture+crop+save OK: {expect_w}x{expect_h}px")

    # 5) 部分选区裁剪
    part = shot.crop_logical_rect(QRect(0, 0, 200, 150))
    assert (part.width(), part.height()) == (round(200 * dpr), round(150 * dpr))
    print("[5] partial crop OK")

    # 6) 工具栏构造
    bar = tb.MiniToolbar(QRect(100, 100, 300, 200), geo)
    assert bar.btn_ask.isEnabled() is False  # M2 占位
    bar.reposition(QRect(150, 150, 300, 200), geo)
    bar.refresh()
    bar.close()
    print("[6] toolbar OK (reposition/refresh/ask-disabled)")

    # 7) 剪贴板（离屏平台可能不支持，失败不阻塞）
    try:
        storage.copy_to_clipboard(part)
        print("[7] clipboard OK")
    except Exception as e:  # noqa: BLE001
        print(f"[7] clipboard skipped on offscreen: {type(e).__name__}")

    # 8) 选区交互状态机（模拟鼠标/键盘事件）
    from PySide6.QtTest import QTest

    L = Qt.MouseButton.LeftButton
    N = Qt.KeyboardModifier.NoModifier

    def make_overlay():
        w = ov.SelectionOverlay(screen, shot)
        return w

    def drag(w, p1, p2):
        QTest.mousePress(w, L, N, QPoint(*p1))
        QTest.mouseMove(w, QPoint(*p2))
        QTest.mouseRelease(w, L, N, QPoint(*p2))

    # 8a 创建选区 -> 松开进入调整态且遮罩不消失
    w = make_overlay()
    locked, changed = [], []
    w.locked.connect(locked.append)
    w.changed.connect(changed.append)
    drag(w, (60, 50), (200, 150))
    assert w.is_adjusting(), "松开后应进入调整态"
    assert w.isVisible(), "调整态遮罩必须保持打开（修复：很快消失）"
    r1 = w.current_rect()
    assert locked and locked[0] == r1
    assert (r1.width(), r1.height()) == (141, 101)  # QRect(点,点) 含两端点
    print("[8a] 框选->松手保持 OK")

    # 8b 拖 SE 手柄缩放
    se = (r1.right(), r1.bottom())
    drag(w, se, (se[0] + 60, se[1] + 40))
    r2 = w.current_rect()
    assert (r2.width(), r2.height()) == (201, 141), f"缩放失效: {r2}"
    assert changed, "缩放应触发 changed 信号（工具栏跟随）"
    print("[8b] 手柄缩放 OK（修复：不可调节大小）")

    # 8c 选区内拖拽移动
    c = r2.center()
    drag(w, (c.x(), c.y()), (c.x() + 30, c.y() + 20))
    r3 = w.current_rect()
    assert r3.topLeft() == r2.topLeft() + QPoint(30, 20), f"移动失效: {r3}"
    print("[8c] 选区内移动 OK")

    # 8d Enter 确认
    finished = []
    w.finished.connect(finished.append)
    QTest.keyClick(w, Qt.Key.Key_Return)
    assert finished and finished[0] == r3, "Enter 应确认当前选区"
    assert not w.isVisible()
    print("[8d] Enter 确认 OK")

    # 8e 双击确认
    w2 = make_overlay()
    fin2 = []
    w2.finished.connect(fin2.append)
    drag(w2, (80, 80), (260, 220))
    QTest.mouseDClick(w2, L, N, QPoint(150, 150))
    assert fin2 and fin2[0] == QRect(QPoint(80, 80), QPoint(260, 220)).normalized()
    print("[8e] 双击确认 OK")

    # 8f Esc 取消
    w3 = make_overlay()
    cancelled = []
    w3.cancelled.connect(lambda: cancelled.append(True))
    drag(w3, (80, 80), (200, 200))
    QTest.keyClick(w3, Qt.Key.Key_Escape)
    assert cancelled and not w3.isVisible()
    print("[8f] Esc 取消 OK")

    # 8g 调整态中选区外按下 -> 重新框选
    w4 = make_overlay()
    drafting = []
    w4.drafting.connect(lambda: drafting.append(True))
    drag(w4, (80, 80), (200, 200))
    QTest.mousePress(w4, L, N, QPoint(400, 400))
    assert drafting and not w4.is_adjusting()
    print("[8g] 选区外按下重新框选 OK")

    # 8h 单击（极小选区）-> 取消
    w5 = make_overlay()
    cancelled5 = []
    w5.cancelled.connect(lambda: cancelled5.append(True))
    QTest.mousePress(w5, L, N, QPoint(100, 100))
    QTest.mouseRelease(w5, L, N, QPoint(100, 100))
    assert cancelled5 and not w5.isVisible()
    print("[8h] 极小选区自动取消 OK")

    # 8i 高 DPI(2x) 下选区预览必须 1:1（修复"放大"缺陷的回归测试）
    from PIL import Image as PILImage
    pil2 = PILImage.effect_noise((1600, 1600), 64).convert("RGB")
    shot2 = ov.ScreenShot(ov._pil_to_qpixmap(pil2, 2.0), pil2, 2.0)
    w6 = ov.SelectionOverlay(screen, shot2)
    w6._state = "adjusting"
    w6._rect = QRect(400, 400, 200, 200)
    w6.update()
    rendered = w6.grab().toImage()
    samples = [(450, 450), (550, 450), (450, 550), (550, 550),
               (480, 480), (520, 520), (500, 500), (410, 590),
               (590, 410), (430, 430), (570, 570), (500, 460)]
    for x, y in samples:
        got = rendered.pixelColor(x, y)
        want = pil2.getpixel((x * 2, y * 2))
        assert (abs(got.red() - want[0]) <= 2
                and abs(got.green() - want[1]) <= 2
                and abs(got.blue() - want[2]) <= 2), \
            f"选区内 ({x},{y}) 应显示原图 1:1 内容 {want}，实际 {got}"
    print("[8i] 高DPI(2x)选区预览 1:1 OK（12 采样点）")
    w6.close()

    # 9) main 模块导入与控制器构造（防 NameError 类问题漏网）
    import main as app_main
    icon = app_main._make_icon()
    assert not icon.isNull()
    controller = app_main.AppController(cfg)
    assert controller._capturing is False
    print("[9] main import + controller OK")

    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
