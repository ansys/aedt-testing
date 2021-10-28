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
