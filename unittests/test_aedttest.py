from aedttest.aedttest import allocate_task
from aedttest.aedttest import allocate_task_within_node
from aedttest.aedttest import machines_dict
from aedttest.clusters.job_hosts import get_job_machines


def test_allocate_task_single():
    job_machines = get_job_machines("host1:15,host2:10")
    machines_dict.clear()
    for machine in job_machines:
        machines_dict[machine.hostname] = machine.cores

    allocated_machines = allocate_task({"cores": 17})
    assert allocated_machines == {"host1": {"cores": 15, "tasks": 1}, "host2": {"cores": 2, "tasks": 1}}

    allocated_machines = allocate_task({"cores": 15})
    assert allocated_machines == {"host1": {"cores": 15, "tasks": 1}}

    allocated_machines = allocate_task({"cores": 2})
    assert allocated_machines == {"host1": {"cores": 2, "tasks": 1}}

    allocated_machines = allocate_task({"cores": 25})
    assert allocated_machines == {"host1": {"cores": 15, "tasks": 1}, "host2": {"cores": 10, "tasks": 1}}

    allocated_machines = allocate_task({"cores": 26})
    assert not allocated_machines


def test_allocate_task_within_node():
    job_machines = get_job_machines("host1:15,host2:10")
    machines_dict.clear()
    for machine in job_machines:
        machines_dict[machine.hostname] = machine.cores

    allocated_machines = allocate_task_within_node({"cores": 17})
    assert not allocated_machines

    allocated_machines = allocate_task_within_node({"cores": 15})
    assert allocated_machines == {"host1": {"cores": 15, "tasks": 1}}

    allocated_machines = allocate_task_within_node({"cores": 2})
    assert allocated_machines == {"host1": {"cores": 2, "tasks": 1}}
