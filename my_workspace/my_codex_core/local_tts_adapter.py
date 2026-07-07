from __future__ import annotations

import json
import base64
import re
import subprocess
import textwrap
import wave
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
        needs_reference_audio = mode in {"clone", "voice_clone"} or bool(reference_audio)
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
        self._remove_stale_file(output_path)
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
        requested_timeout = _int_or_default(voice_config.get("timeout_seconds"), 1800)
        timeout, timeout_note = self._effective_timeout(mode, requested_timeout, text, command)
        target_duration = _float_or_default(voice_config.get("target_duration_seconds"), 0.0)
        estimated_duration = self._estimate_speech_duration(text, voice_config)

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
            "requested_timeout_seconds": requested_timeout,
            "target_duration_seconds": target_duration,
            "estimated_duration_seconds": estimated_duration,
        }
        if timeout_note:
            manifest["timeout_note"] = timeout_note
        if target_duration and estimated_duration > target_duration * 1.12:
            manifest.update(
                {
                    "status": "quality_failed",
                    "error": (
                        f"Estimated voiceover duration {estimated_duration:.1f}s exceeds the "
                        f"{target_duration:.1f}s target; shorten the script before VoxCPM2 synthesis."
                    ),
                    "duration_overrun_seconds": round(estimated_duration - target_duration, 3),
                }
            )
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return manifest
        if self._should_preempt_voxcpm2_cpu(mode, command, estimated_duration, needs_reference_audio, voice_config):
            manifest.update(
                {
                    "status": "failed",
                    "error": (
                        "VoxCPM2 CPU synthesis was skipped for a long voiceover; "
                        "using Windows SAPI fallback to keep final composition unblocked."
                    ),
                    "fallback_status": "preemptive",
                }
            )
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return self._fallback_after_voxcpm2_failure(voice_text, voice_config, output_dir, manifest)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        result, timed_out, timeout_stdout, timeout_stderr = self._run_shell_command(command, timeout)
        if timed_out:
            self._remove_stale_file(output_path)
            stdout_path.write_text(timeout_stdout or "", encoding="utf-8")
            stderr_path.write_text(timeout_stderr or "", encoding="utf-8")
            manifest.update(
                {
                    "status": "failed",
                    "error": f"VoxCPM2 command timed out after {timeout} seconds",
                    "stdout_file": str(stdout_path),
                    "stderr_file": str(stderr_path),
                }
            )
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return self._fallback_after_voxcpm2_failure(voice_text, voice_config, output_dir, manifest)

        stdout_path.write_text(result.stdout or "", encoding="utf-8")
        stderr_path.write_text(result.stderr or "", encoding="utf-8")
        if result.returncode != 0:
            self._remove_stale_file(output_path)
            manifest.update(
                {
                    "status": "failed",
                    "error": f"VoxCPM2 command exited with code {result.returncode}",
                    "stdout_file": str(stdout_path),
                    "stderr_file": str(stderr_path),
                }
            )
        elif not output_path.is_file():
            self._remove_stale_file(output_path)
            manifest.update(
                {
                    "status": "failed",
                    "error": "VoxCPM2 command finished but voiceover.wav was not created",
                    "stdout_file": str(stdout_path),
                    "stderr_file": str(stderr_path),
                }
            )
        else:
            actual_duration = self._wav_duration(output_path)
            manifest.update(
                {
                    "status": "success",
                    "stdout_file": str(stdout_path),
                    "stderr_file": str(stderr_path),
                    "downloaded_files": [str(output_path)],
                    "output_size_bytes": output_path.stat().st_size,
                    "actual_duration_seconds": actual_duration,
                }
            )
            if target_duration and actual_duration and actual_duration > target_duration + 1.5:
                manifest.update(
                    {
                        "status": "quality_failed",
                        "error": (
                            f"Synthesized voiceover is {actual_duration:.1f}s, longer than the "
                            f"{target_duration:.1f}s target. Shorten the voiceover text or adjust speech rate."
                        ),
                        "duration_overrun_seconds": round(actual_duration - target_duration, 3),
                    }
                )

        if str(manifest.get("status") or "").lower() == "failed":
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return self._fallback_after_voxcpm2_failure(voice_text, voice_config, output_dir, manifest)

        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest

    def _fallback_after_voxcpm2_failure(
        self,
        voice_text: str,
        voice_config: dict[str, Any],
        output_dir: Path,
        primary_manifest: dict[str, Any],
    ) -> dict[str, Any]:
        fallback_provider = str(
            voice_config.get("fallback_provider")
            or voice_config.get("fallback_tts_provider")
            or "windows_sapi"
        ).strip().lower()
        if fallback_provider in {"", "off", "none", "disabled"}:
            primary_manifest["fallback_status"] = "disabled"
            manifest_path = output_dir / "local_tts_manifest.json"
            manifest_path.write_text(json.dumps(primary_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return primary_manifest
        if fallback_provider not in {"windows_sapi", "sapi"}:
            primary_manifest["fallback_status"] = "unsupported"
            primary_manifest["fallback_provider"] = fallback_provider
            manifest_path = output_dir / "local_tts_manifest.json"
            manifest_path.write_text(json.dumps(primary_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return primary_manifest

        fallback_config = dict(voice_config)
        fallback_config["mode"] = "windows_sapi"
        fallback_config["provider"] = "windows_sapi"
        fallback_config["timeout_seconds"] = voice_config.get("fallback_timeout_seconds") or 900
        if voice_config.get("fallback_sapi_rate") is not None:
            fallback_config["sapi_rate"] = _int_or_default(voice_config.get("fallback_sapi_rate"), 3, minimum=-10, maximum=10)
        else:
            recommended_rate = self._recommended_sapi_rate(voice_text, voice_config)
            if recommended_rate:
                fallback_config["sapi_rate"] = recommended_rate
            elif "sapi_rate" not in fallback_config:
                fallback_config["sapi_rate"] = 0
        result = self._run_windows_sapi(voice_text, fallback_config, output_dir)
        result.update(
            {
                "fallback_from_provider": "voxcpm2",
                "fallback_reason": str(primary_manifest.get("error") or "VoxCPM2 synthesis failed"),
                "primary_status": primary_manifest.get("status"),
                "primary_error": primary_manifest.get("error"),
                "primary_stdout_file": primary_manifest.get("stdout_file"),
                "primary_stderr_file": primary_manifest.get("stderr_file"),
            }
        )
        manifest_path = output_dir / "local_tts_manifest.json"
        manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return result

    @staticmethod
    def _effective_timeout(mode: str, requested_timeout: int, text: str, command: str) -> tuple[int, str]:
        timeout = requested_timeout
        note = ""
        command_lower = command.lower()
        is_voxcpm_mode = mode in {"preset", "voxcpm2", "clone", "voice_clone"}
        is_cpu = "--device cpu" in command_lower or " device=cpu" in command_lower
        if is_voxcpm_mode and is_cpu:
            estimated_timeout = min(7200, max(600, int(len(text) * 1.15)))
            if estimated_timeout > timeout:
                timeout = estimated_timeout
                note = (
                    "VoxCPM2 CPU synthesis is slow for long scripts; timeout was automatically "
                    f"raised from {requested_timeout} to {timeout} seconds based on {len(text)} characters."
                )
        return timeout, note

    @staticmethod
    def _should_preempt_voxcpm2_cpu(
        mode: str,
        command: str,
        estimated_duration: float,
        needs_reference_audio: bool,
        voice_config: dict[str, Any],
    ) -> bool:
        if needs_reference_audio:
            return False
        if str(voice_config.get("preemptive_fallback") or "").strip().lower() in {"0", "false", "off", "disabled"}:
            return False
        fallback_provider = str(
            voice_config.get("fallback_provider")
            or voice_config.get("fallback_tts_provider")
            or "windows_sapi"
        ).strip().lower()
        if fallback_provider in {"", "off", "none", "disabled"}:
            return False
        command_lower = command.lower()
        is_voxcpm_mode = mode in {"preset", "voxcpm2", "clone", "voice_clone"}
        is_cpu = "--device cpu" in command_lower or " device=cpu" in command_lower
        threshold = _float_or_default(voice_config.get("preemptive_fallback_min_seconds"), 45.0)
        return is_voxcpm_mode and is_cpu and estimated_duration >= threshold

    @staticmethod
    def _recommended_sapi_rate(text: str, voice_config: dict[str, Any]) -> int:
        explicit = voice_config.get("fallback_sapi_rate")
        if explicit is not None:
            return _int_or_default(explicit, 3, minimum=-10, maximum=10)
        target_duration = _float_or_default(voice_config.get("target_duration_seconds"), 0.0)
        if target_duration <= 0:
            return 0
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", str(text or "")))
        latin_words = len(re.findall(r"[A-Za-z0-9]+", str(text or "")))
        density = cjk_count + latin_words * 2
        if target_duration <= 150 and density >= 350:
            return 3
        if target_duration <= 90 and density >= 220:
            return 4
        return 0

    @staticmethod
    def _estimate_speech_duration(text: str, voice_config: dict[str, Any]) -> float:
        cjk_rate = _float_or_default(voice_config.get("estimated_cjk_chars_per_second"), 5.2)
        cjk_rate = min(8.0, max(3.0, cjk_rate))
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
        latin_words = len(re.findall(r"[A-Za-z0-9]+", text))
        sentence_pauses = len(re.findall(r"[。！？!?]", text)) * 0.28
        short_pauses = len(re.findall(r"[，、；;,:：]", text)) * 0.12
        line_pauses = max(0, len([line for line in text.splitlines() if line.strip()]) - 1) * 0.15
        duration = cjk_count / cjk_rate + latin_words / 2.8 + sentence_pauses + short_pauses + line_pauses
        return round(duration, 3)

    @staticmethod
    def _wav_duration(path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as stream:
                frame_rate = stream.getframerate()
                return round(stream.getnframes() / frame_rate, 3) if frame_rate else 0.0
        except (wave.Error, OSError):
            return 0.0

    def _run_shell_command(self, command: str, timeout: int) -> tuple[subprocess.CompletedProcess[str] | None, bool, str, str]:
        process = subprocess.Popen(
            command,
            cwd=str(self.workspace_root),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr), False, "", ""
        except subprocess.TimeoutExpired:
            self._kill_process_tree(process.pid)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
            return None, True, stdout or "", stderr or ""

    @staticmethod
    def _kill_process_tree(pid: int) -> None:
        if pid <= 0:
            return
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, text=True, timeout=5)
            except Exception:
                pass

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
            "custom tts {voice_preset} {output_file}",
        }
        if command_template.lower().startswith("custom tts "):
            return ""
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
        self._remove_stale_file(output_path)
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
        encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
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
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded_script],
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
            self._remove_stale_file(output_path)
            manifest.update(
                {
                    "status": "failed",
                    "error": f"Windows SAPI command exited with code {result.returncode}",
                    "stdout_file": str(stdout_path),
                    "stderr_file": str(stderr_path),
                }
            )
        elif not output_path.is_file():
            self._remove_stale_file(output_path)
            manifest.update(
                {
                    "status": "failed",
                    "error": "Windows SAPI command finished but voiceover.wav was not created",
                    "stdout_file": str(stdout_path),
                    "stderr_file": str(stderr_path),
                }
            )
        else:
            actual_duration = self._wav_duration(output_path)
            manifest.update(
                {
                    "status": "success",
                    "stdout_file": str(stdout_path),
                    "stderr_file": str(stderr_path),
                    "downloaded_files": [str(output_path)],
                    "output_size_bytes": output_path.stat().st_size,
                    "actual_duration_seconds": actual_duration,
                }
            )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest

    @staticmethod
    def _remove_stale_file(path: Path) -> None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

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


def _float_or_default(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
