# 04_keyframe_image - ???????????? / FaceID ????

????????? `04_keyframe` / `workflow_mode=keyframe`?

## ??

- `workflow_canvas.json`?????? ComfyUI ??????
- `api_template.json`??? API ???
- `runninghub_node_info_list_preset.json`?RunningHub nodeInfoList ?????????????

## ??????

?????????????

- `{{prompt}}`
- `{{negative_prompt}}`
- `{{reference_image}}`??????????????????
- `{{reference_image_1}}`???A / ??????
- `{{reference_image_2}}`???B / ????
- `{{reference_image_3}}`????????
- `{{reference_image_4}}`???????????
- `{{has_reference_image_1}}` ? `{{has_reference_image_4}}`
- `{{width}}`
- `{{height}}`
- `{{seed}}`

????

- `{{duration}}`
- `{{fps}}`

## ?????

???????????????????? prompt??? 06 ???

```json
{
  "reference_images": [
    {"role": "character_a", "image": "ref_character_a_base", "control": "face_identity"},
    {"role": "character_b", "image": "ref_character_b_base", "control": "face_identity"},
    {"role": "scene", "image": "ref_scene_office", "control": "style_scene"}
  ]
}
```

????????? `{{reference_image_1}}`?`{{reference_image_2}}`?`{{reference_image_3}}`?

## FaceID / IPAdapter ??

???????????????????? FaceID ?????????????????

- LoadImage A -> FaceID / InstantID / IPAdapter FaceID??? `{{reference_image_1}}`
- LoadImage B -> FaceID / InstantID / IPAdapter FaceID??? `{{reference_image_2}}`
- LoadImage Scene/Style -> IPAdapter Style / Reference??? `{{reference_image_3}}`

?????? `ComfyUI?? -> 04 ?????` ?? API JSON??? nodeInfoList?

## ??

`runninghub_node_info_list_preset.json` ?? `FACEID_A_LOAD_IMAGE_NODE` ?????????????ID??????????? API JSON ?????????????????ID?

## Consistent character keyframe variants

The user-provided `角色一致性 & CONSISTENT CHARACTERS from an INPUT IMAGE` canvas has been adapted into two system modes:

- `consistent_character_identity_keyframe_canvas.json` + `consistent_character_identity_keyframe_nodeinfo.json`
  - for `04_keyframe / identity_keyframe`
  - requires `{{input_identity_image}}`
- `consistent_character_pose_identity_keyframe_canvas.json` + `consistent_character_pose_identity_keyframe_nodeinfo.json`
  - for `04_keyframe / pose_identity_keyframe`
  - requires `{{input_identity_image}}` and `{{input_pose_image}}`

After exposing each canvas as a RunningHub workflow, paste the returned endpoint into the matching debug-console mode. The mode-specific default nodeInfoList is loaded automatically by the system and can be adjusted after importing the actual RunningHub API JSON.
## Clean consistent character canvases

The consistent-character canvases are intentionally reduced to the main identity keyframe chain only. Background generation, emotion batches, pose-saving batches, extra SaveImage/PreviewImage outputs, and older experimental JSON templates were removed to avoid unrelated node validation errors.

Current files:

- `consistent_character_identity_keyframe_canvas.json`
- `consistent_character_identity_keyframe_nodeinfo.json`
- `consistent_character_pose_identity_keyframe_canvas.json`
- `consistent_character_pose_identity_keyframe_nodeinfo.json`
- `style_reference_keyframe_canvas.json`
- `style_reference_keyframe_nodeinfo.json`
- `img2img_style_keyframe_canvas.json`
- `img2img_style_keyframe_nodeinfo.json`

Canvas files keep literal default widget values so ComfyUI can open and validate them locally. Runtime placeholders such as `{{input_identity_image}}`, `{{input_pose_image}}`, `{{prompt}}`, `{{width}}`, `{{height}}`, and `{{seed}}` live only in the paired RunningHub nodeInfoList files.

## Style-reference keyframe variant

`style_reference_keyframe_canvas.json` is exposed as `04_keyframe / style_reference_keyframe`.
It is an SDXL IPAdapter Style Transfer image workflow for generating a current-shot keyframe from a style/reference image plus the shot prompt. It requires `{{input_reference_style}}` and maps `{{ipadapter_weight}}`, `{{prompt}}`, `{{negative_prompt}}`, `{{width}}`, `{{height}}`, and `{{seed}}` through `style_reference_keyframe_nodeinfo.json`.

This mode is kept as an explicit debug-console submode while the production flow is still being stabilized. A later architecture pass can collapse it into a generic `04_keyframe/keyframe + controls.style_reference` preset without changing the canvas itself.

## Img2img style keyframe variant

`img2img_style_keyframe_canvas.json` is exposed as `04_keyframe / img2img_style_keyframe`.
The active `img2img_style_keyframe_nodeinfo.json` is calibrated for the user-provided Qwen Image Edit 2511 img2img workflow `图生图风格关键帧.json`: `LoadImage(2) -> PrimitiveStringMultiline(34) -> shortest-side Int(8) -> TextEncodeQwenImageEditPlus(3) -> KSampler(24) -> SaveImage(48)`. It requires `{{input_base_image}}` and maps `{{prompt}}`, `{{negative_prompt}}`, `{{short_side}}`, `{{seed}}`, and `{{denoise}}`.

Use this mode when the reference image should remain recognizable as the basis of the keyframe. The `{{denoise}}` value is supplied by staff/production payloads; when no value is provided, the current default is `1` so the Qwen Image Edit workflow can apply the prompt strongly enough.
