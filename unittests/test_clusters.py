import os
from tempfile import NamedTemporaryFile

from aedttest.clusters import job_hosts


def test_slurm_nodes_start_end_unparsed():
    slurm_job_nodelist = "host_a[2-5,7,14-15],host_b,host_c[008-010,012,017-019],host_d[099-101]"
    hosts = job_hosts.parse_hosts_slurm(slurm_job_nodelist)
    assert len(hosts) == 18
    assert hosts[0].hostname == "host_a2"
    assert hosts[7].hostname == "host_b"
    assert hosts[10].hostname == "host_c010"
    assert hosts[-1].hostname == "host_d101"


def test_slurm_nodes_all_unparsed():
    slurm_job_nodelist = "host_a[2-5,7,14-15],host_c[008-010,012,017-019],host_d[099-101]"
    hosts = job_hosts.parse_hosts_slurm(slurm_job_nodelist)
    assert len(hosts) == 17
    assert hosts[-1].hostname == "host_d101"


def test_slurm_nodes_start_end_pure():
    slurm_job_nodelist = "host1,host_a[2-5,7,14-15],host_b"
    hosts = job_hosts.parse_hosts_slurm(slurm_job_nodelist)
    assert len(hosts) == 9
    assert hosts[0].hostname == "host1"
    assert hosts[7].hostname == "host_a15"
    assert hosts[-1].hostname == "host_b"


def test_slurm_nodes_all_pure():
    slurm_job_nodelist = "host1,host_b,host_c"
    hosts = job_hosts.parse_hosts_slurm(slurm_job_nodelist)
    assert hosts[0].hostname == "host1"
    assert hosts[1].hostname == "host_b"
    assert hosts[2].hostname == "host_c"


def test_slurm_cores_mixed():
    cores = job_hosts._expand_slurm_cores("10,3,12(x2),4,15(x5)")
    assert cores == [10, 3, 12, 12, 4, 15, 15, 15, 15, 15]


def test_slurm_cores_unparsed():
    cores = job_hosts._expand_slurm_cores("12(x2)")
    assert cores == [12, 12]


def test_slurm_cores_pure():
    cores = job_hosts._expand_slurm_cores("12,24")
    assert cores == [12, 24]


def test_parse_hosts_lsf():
    hosts = job_hosts.parse_hosts_lsf("host_nameA 15 hostnameB 10")

    assert hosts[0].hostname == "host_nameA"
    assert hosts[0].cores == 15
    assert hosts[1].hostname == "hostnameB"
    assert hosts[1].cores == 10


def test_parse_hosts_lsf_single():
    hosts = job_hosts.parse_hosts_lsf("host_nameA 15")

    assert hosts[0].hostname == "host_nameA"
    assert hosts[0].cores == 15
    assert len(hosts) == 1


def test_parse_custom_input():
    hosts = job_hosts.parse_custom_input("host1:15,host2:10")
    assert hosts[0].hostname == "host1"
    assert hosts[0].cores == 15
    assert hosts[1].hostname == "host2"
    assert hosts[1].cores == 10


def test_parse_hosts_ccs():
    hosts = job_hosts.parse_hosts_ccs("2 host1 15 host2 10")
    assert hosts[0].hostname == "host1"
    assert hosts[0].cores == 15
    assert hosts[1].hostname == "host2"
    assert hosts[1].cores == 10


def test_parse_hosts_pbs():
    with NamedTemporaryFile(mode="w+", suffix=".py", delete=False) as file:
        file.write("comp001.hpc\r\n" * 3)
        file.write("comp002.hpc\n" * 4)
        file.close()
        try:
            hosts = job_hosts.parse_hosts_pbs(file.name)
        finally:
            os.unlink(file.name)

    assert len(hosts) == 2
    assert hosts[0].hostname == "comp001.hpc"
    assert hosts[0].cores == 3
    assert hosts[1].hostname == "comp002.hpc"
    assert hosts[1].cores == 4


def test_parse_hosts_sge():
    with NamedTemporaryFile(mode="w+", suffix=".py", delete=False) as file:
        file.write("node104.a.itservices.ac.uk 32 R815.q@node104.a.itservices.ac.uk UNDEFINED\n")
        file.write("node110.a.itservices.ac.uk 15 R815.q@node104.a.itservices.ac.uk UNDEFINED\n")
        file.write("node115.a.itservices.ac.uk 64 R815.q@node104.a.itservices.ac.uk UNDEFINED\n")
        file.close()
        try:
            hosts = job_hosts.parse_hosts_sge(file.name)
        finally:
            os.unlink(file.name)

    assert len(hosts) == 3
    assert hosts[0].hostname == "node104.a.itservices.ac.uk"
    assert hosts[0].cores == 32
    assert hosts[1].hostname == "node110.a.itservices.ac.uk"
    assert hosts[1].cores == 15
    assert hosts[2].hostname == "node115.a.itservices.ac.uk"
    assert hosts[2].cores == 64
