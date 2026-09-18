"""
src/ai_router.py — Автопереключение между AI провайдерами.
Порядок: Gemini → Groq → DeepSeek → GigaChat → YandexGPT → WatsonX → xAI Grok → Ollama

Cost Optimization:
- Model Tiering: автоматический выбор модели по сложности запроса
- Semantic Caching: кэширование ответов по смыслу
"""

from __future__ import annotations
import os
import re
import time
import logging
import threading
from collections import deque

log = logging.getLogger("argos.ai_router")

# Cost optimization imports
try:
    from src.api_cost_optimizer import get_cached, store_cached, get_tier_and_model
    HAS_COST_OPT = True
except ImportError:
    HAS_COST_OPT = False
    log.warning("[AI Router] api_cost_optimizer not available")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "on", "yes", "да", "вкл"}


_GEMINI_DEFAULT_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)
_GEMINI_DEPRECATED_PREFIXES = ("gemini-1.5",)


def _split_gemini_model_list(raw: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\s]+", raw or "") if item.strip()]


def _gemini_model_candidates(requested: str = "") -> list[str]:
    env_model = os.getenv("GEMINI_MODEL", "").strip()
    if env_model:
        requested = env_model

    candidates = [
        requested,
        *_split_gemini_model_list(os.getenv("GEMINI_MODEL_CANDIDATES", "")),
        *_GEMINI_DEFAULT_MODELS,
    ]
    allow_deprecated = _env_flag("ARGOS_ALLOW_DEPRECATED_GEMINI_MODELS", False)
    seen: set[str] = set()
    result: list[str] = []
    for model_name in candidates:
        model_name = (model_name or "").strip().removeprefix("models/")
        if not model_name or model_name in seen:
            continue
        lowered = model_name.lower()
        if any(lowered.startswith(prefix) for prefix in _GEMINI_DEPRECATED_PREFIXES) and not allow_deprecated:
            log.warning("Gemini: пропускаю устаревшую модель %s", model_name)
            continue
        seen.add(model_name)
        result.append(model_name)
    return result


# ── Пул ключей Gemini с per-key rate-limiting ─────────────────────────────────
class _GeminiKeyPool:
    """
    Ротирует ключи GEMINI_API_KEY_0 … GEMINI_API_KEY_N (и GEMINI_API_KEY).
    Каждый ключ ограничен MAX_RPM запросами в минуту.
    get_key() возвращает (index, key) следующего доступного ключа
    или блокируется до освобождения места (не более WAIT_SEC секунд).
    """

    MAX_RPM   = int(os.getenv("GEMINI_RPM_PER_KEY", "5"))   # лимит на ключ
    WAIT_SEC  = 65                                            # макс. ожидание

    def __init__(self):
        self._lock = threading.Lock()
        self._keys: list[str] = self._collect_keys()
        # для каждого ключа храним временны́е метки запросов в окне 60 с
        self._timestamps: list[deque] = [deque() for _ in self._keys]
        self._cursor = 0   # round-robin

    @staticmethod
    def _collect_keys() -> list[str]:
        keys = []
        for i in range(20):                          # поддерживаем до 20 ключей
            # Поддерживаем оба формата: GEMINI_API_KEY_0 и GEMINI_API_KEY0
            k = os.getenv(f"GEMINI_API_KEY_{i}", "") or os.getenv(f"GEMINI_API_KEY{i}", "")
            if k and k not in ("", "your_key_here"):
                keys.append(k)
        # Также проверяем «голый» GEMINI_API_KEY (обратная совместимость)
        fallback = os.getenv("GEMINI_API_KEY", "")
        if fallback and fallback not in ("", "your_key_here") and fallback not in keys:
            keys.append(fallback)
        return keys

    def available(self) -> bool:
        return bool(self._keys)

    def get_key(self) -> tuple[int, str] | None:
        """
        Возвращает (idx, key) ключ с доступным слотом.
        Ждёт до WAIT_SEC секунд; возвращает None если всё занято.
        """
        if not self._keys:
            return None
        deadline = time.time() + self.WAIT_SEC
        while time.time() < deadline:
            with self._lock:
                now = time.time()
                n = len(self._keys)
                # проверяем ключи по кругу начиная с cursor
                for offset in range(n):
                    idx = (self._cursor + offset) % n
                    dq = self._timestamps[idx]
                    # чистим метки старше 60 с
                    while dq and now - dq[0] >= 60:
                        dq.popleft()
                    if len(dq) < self.MAX_RPM:
                        dq.append(now)
                        self._cursor = (idx + 1) % n   # следующий старт
                        return idx, self._keys[idx]
            # все ключи заняты — ждём освобождения
            time.sleep(1)
        return None   # тайм-аут

    def mark_rate_limited(self, idx: int):
        """Помечает ключ как полностью исчерпанный на 60 с."""
        with self._lock:
            dq = self._timestamps[idx]
            now = time.time()
            # заполняем очередь «до отказа»
            while len(dq) < self.MAX_RPM:
                dq.append(now)

    def reload(self):
        """Перечитывает ключи из env (для динамического добавления)."""
        with self._lock:
            new_keys = self._collect_keys()
            if new_keys != self._keys:
                self._keys = new_keys
                self._timestamps = [deque() for _ in new_keys]
                self._cursor = 0
                log.info(f"[GeminiPool] Ключей загружено: {len(new_keys)}")

    def status(self) -> str:
        """Возвращает строку состояния пула для диагностики."""
        with self._lock:
            now = time.time()
            parts = []
            for i, dq in enumerate(self._timestamps):
                used = sum(1 for t in dq if now - t < 60)
                parts.append(f"key_{i}: {used}/{self.MAX_RPM}")
            return "  ".join(parts) if parts else "нет ключей"


_GEMINI_POOL = _GeminiKeyPool()

# Cooldown в секундах после ошибки провайдера
_COOLDOWN = int(os.getenv("ARGOS_PROVIDER_COOLDOWN", "60"))

# Состояние провайдеров: {name: last_fail_time}
_provider_state: dict[str, float] = {}


def _is_available(name: str) -> bool:
    """Провайдер доступен если не было ошибки или cooldown истёк."""
    last_fail = _provider_state.get(name, 0)
    return (time.time() - last_fail) > _COOLDOWN


def _mark_failed(name: str) -> None:
    _provider_state[name] = time.time()
    log.warning(f"[AI Router] {name} недоступен — cooldown {_COOLDOWN}s")


def _mark_ok(name: str) -> None:
    _provider_state.pop(name, None)


class AIRouter:
    """Роутер запросов между AI провайдерами с автофallback."""

    # Порядок приоритетов
    PROVIDERS = [
        "gemini",
        "groq",
        "deepseek",
        "gigachat",
        "yandexgpt",
        "watsonx",
        "xai",
        "ollama",
    ]

    def __init__(self, core=None):
        self.core = core

    @staticmethod
    def _gemini_model_candidates() -> list[str]:
        return _gemini_model_candidates()

    def ask(self, prompt: str, system: str = "") -> str | None:
        """Отправить запрос — автоматически выбирает доступного провайдера."""
        # Cost optimization: semantic cache
        if HAS_COST_OPT:
            cached = get_cached(prompt)
            if cached:
                log.info("[AI Router] Semantic cache HIT")
                return cached
        
        # Cost optimization: model tiering
        if HAS_COST_OPT:
            tier, model = get_tier_and_model(prompt)
            log.info("[AI Router] Query tier: %s (%s)", tier, model)
        
        for provider in self.PROVIDERS:
            if not _is_available(provider):
                continue
            result = self._try_provider(provider, prompt, system)
            if result:
                _mark_ok(provider)
                # Cost optimization: store in cache
                if HAS_COST_OPT:
                    store_cached(prompt, result)
                return result
        log.error("[AI Router] Все провайдеры недоступны")
        return None

    def _try_provider(self, name: str, prompt: str, system: str) -> str | None:
        try:
            method = getattr(self, f"_ask_{name}", None)
            if method:
                return method(prompt, system)
        except Exception as e:
            log.warning(f"[AI Router] {name} ошибка: {e}")
            _mark_failed(name)
        return None

    # ── Провайдеры ────────────────────────────────────────────────────────────

    def _ask_gemini(self, prompt: str, system: str) -> str | None:
        if _env_flag("ARGOS_DISABLE_GEMINI"):
            return None

        import requests

        full = f"{system}\n\n{prompt}" if system else prompt
        payload = {"contents": [{"parts": [{"text": full}]}]}
        model_candidates = self._gemini_model_candidates()
        gcp_url = os.getenv("ARGOS_GCP_URL", "").strip().rstrip("/")
        if gcp_url:
            try:
                response = requests.post(
                    f"{gcp_url}/proxy/gemini/v1/models/{model_candidates[0]}:generateContent",
                    json=payload,
                    timeout=8,
                )
                if response.status_code == 200:
                    candidates = response.json().get("candidates", [])
                    if candidates:
                        return candidates[0]["content"]["parts"][0]["text"]
            except Exception:
                pass

        _GEMINI_POOL.reload()
        if not _GEMINI_POOL.available():
            return None
        slot = _GEMINI_POOL.get_key()
        if slot is None:
            raise RuntimeError("Gemini: все ключи исчерпаны (rate limit), подожди минуту")

        for key_attempt in range(2):
            idx, key = slot
            quota_exhausted = False
            last_error = "нет доступной модели"
            for model_name in model_candidates:
                try:
                    response = requests.post(
                        f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent",
                        headers={"x-goog-api-key": key},
                        json=payload,
                        timeout=10,
                    )
                    data = response.json()
                except Exception as error:
                    raise RuntimeError(f"Gemini[key_{idx}]: {type(error).__name__}") from None

                if response.status_code == 200:
                    candidates = data.get("candidates", [])
                    if candidates:
                        return candidates[0]["content"]["parts"][0]["text"]
                    return ""

                last_error = f"Gemini HTTP {response.status_code}: {data}"
                error_text = str(data).lower()
                if response.status_code == 404 or "not found" in error_text or "not supported" in error_text:
                    continue
                if response.status_code == 429 or any(
                    marker in error_text for marker in ("quota", "resource_exhausted", "rate limit")
                ):
                    quota_exhausted = True
                    continue
                raise RuntimeError(f"Gemini[key_{idx}]: {last_error}")

            if quota_exhausted:
                _GEMINI_POOL.mark_rate_limited(idx)
                if key_attempt == 0:
                    next_slot = _GEMINI_POOL.get_key()
                    if next_slot and next_slot[0] != idx:
                        slot = next_slot
                        continue
            raise RuntimeError(f"Gemini[key_{idx}]: {last_error}")

    def _ask_groq(self, prompt: str, system: str) -> str | None:
        key = os.getenv("GROQ_API_KEY", "")
        if not key:
            return None
        try:
            import requests

            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json={"model": "llama-3.3-70b-versatile", "messages": messages},
                timeout=30,
            )
            if resp.status_code == 429:
                raise RuntimeError("Rate limit")
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            raise RuntimeError(f"Groq: {e}")

    def _ask_deepseek(self, prompt: str, system: str) -> str | None:
        key = os.getenv("DEEPSEEK_API_KEY", "")
        if not key:
            return self._ask_deepseek_space(prompt, system) if _env_flag("HF_DEEPSEEK_SPACE_ENABLED", False) else None
        try:
            import requests

            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers=headers,
                json={"model": "deepseek-chat", "messages": messages},
                timeout=30,
            )
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                err = str(data)
                if "Insufficient Balance" in err or "invalid_request_error" in err:
                    if not _env_flag("HF_DEEPSEEK_SPACE_ENABLED", False):
                        raise RuntimeError(f"DeepSeek bad response: {data}")
                    space_fallback = self._ask_deepseek_space(prompt, system)
                    if space_fallback:
                        return space_fallback
                raise RuntimeError(f"DeepSeek bad response: {data}")
            return choices[0]["message"]["content"]
        except Exception as e:
            raise RuntimeError(f"DeepSeek: {e}")

    def _ask_deepseek_space(self, prompt: str, system: str) -> str | None:
        """Fallback через HF Space hi1234567890t/deepseek (gradio_client)."""
        if not _env_flag("HF_DEEPSEEK_SPACE_ENABLED", False):
            return None
        space_id = (os.getenv("HF_DEEPSEEK_SPACE", "hi1234567890t/deepseek") or "").strip()
        if not space_id:
            return None
        full = f"{system}\n\n{prompt}" if system else prompt
        try:
            from gradio_client import Client
            kwargs = {}
            token = os.getenv("HF_TOKEN", "").strip()
            if token:
                kwargs["hf_token"] = token
            client = Client(space_id, **kwargs)
            for api_name in ("/chat", "/predict", "/run"):
                try:
                    out = client.predict(full, api_name=api_name)
                    if isinstance(out, str) and out.strip():
                        return out.strip()
                    if isinstance(out, (list, tuple)):
                        for item in out:
                            if isinstance(item, str) and item.strip():
                                return item.strip()
                except Exception:
                    continue
        except Exception as e:
            log.debug("[AI Router] deepseek space fallback unavailable: %s", e)
        return None

    def _ask_gigachat(self, prompt: str, system: str) -> str | None:
        if not self.core:
            return None
        try:
            return self.core._ask_gigachat(system, prompt)
        except Exception as e:
            raise RuntimeError(f"GigaChat: {e}")

    def _ask_yandexgpt(self, prompt: str, system: str) -> str | None:
        if not self.core:
            return None
        try:
            return self.core._ask_yandexgpt(system, prompt)
        except Exception as e:
            raise RuntimeError(f"YandexGPT: {e}")

    def _ask_watsonx(self, prompt: str, system: str) -> str | None:
        key = os.getenv("WATSONX_API_KEY", "")
        if not key:
            return None
        try:
            if self.core and hasattr(self.core, "_ask_watsonx"):
                return self.core._ask_watsonx(system, prompt)
        except Exception as e:
            raise RuntimeError(f"WatsonX: {e}")
        return None

    def _ask_xai(self, prompt: str, system: str) -> str | None:
        key = (os.getenv("XAI_API_KEY", "") or os.getenv("GROK_API_KEY", "")).strip()
        if not key:
            return None
        try:
            import requests

            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = requests.post(
                "https://api.x.ai/v1/chat/completions",
                headers=headers,
                json={"model": "grok-beta", "messages": messages},
                timeout=30,
            )
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            raise RuntimeError(f"xAI Grok: {e}")

    def _ask_ollama(self, prompt: str, system: str) -> str | None:
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        model = os.getenv("OLLAMA_MODEL", "llama3")
        try:
            import requests

            resp = requests.post(
                f"{host}/api/generate",
                json={"model": model, "prompt": prompt, "system": system, "stream": False},
                timeout=120,
            )
            return resp.json().get("response")
        except Exception as e:
            raise RuntimeError(f"Ollama: {e}")

    def status(self) -> str:
        lines = ["🤖 AI Router — статус провайдеров:\n"]
        gemini_has_key = _GEMINI_POOL.available()
        gigachat_has_key = bool(
            os.getenv("GIGACHAT_ACCESS_TOKEN")
            or (os.getenv("GIGACHAT_CLIENT_ID") and os.getenv("GIGACHAT_CLIENT_SECRET"))
        )
        yandex_has_key = bool(os.getenv("YANDEX_IAM_TOKEN") and os.getenv("YANDEX_FOLDER_ID"))
        for p in self.PROVIDERS:
            if p == "gemini":
                has_key = gemini_has_key
            elif p == "gigachat":
                has_key = gigachat_has_key
            elif p == "yandexgpt":
                has_key = yandex_has_key
            elif p == "groq":
                has_key = bool(os.getenv("GROQ_API_KEY"))
            elif p == "deepseek":
                has_key = bool(os.getenv("DEEPSEEK_API_KEY"))
            elif p == "watsonx":
                has_key = bool(os.getenv("WATSONX_API_KEY"))
            elif p == "xai":
                has_key = bool((os.getenv("XAI_API_KEY") or "").strip() or (os.getenv("GROK_API_KEY") or "").strip())
            else:
                has_key = True
            available = _is_available(p)
            icon = "✅" if has_key and available else ("⏳" if not available else "❌")
            lines.append(f"  {icon} {p:<12} {'ключ есть' if has_key else 'нет ключа'}")
        lines.append(f"\n  Gemini pool: {_GEMINI_POOL.status()}")
        return "\n".join(lines)
