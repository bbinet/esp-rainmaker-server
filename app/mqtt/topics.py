"""MQTT topic constants — mirror of esp_rmaker_mqtt_topics.h.

All device-side topics are prefixed with ``node/<node_id>/``.
"""

from __future__ import annotations

CONFIG = "config"
PARAMS_LOCAL = "params/local"
PARAMS_LOCAL_INIT = "params/local/init"
PARAMS_REMOTE = "params/remote"
ALERT = "alert"
USER_MAPPING = "user/mapping"
OTAFETCH = "otafetch"
OTAURL = "otaurl"
OTASTATUS = "otastatus"
TSDATA = "tsdata"
SIMPLE_TSDATA = "simple_tsdata"
FROM_NODE = "from-node"
TO_NODE = "to-node"
DIAGNOSTICS_FROM_NODE = "diagnostics/from-node"


def node_topic(node_id: str, suffix: str) -> str:
    return f"node/{node_id}/{suffix}"
