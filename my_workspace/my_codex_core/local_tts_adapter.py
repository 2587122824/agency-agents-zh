from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class LocalTTSAdapter:
    """Run local TTS commands against the extracted voiceover text."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def run(self, voice_text: str, voice_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        mode = str(voice_config.get("mode") or "off").strip().lower()
        provider = str(voice_config.get("provider") or "").strip().lower()
        if mode in {"", "off"} or provider in {"", "none"}:
            return {"status": "skipped", "reason": "local TTS is disabled"}
        if provider != "voxcpm2":
            return {"status": "skipped", "reason": f"unsupported local TTS provider: {provider}"}

        text = str(voice_text or "").strip()
        if not text:
            return {"status": "skipped", "reason": "voice text is empty"}

        reference_audio = str(voice_config.get("reference_audio") or "").strip()
        if not reference_audio:
            return {"status": "skipped", "reason": "VoxCPM2 reference audio is missing"}
        reference_audio_path = Path(reference_audio)
        if not reference_audio_path.is_absolute():
            reference_audio_path = (self.workspace_root / reference_audio_path).resolve()
        else:
            reference_audio_path = reference_audio_path.resolve()

        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        text_path = output_dir / "voxcpm2_voice_text.txt"
        output_path = output_dir / "voiceover.wav"
        manifest_path = output_dir / "local_tts_manifest.json"
        stdout_path = output_dir / "voxcpm2_stdout.txt"
        stderr_path = output_dir / "voxcpm2_stderr.txt"
        text_path.write_text(text + "\n", encoding="utf-8")

        command_template = str(voice_config.get("command_template") or "").strip()
        if not command_template:
            command_template = (
                "voxcpm clone --text-file {text_file} "
                "--reference-audio {reference_audio} --output {output_file}"
            )

        prompt_text = str(voice_config.get("reference_text") or "").strip()
        command = (
            command_template.replace("{text}", _quote_arg(text))
            .replace("{text_file}", _quote_arg(str(text_path)))
            .replace("{reference_audio}", _quote_arg(str(reference_audio_path)))
            .replace("{reference_text}", _quote_arg(prompt_text))
            .replace("{output_file}", _quote_arg(str(output_path)))
        )
        timeout = _int_or_default(voice_config.get("timeout_seconds"), 1800)

        manifest: dict[str, Any] = {
            "status": "running",
            "provider": "voxcpm2",
            "mode": mode,
            "text_file": str(text_path),
            "reference_audio_provided": bool(reference_audio),
            "reference_audio": str(reference_audio_path),
            "reference_text_provided": bool(prompt_text),
            "output_file": str(output_path),
            "command_template": command_template,
            "timeout_seconds": timeout,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        try:
            result = subprocess.run(
                command,
                cwd=str(self.workspace_root),
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_path.write_text(exc.stdout or "", encoding="utf-8")
            stderr_path.write_text(exc.stderr or "", encoding="utf-8")
            manifest.update(
                {
                    "status": "failed",
                    "error": f"VoxCPM2 command timed out after {timeout} seconds",
                    "stdout_file": str(stdout_path),
                    "stderr_file": str(stderr_path),
                }
            )
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return manifest

        stdout_path.write_text(result.stdout or "", encoding="utf-8")
        stderr_path.write_text(result.stderr or "", encoding="utf-8")
        if result.returncode != 0:
            manifest.update(
                {
                    "status": "failed",
                    "error": f"VoxCPM2 command exited with code {result.returncode}",
                    "stdout_file": str(stdout_path),
                    "stderr_file": str(stderr_path),
                }
            )
        elif not output_path.is_file():
            manifest.update(
                {
                    "status": "failed",
                    "error": "VoxCPM2 command finished but voiceover.wav was not created",
                    "stdout_file": str(stdout_path),
                    "stderr_file": str(stderr_path),
                }
            )
        else:
            manifest.update(
                {
                    "status": "success",
                    "stdout_file": str(stdout_path),
                    "stderr_file": str(stderr_path),
                    "downloaded_files": [str(output_path)],
                    "output_size_bytes": output_path.stat().st_size,
                }
            )

        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest


def _quote_arg(value: str) -> str:
    return '"' + str(value).replace('"', '\\"') + '"'


def _int_or_default(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(30, min(parsed, 7200))
