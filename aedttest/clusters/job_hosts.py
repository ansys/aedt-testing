import csv
import os
import re
from collections import namedtuple
from socket import gethostname

hostinfo = namedtuple("hostinfo", ("hostname", "cores"))


def get_job_machines(custom_input=None):
    """Function to get all available hostnames and cores for the submitted job.

    Schedulers use different environment variables to share available hosts for the job:

    * UGE (SGE): PE_HOSTFILE
    * LSF: LSB_MCPU_HOSTS
    * PBS: PBS_NODEFILE
    * SLURM: SLURM_JOB_NODELIST
    * Azure Batch: CCP_NODES

    Parameters
    ----------
    custom_input : str, optional
        If the scheduler is not in the list of supported schedulers, then
        the user needs to provide a string input with the available
        hosts and cores. Format: ``"host1:15,host2:10"``.

    Returns
    -------
    machines : tuple[hostinfo]
        All available machines.

    """
    if custom_input is not None:
        return parse_custom_input(custom_input)

    if "PE_HOSTFILE" in os.environ:
        host_file_name = os.environ.get("PE_HOSTFILE")
        machine_list = parse_hosts_sge(host_file_name)
    elif "LSB_MCPU_HOSTS" in os.environ:
        host_list = os.environ.get("LSB_MCPU_HOSTS")
        machine_list = parse_hosts_lsf(host_list)
    elif "PBS_NODEFILE" in os.environ:
        host_file_name = os.environ.get("PBS_NODEFILE")
        machine_list = parse_hosts_pbs(host_file_name)
    elif "SLURM_JOB_NODELIST" in os.environ:
        host_list = os.environ.get("SLURM_JOB_NODELIST")
        machine_list = parse_hosts_slurm(host_list)
    elif "CCP_NODES" in os.environ:
        host_list = os.environ.get("CCP_NODES")
        machine_list = parse_hosts_ccs(host_list)
    else:
        # we assume that not run on cluster environment
        machine_list = (hostinfo(gethostname(), os.cpu_count()),)

    return tuple(machine_list)


def parse_custom_input(custom_input: str):
    """
    Parse custom input string.

    Parameters
    ----------
    custom_input : str
        Format: ``"host1:15,host2:10"``.

    Returns
    -------
    machines : tuple
        All machines parsed from string.

    """

    machines = custom_input.split(",")
    all_hosts = []
    for machine in machines:
        name, cores = machine.split(":")
        all_hosts.append(hostinfo(hostname=name, cores=int(cores)))

    return tuple(all_hosts)


def parse_hosts_sge(host_file_name):
    """Parse SGE (UGE) host file.

    Parameters
    ----------
    host_file_name : str
        Path to the host file.

    Returns
    -------
    machines : tuple
        All machines parsed from string.

    """
    csv.register_dialect("pemachines", delimiter=" ", skipinitialspace=True)
    all_hosts = []
    with open(host_file_name) as file:
        reader = csv.reader(file, dialect="pemachines")
        for row in reader:
            if not row:
                break

            all_hosts.append(hostinfo(hostname=row[0], cores=int(row[1])))

    return tuple(all_hosts)


def parse_hosts_lsf(host_list_str):
    """Parse LSF hosts

    Parameters
    ----------
    host_list_str : str
        Format from env var ``host_nameA num_processors1 host_nameB num_processors2``.

    Returns
    -------
    machines : tuple
        All machines parsed from string.

    """
    host_list = host_list_str.split()

    # get pairs of data, eg hostname1 core_num1
    all_hosts = [hostinfo(hostname=host_list[i], cores=int(host_list[i + 1])) for i in range(0, len(host_list), 2)]

    return tuple(all_hosts)


def parse_hosts_ccs(host_list_str):
    """Parse the Windows HPC/CCS host list.

    Parameters
    ----------
    host_list_str : str
        format ``#hosts host1 #cores1 host2 #cores2 host3 #cores3 ... hostN #coresN``.

    Returns
    -------
    machines : tuple
        All machines parsed from string.

    """
    host_list = host_list_str.split()
    all_hosts = [hostinfo(hostname=host_list[i], cores=int(host_list[i + 1])) for i in range(1, len(host_list), 2)]

    return tuple(all_hosts)


def parse_hosts_pbs(pbs_node_file):
    """Provide private module function to parse the PBS host file.

    Example:
        #!/bin/bash
        #PBS -l walltime=1:00:00
        #PBS -l select=2:ncpus=3:mpiprocs=3

        produces:
            comp001.hpc
            comp001.hpc
            comp001.hpc
            comp002.hpc
            comp002.hpc
            comp002.hpc

    Parameters
    ----------
    pbs_node_file : str
        Path to the PBS file.

    Returns
    -------
    machines : tuple
        All machines parsed from file.

    """
    host_cores = {}
    with open(pbs_node_file) as file:
        for line in file:
            host = line.strip()
            if not host:
                continue

            host_cores.setdefault(host, 0)
            host_cores[host] += 1

    all_hosts = [hostinfo(hostname=name, cores=int(cpu)) for name, cpu in host_cores.items()]
    return tuple(all_hosts)


def parse_hosts_slurm(host_list_str):
    """Parse the SLURM host and task lists.

    The SLURM system provides a comma separated list of host names.  The host names may be
    listed individually or consecutive host names may have IDs that are provided by a
    set within brackets:

    SLURM_JOB_NODELIST = host_a[2-5,7,14-15],host_b,host_c[008-010,012,017-019],host_d[099-101] ...

    Consecutive IDs may be prefixed (or pre-padded) with zeros so that the string representation
    of each machine ID always has the same length as the number of digits required to represent
    the last machine ID in the bracketed range.

    The cores allocated to each machine come in a separate variable

    SLURM_TASKS_PER_NODE = '10,3,12(x2),4,15(x5)'

    An (x#) after the core count indicates that the core count is repeated # times.  The order is the
    same as SLURM_JOB_NODELIST.

    Parameters
    ----------
    host_list_str : str
        String extracted from ``SLURM_JOB_NODELIST``.

    Returns
    -------
    machines : tuple
        All machines parsed.

    """
    # first get all unparsed nodes, eg host_a[2-5,7,14-15]
    all_unparsed_nodes = re.findall(r"([a-zA-Z0-9_.-]*\[[a-zA-Z0-9,-]*])", host_list_str)

    # remove them from string and get all hosts, that are single, eg host_b
    all_parsed_nodes = []
    node_chunks = []
    parsed_nodes_str = host_list_str
    for node in all_unparsed_nodes:
        parsed_nodes_str = parsed_nodes_str.replace(node, "")
        node_chunks.append(_parse_single_host(node))

    simple_name_nodes = re.findall(r"([a-zA-Z0-9_.-]*)", parsed_nodes_str)
    # we must preserve order of nodes, to fill-up cores
    for node in simple_name_nodes:
        if not node:
            if node_chunks:
                all_parsed_nodes += node_chunks.pop(0)
        else:
            all_parsed_nodes.append(node)

    if "SLURM_NTASKS_PER_NODE" in os.environ:
        cores_per_machine = int(os.environ.get("SLURM_NTASKS_PER_NODE"))
        host_cores = {node: cores_per_machine for node in all_parsed_nodes}
    else:
        if "SLURM_TASKS_PER_NODE" in os.environ:
            parsed_cores = _expand_slurm_cores(os.environ["SLURM_TASKS_PER_NODE"])
            host_cores = {node: parsed_cores[i] for i, node in enumerate(all_parsed_nodes)}
        else:
            host_cores = {node: 1 for node in all_parsed_nodes}

    all_hosts = [hostinfo(hostname=name, cores=int(cpu)) for name, cpu in host_cores.items()]
    return tuple(all_hosts)


def _parse_single_host(unparsed_str):
    """Parse host string.

    Parameters
    ----------
    unparsed_str : str
        Format ``'host_c[008-010,012,017-019]'``.

    Returns
    -------
    hosts : list
        Expanded list of hosts.

    """
    host, node_numbers_str = re.findall(r"([a-zA-Z0-9_.-]*)\[([0-9,-]*)]", unparsed_str)[0]
    node_num_list = node_numbers_str.split(",")

    parsed_hosts = []
    for node_num in node_num_list:
        if "-" in node_num:
            low, high = node_num.split("-")
            max_len = max((len(low), len(high)))  # required if number is prefixed with 0's, eg 001
            for i in range(int(low), int(high) + 1):
                host_number = "0" * (max_len - len(str(i))) + str(i)
                parsed_hosts.append(host + host_number)
        else:
            parsed_hosts.append(host + node_num)

    return parsed_hosts


def _expand_slurm_cores(cores_str):
    core_list = cores_str.split(",")
    parsed_cores = []
    for cores in core_list:
        match = re.match(r"^([0-9]+)(\(x([0-9]+)\))?$", cores)

        multiplicator = match.group(3) or 1
        parsed_cores += [int(match.group(1))] * int(multiplicator)

    return parsed_cores
