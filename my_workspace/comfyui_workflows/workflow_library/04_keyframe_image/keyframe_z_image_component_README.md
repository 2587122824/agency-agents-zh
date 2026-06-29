# 04_keyframe_image - Z-Image component keyframe canvas

This default keyframe canvas was rebuilt from the verified `01_all_in_one_image` Z-Image component canvas in this project.

The previous hand-written `UNETLoader -> VAEEncode -> KSampler` keyframe canvas produced noisy or ineffective img2img behavior because that chain was not proven compatible with the local Z-Image component setup.

## Production principle

Keyframes should be text-to-image first. A reference image is an optional visual condition, not the main latent branch by default.

## Files

- `workflow_canvas.json`: canonical default canvas.
- `keyframe_z_image_component_canvas.json`: named copy of the canonical default canvas.
- `runninghub_node_info_list_preset.json`: minimal nodeInfo mapping.
- `keyframe_experimental_handwired_canvas.json`: archived experimental multi-reference hand-wired canvas; do not use it as the default production keyframe workflow.

## Canvas structure

- Node 57: verified Z-Image component.
- Node 12: optional reference image.
- Node 9: SaveImage output.

## nodeInfo mapping

- `57.text -> {{prompt}}`
- `12.image -> {{reference_image}}`
- `9.filename_prefix -> keyframe_z_image`

## Current limitation

This default canvas intentionally does not claim multi-character FaceID or style-reference control. If you need those, export a real working ComfyUI workflow with those nodes from your local ComfyUI and calibrate nodeInfo against that exported workflow.
