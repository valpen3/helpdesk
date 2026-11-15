import asyncio
from unittest.mock import AsyncMock, patch

from aio_pika.robust_connection import RobustConnection
from yarl import URL

from helpdesk_common.broker import ClusterConnection, cluster_connection_options, connect_broker
from helpdesk_common.redis_client import make_redis_client


def test_sentinel_uses_distinct_control_and_data_credentials(monkeypatch):
    monkeypatch.setenv("REDIS_SENTINELS", "one:26379,two:26380,three")
    monkeypatch.setenv("REDIS_SENTINEL_PASSWORD", "control-secret")
    monkeypatch.setenv("REDIS_MASTER_NAME", "helpdesk")
    with patch("helpdesk_common.redis_client.Sentinel") as sentinel:
        result = make_redis_client(
            username="ticket-svc", password="data-secret", socket_timeout=0.25
        )
    args, kwargs = sentinel.call_args
    assert args[0] == [("one", 26379), ("two", 26380), ("three", 26379)]
    assert kwargs["sentinel_kwargs"]["password"] == "control-secret"
    assert kwargs["username"] == "ticket-svc"
    assert kwargs["password"] == "data-secret"
    sentinel.return_value.master_for.assert_called_once_with("helpdesk")
    assert result is sentinel.return_value.master_for.return_value


def test_broker_rotates_after_disconnect_and_failed_reconnect(monkeypatch):
    monkeypatch.setenv("MQ_HOSTS", "one,two,three")

    async def scenario():
        connection = ClusterConnection(URL("amqp://user:secret@one/"), fail_fast="0")
        try:
            for expected in ("two", "three", "one"):
                closing = asyncio.get_running_loop().create_future()
                closing.set_exception(ConnectionError("node down"))
                with patch.object(RobustConnection, "_on_connection_close") as parent:
                    await connection._on_connection_close(closing)
                # Consume the exception as the parent normally does.
                closing.exception()
                parent.assert_awaited_once_with(closing)
                assert connection.url.host == expected
                assert connection.url.password == "secret"
        finally:
            await connection.close()

    asyncio.run(scenario())


def test_cluster_bootstrap_waits_for_other_nodes(monkeypatch):
    monkeypatch.setenv("MQ_HOSTS", "one,two,three")
    assert cluster_connection_options()["fail_fast"] == "0"
    monkeypatch.delenv("MQ_HOSTS")
    assert cluster_connection_options() == {}


def test_failed_bootstrap_closes_retry_task_and_sets_heartbeat(monkeypatch):
    monkeypatch.setenv("MQ_HOSTS", "one,two,three")
    connection = AsyncMock()
    connection.connect.side_effect = TimeoutError("all nodes down")

    async def scenario():
        with patch("helpdesk_common.broker.ClusterConnection", return_value=connection) as factory:
            try:
                await connect_broker(host="one", login="user", password="secret", timeout=0.01)
            except TimeoutError:
                pass
            else:
                raise AssertionError("Unreachable brokers must fail startup")
        assert factory.call_args.args[0].query["heartbeat"] == "10"
        connection.close.assert_awaited_once()

    asyncio.run(scenario())
