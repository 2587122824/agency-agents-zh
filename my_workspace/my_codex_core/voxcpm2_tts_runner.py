from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import soundfile as sf
from voxcpm import VoxCPM


VOICE_PRESET_CONTROLS = {
    "warm_female": "warm, natural, friendly female Mandarin voice, suitable for social media narration",
    "clear_female": "clear, bright, precise female Mandarin voice, suitable for tutorials and explainers",
    "pro_male": "professional, steady, trustworthy male Mandarin voice, suitable for business presentation",
    "deep_male": "deep, calm, textured male Mandarin voice, suitable for documentary narration",
    "young_male": "young, energetic, clean male Mandarin voice, suitable for product demos",
    "story_female": "soft, expressive, storytelling female Mandarin voice, suitable for long-form narration",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Project wrapper for local VoxCPM2 TTS.")
    parser.add_argument("--text-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--reference-audio", default="")
    parser.add_argument("--reference-text", default="")
    parser.add_argument("--voice-preset", default="warm_female")
    parser.add_argument("--control", default="")
    parser.add_argument("--hf-model-id", default="openbmb/VoxCPM2")
    parser.add_argument("--cache-dir", default="")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--cfg-value", type=float, default=2.0)
    parser.add_argument("--inference-timesteps", type=int, default=10)
    parser.add_argument("--chunk-chars", type=int, default=160)
    parser.add_argument("--silence-ms", type=int, default=180)
    parser.add_argument("--normalize", action="store_true")
    parser.add_argument("--denoise", action="store_true")
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--no-denoiser", action="store_true")
    parser.add_argument("--no-optimize", action="store_true")
    args = parser.parse_args()

    text = Path(args.text_file).read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit("text file is empty")

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cache_dir = str(Path(args.cache_dir).resolve()) if args.cache_dir else None
    device = None if args.device == "auto" else args.device
    control = args.control.strip() or VOICE_PRESET_CONTROLS.get(args.voice_preset, "")
    reference_audio = args.reference_audio.strip()
    reference_text = args.reference_text.strip() or None

    chunks = split_text(text, max_chars=max(40, min(args.chunk_chars, 4096)))
    model = VoxCPM.from_pretrained(
        hf_model_id=args.hf_model_id,
        load_denoiser=not args.no_denoiser,
        cache_dir=cache_dir,
        local_files_only=args.local_files_only,
        optimize=not args.no_optimize,
        device=device,
    )
    sample_rate = int(model.tts_model.sample_rate)
    silence = np.zeros(max(0, int(sample_rate * args.silence_ms / 1000)), dtype=np.float32)
    audio_parts = []
    chunk_results = []

    for index, chunk in enumerate(chunks, start=1):
        final_text = f"({control}){chunk}" if control else chunk
        audio = model.generate(
            text=final_text,
            reference_wav_path=reference_audio or None,
            prompt_text=reference_text,
            cfg_value=args.cfg_value,
            inference_timesteps=args.inference_timesteps,
            normalize=args.normalize,
            denoise=args.denoise and bool(reference_audio),
        )
        audio = np.asarray(audio, dtype=np.float32)
        audio_parts.append(audio)
        if index < len(chunks) and len(silence):
            audio_parts.append(silence)
        chunk_results.append({"index": index, "chars": len(chunk), "samples": int(audio.shape[0])})

    merged = np.concatenate(audio_parts) if audio_parts else np.zeros(1, dtype=np.float32)
    sf.write(str(output), merged, sample_rate)
    manifest = {
        "status": "success",
        "output": str(output),
        "sample_rate": sample_rate,
        "duration_seconds": round(float(len(merged)) / sample_rate, 3),
        "chunks": chunk_results,
        "voice_preset": args.voice_preset,
        "reference_audio_provided": bool(reference_audio),
        "cache_dir": cache_dir,
        "hf_model_id": args.hf_model_id,
    }
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


def split_text(text: str, max_chars: int) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return [text]
    parts = [part.strip() for part in re.split(r"([。！？!?；;，,])", text) if part.strip()]
    sentences: list[str] = []
    current = ""
    for part in parts:
        current += part
        if re.fullmatch(r"[。！？!?；;，,]", part):
            sentences.append(current.strip())
            current = ""
    if current.strip():
        sentences.append(current.strip())

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars))
            continue
        candidate = f"{current}{sentence}" if current else sentence
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
