"""与本地 Ollama 服务交互的轻量客户端（仅用 Python 标准库，无第三方依赖）。

Ollama 默认监听 http://localhost:11434，主要接口：
  GET  /api/tags      列出已安装模型
  POST /api/chat      对话补全（messages 形式）
  POST /api/generate  文本生成（prompt 形式）

用法：
  python ollama_client.py list                   列出本地模型
  python ollama_client.py ask "问题" [模型]      单次非流式问答
  python ollama_client.py chat "问题" [模型]     流式问答
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "qwen3.5:4b"


class OllamaError(Exception):
    """Ollama 交互错误。"""


def _request(method: str, path: str, payload: dict | None = None, timeout: int = 300) -> dict:
    """发送 HTTP 请求并解析 JSON 响应。"""
    url = f"{DEFAULT_HOST}{path}"
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise OllamaError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise OllamaError(
            f"无法连接 Ollama 服务（{DEFAULT_HOST}）。请确认 Ollama 已启动：{e.reason}"
        ) from e

    if not body:
        return {}
    return json.loads(body)


def list_models() -> list[dict]:
    """列出本地已安装的模型。"""
    data = _request("GET", "/api/tags", timeout=10)
    return data.get("models", [])


def chat(model: str, messages: list[dict], *, options: dict | None = None, timeout: int = 300) -> dict:
    """非流式对话，返回完整响应 dict。

    messages 形如 [{"role": "system", "content": "..."},
                   {"role": "user", "content": "..."}]
    """
    payload: dict = {"model": model, "messages": messages, "stream": False}
    if options:
        payload["options"] = options
    return _request("POST", "/api/chat", payload, timeout=timeout)


def generate(model: str, prompt: str, *, options: dict | None = None, timeout: int = 300) -> dict:
    """非流式文本生成，返回完整响应 dict。"""
    payload: dict = {"model": model, "prompt": prompt, "stream": False}
    if options:
        payload["options"] = options
    return _request("POST", "/api/generate", payload, timeout=timeout)


def chat_stream(model: str, messages: list[dict], *, options: dict | None = None, timeout: int = 300):
    """流式对话，逐段 yield 增量内容文本。"""
    payload: dict = {"model": model, "messages": messages, "stream": True}
    if options:
        payload["options"] = options

    url = f"{DEFAULT_HOST}/api/chat"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                obj = json.loads(line)
                if obj.get("done"):
                    break
                piece = obj.get("message", {}).get("content", "")
                if piece:
                    yield piece
    except urllib.error.HTTPError as e:
        raise OllamaError(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}") from e
    except urllib.error.URLError as e:
        raise OllamaError(f"无法连接 Ollama 服务（{DEFAULT_HOST}）：{e.reason}") from e


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------


def _print_models() -> None:
    models = list_models()
    if not models:
        print("未找到任何模型。")
        return
    print(f"共 {len(models)} 个模型：")
    for m in models:
        details = m.get("details", {})
        size_gb = m.get("size", 0) / 1e9
        print(
            f"  - {m['name']}  "
            f"({details.get('parameter_size', '?')}, "
            f"{details.get('quantization_level', '?')}, {size_gb:.1f} GB)"
        )


def _ask(question: str, model: str) -> None:
    resp = chat(model, [{"role": "user", "content": question}])
    content = resp.get("message", {}).get("content", "")
    print(content)


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    cmd = argv[0]
    if cmd == "list":
        _print_models()
        return 0

    if cmd in ("ask", "chat"):
        if len(argv) < 2:
            print("缺少问题内容。", file=sys.stderr)
            return 2
        question = argv[1]
        model = argv[2] if len(argv) > 2 else DEFAULT_MODEL
        if cmd == "ask":
            _ask(question, model)
        else:
            for piece in chat_stream(model, [{"role": "user", "content": question}]):
                print(piece, end="", flush=True)
            print()
        return 0

    print(f"未知命令: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except OllamaError as e:
        print(f"错误：{e}", file=sys.stderr)
        raise SystemExit(1)
