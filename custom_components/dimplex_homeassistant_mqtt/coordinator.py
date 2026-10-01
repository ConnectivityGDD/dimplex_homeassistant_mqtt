import logging

from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .mqtt_client import DimplexMqttClient
from .utils.energy import EnergyCounter

_LOGGER = logging.getLogger(__name__)

ENERGY_MAPPINGS = {
    "WMZ_Anz_G_ST1bis4": ("1660i", "1661i", "1662i"),
    "WMZ_Anz_H_ST1bis4": ("1663i", "1664i", "1665i"),
    "WMZ_Anz_WW_ST1bis4": ("1669i", "1670i", "1671i"),
    "WMZ_Anz_SW_ST1bis4": ("1666i", "1667i", "1668i"),
    "Qc_Anz_G_ST1bis4": ("1644i", "1645i", "1646i"),
}


class DimplexMqttCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, config):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=None,
        )

        self.hass = hass
        self.config = config
        self.device_id = config["host"]
        self.connected = False
        self._stopped = False
        self._energy_counters = {
            target: EnergyCounter(registers)
            for target, registers in ENERGY_MAPPINGS.items()
        }

        try:
            self.client = DimplexMqttClient(
                config,
                on_values_changed=self._handle_values_changed,
                on_connection_changed=self._handle_connection_changed,
            )
        except TimeoutError as err:
            raise ConfigEntryNotReady(
                f"Dimplex MQTT broker unreachable at {config['host']}:{config['port']}"
            ) from err
        except OSError as err:
            raise ConfigEntryNotReady(
                f"Dimplex MQTT connection failed at {config['host']}:{config['port']}: {err}"
            ) from err

    def _handle_connection_changed(self, connected: bool):
        self.hass.loop.call_soon_threadsafe(
            self._async_handle_connection_changed, connected
        )

    def _async_handle_connection_changed(self, connected: bool):
        if self._stopped or connected == self.connected:
            return
        self.connected = connected
        if not connected:
            self._energy_counters = {
                target: EnergyCounter(registers)
                for target, registers in ENERGY_MAPPINGS.items()
            }
            self.async_set_updated_data({})
        else:
            self.async_set_updated_data(self.data or {})

    def _handle_values_changed(self, changed_values: dict):
        self.hass.loop.call_soon_threadsafe(
            self._async_handle_values_changed, changed_values
        )

    def _async_handle_values_changed(self, changed_values: dict):
        if self._stopped or not self.connected:
            return
        data = dict(self.data or {})
        # The first register's MQTT name is also the combined sensor's key.
        data.update({
            key: value for key, value in changed_values.items()
            if key not in ENERGY_MAPPINGS
        })
        for target, counter in self._energy_counters.items():
            if any(register in changed_values for register in counter.registers):
                data[target] = counter.update(changed_values)

        if data != self.data:
            self.async_set_updated_data(data)

    async def async_shutdown(self):
        if self._stopped:
            return
        self._stopped = True
        await self.hass.async_add_executor_job(self.client.stop)

    async def _async_update_data(self):
        return self.data or {}
