from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
DEFAULT_SUBTITLE_STYLE = "FontName=Microsoft YaHei,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,Alignment=2,MarginV=72"


class LocalFFmpegAdapter:
    """Create a local preview/final draft video with FFmpeg when assets exist."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.project_root = self.workspace_root.parent

    def run(
        self,
        task_dir: Path,
        paths: dict[str, Path],
        compose_config: dict[str, Any],
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        tool = str(compose_config.get("tool") or "").strip().lower()
        if tool not in {"", "ffmpeg"}:
            return {"status": "skipped", "reason": f"compose tool is not ffmpeg: {tool}"}

        output_file = Path(manifest.get("composition", {}).get("target_file") or task_dir / "final_video.mp4")
        if not output_file.is_absolute():
            output_file = (task_dir / output_file).resolve()
        else:
            output_file = output_file.resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)

        manifest_path = task_dir / "local_ffmpeg_manifest.json"
        stdout_path = task_dir / "local_ffmpeg_stdout.txt"
        stderr_path = task_dir / "local_ffmpeg_stderr.txt"
        command_path = task_dir / "local_ffmpeg_command.txt"
        timeline_path = task_dir / "edit_timeline.json"
        edit_plan_path = task_dir / "ffmpeg_edit_plan.md"

        ffmpeg_path = self.find_ffmpeg(str(compose_config.get("ffmpeg_path") or ""))
        result_manifest: dict[str, Any] = {
            "status": "running",
            "tool": "ffmpeg",
            "ffmpeg_path": ffmpeg_path or "",
            "output_file": str(output_file),
            "stdout_file": str(stdout_path),
            "stderr_file": str(stderr_path),
            "command_file": str(command_path),
            "timeline_file": str(timeline_path),
            "edit_plan_file": str(edit_plan_path),
        }

        if not ffmpeg_path:
            result_manifest.update(
                {
                    "status": "skipped",
                    "reason": "ffmpeg executable not found; put ffmpeg.exe in runtime/ffmpeg/bin or add it to PATH",
                }
            )
            self._write_json(manifest_path, result_manifest)
            return result_manifest

        video_files = self._dedupe_paths(
            [
                *self._collect_files(paths.get("video_clips"), VIDEO_EXTENSIONS, recursive=True),
                *self._collect_files(paths.get("comfyui"), VIDEO_EXTENSIONS, recursive=True),
            ]
        )
        image_files = self._dedupe_paths(
            [
                *self._collect_files(paths.get("generated_images"), IMAGE_EXTENSIONS, recursive=True),
                *self._collect_files(paths.get("comfyui"), IMAGE_EXTENSIONS, recursive=True),
            ]
        )
        audio_file = self._find_audio_file(paths.get("audio"), manifest)
        bgm_file = self._find_bgm_file(manifest)
        subtitles_file = Path(str(manifest.get("files", {}).get("subtitles") or ""))
        if not subtitles_file.is_absolute():
            subtitles_file = (task_dir / subtitles_file).resolve()
        burn_subtitles = (
            subtitles_file.is_file()
            and _bool_or_default(compose_config.get("burn_subtitles"), True)
            and self._subtitles_are_burnable(subtitles_file, manifest)
        )
        subtitle_style = str(compose_config.get("subtitle_style") or DEFAULT_SUBTITLE_STYLE).strip()
        render = manifest.get("global_context", {}).get("render", {}) if isinstance(manifest.get("global_context"), dict) else {}
        delivery = render.get("delivery_resolution", {}) if isinstance(render.get("delivery_resolution"), dict) else {}
        output_width = _int_or_default(compose_config.get("delivery_width") or delivery.get("width"), 1920)
        output_height = _int_or_default(compose_config.get("delivery_height") or delivery.get("height"), 1080)
        output_fps = _int_or_default(compose_config.get("fps") or render.get("fps"), 24)

        if video_files:
            command, input_files = self._build_video_concat_command(
                ffmpeg_path=ffmpeg_path,
                task_dir=task_dir,
                video_files=video_files,
                audio_file=audio_file,
                bgm_file=bgm_file,
                subtitles_file=subtitles_file if burn_subtitles else None,
                subtitle_style=subtitle_style,
                output_width=output_width,
                output_height=output_height,
                output_fps=output_fps,
                output_file=output_file,
            )
        elif image_files:
            command, input_files = self._build_image_slideshow_command(
                ffmpeg_path=ffmpeg_path,
                task_dir=task_dir,
                image_files=image_files,
                audio_file=audio_file,
                bgm_file=bgm_file,
                subtitles_file=subtitles_file if burn_subtitles else None,
                subtitle_style=subtitle_style,
                output_width=output_width,
                output_height=output_height,
                output_fps=output_fps,
                output_file=output_file,
            )
        elif audio_file:
            command, input_files = self._build_audio_card_command(
                ffmpeg_path=ffmpeg_path,
                audio_file=audio_file,
                bgm_file=bgm_file,
                subtitles_file=subtitles_file if burn_subtitles else None,
                subtitle_style=subtitle_style,
                output_width=output_width,
                output_height=output_height,
                output_fps=output_fps,
                output_file=output_file,
            )
        else:
            result_manifest.update(
                {
                    "status": "skipped",
                    "reason": "no video clips, images, or generated voiceover audio found for ffmpeg composition",
                    "video_files": [],
                    "image_files": [],
                    "audio_file": "",
                    "subtitles_file": str(subtitles_file) if subtitles_file.is_file() else "",
                }
            )
            self._write_json(manifest_path, result_manifest)
            return result_manifest

        timeline = self._build_timeline(
            video_files=video_files,
            image_files=image_files,
            audio_file=audio_file,
            bgm_file=bgm_file,
            subtitles_file=subtitles_file if subtitles_file.is_file() else None,
            output_file=output_file,
            burn_subtitles=burn_subtitles,
        )
        self._write_json(timeline_path, timeline)
        edit_plan_path.write_text(self._build_edit_plan(timeline, subtitle_style), encoding="utf-8")
        command_path.write_text(self._format_command(command) + "\n", encoding="utf-8")
        result_manifest.update(
            {
                "input_files": [str(path) for path in input_files],
                "video_files": [str(path) for path in video_files],
                "image_files": [str(path) for path in image_files],
                "audio_file": str(audio_file) if audio_file else "",
                "bgm_file": str(bgm_file) if bgm_file else "",
                "subtitles_file": str(subtitles_file) if subtitles_file.is_file() else "",
                "subtitle_mode": "burned_in" if burn_subtitles else "sidecar_only",
                "subtitle_burn_skipped_reason": "" if burn_subtitles else self._subtitle_skip_reason(subtitles_file, manifest),
                "subtitle_style": subtitle_style if burn_subtitles else "",
                "render": {"width": output_width, "height": output_height, "fps": output_fps},
                "note": "Local FFmpeg creates an editable preview/final draft from available clips/images/audio. When subtitles.srt exists, subtitles are burned in by default.",
            }
        )
        self._write_json(manifest_path, result_manifest)

        timeout = _int_or_default(compose_config.get("ffmpeg_timeout_seconds"), 3600)
        try:
            completed = subprocess.run(
                command,
                cwd=str(task_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_path.write_text(exc.stdout or "", encoding="utf-8")
            stderr_path.write_text(exc.stderr or "", encoding="utf-8")
            result_manifest.update(
                {
                    "status": "failed",
                    "error": f"ffmpeg command timed out after {timeout} seconds",
                    "timeout_seconds": timeout,
                }
            )
            self._write_json(manifest_path, result_manifest)
            return result_manifest

        stdout_path.write_text(completed.stdout or "", encoding="utf-8")
        stderr_path.write_text(completed.stderr or "", encoding="utf-8")
        if completed.returncode != 0:
            result_manifest.update(
                {
                    "status": "failed",
                    "error": f"ffmpeg exited with code {completed.returncode}",
                    "timeout_seconds": timeout,
                }
            )
        elif not output_file.is_file():
            result_manifest.update(
                {
                    "status": "failed",
                    "error": "ffmpeg finished but output video was not created",
                    "timeout_seconds": timeout,
                }
            )
        else:
            result_manifest.update(
                {
                    "status": "success",
                    "timeout_seconds": timeout,
                    "downloaded_files": [str(output_file)],
                    "output_size_bytes": output_file.stat().st_size,
                    "timeline_file": str(timeline_path),
                    "edit_plan_file": str(edit_plan_path),
                }
            )

        self._write_json(manifest_path, result_manifest)
        return result_manifest

    def find_ffmpeg(self, explicit_path: str = "") -> str:
        candidates = []
        if explicit_path:
            candidates.append(Path(explicit_path))
        env_path = os.environ.get("FFMPEG_PATH")
        if env_path:
            candidates.append(Path(env_path))
        candidates.extend(
            [
                self.project_root / "runtime" / "ffmpeg" / "bin" / "ffmpeg.exe",
                self.project_root / "runtime" / "ffmpeg" / "ffmpeg.exe",
                self.workspace_root / "runtime" / "ffmpeg" / "bin" / "ffmpeg.exe",
                self.workspace_root / "runtime" / "ffmpeg" / "ffmpeg.exe",
            ]
        )
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.resolve())
        return shutil.which("ffmpeg") or ""

    @staticmethod
    def _collect_files(directory: Path | None, extensions: set[str], recursive: bool = False) -> list[Path]:
        if not directory or not directory.exists():
            return []
        iterator = directory.rglob("*") if recursive else directory.iterdir()
        return sorted(
            path.resolve()
            for path in iterator
            if path.is_file() and path.suffix.lower() in extensions
        )

    @staticmethod
    def _dedupe_paths(paths: list[Path]) -> list[Path]:
        result: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path.resolve()).lower()
            if key not in seen:
                result.append(path.resolve())
                seen.add(key)
        return result

    def _find_audio_file(self, audio_dir: Path | None, manifest: dict[str, Any]) -> Path | None:
        configured = str(manifest.get("audio", {}).get("voiceover_audio_file") or "").strip()
        candidates = []
        if configured:
            candidates.append(Path(configured))
        if audio_dir:
            candidates.extend([audio_dir / "voiceover.wav", audio_dir / "voiceover.mp3"])
            candidates.extend(self._collect_files(audio_dir, AUDIO_EXTENSIONS))
        for candidate in candidates:
            if not candidate.is_absolute():
                candidate = (self.workspace_root / candidate).resolve()
            else:
                candidate = candidate.resolve()
            if candidate.is_file():
                return candidate
        return None

    def _find_bgm_file(self, manifest: dict[str, Any]) -> Path | None:
        configured = str(manifest.get("audio", {}).get("bgm_file") or "").strip()
        if not configured:
            return None
        candidate = Path(configured)
        candidate = candidate.resolve() if candidate.is_absolute() else (self.workspace_root / candidate).resolve()
        return candidate if candidate.is_file() and candidate.suffix.lower() in AUDIO_EXTENSIONS else None

    def _build_video_concat_command(
        self,
        ffmpeg_path: str,
        task_dir: Path,
        video_files: list[Path],
        audio_file: Path | None,
        bgm_file: Path | None,
        subtitles_file: Path | None,
        subtitle_style: str,
        output_width: int,
        output_height: int,
        output_fps: int,
        output_file: Path,
    ) -> tuple[list[str], list[Path]]:
        concat_path = task_dir / "local_ffmpeg_video_inputs.txt"
        concat_path.write_text(
            "".join(f"file '{_ffconcat_path(path)}'\n" for path in video_files),
            encoding="utf-8",
        )
        command = [
            ffmpeg_path,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
        ]
        input_files: list[Path] = [*video_files]
        if audio_file:
            command.extend(["-i", str(audio_file)])
            input_files.append(audio_file)
        if bgm_file:
            command.extend(["-stream_loop", "-1", "-i", str(bgm_file)])
            input_files.append(bgm_file)
        command.extend(self._audio_mix_args(audio_file, bgm_file))
        command.extend(["-vf", self._video_filter(subtitles_file, subtitle_style, output_width, output_height, output_fps), "-c:v", "libx264", "-c:a", "aac", str(output_file)])
        return command, input_files

    def _build_image_slideshow_command(
        self,
        ffmpeg_path: str,
        task_dir: Path,
        image_files: list[Path],
        audio_file: Path | None,
        bgm_file: Path | None,
        subtitles_file: Path | None,
        subtitle_style: str,
        output_width: int,
        output_height: int,
        output_fps: int,
        output_file: Path,
    ) -> tuple[list[str], list[Path]]:
        concat_path = task_dir / "local_ffmpeg_image_inputs.txt"
        duration = 3
        lines: list[str] = []
        for image in image_files:
            lines.append(f"file '{_ffconcat_path(image)}'\n")
            lines.append(f"duration {duration}\n")
        lines.append(f"file '{_ffconcat_path(image_files[-1])}'\n")
        concat_path.write_text("".join(lines), encoding="utf-8")
        command = [
            ffmpeg_path,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
        ]
        input_files: list[Path] = [*image_files]
        if audio_file:
            command.extend(["-i", str(audio_file)])
            input_files.append(audio_file)
        if bgm_file:
            command.extend(["-stream_loop", "-1", "-i", str(bgm_file)])
            input_files.append(bgm_file)
        command.extend(self._audio_mix_args(audio_file, bgm_file))
        command.extend(
            [
                "-vf",
                self._image_filter(subtitles_file, subtitle_style, output_width, output_height, output_fps),
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                str(output_file),
            ]
        )
        return command, input_files

    @staticmethod
    def _build_audio_card_command(
        ffmpeg_path: str,
        audio_file: Path,
        bgm_file: Path | None,
        subtitles_file: Path | None,
        subtitle_style: str,
        output_width: int,
        output_height: int,
        output_fps: int,
        output_file: Path,
    ) -> tuple[list[str], list[Path]]:
        command = [
            ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={output_width}x{output_height}:r={output_fps}",
            "-i",
            str(audio_file),
        ]
        files = [audio_file]
        if bgm_file:
            command.extend(["-stream_loop", "-1", "-i", str(bgm_file)])
            files.append(bgm_file)
        command.extend(LocalFFmpegAdapter._audio_mix_args(audio_file, bgm_file))
        command.extend(
            [
                "-vf",
                LocalFFmpegAdapter._audio_card_filter(subtitles_file, subtitle_style),
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                str(output_file),
            ]
        )
        return command, files

    @staticmethod
    def _audio_mix_args(audio_file: Path | None, bgm_file: Path | None) -> list[str]:
        if audio_file and bgm_file:
            return [
                "-filter_complex",
                "[2:a]volume=0.35[bgm];[bgm][1:a]sidechaincompress=threshold=0.015:ratio=8:attack=20:release=500[ducked];[1:a][ducked]amix=inputs=2:duration=first:normalize=0[aout]",
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
                "-shortest",
            ]
        if audio_file:
            return ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
        if bgm_file:
            return ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
        return []

    @staticmethod
    def _video_filter(
        subtitles_file: Path | None,
        subtitle_style: str,
        output_width: int,
        output_height: int,
        output_fps: int,
    ) -> str:
        filters = [
            f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease",
            f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2",
            f"fps={output_fps}",
        ]
        if subtitles_file:
            filters.append(_subtitle_filter(subtitles_file, subtitle_style))
        filters.append("format=yuv420p")
        return ",".join(filters)

    @staticmethod
    def _image_filter(
        subtitles_file: Path | None,
        subtitle_style: str,
        output_width: int,
        output_height: int,
        output_fps: int,
    ) -> str:
        filters = [
            f"scale={output_width}:{output_height}:force_original_aspect_ratio=decrease",
            f"pad={output_width}:{output_height}:(ow-iw)/2:(oh-ih)/2",
            f"fps={output_fps}",
        ]
        if subtitles_file:
            filters.append(_subtitle_filter(subtitles_file, subtitle_style))
        filters.append("format=yuv420p")
        return ",".join(filters)

    @staticmethod
    def _audio_card_filter(subtitles_file: Path | None, subtitle_style: str) -> str:
        filters = []
        if subtitles_file:
            filters.append(_subtitle_filter(subtitles_file, subtitle_style))
        filters.append("format=yuv420p")
        return ",".join(filters)

    @staticmethod
    def _build_timeline(
        video_files: list[Path],
        image_files: list[Path],
        audio_file: Path | None,
        bgm_file: Path | None,
        subtitles_file: Path | None,
        output_file: Path,
        burn_subtitles: bool,
    ) -> dict[str, Any]:
        clips: list[dict[str, Any]] = []
        for index, path in enumerate(video_files, start=1):
            clips.append({"index": index, "type": "video", "file": str(path), "role": "primary_visual"})
        for index, path in enumerate(image_files, start=len(clips) + 1):
            clips.append({"index": index, "type": "image", "file": str(path), "role": "slideshow_visual", "duration_seconds": 3})
        return {
            "schema_version": 1,
            "output_file": str(output_file),
            "timeline_mode": "video_concat" if video_files else "image_slideshow" if image_files else "audio_card",
            "clips": clips,
            "audio": {"file": str(audio_file) if audio_file else "", "role": "voiceover_or_main_audio"},
            "bgm": {"file": str(bgm_file) if bgm_file else "", "role": "background_music", "ducking": bool(audio_file and bgm_file)},
            "subtitles": {
                "file": str(subtitles_file) if subtitles_file else "",
                "mode": "burned_in" if burn_subtitles and subtitles_file else "sidecar_only",
            },
        }

    @staticmethod
    def _subtitles_are_burnable(subtitles_file: Path, manifest: dict[str, Any]) -> bool:
        audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
        subtitle_status = str(audio.get("subtitle_status") or "").strip().lower()
        if subtitle_status and subtitle_status not in {"ok", "usable", "success"}:
            return False
        try:
            text = subtitles_file.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return False
        normalized = text.strip().lower()
        if not normalized or "-->" not in normalized:
            return False
        placeholder_terms = [
            "待从",
            "整理字幕",
            "语音字幕包装师",
            "placeholder",
            "todo",
            "字幕稿",
            "配音稿",
            "寰呬粠",
            "瀛楀箷",
            "閰嶉煶",
        ]
        if any(term.lower() in normalized for term in placeholder_terms):
            return False
        return True

    @staticmethod
    def _subtitle_skip_reason(subtitles_file: Path, manifest: dict[str, Any]) -> str:
        if not subtitles_file.is_file():
            return "subtitle file is missing"
        audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
        subtitle_status = str(audio.get("subtitle_status") or "").strip()
        subtitle_reason = str(audio.get("subtitle_reason") or "").strip()
        if subtitle_status and subtitle_status.lower() not in {"ok", "usable", "success"}:
            return subtitle_reason or f"subtitle_status is {subtitle_status}"
        if not LocalFFmpegAdapter._subtitles_are_burnable(subtitles_file, manifest):
            return "subtitle file looks empty, invalid, or placeholder-only"
        return ""

    @staticmethod
    def _build_edit_plan(timeline: dict[str, Any], subtitle_style: str) -> str:
        clips = timeline.get("clips") or []
        lines = [
            "# FFmpeg 自动剪辑预览方案",
            "",
            f"- 输出文件：{timeline.get('output_file') or ''}",
            f"- 合成模式：{timeline.get('timeline_mode') or ''}",
            f"- 主音频：{(timeline.get('audio') or {}).get('file') or '未找到'}",
            f"- 背景音乐：{(timeline.get('bgm') or {}).get('file') or '未找到'}",
            f"- 旁白自动压低 BGM：{'是' if (timeline.get('bgm') or {}).get('ducking') else '否'}",
            f"- 字幕：{(timeline.get('subtitles') or {}).get('file') or '未找到'}",
            f"- 字幕模式：{(timeline.get('subtitles') or {}).get('mode') or 'sidecar_only'}",
            f"- 字幕样式：{subtitle_style}",
            "",
            "## 素材顺序",
        ]
        if clips:
            for clip in clips:
                lines.append(f"- {clip.get('index')}. {clip.get('type')}：{clip.get('file')}")
        else:
            lines.append("- 未找到视频或图片素材，使用音频黑底卡片。")
        lines.extend(
            [
                "",
                "## 复核重点",
                "- 检查字幕是否有错字和时间轴偏移。",
                "- 检查人声、BGM、素材画面是否节奏匹配。",
                "- 预览文件可直接发布前复核，也可导入剪辑软件继续精修。",
                "",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _write_json(path: Path, data: dict[str, Any]) -> None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _format_command(command: list[str]) -> str:
        return " ".join(_quote_arg(part) if any(ch.isspace() for ch in str(part)) else str(part) for part in command)


def _ffconcat_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace("'", "'\\''")


def _quote_arg(value: str) -> str:
    return '"' + str(value).replace('"', '\\"') + '"'


def _subtitle_filter(subtitles_file: Path, subtitle_style: str) -> str:
    subtitle_path = str(subtitles_file).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    escaped_style = subtitle_style.replace("\\", "\\\\").replace("'", "\\'")
    return f"subtitles='{subtitle_path}':force_style='{escaped_style}'"


def _bool_or_default(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled", "启用", "是"}


def _int_or_default(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(30, min(parsed, 14400))
