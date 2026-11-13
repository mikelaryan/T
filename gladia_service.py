"""
سرویس ترجمه و گروه‌بندی کلمات (مبتنی بر خروجی Gladia)
"""
import json
import time
import logging
import requests
import config

logger = logging.getLogger(__name__)

# ===========================================
# ۱. گروه‌بندی کلمات (Chunking)
# ===========================================
def group_words_into_chunks(words: list[dict], max_words: int = 4, pause_threshold: float = 0.6) -> list[dict]:
    chunks = []
    current_chunk = []
    chunk_start = 0.0
    chunk_end = 0.0

    for i, w in enumerate(words):
        word = w.get("word", "").strip()
        if not word:
            continue

        if not current_chunk:
            chunk_start = w["start"]

        current_chunk.append(word)
        chunk_end = w["end"]

        is_max_words = len(current_chunk) >= max_words
        is_long_pause = False
        
        if i + 1 < len(words):
            next_word_start = words[i + 1]["start"]
            if next_word_start - chunk_end > pause_threshold:
                is_long_pause = True

        is_punctuation = word.endswith(('.', ',', '?', '!'))

        if is_max_words or is_long_pause or is_punctuation or (i == len(words) - 1):
            chunks.append({
                "text_en": " ".join(current_chunk),
                "start": chunk_start,
                "end": chunk_end
            })
            current_chunk = []

    return chunks

# ===========================================
# ۲. ترجمه گروه‌ها با هوش مصنوعی
# ===========================================
def translate_chunks(chunks: list[dict]) -> list[dict]:
    if not chunks:
        return []

    english_texts = [c["text_en"] for c in chunks]

    # 👈 پرامپت سخت‌گیرانه برای اجبار به ۱ به ۱ بودن تعداد جملات
    prompt = f"""
تو یک مترجم حرفه‌ای هستی. من به تو یک لیست از جملات انگلیسی می‌دهم.
وظیفه تو این است که هر جمله را به فارسی روان، محاوره‌ای و کوتاه (مناسب زیرنویس) ترجمه کنی.

⚠️ قانون بسیار مهم:
تو باید دقیقاً به همان تعداد جمله‌ای که در لیست می‌بینی، ترجمه تولید کنی. 
حق نداری جملات را با هم ادغام کنی، حق نداری جمله‌ای را حذف کنی. باید آرایه خروجی دقیقاً {len(english_texts)} عضو داشته باشد.

لیست جملات انگلیسی:
{json.dumps(english_texts, ensure_ascii=False)}

خروجی را فقط به صورت یک آرایه JSON از رشته‌های فارسی برگردان (مثل ["ترجمه ۱", "ترجمه ۲"]). بدون هیچ متن اضافه.
""".strip()

    translated_texts = []

    # تلاش با GapGPT (پولی)
    if getattr(config, "GAPGPT_API_KEY", None) and "YOUR_GAPGPT" not in config.GAPGPT_API_KEY:
        try:
            logger.info("💰 ترجمه متنی با GapGPT...")
            headers = {"Authorization": f"Bearer {config.GAPGPT_API_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": "gemini-3.8-flash",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}
            }
            response = requests.post("https://api.gapgpt.app/v1/chat/completions", headers=headers, json=payload, timeout=120)
            if response.status_code == 200:
                content = response.json()["choices"][0]["message"]["content"]
                translated_texts = _parse_translation_response(content)
        except Exception as e:
            logger.error(f"خطای ترجمه GapGPT: {e}")

    # تلاش با گوگل رایگان
    if not translated_texts:
        try:
            logger.info("🌐 ترجمه متنی با گوگل (رایگان)...")
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
            headers = {"Content-Type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY}
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.2, "response_mime_type": "application/json"}
            }
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code == 200:
                text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                translated_texts = _parse_translation_response(text)
        except Exception as e:
            logger.error(f"خطای ترجمه گوگل: {e}")

    # 👈 اگر ترجمه ناموفق بود یا تعدادش همخوانی نداشت
    if not translated_texts or len(translated_texts) != len(chunks):
        logger.warning(f"⚠️ ترجمه ناموفق یا تعداد نامساوی (انتظار: {len(chunks)}, دریافتی: {len(translated_texts)}). استفاده از متن انگلیسی.")
        return chunks

    for i in range(len(chunks)):
        chunks[i]["text_fa"] = translated_texts[i]

    return chunks

def _parse_translation_response(content: str) -> list:
    """استخراج فوق مقاوم آرایه ترجمه از پاسخ هوش مصنوعی"""
    cleaned = content.replace("\n", "").replace("\r", "")
    
    # اگر پاسخ در یک آبجکت پیچیده بود (مثل {"translations": [...]})
    try:
        parsed = json.loads(cleaned, strict=False)
        if isinstance(parsed, dict):
            # اولین لیستی که پیدا کنیم رو برمی‌داریم
            for val in parsed.values():
                if isinstance(val, list):
                    return val
        elif isinstance(parsed, list):
            return parsed
    except:
        pass

    # جستجوی مستقیم برای آکولاد (اگر آبجکت نبود)
    start, end = cleaned.find('['), cleaned.rfind(']')
    if start != -1 and end != -1:
        try:
            return json.loads(cleaned[start:end+1], strict=False)
        except:
            pass
            
    logger.error(f"خطای خواندن JSON ترجمه. متن خام: {content[:200]}")
    return []

# ===========================================
# ۳. تبدیل نهایی به فرمت زیرنویس
# ===========================================
def _format_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def build_final_subtitles(words: list[dict]) -> dict:
    if not words:
        raise ValueError("لیست کلمات خالی است.")

    chunks = group_words_into_chunks(words)
    logger.info(f"[Pipeline] {len(words)} کلمه به {len(chunks)} خط زیرنویس تبدیل شد.")

    chunks = translate_chunks(chunks)

    final_subtitles = []
    for i, c in enumerate(chunks):
        final_subtitles.append({
            "line_index": i + 1,
            "start_time": _format_time(c["start"]),
            "end_time": _format_time(c["end"]),
            "text": c.get("text_fa", c["text_en"])
        })

    return {
        "subtitles": final_subtitles,
        "used_model": "Gladia + AI",
        "api_source": "Gladia (Sync) + Translation"
    }
