"""Share useful health evidence without locations or telemetry identifiers."""


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "version": entry.version,
        "tracking_types": [sub.subentry_type for sub in entry.subentries.values()],
        "snapshot_counts": {key: len(rows) for key, rows in coordinator.snapshots.items()},
        "last_fetched": dict(coordinator.fetched),
        "source_errors": dict(coordinator.errors),
        "source_metadata": dict(coordinator.source_metadata),
        "catalogue_count": len(coordinator.catalogue),
    }
