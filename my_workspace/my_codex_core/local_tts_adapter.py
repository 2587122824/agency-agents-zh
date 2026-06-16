from __future__ import annotations

import json
import subprocess
import textwrap
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
        if provider in {"windows_sapi", "sapi"} or mode in {"windows_sapi", "sapi"}:
            return self._run_windows_sapi(voice_text, voice_config, output_dir)
        if provider != "voxcpm2":
            return {"status": "skipped", "reason": f"unsupported local TTS provider: {provider}"}

        text = str(voice_text or "").strip()
        if not text:
            return {"status": "skipped", "reason": "voice text is empty"}

        voice_preset = str(voice_config.get("voice_preset") or "warm_female").strip()
        voice_preset_name = str(voice_config.get("voice_preset_name") or voice_preset).strip()
        reference_audio = str(voice_config.get("reference_audio") or "").strip()
        needs_reference_audio = mode in {"voxcpm2", "clone", "voice_clone"}
        if needs_reference_audio and not reference_audio:
            return {"status": "skipped", "reason": "VoxCPM2 reference audio is missing"}
        if reference_audio:
            reference_audio_path = Path(reference_audio)
            if not reference_audio_path.is_absolute():
                reference_audio_path = (self.workspace_root / reference_audio_path).resolve()
            else:
                reference_audio_path = reference_audio_path.resolve()
        else:
            reference_audio_path = None

        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        text_path = output_dir / "voxcpm2_voice_text.txt"
        output_path = output_dir / "voiceover.wav"
        manifest_path = output_dir / "local_tts_manifest.json"
        stdout_path = output_dir / "voxcpm2_stdout.txt"
        stderr_path = output_dir / "voxcpm2_stderr.txt"
        text_path.write_text(text + "\n", encoding="utf-8")

        command_template = self._normalize_command_template(str(voice_config.get("command_template") or ""))
        if not command_template:
            command_template = self._default_command_template(needs_reference_audio)

        prompt_text = str(voice_config.get("reference_text") or "").strip()
        cache_dir = str((self.workspace_root.parent / "runtime" / "tts" / "cache").resolve())
        command = (
            command_template.replace("{text}", _quote_arg(text))
            .replace("{text_file}", _quote_arg(str(text_path)))
            .replace("{reference_audio}", _quote_arg(str(reference_audio_path or "")))
            .replace("{reference_text}", _quote_arg(prompt_text))
            .replace("{voice_preset}", _quote_arg(voice_preset))
            .replace("{voice_name}", _quote_arg(voice_preset_name))
            .replace("{cache_dir}", _quote_arg(cache_dir))
            .replace("{output_file}", _quote_arg(str(output_path)))
        )
        timeout = _int_or_default(voice_config.get("timeout_seconds"), 1800)

        manifest: dict[str, Any] = {
            "status": "running",
            "provider": "voxcpm2",
            "mode": mode,
            "text_file": str(text_path),
            "reference_audio_provided": bool(reference_audio),
            "reference_audio": str(reference_audio_path or ""),
            "reference_text_provided": bool(prompt_text),
            "voice_preset": voice_preset,
            "voice_preset_name": voice_preset_name,
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

    def _default_command_template(self, needs_reference_audio: bool) -> str:
        project_root = self.workspace_root.parent
        runtime_python = project_root / "runtime" / "tts" / "venv" / "Scripts" / "python.exe"
        runner = self.workspace_root / "my_codex_core" / "voxcpm2_tts_runner.py"
        cache_dir = project_root / "runtime" / "tts" / "cache"
        if runtime_python.is_file() and runner.is_file():
            command = (
                f'{_quote_arg(str(runtime_python))} {_quote_arg(str(runner))} '
                "--text-file {text_file} --output {output_file} "
                "--voice-preset {voice_preset} --cache-dir {cache_dir} "
                "--device cpu --no-denoiser --no-optimize"
            )
            if needs_reference_audio:
                command += " --reference-audio {reference_audio} --reference-text {reference_text}"
            return command
        if needs_reference_audio:
            return "voxcpm clone --text {text} --reference-audio {reference_audio} --prompt-text {reference_text} --output {output_file}"
        return "voxcpm design --text {text} --control {voice_name} --output {output_file}"

    @staticmethod
    def _normalize_command_template(command_template: str) -> str:
        command_template = command_template.strip()
        stale_defaults = {
            "voxcpm tts --text-file {text_file} --voice {voice_preset} --output {output_file}",
            "voxcpm clone --text-file {text_file} --reference-audio {reference_audio} --output {output_file}",
        }
        return "" if command_template in stale_defaults else command_template

    def _run_windows_sapi(self, voice_text: str, voice_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        text = str(voice_text or "").strip()
        if not text:
            return {"status": "skipped", "reason": "voice text is empty"}
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        text_path = output_dir / "sapi_voice_text.txt"
        output_path = output_dir / "voiceover.wav"
        manifest_path = output_dir / "local_tts_manifest.json"
        stdout_path = output_dir / "windows_sapi_stdout.txt"
        stderr_path = output_dir / "windows_sapi_stderr.txt"
        script_path = output_dir / "windows_sapi_tts.ps1"
        text_path.write_text(text + "\n", encoding="utf-8")
        rate = _int_or_default(voice_config.get("sapi_rate"), 0, minimum=-10, maximum=10)
        volume = _int_or_default(voice_config.get("sapi_volume"), 100, minimum=0, maximum=100)
        script = textwrap.dedent(
            f"""
            $ErrorActionPreference = "Stop"
            Add-Type -AssemblyName System.Speech
            $text = Get-Content -LiteralPath {self._ps_literal(str(text_path))} -Raw -Encoding UTF8
            $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
            $synth.Rate = {rate}
            $synth.Volume = {volume}
            $synth.SetOutputToWaveFile({self._ps_literal(str(output_path))})
            $synth.Speak($text)
            $synth.Dispose()
            """
        ).strip()
        script_path.write_text(script + "\n", encoding="utf-8")
        manifest = {
            "status": "running",
            "provider": "windows_sapi",
            "mode": voice_config.get("mode") or "windows_sapi",
            "text_file": str(text_path),
            "output_file": str(output_path),
            "rate": rate,
            "volume": volume,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
            cwd=str(self.workspace_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_int_or_default(voice_config.get("timeout_seconds"), 600),
        )
        stdout_path.write_text(result.stdout or "", encoding="utf-8")
        stderr_path.write_text(result.stderr or "", encoding="utf-8")
        if result.returncode != 0:
            manifest.update(
                {
                    "status": "failed",
                    "error": f"Windows SAPI command exited with code {result.returncode}",
                    "stdout_file": str(stdout_path),
                    "stderr_file": str(stderr_path),
                }
            )
        elif not output_path.is_file():
            manifest.update(
                {
                    "status": "failed",
                    "error": "Windows SAPI command finished but voiceover.wav was not created",
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

    @staticmethod
    def _ps_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"


def _quote_arg(value: str) -> str:
    return '"' + str(value).replace('"', '\\"') + '"'


def _int_or_default(value: Any, default: int, minimum: int = 30, maximum: int = 7200) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))
