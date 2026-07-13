from __future__ import annotations

import json
import base64
import re
import subprocess
import textwrap
import wave
from pathlib import Path
from typing import Any
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError


class LocalTTSAdapter:
    """Run local TTS commands against the extracted voiceover text."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def run(self, voice_text: str, voice_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        mode = str(voice_config.get("mode") or "off").strip().lower()
        provider = str(voice_config.get("provider") or "").strip().lower()
        if mode in {"", "off"} or provider in {"", "none"}:
            return {"status": "failed", "error": "TTS provider is not configured"}
        if provider in {"windows_sapi", "sapi"} or mode in {"windows_sapi", "sapi"}:
            return self._run_windows_sapi(voice_text, voice_config, output_dir)
        if provider in {"aliyun_cosyvoice", "cosyvoice"} or mode in {"aliyun_cosyvoice", "cosyvoice"}:
            return self._run_aliyun_cosyvoice(voice_text, voice_config, output_dir)
        if provider != "voxcpm2":
            return {"status": "failed", "error": f"unsupported local TTS provider: {provider}"}

        text = str(voice_text or "").strip()
        if not text:
            return {"status": "skipped", "reason": "voice text is empty"}

        voice_preset = str(voice_config.get("voice_preset") or "warm_female").strip()
        voice_preset_name = str(voice_config.get("voice_preset_name") or voice_preset).strip()
        reference_audio = str(voice_config.get("reference_audio") or "").strip()
        needs_reference_audio = mode in {"clone", "voice_clone"} or bool(reference_audio)
        if needs_reference_audio and not reference_audio:
            return {"status": "failed", "error": "VoxCPM2 reference audio is missing"}
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
            return manifest

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

        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest

    def _run_aliyun_cosyvoice(self, voice_text: str, voice_config: dict[str, Any], output_dir: Path) -> dict[str, Any]:
        text = str(voice_text or "").strip()
        if not text:
            return {"status": "skipped", "reason": "voice text is empty"}

        api_key = str(
            voice_config.get("aliyun_api_key")
            or voice_config.get("api_key")
            or voice_config.get("dashscope_api_key")
            or ""
        ).strip()
        if not api_key:
            return {"status": "failed", "error": "Aliyun CosyVoice API Key is missing"}

        workspace_id = str(voice_config.get("aliyun_workspace_id") or "").strip()
        endpoint = self._aliyun_cosyvoice_endpoint(voice_config)
        model_default = "cosyvoice-v3-flash" if workspace_id else "cosyvoice-v1"
        model = str(voice_config.get("aliyun_model") or voice_config.get("model") or model_default).strip()
        if not workspace_id and model == "cosyvoice-v3-flash":
            model = "cosyvoice-v1"
        voice = self._normalize_aliyun_preset_voice(
            str(voice_config.get("aliyun_voice") or voice_config.get("voice") or "longanyang").strip(),
            model=model,
            is_custom_clone=bool(workspace_id),
        )
        audio_format = str(voice_config.get("aliyun_format") or voice_config.get("format") or "wav").strip().lower()
        if audio_format not in {"mp3", "wav", "pcm", "opus"}:
            audio_format = "mp3"

        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        text_path = output_dir / "aliyun_cosyvoice_text.txt"
        output_path = output_dir / f"voiceover.{audio_format}"
        manifest_path = output_dir / "local_tts_manifest.json"
        response_path = output_dir / "aliyun_cosyvoice_response.json"
        self._remove_stale_file(output_path)
        text_path.write_text(text + "\n", encoding="utf-8")

        payload_input: dict[str, Any] = {
            "text": text,
            "voice": voice,
            "format": audio_format,
            "sample_rate": _int_or_default(voice_config.get("aliyun_sample_rate"), 24000, minimum=8000, maximum=48000),
        }
        optional_specs: tuple[tuple[str, str, str], ...] = (
            ("aliyun_volume", "volume", "int"),
            ("aliyun_rate", "rate", "float"),
            ("aliyun_pitch", "pitch", "float"),
            ("aliyun_bit_rate", "bit_rate", "int"),
            ("aliyun_seed", "seed", "int"),
            ("aliyun_instruction", "instruction", "str"),
            ("aliyun_aigc_propagator", "aigc_propagator", "str"),
            ("aliyun_aigc_propagate_id", "aigc_propagate_id", "str"),
        )
        for config_key, payload_key, value_type in optional_specs:
            value = voice_config.get(config_key)
            if value in (None, ""):
                continue
            try:
                if value_type == "int":
                    payload_input[payload_key] = int(value)
                elif value_type == "float":
                    payload_input[payload_key] = float(value)
                else:
                    payload_input[payload_key] = str(value)
            except (TypeError, ValueError):
                continue

        language_hint = str(voice_config.get("aliyun_language_hint") or "").strip()
        if language_hint:
            payload_input["language_hints"] = [item.strip() for item in language_hint.replace("，", ",").split(",") if item.strip()]
        for config_key, payload_key in (
            ("aliyun_enable_ssml", "enable_ssml"),
            ("aliyun_word_timestamp_enabled", "word_timestamp_enabled"),
            ("aliyun_enable_aigc_tag", "enable_aigc_tag"),
            ("aliyun_enable_markdown_filter", "enable_markdown_filter"),
        ):
            value = voice_config.get(config_key)
            if value not in (None, ""):
                payload_input[payload_key] = _bool_value(value)

        payload = {"model": model, "input": payload_input}
        timeout = _int_or_default(voice_config.get("timeout_seconds"), 600)
        target_duration = _float_or_default(voice_config.get("target_duration_seconds"), 0.0)
        estimated_duration = self._estimate_speech_duration(text, voice_config)
        manifest: dict[str, Any] = {
            "status": "running",
            "provider": "aliyun_cosyvoice",
            "mode": voice_config.get("mode") or "aliyun_cosyvoice",
            "endpoint": endpoint,
            "api_key_provided": True,
            "model": model,
            "voice": voice,
            "voice_kind": "custom_clone" if workspace_id else "preset",
            "format": audio_format,
            "sample_rate": payload_input["sample_rate"],
            "text_file": str(text_path),
            "output_file": str(output_path),
            "timeout_seconds": timeout,
            "target_duration_seconds": target_duration,
            "estimated_duration_seconds": estimated_duration,
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        data, audio, error = self._submit_aliyun_cosyvoice_request(
            endpoint=endpoint,
            api_key=api_key,
            payload=payload,
            output_path=output_path,
            response_path=response_path,
            timeout=timeout,
        )
        if error:
            manifest.update({"status": "failed", "error": error})

        if output_path.is_file():
            actual_duration = self._media_duration(output_path)
            manifest.update(
                {
                    "status": "success",
                    "request_id": data.get("request_id") if isinstance(data, dict) else "",
                    "response_file": str(response_path),
                    "audio_url_expires_at": audio.get("expires_at", ""),
                    "downloaded_files": [str(output_path)],
                    "output_size_bytes": output_path.stat().st_size,
                    "actual_duration_seconds": actual_duration,
                }
            )
            usage = data.get("usage") if isinstance(data, dict) else {}
            if isinstance(usage, dict):
                manifest["usage"] = usage
            if target_duration and actual_duration and actual_duration > target_duration + 1.5:
                retry_rate = self._aliyun_duration_retry_rate(payload_input.get("rate"), actual_duration, target_duration)
                retry_enabled = str(voice_config.get("aliyun_auto_rate_retry") or "true").strip().lower() not in {
                    "0",
                    "false",
                    "off",
                    "disabled",
                }
                if retry_enabled and retry_rate:
                    retry_input = dict(payload_input)
                    retry_input["rate"] = retry_rate
                    retry_payload = {"model": model, "input": retry_input}
                    retry_response_path = output_dir / "aliyun_cosyvoice_response_duration_retry.json"
                    retry_output_path = output_dir / f"voiceover_duration_retry.{audio_format}"
                    self._remove_stale_file(retry_output_path)
                    retry_data, retry_audio, retry_error = self._submit_aliyun_cosyvoice_request(
                        endpoint=endpoint,
                        api_key=api_key,
                        payload=retry_payload,
                        output_path=retry_output_path,
                        response_path=retry_response_path,
                        timeout=timeout,
                    )
                    manifest["duration_retry"] = {
                        "enabled": True,
                        "previous_duration_seconds": actual_duration,
                        "previous_rate": payload_input.get("rate", 1.0),
                        "retry_rate": retry_rate,
                    }
                    if retry_error:
                        manifest["duration_retry"]["error"] = retry_error
                    elif retry_output_path.is_file():
                        retry_duration = self._media_duration(retry_output_path)
                        retry_output_path.replace(output_path)
                        manifest.update(
                            {
                                "status": "success",
                                "request_id": retry_data.get("request_id") if isinstance(retry_data, dict) else "",
                                "response_file": str(retry_response_path),
                                "audio_url_expires_at": retry_audio.get("expires_at", ""),
                                "downloaded_files": [str(output_path)],
                                "output_size_bytes": output_path.stat().st_size,
                                "actual_duration_seconds": retry_duration,
                                "aliyun_rate": retry_rate,
                            }
                        )
                        retry_usage = retry_data.get("usage") if isinstance(retry_data, dict) else {}
                        if isinstance(retry_usage, dict):
                            manifest["usage"] = retry_usage
                        if target_duration and retry_duration and retry_duration > target_duration + 1.5:
                            manifest.update(
                                {
                                    "status": "quality_failed",
                                    "error": (
                                        f"Synthesized voiceover is {retry_duration:.1f}s, longer than the "
                                        f"{target_duration:.1f}s target after automatic rate retry."
                                    ),
                                    "duration_overrun_seconds": round(retry_duration - target_duration, 3),
                                }
                            )
                    data = retry_data or data
                    audio = retry_audio or audio
                if str(manifest.get("status") or "").lower() == "success" and target_duration and manifest.get("actual_duration_seconds"):
                    actual_after_retry = _float_or_default(manifest.get("actual_duration_seconds"), 0.0)
                    if actual_after_retry > target_duration + 1.5:
                        manifest.update(
                            {
                                "status": "quality_failed",
                                "error": (
                                    f"Synthesized voiceover is {actual_after_retry:.1f}s, longer than the "
                                    f"{target_duration:.1f}s target. Shorten the voiceover text or adjust speech rate."
                                ),
                                "duration_overrun_seconds": round(actual_after_retry - target_duration, 3),
                            }
                        )

        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest

    def _submit_aliyun_cosyvoice_request(
        self,
        *,
        endpoint: str,
        api_key: str,
        payload: dict[str, Any],
        output_path: Path,
        response_path: Path,
        timeout: int,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        request = urllib_request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            return {}, {}, self._friendly_aliyun_error(exc.code, error_body)
        except (URLError, TimeoutError, OSError) as exc:
            return {}, {}, f"Aliyun CosyVoice request failed: {exc}"

        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            data = {}
        response_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        audio = ((data.get("output") or {}).get("audio") or {}) if isinstance(data, dict) else {}
        audio_url = str(audio.get("url") or "").strip()
        audio_data = str(audio.get("data") or "").strip()
        try:
            if audio_data:
                output_path.write_bytes(base64.b64decode(audio_data))
            elif audio_url:
                download_request = urllib_request.Request(audio_url, method="GET")
                with urllib_request.urlopen(download_request, timeout=timeout) as response:
                    output_path.write_bytes(response.read())
            else:
                return data, audio, "Aliyun CosyVoice response did not include output.audio.url or output.audio.data"
        except (OSError, ValueError, URLError, TimeoutError) as exc:
            self._remove_stale_file(output_path)
            return data, audio, f"Aliyun CosyVoice audio download failed: {exc}"
        return data, audio, ""

    @staticmethod
    def _aliyun_duration_retry_rate(current_rate: Any, actual_duration: float, target_duration: float) -> float:
        if not actual_duration or not target_duration or actual_duration <= target_duration + 1.5:
            return 0.0
        current = _float_or_default(current_rate, 1.0)
        current = max(0.5, min(current, 2.0))
        ratio = actual_duration / max(1.0, target_duration)
        retry_rate = min(2.0, max(current + 0.1, current * ratio * 1.03))
        if retry_rate <= current + 0.01:
            return 0.0
        return round(retry_rate, 3)

    @staticmethod
    def _normalize_aliyun_preset_voice(voice: str, *, model: str = "cosyvoice-v1", is_custom_clone: bool = False) -> str:
        if is_custom_clone:
            return voice or "longanyang"
        v1_aliases = {
            "longxiaochun_v3": "longxiaochun",
            "longxiaoxia_v3": "longxiaoxia",
            "longshu_v3": "longshu",
        }
        allowed_v1_presets = {
            "longxiaochun",
            "longxiaoxia",
            "longxiaocheng",
            "longxiaobai",
            "longlaotie",
            "longshu",
            "longshuo",
            "longtong",
            "longwan",
            "longcheng",
            "longhua",
            "longjing",
            "longmiao",
            "longyue",
            "longyuan",
            "longfei",
            "longxiang",
            "loongstella",
            "loongbella",
        }
        normalized = v1_aliases.get(voice, voice or "longxiaochun")
        if model == "cosyvoice-v1":
            return normalized if normalized in allowed_v1_presets else "longxiaochun"
        return normalized or "longxiaochun"

    @staticmethod
    def _aliyun_cosyvoice_endpoint(voice_config: dict[str, Any]) -> str:
        endpoint = str(voice_config.get("aliyun_endpoint") or "").strip()
        if endpoint:
            return endpoint
        base_url = str(voice_config.get("aliyun_base_url") or "").strip().rstrip("/")
        workspace_id = str(voice_config.get("aliyun_workspace_id") or "").strip()
        region = str(voice_config.get("aliyun_region") or "cn-beijing").strip() or "cn-beijing"
        if base_url:
            return base_url + "/api/v1/services/audio/tts/SpeechSynthesizer"
        if workspace_id:
            return f"https://{workspace_id}.{region}.maas.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"
        return "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/SpeechSynthesizer"

    @staticmethod
    def _friendly_aliyun_error(status_code: int, error_body: str) -> str:
        text = error_body[:1000]
        try:
            data = json.loads(error_body)
        except Exception:
            data = {}
        message = str(data.get("message") or "") if isinstance(data, dict) else ""
        code = str(data.get("code") or "") if isinstance(data, dict) else ""
        if "error code: 418" in message or "error code: 418" in text:
            return (
                "阿里云 CosyVoice 合成失败：引擎拒绝了本次文本或音色请求。"
                "请确认已选择正确的复刻 voice_id、Workspace ID 和地域；"
                "也建议先用正常中文句子测试，避免随机字符、乱码或过短混杂文本。"
                f" 原始错误：HTTP {status_code} {code}: {text}"
            )
        return f"Aliyun CosyVoice HTTP {status_code}: {text}"

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
                if not frame_rate:
                    return 0.0
                channels = max(1, stream.getnchannels())
                sample_width = max(1, stream.getsampwidth())
                bytes_per_second = frame_rate * channels * sample_width
                declared_audio_bytes = stream.getnframes() * channels * sample_width
                actual_file_bytes = path.stat().st_size
                if declared_audio_bytes > actual_file_bytes + 4096:
                    payload_bytes = LocalTTSAdapter._wav_payload_bytes_from_file_size(path)
                    if payload_bytes and bytes_per_second:
                        return round(payload_bytes / bytes_per_second, 3)
                return round(stream.getnframes() / frame_rate, 3)
        except (wave.Error, OSError):
            return 0.0

    @staticmethod
    def _wav_payload_bytes_from_file_size(path: Path) -> int:
        try:
            file_size = path.stat().st_size
            with path.open("rb") as handle:
                header = handle.read(12)
                if len(header) < 12 or header[:4] not in {b"RIFF", b"RF64"} or header[8:12] != b"WAVE":
                    return 0
                offset = 12
                while offset + 8 <= file_size:
                    handle.seek(offset)
                    chunk_header = handle.read(8)
                    if len(chunk_header) < 8:
                        return 0
                    chunk_id = chunk_header[:4]
                    chunk_size = int.from_bytes(chunk_header[4:8], "little", signed=False)
                    payload_offset = offset + 8
                    if chunk_id == b"data":
                        return max(0, file_size - payload_offset)
                    next_offset = payload_offset + chunk_size + (chunk_size % 2)
                    if next_offset <= offset:
                        return 0
                    offset = next_offset
        except OSError:
            return 0
        return 0

    def _media_duration(self, path: Path) -> float:
        if path.suffix.lower() == ".wav":
            duration = self._wav_duration(path)
            if duration:
                return duration
        ffprobe = self.workspace_root.parent / "runtime" / "ffmpeg" / "bin" / "ffprobe.exe"
        if not ffprobe.is_file():
            ffprobe = Path("ffprobe")
        try:
            result = subprocess.run(
                [
                    str(ffprobe),
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                return round(float(str(result.stdout or "").strip()), 3)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
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


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enable", "enabled"}
