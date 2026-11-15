"""Check real scrape targets, data and provisioned dashboards, not just processes."""

import base64
import json
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def get(url):
    with urlopen(url, timeout=5) as response:
        return json.load(response)


def check():
    targets = get("http://localhost:9090/api/v1/targets")["data"]["activeTargets"]
    assert len(targets) >= 19, f"Only {len(targets)} targets were discovered"
    failed = [target["scrapeUrl"] for target in targets if target["health"] != "up"]
    assert not failed, failed
    for expression in (
        'count(redis_up{job="redis"} == 1) == 3',
        'count(redis_up{job="sentinel"} == 1) == 3',
        'sum(redis_connected_slaves{job="redis"}) == 2',
        'count(up{job="rabbitmq"} == 1) == 3',
        'count(probe_success{job="readiness"} == 1) == 5',
        "pg_up == 1",
        "nginx_up == 1",
        'sum(http_requests_total{handler="/hello"}) >= 1',
    ):
        result = get("http://localhost:9090/api/v1/query?" + urlencode({"query": expression}))
        assert result["data"]["result"], expression
    assert get("http://localhost:3000/api/health")["database"] == "ok"
    get("http://localhost:9093/api/v2/status")
    config = json.loads(
        subprocess.check_output(
            ["docker", "compose", "--profile", "monitoring", "config", "--format", "json"],
            text=True,
        )
    )
    password = config["services"]["grafana"]["environment"]["GF_SECURITY_ADMIN_PASSWORD"]
    auth = base64.b64encode(f"admin:{password}".encode()).decode()
    for uid in ("helpdesk-overview", "helpdesk-clusters"):
        request = Request(
            f"http://localhost:3000/api/dashboards/uid/{uid}",
            headers={"Authorization": "Basic " + auth},
        )
        assert len(get(request)["dashboard"]["panels"]) >= 6, uid


if __name__ == "__main__":
    for attempt in range(30):
        try:
            check()
            print("PASS: all scrape targets, Redis replicas, readiness and monitoring APIs")
            break
        except Exception:
            if attempt == 29:
                raise
            time.sleep(3)
