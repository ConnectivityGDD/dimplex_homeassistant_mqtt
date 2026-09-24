"""Sensor platform for Dimplex MQTT."""
from __future__ import annotations

from dataclasses import dataclass
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
from .coordinator import DimplexMqttCoordinator

SENSOR_CONFIG_FILE = Path(__file__).parent / "sensors.json"
TRANSLATION_DIR = Path(__file__).parent / "translations"

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


def _load_translation_file(language: str) -> dict[str, Any]:
    path = TRANSLATION_DIR / f"{language}.json"

    if not path.exists():
        path = TRANSLATION_DIR / "en.json"

    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return {}


TRANSLATIONS = {
    "en": _load_translation_file("en"),
    "de": _load_translation_file("de"),
}


def _translate_sensor_state(
    language: str,
    translation_key: str,
    raw_value: Any,
) -> str | None:
    translations = TRANSLATIONS.get(language) or TRANSLATIONS.get("en", {})

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

    return tuple(descriptions)


SENSOR_DESCRIPTIONS = _load_sensor_descriptions()


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator: DimplexMqttCoordinator = hass.data[DOMAIN][entry.entry_id]

    added: set[str] = set()
    installer_access = coordinator.config.get("installer_access", False)

    # Roh-Register der einzelnen Stellen, die NICHT als eigene Sensoren angelegt werden sollen
    IGNORED_RAW_KEYS = {
        # Elektrische Energie Einzelstellen
        "1300u", "1301u", "1302u",
        "1303u", "1304u", "1305u",
        "1306u", "1307u", "1308u",
        # WMZ Einzelstellen
        "1672i", "1673i", "1674i",  # Heizen
        "1660i", "1661i", "1662i",  # Gesamt
        "1663i", "1664i", "1665i",  # Warmwasser
        "1669i", "1670i", "1671i",  # Kühlung / Weitere
    }

    def add_new_sensors():
        data = coordinator.data or {}
        new_entities = []

        for description in SENSOR_DESCRIPTIONS:
            # Ignoriere die Roh-Register der Einzelstellen
            if description.key in IGNORED_RAW_KEYS:
                continue

            if description.installer_only and not installer_access:
                continue

            # Sensor wird NUR angelegt, wenn er tatsächlich im Data-Dict vorhanden ist
            if description.key in data and description.key not in added:
                added.add(description.key)
                new_entities.append(DimplexMqttSensor(coordinator, description))

        if new_entities:
            async_add_entities(new_entities)

    add_new_sensors()
    coordinator.async_add_listener(add_new_sensors)


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
        if not self.coordinator.last_update_success:
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