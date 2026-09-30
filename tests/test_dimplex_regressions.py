"""Run with: python -m unittest discover -s tests -v."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import HomeAssistant

from custom_components.dimplex_homeassistant_mqtt import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.dimplex_homeassistant_mqtt.binary_sensor import (
    DimplexMqttConnectionSensor,
)
from custom_components.dimplex_homeassistant_mqtt.const import DOMAIN
from custom_components.dimplex_homeassistant_mqtt.config_flow import DimplexMqttConfigFlow
from custom_components.dimplex_homeassistant_mqtt.coordinator import (
    DimplexMqttCoordinator,
    ENERGY_MAPPINGS,
)
from custom_components.dimplex_homeassistant_mqtt.mqtt_client import DimplexMqttClient
from custom_components.dimplex_homeassistant_mqtt.sensor import (
    DimplexMqttSensor,
    SENSOR_DESCRIPTIONS,
)
from custom_components.dimplex_homeassistant_mqtt.utils.discovery import discover_entities
from custom_components.dimplex_homeassistant_mqtt.utils.energy import EnergyCounter
from custom_components.dimplex_homeassistant_mqtt.utils.translations_helper import load_translations
from custom_components.dimplex_homeassistant_mqtt.select import DimplexMqttSelect, SELECT_DESCRIPTIONS

ROOT = Path(__file__).resolve().parents[1]
REGISTERS = ENERGY_MAPPINGS['WMZ_Anz_G_ST1bis4']
TARGET = 'WMZ_Anz_G_ST1bis4'
CONFIG = {'host': 'test', 'port': 61894, 'username': 'test', 'password': 'test'}


class EnergyTests(unittest.TestCase):
    def test_any_single_register_initializes_each_group(self):
        for registers in ENERGY_MAPPINGS.values():
            for register, weight in zip(registers, (1, 10000, 100000000)):
                with self.subTest(register=register):
                    counter = EnergyCounter(registers)
                    self.assertEqual(counter.update({register: 3}), 3 * weight)
                    self.assertEqual(counter.update({register: 4}), 4 * weight)

    def test_cached_parts_are_preserved(self):
        counter = EnergyCounter(REGISTERS)
        self.assertEqual(counter.update({'1660i': 3}), 3)
        self.assertEqual(counter.update({'1662i': 1}), 100000003)
        self.assertEqual(counter.update({'1661i': 2}), 100020003)
        self.assertEqual(counter.update({'1660i': 4}), 100020004)

    def test_separate_rollover_updates_converge_in_both_orders(self):
        for first, second in (({'1660i': 0}, {'1661i': 1}),
                              ({'1661i': 1}, {'1660i': 0})):
            with self.subTest(first=first):
                counter = EnergyCounter(REGISTERS)
                counter.update(dict(zip(REGISTERS, (9999, 0, 0))))
                counter.update(first)
                self.assertEqual(counter.update(second), 10000)

    def test_single_register_reset_and_invalid_values(self):
        counter = EnergyCounter(REGISTERS)
        counter.update({'1660i': 100})
        self.assertEqual(counter.update({'1660i': 0}), 0)
        for value in (None, 'bad', -1, 10000, float('inf')):
            self.assertEqual(counter.update({'1660i': value}), 0)
        self.assertEqual(counter.update({'1660i': 'bad', '1661i': 2}), 20000)


class MqttTests(unittest.TestCase):
    def setUp(self):
        with patch('custom_components.dimplex_homeassistant_mqtt.mqtt_client.mqtt.Client'):
            self.callback = Mock()
            self.client = DimplexMqttClient(CONFIG, on_connection_changed=self.callback)

    def test_numeric_precision(self):
        for value in (21.5, -0.25, 0, 123):
            self.assertEqual(self.client._convert_typed_value(value), value)
        self.assertEqual(self.client._convert_typed_value('21.5'), 21.5)
        self.assertEqual(self.client._convert_typed_value('21', 'int16'), 21)

    def test_connection_callbacks_and_cache_clear(self):
        self.client.subscribe_to_topic = Mock()
        self.client._on_connect(None, None, None, 0)
        self.callback.assert_called_with(True)
        self.client.values['temperature'] = 21
        self.client._on_disconnect(None, None, 1)
        self.callback.assert_called_with(False)
        self.assertFalse(self.client.connected)
        self.assertEqual(self.client.values, {})

    def test_snapshot_waits_for_both_subscriptions(self):
        reply = f'extern/{self.client.client_id}/clear_prev_val_cache_reply'
        broadcast = 'gateway/broadcast/changed_on/#'
        for topics in ((reply, broadcast), (broadcast, reply)):
            self.client.subscribed_topics.clear()
            self.client.pending_subscriptions = dict(enumerate(topics))
            self.client.send_clear_prev_value_cache = Mock()
            self.client._on_subscribe(None, None, 0, [0])
            self.client.send_clear_prev_value_cache.assert_not_called()
            self.client._on_subscribe(None, None, 1, [0])
            self.client.send_clear_prev_value_cache.assert_called_once()

    def test_rejected_subscription_does_not_request_snapshot(self):
        self.client.pending_subscriptions[1] = 'gateway/broadcast/changed_on/#'
        self.client.send_clear_prev_value_cache = Mock()
        self.client._on_subscribe(None, None, 1, [128])
        self.assertEqual(self.client.subscribed_topics, set())
        self.client.send_clear_prev_value_cache.assert_not_called()


class DiscoveryTests(unittest.TestCase):
    def test_add_once_remove_completed_listener(self):
        coordinator = SimpleNamespace(data={}, async_add_listener=Mock(return_value=Mock()))
        entry = SimpleNamespace(async_on_unload=Mock())
        add = Mock()
        descriptions = [SimpleNamespace(key='a'), SimpleNamespace(key='b')]
        discover_entities(coordinator, entry, descriptions, lambda d: d.key, lambda d: d.key, add)
        callback = coordinator.async_add_listener.call_args.args[0]
        coordinator.data = {'a': 1}
        callback()
        callback()
        add.assert_called_once_with(['a'])
        coordinator.data['b'] = 2
        callback()
        self.assertEqual(add.call_count, 2)
        coordinator.async_add_listener.return_value.assert_called_once()
        entry.async_on_unload.call_args.args[0]()
        coordinator.async_add_listener.return_value.assert_called_once()

    def test_unload_removes_pending_listener(self):
        coordinator = SimpleNamespace(data={}, async_add_listener=Mock(return_value=Mock()))
        entry = SimpleNamespace(async_on_unload=Mock())
        discover_entities(coordinator, entry, ['missing'], lambda d: d, lambda d: d, Mock())
        entry.async_on_unload.call_args.args[0]()
        coordinator.async_add_listener.return_value.assert_called_once()


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = HomeAssistant('/tmp/dimplex-regression-tests')
        with patch('custom_components.dimplex_homeassistant_mqtt.coordinator.DimplexMqttClient'):
            self.coordinator = DimplexMqttCoordinator(self.hass, CONFIG)
        self.coordinator._async_handle_connection_changed(True)

    async def asyncTearDown(self):
        await self.coordinator.async_shutdown()

    async def test_duplicate_messages_and_disconnect_availability(self):
        c = self.coordinator
        listener = Mock()
        unsub = c.async_add_listener(listener)
        description = next(d for d in SENSOR_DESCRIPTIONS if d.key not in ENERGY_MAPPINGS)
        sensor = DimplexMqttSensor(c, description)
        c._async_handle_values_changed({description.key: 21.5})
        self.assertTrue(sensor.available)
        c._async_handle_values_changed({description.key: 21.5})
        listener.assert_called_once()
        c._async_handle_connection_changed(False)
        self.assertFalse(sensor.available)
        connection = DimplexMqttConnectionSensor(c)
        self.assertTrue(connection.available)
        self.assertFalse(connection.is_on)
        self.assertEqual(c.data, {})
        c._async_handle_connection_changed(True)
        self.assertFalse(sensor.available)
        self.assertTrue(connection.is_on)
        unsub()

    async def test_energy_alias_does_not_overwrite_total(self):
        c = self.coordinator
        c._async_handle_values_changed({'1661i': 2})
        self.assertEqual(c.data[TARGET], 20000)
        c._async_handle_values_changed({'1660i': 3, TARGET: 3})
        self.assertEqual(c.data[TARGET], 20003)
        c._async_handle_values_changed({'1662i': 1})
        self.assertEqual(c.data[TARGET], 100020003)
        c.client.send_clear_prev_value_cache.assert_not_called()

    async def test_single_register_creates_one_energy_sensor(self):
        from custom_components.dimplex_homeassistant_mqtt.sensor import async_setup_entry
        c = self.coordinator
        self.hass.data[DOMAIN] = {'entry': c}
        entry = SimpleNamespace(entry_id='entry', async_on_unload=Mock())
        add = Mock()
        await async_setup_entry(self.hass, entry, add)
        c._async_handle_values_changed({'1661i': 2})
        add.assert_called_once()
        sensor = add.call_args.args[0][0]
        self.assertEqual(sensor.entity_description.key, TARGET)
        self.assertEqual(sensor.native_value, 20000)
        c._async_handle_values_changed({'1661i': 3})
        add.assert_called_once()
        self.assertEqual(sensor.native_value, 30000)
        entry.async_on_unload.call_args.args[0]()

    async def test_queued_messages_ignored_after_shutdown(self):
        await self.coordinator.async_shutdown()
        self.coordinator._async_handle_values_changed({'test': 1})
        self.assertNotIn('test', self.coordinator.data)

    async def test_select_uses_shared_translations(self):
        load_translations()
        self.hass.config.language = 'de'
        entity = DimplexMqttSelect(self.coordinator, SELECT_DESCRIPTIONS[0])
        self.assertIn('Sommer', entity.options)
        self.hass.config.language = 'en'
        self.assertIn('Summer', entity.options)


class ConfigFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconfigure_reloads_and_preserves_password(self):
        flow = DimplexMqttConfigFlow()
        flow.hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value=None))
        entry = SimpleNamespace(data=CONFIG)
        flow._get_reconfigure_entry = Mock(return_value=entry)
        flow.async_update_reload_and_abort = Mock(return_value={'type': 'abort'})
        await flow.async_step_reconfigure({'host': 'new-host', 'password': ''})
        args = flow.async_update_reload_and_abort.call_args
        self.assertIs(args.args[0], entry)
        self.assertEqual(args.kwargs['data']['host'], 'new-host')
        self.assertEqual(args.kwargs['data']['password'], CONFIG['password'])
        self.assertEqual(args.kwargs['reason'], 'reconfigure_successful')

    async def test_connection_errors_use_translation_key(self):
        for step in ('user', 'reconfigure'):
            with self.subTest(step=step):
                flow = DimplexMqttConfigFlow()
                flow.hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value='mqtt_timeout'))
                flow.async_set_unique_id = AsyncMock()
                flow._abort_if_unique_id_configured = Mock()
                flow._get_reconfigure_entry = Mock(return_value=SimpleNamespace(data=CONFIG))
                flow.async_show_form = Mock()
                await getattr(flow, f'async_step_{step}')({'host': 'test', 'password': 'test'})
                self.assertEqual(flow.async_show_form.call_args.kwargs['errors'], {'base': 'cannot_connect'})


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_unload_retains_running_coordinator(self):
        c = SimpleNamespace(async_shutdown=AsyncMock())
        hass = SimpleNamespace(data={DOMAIN: {'entry': c}}, config_entries=SimpleNamespace(async_unload_platforms=AsyncMock(return_value=False)))
        self.assertFalse(await async_unload_entry(hass, SimpleNamespace(entry_id='entry')))
        self.assertIs(hass.data[DOMAIN]['entry'], c)
        c.async_shutdown.assert_not_awaited()

    async def test_successful_unload_stops_coordinator(self):
        c = SimpleNamespace(async_shutdown=AsyncMock())
        hass = SimpleNamespace(data={DOMAIN: {'entry': c}}, config_entries=SimpleNamespace(async_unload_platforms=AsyncMock(return_value=True)))
        self.assertTrue(await async_unload_entry(hass, SimpleNamespace(entry_id='entry')))
        c.async_shutdown.assert_awaited_once()
        self.assertNotIn(DOMAIN, hass.data)

    async def test_failed_setup_stops_client(self):
        c = SimpleNamespace(async_shutdown=AsyncMock())
        hass = SimpleNamespace(data={}, async_add_executor_job=AsyncMock(), config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock(side_effect=RuntimeError('setup failed'))))
        with patch('custom_components.dimplex_homeassistant_mqtt.DimplexMqttCoordinator', return_value=c):
            with self.assertRaises(RuntimeError):
                await async_setup_entry(hass, SimpleNamespace(data=CONFIG, entry_id='entry'))
        c.async_shutdown.assert_awaited_once()
        self.assertNotIn(DOMAIN, hass.data)


if __name__ == '__main__':
    unittest.main()
