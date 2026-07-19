"""Job 持久化：jobs.json（任务定义）+ runs.jsonl（运行记录）。"""
import json
from pathlib import Path
from typing import List

from lebotclaw.scheduler.models import Job


class JobStore:
    def __init__(self, jobs_file: str, runs_file: str):
        self.jobs_file = Path(jobs_file).expanduser()
        self.runs_file = Path(runs_file).expanduser()
        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)

    def load_jobs(self) -> List[Job]:
        if not self.jobs_file.exists():
            return []
        try:
            data = json.loads(self.jobs_file.read_text(encoding="utf-8"))
            return [Job.from_dict(j) for j in data]
        except Exception:  # noqa: BLE001
            return []

    def save_jobs(self, jobs: List[Job]) -> None:
        self.jobs_file.write_text(
            json.dumps([j.to_dict() for j in jobs], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def append_run(self, run: dict) -> None:
        with open(self.runs_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(run, ensure_ascii=False) + "\n")
