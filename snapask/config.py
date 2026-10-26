"""配置管理：读写本地 config.json（非密钥设置持久化）。

注意：API Key 不存放在这里！密钥只放项目根目录的 .env（已被 .gitignore 排除），
由 qa_agent.load_api_key() 读取。config.json 本身也被 .gitignore 排除。
"""
import json
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULTS = {
    "hotkey": "f1",                                   # 截图热键，如 f1 / alt+s
    "save_dir": os.path.join(os.path.expanduser("~"), "Pictures", "SnapAsk"),
    "auto_copy_clipboard": True,                      # 截图后自动复制到剪贴板
    "model": "deepseek-v4.1-flash",                   # 默认模型（见 qa_agent.MODELS）
    "api_base": "https://cn-beijing.maas.qianwenaiapi.com/api/v1",  # LLM 端点
    "custom_models": [],                              # 用户自定义的模型 ID
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
