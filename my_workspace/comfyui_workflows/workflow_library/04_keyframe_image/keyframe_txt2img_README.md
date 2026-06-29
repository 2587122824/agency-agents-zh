# 04_keyframe_image - txt2img keyframe workflow

Current production decision: keyframe generation is plain text-to-image first. No reference image is required or passed by default.

## Canonical files

- `workflow_canvas.json`
- `runninghub_node_info_list_preset.json`

## nodeInfo mapping

- `63.text -> {{prompt}}`
- `64.width -> {{width}}`
- `64.height -> {{height}}`
- `66.seed -> {{seed}}`
- `9.filename_prefix -> keyframe_txt2img`

## Not included for now

- reference_image
- reference_images
- img2img
- FaceID
- IPAdapter
- multi-character reference control

Add those later only after the txt2img keyframe path is stable.
