# -*- coding: utf-8 -*-
"""providers.py - AI Provider 接口抽象

统一 chat 语义: chat(messages, tools, stream, max_tokens) -> (reply, tool_calls, usage)
- OpenAIProvider: OpenAI 兼容 REST (/chat/completions, Bearer token, tools 原生支持)
- OllamaProvider: 本地 Ollama (/api/chat, 无 API key, tools 原生支持)
新增供应商: 继承 Provider 实现 chat() 即可, AIClient 零改动。
"""
import json
import ssl
import time
import urllib.request
import urllib.error

from utils import log

_SSL_CTX = ssl.create_default_context()

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


class Provider:
    """Provider 基类: 约定统一返回 (reply_text, tool_calls_or_None, usage_dict)。"""

    name = "base"

    def __init__(self, api_url="", api_key="", model="", max_retries=3):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries

    def chat(self, messages, tools=None, stream=False, max_tokens=800):
        raise NotImplementedError

    def chat_stream(self, messages, max_tokens=800):
        raise NotImplementedError

    # ── 工具调用循环（供应商无关）──
    def tool_loop(self, messages, tool_funcs, tools_def, max_rounds=3):
        for _ in range(max_rounds):
            reply, tool_calls, usage = self.chat(messages, tools=tools_def, max_tokens=800)
            if not tool_calls:
                return reply, usage
            messages.append({"role": "assistant", "content": reply or "",
                             "tool_calls": tool_calls})
            for tc in tool_calls:
                fname = tc["function"]["name"]
                try:
                    fargs = json.loads(tc["function"]["arguments"])
                except Exception:
                    fargs = {}
                func = tool_funcs.get(fname)
                result = func(fargs) if func else f"unknown: {fname}"
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
        reply, _, usage = self.chat(messages, max_tokens=800)
        return reply, usage

    @staticmethod
    def _read_error(e):
        try:
            if hasattr(e, 'read'):
                return e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return str(e)


class OpenAIProvider(Provider):
    """OpenAI / DeepSeek / 兼容 /chat/completions 端点。"""

    name = "openai"

    def chat(self, messages, tools=None, stream=False, max_tokens=800):
        data = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.8,
            "presence_penalty": 0.15,
            "frequency_penalty": 0.1,
            "stream": stream,
        }
        if tools:
            data["tools"] = tools

        body = json.dumps(data).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(self.api_url, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                msg = resp["choices"][0]["message"]
                usage = resp.get("usage", {})
                total = usage.get("total_tokens", 0)
                cost = total * 0.5 / 1_000_000
                log(f"AI ({total} tokens, {cost:.6f} yuan)")
                return msg["content"].strip(), msg.get("tool_calls"), usage
            except Exception as e:
                if attempt < self.max_retries - 1:
                    log(f"AI retry {attempt+1}/{self.max_retries}: {self._read_error(e)}")
                    time.sleep(3)
        return None, None, {}

    def chat_stream(self, messages, max_tokens=800):
        if not HAS_REQUESTS:
            return self.chat(messages, max_tokens=max_tokens)[0]
        full = ""
        try:
            r = requests.post(
                self.api_url,
                json={
                    "model": self.model, "messages": messages,
                    "max_tokens": max_tokens, "temperature": 0.7, "stream": True,
                },
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.api_key}"},
                stream=True, timeout=60,
            )
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    content = chunk.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if content:
                        full += content
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            log(f"stream failed: {e}, fallback")
            return self.chat(messages, max_tokens=max_tokens)[0]
        return full


class OllamaProvider(Provider):
    """本地 Ollama /api/chat：无 API key，响应体为 {"message": {"content": ...}}。

    stream=True 时逐行 JSON（非 SSE 前缀），delta 在 chunk["message"]["content"]。
    tools 原生支持（Ollama ≥0.4），返回 message["tool_calls"] = [{"function": {...}}]。
    """

    name = "ollama"

    def chat(self, messages, tools=None, stream=False, max_tokens=800):
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {
                "temperature": 0.8,
                "num_predict": max_tokens,
            },
        }
        if tools:
            payload["tools"] = tools

        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        url = self.api_url.rstrip("/") + "/api/chat"

        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(url, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=120, context=_SSL_CTX) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                msg = resp.get("message") or {}
                content = (msg.get("content") or "").strip()
                tool_calls = msg.get("tool_calls")
                # Ollama tool_calls: [{"function": {"name":..., "arguments": {...}}}]
                if tool_calls:
                    normalized = []
                    for tc in tool_calls:
                        fn = tc.get("function") or {}
                        args = fn.get("arguments")
                        if isinstance(args, dict):
                            args = json.dumps(args, ensure_ascii=False)
                        normalized.append({"id": tc.get("id") or f"call_{attempt}", "function": {
                            "name": fn.get("name", ""),
                            "arguments": args or "{}",
                        }})
                    tool_calls = normalized
                usage = resp.get("eval_count") or {}
                return content, tool_calls, {"total_tokens": usage}
            except Exception as e:
                if attempt < self.max_retries - 1:
                    log(f"Ollama retry {attempt+1}/{self.max_retries}: {self._read_error(e)}")
                    time.sleep(3)
        return None, None, {}

    def chat_stream(self, messages, max_tokens=800):
        if not HAS_REQUESTS:
            return self.chat(messages, max_tokens=max_tokens)[0]
        full = ""
        try:
            r = requests.post(
                self.api_url.rstrip("/") + "/api/chat",
                json={
                    "model": self.model, "messages": messages,
                    "stream": True, "options": {"temperature": 0.7, "num_predict": max_tokens},
                },
                headers={"Content-Type": "application/json"},
                stream=True, timeout=120,
            )
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    content = (chunk.get("message") or {}).get("content", "")
                    if content:
                        full += content
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            log(f"Ollama stream failed: {e}, fallback")
            return self.chat(messages, max_tokens=max_tokens)[0]
        return full


# ── 供应商路由 ──
_PROVIDER_REGISTRY = {
    "openai": OpenAIProvider,
    "ollama": OllamaProvider,
    "deepseek": OpenAIProvider,  # DeepSeek 走 OpenAI 兼容
    "custom": OpenAIProvider,
    # 国产官方（全部 OpenAI 兼容，统一走 OpenAIProvider）
    "aliyun": OpenAIProvider,
    "zhipu": OpenAIProvider,
    "kimi": OpenAIProvider,
    "step": OpenAIProvider,
    "minimax": OpenAIProvider,
    "volcengine": OpenAIProvider,
    "qianfan": OpenAIProvider,
    "hunyuan": OpenAIProvider,
    "siliconflow": OpenAIProvider,
    "openrouter": OpenAIProvider,
}


def make_provider(api_url="", api_key="", model="", provider="openai", max_retries=3):
    """按 provider 名称构造适配器；未知 provider 回退 OpenAI 兼容。"""
    cls = _PROVIDER_REGISTRY.get((provider or "openai").lower(), OpenAIProvider)
    return cls(api_url=api_url, api_key=api_key, model=model, max_retries=max_retries)