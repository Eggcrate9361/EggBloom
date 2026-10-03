# -*- coding: utf-8 -*-
"""providers.py - AI Provider 接口抽象

统一 chat 语义: chat(messages, tools, stream, max_tokens) -> (reply, tool_calls, usage)
- OpenAIProvider: OpenAI 兼容 REST (/chat/completions, Bearer token, tools 原生支持)
- OllamaProvider: 本地 Ollama (/api/chat, 无 API key, tools 原生支持)
- AnthropicProvider: Claude Messages API (x-api-key + anthropic-version, tools 原生支持)
- GoogleProvider: Gemini generateContent API (?key=, functionDeclarations 工具)
新增供应商: 继承 Provider 实现 chat() 即可, AIClient 零改动。
"""
import json
import ssl
import time
import urllib.parse
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

    def __init__(self, api_url="", api_key="", model="", max_retries=3, temperature=None,
                 extra_body=None):
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries
        self.temperature = temperature  # None=使用适配器默认值；由 AIClient 透传 ai_temperature
        # 可选：调用方额外要带进请求体的字段（例如 {"thinking": {"type": "disabled"}}
        # 用来关掉推理型模型的思考过程）。默认 None = 完全不影响原有行为。
        self.extra_body = extra_body

    def _apply_extra(self, payload):
        """把 extra_body 合并进请求体。默认无操作。"""
        if self.extra_body:
            payload.update(self.extra_body)
        return payload

    def _temp(self, fallback):
        """解析温度：显式配置优先，否则适配器默认值。"""
        return self.temperature if self.temperature is not None else fallback

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
            "temperature": self._temp(0.8),
            "presence_penalty": 0.15,
            "frequency_penalty": 0.1,
            "stream": stream,
        }
        if tools:
            data["tools"] = tools
        self._apply_extra(data)

        body = json.dumps(data).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        last_err = None
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
                # content 可能是 None（纯工具调用响应）：先归一化再 strip，避免 AttributeError
                return (msg.get("content") or "").strip(), msg.get("tool_calls"), usage
            except Exception as e:
                last_err = e
                if attempt < self.max_retries - 1:
                    log(f"AI retry {attempt+1}/{self.max_retries}: {self._read_error(e)}")
                    time.sleep(3)
        log(f"AI 请求最终失败: {self._read_error(last_err) if last_err else 'unknown'}")
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
                "temperature": self._temp(0.8),
                "num_predict": max_tokens,
            },
        }
        if tools:
            payload["tools"] = tools
        self._apply_extra(payload)

        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        url = self.api_url.rstrip("/") + "/api/chat"

        last_err = None
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
                last_err = e
                if attempt < self.max_retries - 1:
                    log(f"Ollama retry {attempt+1}/{self.max_retries}: {self._read_error(e)}")
                    time.sleep(3)
        log(f"Ollama 请求最终失败: {self._read_error(last_err) if last_err else 'unknown'}")
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


class AnthropicProvider(Provider):
    """Anthropic Claude Messages API（/v1/messages）。

    鉴权：x-api-key + anthropic-version（非 Bearer）。
    tools：原生 tool_use / tool_result 块，与 OpenAI 格式相互转换。
    """

    name = "anthropic"

    @staticmethod
    def _split_system(messages):
        system_parts, rest = [], []
        for m in messages or []:
            if m.get("role") == "system":
                c = m.get("content")
                if isinstance(c, list):
                    c = "".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                if c:
                    system_parts.append(str(c))
            else:
                rest.append(m)
        return "\n\n".join(system_parts), rest

    @staticmethod
    def _content_to_text(content):
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    parts.append(str(c.get("text") or ""))
                # image_url 等非文本块：Anthropic 适配暂不转发图片
            return "".join(parts)
        return str(content)

    @staticmethod
    def _tools_to_anthropic(tools):
        out = []
        for t in tools or []:
            fn = t.get("function") if isinstance(t, dict) else None
            if not fn:
                continue
            out.append({
                "name": fn.get("name", ""),
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return out

    @staticmethod
    def _messages_to_anthropic(messages):
        out = []
        for m in messages or []:
            role = m.get("role")
            content = m.get("content")
            if role == "user":
                text = AnthropicProvider._content_to_text(content)
                if m.get("tool_calls"):
                    text = text or ""
                out.append({"role": "user", "content": text})
            elif role == "assistant":
                tcs = m.get("tool_calls")
                if tcs:
                    blocks = []
                    if content:
                        blocks.append({"type": "text", "text": str(content)})
                    for tc in tcs:
                        fn = tc.get("function") or {}
                        try:
                            inp = json.loads(fn.get("arguments") or "{}")
                        except Exception:
                            inp = {}
                        blocks.append({
                            "type": "tool_use",
                            "id": tc.get("id") or f"toolu_{abs(hash(fn.get('name','')))}",
                            "name": fn.get("name", ""),
                            "input": inp,
                        })
                    out.append({"role": "assistant", "content": blocks})
                else:
                    out.append({"role": "assistant", "content": str(content or "")})
            elif role == "tool":
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": m.get("tool_call_id") or "",
                     "content": str(content or "")}]})
            else:
                out.append({"role": role, "content": str(content or "")})
        # Anthropic 要求首条为 user
        if out and out[0]["role"] != "user":
            out.insert(0, {"role": "user", "content": "（继续）"})
        return out

    @staticmethod
    def _parse_resp(resp):
        text_parts, tool_calls = [], []
        for block in resp.get("content") or []:
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text") or "")
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.get("id") or "",
                    "function": {
                        "name": block.get("name") or "",
                        "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                    },
                })
        usage = resp.get("usage") or {}
        total = (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
        return "".join(text_parts), (tool_calls or None), {"total_tokens": total, **usage}

    def _endpoint(self):
        base = (self.api_url or "").rstrip("/")
        if base.endswith("/messages"):
            base = base[: -len("/messages")]
        if not base.endswith("/v1"):
            base += "/v1"
        return base + "/messages"

    def chat(self, messages, tools=None, stream=False, max_tokens=800):
        system_text, rest = self._split_system(messages)
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": self._temp(0.8),
            "messages": self._messages_to_anthropic(rest),
            "stream": stream,
        }
        if system_text:
            payload["system"] = system_text
        if tools:
            payload["tools"] = self._tools_to_anthropic(tools)
        self._apply_extra(payload)
        headers = {"Content-Type": "application/json",
                   "x-api-key": self.api_key or "",
                   "anthropic-version": "2023-06-01"}
        body = json.dumps(payload).encode("utf-8")
        last_err = None
        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(self._endpoint(), data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=120, context=_SSL_CTX) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                return self._parse_resp(resp)
            except Exception as e:
                last_err = e
                if attempt < self.max_retries - 1:
                    log(f"Claude retry {attempt+1}/{self.max_retries}: {self._read_error(e)}")
                    time.sleep(3)
        log(f"Claude 请求最终失败: {self._read_error(last_err) if last_err else 'unknown'}")
        return None, None, {}

    def chat_stream(self, messages, max_tokens=800):
        if not HAS_REQUESTS:
            return self.chat(messages, max_tokens=max_tokens)[0]
        system_text, rest = self._split_system(messages)
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": 0.7,
            "messages": self._messages_to_anthropic(rest),
            "stream": True,
        }
        if system_text:
            payload["system"] = system_text
        full = ""
        try:
            r = requests.post(
                self._endpoint(),
                json=payload,
                headers={"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01"},
                stream=True, timeout=120,
            )
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            full += delta.get("text") or ""
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            log(f"Claude stream failed: {e}, fallback")
            return self.chat(messages, max_tokens=max_tokens)[0]
        return full


class GoogleProvider(Provider):
    """Google Gemini generateContent API（?key= 查询参数鉴权）。

    tools：functionDeclarations / functionCall / functionResponse，与 OpenAI 格式互转。
    """

    name = "google"

    @staticmethod
    def _split_system(messages):
        return AnthropicProvider._split_system(messages)

    @staticmethod
    def _content_to_text(content):
        return AnthropicProvider._content_to_text(content)

    @staticmethod
    def _tools_to_gemini(tools):
        fds = []
        for t in tools or []:
            fn = t.get("function") if isinstance(t, dict) else None
            if not fn:
                continue
            fds.append({
                "name": fn.get("name", ""),
                "description": fn.get("description") or "",
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return [{"functionDeclarations": fds}] if fds else None

    @staticmethod
    def _messages_to_gemini(messages):
        out = []
        for m in messages or []:
            role = m.get("role")
            content = m.get("content")
            if role == "user":
                parts = []
                if m.get("tool_calls"):
                    parts = []
                txt = GoogleProvider._content_to_text(content)
                if txt:
                    parts.append({"text": txt})
                out.append({"role": "user", "parts": parts})
            elif role == "assistant":
                parts = []
                if content:
                    parts.append({"text": str(content)})
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    parts.append({"functionCall": {"name": fn.get("name", ""), "args": args}})
                out.append({"role": "model", "parts": parts})
            elif role == "tool":
                out.append({"role": "user", "parts": [
                    {"functionResponse": {"name": m.get("tool_call_id") or "",
                                          "response": {"result": str(content or "")}}}]})
            else:
                grole = "user" if role != "assistant" else "model"
                out.append({"role": grole, "parts": [{"text": str(content or "")}]})
        return out

    @staticmethod
    def _parse_resp(resp):
        text_parts, tool_calls = [], []
        for cand in resp.get("candidates") or []:
            content = cand.get("content") or {}
            for part in content.get("parts") or []:
                if part.get("text"):
                    text_parts.append(part["text"])
                fc = part.get("functionCall")
                if fc:
                    tool_calls.append({
                        "id": fc.get("name") or "",
                        "function": {
                            "name": fc.get("name") or "",
                            "arguments": json.dumps(fc.get("args") or {}, ensure_ascii=False),
                        },
                    })
        usage = resp.get("usageMetadata") or {}
        return "".join(text_parts), (tool_calls or None), {"total_tokens": usage.get("totalTokenCount") or 0, **usage}

    def _endpoint(self):
        base = (self.api_url or "").rstrip("/")
        if "/models/" in base:
            base = base.split("/models/")[0]
        model = (self.model or "").strip().split("/")[-1]
        sep = "&" if "?" in base else "?"
        return f"{base}/models/{urllib.parse.quote(model)}:generateContent{sep}key={urllib.parse.quote(self.api_key or '')}"

    def chat(self, messages, tools=None, stream=False, max_tokens=800):
        system_text, rest = self._split_system(messages)
        payload = {
            "contents": self._messages_to_gemini(rest),
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": self._temp(0.8)},
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        if tools:
            gtools = self._tools_to_gemini(tools)
            if gtools:
                payload["tools"] = gtools
        self._apply_extra(payload)
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        last_err = None
        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(self._endpoint(), data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=120, context=_SSL_CTX) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                return self._parse_resp(resp)
            except Exception as e:
                last_err = e
                if attempt < self.max_retries - 1:
                    log(f"Gemini retry {attempt+1}/{self.max_retries}: {self._read_error(e)}")
                    time.sleep(3)
        log(f"Gemini 请求最终失败: {self._read_error(last_err) if last_err else 'unknown'}")
        return None, None, {}

    def chat_stream(self, messages, max_tokens=800):
        if not HAS_REQUESTS:
            return self.chat(messages, max_tokens=max_tokens)[0]
        system_text, rest = self._split_system(messages)
        payload = {
            "contents": self._messages_to_gemini(rest),
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.7},
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        full = ""
        try:
            r = requests.post(self._endpoint(), json=payload, stream=True, timeout=120)
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    for cand in chunk.get("candidates") or []:
                        for part in (cand.get("content") or {}).get("parts") or []:
                            if part.get("text"):
                                full += part["text"]
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            log(f"Gemini stream failed: {e}, fallback")
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
    # 非 OpenAI 兼容协议：专属适配器
    "anthropic": AnthropicProvider,
    "claude": AnthropicProvider,
    "google": GoogleProvider,
    "gemini": GoogleProvider,
}


def make_provider(api_url="", api_key="", model="", provider="openai", max_retries=3,
                  temperature=None, extra_body=None):
    """按 provider 名称构造适配器；未知 provider 回退 OpenAI 兼容。"""
    cls = _PROVIDER_REGISTRY.get((provider or "openai").lower(), OpenAIProvider)
    return cls(api_url=api_url, api_key=api_key, model=model, max_retries=max_retries,
               temperature=temperature, extra_body=extra_body)