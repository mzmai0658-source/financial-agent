import os
import time
from http import HTTPStatus
from typing import List, Sequence, Any

class DashScopeEmbeddingFunction:
    def __init__(self, model_name: str = "text-embedding-v4", api_key: str = None):
        self.model_name = model_name
        self.api_key = api_key or self._load_key_from_code_file() or os.getenv("DASHSCOPE_API_KEY")
        self.max_retries = int(os.getenv("DASHSCOPE_MAX_RETRIES", "3"))
        self.retry_backoff_seconds = float(os.getenv("DASHSCOPE_RETRY_BACKOFF_SECONDS", "1.5"))
        self.dashscope = None
        try:
            import dashscope
            self.dashscope = dashscope
            if self.api_key:
                self.dashscope.api_key = self.api_key
        except Exception:
            self.dashscope = None

    def name(self) -> str:
        return f"dashscope:{self.model_name}"

    @staticmethod
    def _normalize_texts(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, (list, tuple)):
            normalized: List[str] = []
            for item in value:
                normalized.extend(DashScopeEmbeddingFunction._normalize_texts(item))
            return normalized
        text = str(value).strip()
        return [text] if text else []

    def __call__(self, input: Sequence[str]) -> List[List[float]]:
        texts = self._normalize_texts(input)
        if not texts:
            return []
        if self.dashscope is None:
            raise RuntimeError("dashscope package is not installed")
        if not self.api_key:
            raise ValueError("DashScope API key is required. Set it in config/local_keys_private.py")
        last_error: Exception | None = None
        response = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.dashscope.TextEmbedding.call(
                    model=self.model_name,
                    input=texts
                )
                status = self._get_value(response, "status_code")
                if status == HTTPStatus.OK:
                    break
                message = self._get_value(response, "message") or self._get_value(response, "code") or "dashscope embedding error"
                last_error = RuntimeError(str(message))
            except Exception as exc:
                last_error = exc

            if attempt < self.max_retries:
                time.sleep(self.retry_backoff_seconds * attempt)
            else:
                raise RuntimeError(str(last_error or "dashscope embedding error"))

        output = self._get_value(response, "output")
        embeddings = self._get_value(output, "embeddings")
        if embeddings is None:
            raise RuntimeError("DashScope response missing embeddings")
        parsed = []
        for item in embeddings:
            idx = self._get_value(item, "text_index")
            vec = self._get_value(item, "embedding")
            parsed.append((idx if idx is not None else len(parsed), vec))
        parsed.sort(key=lambda x: x[0])
        return [p[1] for p in parsed]

    def embed_documents(self, texts: Sequence[str] = None, **kwargs) -> List[List[float]]:
        """批量文本向量化（embed_documents 风格接口）。"""
        if texts is None:
            texts = kwargs.get("texts") or kwargs.get("input") or []
        return self(self._normalize_texts(texts))

    def embed_query(self, text: str = None, **kwargs) -> List[float]:
        """兼容 query / similarity_search 等单条查询接口。"""
        if text is None:
            text = kwargs.get("text") or kwargs.get("input") or ""
        normalized = self._normalize_texts(text)
        vectors = self(normalized[:1])
        return vectors[0] if vectors else []

    @staticmethod
    def _get_value(obj: Any, key: str):
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    @staticmethod
    def _load_key_from_code_file() -> str:
        try:
            from config.local_keys_private import DASHSCOPE_API_KEY
            return DASHSCOPE_API_KEY
        except Exception:
            return ""
