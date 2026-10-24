"""全局热键：pynput 低阶钩子注册 + Win32 RegisterHotKey 冲突探测。

冲突探测原理：Windows 不公开"谁占用了热键"的查询接口，但可用
RegisterHotKey 抢占式试探——注册成功说明空闲（随即释放），注册失败
（返回 0）说明该组合键已被其他程序占用。探测与正式监听相互独立：
pynput 走键盘钩子，不依赖 RegisterHotKey，因此即使探测到占用，
本程序仍可监听，只是会同时触发两个程序，故需提示用户改绑。
"""
import ctypes
from pynput import keyboard

# F1~F12 -> VK 0x70~0x7B
_VK_F = {f"f{i}": 0x6F + i for i in range(1, 13)}
_MOD_FLAGS = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "win": 0x0008}


def parse(hotkey: str):
    """'alt+f1' -> (mod_flags, vk)；解析失败返回 (None, None)。"""
    parts = [p.strip().lower() for p in hotkey.replace("+", " ").split() if p.strip()]
    if not parts:
        return None, None
    mod = 0
    for p in parts[:-1]:
        mod |= _MOD_FLAGS.get(p, 0)
    key = parts[-1]
    if key in _VK_F:
        return mod, _VK_F[key]
    if len(key) == 1 and key.isalnum():
        return mod, ord(key.upper())
    return None, None


def to_pynput_combo(hotkey: str) -> str:
    """'alt+f1' -> '<alt+f1>'（pynput GlobalHotKeys 格式）。"""
    parts = [p.strip().lower() for p in hotkey.replace("+", " ").split() if p.strip()]
    return "<" + "+".join(parts) + ">"


def is_hotkey_taken(hotkey: str) -> bool:
    """探测热键是否被其他程序占用（仅 Windows）。"""
    mod, vk = parse(hotkey)
    if vk is None:
        return False
    user32 = ctypes.windll.user32
    if user32.RegisterHotKey(None, 0, mod, vk):
        user32.UnregisterHotKey(None, 0)
        return False
    return True


def start_listener(hotkey: str, callback):
    """启动全局热键监听（守护线程），返回 listener 供停止。"""
    combo = to_pynput_combo(hotkey)
    listener = keyboard.GlobalHotKeys({combo: callback})
    listener.daemon = True
    listener.start()
    return listener
