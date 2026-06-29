# 04_keyframe_image production canvases

## Default safe canvas

Files:

- `workflow_canvas.json`
- `runninghub_node_info_list_preset.json`

This is the default production canvas. It does not require all reference images.

Required inputs:

- `{{prompt}}`
- `{{negative_prompt}}`
- `{{width}}`
- `{{height}}`
- `{{seed}}`

Optional inputs:

- `{{reference_image}}`: main img2img reference. Controlled by `69.switch = {{has_reference_image}}`.
- `{{reference_image_1}}`: Character A FaceID reference. Controlled by `68.switch = {{has_reference_image_1}}`.
- `{{reference_image_2}}`: Character B FaceID reference. Controlled by `67.switch = {{has_reference_image_2}}`.
- `{{reference_image_3}}`: scene/style IPAdapter reference. Controlled by `66.switch = {{has_reference_image_3}}`.

Important mapping rule:

- `reference_image` is only the main image for node 12.
- `reference_images[0]` becomes `{{reference_image_1}}` for node 13.
- `reference_images[1]` becomes `{{reference_image_2}}` for node 14.
- `reference_images[2]` becomes `{{reference_image_3}}` for node 15.

If no reference image is provided, the canvas runs txt2img. If only `reference_image` is provided, the canvas runs img2img and bypasses all FaceID/style branches. Each numbered reference enables only its matching optional branch.

## Multi-reference enhanced canvas

Files:

- `keyframe_multi_reference_canvas.json`
- `keyframe_multi_reference_nodeinfo.json`

Use this only when the job really has all required references for multi-character/style consistency. The default production route should not use this variant blindly.
