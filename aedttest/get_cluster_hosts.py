import json

all_hosts = oDesktop.GetDistributedAnalysisMachinesForDesignType("hfss")  # noqa: F821

host_cores = {}
for host in all_hosts:
    host_cores.setdefault(host, 0)
    host_cores[host] += 1

with open("host_info.json", "w") as file:
    json.dump(host_cores, file, indent=4)
