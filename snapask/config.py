"""配置管理：读写本地 config.json，所有设置持久化。"""
import json
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULTS = {
    "hotkey": "f1",                                   # 截图热键，如 f1 / alt+s
    "save_dir": os.path.join(os.path.expanduser("~"), "Pictures", "SnapAsk"),
    "auto_copy_clipboard": True,                      # 截图后自动复制到剪贴板（保留旧习惯）
    "model": "qwen-vl-plus",                          # 默认模型（M2 启用）
    "api_key": "",                                    # 千问 API Key，留空待用户填入（M2 启用）
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return cfg


def save(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
