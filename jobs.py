"""Background jobs: long work (AI calls, pricing, publishing) runs in a thread
and reports progress to the `jobs` table, which the UI polls."""
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from db import get_db, now


class Job:
    def __init__(self, job_id):
        self.id = job_id
        self._lock = threading.Lock()

    def log(self, line, advance=0):
        with self._lock:
            conn = get_db()
            conn.execute(
                "UPDATE jobs SET log = log || ? || char(10), done = done + ? WHERE id = ?",
                (line, advance, self.id),
            )
            conn.commit()
            conn.close()

    def set_total(self, total):
        conn = get_db()
        conn.execute("UPDATE jobs SET total = ? WHERE id = ?", (total, self.id))
        conn.commit()
        conn.close()

    def map(self, fn, items, workers=4, label=str):
        """Run fn(item) over items in parallel; each finish advances progress.
        Exceptions are logged per item, never abort the job."""
        results = {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(fn, it): it for it in items}
            for fut in as_completed(futures):
                it = futures[fut]
                try:
                    results[id(it)] = fut.result()
                except Exception as e:  # noqa: BLE001 — report and continue
                    self.log(f"✗ {label(it)}: {type(e).__name__}: {str(e)[:200]}", advance=1)
                    results[id(it)] = None
        return results


def start(kind, label, fn, total=0, batch_id=None):
    """Start fn(job) in a background thread. Returns the job id."""
    conn = get_db()
    job_id = conn.execute(
        "INSERT INTO jobs (kind, label, batch_id, total) VALUES (?, ?, ?, ?)",
        (kind, label, batch_id, total),
    ).lastrowid
    conn.commit()
    conn.close()
    job = Job(job_id)

    def run():
        status = "done"
        try:
            fn(job)
        except Exception as e:  # noqa: BLE001
            status = "error"
            job.log(f"Job failed: {type(e).__name__}: {e}")
            traceback.print_exc()
        conn = get_db()
        conn.execute("UPDATE jobs SET status = ?, finished_at = ? WHERE id = ?", (status, now(), job_id))
        conn.commit()
        conn.close()

    threading.Thread(target=run, daemon=True).start()
    return job_id


def get(job_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def recent(limit=10):
    conn = get_db()
    rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
