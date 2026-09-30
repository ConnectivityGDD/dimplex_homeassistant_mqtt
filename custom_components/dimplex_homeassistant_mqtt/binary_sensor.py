from dataclasses import dataclass
from typing import Any
import json
from pathlib import Path

from homeassistant.helpers.entity import EntityCategory
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .utils.discovery import discover_entities
from .utils.translations_helper import get_translations

BINARY_SENSOR_CONFIG_FILE = Path(__file__).parent / "sensors.json"
TRANSLATION_DIR = Path(__file__).parent / "translations"

DEVICE_CLASSES = {
    "connectivity": BinarySensorDeviceClass.CONNECTIVITY,
    "running": BinarySensorDeviceClass.RUNNING,
    "problem": BinarySensorDeviceClass.PROBLEM,
}


@dataclass(frozen=True, kw_only=True)
class DimplexMqttBinarySensorEntityDescription(BinarySensorEntityDescription):
    id: str
    read_only: bool = True
    hidden: bool = False


def _walk_sensor_config(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk_sensor_config(item)
        return

    if not isinstance(node, dict):
        return

    for object_key, value in node.items():
        if (
            isinstance(value, dict)
            and "id" in value
            and value["id"].endswith("d")
        ):
            yield value
        else:
            yield from _walk_sensor_config(value)


def _load_binary_sensor_descriptions():
    with BINARY_SENSOR_CONFIG_FILE.open("r", encoding="utf-8") as file:
        config = json.load(file)

    descriptions = []

    for item in _walk_sensor_config(config):
        descriptions.append(
            DimplexMqttBinarySensorEntityDescription(
                key=item["key"],
                id=item["id"],
                translation_key=item.get("translation_key", item["key"]),
                device_class=DEVICE_CLASSES.get(
                    item.get("device_class", "running")
                ),
                read_only=item.get("read_only", True),
                    hidden=item.get("hidden", False),
            )
        )

    return tuple(descriptions)


BINARY_SENSOR_DESCRIPTIONS = _load_binary_sensor_descriptions()


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    installer_access = coordinator.config.get("installer_access", False)
    async_add_entities([DimplexMqttConnectionSensor(coordinator)])

    discover_entities(
        coordinator,
        entry,
        (description for description in BINARY_SENSOR_DESCRIPTIONS
         if not description.hidden or installer_access),
        lambda description: description.id,
        lambda description: DimplexMqttBinarySensor(coordinator, description),
        async_add_entities,
    )


class DimplexMqttBinarySensor(CoordinatorEntity, BinarySensorEntity):
    entity_description: DimplexMqttBinarySensorEntityDescription

    def __init__(self, coordinator, description):
        super().__init__(coordinator)

        self.entity_description = description
        self._attr_has_entity_name = False
        self._attr_translation_key = description.translation_key
        translations = get_translations(
            (coordinator.hass.config.language or "en").split("-")[0]
        )
        entities = translations.get("entity", {})
        self._attr_name = description.translation_key
        for category in ("binary_sensor", "sensor", "number", "select"):
            name = entities.get(category, {}).get(
                description.translation_key, {}
            ).get("name")
            if name is not None:
                self._attr_name = name
                break
        self._attr_unique_id = f"{coordinator.device_id}_{description.id}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.device_id)},
            "name": "Dimplex MQTT Gateway",
            "manufacturer": "Dimplex",
            "model": "MQTT Gateway",
        }

    @property
    def available(self):
        data = self.coordinator.data or {}
        return self.coordinator.connected and self.entity_description.id in data

    @property
    def is_on(self):
        data = self.coordinator.data or {}
        value = data.get(self.entity_description.id)

        if value is None:
            return None

        return str(value) in ("1", "true", "True", "on", "ON")
    


class DimplexMqttConnectionSensor(
    CoordinatorEntity,
    BinarySensorEntity,
):
    def __init__(self, coordinator):
        super().__init__(coordinator)

        self._attr_has_entity_name = True
        self._attr_translation_key = "mqtt_connection"
        self._attr_unique_id = (
            f"{coordinator.device_id}_mqtt_connection"
        )

        self._attr_device_class = (
            BinarySensorDeviceClass.CONNECTIVITY
        )

        self._attr_entity_category = (
            EntityCategory.DIAGNOSTIC
        )

        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator.device_id)},
            "name": "Dimplex MQTT Gateway",
            "manufacturer": "Dimplex",
            "model": "MQTT Gateway",
        }

    @property
    def available(self):
        return True

    @property
    def is_on(self):
        return self.coordinator.connected
