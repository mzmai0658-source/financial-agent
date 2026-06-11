import json
import os
import threading
import time
from typing import Any, Dict, Iterator, List, Optional

import requests
from requests.adapters import HTTPAdapter
from loguru import logger


class LLMClient:
    """
    LLM 客户端，支持 DeepSeek / OpenAI 兼容接口。

    能力：
      - chat:            普通一次性对话
      - chat_json:       JSON 结构化输出（json_object 模式 + 解析重试）
      - chat_with_tools: function calling，返回完整 assistant message（含 tool_calls）
      - chat_stream:     流式输出，逐段 yield 内容增量

    环境变量：
      DEEPSEEK_API_KEY  → API Key
      DEEPSEEK_BASE_URL → Base URL（默认 https://api.deepseek.com）
      DEEPSEEK_MODEL    → 模型名（默认 deepseek-chat）
    """

    def __init__(
        self,
        api_url: str = None,
        api_key: str = None,
        model: str = None,
    ):
        base_url = api_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self.api_url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        self.timeout = int(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "120"))
        self.max_retries = int(os.getenv("DEEPSEEK_MAX_RETRIES", "3"))
        self.retry_backoff_seconds = float(os.getenv("DEEPSEEK_RETRY_BACKOFF_SECONDS", "2"))

        if not self.api_key:
            logger.warning("DEEPSEEK_API_KEY 未设置，LLM 调用将失败")

        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        # requests.Session 非线程安全，且重试时的 reset 不能影响其他并发请求，
        # 因此按线程各自持有一个 Session。
        self._local = threading.local()

    @staticmethod
    def _build_session() -> requests.Session:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def _get_session(self) -> requests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = self._build_session()
            self._local.session = session
        return session

    def _reset_session(self) -> None:
        session = getattr(self._local, "session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        self._local.session = None

    # ── 核心请求（带重试）──────────────────────────────────────────────────────

    def _request_completion(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """非流式请求，返回完整 response json；不可恢复错误返回 None。"""
        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._get_session().post(
                    self.api_url, json=payload, headers=self.headers, timeout=self.timeout,
                )
                if response.status_code == 401:
                    logger.error("LLM API 401 Unauthorized: 请检查 DEEPSEEK_API_KEY")
                    return None
                if response.status_code == 404:
                    logger.error(f"LLM API 404: {self.api_url}")
                    return None
                if response.status_code in {408, 429, 500, 502, 503, 504}:
                    last_error = f"HTTP {response.status_code}"
                    if attempt < self.max_retries:
                        logger.warning(f"LLM API 临时异常，第 {attempt}/{self.max_retries} 次重试: {last_error}")
                        self._reset_session()
                        time.sleep(self.retry_backoff_seconds * attempt)
                        continue
                response.raise_for_status()
                return response.json()
            except requests.exceptions.Timeout:
                last_error = f"Timeout ({self.timeout}s)"
            except requests.exceptions.ConnectionError:
                last_error = f"connection failed: {self.api_url}"
            except requests.exceptions.RequestException as e:
                last_error = str(e)
            except Exception as e:
                last_error = str(e)

            if attempt < self.max_retries:
                logger.warning(f"LLM 调用失败，第 {attempt}/{self.max_retries} 次重试: {last_error}")
                self._reset_session()
                time.sleep(self.retry_backoff_seconds * attempt)

        logger.error(f"LLM Call Error after {self.max_retries} attempts: {last_error}")
        return None

    @staticmethod
    def _extract_message(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not result:
            return None
        choices = result.get("choices") or []
        if choices and isinstance(choices[0], dict):
            return choices[0].get("message")
        err = result.get("error")
        if err:
            msg = err.get("message", "") if isinstance(err, dict) else str(err)
            logger.error(f"LLM API Error: {msg}")
        return None

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.1) -> Optional[str]:
        payload = {
            "messages": messages,
            "model": self.model,
            "temperature": temperature,
            "stream": False,
        }
        message = self._extract_message(self._request_completion(payload))
        return message.get("content") if message else None

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.0,
        max_parse_retries: int = 2,
    ) -> Optional[Dict[str, Any]]:
        """JSON 结构化输出。要求 messages 提示词中明确说明返回 JSON。"""
        payload = {
            "messages": messages,
            "model": self.model,
            "temperature": temperature,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        for attempt in range(max_parse_retries + 1):
            message = self._extract_message(self._request_completion(payload))
            content = (message or {}).get("content") or ""
            if not content:
                return None
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                # 容错：剥掉可能的 markdown 代码块包裹
                stripped = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    pass
            if attempt < max_parse_retries:
                logger.warning(f"LLM JSON 解析失败，重试 {attempt + 1}/{max_parse_retries}")
        logger.error("LLM JSON 输出多次解析失败")
        return None

    def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        temperature: float = 0.1,
        tool_choice: str = "auto",
    ) -> Optional[Dict[str, Any]]:
        """function calling，返回 assistant message dict（含 content / tool_calls）。"""
        payload = {
            "messages": messages,
            "model": self.model,
            "temperature": temperature,
            "stream": False,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        return self._extract_message(self._request_completion(payload))

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        temperature: float = 0.3,
    ) -> Iterator[str]:
        """流式输出，逐段 yield 内容增量。连接失败时重试，首 token 后不再重试。"""
        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            started = False
            try:
                response = self._get_session().post(
                    self.api_url,
                    json={
                        "messages": messages,
                        "model": self.model,
                        "temperature": temperature,
                        "stream": True,
                    },
                    headers=self.headers,
                    timeout=self.timeout,
                    stream=True,
                )
                if response.status_code != 200:
                    last_error = f"HTTP {response.status_code}"
                    if response.status_code in {401, 404}:
                        logger.error(f"LLM 流式调用失败: {last_error}")
                        return
                    raise requests.exceptions.RequestException(last_error)

                for raw_line in response.iter_lines(decode_unicode=True):
                    if not raw_line or not raw_line.startswith("data:"):
                        continue
                    data = raw_line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        started = True
                        yield content
                return
            except requests.exceptions.RequestException as e:
                last_error = str(e) or last_error
            except Exception as e:
                last_error = str(e)

            if started:
                # 已经下发部分内容，中途断流不可重放，直接结束
                logger.error(f"LLM 流式输出中断: {last_error}")
                return
            if attempt < self.max_retries:
                logger.warning(f"LLM 流式连接失败，第 {attempt}/{self.max_retries} 次重试: {last_error}")
                self._reset_session()
                time.sleep(self.retry_backoff_seconds * attempt)

        logger.error(f"LLM 流式调用失败 after {self.max_retries} attempts: {last_error}")
