import os
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
        self.futures = set()
        self.last_maintenance = 0.0

    def request_stop(self, signum, _frame):
        print(f"[DEVELOPER WORKER] received signal {signum}; draining active tasks", flush=True)
        self.stop_event.set()

    def _collect_finished(self):
        finished = {future for future in self.futures if future.done()}
        self.futures.difference_update(finished)
        for future in finished:
            try:
                future.result()
            except Exception as exc:
                # The task lease remains authoritative. An unhandled execution
                # failure is reclaimed after expiry by this or another worker.
                print(f"[DEVELOPER WORKER ERROR] task execution escaped: {exc}", flush=True)

    def _run_maintenance(self, force=False):
        now = time.monotonic()
        if not force and now - self.last_maintenance < MAINTENANCE_INTERVAL_SECONDS:
            return
        try:
            result = developer_platform._run_worker_maintenance()
            if any(result.values()):
                print(f"[DEVELOPER WORKER] maintenance {result}", flush=True)
        except Exception as exc:
            print(f"[DEVELOPER WORKER ERROR] maintenance deferred: {exc}", flush=True)
        self.last_maintenance = now

    def _heartbeat(self):
        HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
        temporary = HEARTBEAT_PATH.with_name(f".{HEARTBEAT_PATH.name}.{os.getpid()}")
        temporary.write_text(f"{self.instance} {time.time():.3f}\n", encoding="ascii")
        os.replace(temporary, HEARTBEAT_PATH)

    def run(self):
        developer_platform._ensure_spool_root()
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
                claimed_any = False
                while len(self.futures) < WORKER_CONCURRENCY and not self.stop_event.is_set():
                    try:
                        task = developer_platform._claim_next_task(self.instance)
                    except Exception as exc:
                        print(f"[DEVELOPER WORKER ERROR] claim deferred: {exc}", flush=True)
                        break
                    if not task:
                        break
                    claimed_any = True
                    self.futures.add(
                        self.executor.submit(developer_platform._run_openapi_job, task)
                    )
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
