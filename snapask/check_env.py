"""环境自检：验证 .env 中的 API Key 与模型可用性（文本/图片/流式）。

运行：python check_env.py
"""
import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import qa_agent
import dashscope
from dashscope import MultiModalConversation
from PIL import Image


def _probe(client_model, content, max_tokens=10):
    try:
        r = MultiModalConversation.call(
            api_key=qa_agent.load_api_key(),
            model=client_model,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
        )
        if r.status_code == 200:
            return True, qa_agent._content_to_text(
                r.output.choices[0].message.content)[:60]
        return False, f"{r.code} {str(r.message)[:60]}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:60]


def main():
    cfg = config.load()
    api_base = cfg.get("api_base", qa_agent.API_BASE)
    dashscope.base_http_api_url = api_base
    print("ENV 路径:", qa_agent.ENV_PATH)
    print("端点:", api_base)
    key = qa_agent.load_api_key()
    if not key:
        print("未读到 DASHSCOPE_API_KEY —— 请在 .env 中填写后重试")
        return
    print(f"Key 已读取（长度 {len(key)}，前缀 {key[:6]}...）\n")

    model = cfg.get("model", qa_agent.DEFAULT_MODEL)
    print(f"== 当前默认模型：{model} ==")
    ok, info = _probe(model, [{"text": "你好，一句话介绍你自己"}])
    print(f"  文本: {'可用' if ok else '不可用'} {info}")
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 30, 30)).save(buf, "PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    ok, info = _probe(model, [{"image": data_url},
                              {"text": "这张图是什么颜色？只回答颜色"}])
    print(f"  图片: {'可用' if ok else '不可用'} {info}")

    print("\n== 流式 ==")
    try:
        parts = []
        for resp in MultiModalConversation.call(
                api_key=key, model=model,
                messages=[{"role": "user", "content": [{"text": "数数 1 到 3"}]}],
                stream=True):
            if resp.status_code != 200:
                print("  流式错误:", resp.code, str(resp.message)[:60])
                break
            parts.append(qa_agent._content_to_text(
                resp.output.choices[0].message.content))
        else:
            print("  流式可用，拼接:", "".join(parts)[:60])
    except Exception as e:  # noqa: BLE001
        print("  流式异常:", str(e)[:60])

    print("\n== 其他已注册模型 ==")
    for m in qa_agent.MODELS:
        if m["id"] == model:
            continue
        ok, info = _probe(m["id"], [{"text": "hi"}], max_tokens=1)
        print(f"  {'[可用]' if ok else '[不可用]'} {m['id']} {info if ok else ''}")


if __name__ == "__main__":
    main()
