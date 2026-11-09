"""SLURM implementation of the scheduler interface.

Thin adapter over ``slurm_submit`` and ``slurm_collect``; the command building
and ``sacct`` parsing are unchanged.
"""

from typing import Dict
from typing import List
from typing import Optional

from aedttest.clusters.scheduler import JobSpec
from aedttest.clusters.scheduler import JobState
from aedttest.clusters.scheduler import Scheduler
from aedttest.clusters.slurm_collect import get_job_states
from aedttest.clusters.slurm_submit import submit_project

_STATE_MAP = {
    "PENDING": JobState.PENDING,
    "CONFIGURING": JobState.PENDING,
    "REQUEUED": JobState.PENDING,
    "RESV_DEL_HOLD": JobState.PENDING,
    "REQUEUE_FED": JobState.PENDING,
    "REQUEUE_HOLD": JobState.PENDING,
    "RUNNING": JobState.RUNNING,
    "COMPLETING": JobState.RUNNING,
    "SUSPENDED": JobState.RUNNING,
    "STOPPED": JobState.RUNNING,
    "RESIZING": JobState.RUNNING,
    "SIGNALING": JobState.RUNNING,
    "STAGE_OUT": JobState.RUNNING,
    "COMPLETED": JobState.SUCCEEDED,
    "CANCELLED": JobState.CANCELLED,
    "REVOKED": JobState.CANCELLED,
    "FAILED": JobState.FAILED,
    "TIMEOUT": JobState.FAILED,
    "NODE_FAIL": JobState.FAILED,
    "OUT_OF_MEMORY": JobState.FAILED,
    "BOOT_FAIL": JobState.FAILED,
    "DEADLINE": JobState.FAILED,
    "PREEMPTED": JobState.FAILED,
    "SPECIAL_EXIT": JobState.FAILED,
}


class SlurmScheduler(Scheduler):
    def submit(self, spec: JobSpec) -> str:
        return submit_project(
            project_name=spec.name,
            project_path=spec.project_path,
            cores=spec.cores,
            aedt_root=spec.aedt_root,
            working_dir=spec.working_dir,
            slurm_config=spec.options,
            auto=spec.options.get("auto_hpc", True),
        )

    def get_states(self, job_ids: List[str]) -> Dict[str, str]:
        return get_job_states(job_ids)

    def normalize(self, raw_state: Optional[str]) -> JobState:
        if not raw_state:
            return JobState.UNKNOWN
        return _STATE_MAP.get(raw_state.split()[0].upper(), JobState.UNKNOWN)
