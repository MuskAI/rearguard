import os
import json
import signal
import socket
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from imagedetection.views import developer_platform


WORKER_CONCURRENCY = max(
    1,
    min(8, int(os.environ.get("REALGUARD_DEVELOPER_WORKER_CONCURRENCY", "2"))),
)
WEB_WORKER_CONCURRENCY = max(
    1,
    min(WORKER_CONCURRENCY, int(os.environ.get("REALGUARD_WEB_WORKER_CONCURRENCY", "1"))),
)
DEVELOPER_WORKER_CONCURRENCY = max(
    1,
    min(WORKER_CONCURRENCY, int(os.environ.get("REALGUARD_API_WORKER_CONCURRENCY", "1"))),
)
POLL_INTERVAL_SECONDS = max(
    0.2,
    float(os.environ.get("REALGUARD_DEVELOPER_WORKER_POLL_SECONDS", "1")),
)
MAINTENANCE_INTERVAL_SECONDS = max(
    5,
    int(os.environ.get("REALGUARD_DEVELOPER_WORKER_MAINTENANCE_SECONDS", "15")),
)
HEARTBEAT_PATH = Path(
    os.environ.get("REALGUARD_DEVELOPER_WORKER_HEARTBEAT", "/opt/realguard-data/developer-worker.heartbeat")
)


class DeveloperDetectionWorker:
    def __init__(self):
        hostname = socket.gethostname().split(".", 1)[0]
        self.instance = f"{hostname}-{os.getpid()}"
        self.stop_event = threading.Event()
        self.executor = ThreadPoolExecutor(
            max_workers=WORKER_CONCURRENCY,
            thread_name_prefix="developer-detection",
        )
        self.futures = {}
        self.last_maintenance = 0.0
        self.prefer_web_task = True
        self.health = {
            "claimHealthy": False,
            "maintenanceHealthy": False,
            "lastClaimCheckAt": 0.0,
            "lastMaintenanceSuccessAt": 0.0,
            "lastCompletionAt": 0.0,
            "lastError": "worker_starting",
        }

    def request_stop(self, signum, _frame):
        print(f"[DEVELOPER WORKER] received signal {signum}; draining active tasks", flush=True)
        self.stop_event.set()

    def _collect_finished(self):
        finished = {future for future in self.futures if future.done()}
        for future in finished:
            self.futures.pop(future, None)
            try:
                future.result()
                self.health["lastCompletionAt"] = time.time()
            except Exception as exc:
                # The task lease remains authoritative. An unhandled execution
                # failure is reclaimed after expiry by this or another worker.
                print(f"[DEVELOPER WORKER ERROR] task execution escaped: {exc}", flush=True)
                self.health["lastError"] = f"task_execution:{type(exc).__name__}"

    def _run_maintenance(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_maintenance < MAINTENANCE_INTERVAL_SECONDS:
            return
        try:
            result = developer_platform._run_worker_maintenance()
            self.health["maintenanceHealthy"] = True
            self.health["lastMaintenanceSuccessAt"] = time.time()
            if any(result.values()):
                print(f"[DEVELOPER WORKER] maintenance {result}", flush=True)
        except Exception as exc:
            print(f"[DEVELOPER WORKER ERROR] maintenance deferred: {exc}", flush=True)
            self.health["maintenanceHealthy"] = False
            self.health["lastError"] = f"maintenance:{type(exc).__name__}"
        self.last_maintenance = now

    def _heartbeat(self):
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = HEARTBEAT_PATH.with_name(f".{HEARTBEAT_PATH.name}.{os.getpid()}")
        payload = {
            "instance": self.instance,
            "timestamp": time.time(),
            "activeTasks": len(self.futures),
            "capacity": WORKER_CONCURRENCY,
            "activeWebTasks": sum(1 for kind in self.futures.values() if kind == "web"),
            "activeDeveloperTasks": sum(
                1 for kind in self.futures.values() if kind == "developer"
            ),
            **self.health,
        }
        temporary.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="ascii")
        os.replace(temporary, HEARTBEAT_PATH)

    def _prefer_web_for_next_claim(self):
        active_kinds = set(self.futures.values())
        if WORKER_CONCURRENCY >= 2 and "web" in active_kinds and "developer" not in active_kinds:
            return False
        if WORKER_CONCURRENCY >= 2 and "developer" in active_kinds and "web" not in active_kinds:
            return True
        return self.prefer_web_task

    def run(self):
        developer_platform._ensure_spool_root()
        developer_platform._ensure_web_spool_root()
        if not developer_platform._ensure_developer_platform_tables():
            raise RuntimeError("developer platform schema is unavailable")
        self._run_maintenance(force=True)
        self._heartbeat()
        print(
            f"[DEVELOPER WORKER] started instance={self.instance} concurrency={WORKER_CONCURRENCY}",
            flush=True,
        )
        try:
            while not self.stop_event.is_set():
                self._heartbeat()
                self._collect_finished()
                self._run_maintenance()
                if not developer_platform._detector_ready_for_worker():
                    self.health["claimHealthy"] = False
                    self.health["lastError"] = "detector_not_ready"
                    self.stop_event.wait(POLL_INTERVAL_SECONDS)
                    continue
                claimed_any = False
                while len(self.futures) < WORKER_CONCURRENCY and not self.stop_event.is_set():
                    prefer_web = self._prefer_web_for_next_claim()
                    claimers = (
                        (
                            ("web", developer_platform._claim_next_web_task, developer_platform._run_web_detection_job),
                            ("developer", developer_platform._claim_next_task, developer_platform._run_openapi_job),
                        )
                        if prefer_web
                        else (
                            ("developer", developer_platform._claim_next_task, developer_platform._run_openapi_job),
                            ("web", developer_platform._claim_next_web_task, developer_platform._run_web_detection_job),
                        )
                    )
                    claimed = None
                    claim_errors = []
                    for kind, claim, execute in claimers:
                        kind_limit = (
                            WEB_WORKER_CONCURRENCY
                            if kind == "web"
                            else DEVELOPER_WORKER_CONCURRENCY
                        )
                        if sum(1 for active in self.futures.values() if active == kind) >= kind_limit:
                            continue
                        try:
                            task = claim(self.instance)
                        except Exception as exc:
                            print(f"[DEVELOPER WORKER ERROR] {kind} claim deferred: {exc}", flush=True)
                            claim_errors.append(f"{kind}:{type(exc).__name__}")
                            continue
                        if task:
                            claimed = (kind, task, execute)
                            break
                    if claim_errors:
                        self.health["claimHealthy"] = False
                        self.health["lastError"] = "claim:" + ",".join(claim_errors)
                    else:
                        self.health["claimHealthy"] = True
                        self.health["lastClaimCheckAt"] = time.time()
                        self.health["lastError"] = ""
                    if not claimed:
                        break
                    claimed_any = True
                    kind, task, execute = claimed
                    self.prefer_web_task = kind != "web"
                    self.futures[self.executor.submit(execute, task)] = kind
                if not claimed_any:
                    self.stop_event.wait(POLL_INTERVAL_SECONDS)
        finally:
            self.executor.shutdown(wait=True, cancel_futures=False)
            self._collect_finished()
            print("[DEVELOPER WORKER] stopped cleanly", flush=True)


def main():
    worker = DeveloperDetectionWorker()
    signal.signal(signal.SIGTERM, worker.request_stop)
    signal.signal(signal.SIGINT, worker.request_stop)
    worker.run()


if __name__ == "__main__":
    main()
