from fastapi import FastAPI
from fastapi.testclient import TestClient

from helpdesk_common.metrics import instrument_app


def test_metrics_label_route_templates_and_exclude_scrapes():
    app = FastAPI()

    @app.get("/widgets/{widget_id}")
    def widget(widget_id: int):
        return {"id": widget_id}

    instrument_app(app)
    client = TestClient(app)
    assert client.get("/widgets/123?token=private").status_code == 200
    metrics = client.get("/metrics").text
    assert 'handler="/widgets/{widget_id}"' in metrics
    assert 'handler="/widgets/123"' not in metrics
    assert "private" not in metrics
    assert 'handler="/metrics"' not in metrics
