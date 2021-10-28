from aedttest.clusters import job_hosts


def test_slurm_nodes():
    slurm_job_nodelist = "host_a[2-5,7,14-15],host_b,host_c[008-010,012,017-019],host_d[099-101]"
    hosts = job_hosts.parse_hosts_slurm(slurm_job_nodelist)
    assert len(hosts) == 18
    assert hosts[0].hostname == "host_a2"
    assert hosts[7].hostname == "host_b"
    assert hosts[10].hostname == "host_c010"
    assert hosts[-1].hostname == "host_d101"

    slurm_job_nodelist = "host1,host_a[2-5,7,14-15],host_b"
    hosts = job_hosts.parse_hosts_slurm(slurm_job_nodelist)
    assert len(hosts) == 9
    assert hosts[0].hostname == "host1"
    assert hosts[7].hostname == "host_a15"
    assert hosts[-1].hostname == "host_b"

    slurm_job_nodelist = "host1,host_b,host_c"
    hosts = job_hosts.parse_hosts_slurm(slurm_job_nodelist)
    assert hosts[0].hostname == "host1"
    assert hosts[1].hostname == "host_b"
    assert hosts[2].hostname == "host_c"


def test_slurm_cores():
    cores = job_hosts._expand_slurm_cores("10,3,12(x2),4,15(x5)")
    assert cores == [10, 3, 12, 12, 4, 15, 15, 15, 15, 15]
