"""
content_gen.py — Медиа-Архитектор
  Сбор AI-новостей через RSS + генерация поста + публикация в Telegram
"""

SKILL_DESCRIPTION = "Генерация контента: тексты, посты, статьи через LLM"

import os
import json
import time
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import requests
    _REQUESTS_OK = True
except ImportError:
    _REQUESTS_OK = False


class ContentGen:
    # Файл персистентной очереди публикаций
    _QUEUE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "publish_queue.json"

    # RSS-источники — работают без JavaScript, не блокируют парсеры
    RSS_SOURCES = [
        {"name": "MIT Tech Review AI", "url": "https://www.technologyreview.com/feed/"},
        {"name": "VentureBeat AI",     "url": "https://venturebeat.com/category/ai/feed/"},
        {"name": "TechCrunch AI",      "url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
        {"name": "The Verge AI",       "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
        {"name": "Ars Technica AI",    "url": "https://feeds.arstechnica.com/arstechnica/technology-lab"},
        {"name": "Wired AI",           "url": "https://www.wired.com/feed/tag/ai/latest/rss"},
    ]

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (compatible; ArgosBot/2.0; +https://github.com/argos)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }

    def __init__(self):
        self._pending = self._load_queue()
        self._running = False
        self._tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self._tg_chatid = os.getenv("USER_ID")

    def _load_queue(self) -> list:
        """Загружает очередь публикаций из файла."""
        try:
            if self._QUEUE_FILE.exists():
                return json.loads(self._QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return []

    def _save_queue(self):
        """Сохраняет очередь публикаций в файл."""
        try:
            self._QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._QUEUE_FILE.write_text(
                json.dumps(self._pending, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _parse_rss(self, xml_text: str, source_name: str) -> list:
        """Парсит RSS/Atom XML, возвращает список заголовков."""
        items = []
        try:
            root = ET.fromstring(xml_text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}

            # RSS 2.0
            for item in root.iter("item"):
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    title = title_el.text.strip()
                    if len(title) > 15:
                        items.append({"source": source_name, "title": title})
                if len(items) >= 3:
                    break

            # Atom 1.0 (если RSS не нашёл)
            if not items:
                for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
                    title_el = entry.find("{http://www.w3.org/2005/Atom}title")
                    if title_el is not None and title_el.text:
                        title = title_el.text.strip()
                        if len(title) > 15:
                            items.append({"source": source_name, "title": title})
                    if len(items) >= 3:
                        break
        except Exception:
            pass
        return items

    def fetch_headlines(self) -> list:
        """Собирает заголовки из RSS-лент. Возвращает до 9 новостей."""
        if not _REQUESTS_OK:
            return []

        headlines = []
        errors = []
        for source in self.RSS_SOURCES:
            if len(headlines) >= 9:
                break
            try:
                r = requests.get(
                    source["url"],
                    headers=self.HEADERS,
                    timeout=10,
                    allow_redirects=True,
                )
                if r.status_code == 200:
                    items = self._parse_rss(r.text, source["name"])
                    headlines.extend(items)
                else:
                    errors.append(f"{source['name']}: HTTP {r.status_code}")
            except Exception as e:
                errors.append(f"{source['name']}: {type(e).__name__}")
                continue

        self._last_errors = errors
        return headlines[:9]

    def generate_digest(self) -> str:
        """Генерирует AI-дайджест из свежих RSS-заголовков."""
        headlines = self.fetch_headlines()

        if not headlines:
            err_info = ""
            if hasattr(self, "_last_errors") and self._last_errors:
                err_info = "\n".join(f"  • {e}" for e in self._last_errors[:4])
                return (
                    "❌ AI-ДАЙДЖЕСТ: все источники недоступны.\n"
                    f"Причины:\n{err_info}\n"
                    "Проверь интернет-соединение или попробуй позже."
                )
            return "❌ Источники недоступны. Дайджест не сформирован."

        top = headlines[:5]
        date = time.strftime("%d.%m.%Y %H:%M")
        lines = [f"📰 AI-ДАЙДЖЕСТ от {date}", "━" * 28]
        for i, item in enumerate(top, 1):
            lines.append(f"\n{i}. 【{item['source']}】\n   {item['title']}")
        lines.append("\n" + "━" * 28)
        lines.append(f"📊 Источников опрошено: {len(self.RSS_SOURCES)} | Новостей: {len(headlines)}")
        lines.append("📡 Подготовлено Аргосом. Жду команды: опубликуй")
        post = "\n".join(lines)
        self._pending.append(post)
        self._save_queue()
        return post

    def publish(self) -> str:
        """Публикует пост через Telegram Bot API."""
        if not self._pending:
            return "📭 Нет постов в очереди. Сначала: дайджест"
        post = self._pending.pop(0)
        self._save_queue()

        if self._tg_token and self._tg_chatid and self._tg_token != "your_token_here":
            try:
                url = f"https://api.telegram.org/bot{self._tg_token}/sendMessage"
                resp = requests.post(
                    url,
                    json={
                        "chat_id": self._tg_chatid,
                        "text": post,
                        "parse_mode": "HTML",
                    },
                    timeout=10,
                )
                if resp.ok:
                    return f"✅ Пост опубликован в Telegram ({len(post)} символов)."
                else:
                    return f"⚠️ Telegram вернул ошибку: {resp.text[:200]}"
            except Exception as e:
                return f"❌ Ошибка публикации: {e}"
        else:
            print(f"[MEDIA-ARCHITECT]:\n{post}")
            return f"✅ Пост выведен в консоль ({len(post)} символов). Настрой TELEGRAM_BOT_TOKEN для публикации."

    def start_morning_loop(self, hour: int = 9):
        """Запускает ежедневную публикацию дайджеста в указанный час."""
        self._running = True

        def _loop():
            while self._running:
                if int(time.strftime("%H")) == hour:
                    self.generate_digest()
                    self.publish()
                    time.sleep(3600)
                time.sleep(60)

        threading.Thread(target=_loop, daemon=True).start()
        return f"Медиа-Архитектор активен. Дайджест в {hour:02d}:00 ежедневно."
