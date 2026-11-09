"""Scheduler-neutral interface for submitting and polling batch jobs.

Only submit and poll talk to the cluster scheduler. Everything else in the
pipeline (matrix expansion, project isolation, extraction, reporting) is
scheduler independent and only relies on the normalized ``JobState``.

The raw scheduler state string (for example ``"COMPLETED"``) is what is stored
in ``job_map.json``; ``Scheduler.normalize`` maps it to a ``JobState``.
"""

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

DEFAULT_SCHEDULER = "slurm"


class JobState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


TERMINAL_JOB_STATES = {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}


@dataclass
class JobSpec:
    """Everything needed to submit one AEDT batch job.

    ``options`` holds the merged scheduler specific settings of the project
    (defaults, ``site.toml`` and the project's own table); its keys are defined
    by the concrete scheduler.
    """

    name: str
    project_path: str
    working_dir: str
    cores: int
    aedt_root: Path
    options: Dict[str, Any] = field(default_factory=dict)


class Scheduler(ABC):
    """Submit jobs and query their state on one kind of cluster scheduler."""

    @abstractmethod
    def submit(self, spec: JobSpec) -> str:
        """Submit one job and return its scheduler job ID.

        Raises ``OSError`` if the submission fails.
        """

    @abstractmethod
    def get_states(self, job_ids: List[str]) -> Dict[str, str]:
        """Return the raw scheduler state per job ID.

        Job IDs that are not visible to the scheduler yet are omitted.
        Raises ``OSError`` if the scheduler cannot be queried.
        """

    @abstractmethod
    def normalize(self, raw_state: Optional[str]) -> JobState:
        """Map a raw scheduler state string to a ``JobState``."""

    def is_terminal(self, raw_state: Optional[str]) -> bool:
        return self.normalize(raw_state) in TERMINAL_JOB_STATES

    def is_success(self, raw_state: Optional[str]) -> bool:
        return self.normalize(raw_state) is JobState.SUCCEEDED


def get_scheduler(site_config: Optional[Dict[str, Any]] = None) -> Scheduler:
    """Create the scheduler selected by ``[site.scheduler] type`` (default ``"slurm"``)."""
    scheduler_type = str(((site_config or {}).get("scheduler") or {}).get("type", DEFAULT_SCHEDULER)).lower()

    if scheduler_type == "slurm":
        from aedttest.clusters.slurm_scheduler import SlurmScheduler

        return SlurmScheduler()

    raise ValueError(f"Unknown scheduler type {scheduler_type!r}. Supported: 'slurm'")
