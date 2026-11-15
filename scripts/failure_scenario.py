"""Stop one node at a time, restore it, and journal real client observations.

Run against a disposable Compose project only. This deliberately stops nodes.
Uses only the Python standard library on the host; the probe runs in the image.
"""

import argparse
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

COMPOSE = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.ci.yml"]


def run(*args):
    return subprocess.check_output([*COMPOSE, *args], text=True, stderr=subprocess.STDOUT)


def summarize(samples):
    """Uptime exposure / observed outages; censored downtime is reported separately."""
    summary = {}
    for component in ("redis", "rabbitmq"):
        downtime = uptime = 0.0
        failures = 0
        for current, following in zip(samples, samples[1:], strict=False):
            duration = following["timestamp"] - current["timestamp"]
            if current[component]:
                uptime += duration
            else:
                downtime += duration
            if current[component] and not following[component]:
                failures += 1
        censored = bool(samples and not samples[-1][component])
        summary[component] = {
            "observed_outages": failures,
            "uptime_seconds": round(uptime, 3),
            "downtime_seconds": round(downtime, 3),
            "mttr_seconds": round(downtime / failures, 3) if failures and not censored else None,
            "mtbf_seconds": round(uptime / failures, 3) if failures else None,
            "censored_outage": censored,
        }
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="artifacts/ha-results")
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--baseline-seconds", type=float, default=10)
    args = parser.parse_args()
    if not os.getenv("COMPOSE_PROJECT_NAME", "").startswith("helpdesk-ha-"):
        parser.error("Set COMPOSE_PROJECT_NAME=helpdesk-ha-<name> for a disposable test stack")
    if args.cycles < 1 or args.baseline_seconds < 1:
        parser.error("cycles and baseline-seconds must be positive")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    samples, incidents = [], []
    messages = queue.Queue()
    command = [
        *COMPOSE,
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--user",
        "root",
        "--name",
        f"{os.getenv('COMPOSE_PROJECT_NAME', 'helpdesk')}-ha-probe",
        "-v",
        f"{Path.cwd()}:/workspace:ro",
        "--entrypoint",
        "python",
        "ticket-service",
        "/workspace/scripts/ha_probe.py",
    ]
    probe = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=(output / "probe.log").open("w"),
        text=True,
        bufsize=1,
    )

    def read_samples():
        for line in probe.stdout:
            try:
                messages.put(json.loads(line))
            except json.JSONDecodeError:
                pass

    threading.Thread(target=read_samples, daemon=True).start()

    def observe_until(predicate, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                sample = messages.get(timeout=1)
            except queue.Empty:
                if probe.poll() is not None:
                    raise RuntimeError("Probe exited; inspect probe.log") from None
                continue
            samples.append(sample)
            if predicate(sample):
                return sample
        raise TimeoutError("Clients did not recover within the measurement deadline")

    def healthy_window(seconds):
        since = None

        def stable(sample):
            nonlocal since
            if sample["redis"] and sample["rabbitmq"]:
                since = sample["timestamp"] if since is None else since
                return sample["timestamp"] - since >= seconds
            since = None
            return False

        return observe_until(stable)

    def primary_service(address):
        for service in ("redis", "redis-replica-1", "redis-replica-2"):
            container = run("ps", "-q", service).strip()
            info = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[
                0
            ]
            ips = [net["IPAddress"] for net in info["NetworkSettings"]["Networks"].values()]
            if address in [service, *ips]:
                return service
        raise RuntimeError(f"Cannot identify primary {address}")

    def firing_alert(component):
        name = "RedisNodeDown" if component == "redis" else "RabbitMQQuorumAtRisk"
        query = f'ALERTS{{alertname="{name}",alertstate="firing"}}'
        url = "http://localhost:9090/api/v1/query?" + urlencode({"query": query})
        with urlopen(url, timeout=3) as response:
            return bool(json.load(response)["data"]["result"])

    success = False
    try:
        last = healthy_window(args.baseline_seconds)
        for cycle in range(args.cycles):
            for component in ("redis", "rabbitmq"):
                target = (
                    primary_service(last["redis_primary"])
                    if component == "redis"
                    else last["rabbitmq_node"]
                )
                incident = {
                    "cycle": cycle + 1,
                    "component": component,
                    "target": target,
                    "injected_at": time.time(),
                }
                incidents.append(incident)
                try:
                    run("stop", "--timeout", "1", target)
                    # The original process and original Redis/AMQP objects stay alive.
                    last = healthy_window(3)
                    if component == "rabbitmq" and last["rabbitmq_node"] == target:
                        raise AssertionError("AMQP connection did not move to another node")
                    if component == "redis" and primary_service(last["redis_primary"]) == target:
                        raise AssertionError("Sentinel did not elect another primary")
                    incident["recovered_at"] = last["timestamp"]
                    # Keep the failed node stopped until the real alert fires.
                    # Continue recording operations during this observation window.
                    observe_until(lambda sample: firing_alert(component), timeout=45)
                    incident["alert_fired_at"] = time.time()
                    for service in (
                        "auth-service",
                        "ticket-service",
                        "sla-service",
                        "notification-service",
                    ):
                        run(
                            "exec",
                            "-T",
                            service,
                            "python",
                            "-c",
                            "from urllib.request import urlopen; "
                            "assert urlopen('http://127.0.0.1:8000/health/ready', "
                            "timeout=10).status == 200",
                        )
                    incident["applications_ready"] = True
                finally:
                    run("start", target)
                    incident["restored_at"] = time.time()
                last = healthy_window(args.baseline_seconds)
                # Allow restored Redis replicas to catch up before the next fault.
                time.sleep(5)
        success = True
    finally:
        # Stop only our one-off probe, never the application service.
        subprocess.run(
            [
                "docker",
                "stop",
                "--time",
                "1",
                f"{os.getenv('COMPOSE_PROJECT_NAME', 'helpdesk')}-ha-probe",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        probe.wait(timeout=15)
        while not messages.empty():
            samples.append(messages.get_nowait())
        (output / "samples.jsonl").write_text(
            "".join(json.dumps(sample) + "\n" for sample in samples)
        )
        report = {
            "success": success,
            "kind": "controlled fault injection, not field reliability",
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "run_url": os.getenv("GITHUB_SERVER_URL", "")
            + "/"
            + os.getenv("GITHUB_REPOSITORY", "")
            + "/actions/runs/"
            + os.getenv("GITHUB_RUN_ID", ""),
            "incidents": incidents,
            "metrics": summarize(samples),
        }
        (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
