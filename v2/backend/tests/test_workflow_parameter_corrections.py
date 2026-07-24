from types import SimpleNamespace

import pytest

from v2.scripts.correct_identity_workflow_parameters import (
    IDENTITY_DENOISE_VALUE_SOURCE,
    correct_identity_denoise,
)
from v2.scripts.expand_runninghub_workflows import workflow_slots


def _draft(*, workflow_id: str = "2073414172825706497", value_source: str = "literal:0.32"):
    binding = SimpleNamespace(
        node_id="24",
        field_path="denoise",
        value_source=value_source,
        value_type="number",
    )
    slot = SimpleNamespace(
        provider_workflow_id=workflow_id,
        node_info_list=[binding],
    )
    return SimpleNamespace(workflow_slots=[slot]), binding


def test_correct_identity_denoise_uses_v1_proven_runtime_value():
    draft, binding = _draft()

    assert correct_identity_denoise(draft) is True
    assert binding.value_source == IDENTITY_DENOISE_VALUE_SOURCE


def test_correct_identity_denoise_is_idempotent():
    draft, binding = _draft(value_source=IDENTITY_DENOISE_VALUE_SOURCE)

    assert correct_identity_denoise(draft) is False
    assert binding.value_source == IDENTITY_DENOISE_VALUE_SOURCE


def test_correct_identity_denoise_requires_exact_workflow():
    draft, _binding = _draft(workflow_id="other-workflow")

    with pytest.raises(RuntimeError, match="Expected exactly one identity workflow"):
        correct_identity_denoise(draft)


def test_identity_workflow_seed_definition_matches_v1_runtime_evidence():
    slot = next(
        item
        for item in workflow_slots("runninghub", "vertical")
        if item.provider_workflow_id == "2073414172825706497"
    )
    binding = next(
        item
        for item in slot.node_info_list
        if item.node_id == "24" and item.field_path == "denoise"
    )

    assert binding.value_source == IDENTITY_DENOISE_VALUE_SOURCE
