"""Shared permission checks for dashboard and response-returning actions."""

from homeassistant.helpers import entity_registry as er


def can_read(hass, user, entry, sid, visited=None):
    """Check each path separately: shared descendants are not reference cycles."""
    visited = set() if visited is None else visited
    if sid in visited or sid not in entry.subentries:
        return False
    if not user.is_admin:
        owned = [
            entity
            for entity in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
            if entity.config_subentry_id == sid
        ]
        # A view includes the whole profile, so every entity must be readable.
        # Fail closed during setup before entities have been registered.
        if not owned or any(
            not user.permissions.check_entity(entity.entity_id, "read") for entity in owned
        ):
            return False
    path = visited | {sid}
    for key, value in entry.subentries[sid].data.items():
        if (key.endswith("_entity") or key == "zone") and value:
            if not user.permissions.check_entity(value, "read"):
                return False
        if (
            key in ("vehicle_id", "search_id")
            and value
            and not can_read(hass, user, entry, value, path)
        ):
            return False
    for sub in entry.subentries.values():
        if sub.subentry_type == "discount" and (entity := sub.data.get("eligibility_entity")):
            if not user.permissions.check_entity(entity, "read"):
                return False
    return True
