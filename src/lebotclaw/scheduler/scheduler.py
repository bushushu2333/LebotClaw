"""CronScheduler：APScheduler BackgroundScheduler 封装 + CRUD。

三入口对齐 inno-agent：① jobs 页 / /api/jobs CRUD；② run_now 立即跑；
③ 后台守护到点自动触发。one_shot 任务触发后自动禁用。
"""
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from lebotclaw.scheduler.job_runner import execute
from lebotclaw.scheduler.job_store import JobStore
from lebotclaw.scheduler.models import Job


class CronScheduler:
    def __init__(self, runtime):
        self.rt = runtime
        scfg = runtime.config.get("scheduler", {})
        self.store = JobStore(
            scfg.get("jobs_file", "~/.lebotclaw/jobs.json"),
            scfg.get("runs_file", "~/.lebotclaw/runs.jsonl"),
        )
        self._sched = BackgroundScheduler(timezone=scfg.get("tz", "Asia/Shanghai"))
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    # ── 生命周期 ──

    def start(self):
        for job in self.store.load_jobs():
            self._jobs[job.id] = job
            if job.enabled:
                self._add_aps_job(job)
        self._sched.start()

    def shutdown(self, wait: bool = False):
        try:
            self._sched.shutdown(wait=wait)
        except Exception:  # noqa: BLE001
            pass

    # ── 内部 ──

    def _add_aps_job(self, job: Job):
        trigger = CronTrigger.from_crontab(job.cron)
        self._sched.add_job(
            self._run_one, trigger=trigger, args=[job.id],
            id=job.id, replace_existing=True,
        )

    def _run_one(self, job_id: str):
        with self._lock:
            job = self._jobs.get(job_id)
        if not job or not job.enabled:
            return
        try:
            run = execute(self.rt, job)
        except Exception:  # noqa: BLE001
            run = {"status": "error"}
        self.store.append_run(run)
        if job.one_shot:
            job.enabled = False
            try:
                self._sched.remove_job(job_id)
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self.store.save_jobs(list(self._jobs.values()))

    # ── CRUD ──

    def add(self, job: Job):
        with self._lock:
            self._jobs[job.id] = job
            self.store.save_jobs(list(self._jobs.values()))
        if job.enabled:
            self._add_aps_job(job)

    def remove(self, job_id: str):
        with self._lock:
            self._jobs.pop(job_id, None)
            self.store.save_jobs(list(self._jobs.values()))
        try:
            self._sched.remove_job(job_id)
        except Exception:  # noqa: BLE001
            pass

    def list_jobs(self) -> list:
        with self._lock:
            return list(self._jobs.values())

    def get(self, job_id: str):
        with self._lock:
            return self._jobs.get(job_id)

    def run_now(self, job_id: str) -> dict:
        job = self.get(job_id)
        if not job:
            return {"status": "not_found"}
        run = execute(self.rt, job)
        self.store.append_run(run)
        if job.one_shot:
            job.enabled = False
        with self._lock:
            self.store.save_jobs(list(self._jobs.values()))
        return run
