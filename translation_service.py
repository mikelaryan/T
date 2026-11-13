"""
سرویس ترجمه و گروه‌بندی کلمات (با تشخیص هوشمند ادغام خطوط)
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
                "id": len(chunks) + 1, # 👈 اضافه کردن شماره خط
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

    final_chunks = []
    batch_size = 10

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i+batch_size]
        logger.info(f"[Translation] در حال ترجمه بسته {i//batch_size + 1} (شامل {len(batch)} خط)...")
        
        batch_fa = _translate_single_batch(batch)
        
        if batch_fa:
            final_chunks.extend(batch_fa)
        else:
            logger.warning(f"⚠️ بسته {i//batch_size + 1} ناموفق بود. استفاده از انگلیسی برای این بسته.")
            # اگر ترجمه کل بسته شکست خورد، عیناً همون خطوط انگلیسی رو با همون زمان‌ها برمی‌گردونیم
            for c in batch:
                final_chunks.append({
                    "text_fa": c["text_en"],
                    "start": c["start"],
                    "end": c["end"]
                })

    return final_chunks

def _translate_single_batch(batch_chunks: list[dict]) -> list:
    expected_len = len(batch_chunks)
    
    # 👈 ساخت لیست ورودی برای هوش مصنوعی (متن + شماره خط)
    input_data = [{"id": c["id"], "text": c["text_en"]} for c in batch_chunks]
    
    prompt = f"""
تو یک مترجم حرفه‌ای هستی. من به تو یک لیست از جملات انگلیسی (همراه با شماره خط id) می‌دهم.
وظیفه تو ترجمه روان و محاوره‌ای این جملات به فارسی است.

⚠️ قوانین بسیار مهم:
۱. اگر دیدی دو یا چند جمله انگلیسی از نظر معنایی به هم وصل هستند و در فارسی بهتر است با هم بیایند، می‌توانی آن‌ها را با هم ترکیب کنی.
۲. اگر جمله‌ای را ترکیب کردی، باید در خروجی آرایه‌ای از id های آن جملات را قرار دهی.
۳. زمان‌بندی کلمات به عهده سیستم است، تو فقط باید id ها را درست بدهی.

لیست جملات انگلیسی:
{json.dumps(input_data, ensure_ascii=False)}

خروجی را دقیقاً به این فرمت JSON برگردان:
{{
  "translations": [
    {{"text": "ترجمه روان جمله اول", "ids": [1, 2]}},
    {{"text": "ترجمه جمله سوم", "ids": [3]}}
  ]
}}
""".strip()

    # --- تلاش با GapGPT ---
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
                if res:
                    return _calculate_final_times(res, batch_chunks)
            else:
                logger.error(f"❌ خطای GapGPT: HTTP {response.status_code}")
        except Exception as e:
            logger.error(f"❌ خطای شبکه GapGPT: {e}")

    # --- تلاش با گوگل رایگان ---
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "response_mime_type": "application/json"}
    }

    logger.info(f"[Translation] English text sent to 3.5-lite: {input_data}")

    for attempt in range(2):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            
            if response.status_code == 200:
                text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                res = _parse_translation_response(text)
                if res:
                    return _calculate_final_times(res, batch_chunks)
                else:
                    logger.warning(f"⚠️ فرمت خروجی گوگل نامعتبر بود: {text[:300]}")
                    break
                    
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

def _calculate_final_times(translations: list, original_chunks: list) -> list:
    """
    این تابع نابغه‌ی کد ماست!
    زمان شروع را از اولین id و زمان پایان را از آخرین id برمی‌دارد.
    """
    chunk_dict = {c["id"]: c for c in original_chunks}
    final_result = []

    for item in translations:
        text_fa = item.get("text", "").strip()
        ids = item.get("ids", [])
        
        if not text_fa or not ids:
            continue

        try:
            # پیدا کردن زمان شروع و پایان با توجه به id ها
            starts = [chunk_dict[i]["start"] for i in ids if i in chunk_dict]
            ends = [chunk_dict[i]["end"] for i in ids if i in chunk_dict]
            
            if starts and ends:
                final_result.append({
                    "text_fa": text_fa,
                    "start": min(starts),
                    "end": max(ends)
                })
        except Exception as e:
            logger.error(f"خطا در محاسبه زمان برای ids {ids}: {e}")

    return final_result

def _parse_translation_response(content: str) -> list:
    cleaned = content.replace("\n", "").replace("\r", "")
    try:
        parsed = json.loads(cleaned, strict=False)
        if isinstance(parsed, dict) and "translations" in parsed:
            return parsed["translations"]
        elif isinstance(parsed, list):
            return parsed
    except:
        pass

    start, end = cleaned.find('{'), cleaned.rfind('}')
    if start != -1 and end != -1:
        try:
            parsed = json.loads(cleaned[start:end+1], strict=False)
            if isinstance(parsed, dict) and "translations" in parsed:
                return parsed["translations"]
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

    # گرفتن خطوط ترجمه شده و محاسبه زمان‌ها
    translated_chunks = translate_chunks(chunks)

    final_subtitles = []
    for i, c in enumerate(translated_chunks):
        final_subtitles.append({
            "line_index": i + 1,
            "start_time": _format_time(c["start"]),
            "end_time": _format_time(c["end"]),
            "text": c["text_fa"]
        })

    return {
        "subtitles": final_subtitles,
        "used_model": "Gladia + AI (Smart Merge)",
        "api_source": "Gladia (Sync) + Google 3.5-lite"
    }
