"""M1 冒烟测试：offscreen 模式下验证截图链路（不弹窗、不抢屏幕）。

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
    print("[1] config OK:", {k: cfg[k] for k in ("hotkey", "auto_copy_clipboard")})

    # 2) 热键解析与冲突探测
    assert hk.parse("alt+f1") == (0x0001, 0x70)
    assert hk.to_pynput_combo("f1") == "<f1>"
    taken = hk.is_hotkey_taken("f1")
    print(f"[2] hotkey parse OK; F1 taken={taken}")

    # 3) Qt 离屏应用
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)

    screen = QGuiApplication.primaryScreen()
    geo = screen.geometry()
    dpr = screen.devicePixelRatio()
    print(f"[3] screen {geo.width()}x{geo.height()} logical, dpr={dpr}, "
          f"screens={len(QGuiApplication.screens())}")

    # 4) 捕获 -> 裁剪 -> 保存
    shot = ov.grab_screen(screen)
    full = shot.crop_logical_rect(geo)
    expect_w, expect_h = round(geo.width() * dpr), round(geo.height() * dpr)
    assert (full.width(), full.height()) == (expect_w, expect_h), \
        f"crop size {full.width()}x{full.height()} != {expect_w}x{expect_h}"
    tmp_dir = os.path.join(tempfile.gettempdir(), "snapask_smoke")
    saved_path = storage.save_pixmap(full, tmp_dir)
    assert os.path.getsize(saved_path) > 1024, "saved png too small"
    print(f"[4] capture+crop+save OK: {expect_w}x{expect_h}px, "
          f"{os.path.getsize(saved_path)} bytes -> {os.path.basename(saved_path)}")

    # 5) 部分选区裁剪（左上角 200x150 逻辑像素）
    part = shot.crop_logical_rect(type(geo)(0, 0, 200, 150))
    assert (part.width(), part.height()) == (
        round(200 * dpr), round(150 * dpr))
    print(f"[5] partial crop OK: {part.width()}x{part.height()}px")

    # 6) 工具栏构造（离屏，不抢焦点）
    bar = tb.MiniToolbar(type(geo)(100, 100, 300, 200), geo)
    assert bar.btn_ask.isEnabled() is False  # M2 占位
    print("[6] toolbar OK (ask button disabled placeholder)")

    # 7) 剪贴板（离屏平台可能不支持，失败不阻塞）
    try:
        storage.copy_to_clipboard(part)
        print("[7] clipboard OK")
    except Exception as e:  # noqa: BLE001
        print(f"[7] clipboard skipped on offscreen: {type(e).__name__}")

    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
