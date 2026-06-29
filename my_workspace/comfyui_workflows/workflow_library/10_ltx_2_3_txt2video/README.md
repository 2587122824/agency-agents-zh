# LTX 2.3 Txt2Video Canvas

用途：给 B-roll、空镜、转场、气氛镜头生成纯文生视频素材。

这个模板基于现有 LTX 2.3 B-roll 画布拆出，默认关闭参考图条件，避免文生视频槽位误要求 `reference_image`。

## Files

```text
workflow_canvas.json
  可导入 ComfyUI 的 LTX 2.3 文生视频画布。
api_template.json
  API JSON 模板。
runninghub_node_info_list_preset.json
  可复制到调试台的 RunningHub nodeInfoList。
```

## Key Nodes

```text
2483.text      positive prompt
2612.text      negative prompt
4977.value     bypass_i2v_reference, txt2video must be true
3159.strength  image reference strength, txt2video keeps 0.0
4979.value     frame_count
3059.width     output width
3059.height    output height
3059.length    output frame count
4823/4852      SaveVideo filename prefix
```

## Suggested Defaults

```text
width: 1024
height: 576
fps: 24
frame_count: 97
duration: about 4 seconds
```

For short transitions, use `frame_count` 49 or 73. For B-roll, use 97 or 121.

## Prompt Tips

Use this for shots that do not need identity continuity:

```text
cinematic transition shot, fast push in through smoke and sparks, anime style, dynamic camera movement, no characters, clean composition
```

Keep identity-sensitive character shots in first-frame image-to-video workflows instead.
