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

        ffmpeg_path = self.find_ffmpeg(str(compose_config.get("ffmpeg_path") or ""))
        result_manifest: dict[str, Any] = {
            "status": "running",
            "tool": "ffmpeg",
            "ffmpeg_path": ffmpeg_path or "",
            "output_file": str(output_file),
            "stdout_file": str(stdout_path),
            "stderr_file": str(stderr_path),
            "command_file": str(command_path),
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

        video_files = self._collect_files(paths.get("video_clips"), VIDEO_EXTENSIONS)
        image_files = self._collect_files(paths.get("generated_images"), IMAGE_EXTENSIONS)
        audio_file = self._find_audio_file(paths.get("audio"), manifest)
        subtitles_file = Path(str(manifest.get("files", {}).get("subtitles") or ""))
        if not subtitles_file.is_absolute():
            subtitles_file = (task_dir / subtitles_file).resolve()

        if video_files:
            command, input_files = self._build_video_concat_command(
                ffmpeg_path=ffmpeg_path,
                task_dir=task_dir,
                video_files=video_files,
                audio_file=audio_file,
                output_file=output_file,
            )
        elif image_files:
            command, input_files = self._build_image_slideshow_command(
                ffmpeg_path=ffmpeg_path,
                task_dir=task_dir,
                image_files=image_files,
                audio_file=audio_file,
                output_file=output_file,
            )
        elif audio_file:
            command, input_files = self._build_audio_card_command(
                ffmpeg_path=ffmpeg_path,
                audio_file=audio_file,
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
                }
            )
            self._write_json(manifest_path, result_manifest)
            return result_manifest

        command_path.write_text(self._format_command(command) + "\n", encoding="utf-8")
        result_manifest.update(
            {
                "input_files": [str(path) for path in input_files],
                "audio_file": str(audio_file) if audio_file else "",
                "subtitles_file": str(subtitles_file) if subtitles_file.is_file() else "",
                "subtitle_mode": "sidecar_only",
                "note": "Current local FFmpeg pass creates a draft/final video from available clips/images/audio. Subtitles are kept as sidecar SRT for editing tools.",
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
    def _collect_files(directory: Path | None, extensions: set[str]) -> list[Path]:
        if not directory or not directory.exists():
            return []
        return sorted(
            path.resolve()
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in extensions
        )

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

    def _build_video_concat_command(
        self,
        ffmpeg_path: str,
        task_dir: Path,
        video_files: list[Path],
        audio_file: Path | None,
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
            command.extend(["-i", str(audio_file), "-map", "0:v:0", "-map", "1:a:0", "-shortest"])
            input_files.append(audio_file)
        command.extend(["-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-c:a", "aac", str(output_file)])
        return command, input_files

    def _build_image_slideshow_command(
        self,
        ffmpeg_path: str,
        task_dir: Path,
        image_files: list[Path],
        audio_file: Path | None,
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
            command.extend(["-i", str(audio_file), "-shortest"])
            input_files.append(audio_file)
        command.extend(
            [
                "-vf",
                "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p",
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
        output_file: Path,
    ) -> tuple[list[str], list[Path]]:
        command = [
            ffmpeg_path,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=1080x1920:r=30",
            "-i",
            str(audio_file),
            "-shortest",
            "-vf",
            "format=yuv420p",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(output_file),
        ]
        return command, [audio_file]

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


def _int_or_default(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(30, min(parsed, 14400))
