"""Golden tests that freeze the current SLURM submit/poll/collect behaviour.

They must pass unchanged before and after the scheduler-adapter refactor.
Nothing here needs a SLURM installation: ``subprocess.run`` is mocked.
"""

import json
import subprocess
from pathlib import Path
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from aedttest.clusters import slurm_collect
from aedttest.clusters import slurm_scheduler
from aedttest.clusters import slurm_submit

AEDT_ROOT = PurePosixPath("/ansys/AnsysEM")

LAUNCHER_PREFIX = [
    "/ansys/AnsysEM/schedulers/scripts/utils/ansysedt_launcher.sh",
    "/usr/bin/env",
    "ANSYSEM_GENERIC_MPI_WRAPPER=/ansys/AnsysEM/schedulers/scripts/utils/slurm_srun_wrapper.sh",
    "ANSYSEM_COMMON_PREFIX=/ansys/AnsysEM/common",
    "ANSOFT_PASS_DEBUG_ENV_TO_REMOTE_ENGINES=1",
    "srun",
    "--overcommit",
    "--export=ALL",
    "-n",
    "1",
    "-N",
    "1",
    "--cpu-bind=none",
    "--mem-per-cpu=0",
    "--overlap",
]
BATCH_OPTIONS = " 'HFSS/RAMLimitPercent'='90'"


@pytest.fixture
def sbatch_calls(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout=f"{1000 + len(calls)}\n", stderr="")

    monkeypatch.setattr(slurm_submit.subprocess, "run", fake_run)
    return calls


def submit_one(tmp_path, project_config, global_slurm_config=None):
    job_map = slurm_submit.submit_all(
        {"proj": project_config},
        aedt_root=AEDT_ROOT,
        working_dir="/work",
        job_map_path=tmp_path / "job_map.json",
        global_slurm_config=global_slurm_config,
    )
    return job_map


def test_hfss_sbatch_argv(tmp_path, sbatch_calls):
    submit_one(
        tmp_path,
        {
            "path": "/proj/p.aedt",
            "distribution": {"cores": 16},
            "slurm": {"partition": "part", "design_name": "HFSSDesign1"},
        },
    )

    assert sbatch_calls == [
        [
            "sbatch",
            "--parsable",
            "--export=NONE",
            "--chdir=/work",
            "--job-name=AEDT",
            "--partition=part",
            "--nodes=1",
            "--cpus-per-task=1",
            "--ntasks=16",
            "--exclusive",
            *LAUNCHER_PREFIX,
            "/ansys/AnsysEM/ansysedt",
            "-distributed",
            "-machinelist",
            "numcores=16",
            "-auto",
            "-monitor",
            "-ng",
            "-batchoptions",
            BATCH_OPTIONS,
            "-batchsolve",
            "HFSSDesign1",
            "/proj/p.aedt",
        ]
    ]


def test_icepak_without_auto_hpc_argv(tmp_path, sbatch_calls):
    submit_one(
        tmp_path,
        {
            "path": "/proj/card.aedt",
            "distribution": {"cores": 16},
            "slurm": {"partition": "part", "design_name": "IcepakDesign1", "auto_hpc": False},
        },
    )

    assert sbatch_calls == [
        [
            "sbatch",
            "--parsable",
            "--export=NONE",
            "--chdir=/work",
            "--job-name=AEDT",
            "--partition=part",
            "--nodes=1",
            "--cpus-per-task=1",
            "--ntasks=16",
            "--exclusive",
            *LAUNCHER_PREFIX,
            "/ansys/AnsysEM/ansysedt",
            "-monitor",
            "-ng",
            "-batchoptions",
            BATCH_OPTIONS,
            "-batchsolve",
            "IcepakDesign1",
            "/proj/card.aedt",
        ]
    ]


def test_multi_node_sbatch_argv(tmp_path, sbatch_calls):
    submit_one(
        tmp_path,
        {
            "path": "/proj/p.aedt",
            "distribution": {"cores": 16},
            "slurm": {
                "nodes": 2,
                "ntasks_per_node": 8,
                "machinelist_args": ["numnodes=2"],
                "extra_sbatch_args": ["--nodelist=node[01-02]"],
            },
        },
    )

    assert sbatch_calls == [
        [
            "sbatch",
            "--parsable",
            "--export=NONE",
            "--chdir=/work",
            "--job-name=AEDT",
            "--nodes=2",
            "--cpus-per-task=1",
            "--ntasks=16",
            "--ntasks-per-node=8",
            "--exclusive",
            "--nodelist=node[01-02]",
            *LAUNCHER_PREFIX,
            "/ansys/AnsysEM/ansysedt",
            "-distributed",
            "-machinelist",
            "numcores=16,numnodes=2",
            "-auto",
            "-monitor",
            "-ng",
            "-batchoptions",
            BATCH_OPTIONS,
            "-batchsolve",
            "/proj/p.aedt",
        ]
    ]


def test_mail_options_in_sbatch_argv(tmp_path, sbatch_calls):
    submit_one(
        tmp_path,
        {"path": "/proj/p.aedt", "distribution": {"cores": 4}},
        global_slurm_config={"mail_user": "user@example.com", "mail_type": "BEGIN,END,FAIL"},
    )

    argv = sbatch_calls[0]
    assert argv[:11] == [
        "sbatch",
        "--parsable",
        "--export=NONE",
        "--chdir=/work",
        "--job-name=AEDT",
        "--nodes=1",
        "--cpus-per-task=1",
        "--ntasks=4",
        "--exclusive",
        "--mail-user=user@example.com",
        "--mail-type=BEGIN,END,FAIL",
    ]
    assert argv[11:] == [*LAUNCHER_PREFIX, *argv[11 + len(LAUNCHER_PREFIX) :]]


def test_project_slurm_table_overrides_global(tmp_path, sbatch_calls):
    submit_one(
        tmp_path,
        {"path": "/proj/p.aedt", "distribution": {"cores": 4}, "slurm": {"partition": "project"}},
        global_slurm_config={"partition": "global", "job_name": "GLOBAL"},
    )

    argv = sbatch_calls[0]
    assert "--partition=project" in argv
    assert "--job-name=GLOBAL" in argv


def test_job_map_format_on_success(tmp_path, sbatch_calls):
    job_map = submit_one(tmp_path, {"path": "/proj/p.aedt", "distribution": {"cores": 4}})

    assert job_map == {"proj": {"job_id": "1001", "project_path": "/proj/p.aedt"}}
    assert json.loads((tmp_path / "job_map.json").read_text()) == job_map


def test_job_map_format_on_submit_failure(tmp_path, monkeypatch):
    def failing_run(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv, stderr="invalid partition")

    monkeypatch.setattr(slurm_submit.subprocess, "run", failing_run)

    job_map = submit_one(tmp_path, {"path": "/proj/p.aedt", "distribution": {"cores": 4}})

    assert job_map["proj"]["job_id"] is None
    assert job_map["proj"]["project_path"] == "/proj/p.aedt"
    assert "invalid partition" in job_map["proj"]["error"]
    assert json.loads((tmp_path / "job_map.json").read_text()) == job_map


def test_sbatch_parsable_with_cluster_suffix(monkeypatch):
    monkeypatch.setattr(
        slurm_submit.subprocess, "run", lambda argv, **kwargs: SimpleNamespace(stdout="4711;cluster\n", stderr="")
    )

    assert slurm_submit.submit_job(["sbatch"]) == "4711"


def test_sbatch_unparsable_output_raises(monkeypatch):
    monkeypatch.setattr(
        slurm_submit.subprocess, "run", lambda argv, **kwargs: SimpleNamespace(stdout="oops\n", stderr="")
    )

    with pytest.raises(OSError, match="Could not parse job ID"):
        slurm_submit.submit_job(["sbatch"])


def test_sbatch_missing_executable_raises(monkeypatch):
    def missing(argv, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(slurm_submit.subprocess, "run", missing)

    with pytest.raises(OSError, match="sbatch executable not found"):
        slurm_submit.submit_job(["sbatch"])


SACCT_OUTPUT = "\n".join(
    [
        "1|COMPLETED",
        "2|CANCELLED by 123",
        "3|TIMEOUT",
        "4|OUT_OF_MEMORY",
        "5|RUNNING",
        "6|PENDING",
        "7|FAILED",
        "",
    ]
)


@pytest.fixture
def sacct_calls(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(stdout=SACCT_OUTPUT, stderr="")

    monkeypatch.setattr(slurm_collect.subprocess, "run", fake_run)
    return calls


def test_get_job_states_parses_sacct(sacct_calls):
    states = slurm_collect.get_job_states(["1", "2", "3", "4", "5", "6", "7"])

    assert states == {
        "1": "COMPLETED",
        "2": "CANCELLED",
        "3": "TIMEOUT",
        "4": "OUT_OF_MEMORY",
        "5": "RUNNING",
        "6": "PENDING",
        "7": "FAILED",
    }
    assert sacct_calls == [["sacct", "-j", "1,2,3,4,5,6,7", "--format=JobID,State", "--noheader", "--parsable2", "-X"]]


def test_get_job_states_without_ids_does_not_call_sacct(sacct_calls):
    assert slurm_collect.get_job_states([]) == {}
    assert sacct_calls == []


def test_terminal_and_success_states(sacct_calls):
    states = slurm_collect.get_job_states(["1", "2", "3", "4", "5", "6", "7"])

    finished = {job_id for job_id, state in states.items() if state in slurm_collect.TERMINAL_STATES}
    assert finished == {"1", "2", "3", "4", "7"}
    assert slurm_collect.SUCCESS_STATES == {"COMPLETED"}


def test_sacct_missing_executable_raises(monkeypatch):
    def missing(argv, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(slurm_collect.subprocess, "run", missing)

    with pytest.raises(OSError, match="sacct executable not found"):
        slurm_collect.get_job_states(["1"])


def test_poll_until_done_sets_states(monkeypatch):
    responses = iter([{"10": "RUNNING", "11": "PENDING"}, {"10": "COMPLETED", "11": "TIMEOUT"}])
    monkeypatch.setattr(slurm_scheduler, "get_job_states", lambda ids: next(responses))
    monkeypatch.setattr(slurm_collect, "sleep", lambda seconds: None)
    job_map = {
        "ok": {"job_id": "10", "project_path": "/p/ok.aedt"},
        "slow": {"job_id": "11", "project_path": "/p/slow.aedt"},
        "never": {"job_id": None, "project_path": "/p/never.aedt", "error": "x"},
    }

    result = slurm_collect.poll_until_done(job_map, poll_interval=1)

    assert result["ok"]["state"] == "COMPLETED"
    assert result["slow"]["state"] == "TIMEOUT"
    assert result["never"]["state"] == "SUBMIT_FAILED"


def test_collect_all_skips_unsuccessful_jobs(monkeypatch):
    collected = []

    def fake_collect_project(project_path, version, log_file=None, debug=False, **kwargs):
        collected.append((project_path, version))
        return None

    monkeypatch.setattr(slurm_collect, "collect_project", fake_collect_project)
    job_map = {
        "ok": {"job_id": "1", "project_path": "/p/ok.aedt", "state": "COMPLETED"},
        "slow": {"job_id": "2", "project_path": "/p/slow.aedt", "state": "TIMEOUT"},
    }

    result = slurm_collect.collect_all(job_map, version="2026.1", log_dir=Path("logs"))

    assert collected == [("/p/ok.aedt", "2026.1")]
    assert result["ok"]["collect_error"] is None
    assert result["slow"]["collect_error"] == "Skipped, job state was TIMEOUT"


def test_job_map_roundtrip(tmp_path):
    job_map = {"p": {"job_id": "1", "project_path": "/p.aedt", "state": "COMPLETED", "collect_error": None}}
    path = tmp_path / "job_map.json"

    slurm_collect.save_job_map(job_map, path)

    assert slurm_collect.load_job_map(path) == job_map


def test_throttled_submission_never_exceeds_max_parallel(tmp_path, monkeypatch):
    events = []
    counter = iter(range(1, 100))

    def fake_run(argv, **kwargs):
        job_id = str(next(counter))
        events.append(("submit", job_id))
        return SimpleNamespace(stdout=f"{job_id}\n", stderr="")

    def fake_states(job_ids):
        events.append(("poll", sorted(job_ids)))
        return {job_id: "COMPLETED" for job_id in job_ids}

    monkeypatch.setattr(slurm_submit.subprocess, "run", fake_run)
    monkeypatch.setattr(slurm_scheduler, "get_job_states", fake_states)
    monkeypatch.setattr(slurm_submit, "sleep", lambda seconds: None)
    configs = {name: {"path": f"/p/{name}.aedt", "distribution": {"cores": 4}} for name in ("a", "b", "c")}

    job_map = slurm_submit.submit_all_throttled(
        configs,
        aedt_root=AEDT_ROOT,
        working_dir="/work",
        job_map_path=tmp_path / "job_map.json",
        max_parallel=1,
        poll_interval=1,
    )

    assert {name: info["job_id"] for name, info in job_map.items()} == {"a": "1", "b": "2", "c": "3"}
    assert events == [
        ("submit", "1"),
        ("poll", ["1"]),
        ("submit", "2"),
        ("poll", ["2"]),
        ("submit", "3"),
        ("poll", ["3"]),
    ]


def test_throttled_without_limit_submits_everything(tmp_path, sbatch_calls):
    configs = {name: {"path": f"/p/{name}.aedt", "distribution": {"cores": 4}} for name in ("a", "b")}

    job_map = slurm_submit.submit_all_throttled(
        configs,
        aedt_root=AEDT_ROOT,
        working_dir="/work",
        job_map_path=tmp_path / "job_map.json",
        max_parallel=None,
    )

    assert len(sbatch_calls) == 2
    assert set(job_map) == {"a", "b"}
