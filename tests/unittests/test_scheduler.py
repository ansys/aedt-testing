from pathlib import PurePosixPath

import pytest

from aedttest.clusters import slurm_collect
from aedttest.clusters.scheduler import JobSpec
from aedttest.clusters.scheduler import JobState
from aedttest.clusters.scheduler import Scheduler
from aedttest.clusters.scheduler import get_scheduler
from aedttest.clusters.slurm_scheduler import SlurmScheduler


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("COMPLETED", JobState.SUCCEEDED),
        ("PENDING", JobState.PENDING),
        ("CONFIGURING", JobState.PENDING),
        ("RUNNING", JobState.RUNNING),
        ("COMPLETING", JobState.RUNNING),
        ("CANCELLED", JobState.CANCELLED),
        ("CANCELLED by 123", JobState.CANCELLED),
        ("FAILED", JobState.FAILED),
        ("TIMEOUT", JobState.FAILED),
        ("OUT_OF_MEMORY", JobState.FAILED),
        ("NODE_FAIL", JobState.FAILED),
        ("SUBMIT_FAILED", JobState.UNKNOWN),
        ("SOMETHING_NEW", JobState.UNKNOWN),
        ("", JobState.UNKNOWN),
        (None, JobState.UNKNOWN),
    ],
)
def test_slurm_state_normalization(raw, expected):
    assert SlurmScheduler().normalize(raw) is expected


def test_slurm_terminal_states_match_existing_sets():
    scheduler = SlurmScheduler()

    for state in slurm_collect.TERMINAL_STATES:
        assert scheduler.is_terminal(state), state
    for state in ("PENDING", "RUNNING", "COMPLETING", "SUSPENDED", "REQUEUED"):
        assert not scheduler.is_terminal(state), state
    for state in slurm_collect.SUCCESS_STATES:
        assert scheduler.is_success(state)
    assert not scheduler.is_success("TIMEOUT")


def test_unknown_state_is_not_terminal():
    assert not SlurmScheduler().is_terminal("SOMETHING_NEW")
    assert not SlurmScheduler().is_terminal(None)


def test_factory_defaults_to_slurm():
    assert isinstance(get_scheduler(), SlurmScheduler)
    assert isinstance(get_scheduler({}), SlurmScheduler)
    assert isinstance(get_scheduler({"scheduler": {}}), SlurmScheduler)


def test_factory_selects_slurm_case_insensitive():
    assert isinstance(get_scheduler({"scheduler": {"type": "SLURM"}}), SlurmScheduler)


def test_factory_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown scheduler type 'lsf'"):
        get_scheduler({"scheduler": {"type": "lsf"}})


def test_scheduler_is_abstract():
    with pytest.raises(TypeError):
        Scheduler()


def test_job_spec_defaults():
    spec = JobSpec(name="p", project_path="/p.aedt", working_dir="/work", cores=4, aedt_root=PurePosixPath("/ansys"))

    assert spec.options == {}
    other = JobSpec(name="q", project_path="/q.aedt", working_dir="/work", cores=4, aedt_root=PurePosixPath("/ansys"))
    assert other.options is not spec.options


def test_slurm_scheduler_delegates_to_existing_functions(monkeypatch):
    from aedttest.clusters import slurm_scheduler

    submitted = {}

    def fake_submit_project(**kwargs):
        submitted.update(kwargs)
        return "42"

    monkeypatch.setattr(slurm_scheduler, "submit_project", fake_submit_project)
    monkeypatch.setattr(slurm_scheduler, "get_job_states", lambda ids: {job_id: "RUNNING" for job_id in ids})
    spec = JobSpec(
        name="p",
        project_path="/p.aedt",
        working_dir="/work",
        cores=8,
        aedt_root=PurePosixPath("/ansys"),
        options={"partition": "x", "auto_hpc": False},
    )

    scheduler = SlurmScheduler()

    assert scheduler.submit(spec) == "42"
    assert submitted["auto"] is False
    assert submitted["slurm_config"] == {"partition": "x", "auto_hpc": False}
    assert submitted["cores"] == 8
    assert scheduler.get_states(["1"]) == {"1": "RUNNING"}
