"""Check real scrape targets, data and provisioned dashboards, not just processes."""

import json
import time
from urllib.parse import urlencode
from urllib.request import urlopen


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
        'pg_up == 1',
        'nginx_up == 1',
    ):
        result = get("http://localhost:9090/api/v1/query?" + urlencode({"query": expression}))
        assert result["data"]["result"], expression
    assert get("http://localhost:3000/api/health")["database"] == "ok"
    get("http://localhost:9093/api/v2/status")


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
