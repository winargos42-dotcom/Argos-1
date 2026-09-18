"""
huggingface_ai.py — Интеграция с Hugging Face Inference Providers.

Поддерживает:
  * Пул токенов HUGGINGFACE_TOKEN_0 … HUGGINGFACE_TOKEN_N с round-robin ротацией
  * Обычные модели Hugging Face Hub через huggingface_hub.InferenceClient
  * Ссылки на Spaces как конфиг-референс c fallback на рабочую embedding-модель
  * Авто-парсинг URL -> model_id / space_id
"""

from __future__ import annotations

SKILL_DESCRIPTION = "HuggingFace Inference: пул токенов, round-robin"

import json
import math
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv
from huggingface_hub import InferenceClient

load_dotenv()

HF_HUB_API = "https://huggingface.co/api"
DEFAULT_EMBED_MODEL = os.getenv(
    "HUGGINGFACE_EMBED_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2",
)
DEFAULT_TEXT_MODEL = os.getenv("HUGGINGFACE_TEXT_MODEL", "").strip()
DEFAULT_SEMANTIC_SPACE = os.getenv("HF_SEMANTIC_SPACE", "RJuro/scifact-semantic-search").strip()
HF_DATASET_INDEX_PATH = Path(os.getenv("HF_DATASET_INDEX_PATH", "data/hf_dataset_index.json"))
HF_SPACE_ALIASES = {
    "voiceclone": "tonyassi/voice-clone",
    "joycaption": "fancyfeast/joy-caption-beta-one",
    "sentiment": "nazianafis/Sentiment-Analysis",
    "finance": "PleIAs/Finance-Commons",
    "datasetgen": "jaymanr/dataset-generator",
    "echoenv": "openenv/echo_env",
    "netgoat": "netgoat-ai/netgoat-ai",
}
HF_DATASET_ALIASES = {
    "prompts.chat": "fka/prompts.chat",
    "prompts_chat": "fka/prompts.chat",
}


class _HFTokenPool:
    """
    Ротирует HUGGINGFACE_TOKEN_0 … HUGGINGFACE_TOKEN_N.
    Fallback: HUGGINGFACE_TOKEN / HF_TOKEN.
    При HTTP 429 помечает токен исчерпанным и переключается на следующий.
    """

    WAIT_SEC = 65
    MAX_RPM = int(os.getenv("HF_RPM_PER_TOKEN", "30"))

    def __init__(self):
        self._lock = threading.Lock()
        self._tokens: list[str] = self._collect()
        self._ts: list[deque] = [deque() for _ in self._tokens]
        self._cursor = 0

    @staticmethod
    def _collect() -> list[str]:
        """Собирает ВСЕ уникальные токены из всех поддерживаемых форматов имён."""
        seen: set[str] = set()
        tokens: list[str] = []

        def _add(val: str) -> None:
            v = (val or "").strip()
            if v and v not in ("your_token_here",) and v not in seen:
                seen.add(v)
                tokens.append(v)

        # Индексированные форматы: TOKEN_N, TOKENN — каждый проверяем независимо
        for i in range(20):
            for tpl in (
                f"HUGGINGFACE_TOKEN_{i}",
                f"HUGGINGFACE_TOKEN{i}",
                f"HF_TOKEN_{i}",
                f"HF_TOKEN{i}",
            ):
                _add(os.getenv(tpl, ""))

        # Безындексные fallback
        for env_name in ("HUGGINGFACE_TOKEN", "HF_TOKEN"):
            _add(os.getenv(env_name, ""))

        return tokens

    def reload(self):
        with self._lock:
            new_tokens = self._collect()
            if new_tokens != self._tokens:
                self._tokens = new_tokens
                self._ts = [deque() for _ in new_tokens]
                self._cursor = 0

    def available(self) -> bool:
        return bool(self._tokens)

    def get(self) -> tuple[int, str] | None:
        if not self._tokens:
            return None
        deadline = time.time() + self.WAIT_SEC
        while time.time() < deadline:
            with self._lock:
                now = time.time()
                count = len(self._tokens)
                for offset in range(count):
                    idx = (self._cursor + offset) % count
                    dq = self._ts[idx]
                    while dq and now - dq[0] >= 60:
                        dq.popleft()
                    if len(dq) < self.MAX_RPM:
                        dq.append(now)
                        self._cursor = (idx + 1) % count
                        return idx, self._tokens[idx]
            time.sleep(1)
        return None

    def mark_exhausted(self, idx: int):
        with self._lock:
            dq = self._ts[idx]
            now = time.time()
            while len(dq) < self.MAX_RPM:
                dq.append(now)

    def status(self) -> str:
        with self._lock:
            now = time.time()
            parts = [
                f"token_{i}: {sum(1 for t in dq if now - t < 60)}/{self.MAX_RPM}"
                for i, dq in enumerate(self._ts)
            ]
            return "  ".join(parts) if parts else "нет токенов"


_HF_POOL = _HFTokenPool()


def _parse_model_ref(ref: str) -> dict[str, Any]:
    ref = ref.strip().rstrip("/")
    if not ref.startswith("http"):
        return {
            "kind": "model",
            "model_id": ref,
            "space_id": None,
            "space_host": None,
        }

    parsed = urlparse(ref)
    path_parts = [p for p in parsed.path.split("/") if p]

    if "spaces" in path_parts:
        idx = path_parts.index("spaces")
        if len(path_parts) > idx + 2:
            user, name = path_parts[idx + 1], path_parts[idx + 2]
        else:
            user, name = "unknown", "unknown"
        return {
            "kind": "space",
            "model_id": f"{user}/{name}",
            "space_id": f"{user}/{name}",
            "space_host": f"{user}-{name}.hf.space",
        }

    if len(path_parts) >= 2:
        return {
            "kind": "model",
            "model_id": "/".join(path_parts[:2]),
            "space_id": None,
            "space_host": None,
        }

    return {
        "kind": "model",
        "model_id": ref,
        "space_id": None,
        "space_host": None,
    }


def _parse_dataset_ref(ref: str) -> str:
    val = (ref or "").strip().rstrip("/")
    if not val:
        return ""
    if "huggingface.co/datasets/" in val:
        parsed = urlparse(val)
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 3 and parts[0] == "datasets":
            return f"{parts[1]}/{parts[2]}"
    return val


def _resolve_model_env() -> dict[str, Any]:
    """
    Возвращает ref для ОБЩЕЙ модели (text gen / inference).
    Приоритет: HUGGINGFACE_TEXT_MODEL > HUGGINGFACE_MODEL > DEFAULT_EMBED_MODEL.
    HUGGINGFACE_MODEL_SPACE предназначен ТОЛЬКО для embeddings — здесь не используется.
    """
    text_model = os.getenv("HUGGINGFACE_TEXT_MODEL", "").strip()
    if text_model:
        return _parse_model_ref(text_model)
    model_val = os.getenv("HUGGINGFACE_MODEL", DEFAULT_EMBED_MODEL).strip()
    return _parse_model_ref(model_val)


def _resolve_embed_env() -> dict[str, Any]:
    """Возвращает ref для embedding модели (Space или model)."""
    space_url = os.getenv("HUGGINGFACE_MODEL_SPACE", "").strip()
    if space_url:
        return _parse_model_ref(space_url)
    embed_model = os.getenv("HUGGINGFACE_EMBED_MODEL", DEFAULT_EMBED_MODEL).strip()
    return _parse_model_ref(embed_model)


class HuggingFaceAI:
    """Клиент для Hugging Face с ротацией токенов и безопасным fallback для Spaces."""

    def __init__(
        self,
        token: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ):
        _HF_POOL.reload()
        self._explicit_token = token
        self.timeout = timeout
        self._model_ref = _parse_model_ref(model) if model else _resolve_model_env()
        self.model = self._model_ref["model_id"]
        self._local_embedder = None

    def _resolve_space(self, alias_or_id: str) -> str:
        key = (alias_or_id or "").strip().lower()
        return HF_SPACE_ALIASES.get(key, alias_or_id.strip())

    def _resolve_dataset(self, ds: str) -> str:
        key = (ds or "").strip().lower()
        return HF_DATASET_ALIASES.get(key, ds)

    def _get_token_slot(self) -> tuple[int, str] | None:
        if self._explicit_token:
            return -1, self._explicit_token
        return _HF_POOL.get()

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _client(self) -> tuple[int, str, InferenceClient]:
        slot = self._get_token_slot()
        if slot is None:
            raise RuntimeError("HuggingFace: все токены исчерпаны (rate limit)")
        idx, token = slot
        return idx, token, InferenceClient(api_key=token, timeout=self.timeout)

    def _space_meta(self, space_id: str) -> dict[str, Any]:
        _, token, _ = self._client()
        resp = requests.get(
            f"{HF_HUB_API}/spaces/{space_id}",
            headers=self._headers(token),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _effective_embed_model(self, ref: dict[str, Any]) -> str:
        if ref["kind"] == "model":
            return ref["model_id"]
        return DEFAULT_EMBED_MODEL

    def _local_embed(self, text: str) -> list[float]:
        if self._local_embedder is None:
            from sentence_transformers import SentenceTransformer

            local_model = os.getenv(
                "HUGGINGFACE_LOCAL_EMBED_MODEL",
                "paraphrase-multilingual-MiniLM-L12-v2",
            )
            self._local_embedder = SentenceTransformer(local_model)
        vector = self._local_embedder.encode([text])[0]
        return [float(x) for x in vector.tolist()]

    def _text_model(self, ref: dict[str, Any]) -> str:
        if ref["kind"] == "model":
            return ref["model_id"]
        if DEFAULT_TEXT_MODEL:
            text_ref = _parse_model_ref(DEFAULT_TEXT_MODEL)
            if text_ref["kind"] == "model":
                return text_ref["model_id"]
        raise RuntimeError(
            "HuggingFace: для text generation укажи модель в HUGGINGFACE_TEXT_MODEL "
            "или используй model=<repo_id>."
        )

    def ask(self, prompt: str, model: str | None = None, max_new_tokens: int = 512) -> str:
        ref = _parse_model_ref(model) if model else self._model_ref
        text_model = self._text_model(ref)
        idx, _, client = self._client()
        try:
            result = client.text_generation(
                prompt,
                model=text_model,
                max_new_tokens=max_new_tokens,
                return_full_text=False,
            )
            return str(result)
        except Exception as exc:
            if idx >= 0 and "429" in str(exc):
                _HF_POOL.mark_exhausted(idx)
            raise

    def embed(self, text: str, model: str | None = None) -> list[float]:
        # Для embeddings используем Space/embed ref, а не общий model_ref
        ref = _parse_model_ref(model) if model else _resolve_embed_env()
        embed_model = self._effective_embed_model(ref)
        idx, _, client = self._client()
        try:
            result = client.feature_extraction(text, model=embed_model)
        except Exception as exc:
            if idx >= 0 and "429" in str(exc):
                _HF_POOL.mark_exhausted(idx)
            return self._local_embed(text)

        if hasattr(result, "tolist"):
            result = result.tolist()
        if isinstance(result, list) and result and isinstance(result[0], list):
            result = result[0]
        return [float(x) for x in result] if isinstance(result, list) else []

    def classify(
        self,
        text: str,
        candidate_labels: list[str],
        model: str | None = None,
    ) -> dict[str, Any]:
        ref = _parse_model_ref(model) if model else _parse_model_ref("facebook/bart-large-mnli")
        idx, _, client = self._client()
        try:
            return client.zero_shot_classification(
                text,
                candidate_labels,
                model=ref["model_id"],
            )
        except Exception as exc:
            if idx >= 0 and "429" in str(exc):
                _HF_POOL.mark_exhausted(idx)
            raise

    def is_configured(self) -> bool:
        _HF_POOL.reload()
        return bool(self._explicit_token or _HF_POOL.available())

    @staticmethod
    def pool_status() -> str:
        _HF_POOL.reload()
        return _HF_POOL.status()

    def run(self) -> str:
        _HF_POOL.reload()
        ref = self._model_ref
        if not self.is_configured():
            return (
                "❌ HuggingFace не настроен.\n"
                "Добавь HUGGINGFACE_TOKEN, HF_TOKEN или HUGGINGFACE_TOKEN_0 в .env"
            )

        embed_ref = _resolve_embed_env()
        text_model_name = DEFAULT_TEXT_MODEL or "(не задан — задай HUGGINGFACE_TEXT_MODEL в .env)"
        lines = [
            "🤗 HuggingFace AI:",
            f"  Токенов в пуле: {len(_HF_POOL._tokens)}  ({_HF_POOL.MAX_RPM} RPM каждый)",
            f"  Суммарно: ~{len(_HF_POOL._tokens) * _HF_POOL.MAX_RPM} запросов/мин",
            f"  Text generation: {text_model_name}",
            f"  Embed model: {embed_ref['model_id']} ({'Space' if embed_ref['kind'] == 'space' else 'Model'})",
            f"  Пул: {_HF_POOL.status()}",
        ]

        if ref["kind"] == "space":
            try:
                meta = self._space_meta(ref["space_id"])
                sdk = meta.get("sdk", "unknown")
                stage = ((meta.get("runtime") or {}).get("stage") or "unknown")
                lines.append(f"  Space SDK: {sdk}")
                lines.append(f"  Space stage: {stage}")
                if sdk != "gradio":
                    lines.append("  Примечание: Space не используется как inference API, embeddings идут через fallback-модель.")
            except Exception as exc:
                lines.append(f"  Space meta: ошибка ({exc})")
        lines.append(
            f"  Local fallback: {os.getenv('HUGGINGFACE_LOCAL_EMBED_MODEL', 'paraphrase-multilingual-MiniLM-L12-v2')}"
        )

        return "\n".join(lines)

    def _summarize_semantic_result(self, result: Any, top_k: int = 5) -> str:
        if result is None:
            return "Пустой ответ semantic space."
        if isinstance(result, str):
            return result[:4000]
        if isinstance(result, dict):
            items = result.get("results") or result.get("items") or result.get("data") or []
            if isinstance(items, list) and items:
                lines = ["🔎 Semantic search:"]
                for i, item in enumerate(items[:top_k], 1):
                    if isinstance(item, dict):
                        text = (
                            item.get("text")
                            or item.get("content")
                            or item.get("title")
                            or str(item)
                        )
                    else:
                        text = str(item)
                    lines.append(f"{i}. {text[:300]}")
                return "\n".join(lines)
            return str(result)[:4000]
        if isinstance(result, (list, tuple)):
            lines = ["🔎 Semantic search:"]
            for i, item in enumerate(result[:top_k], 1):
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or item.get("title") or str(item)
                else:
                    text = str(item)
                lines.append(f"{i}. {text[:300]}")
            return "\n".join(lines[: top_k + 1])
        return str(result)[:4000]

    def _summarize_space_output(self, result: Any) -> str:
        if result is None:
            return "Пустой ответ Space."
        if isinstance(result, bytes):
            return f"Space вернул bytes: {len(result)}"
        if isinstance(result, str):
            return result[:4000]
        if isinstance(result, dict):
            items = []
            for k, v in result.items():
                if isinstance(v, (str, int, float, bool)):
                    items.append(f"{k}: {v}")
            if items:
                return "\n".join(items)[:4000]
            return json.dumps(result, ensure_ascii=False)[:4000]
        if isinstance(result, (list, tuple)):
            parts = []
            for item in result[:6]:
                if isinstance(item, (str, int, float, bool)):
                    parts.append(str(item))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False)[:300])
            return "\n".join(parts)[:4000]
        return str(result)[:4000]

    def list_spaces(self) -> str:
        lines = ["🤗 HF Spaces (aliases):"]
        for k, v in sorted(HF_SPACE_ALIASES.items()):
            lines.append(f"  {k} -> {v}")
        return "\n".join(lines)

    def run_space(self, alias_or_id: str, text: str = "", image_url: str = "") -> str:
        space = self._resolve_space(alias_or_id)
        if not space:
            return "Формат: hf space <alias_or_id> :: <text>"
        try:
            from gradio_client import Client
        except Exception:
            return "gradio_client не установлен. Установи: pip install gradio_client"

        hf_token = os.getenv("HF_TOKEN", "").strip()
        kwargs = {"hf_token": hf_token} if hf_token else {}
        try:
            try:
                client = Client(space, **kwargs)
            except TypeError:
                client = Client(space)
        except Exception as exc:
            return f"❌ Space недоступен ({space}): {exc}"

        named_endpoints = {}
        try:
            api_info = client.view_api(return_format="dict") or {}
            named_endpoints = api_info.get("named_endpoints", {}) or {}
        except Exception:
            named_endpoints = {}

        preferred = ["/predict", "/run", "/process", "/generate", "/infer", "/query", "/classify"]
        endpoints = [x for x in preferred if x in named_endpoints] or list(named_endpoints.keys()) or preferred
        last_err = ""
        for api_name in endpoints:
            try:
                if api_name in named_endpoints:
                    spec = named_endpoints.get(api_name, {})
                    args = []
                    for p in spec.get("parameters", []):
                        pname = (p.get("parameter_name") or "").lower()
                        label = (p.get("label") or "").lower()
                        component = (p.get("component") or "").lower()
                        if any(x in pname for x in ("prompt", "text", "query", "message", "input")) or any(
                            x in label for x in ("prompt", "text", "query", "message", "input")
                        ):
                            args.append(text or "hello")
                        elif "image" in pname or "image" in label:
                            args.append(image_url or "")
                        elif p.get("parameter_has_default", False):
                            args.append(p.get("parameter_default"))
                        elif component == "checkbox":
                            args.append(False)
                        elif component in {"slider", "number"}:
                            args.append(1)
                        else:
                            args.append("")
                    out = client.predict(*args, api_name=api_name)
                else:
                    out = client.predict(text or "hello", api_name=api_name)
                return f"✅ {space}\n{self._summarize_space_output(out)}"
            except Exception as exc:
                last_err = str(exc)
                continue
        return f"❌ Не удалось вызвать {space}: {last_err or 'no endpoint'}"

    def _index_load(self) -> dict[str, Any]:
        try:
            if HF_DATASET_INDEX_PATH.exists():
                return json.loads(HF_DATASET_INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {"datasets": {}}

    def _index_save(self, payload: dict[str, Any]) -> None:
        HF_DATASET_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        HF_DATASET_INDEX_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _to_text_row(row: dict[str, Any]) -> str:
        parts = []
        for key, val in (row or {}).items():
            if val is None:
                continue
            if isinstance(val, (dict, list)):
                sval = json.dumps(val, ensure_ascii=False)
            else:
                sval = str(val)
            if sval.strip():
                parts.append(f"{key}: {sval}")
        return "\n".join(parts).strip()

    @staticmethod
    def _chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[str]:
        txt = (text or "").strip()
        if not txt:
            return []
        if len(txt) <= chunk_size:
            return [txt]
        chunks = []
        step = max(100, chunk_size - overlap)
        for i in range(0, len(txt), step):
            part = txt[i : i + chunk_size].strip()
            if part:
                chunks.append(part)
            if i + chunk_size >= len(txt):
                break
        return chunks

    def _hf_rows(self, dataset_ref: str, rows_limit: int = 160) -> list[dict[str, Any]]:
        ds = self._resolve_dataset(_parse_dataset_ref(dataset_ref))
        if not ds:
            return []
        try:
            splits_resp = requests.get(
                "https://datasets-server.huggingface.co/splits",
                params={"dataset": ds},
                timeout=self.timeout,
            )
            splits_resp.raise_for_status()
            splits = (splits_resp.json() or {}).get("splits") or []
            if not splits:
                return []
            target = splits[0]
            config = target.get("config")
            split = target.get("split")
            if not config or not split:
                return []

            rows_resp = requests.get(
                "https://datasets-server.huggingface.co/rows",
                params={
                    "dataset": ds,
                    "config": config,
                    "split": split,
                    "offset": 0,
                    "length": max(10, min(rows_limit, 200)),
                },
                timeout=max(self.timeout, 60),
            )
            if not rows_resp.ok:
                return []
            rows_payload = rows_resp.json() or {}
            rows = rows_payload.get("rows") or []
            out = []
            for item in rows:
                row = item.get("row") if isinstance(item, dict) else item
                if isinstance(row, dict):
                    out.append(row)
            return out
        except Exception:
            return []

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return -1.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na <= 0 or nb <= 0:
            return -1.0
        return dot / (na * nb)

    def index_dataset(self, dataset_ref: str, rows_limit: int = 160) -> str:
        ds = self._resolve_dataset(_parse_dataset_ref(dataset_ref))
        if not ds:
            return "Формат: hf index <owner/name|url>"
        rows = self._hf_rows(ds, rows_limit=rows_limit)
        if not rows:
            return f"❌ Не удалось прочитать строки датасета: {ds}"

        chunks: list[dict[str, Any]] = []
        for idx, row in enumerate(rows):
            row_text = self._to_text_row(row)
            if not row_text:
                continue
            for chunk in self._chunk_text(row_text):
                vec = self.embed(chunk)
                if vec:
                    chunks.append({"text": chunk, "vector": vec, "row": idx})

        if not chunks:
            return f"❌ Не удалось построить embedding-индекс для {ds}"

        index = self._index_load()
        index.setdefault("datasets", {})
        index["datasets"][ds] = {
            "updated_at": int(time.time()),
            "size": len(chunks),
            "chunks": chunks,
        }
        self._index_save(index)
        return f"✅ HF index готов: {ds} | chunks={len(chunks)} | file={HF_DATASET_INDEX_PATH}"

    def search_local_index(self, query: str, top_k: int = 5) -> str:
        q = (query or "").strip()
        if not q:
            return "Формат: hf search <query>"
        index = self._index_load()
        datasets = index.get("datasets", {}) or {}
        if not datasets:
            return "❌ Локальный индекс пуст. Сначала: hf index <dataset>"
        qv = self.embed(q)
        if not qv:
            return "❌ Пустой embedding запроса."

        scored = []
        for ds_name, ds_payload in datasets.items():
            for ch in ds_payload.get("chunks", []):
                vec = ch.get("vector") or []
                sim = self._cosine(qv, vec)
                if sim > -0.5:
                    scored.append((sim, ds_name, ch.get("text", "")))
        scored.sort(key=lambda x: x[0], reverse=True)
        if not scored:
            return "❌ Совпадения не найдены в локальном индексе."
        lines = [f"🔎 HF local search: {q}"]
        for i, (sim, ds_name, text) in enumerate(scored[: max(1, min(top_k, 10))], 1):
            lines.append(f"{i}. [{ds_name}] (sim={sim:.3f}) {text[:300]}")
        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────────
    # Специализированные интеграции HF Spaces
    # ─────────────────────────────────────────────────────────────────────────

    def _gradio_client(self, space_id: str):
        """Создаёт gradio_client.Client с HF токеном, если доступен."""
        try:
            from gradio_client import Client
        except ImportError:
            raise RuntimeError("gradio_client не установлен. Установи: pip install gradio_client")
        token = os.getenv("HF_TOKEN", "").strip() or os.getenv("HUGGINGFACE_TOKEN", "").strip()
        try:
            return Client(space_id, hf_token=token) if token else Client(space_id)
        except TypeError:
            return Client(space_id)

    def _gradio_call(self, space_id: str, *args, api_name: str = "/predict") -> Any:
        """Вызывает Gradio endpoint, пробует список предпочтительных эндпоинтов."""
        client = self._gradio_client(space_id)
        preferred = [api_name, "/predict", "/run", "/process", "/generate",
                     "/infer", "/query", "/classify", "/chat", "/caption"]
        try:
            named = (client.view_api(return_format="dict") or {}).get("named_endpoints", {})
        except Exception:
            named = {}
        endpoints = [e for e in preferred if e in named] or list(named.keys()) or preferred[:3]
        last_err = ""
        for ep in endpoints:
            try:
                return client.predict(*args, api_name=ep)
            except Exception as exc:
                last_err = str(exc)
                continue
        raise RuntimeError(f"Нет рабочего endpoint у {space_id}: {last_err}")

    # ── 1. Voice Clone (tonyassi/voice-clone) ────────────────────────────────
    def voice_clone(self, text: str, audio_path: str = "") -> str:
        """
        Клонирование голоса через tonyassi/voice-clone.
        text       — текст для синтеза
        audio_path — путь к WAV/MP3 референсного голоса (опционально)
        """
        space = "tonyassi/voice-clone"
        if not text:
            return "Формат: hf voiceclone <текст> [:: <путь к аудио>]"
        if not self.is_configured():
            return "❌ HF токен не задан"
        try:
            client = self._gradio_client(space)
            try:
                named = (client.view_api(return_format="dict") or {}).get("named_endpoints", {})
            except Exception:
                named = {}
            # Стандартные эндпоинты voice-clone Space
            for ep in ["/clone", "/predict", "/tts", "/synthesize"]:
                if ep in named:
                    try:
                        args = [audio_path, text] if audio_path else [text]
                        out = client.predict(*args, api_name=ep)
                        if isinstance(out, str):
                            return f"✅ Voice clone: {out}"
                        return f"✅ Voice clone готов: {self._summarize_space_output(out)}"
                    except Exception as e:
                        continue
            # fallback — run_space
            return self.run_space(space, text)
        except Exception as e:
            return f"❌ voice-clone: {e}"

    # ── 2. Joy Caption (fancyfeast/joy-caption-beta-one) ─────────────────────
    def joy_caption(self, image_url: str, caption_type: str = "Descriptive") -> str:
        """
        Генерация описания изображения через fancyfeast/joy-caption-beta-one.
        image_url    — URL или путь к изображению
        caption_type — тип (Descriptive / Straightforward / MidJourney / Booru / Art)
        """
        space = "fancyfeast/joy-caption-beta-one"
        if not image_url:
            return "Формат: hf joycaption <url изображения> [:: тип]"
        if not self.is_configured():
            return "❌ HF токен не задан"
        try:
            client = self._gradio_client(space)
            try:
                named = (client.view_api(return_format="dict") or {}).get("named_endpoints", {})
            except Exception:
                named = {}
            for ep in ["/stream_chat", "/caption", "/predict", "/process"]:
                if ep in named:
                    try:
                        out = client.predict(image_url, caption_type, "", api_name=ep)
                        return f"🖼️ Joy Caption:\n{self._summarize_space_output(out)}"
                    except Exception:
                        continue
            return self.run_space(space, image_url, image_url=image_url)
        except Exception as e:
            return f"❌ joy-caption: {e}"

    # ── 3. Sentiment Analysis (nazianafis/Sentiment-Analysis) ────────────────
    def sentiment(self, text: str) -> str:
        """
        Анализ тональности текста через nazianafis/Sentiment-Analysis.
        Возвращает: label (POSITIVE/NEGATIVE/NEUTRAL) + score.
        """
        space = "nazianafis/Sentiment-Analysis"
        if not text:
            return "Формат: hf sentiment <текст>"
        if not self.is_configured():
            return "❌ HF токен не задан"
        try:
            result = self._gradio_call(space, text, api_name="/predict")
            if isinstance(result, (list, tuple)) and result:
                item = result[0] if isinstance(result[0], dict) else {"label": str(result[0])}
                label = item.get("label", str(result[0]))
                score = item.get("score", "")
                emoji = {"POSITIVE": "😊", "NEGATIVE": "😠", "NEUTRAL": "😐"}.get(
                    str(label).upper(), "🔍")
                score_str = f" ({score:.2%})" if isinstance(score, float) else ""
                return f"{emoji} Тональность: {label}{score_str}\nТекст: {text[:200]}"
            return f"🔍 Sentiment: {self._summarize_space_output(result)}"
        except Exception as e:
            return f"❌ sentiment: {e}"

    # ── 4. Finance Commons (PleIAs/Finance-Commons) ──────────────────────────
    def finance_analyze(self, text: str) -> str:
        """
        Финансовый анализ текста через PleIAs/Finance-Commons.
        Подходит для разбора финансовых документов, отчётов, новостей.
        """
        space = "PleIAs/Finance-Commons"
        if not text:
            return "Формат: hf finance <финансовый текст>"
        if not self.is_configured():
            return "❌ HF токен не задан"
        try:
            result = self._gradio_call(space, text, api_name="/predict")
            return f"💹 Finance Commons:\n{self._summarize_space_output(result)}"
        except Exception as e:
            return f"❌ finance: {e}"

    # ── 5. Dataset Generator (jaymanr/dataset-generator) ────────────────────
    def dataset_gen(self, description: str, n_rows: int = 10) -> str:
        """
        Генерация синтетического датасета через jaymanr/dataset-generator.
        description — описание нужного датасета (тема, формат)
        n_rows      — количество примеров
        """
        space = "jaymanr/dataset-generator"
        if not description:
            return "Формат: hf datasetgen <описание датасета>"
        if not self.is_configured():
            return "❌ HF токен не задан"
        try:
            result = self._gradio_call(space, description, n_rows, api_name="/generate")
            return f"🗂️ Dataset Generator:\n{self._summarize_space_output(result)}"
        except Exception as e:
            return f"❌ datasetgen: {e}"

    # ── 6. Echo Env (openenv/echo_env) ───────────────────────────────────────
    def echo_env(self, query: str = "status") -> str:
        """
        Запрос к openenv/echo_env — диагностическая среда, возвращает env-данные.
        """
        space = "openenv/echo_env"
        if not self.is_configured():
            return "❌ HF токен не задан"
        try:
            result = self._gradio_call(space, query, api_name="/predict")
            return f"🔧 Echo Env:\n{self._summarize_space_output(result)}"
        except Exception as e:
            return f"❌ echo_env: {e}"

    # ── 7. NetGoat AI (netgoat-ai/netgoat-ai) ────────────────────────────────
    def netgoat(self, query: str) -> str:
        """
        Сетевой/безопасностной анализ через netgoat-ai/netgoat-ai.
        query — цель, домен, IP или вопрос по сетевой безопасности.
        """
        space = "netgoat-ai/netgoat-ai"
        if not query:
            return "Формат: hf netgoat <домен или вопрос>"
        if not self.is_configured():
            return "❌ HF токен не задан"
        try:
            result = self._gradio_call(space, query, api_name="/predict")
            return f"🐐 NetGoat AI:\n{self._summarize_space_output(result)}"
        except Exception as e:
            return f"❌ netgoat: {e}"

    # ── 8. Prompts.chat dataset (fka/prompts.chat) ────────────────────────────
    def prompts_random(self, n: int = 5) -> str:
        """
        Показывает случайные промпты из fka/prompts.chat.
        Если датасет не проиндексирован — возвращает данные напрямую через API.
        """
        import random
        rows = self._hf_rows("fka/prompts.chat", rows_limit=50)
        if not rows:
            return "❌ prompts.chat недоступен. Попробуй: hf index prompts.chat"
        sample = random.sample(rows, min(n, len(rows)))
        lines = [f"💬 prompts.chat — {len(rows)} промптов, показано {len(sample)}:"]
        for i, row in enumerate(sample, 1):
            act = row.get("act", row.get("role", ""))
            prompt = row.get("prompt", row.get("content", str(row)))
            lines.append(f"\n{i}. 🎭 {act}\n   {prompt[:300]}")
        return "\n".join(lines)

    def prompts_search(self, query: str, top_k: int = 5) -> str:
        """
        Семантический поиск по fka/prompts.chat через локальный индекс.
        Если индекса нет — предлагает проиндексировать.
        """
        q = (query or "").strip()
        if not q:
            return "Формат: hf prompts <запрос>"
        index = self._index_load()
        if "fka/prompts.chat" not in index.get("datasets", {}):
            # Пробуем индексировать на лету (небольшой сэмпл)
            rows = self._hf_rows("fka/prompts.chat", rows_limit=100)
            if not rows:
                return "❌ prompts.chat недоступен — нет соединения или нет индекса. Запусти: hf index prompts.chat"
            # Быстрый keyword-поиск без embedding
            q_lo = q.lower()
            hits = []
            for row in rows:
                act = str(row.get("act", "")).lower()
                prompt = str(row.get("prompt", "")).lower()
                if q_lo in act or q_lo in prompt:
                    hits.append(row)
            if hits:
                lines = [f"💬 prompts.chat keyword search: «{q}» ({len(hits)} совпадений)"]
                for i, row in enumerate(hits[:top_k], 1):
                    act = row.get("act", "")
                    prompt = row.get("prompt", str(row))
                    lines.append(f"\n{i}. 🎭 {act}\n   {prompt[:300]}")
                return "\n".join(lines)
            return f"❌ Промптов по «{q}» не найдено. Для семантического поиска: hf index prompts.chat"
        return self.search_local_index(q, top_k=top_k)

    # ─────────────────────────────────────────────────────────────────────────
    # Unified command handler (вызывается из core.py)
    # ─────────────────────────────────────────────────────────────────────────

    def execute(self, text: str = "") -> str:
        """Точка входа SkillLoader."""
        t = (text or "").strip()
        if not t or t.lower() in ("статус", "status"):
            return self.run()
        result = self.handle(t)
        if result is not None:
            return result
        return self.run()

    def handle(self, text: str) -> str | None:
        """
        Единая точка входа для HF-команд из core.py.
        Разбирает команды вида: hf <sub> [args...] [:: extra]

        Возвращает None если текст не содержит HF команд.

        Команды:
          hf status / hf статус          — статус пула токенов
          hf spaces                       — список алиасов Space
          hf voiceclone <текст> [:: wav]  — клонирование голоса
          hf joycaption <url> [:: тип]    — описание изображения
          hf sentiment <текст>            — анализ тональности
          hf finance <текст>              — финансовый анализ
          hf datasetgen <описание>        — генерация датасета
          hf echo [запрос]                — echo_env диагностика
          hf netgoat <цель/вопрос>        — сетевой AI анализ
          hf prompts [запрос]             — поиск в prompts.chat
          hf random [N]                   — N случайных промптов
          hf index <dataset>              — индексировать датасет
          hf search <запрос>              — семантический поиск
          hf space <alias> [:: текст]     — произвольный Space
          hf ask <текст>                  — text generation
          hf embed <текст>                — embedding вектор (размер)
        """
        # Проверка TRIGGERS - если текст не содержит HF команд, выходим
        t_lower = (text or "").lower()
        hf_triggers = ("hf ", "huggingface", "hf status", "hf spaces", 
                       "hf voiceclone", "hf joycaption", "hf sentiment",
                       "hf finance", "hf datasetgen", "hf echo", 
                       "hf netgoat", "hf prompts", "hf random", 
                       "hf index", "hf search", "hf space", 
                       "hf ask", "hf embed", "huggingfaceai")
        if not any(tr in t_lower for tr in hf_triggers):
            return None
        
        t = (text or "").strip()
        # Убираем префикс "hf " если есть
        for prefix in ("hf ", "huggingface "):
            if t.lower().startswith(prefix):
                t = t[len(prefix):]
                break

        lo = t.lower()
        # Разбиваем по "::" на основную часть и доп. аргумент
        parts = t.split("::", 1)
        main = parts[0].strip()
        extra = parts[1].strip() if len(parts) > 1 else ""

        # ── Статус ─────────────────────────────────────────────────────
        if lo in ("status", "статус", "info", "инфо", "") or lo.startswith("status") or lo.startswith("статус"):
            return self.run()

        if lo in ("spaces", "списки спейсов", "list spaces"):
            return self.list_spaces()

        # ── Voice Clone ────────────────────────────────────────────────
        if lo.startswith(("voiceclone", "voice clone", "голос клон", "клон голоса")):
            args_text = main.split(" ", 1)[1].strip() if " " in main else ""
            return self.voice_clone(args_text or extra, audio_path=extra if extra and not args_text else "")

        # ── Joy Caption ────────────────────────────────────────────────
        if lo.startswith(("joycaption", "joy caption", "описание фото", "caption")):
            img = main.split(" ", 1)[1].strip() if " " in main else extra
            cap_type = extra if extra and not img.startswith("http") else "Descriptive"
            return self.joy_caption(img, cap_type)

        # ── Sentiment ──────────────────────────────────────────────────
        if lo.startswith(("sentiment", "тональность", "анализ тон", "сентимент")):
            query = main.split(" ", 1)[1].strip() if " " in main else extra
            return self.sentiment(query)

        # ── Finance ────────────────────────────────────────────────────
        if lo.startswith(("finance", "финанс", "финансовый анализ")):
            query = main.split(" ", 1)[1].strip() if " " in main else extra
            return self.finance_analyze(query)

        # ── Dataset Generator ──────────────────────────────────────────
        if lo.startswith(("datasetgen", "dataset gen", "генерируй датасет", "создай датасет")):
            desc = main.split(" ", 1)[1].strip() if " " in main else extra
            return self.dataset_gen(desc)

        # ── Echo Env ───────────────────────────────────────────────────
        if lo.startswith(("echo", "echo env", "echoenv")):
            q = main.split(" ", 1)[1].strip() if " " in main else (extra or "status")
            return self.echo_env(q)

        # ── NetGoat ────────────────────────────────────────────────────
        if lo.startswith(("netgoat", "нетгоат", "сеть анализ hf")):
            q = main.split(" ", 1)[1].strip() if " " in main else extra
            return self.netgoat(q)

        # ── Prompts.chat ───────────────────────────────────────────────
        if lo.startswith(("prompts", "промпты", "prompt search")):
            q = main.split(" ", 1)[1].strip() if " " in main else extra
            return self.prompts_search(q) if q else self.prompts_random()

        if lo.startswith(("random", "случайн")):
            try:
                n = int(main.split()[-1])
            except (ValueError, IndexError):
                n = 5
            return self.prompts_random(n)

        # ── Index dataset ──────────────────────────────────────────────
        if lo.startswith(("index", "индекс", "индексируй")):
            ds = main.split(" ", 1)[1].strip() if " " in main else extra
            if not ds:
                return "Формат: hf index <owner/dataset>"
            return self.index_dataset(ds)

        # ── Semantic search ────────────────────────────────────────────
        if lo.startswith(("search", "поиск", "найди")):
            q = main.split(" ", 1)[1].strip() if " " in main else extra
            return self.search_local_index(q) if q else "Формат: hf search <запрос>"

        # ── Generic Space ──────────────────────────────────────────────
        if lo.startswith(("space ", "спейс ")):
            rest = main.split(" ", 1)[1].strip() if " " in main else ""
            alias_parts = rest.split(" ", 1)
            alias = alias_parts[0]
            payload = alias_parts[1] if len(alias_parts) > 1 else (extra or "hello")
            return self.run_space(alias, payload)

        # ── Text generation (ask) ──────────────────────────────────────
        if lo.startswith(("ask ", "спроси ", "запрос ")):
            prompt = main.split(" ", 1)[1].strip() if " " in main else extra
            try:
                return "🤗 " + self.ask(prompt)
            except Exception as e:
                return f"❌ HF ask: {e}"

        # ── Embed ──────────────────────────────────────────────────────
        if lo.startswith(("embed ", "эмбед ")):
            txt = main.split(" ", 1)[1].strip() if " " in main else extra
            try:
                vec = self.embed(txt)
                return f"📐 Embedding: dim={len(vec)}, sample={[round(v,4) for v in vec[:5]]}..."
            except Exception as e:
                return f"❌ HF embed: {e}"

        # ── Fallback: статус ───────────────────────────────────────────
        help_text = (
            "🤗 HuggingFace команды:\n"
            "  hf status              — статус пула токенов\n"
            "  hf spaces              — список алиасов Space\n"
            "  hf voiceclone <текст>  — клонирование голоса\n"
            "  hf joycaption <url>    — описание изображения\n"
            "  hf sentiment <текст>   — анализ тональности\n"
            "  hf finance <текст>     — финансовый анализ\n"
            "  hf datasetgen <тема>   — генерация датасета\n"
            "  hf echo [запрос]       — echo_env диагностика\n"
            "  hf netgoat <цель>      — сетевой AI анализ\n"
            "  hf prompts [запрос]    — поиск в prompts.chat\n"
            "  hf random [N]          — N случайных моделей\n"
        )
        return help_text