"""Discover each configured entity once and clean up its listener on unload."""


def discover_entities(coordinator, entry, descriptions, key, factory, add_entities):
    pending = {key(description): description for description in descriptions}
    unsubscribe = None

    def discover():
        nonlocal unsubscribe
        data = coordinator.data or {}
        ready = pending.keys() & data.keys()
        if ready:
            entities = [factory(pending[item]) for item in sorted(ready)]
            add_entities(entities)
            for item in ready:
                del pending[item]
        if not pending and unsubscribe is not None:
            unsubscribe()
            unsubscribe = None

    def cleanup():
        nonlocal unsubscribe
        if unsubscribe is not None:
            unsubscribe()
            unsubscribe = None

    discover()
    if pending:
        unsubscribe = coordinator.async_add_listener(discover)
        entry.async_on_unload(cleanup)
