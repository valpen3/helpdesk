from scripts.failure_scenario import summarize


def test_observed_intervals_and_no_failure_are_reported_separately():
    samples = [
        {"timestamp": 0, "redis": True, "rabbitmq": True},
        {"timestamp": 10, "redis": False, "rabbitmq": True},
        {"timestamp": 14, "redis": True, "rabbitmq": True},
        {"timestamp": 30, "redis": False, "rabbitmq": True},
        {"timestamp": 36, "redis": True, "rabbitmq": True},
        {"timestamp": 50, "redis": True, "rabbitmq": True},
    ]
    report = summarize(samples)
    assert report["redis"]["observed_outages"] == 2
    assert report["redis"]["mttr_seconds"] == 5
    assert report["redis"]["mtbf_seconds"] == 20
    assert report["rabbitmq"]["mttr_seconds"] is None
    assert report["rabbitmq"]["mtbf_seconds"] is None


def test_unfinished_outage_is_censored():
    report = summarize(
        [
            {"timestamp": 0, "redis": True, "rabbitmq": True},
            {"timestamp": 2, "redis": False, "rabbitmq": True},
            {"timestamp": 5, "redis": False, "rabbitmq": True},
        ]
    )
    assert report["redis"]["censored_outage"] is True
    assert report["redis"]["mttr_seconds"] is None
