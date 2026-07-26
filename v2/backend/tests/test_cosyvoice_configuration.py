from copy import deepcopy
from types import SimpleNamespace

from v2.backend.app.configuration.contracts import (
    AudioConfigDraft,
    PricingCatalogDraft,
    PricingRuleDraft,
    StoragePolicyDraft,
)
from v2.scripts.enable_cosyvoice_audio import enable_cosyvoice


def _draft():
    return SimpleNamespace(
        providers=[],
        workflow_slots=[],
        audio=AudioConfigDraft(
            config_key="audio",
            display_name="Audio Off",
            supported_modes=["off"],
            sample_rate=24000,
            channels=2,
            format="mp3",
            speaking_rate_min=0.8,
            speaking_rate_max=1.2,
        ),
        storage=StoragePolicyDraft(
            policy_key="storage",
            display_name="Local",
            backend_kind="local",
            allowed_mime_types=["video/mp4"],
            max_file_size_bytes=10_000_000,
            public_url_policy="none",
            local_root_ref="v2.runtime.assets",
        ),
        pricing=PricingCatalogDraft(
            catalog_key="test",
            display_name="Test",
            currency="TEST",
            confirmation_threshold=0,
            rules=[PricingRuleDraft(
                workflow_slot_key="video",
                unit="call",
                unit_price=1,
            )],
        ),
    )


def test_enable_cosyvoice_adds_one_explicit_audio_route() -> None:
    draft = _draft()

    assert enable_cosyvoice(draft) is True
    assert enable_cosyvoice(draft) is False
    assert [provider.adapter_kind for provider in draft.providers] == ["cosyvoice"]
    assert draft.audio.supported_modes == ["off", "voiceover"]
    assert draft.audio.tts_workflow_slot_key == "cosyvoice-voiceover-wav"
    assert draft.audio.format == "wav"
    assert "audio/wav" in draft.storage.allowed_mime_types
    assert draft.pricing.rules[-1].unit == "output_second"
