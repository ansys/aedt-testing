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


class FakeBatchScheduler(Scheduler):
    """A non-SLURM scheduler with its own state names."""

    def __init__(self):
        self.submitted = []

    def submit(self, spec):
        self.submitted.append(spec)
        return str(len(self.submitted))

    def get_states(self, job_ids):
        return {job_id: "DONE" if job_id == "1" else "ABORTED" for job_id in job_ids}

    def normalize(self, raw_state):
        return {"DONE": JobState.SUCCEEDED, "ABORTED": JobState.FAILED}.get(raw_state, JobState.UNKNOWN)


def test_pipeline_functions_work_with_a_non_slurm_scheduler(tmp_path, monkeypatch):
    from aedttest.clusters import slurm_submit

    monkeypatch.setattr(slurm_submit, "sleep", lambda seconds: None)
    monkeypatch.setattr(slurm_collect, "sleep", lambda seconds: None)
    collected = []
    monkeypatch.setattr(
        slurm_collect, "collect_project", lambda project_path, version, **kwargs: collected.append(project_path)
    )
    scheduler = FakeBatchScheduler()
    configs = {name: {"path": f"/p/{name}.aedt", "distribution": {"cores": 4}} for name in ("a", "b")}

    job_map = slurm_submit.submit_all_throttled(
        configs,
        aedt_root=PurePosixPath("/ansys"),
        working_dir="/work",
        job_map_path=tmp_path / "job_map.json",
        max_parallel=1,
        poll_interval=1,
        scheduler=scheduler,
    )
    job_map = slurm_collect.poll_until_done(job_map, poll_interval=1, scheduler=scheduler)
    job_map = slurm_collect.collect_all(job_map, version="2026.1", scheduler=scheduler)

    assert [spec.name for spec in scheduler.submitted] == ["a", "b"]
    assert {name: info["state"] for name, info in job_map.items()} == {"a": "DONE", "b": "ABORTED"}
    assert collected == ["/p/a.aedt"]
    assert job_map["b"]["collect_error"] == "Skipped, job state was ABORTED"
