"""Validate shipped blueprints and substituted automations using Home Assistant."""

from pathlib import Path

import pytest
from homeassistant.components.automation.config import (
    AUTOMATION_BLUEPRINT_SCHEMA,
    async_validate_config_item,
)
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.util.yaml import load_yaml


@pytest.mark.parametrize("path", sorted(Path("blueprints/automation").glob("*.yml")))
async def test_blueprint_accepts_inputs_and_builds_valid_automation(hass, path):
    blueprint = Blueprint(
        load_yaml(str(path)), expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA
    )
    assert blueprint.validate() is None
    inputs = {key: "sensor.test" for key in blueprint.inputs if key.endswith("_entity")}
    if "comparison" in blueprint.inputs:
        inputs["comparison"] = "binary_sensor.test"
    inputs["action_to_run"] = [
        {"action": "persistent_notification.create", "data": {"message": "FuelWatch test"}}
    ]
    supplied = BlueprintInputs(blueprint, {"use_blueprint": {"input": inputs}})
    supplied.validate()
    result = await async_validate_config_item(hass, "automation", supplied.async_substitute())
    assert result is not None
