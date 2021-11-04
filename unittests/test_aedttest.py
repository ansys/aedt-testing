from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from aedttest.aedttest import allocate_task
from aedttest.aedttest import allocate_task_within_node
from aedttest.aedttest import copy_single_file
from aedttest.clusters.job_hosts import get_job_machines


def test_allocate_task_single():
    job_machines = get_job_machines("host1:15,host2:10")
    machines_dict = {machine.hostname: machine.cores for machine in job_machines}

    allocated_machines = allocate_task({"cores": 17}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 15, "tasks": 1}, "host2": {"cores": 2, "tasks": 1}}

    allocated_machines = allocate_task({"cores": 15}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 15, "tasks": 1}}

    allocated_machines = allocate_task({"cores": 2}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 2, "tasks": 1}}

    allocated_machines = allocate_task({"cores": 25}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 15, "tasks": 1}, "host2": {"cores": 10, "tasks": 1}}

    allocated_machines = allocate_task({"cores": 26}, machines_dict)
    assert not allocated_machines


def test_allocate_task_multiple():
    """
    Test all possible scenarios of job splitting. Every test is critical
    Returns:

    """
    job_machines = get_job_machines("host1:20,host2:10")
    machines_dict = {machine.hostname: machine.cores for machine in job_machines}

    allocated_machines = allocate_task({"cores": 16, "parametric_tasks": 2}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 16, "tasks": 2}}

    allocated_machines = allocate_task({"cores": 24, "parametric_tasks": 2}, machines_dict)
    assert not allocated_machines

    allocated_machines = allocate_task({"cores": 10, "parametric_tasks": 2}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 10, "tasks": 2}}

    allocated_machines = allocate_task({"cores": 25, "parametric_tasks": 5}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 20, "tasks": 4}, "host2": {"cores": 5, "tasks": 1}}

    job_machines = get_job_machines("host1:10,host2:15")
    machines_dict.clear()
    for machine in job_machines:
        machines_dict[machine.hostname] = machine.cores

    allocated_machines = allocate_task({"cores": 26, "parametric_tasks": 2}, machines_dict)
    assert not allocated_machines

    allocated_machines = allocate_task({"cores": 10, "parametric_tasks": 2}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 10, "tasks": 2}}

    allocated_machines = allocate_task({"cores": 25, "parametric_tasks": 5}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 10, "tasks": 2}, "host2": {"cores": 15, "tasks": 3}}


def test_allocate_task_within_node():
    job_machines = get_job_machines("host1:15,host2:10")
    machines_dict = {machine.hostname: machine.cores for machine in job_machines}

    allocated_machines = allocate_task_within_node({"cores": 17}, machines_dict)
    assert not allocated_machines

    allocated_machines = allocate_task_within_node({"cores": 15}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 15, "tasks": 1}}

    allocated_machines = allocate_task_within_node({"cores": 2}, machines_dict)
    assert allocated_machines == {"host1": {"cores": 2, "tasks": 1}}


@pytest.mark.parametrize("tmp_dir", (None, Path.cwd()))
def test_copy_single_file_file(tmp_dir):
    with TemporaryDirectory(prefix="src_", dir=tmp_dir) as src_tmp_dir:
        file = Path(src_tmp_dir, "tmp_file.txt")
        file_no = Path(src_tmp_dir, "not_copy.txt")
        file.touch()
        file_no.touch()
        with TemporaryDirectory(prefix="dst_") as dst_tmp_dir:
            copy_single_file(str(file), dst_tmp_dir)

            assert Path(dst_tmp_dir, file.name).is_file()
            assert Path(dst_tmp_dir, file.name).exists()
            assert not Path(dst_tmp_dir, file_no.name).exists()


@pytest.mark.parametrize("tmp_dir", (None, Path.cwd()))
def test_copy_single_file_folder(tmp_dir):
    with TemporaryDirectory(prefix="src_", dir=tmp_dir) as src_tmp_dir:
        folder = Path(src_tmp_dir, "tmp_folder")
        folder.mkdir()
        file = folder / "tmp_file.txt"
        file2 = folder / "tmp_file2.txt"
        file.touch()
        file2.touch()
        with TemporaryDirectory(prefix="dst_") as dst_tmp_dir:
            copy_single_file(str(folder), dst_tmp_dir)

            assert Path(dst_tmp_dir, "tmp_folder", file.name).is_file()
            assert Path(dst_tmp_dir, "tmp_folder", file.name).exists()
            assert Path(dst_tmp_dir, "tmp_folder", file2.name).exists()
