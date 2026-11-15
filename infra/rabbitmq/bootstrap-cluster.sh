#!/bin/sh
set -eu
umask 077
printf '%s' "${RABBITMQ_ERLANG_COOKIE:?}" > "$HOME/.erlang.cookie"
chmod 600 "$HOME/.erlang.cookie"
# Definitions can be imported before peers join. Explicitly grow every quorum
# queue to all three members before allowing applications to start publishing.
rabbitmqctl -n rabbit@rabbitmq await_online_nodes 3
rabbitmq-queues -n rabbit@rabbitmq grow rabbit@rabbitmq-2 all
rabbitmq-queues -n rabbit@rabbitmq grow rabbit@rabbitmq-3 all
rabbitmqctl -n rabbit@rabbitmq list_queues name type members online
