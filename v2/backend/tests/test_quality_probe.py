from __future__ import annotations

import pytest

from v2.backend.app.quality.service import _probe


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
