"""
پردازش ورودی کاربر:
- اگر لینک بود → دانلود ویدیو از اینستاگرام
- اگر فایل بود → ذخیره‌ی ویدیوی ارسالی تلگرام
- سپس استخراج صدا با ffmpeg
"""

import os
import uuid
import logging
import subprocess
import tempfile
from pathlib import Path

import yt_dlp

logger = logging.getLogger(__name__)

WORK_DIR = Path("downloads")

# خطای اختصاصی برای ویدیوهای طولانی
class VideoTooLongError(Exception):
    pass


def _ensure_work_dir():
    WORK_DIR.mkdir(parents=True, exist_ok=True)


def _get_duration(path: str) -> float:
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
            stdin=subprocess.DEVNULL,
        )
        return float(probe.stdout.strip())
    except Exception as e:
        logger.warning(f"Failed to get video duration: {e}")
        return 0.0


# ===========================================
# ۱. دانلود ویدیو از اینستاگرام
# ===========================================
def download_instagram_video(url: str, user_id: int, progress=None) -> str:
    _ensure_work_dir()
    
    unique_id = uuid.uuid4().hex[:8]
    out_template = str(WORK_DIR / f"video_{user_id}_{unique_id}_%(id)s.%(ext)s")

    def _hook(d):
        if progress is None:
            return
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes", 0)
            speed = d.get("speed") or 0
            if total > 0:
                pct = downloaded / total * 100
                speed_mb = speed / (1024 * 1024) if speed else 0
                extra = f"📦 {downloaded/1024/1024:.1f} MB / {total/1024/1024:.1f} MB\n⚡ {speed_mb:.2f} MB/s"
                progress.update(stage=0, sub_text="در حال دانلود از اینستاگرام...", pct=pct, extra=extra)
        elif d.get("status") == "finished":
            progress.update(stage=0, sub_text="دانلود کامل شد", pct=100, force=True)

    ydl_opts = {
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "noplaylist": True,
        "progress_hooks": [_hook],
    }

    # چک کردن کوکی
    for cookie_name in ("cookies.txt", "cookie.txt"):
        if os.path.exists(cookie_name):
            ydl_opts["cookiefile"] = cookie_name
            logger.info(f"Cookie file found and used: {cookie_name}")
            break
    else:
        logger.warning("No cookie file (cookies.txt) found! Instagram might block the request.")

    if progress:
        progress.update(stage=0, sub_text="در حال دریافت اطلاعات لینک...", pct=0, force=True)

    logger.info(f"Starting yt-dlp process for URL: {url}")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 👈 مرحله اول: چک کردن مدت زمان بدون دانلود
            info = ydl.extract_info(url, download=False)
            duration = info.get("duration", 0)
            
            if duration and duration > 120:
                raise VideoTooLongError(f"مدت زمان ویدیو {int(duration)} ثانیه است. برای تست، فقط ویدیوهای زیر ۹۰ ثانیه مجاز هستند.")
            
            # 👈 مرحله دوم: دانلود ویدیو
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
    except VideoTooLongError:
        raise
    except Exception as e:
        logger.error(f"Download error: {e}")
        raise

    return filepath


# ===========================================
# ۲. ذخیره‌ی ویدیوی ارسالی تلگرام
# ===========================================
def save_telegram_video(bot, message, user_id: int, progress=None) -> str:
    _ensure_work_dir()

    if message.video:
        # 👈 چک کردن محدودیت ۲ دقیقه برای فایل‌های آپلود شده در تلگرام
        if message.video.duration and message.video.duration > 120:
            raise VideoTooLongError(f"مدت زمان ویدیو {message.video.duration} ثانیه است. حداکثر زمان مجاز، ۲ دقیقه است.")
            
        file_id = message.video.file_id
        file_size = message.video.file_size or 0
    elif message.document:
        file_id = message.document.file_id
        file_size = message.document.file_size or 0
    else:
        raise ValueError("The message does not contain a video.")

        # 👈 چک کردن محدودیت ۲۰ مگابایتی دانلود تلگرام
    if file_size > 20 * 1024 * 1024:
        raise ValueError("TELEGRAM_FILE_TOO_LARGE")

    if progress:
        size_mb = file_size / (1024 * 1024) if file_size else 0
        progress.update(
            stage=0,
            sub_text=f"در حال دریافت از تلگرام (حجم: {size_mb:.1f} MB)...",
            pct=50,
            force=True
        )

    logger.info(f"Downloading file from Telegram (file_id={file_id})...")
    file_info = bot.get_file(file_id)
    downloaded = bot.download_file(file_info.file_path)
    logger.info("Telegram file download complete.")

    # پسوند بر اساس mime
    ext = ".mp4"
    if message.document and message.document.mime_type:
        mime = message.document.mime_type
        if "quicktime" in mime:
            ext = ".mov"
        elif "webm" in mime:
            ext = ".webm"

    # 👈 نام‌گذاری امن با uuid برای جلوگیری از خطای latin-1
    unique_id = uuid.uuid4().hex[:8]
    out_path = WORK_DIR / f"video_{user_id}_{unique_id}{ext}"
    
    with open(out_path, "wb") as f:
        f.write(downloaded)

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    logger.info(f"File saved to disk: {out_path} ({size_mb:.2f} MB)")
    
    if progress:
        progress.update(stage=0, sub_text="دانلود از تلگرام کامل شد", pct=100, force=True)
        
    return str(out_path)


# ===========================================
# ۳. استخراج صدا از ویدیو
# ===========================================
def extract_audio(video_path: str, user_id: int, progress=None) -> str:
    _ensure_work_dir()
    audio_path = str(WORK_DIR / f"audio_{user_id}.wav")
    duration = _get_duration(video_path)
    logger.info(f"Starting audio extraction. Video duration: {duration}s")

    if progress:
        progress.update(stage=1, sub_text="در حال استخراج صدا...", pct=0, force=True)

    cmd = [
        "ffmpeg", "-y", "-nostdin",
        "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "44100", "-ac", "2",
        "-progress", "pipe:1",
        "-loglevel", "error",
        audio_path,
    ]

    logger.info("Executing ffmpeg command for audio extraction...")
    err_tmp = tempfile.NamedTemporaryFile(mode="w+", delete=False, suffix=".log", encoding="utf-8")
    err_path = err_tmp.name
    err_tmp.close()

    try:
        with open(err_path, "w", encoding="utf-8") as stderr_f:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=stderr_f,
                stdin=subprocess.DEVNULL, text=True, bufsize=1,
            )
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_us=") and duration > 0 and progress:
                    try:
                        us = int(line.split("=", 1)[1])
                        pct = min(100.0, us / (duration * 1_000_000) * 100)
                        progress.update(stage=1, sub_text="در حال جدا کردن صدا...", pct=pct)
                    except ValueError:
                        pass
            proc.wait()
            logger.info("Audio extraction finished.")
    except Exception as e:
        logger.error(f"ffmpeg execution error: {e}")
        raise
    finally:
        if os.path.exists(err_path):
            os.unlink(err_path)

    return audio_path


# ===========================================
# ۴. تابع اصلی
# ===========================================
def process_incoming(bot, message, is_link: bool, progress=None) -> tuple[str, str]:
    user_id = message.from_user.id

    if is_link:
        logger.info("Input is an Instagram link.")
        video_path = download_instagram_video(message.text.strip(), user_id, progress)
    else:
        logger.info("Input is a Telegram video file.")
        video_path = save_telegram_video(bot, message, user_id, progress)

    audio_path = extract_audio(video_path, user_id, progress)
    return video_path, audio_path


# ===========================================
# ۵. ذخیره‌ی زیرنویس به فرمت SRT
# ===========================================
def save_srt(subtitles: list, srt_path: str) -> str:
    with open(srt_path, "w", encoding="utf-8") as f:
        for sub in subtitles:
            idx = sub.get("line_index", 0)
            start = sub.get("start_time", "00:00:00,000")
            end = sub.get("end_time", "00:00:00,000")
            text = (sub.get("text") or "").strip()
            f.write(f"{idx}\n{start} --> {end}\n{text}\n\n")

    size = os.path.getsize(srt_path)
    logger.info(f"SRT file saved: {srt_path} ({size} bytes)")
    return srt_path
