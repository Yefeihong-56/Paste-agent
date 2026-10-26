"""千问/DeepSeek LLM 客户端（原生 dashscope SDK + MultiModalConversation）。

- 端点与模型：默认 https://cn-beijing.maas.qianwenaiapi.com/api/v1 ，
  模型 deepseek-v4.1-flash（多模态，支持图片 data URL、流式、多轮带图上下文）；
  端点可用 config.json 的 api_base 覆盖，模型可在面板切换/自定义。
- API Key 只从项目根目录的 .env 读取（DASHSCOPE_API_KEY），
  .env 已在 .gitignore 中排除，绝不入库；模板见 .env.example。
"""
import base64
import io
import os
import shutil

import PIL.Image
import dashscope
from dashscope import MultiModalConversation
from dotenv import load_dotenv

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")

MAX_IMAGE_EDGE = 1568        # 图片长边上限（降 token 与延迟）
MAX_HISTORY_TURNS = 4        # 多轮追问保留的最大轮数
NO_KEY_MSG = "NO_KEY"        # 未配置 Key 的错误标识
API_BASE = "https://cn-beijing.maas.qianwenaiapi.com/api/v1"

SYSTEM_PROMPT = "你是截图问答助手，基于用户提供的截图回答问题，回答简洁、直接、可操作。"

# 模型注册表（方案 B：每个模型一行说明，展示在下拉面板里）
MODELS = [
    {"id": "deepseek-v4.1-flash", "desc": "多模态 · 快速响应（默认）", "vision": True},
]
CUSTOM = "自定义模型…"
DEFAULT_MODEL = "deepseek-v4.1-flash"


def model_vision(model_id: str) -> bool:
    """模型是否支持图片（自定义模型默认按支持处理）。"""
    for m in MODELS:
        if m["id"] == model_id:
            return m["vision"]
    return True


def ensure_env_file() -> str:
    """确保 .env 存在（不存在则从 .env.example 复制模板），返回路径。"""
    if not os.path.exists(ENV_PATH):
        example = ENV_PATH + ".example"
        if os.path.exists(example):
            shutil.copyfile(example, ENV_PATH)
        else:
            with open(ENV_PATH, "w", encoding="utf-8") as f:
                f.write("DASHSCOPE_API_KEY=\n")
    return ENV_PATH


def load_api_key() -> str:
    """从 .env 读取 API Key（不覆盖已存在的环境变量）。"""
    load_dotenv(ENV_PATH)
    return os.environ.get("DASHSCOPE_API_KEY", "").strip()


def compress_image(pil: PIL.Image.Image) -> PIL.Image.Image:
    """长边压缩到 MAX_IMAGE_EDGE 以内，降低 token 与传输耗时。"""
    w, h = pil.size
    scale = min(1.0, MAX_IMAGE_EDGE / max(w, h))
    if scale < 1.0:
        pil = pil.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                         PIL.Image.LANCZOS)
    return pil


def image_to_data_url(pil: PIL.Image.Image) -> str:
    """PIL 图像 -> PNG base64 data URL（MultiModalConversation 的 image 字段）。"""
    pil = compress_image(pil)
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return "data:image/png;base64," + \
        base64.b64encode(buf.getvalue()).decode()


def build_messages(question: str, history: list, vision: bool,
                   image_data_url: str | None = None) -> list:
    """组装多轮消息（原生 dashscope 格式：content 为部件列表）。

    - 首轮（history 为空）携带截图：{"image": data URL}；
    - 后续轮只发文字，截图仍在历史上下文中（已实测多轮带图有效）；
    - 文本模型（vision=False）时，历史中的图片替换为 [截图] 占位，
      避免文本模型收到无法解析的图片内容而报错。
    """
    msgs = [{"role": "system", "content": [{"text": SYSTEM_PROMPT}]}]
    for m in history[-MAX_HISTORY_TURNS * 2:]:
        content = m["content"]
        if not vision and isinstance(content, list):
            content = [{"text": "[截图]"}]
        msgs.append({"role": m["role"], "content": content})
    if history:
        msgs.append({"role": "user", "content": [{"text": question}]})
    else:
        parts = []
        if image_data_url:
            parts.append({"image": image_data_url})
        parts.append({"text": question})
        msgs.append({"role": "user", "content": parts})
    return msgs


def _content_to_text(content) -> str:
    """message.content 可能是 str 或 [{'text': ...}] 列表，统一转 str。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content if isinstance(p, dict))


def classify_error(msg: str) -> str:
    """把异常信息翻译成用户可操作的提示。"""
    m = (msg or "").lower()
    if "401" in m or "invalid api key" in m or "incorrect api key" in m:
        return "API Key 无效，请检查 .env 中的 DASHSCOPE_API_KEY"
    if "404" in m or "model_not_exist" in m or "model not exist" in m:
        return ("模型不存在：请检查面板顶部所选模型是否适用于当前端点"
                "（默认 deepseek-v4.1-flash），或切换到其他模型")
    if "403" in m or "access denied" in m or "access_denied" in m:
        return "没有该模型的访问权限：请更换模型或联系服务提供方"
    if "429" in m or "rate limit" in m or "throttl" in m or "limit_requests" in m:
        return "请求被限流（429）：额度可能未领取或已用尽，请稍后重试"
    if "url error" in m:
        return "图片格式不被接受：请换一张截图重试"
    if "timeout" in m or "timed out" in m:
        return "请求超时，请检查网络后重试"
    if "connection" in m:
        return "网络连接失败，请检查网络后重试"
    return f"请求失败：{msg}"


def stream_chat(model: str, messages: list, on_delta, on_finish, on_error,
                should_stop=None, api_base: str | None = None):
    """流式对话（在调用方线程中执行，不要放 Qt 主线程）。

    on_delta(str)        收到增量文本
    on_finish(bool)      结束，参数为是否被用户停止
    on_error(str)        失败，参数为分类后的错误信息
    should_stop() -> bool  可选，返回 True 时中断流
    """
    import config

    key = load_api_key()
    if not key:
        on_error(NO_KEY_MSG)
        return
    dashscope.base_http_api_url = api_base or \
        config.load().get("api_base", API_BASE)
    try:
        responses = MultiModalConversation.call(
            api_key=key, model=model, messages=messages, stream=True)
        for resp in responses:
            if should_stop is not None and should_stop():
                on_finish(True)
                return
            if resp.status_code != 200:
                on_error(classify_error(f"{resp.code} {resp.message}"))
                return
            choices = getattr(resp.output, "choices", None) or []
            if choices:
                text = _content_to_text(
                    getattr(choices[0].message, "content", None))
                if text:
                    on_delta(text)
        on_finish(False)
    except Exception as e:  # noqa: BLE001
        on_error(classify_error(str(e)))
