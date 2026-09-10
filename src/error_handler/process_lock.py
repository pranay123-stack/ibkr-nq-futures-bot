"""Process lock to prevent double-run of the strategy."""

import fcntl
import os
from datetime import datetime

from ..logger import get_logger
from ..timezone.market_calendar import get_default_tz

EST = get_default_tz()


class ProcessLock:
    """
    File-based lock to prevent running multiple instances of the strategy.
    Uses OS-level flock so the lock is automatically released on process death.
    """

    def __init__(self, lock_file: str = "/tmp/nq_6pm_strategy.lock"):
        self.lock_file = lock_file
        self._lock_fd = None
        self.logger = get_logger("ProcessLock")

    def acquire(self) -> bool:
        """Acquire the file lock, returning True on success."""
        try:
            self._lock_fd = open(self.lock_file, 'w')
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd.write(f"{os.getpid()}\n{datetime.now(EST).isoformat()}\n")
            self._lock_fd.flush()
            self.logger.info(f"Process lock acquired (PID: {os.getpid()})")
            return True
        except (IOError, OSError):
            self.logger.error(
                "CANNOT ACQUIRE LOCK - another instance may be running. "
                f"Lock file: {self.lock_file}"
            )
            return False

    def release(self):
        """Release the file lock and remove the lock file."""
        if self._lock_fd:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
                if os.path.exists(self.lock_file):
                    os.unlink(self.lock_file)
                self.logger.info("Process lock released")
            except Exception as e:
                self.logger.error(f"Error releasing lock: {e}")

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("Failed to acquire process lock")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False
