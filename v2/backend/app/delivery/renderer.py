from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


FFMPEG_PATH_ENV = "V2_FFMPEG_PATH"
RENDERER_CONTRACT = "v2.ffmpeg-render.v1"


class LocalRenderError(RuntimeError):
    def __init__(self, code: str, detail: str, evidence: dict | None = None):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.evidence = evidence or {}


@dataclass(frozen=True)
class FFmpegReadiness:
    available: bool
    reason_code: str | None
    reason: str | None
    executable_path: str | None
    version: str | None


@dataclass(frozen=True)
class LocalRenderInput:
    path: Path
    source_in_ms: int
    source_out_ms: int
    transition_in_ms: int = 0
    transition_out_ms: int = 0


@dataclass(frozen=True)
class LocalRenderAudioInput:
    path: Path
    source_in_ms: int
    source_out_ms: int
    timeline_in_ms: int
    volume_envelope: tuple[tuple[int, float], ...] = ()


@dataclass(frozen=True)
class LocalRenderSubtitleInput:
    path: Path


@dataclass(frozen=True)
class LocalRenderRequest:
    ffmpeg_path: Path
    inputs: tuple[LocalRenderInput, ...]
    output_path: Path
    width: int
    height: int
    fps: int
    video_encoder: str
    preset: str
    crf: int
    audio_inputs: tuple[LocalRenderAudioInput, ...] = ()
    subtitle_input: LocalRenderSubtitleInput | None = None


@dataclass(frozen=True)
class LocalRenderResult:
    command: tuple[str, ...]
    stdout_tail: str
    stderr_tail: str


def inspect_local_ffmpeg() -> FFmpegReadiness:
    configured = os.environ.get(FFMPEG_PATH_ENV, "").strip()
    return _inspect_local_ffmpeg(configured)


@lru_cache(maxsize=4)
def _inspect_local_ffmpeg(configured: str) -> FFmpegReadiness:
    if not configured:
        return FFmpegReadiness(
            False,
            "FFMPEG_PATH_NOT_CONFIGURED",
            f"未配置环境变量 {FFMPEG_PATH_ENV}。",
            None,
            None,
        )
    path = Path(configured).expanduser()
    if not path.is_file():
        return FFmpegReadiness(
            False,
            "FFMPEG_EXECUTABLE_NOT_FOUND",
            "配置的 FFmpeg 可执行文件不存在。",
            str(path),
            None,
        )
    try:
        version_result = subprocess.run(
            [str(path), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return FFmpegReadiness(
            False,
            "FFMPEG_VERSION_CHECK_FAILED",
            f"无法读取 FFmpeg 版本：{exc}",
            str(path),
            None,
        )
    version = (version_result.stdout or version_result.stderr).splitlines()
    version_line = version[0].strip() if version else None
    if version_result.returncode != 0 or not version_line:
        return FFmpegReadiness(
            False,
            "FFMPEG_VERSION_CHECK_FAILED",
            "FFmpeg 版本检查失败。",
            str(path),
            version_line,
        )
    try:
        encoders_result = subprocess.run(
            [str(path), "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return FFmpegReadiness(
            False,
            "FFMPEG_ENCODER_CHECK_FAILED",
            f"无法读取 FFmpeg 编码器：{exc}",
            str(path),
            version_line,
        )
    if encoders_result.returncode != 0 or "libx264" not in encoders_result.stdout:
        return FFmpegReadiness(
            False,
            "FFMPEG_LIBX264_UNAVAILABLE",
            "当前 FFmpeg 不支持 libx264 编码器。",
            str(path),
            version_line,
        )
    return FFmpegReadiness(True, None, None, str(path.resolve()), version_line)


class LocalFFmpegRenderer:
    def render(self, request: LocalRenderRequest) -> LocalRenderResult:
        request.output_path.parent.mkdir(parents=True, exist_ok=True)
        if request.output_path.exists():
            raise LocalRenderError(
                "DELIVERY_OUTPUT_PATH_EXISTS",
                "目标交付文件已存在，不能覆盖。",
                {"path": str(request.output_path)},
            )
        command = [str(request.ffmpeg_path), "-hide_banner", "-nostdin", "-n"]
        for item in request.inputs:
            command.extend(["-i", str(item.path)])
        for item in request.audio_inputs:
            command.extend(["-i", str(item.path)])
        filters: list[str] = []
        labels: list[str] = []
        for index, item in enumerate(request.inputs):
            label = f"v{index}"
            labels.append(f"[{label}]")
            duration_seconds = (item.source_out_ms - item.source_in_ms) / 1000
            video_filters = (
                f"[{index}:v:0]"
                f"trim=start={item.source_in_ms / 1000:.3f}:end={item.source_out_ms / 1000:.3f},"
                "setpts=PTS-STARTPTS,"
                f"scale={request.width}:{request.height}:force_original_aspect_ratio=increase,"
                f"crop={request.width}:{request.height},"
                f"setsar=1,fps={request.fps},"
                f"tpad=stop_mode=clone:stop_duration={1 / request.fps:.6f},"
                f"trim=duration={duration_seconds:.3f},setpts=PTS-STARTPTS,"
            )
            if item.transition_in_ms:
                video_filters += f"fade=t=in:st=0:d={item.transition_in_ms / 1000:.3f},"
            if item.transition_out_ms:
                video_filters += (
                    f"fade=t=out:st={max(0, item.source_out_ms - item.source_in_ms - item.transition_out_ms) / 1000:.3f}:"
                    f"d={item.transition_out_ms / 1000:.3f},"
                )
            filters.append(f"{video_filters}format=yuv420p[{label}]")
        filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[outv]")
        video_output_label = "outv"
        execution_cwd: Path | None = None
        if request.subtitle_input:
            # FFmpeg's subtitles filter on Windows does not reliably consume an
            # absolute drive-letter path even when the colon is escaped. Run the
            # process from the frozen subtitle directory and pass only its name.
            execution_cwd = request.subtitle_input.path.resolve().parent
            escaped_subtitle_path = request.subtitle_input.path.name.replace("'", "\\'")
            filters.append(
                f"[outv]subtitles=filename='{escaped_subtitle_path}':"
                "force_style='Alignment=2,MarginV=48,Outline=2,Shadow=0'[outvs]"
            )
            video_output_label = "outvs"
        if request.audio_inputs:
            audio_labels: list[str] = []
            for audio_index, item in enumerate(request.audio_inputs):
                input_index = len(request.inputs) + audio_index
                label = f"a{audio_index}"
                audio_labels.append(f"[{label}]")
                audio_filters = (
                    f"[{input_index}:a:0]"
                    f"atrim=start={item.source_in_ms / 1000:.3f}:end={item.source_out_ms / 1000:.3f},"
                    "asetpts=PTS-STARTPTS,"
                )
                for (start_ms, start_gain), (end_ms, end_gain) in zip(
                    item.volume_envelope,
                    item.volume_envelope[1:],
                ):
                    start_seconds = start_ms / 1000
                    end_seconds = end_ms / 1000
                    audio_filters += (
                        "volume=eval=frame:"
                        f"volume='pow(10,({start_gain:.3f}+({end_gain - start_gain:.3f})"
                        f"*(t-{start_seconds:.3f})/{end_seconds - start_seconds:.3f})/20)':"
                        f"enable='between(t,{start_seconds:.3f},{end_seconds:.3f})',"
                    )
                filters.append(f"{audio_filters}adelay={item.timeline_in_ms}:all=1[{label}]")
            filters.append(
                f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:"
                "duration=longest:dropout_transition=0,aresample=async=1:first_pts=0[outa]"
            )
        command.extend([
            "-filter_complex",
            ";".join(filters),
            "-map",
            f"[{video_output_label}]",
        ])
        if request.audio_inputs:
            command.extend(["-map", "[outa]", "-c:a", "aac", "-b:a", "192k"])
        else:
            command.append("-an")
        command.extend([
            "-c:v",
            request.video_encoder,
            "-preset",
            request.preset,
            "-crf",
            str(request.crf),
            "-movflags",
            "+faststart",
            str(request.output_path),
        ])
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                cwd=execution_cwd,
            )
        except OSError as exc:
            raise LocalRenderError(
                "FFMPEG_PROCESS_START_FAILED",
                f"无法启动 FFmpeg：{exc}",
            ) from exc
        if result.returncode != 0:
            request.output_path.unlink(missing_ok=True)
            raise LocalRenderError(
                "FFMPEG_RENDER_FAILED",
                "FFmpeg 本地合成失败。",
                {
                    "return_code": result.returncode,
                    "stdout_tail": result.stdout[-4000:],
                    "stderr_tail": result.stderr[-8000:],
                },
            )
        if not request.output_path.is_file():
            raise LocalRenderError(
                "FFMPEG_OUTPUT_MISSING",
                "FFmpeg 返回成功，但没有生成目标文件。",
            )
        return LocalRenderResult(
            tuple(command),
            result.stdout[-4000:],
            result.stderr[-8000:],
        )
