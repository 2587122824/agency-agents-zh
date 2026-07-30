from __future__ import annotations

import os
import math
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
    loop: bool = False
    output_duration_ms: int | None = None
    ducking_regions: tuple[tuple[int, int], ...] = ()
    ducking_reduction_db: float = -12.0
    ducking_attack_ms: int = 200
    ducking_release_ms: int = 500


@dataclass(frozen=True)
class LocalRenderSubtitleInput:
    path: Path
    cues: tuple[tuple[int, int, str], ...] | None = None


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
    loudness_target_lufs: float = -16.0
    true_peak_limit_dbtp: float = -1.0


@dataclass(frozen=True)
class LocalRenderResult:
    command: tuple[str, ...]
    stdout_tail: str
    stderr_tail: str


def _srt_timestamp(time_ms: int) -> str:
    hours, remainder = divmod(time_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def serialize_subtitle_cues(cues: tuple[tuple[int, int, str], ...]) -> str:
    blocks = []
    for sequence, (start_ms, end_ms, text) in enumerate(cues, 1):
        normalized_text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        blocks.append(
            f"{sequence}\n{_srt_timestamp(start_ms)} --> {_srt_timestamp(end_ms)}\n{normalized_text}"
        )
    return "\n\n".join(blocks) + "\n"


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
        temporary_subtitle_path: Path | None = None
        if request.subtitle_input:
            # FFmpeg's subtitles filter on Windows does not reliably consume an
            # absolute drive-letter path even when the colon is escaped. Run the
            # process from the frozen subtitle directory and pass only its name.
            subtitle_path = request.subtitle_input.path
            if request.subtitle_input.cues is not None:
                temporary_subtitle_path = request.output_path.with_name(
                    f".{request.output_path.stem}.timeline-subtitles.srt"
                )
                temporary_subtitle_path.unlink(missing_ok=True)
                temporary_subtitle_path.write_text(
                    serialize_subtitle_cues(request.subtitle_input.cues),
                    encoding="utf-8",
                    newline="\n",
                )
                subtitle_path = temporary_subtitle_path
            execution_cwd = subtitle_path.resolve().parent
            escaped_subtitle_path = subtitle_path.name.replace("'", "\\'")
            filters.append(
                f"[outv]subtitles=filename='{escaped_subtitle_path}':charenc=UTF-8:"
                "force_style='FontName=Microsoft YaHei,Alignment=2,MarginV=48,Outline=2,Shadow=0'[outvs]"
            )
            video_output_label = "outvs"
        if request.audio_inputs:
            audio_labels: list[str] = []
            for audio_index, item in enumerate(request.audio_inputs):
                input_index = len(request.inputs) + audio_index
                label = f"a{audio_index}"
                audio_labels.append(f"[{label}]")
                source_duration_ms = item.source_out_ms - item.source_in_ms
                if item.loop:
                    output_duration_ms = item.output_duration_ms or source_duration_ms
                    repeat_count = math.ceil(output_duration_ms / source_duration_ms)
                    segment_labels: list[str] = []
                    for repeat_index in range(repeat_count):
                        segment_label = f"aseg{audio_index}_{repeat_index}"
                        segment_labels.append(f"[{segment_label}]")
                        filters.append(
                            f"[{input_index}:a:0]"
                            f"atrim=start={item.source_in_ms / 1000:.3f}:end={item.source_out_ms / 1000:.3f},"
                            f"asetpts=PTS-STARTPTS[{segment_label}]"
                        )
                    loop_label = f"aloop{audio_index}"
                    filters.append(
                        f"{''.join(segment_labels)}concat=n={repeat_count}:v=0:a=1,"
                        f"atrim=duration={output_duration_ms / 1000:.3f},asetpts=PTS-STARTPTS[{loop_label}]"
                    )
                    audio_filters = f"[{loop_label}]"
                else:
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
                for start_ms, end_ms in item.ducking_regions:
                    attack_start_ms = max(0, start_ms - item.ducking_attack_ms)
                    release_end_ms = end_ms + item.ducking_release_ms
                    if attack_start_ms < start_ms:
                        audio_filters += (
                            "volume=eval=frame:"
                            f"volume='pow(10,({item.ducking_reduction_db:.3f}"
                            f"*(t-{attack_start_ms / 1000:.3f})/{(start_ms - attack_start_ms) / 1000:.3f})/20)':"
                            f"enable='between(t,{attack_start_ms / 1000:.3f},{start_ms / 1000:.3f})',"
                        )
                    audio_filters += (
                        f"volume={10 ** (item.ducking_reduction_db / 20):.6f}:"
                        f"enable='between(t,{start_ms / 1000:.3f},{end_ms / 1000:.3f})',"
                    )
                    if release_end_ms > end_ms:
                        audio_filters += (
                            "volume=eval=frame:"
                            f"volume='pow(10,({item.ducking_reduction_db:.3f}"
                            f"*(1-(t-{end_ms / 1000:.3f})/{(release_end_ms - end_ms) / 1000:.3f}))/20)':"
                            f"enable='between(t,{end_ms / 1000:.3f},{release_end_ms / 1000:.3f})',"
                        )
                filters.append(f"{audio_filters}adelay={item.timeline_in_ms}:all=1[{label}]")
            limiter_linear = 10 ** (request.true_peak_limit_dbtp / 20)
            filters.append(
                f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:"
                "duration=longest:dropout_transition=0,aresample=async=1:first_pts=0,"
                f"loudnorm=I={request.loudness_target_lufs:.1f}:"
                f"TP={request.true_peak_limit_dbtp:.1f}:LRA=11,"
                f"alimiter=limit={limiter_linear:.6f}:attack=5:release=50[outa]"
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
        finally:
            if temporary_subtitle_path is not None:
                temporary_subtitle_path.unlink(missing_ok=True)
