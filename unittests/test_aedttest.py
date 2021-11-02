from aedttest.aedttest import allocate_task
from aedttest.aedttest import allocate_task_within_node
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
