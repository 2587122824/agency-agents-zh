# 06_i2v_first_middle_last_frame_ltx_2_3

Independent experimental LTX 2.3 three-reference image-to-video workflow.

This workflow is intentionally separate from `06_i2v_first_last_frame_ltx_2_3`.
It does not replace 06B first/last-frame generation.

## Positioning

- Workflow id: `06_i2v_first_middle_last_frame`
- Workflow mode: `i2v_first_middle_last_frame`
- Inputs: `{{reference_image}}`, `{{middle_frame_image}}`, `{{last_frame_image}}`
- Output: one silent guided video clip
- Baseline: 4 seconds, LTX guide workflow
- Status: experimental, use only when the same shot can plausibly pass through all three frames

## Duration and frame-rate mapping

- `{{duration}}` is sent to `436.value` as clip seconds.
- `{{fps}}` is sent to `412.value`, `373.frame_rate`, and `413.frame_rate`.
- `{{frame_count}}` is calculated from duration x FPS and sent to `424.length` and `373.frames_number`.
- Do not send `{{frame_count}}` to `426.frame_count`. Node 426 is `Allab_QwenVL_Advanced`; its sampling control is capped at 64 and is not the generated clip length.
- Runtime configuration repair keeps these six rows parameterized when a full RunningHub node list is imported; it does not change any other workflow slot.

## Current Wiring

```text
LoadImage start_frame (node 2)
  -> LTXVImgToVideoAdvanced (node 15, index 0)

LoadImage middle_frame (node 29)
  -> LTXVAddGuideAdvanced (node 17, frame_idx=40)

LoadImage end_frame (node 3)
  -> LTXVAddGuideAdvancedAttention (node 16, late guide)

Prompt / Negative
  -> LTXVConditioning
  -> CFGGuider + LTXVScheduler + SamplerCustomAdvanced
  -> LTXVSeparateAVLatent
  -> LTXVTiledVAEDecode
  -> CreateVideo
  -> SaveVideo (node 28)
```

## RunningHub Placeholders

```text
{{reference_image}}
{{middle_frame_image}}
{{last_frame_image}}
{{prompt}}
{{negative_prompt}}
{{width}}
{{height}}
{{fps}}
{{duration}}
{{frame_count}}
{{ltx_guide_frame_count}}
{{seed}}
```

## Use Rules

- Use this only for one continuous shot with the same subject, outfit, scene, lighting, camera angle, and framing.
- The three frames should represent approximately start / middle / end moments of one 4-second shot.
- If the middle or end frame changes identity, costume, setting, composition, or action too much, downgrade to `06_i2v_first_frame` or split into multiple clips.
- Keep 06B `i2v_first_last_frame` as the stable first/last-frame option.
