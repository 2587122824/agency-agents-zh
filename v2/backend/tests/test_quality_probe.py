from __future__ import annotations

import math
import struct
import wave
from types import SimpleNamespace

import pytest

from v2.backend.app.quality.service import _analyze_pcm_wav, _audio_qc_findings, _probe


def test_subtitle_probe_reads_strict_srt_and_reports_last_cue_duration(tmp_path) -> None:
    path = tmp_path / "subtitles.srt"
    path.write_text(
        "1\n00:00:00,000 --> 00:00:01,200\n第一句\n\n"
        "2\n00:00:01,500 --> 00:00:03,000\n第二句\n",
        encoding="utf-8",
    )

    assert _probe(path, "subtitle") == {
        "mime_type": "application/x-subrip",
        "width": None,
        "height": None,
        "duration_ms": 3000,
    }


@pytest.mark.parametrize("content", [
    "2\n00:00:00,000 --> 00:00:01,000\n序号错误\n",
    "1\n00:00:01,000 --> 00:00:00,900\n倒序时间\n",
    "1\n00:00:00,000 --> 00:00:02,000\n第一句\n\n2\n00:00:01,900 --> 00:00:03,000\n重叠\n",
])
def test_subtitle_probe_rejects_malformed_sequence_or_timing(tmp_path, content: str) -> None:
    path = tmp_path / "invalid.srt"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        _probe(path, "subtitle")


def test_audio_qc_measures_pcm_silence_clipping_loudness_and_true_peak(monkeypatch, tmp_path) -> None:
    path = tmp_path / "voice.wav"
    sample_rate = 24_000
    samples = [
        round(8000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        for index in range(sample_rate)
    ]
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    fake_ffmpeg = tmp_path / "ffmpeg.exe"
    fake_ffmpeg.write_bytes(b"fake")
    monkeypatch.setenv("V2_FFMPEG_PATH", str(fake_ffmpeg))
    monkeypatch.setattr(
        "v2.backend.app.quality.service.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stderr="Summary:\n  Integrated loudness:\n    I: -16.2 LUFS\n  True peak:\n    Peak: -12.1 dBFS\n",
        ),
    )

    evidence = _analyze_pcm_wav(path)

    assert evidence["sample_rate"] == 24_000
    assert evidence["channels"] == 1
    assert evidence["silence_ratio"] <= 0.01
    assert evidence["clipped_sample_ratio"] == 0
    assert evidence["integrated_loudness_lufs"] == -16.2
    assert evidence["true_peak_dbtp"] == -12.1
    monkeypatch.setattr("v2.backend.app.quality.service._local_asset_path", lambda _uri: path)
    findings = _audio_qc_findings(
        SimpleNamespace(uri="runtime://assets/voice.wav"),
        SimpleNamespace(
            output_contract={"sample_rate": 24_000, "channels": 1},
            input_contract={"loudness_target_lufs": -16},
        ),
    )
    assert [finding["code"] for finding in findings] == ["AUDIO_TECHNICAL_QC_PASSED"]
    assert findings[0]["disposition"] == "manual_review"
