# LTX 2.3 first-frame image-to-video

This library entry is the calibrated source for `06_i2v_first_frame / i2v_first_frame`.

## RunningHub publication

- Active workflow ID: `2069607607387639810`
- Canvas source: `workflow_canvas.json`
- API source: `api_template.json`
- Runtime mapping: `runninghub_node_info_list_preset.json`
- Verified task: `2076709366089867265`

The previous publication `2071735603636563970` used a GGUF diffusion model whose
connector was constructed at width 3840 while a normalization weight was loaded at
width 4096. Every request failed in `LTX2_NAG(238)` before sampling.

The active publication loads the LTX 2.3 FP8 checkpoint directly and completed a
real 4-second, 24fps first-frame I2V request on 2026-07-14. It is now the explicit
primary workflow, not an automatic fallback. Production must fail visibly if this
configured workflow fails.

## Runtime inputs

- prompt: `2483.text`
- negative prompt: `2612.text`
- first frame: `2004.image`
- longest edge: `4981.resize_type.longer_size`
- frame count: `4979.value`
- FPS: `4978.value`
- seed: `4814.noise_seed`
- first-frame conditioning bypass: `4977.value=false`
- output prefix: `4823.filename_prefix`

Prompt fields use the employee/production text directly. Do not add identity prose,
negative terms, missing-reference synthesis, B-roll downgrade, or alternate workflow
selection in the adapter.
