"""MQTT client for Dimplex MQTT gateway."""
from __future__ import annotations

import json, uuid
import logging
import threading
from typing import Any, Callable
from datetime import datetime

import paho.mqtt.client as mqtt

_LOGGER = logging.getLogger(__name__)

class DimplexMqttClient:
    def __init__(
        self,
        config: dict[str, Any],
        on_values_changed: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.client_id = f"dimplex_mqtt_ha_{uuid.uuid4().hex[:8]}"
        self.on_values_changed = on_values_changed

        self._correlation_id = 0
        self._correlation_lock = threading.Lock()

        self.subscribed_topics: set[str] = set()
        self.pending_subscriptions: dict[int, str] = {}

        self.values: dict[str, Any] = {}
        self.lock = threading.Lock()
        self._stopped = False

        self.client = mqtt.Client(
            client_id=self.client_id,
            protocol=mqtt.MQTTv5,
        )

        self.client.username_pw_set(config["username"], config["password"])

        self.connected = False
        self.client.on_subscribe = self._on_subscribe
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.on_log = self._on_log
        self.last_connect_time = None
        self.last_disconnect_time = None
        self.disconnect_reason = None

        self.client.reconnect_delay_set(min_delay=10, max_delay=60)

        _LOGGER.info(
            "Starting Dimplex MQTT client_id=%s host=%s port=%s",
            self.client_id,
            config["host"],
            config["port"],
        )

        try:
            self.client.connect_async(
                config["host"],
                int(config["port"]),
                keepalive=30,
            )
            self.client.loop_start()
        except TimeoutError as err:
            _LOGGER.error(
                "Dimplex MQTT broker unreachable host=%s port=%s",
                config["host"],
                config["port"],
            )
            raise
        except OSError as err:
            _LOGGER.error(
                "Dimplex MQTT connection failed host=%s port=%s error=%s",
                config["host"],
                config["port"],
                err,
            )
            raise

    def _on_log(self, client, userdata, level, buf):
        _LOGGER.debug(
            "MQTT[%s] %s",
            self.client_id,
            buf,
        )

    def _generate_correlation_id(self) -> int:
        with self._correlation_lock:
            self._correlation_id += 1
            return self._correlation_id
        
    def _on_subscribe(
        self,
        client,
        userdata,
        mid,
        reason_codes,
        properties=None,
    ):
        topic = self.pending_subscriptions.pop(mid, "unknown")

        if topic != "unknown":
            self.subscribed_topics.add(topic)

        _LOGGER.info(
            "Subscribed topic=%s mid=%s reason_codes=%s",
            topic,
            mid,
            reason_codes,
        )

        if "clear_prev_val_cache_reply" in topic:
            self.send_clear_prev_value_cache()


    def subscribe_to_topic(self, topic: str):
        result, mid = self.client.subscribe(topic, qos=0)

        if result == mqtt.MQTT_ERR_SUCCESS:
            self.pending_subscriptions[mid] = topic
            _LOGGER.info("Subscribe requested: topic=%s mid=%s", topic, mid)
        else:
            _LOGGER.error("Failed to subscribe topic=%s result=%s", topic, result)

    def _on_connect(
        self,
        client,
        userdata,
        flags,
        reason_code,
        properties=None,
    ):
        rc = getattr(reason_code, "value", reason_code)

        if rc != 0:
            _LOGGER.error(
                "MQTT connection failed client_id=%s rc=%s",
                self.client_id,
                reason_code,
            )
            return

        _LOGGER.info(
            "MQTT connected client_id=%s",
            self.client_id,
        )
        self.connected = True
        self.last_connect_time = datetime.now()
        self.subscribed_topics.clear()
        self.pending_subscriptions.clear()

        self.subscribe_to_topic(
            f"extern/{self.client_id}/clear_prev_val_cache_reply"
        )
        self.subscribe_to_topic(
            "gateway/broadcast/changed_on/#"
        )
        self.subscribe_to_topic(
            f"extern/{self.client_id}/set_value_reply"
)

    def _on_disconnect(
        self,
        client,
        userdata,
        reason_code,
        properties=None,
    ):
        rc = getattr(reason_code, "value", reason_code)

        if not self._stopped:
            _LOGGER.warning(
                "MQTT disconnected client_id=%s rc=%s",
                self.client_id,
                rc,
            )

        self.connected = False
        self.disconnect_reason = rc
        self.last_disconnect_time = datetime.now()

    def _on_message(self, client, userdata, msg) -> None:
        topic = msg.topic
        payload = msg.payload.decode(errors="replace")

        # _LOGGER.info("MQTT message topic=%s payload=%s", topic, payload)

        if "clear_prev_val_cache_reply" in topic:
            return
        
        if "set_value_reply" in topic:
            _LOGGER.info("Set value reply: %s", payload)
            return

        if "changed_on" not in topic:
            return

        parsed = self._parse_changed_on(payload)

        if not parsed:
            return

        with self.lock:
            self.values.update(parsed)

        if self.on_values_changed is not None:
            self.on_values_changed(parsed)

        # _LOGGER.debug("MQTT changed_on values=%s", parsed)

    def send_clear_prev_value_cache(
        self,
        telemetry: bool = True,
        twin: bool = True,
    ):
        response_topic = f"extern/{self.client_id}/clear_prev_val_cache_reply"

        payload = {
            "mqtt_msg_properties": {
                "correlation_data": self._generate_correlation_id(),
                "response_topic": response_topic,
            },
            "telemetry": telemetry,
            "twin": twin,
        }

        topic = "gateway/modbus/clear_prev_val_cache"

        info = self.client.publish(
            topic,
            payload=json.dumps(payload),
            qos=0,
        )

        _LOGGER.info(
            "Published clear_prev_val_cache response_topic=%s",
            response_topic,
        )

        return info

    def _parse_changed_on(self, payload: str) -> dict[str, Any]:
        result: dict[str, Any] = {}

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            _LOGGER.warning("Invalid changed_on JSON: %s", payload)
            return result

        if not isinstance(data, dict):
            return result

        for range_name, range_data in data.items():
            if not isinstance(range_data, dict):
                continue

            data_type = range_data.get("type")
            timestamp = range_data.get("timestamp")
            value_batch = range_data.get("value_batch", {})

            if not isinstance(value_batch, dict):
                continue

            for key, item in value_batch.items():
                if not isinstance(item, dict):
                    continue

                raw_id = item.get("id") or key
                name = item.get("name") or raw_id
                raw_value = item.get("value")
                converted = self._convert_typed_value(raw_value, data_type)

                for candidate in {str(key), str(raw_id), str(name)}:
                    if candidate:
                        result[candidate] = converted
                        if candidate[-1:] in ("i", "u"):
                            result[candidate[:-1] + ("u" if candidate[-1] == "i" else "i")] = converted

        return result

    @staticmethod
    def _convert_typed_value(value: Any, data_type: str | None = None) -> Any:
        if value is None:
            return None

        if data_type == "bool":
            return str(value) in ("1", "true", "True")

        if data_type in ("int16", "int32", "uint16", "uint32"):
            try:
                return int(value)
            except (TypeError, ValueError):
                return value

        if data_type in ("float", "float32", "float64", "double"):
            try:
                return float(value)
            except (TypeError, ValueError):
                return value

        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return float(value)
            except (TypeError, ValueError):
                return value
            

    def write_variable(self, variable_id: str, value) -> None:
        payload = {
            "mqtt_msg_properties": {
                "correlation_data": self._generate_correlation_id(),
                "response_topic": f"extern/{self.client_id}/set_value_reply",
            },
            "name": variable_id,
            "value": value,
        }

        topic = f"gateway/modbus/set_value/{variable_id}"

        self.client.publish(
            topic,
            payload=json.dumps(payload),
            qos=0,
        )

    def stop(self) -> None:
        """Stop MQTT client."""
        self._stopped = True
        self.client.loop_stop()
        self.client.disconnect()