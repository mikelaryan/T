"""
مدیریت صف درخواست‌ها برای جلوگیری از کرش کردن سرور
"""
import queue
import logging

logger = logging.getLogger(__name__)

task_queue = queue.Queue()

def worker(process_func, mark_free_func):
    logger.info("[QUEUE] Worker thread started.")
    while True:
        # 👈 حالا ۴ مقدار از صف خوانده می‌شود
        user_id, message, is_link, is_regen = task_queue.get()
        
        try:
            logger.info(f"[QUEUE] Picked up task for user {user_id}.")
            # پاس دادن is_regen به تابع پردازش
            process_func(message, is_link, is_regen)
        except Exception as e:
            logger.error(f"[QUEUE] Error processing task for user {user_id}: {e}")
        finally:
            mark_free_func(user_id)
            task_queue.task_done()

def start_worker(process_func, mark_free_func):
    import threading
    t = threading.Thread(target=worker, args=(process_func, mark_free_func), daemon=True)
    t.start()

def add_to_queue(user_id: int, message, is_link: bool, is_regen: bool = False) -> int:
    task_queue.put((user_id, message, is_link, is_regen))
    return task_queue.qsize()
