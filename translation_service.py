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
# ۲. ترجمه گروه‌ها
# ===========================================
def translate_chunks(chunks: list[dict]) -> list[dict]:
    if not chunks:
        return []

    english_texts = [c["text_en"] for c in chunks]
    translated_texts = []
    batch_size = 10

    for i in range(0, len(english_texts), batch_size):
        batch_en = english_texts[i:i+batch_size]
        logger.info(f"[Translation] در حال ترجمه بسته {i//batch_size + 1} (شامل {len(batch_en)} خط)...")
        
        batch_fa = _translate_single_batch(batch_en)
        
        if batch_fa and len(batch_fa) == len(batch_en):
            translated_texts.extend(batch_fa)
        else:
            logger.warning(f"⚠️ بسته {i//batch_size + 1} ناموفق بود. استفاده از انگلیسی برای این بسته.")
            translated_texts.extend(batch_en)

    for i in range(len(chunks)):
        chunks[i]["text_fa"] = translated_texts[i]

    return chunks

def _translate_single_batch(english_texts: list[str]) -> list:
    expected_len = len(english_texts)
    
    prompt = f"""
تو یک مترجم حرفه‌ای هستی. من به تو یک لیست از {expected_len} جمله انگلیسی می‌دهم.
وظیفه تو ترجمه هر جمله به فارسی روان، محاوره‌ای و کوتاه (مناسب زیرنویس) است.

⚠️ قوانین بسیار مهم:
۱. تو باید دقیقاً {expected_len} جمله ترجمه کنی. 
۲. حق ادغام کردن یا حذف کردن جملات را نداری.
۳. لیست خروجی باید دقیقاً {expected_len} عضو داشته باشد.

لیست جملات انگلیسی:
{json.dumps(english_texts, ensure_ascii=False)}

خروجی را دقیقاً به این فرمت JSON برگردان:
{{
  "translations": [
    "ترجمه جمله ۱",
    "ترجمه جمله ۲"
  ]
}}
""".strip()

    # تلاش با GapGPT (پولی)
    if getattr(config, "GAPGPT_API_KEY", None) and "YOUR_GAPGPT" not in config.GAPGPT_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {config.GAPGPT_API_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": "gemini-3.8-flash",
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"}
            }
            response = requests.post("https://api.gapgpt.app/v1/chat/completions", headers=headers, json=payload, timeout=60)
            
            if response.status_code == 403:
                logger.warning("⚠️ اعتبار GapGPT تمام شده یا کلید مسدود است. سوییچ به گوگل رایگان.")
            elif response.status_code == 200:
                content = response.json()["choices"][0]["message"]["content"]
                res = _parse_translation_response(content)
                if len(res) == expected_len:
                    return res
                else:
                    logger.warning(f"⚠️ GapGPT تعداد نامعتبری خط ({len(res)} از {expected_len}) برگرداند.")
            else:
                logger.error(f"❌ خطای GapGPT: HTTP {response.status_code}")
        except Exception as e:
            logger.error(f"❌ خطای شبکه GapGPT: {e}")

    # تلاش با گوگل رایگان (مدل 3.5-flash-lite)
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "response_mime_type": "application/json"}
    }

    # 👈 پرینت دقیق متن انگلیسی که به گوگل ارسال می‌شود
    logger.info(f"[Translation] English text sent to 3.5-lite: {english_texts}")

    # ۲ بار تلاش می‌کنیم (اگر 503 داد)
    for attempt in range(2):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            
            if response.status_code == 200:
                text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                res = _parse_translation_response(text)
                if len(res) == expected_len:
                    return res
                else:
                    logger.warning(f"⚠️ گوگل تعداد خطوط را رعایت نکرد. انتظار: {expected_len} | دریافتی: {len(res)}")
                    logger.warning(f"پاسخ خام گوگل: {text[:300]}")
                    break # اگر فرمت خراب بود، تلاش مجدد فایده ندارد
                    
            elif response.status_code == 503:
                logger.warning(f"⚠️ گوگل 503 داد (تلاش {attempt+1}/2). ۲ ثانیه صبر و تلاش مجدد...")
                time.sleep(2)
                continue
            else:
                logger.error(f"❌ خطای HTTP گوگل: {response.status_code} - {response.text[:200]}")
                break
                
        except Exception as e:
            logger.error(f"❌ خطای شبکه گوگل (تلاش {attempt+1}/2): {e}")
            time.sleep(2)

    return []

def _parse_translation_response(content: str) -> list:
    """استخراج فوق مقاوم آرایه ترجمه"""
    cleaned = content.replace("\n", "").replace("\r", "")
    try:
        parsed = json.loads(cleaned, strict=False)
        if isinstance(parsed, dict) and "translations" in parsed:
            return parsed["translations"]
        elif isinstance(parsed, list):
            return parsed
    except:
        pass

    start, end = cleaned.find('['), cleaned.rfind(']')
    if start != -1 and end != -1:
        try:
            return json.loads(cleaned[start:end+1], strict=False)
        except:
            pass
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
        "used_model": "Gladia + AI (Batched)",
        "api_source": "Gladia (Sync) + Google 3.5-lite"
    }
