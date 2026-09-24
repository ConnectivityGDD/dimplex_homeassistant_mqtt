import logging

from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .mqtt_client import DimplexMqttClient

_LOGGER = logging.getLogger(__name__)


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

        try:
            self.client = DimplexMqttClient(
                config,
                on_values_changed=self._handle_values_changed,
            )
        except TimeoutError as err:
            raise ConfigEntryNotReady(
                f"Dimplex MQTT broker unreachable at {config['host']}:{config['port']}"
            ) from err
        except OSError as err:
            raise ConfigEntryNotReady(
                f"Dimplex MQTT connection failed at {config['host']}:{config['port']}: {err}"
            ) from err

    def _handle_values_changed(self, changed_values: dict):
        self.hass.loop.call_soon_threadsafe(
            self._async_handle_values_changed,
            changed_values,
        )

    def _async_handle_values_changed(self, changed_values: dict):
        # Bestehende Daten nicht überschreiben, sondern aktualisieren
        data = dict(self.data or {})
        data.update(changed_values)

        energy_mappings = {
        "energy_heating_total": ("1300u", "1301u", "1302u"),
        "energy_hot_water_total": ("1303u", "1304u", "1305u"),
        "energy_pool_total": ("1306u", "1307u", "1308u"),
        "energy_wmz_1_total": ("1672i", "1673i", "1674i"),
        "energy_wmz_res_total": ("1660i", "1661i", "1662i"),
        "energy_wmz_2_total": ("1663i", "1664i", "1665i"), 
        "energy_wmz_3_total": ("1669i", "1670i", "1671i"), 
        }

        for target, (reg_low, reg_mid, reg_high) in energy_mappings.items():
            if any(r in data for r in (reg_low, reg_mid, reg_high)):
                try:
                    val_low = int(data.get(reg_low, 0))
                    val_mid = int(data.get(reg_mid, 0))
                    val_high = int(data.get(reg_high, 0))

                    calculated_value = (val_high * 100_000_000) + (val_mid * 10_000) + val_low
                    data[target] = calculated_value
                except (TypeError, ValueError) as err:
                    _LOGGER.error("Failed to calculate %s: %s", target, err)

        self.async_set_updated_data(data)

    async def _async_update_data(self):
        return self.data or {}