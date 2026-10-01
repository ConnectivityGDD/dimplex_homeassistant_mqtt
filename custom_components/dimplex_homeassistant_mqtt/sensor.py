"""Sensor platform for Dimplex MQTT."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .utils.discovery import discover_entities
from .coordinator import ENERGY_MAPPINGS, DimplexMqttCoordinator
from .utils.translations_helper import get_translations

SENSOR_CONFIG_FILE = Path(__file__).parent / "sensors.json"

DEVICE_CLASSES = {
    "temperature": SensorDeviceClass.TEMPERATURE,
    "duration": SensorDeviceClass.DURATION,
    "energy": SensorDeviceClass.ENERGY,
    "humidity": SensorDeviceClass.HUMIDITY,
}

STATE_CLASSES = {
    "measurement": SensorStateClass.MEASUREMENT,
    "total": SensorStateClass.TOTAL,
    "total_increasing": SensorStateClass.TOTAL_INCREASING,
}

UNITS = {
    "celsius": UnitOfTemperature.CELSIUS,
    "C": UnitOfTemperature.CELSIUS,
    "h": UnitOfTime.HOURS,
    "min": UnitOfTime.MINUTES,
    "day": UnitOfTime.DAYS,
    "kWh": UnitOfEnergy.KILO_WATT_HOUR,
    "%": PERCENTAGE,
    "rpm": "rpm",
}


@dataclass(frozen=True, kw_only=True)
class DimplexMqttSensorEntityDescription(SensorEntityDescription):
    scale: float = 1.0
    installer_only: bool = False

def _translate_sensor_state(
    language: str,
    translation_key: str,
    raw_value: Any,
) -> str | None:
    translations = get_translations(language)

    return (
        translations
        .get("entity", {})
        .get("sensor", {})
        .get(translation_key, {})
        .get("state", {})
        .get(str(raw_value))
    )


def _walk_sensor_config(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk_sensor_config(item)
        return

    if not isinstance(node, dict):
        return

    # 1. Wenn der Node direkt ein 'id'-Feld besitzt (z. B. "id": "1301a")
    if "id" in node:
        if node.get("read_only", True) and not str(node["id"]).endswith("d"):
            yield node
        return

    # 2. Rekursiv durch Dicts iterieren (für Kategorien wie "energy", "heating")
    for key, value in node.items():
        if isinstance(value, dict):
            # Falls das Unter-Dict keinen eigenen "id"-Key hat (unsere berechneten Keys)
            if "id" not in value and "key" in value:
                value_copy = dict(value)
                value_copy["id"] = key
                if value_copy.get("read_only", True) and not str(key).endswith("d"):
                    yield value_copy
            else:
                yield from _walk_sensor_config(value)


def _load_sensor_descriptions():
    with SENSOR_CONFIG_FILE.open("r", encoding="utf-8") as file:
        config = json.load(file)

    descriptions = []

    for item in _walk_sensor_config(config):
        enabled = item.get("enabled_default", True)
        if "entity_registry_enabled_default" in item:
            enabled = item["entity_registry_enabled_default"]

        descriptions.append(
            DimplexMqttSensorEntityDescription(
                key=item["id"],
                translation_key=item.get("translation_key", item.get("key", item["id"])),
                device_class=DEVICE_CLASSES.get(item.get("device_class")),
                native_unit_of_measurement=UNITS.get(item.get("unit")),
                state_class=STATE_CLASSES.get(item.get("state_class")),
                scale=item.get("scale", 1.0),
                installer_only=item.get("installer_only", item.get("hidden", False)),
                entity_registry_enabled_default=enabled,
            )
        )

    descriptions_by_key = {description.key: description for description in descriptions}
    energy_registers = {
        register for registers in ENERGY_MAPPINGS.values() for register in registers
    }
    combined_descriptions = [
        replace(descriptions_by_key[registers[0]], key=target)
        for target, registers in ENERGY_MAPPINGS.items()
    ]

    return tuple(
        description for description in descriptions
        if description.key not in energy_registers
    ) + tuple(combined_descriptions)


SENSOR_DESCRIPTIONS = _load_sensor_descriptions()


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    installer_access = coordinator.config.get("installer_access", False)

    discover_entities(
        coordinator,
        entry,
        (description for description in SENSOR_DESCRIPTIONS
         if not description.installer_only or installer_access),
        lambda description: description.key,
        lambda description: DimplexMqttSensor(coordinator, description),
        async_add_entities,
    )


class DimplexMqttSensor(CoordinatorEntity[DimplexMqttCoordinator], SensorEntity):
    entity_description: DimplexMqttSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DimplexMqttCoordinator,
        description: DimplexMqttSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)

        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device_id}_{description.key}"
        self._attr_translation_key = description.translation_key or description.key

        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.device_id)},
            "name": "Dimplex MQTT Gateway",
            "manufacturer": "Dimplex",
            "model": "MQTT Gateway",
        }

    @property
    def available(self) -> bool:
        if not self.coordinator.connected or not self.coordinator.last_update_success:
            return False
        data = self.coordinator.data or {}
        return self.entity_description.key in data

    @property
    def native_value(self) -> Any:
        desc = self.entity_description
        data = self.coordinator.data or {}
        raw_value = data.get(desc.key)

        if raw_value is None:
            return None

        language = (self.coordinator.hass.config.language or "en").split("-")[0]

        translated = _translate_sensor_state(
            language,
            desc.translation_key,
            raw_value,
        )

        if translated is not None:
            return translated

        if desc.scale != 1.0:
            try:
                return round(float(raw_value) * desc.scale, 2)
            except (TypeError, ValueError):
                return raw_value

        return raw_value
