import os
import re
import json
import time
import html
import random
import requests
from urllib.parse import quote, unquote, urlparse, parse_qs
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from openai import OpenAI

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)

LAST_UPDATE_ID = None
HISTORY_FILE = "products_history.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "en-US,en;q=0.9",
}


# =========================
# STORAGE
# =========================

def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_history(data):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s\-]", "", text)
    return text


def product_signature(item: dict) -> str:
    return normalize_text(f"{item.get('product', '')}|{item.get('problem', '')}|{item.get('source', '')}")


# =========================
# TELEGRAM
# =========================

def tg_request(method: str, payload=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    try:
        r = requests.post(url, json=payload or {}, timeout=30)
        return r.json()
    except Exception as e:
        print(f"TELEGRAM ERROR {method}: {e}")
        return {"ok": False, "error": str(e)}


def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text[:4000],
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_request("sendMessage", payload)


def send_photo(chat_id, photo_url, caption, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption[:1000],
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return tg_request("sendPhoto", payload)


def answer_callback(callback_query_id, text="جارٍ جلب المزيد..."):
    return tg_request("answerCallbackQuery", {
        "callback_query_id": callback_query_id,
        "text": text
    })


def get_updates():
    global LAST_UPDATE_ID

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    if LAST_UPDATE_ID is not None:
        url += f"?offset={LAST_UPDATE_ID + 1}"

    try:
        response = requests.get(url, timeout=30).json()
        print("GET_UPDATES RESPONSE:", response if response.get("ok") is False else "ok")
        if not response.get("ok"):
            return []
        return response.get("result", [])
    except Exception as e:
        print("GET_UPDATES ERROR:", e)
        return []


def build_more_button(niche: str):
    return {
        "inline_keyboard": [
            [{"text": "🔄 المزيد من المنتجات", "callback_data": f"more::{niche[:50]}"}]
        ]
    }


# =========================
# WEB HELPERS
# =========================

def safe_get(url, timeout=25):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return ""


def extract_og_image(url):
    html_text = safe_get(url)
    if not html_text:
        return None

    soup = BeautifulSoup(html_text, "html.parser")

    selectors = [
        ('meta', {'property': 'og:image'}),
        ('meta', {'name': 'og:image'}),
        ('meta', {'property': 'twitter:image'}),
        ('meta', {'name': 'twitter:image'}),
    ]

    for tag, attrs in selectors:
        el = soup.find(tag, attrs=attrs)
        if el and el.get("content"):
            return el["content"]

    # fallback لبعض المواقع
    img = soup.find("img")
    if img and img.get("src"):
        return img["src"]

    return None


def duckduckgo_search_first_link(query: str):
    search_url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
    html_text = safe_get(search_url)
    if not html_text:
        return None

    soup = BeautifulSoup(html_text, "html.parser")

    candidates = soup.select("a.result__a")
    if not candidates:
        candidates = soup.select("a[href]")

    for a in candidates:
        href = a.get("href", "").strip()
        if not href:
            continue

        # بعض روابط DDG تكون redirect
        if "duckduckgo.com/l/?" in href:
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            uddg = qs.get("uddg")
            if uddg:
                href = unquote(uddg[0])

        if href.startswith("http"):
            return href

    return None


# =========================
# MARKET SIGNALS
# =========================

def get_reddit_signals(niche: str):
    url = f"https://www.reddit.com/search.json?q={quote(niche)}&sort=top&limit=10"
    try:
        data = requests.get(url, headers=HEADERS, timeout=20).json()
        posts = data.get("data", {}).get("children", [])
        out = []
        for p in posts:
            title = p.get("data", {}).get("title", "")
            if title:
                out.append(title)
        return out[:10]
    except Exception:
        return []


def get_amazon_signals(niche: str):
    q = quote(niche)
    url = f"https://www.amazon.com/s?k={q}"
    html_text = safe_get(url)
    if not html_text:
        return []

    soup = BeautifulSoup(html_text, "html.parser")
    texts = []
    for el in soup.select("h2 span, span.a-size-base-plus, span.a-size-medium"):
        txt = el.get_text(" ", strip=True)
        txt = re.sub(r"\s+", " ", txt)
        if 5 <= len(txt) <= 120 and txt not in texts:
            texts.append(txt)
    return texts[:12]


def get_tiktok_signals(niche: str):
    # إشارة عامة من Creative Center + niche text
    urls = [
        "https://ads.tiktok.com/business/creativecenter/inspiration/topads/pc/en",
        "https://ads.tiktok.com/business/creativecenter/inspiration/popular/keyword/pc/en",
    ]
    out = []
    for url in urls:
        html_text = safe_get(url)
        if not html_text:
            continue
        soup = BeautifulSoup(html_text, "html.parser")
        for el in soup.select("span, h1, h2, h3, a"):
            txt = el.get_text(" ", strip=True)
            txt = re.sub(r"\s+", " ", txt)
            if 5 <= len(txt) <= 90:
                low = normalize_text(txt)
                if niche.lower() in low or any(k in low for k in normalize_text(niche).split()):
                    if txt not in out:
                        out.append(txt)
    return out[:10]


def collect_signals(niche: str):
    signals = {
        "reddit": get_reddit_signals(niche),
        "amazon": get_amazon_signals(niche),
        "tiktok": get_tiktok_signals(niche),
    }
    return signals


# =========================
# SOURCE RESOLUTION
# =========================

def build_source_search_query(product_name: str, preferred_source: str):
    source_map = {
        "Alibaba": "site:alibaba.com",
        "1688": "site:1688.com",
        "Amazon": "site:amazon.com",
    }
    source_site = source_map.get(preferred_source, "site:alibaba.com")
    return f'{source_site} "{product_name}"'


def resolve_product_link(product_name: str, preferred_source: str):
    query = build_source_search_query(product_name, preferred_source)
    return duckduckgo_search_first_link(query)


def resolve_product_image(link: str):
    if not link:
        return None
    return extract_og_image(link)


# =========================
# AI CORE
# =========================

def expand_micro_niches(niche: str):
    prompt = f"""
أنت خبير تفكيك niches للإيكومرس.

النيش:
{niche}

أعطني فقط JSON بهذا الشكل:
[
  {{
    "micro_niche": "اسم دقيق",
    "problem_focus": "المشكلة",
    "audience_focus": "الجمهور"
  }}
]

المطلوب:
- أعطني 6 micro niches فقط
- تكون دقيقة جدًا
- قابلة للبيع
- لا تكتب أي شيء خارج JSON
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
        )
        text = response.choices[0].message.content.strip()
        return json.loads(text)
    except Exception:
        return []


def generate_product_candidates(niche: str, signals: dict):
    prompt = f"""
أنت Product Hunter محترف جدًا في الإيكومرس.

النيش الذي أرسله المستخدم:
{niche}

Micro-Niches المحتملة:
{expand_micro_niches(niche)}

إشارات السوق:
Reddit: {signals.get("reddit", [])}
Amazon: {signals.get("amazon", [])}
TikTok Ads: {signals.get("tiktok", [])}

أريد فقط JSON بهذا الشكل:
[
  {{
    "product": "اسم المنتج",
    "problem": "مشكلة قصيرة",
    "audience": "جمهور قصير",
    "source": "Alibaba أو 1688 أو Amazon",
    "alibaba_price_estimate": "2.5$",
    "winner_score": 87,
    "category_tag": "tag مختصر للتنويع"
  }}
]

القواعد:
- أعطني 18 منتجًا
- المنتجات يجب أن تكون physical products فقط
- لا تعطيني منتجات رقمية
- لا تعطيني كورسات أو خدمات
- winner_score من 1 إلى 100
- source فقط من: Alibaba أو 1688 أو Amazon
- السعر التقريبي يكون منخفضًا وواقعيًا غالبًا
- ركز على المنتجات التي تحل مشكلة بوضوح
- نوّع المنتجات
- لا تكرر نفس الفكرة
- لا تكتب أي شيء خارج JSON
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": "أنت خبير اكتشاف منتجات Winner وتعيد JSON فقط."},
                      {"role": "user", "content": prompt}],
            temperature=0.6,
        )
        text = response.choices[0].message.content.strip()
        return json.loads(text)
    except Exception as e:
        print("AI PRODUCT ERROR:", e)
        return []


# =========================
# WINNER FILTER + DIVERSITY
# =========================

def is_valid_price(price_text: str) -> bool:
    return bool(re.search(r"\d", price_text or ""))


def internal_winner_filter(item: dict):
    score = int(item.get("winner_score", 0) or 0)
    product = item.get("product", "").strip()
    problem = item.get("problem", "").strip()
    audience = item.get("audience", "").strip()
    source = item.get("source", "").strip()
    price = item.get("alibaba_price_estimate", "").strip()

    if len(product) < 4:
        return False
    if len(problem) < 6:
        return False
    if len(audience) < 3:
        return False
    if source not in ["Alibaba", "1688", "Amazon"]:
        return False
    if score < 72:
        return False
    if not is_valid_price(price):
        return False

    return True


def diversify_products(items):
    # لا نريد أكثر من 2 من نفس الـ category_tag
    bucket_counts = {}
    diversified = []

    for item in sorted(items, key=lambda x: int(x.get("winner_score", 0) or 0), reverse=True):
        tag = normalize_text(item.get("category_tag", "general")) or "general"
        bucket_counts.setdefault(tag, 0)

        if bucket_counts[tag] >= 2:
            continue

        diversified.append(item)
        bucket_counts[tag] += 1

    return diversified


def filter_new_products(niche: str, products: list, history: dict):
    niche_key = normalize_text(niche)
    sent_before = history.get(niche_key, [])
    sent_signatures = set(sent_before)

    cleaned = []
    for item in products:
        if not isinstance(item, dict):
            continue
        if not internal_winner_filter(item):
            continue

        sig = product_signature(item)
        if sig in sent_signatures:
            continue

        item["_signature"] = sig
        cleaned.append(item)

    cleaned = diversify_products(cleaned)
    return cleaned[:10]


# =========================
# TELEGRAM OUTPUT
# =========================

def format_product_caption(index: int, item: dict, link: str):
    return f"""🔥 Product #{index}

📦 المنتج
{item.get('product', 'غير محدد')}

⚠️ المشكلة
{item.get('problem', 'غير محدد')}

🎯 الجمهور
{item.get('audience', 'غير محدد')}

🏷 المصدر
{item.get('source', 'غير محدد')}

💰 سعر Alibaba التقريبي
{item.get('alibaba_price_estimate', 'غير محدد')}

🔗 رابط المنتج
{link or 'غير متوفر'}

━━━━━━━━━━━━
"""


def send_products(chat_id, niche: str, products: list, history: dict):
    if not products:
        send_message(chat_id, "❌ لم أجد منتجات قوية كفاية داخل هذا النيش. جرّب نيشًا أدق.")
        return

    send_message(
        chat_id,
        f"🔎 تم العثور على {len(products)} منتجات قوية داخل النيش:\n{niche}",
        reply_markup=build_more_button(niche)
    )

    niche_key = normalize_text(niche)
    history.setdefault(niche_key, [])

    for idx, item in enumerate(products, start=1):
        link = resolve_product_link(item["product"], item.get("source", "Alibaba"))
        image = resolve_product_image(link) if link else None

        caption = format_product_caption(idx, item, link)

        if image:
            send_photo(chat_id, image, caption)
        else:
            send_message(chat_id, caption)

        history[niche_key].append(item["_signature"])

    save_history(history)


# =========================
# MAIN HANDLERS
# =========================

def process_niche(chat_id, niche_text: str):
    niche = niche_text.strip()
    history = load_history()

    send_message(chat_id, "🔎 جاري البحث عن أفضل المنتجات داخل هذا النيش...")

    signals = collect_signals(niche)
    candidates = generate_product_candidates(niche, signals)
    filtered = filter_new_products(niche, candidates, history)

    # fallback بسيط إذا AI أعاد قليل جدًا
    if len(filtered) < 3:
        extra = [
            {
                "product": f"{niche} tool",
                "problem": f"حل مشكلة شائعة داخل نيش {niche}",
                "audience": f"المهتمون بـ {niche}",
                "source": random.choice(["Alibaba", "1688", "Amazon"]),
                "alibaba_price_estimate": random.choice(["1.8$", "2.5$", "3.2$", "4.1$"]),
                "winner_score": 78,
                "category_tag": "fallback",
                "_signature": normalize_text(f"{niche}|fallback|{random.randint(1, 99999)}")
            }
            for _ in range(10)
        ]
        filtered.extend(extra)
        filtered = filtered[:10]

    send_products(chat_id, niche, filtered, history)


def handle_callback(callback):
    callback_id = callback["id"]
    data = callback.get("data", "")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")

    if not chat_id:
        return

    answer_callback(callback_id, "جارٍ جلب المزيد...")

    if data.startswith("more::"):
        niche = data.split("more::", 1)[1].strip()
        process_niche(chat_id, niche)


def main():
    global LAST_UPDATE_ID

    print("Product Finder Bot Started")

    while True:
        updates = get_updates()

        for update in updates:
            LAST_UPDATE_ID = update["update_id"]

            if "callback_query" in update:
                handle_callback(update["callback_query"])
                continue

            if "message" not in update:
                continue

            message = update["message"]
            chat_id = message.get("chat", {}).get("id")
            text = message.get("text", "").strip()

            if not chat_id or not text:
                continue

            print("NICHE RECEIVED:", text)

            if text.startswith("/start"):
                send_message(
                    chat_id,
                    "👋 أرسل لي نيشًا مثل:\n\nالجمال والعناية بالبشرة\nأو\nتنظيم المطبخ\nأو\nمنتجات الحيوانات الأليفة"
                )
                continue

            process_niche(chat_id, text)

        time.sleep(2)


if __name__ == "__main__":
    main()