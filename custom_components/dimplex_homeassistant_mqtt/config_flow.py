"""Config flow for Dimplex MQTT."""
from __future__ import annotations

import threading
import uuid

import paho.mqtt.client as mqtt
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD

from .const import DOMAIN, MQTT_PORT, MQTT_USERNAME

CONF_INSTALLER_ACCESS = "installer_access"


def test_mqtt_connection(data: dict) -> str | None:
    """Return None if connection is OK, otherwise return an error key."""
    connected = threading.Event()
    failed = threading.Event()
    result = {"error": None}

    def on_connect(client, userdata, flags, reason_code, properties=None):
        rc = getattr(reason_code, "value", reason_code)

        if rc == 0:
            connected.set()
        else:
            result["error"] = f"mqtt_connect_failed_{reason_code}"
            failed.set()

    def on_disconnect(client, userdata, reason_code, properties=None):
        rc = getattr(reason_code, "value", reason_code)

        if not connected.is_set():
            result["error"] = f"mqtt_disconnected_{rc}"
            failed.set()

    client = mqtt.Client(
        client_id=f"dimplex_mqtt_ha_test_{uuid.uuid4().hex[:8]}",
        protocol=mqtt.MQTTv5,
    )

    client.username_pw_set(
        MQTT_USERNAME,
        data[CONF_PASSWORD],
    )

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    try:
        client.connect(
            data[CONF_HOST],
            MQTT_PORT,
            keepalive=30,
        )

        client.loop_start()

        if connected.wait(8):
            return None

        failed.wait(1)
        return result["error"] or "mqtt_timeout"

    except TimeoutError:
        return "mqtt_timeout"
    except OSError as err:
        return f"mqtt_os_error_{err}"
    except Exception as err:
        return str(err)

    finally:
        try:
            client.loop_stop()
            client.disconnect()
        except Exception:
            pass


class DimplexMqttConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Dimplex MQTT."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle user setup."""
        errors = {}

        if user_input is not None:
            await self.async_set_unique_id(
                f"{user_input[CONF_HOST]}:{MQTT_PORT}"
            )
            self._abort_if_unique_id_configured()

            result = await self.hass.async_add_executor_job(
                test_mqtt_connection,
                user_input,
            )

            if result is None:
                data = dict(user_input)
                data["port"] = MQTT_PORT
                data["username"] = MQTT_USERNAME

                return self.async_create_entry(
                    title=f"Dimplex MQTT {user_input[CONF_HOST]}",
                    data=data,
                )

            errors["base"] = "Host unreachable or invalid credentials."

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default="192.168.0.99"): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_INSTALLER_ACCESS, default=False): bool,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None):
        """Handle reconfiguration of an existing entry."""
        entry = self._get_reconfigure_entry()
        errors = {}

        if user_input is not None:
            data = dict(entry.data)
            data.update(user_input)

            if not user_input.get(CONF_PASSWORD):
                data[CONF_PASSWORD] = entry.data[CONF_PASSWORD]

            result = await self.hass.async_add_executor_job(
                test_mqtt_connection,
                data,
            )

            if result is None:
                data["port"] = MQTT_PORT
                data["username"] = MQTT_USERNAME
                self.hass.config_entries.async_update_entry(
                    entry,
                    data=data,
                )
                return self.async_abort(reason="reconfigure_successful")

            errors["base"] = "Host unreachable or invalid credentials."

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=entry.data.get(CONF_HOST, "192.168.0.99"),
                ): str,
                vol.Optional(CONF_PASSWORD): str,
                vol.Optional(
                    CONF_INSTALLER_ACCESS,
                    default=entry.data.get(CONF_INSTALLER_ACCESS, False),
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )