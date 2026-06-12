from __future__ import annotations

import base64
import json
import mimetypes
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from my_codex_core.workflow_engine import WorkflowEngine


WORKSPACE_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = WORKSPACE_ROOT / "my_task_output"
WORKFLOW_ROOT = WORKSPACE_ROOT / "my_workflows"
STAFF_ROOT = WORKSPACE_ROOT / "my_custom_staff"
MEMORY_ROOT = WORKSPACE_ROOT / "my_memory"
REFERENCE_ROOT = WORKSPACE_ROOT / "my_reference_images"
RUN_JOBS: dict[str, dict] = {}
RUN_JOBS_LOCK = threading.RLock()


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>自媒体工作流管理台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --line: #d8dde6;
      --text: #1b1f24;
      --muted: #667085;
      --accent: #0f766e;
      --accent-strong: #0b5f59;
      --danger: #b42318;
      --shadow: 0 1px 3px rgba(16, 24, 40, .08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      letter-spacing: 0;
    }
    header {
      height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 20px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
    }
    main {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      min-height: calc(100vh - 56px);
    }
    aside {
      border-right: 1px solid var(--line);
      background: #fbfcfd;
      padding: 16px;
      overflow: auto;
    }
    section {
      padding: 18px;
      overflow: auto;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    .stack { display: grid; gap: 14px; }
    .row {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
    label {
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 600;
    }
    select, textarea, input, button {
      font: inherit;
      border-radius: 6px;
    }
    select, textarea, input {
      width: 100%;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      padding: 9px 10px;
      outline: none;
    }
    textarea {
      min-height: 180px;
      resize: vertical;
      line-height: 1.55;
    }
    button {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      padding: 9px 12px;
      cursor: pointer;
      min-height: 38px;
    }
    button.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
      font-weight: 650;
    }
    button.primary:hover { background: var(--accent-strong); }
    button:disabled { opacity: .55; cursor: not-allowed; }
    .form {
      padding: 16px;
      display: grid;
      gap: 14px;
    }
    .split {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) minmax(220px, 1fr) 140px minmax(220px, 1fr);
      gap: 12px;
    }
    details {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfd;
      padding: 0;
    }
    summary {
      cursor: pointer;
      padding: 10px 12px;
      list-style-position: inside;
    }
    details[open] summary {
      border-bottom: 1px solid var(--line);
    }
    .details-body {
      padding: 12px;
      display: grid;
      gap: 12px;
    }
    .provider-grid {
      display: grid;
      grid-template-columns: minmax(220px, 1fr) minmax(260px, 1fr) minmax(260px, 1fr);
      gap: 12px;
    }
    .video-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(160px, 1fr));
      gap: 12px;
    }
    .reference-list {
      display: grid;
      gap: 8px;
      margin-top: 10px;
    }
    .reference-item {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px;
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
      background: #fff;
      font-size: 13px;
    }
    .reference-preview {
      width: 64px;
      height: 64px;
      min-width: 64px;
      border-radius: 6px;
      border: 1px solid var(--line);
      object-fit: cover;
      background: #f2f4f7;
    }
    .reference-info {
      display: grid;
      gap: 4px;
      min-width: 0;
      flex: 1;
    }
    .reference-name {
      font-weight: 650;
      overflow-wrap: anywhere;
    }
    .list {
      display: grid;
      gap: 8px;
      margin-top: 12px;
    }
    .item {
      text-align: left;
      padding: 10px;
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      width: 100%;
    }
    .item.active { border-color: var(--accent); box-shadow: inset 3px 0 0 var(--accent); }
    .item-main {
      display: grid;
      gap: 4px;
      min-width: 0;
      flex: 1;
    }
    .item-title { font-weight: 650; overflow-wrap: anywhere; }
    .item-meta { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    .icon-btn {
      width: 32px;
      min-width: 32px;
      height: 32px;
      min-height: 32px;
      padding: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 18px;
      line-height: 1;
    }
    .icon-btn.danger {
      color: var(--danger);
      border-color: #fecdca;
      background: #fffafa;
    }
    .icon-btn.danger:hover {
      background: #fef3f2;
      border-color: var(--danger);
    }
    .status {
      padding: 8px 10px;
      border-radius: 6px;
      background: #eef6f5;
      color: #134e4a;
      font-size: 13px;
    }
    .status.error { background: #fef3f2; color: var(--danger); }
    .progress-box {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fbfcfd;
      padding: 10px;
      display: grid;
      gap: 8px;
    }
    .progress-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      font-size: 13px;
      color: var(--muted);
    }
    .progress-bar {
      height: 8px;
      border-radius: 999px;
      background: #e4e7ec;
      overflow: hidden;
    }
    .progress-fill {
      height: 100%;
      width: 0%;
      background: var(--accent);
      transition: width .2s ease;
    }
    .progress-list {
      display: grid;
      gap: 6px;
      margin-top: 2px;
    }
    .progress-step {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      background: #fff;
      font-size: 13px;
    }
    .progress-step.active { border-color: var(--accent); box-shadow: inset 3px 0 0 var(--accent); }
    .progress-step.done { color: #166534; background: #f0fdf4; border-color: #bbf7d0; }
    .progress-step.error { color: var(--danger); background: #fef3f2; border-color: #fecdca; }
    .viewer {
      display: grid;
      grid-template-rows: auto minmax(320px, 1fr);
      min-height: 520px;
    }
    .viewer-head {
      padding: 12px;
      border-bottom: 1px solid var(--line);
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
    }
    .file-tabs {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .file-tabs button {
      min-height: 32px;
      padding: 6px 9px;
      font-size: 13px;
    }
    .file-tabs button.active {
      border-color: var(--accent);
      color: var(--accent);
      font-weight: 650;
    }
    pre {
      margin: 0;
      padding: 16px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.55;
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 13px;
      background: #fff;
    }
    .muted { color: var(--muted); }
    .small { font-size: 12px; }
    @media (max-width: 860px) {
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .split { grid-template-columns: 1fr; }
      .provider-grid { grid-template-columns: 1fr; }
      .video-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>自媒体工作流管理台</h1>
    <div class="muted small" id="env">加载中</div>
  </header>
  <main>
    <aside>
      <div class="row">
        <strong>任务输出</strong>
        <button id="refreshTasks" title="刷新任务列表">刷新</button>
      </div>
      <div class="list" id="taskList"></div>
    </aside>
    <section class="stack">
      <div class="panel form">
        <div class="split">
          <label>工作流
            <select id="workflow"></select>
          </label>
          <label>任务名称
            <input id="taskTitle" autocomplete="off" spellcheck="false" placeholder="例如 AI自动化获客短视频-第1版，可留空" />
          </label>
          <label>执行模式
            <select id="provider">
              <option value="auto">auto</option>
              <option value="offline">offline</option>
              <option value="openai">openai</option>
            </select>
          </label>
          <label>模型
            <select id="model">
              <optgroup label="推荐主力模型">
                <option value="gpt-5.5" selected>GPT-5.5 - 复杂任务/最高质量</option>
                <option value="gpt-5.4">GPT-5.4 - 通用高质量</option>
              </optgroup>
              <optgroup label="轻量与低成本">
                <option value="gpt-5.4-mini">GPT-5.4 mini - 速度/成本平衡</option>
                <option value="gpt-5.4-nano">GPT-5.4 nano - 最低延迟/批量任务</option>
              </optgroup>
              <optgroup label="推理模型">
                <option value="o3">o3 - 深度推理</option>
                <option value="o4-mini">o4-mini - 快速推理</option>
              </optgroup>
              <optgroup label="兼容旧模型">
                <option value="gpt-4.1">GPT-4.1 - 旧版通用</option>
                <option value="gpt-4.1-mini">GPT-4.1 mini - 旧版低成本</option>
                <option value="gpt-4o">GPT-4o - 旧版多模态</option>
                <option value="gpt-4o-mini">GPT-4o mini - 旧版轻量</option>
              </optgroup>
              <optgroup label="自定义">
                <option value="custom">手动输入模型名</option>
              </optgroup>
            </select>
          </label>
        </div>
        <label>原始需求
          <textarea id="userInput" placeholder="例如：我要做一条抖音短视频，推广 AI 自动化开发服务，目标客户是中小企业老板，目标是让客户私信咨询。"></textarea>
        </label>
        <details>
          <summary><strong>模型接口配置</strong> <span class="muted small">API Key、中转站和自定义模型名</span></summary>
          <div class="details-body">
            <div class="provider-grid">
              <label>API Key
                <input id="apiKey" type="password" autocomplete="off" spellcheck="false" placeholder="sk-...，只用于本次运行，不保存" />
              </label>
              <label>中转站 Base URL
                <input id="baseUrl" autocomplete="off" spellcheck="false" placeholder="例如 https://api.example.com/v1，留空用官方地址" />
              </label>
              <label>自定义模型名
                <input id="customModel" placeholder="选择“手动输入模型名”时填写" disabled />
              </label>
            </div>
          </div>
        </details>
        <details>
          <summary><strong>记忆与继承</strong> <span class="muted small">长期记忆和历史任务上下文</span></summary>
          <div class="details-body">
            <div class="provider-grid">
              <label>长期记忆
                <select id="useMemory">
                  <option value="on" selected>启用 my_memory</option>
                  <option value="off">不启用</option>
                </select>
              </label>
              <label>继承历史任务
                <select id="inheritTask">
                  <option value="">不继承</option>
                </select>
              </label>
              <label>继承范围
                <select id="inheritMode">
                  <option value="final_output" selected>只参考上次最终成品</option>
                  <option value="input_and_final">参考上次需求和最终成品</option>
                </select>
              </label>
            </div>
          </div>
        </details>
        <details>
          <summary><strong>生图配置</strong> <span class="muted small">用于 06_分镜生图设计师生成关键帧方案</span></summary>
          <div class="details-body">
            <div class="video-grid">
              <label>生图工具
                <select id="imageTool">
                  <option value="prompt_only" selected>仅生成生图提示词</option>
                  <option value="gpt-image">GPT Image</option>
                  <option value="midjourney">Midjourney</option>
                  <option value="stable-diffusion">Stable Diffusion</option>
                  <option value="flux">FLUX</option>
                  <option value="jimeng">即梦生图</option>
                  <option value="kling">可灵生图</option>
                  <option value="seedream">Seedream</option>
                  <option value="custom">其他/自定义</option>
                </select>
              </label>
              <label>生图模型
                <input id="imageModel" list="imageModelOptions" placeholder="例如 gpt-image-1 / midjourney v7，可留空" />
                <datalist id="imageModelOptions">
                  <option value="gpt-image-1" label="GPT Image 1"></option>
                  <option value="dall-e-3" label="DALL-E 3"></option>
                  <option value="midjourney-v7" label="Midjourney v7"></option>
                  <option value="stable-diffusion-xl" label="Stable Diffusion XL"></option>
                  <option value="flux-1.1-pro" label="FLUX 1.1 Pro"></option>
                  <option value="seedream-3.0" label="Seedream 3.0"></option>
                  <option value="jimeng-image" label="即梦生图"></option>
                  <option value="kling-image" label="可灵生图"></option>
                </datalist>
              </label>
              <label>图片尺寸
                <select id="imageSize">
                  <option value="9:16" selected>9:16 竖屏关键帧</option>
                  <option value="16:9">16:9 横屏关键帧</option>
                  <option value="1:1">1:1 方图</option>
                  <option value="4:5">4:5 信息流</option>
                  <option value="1024x1792">1024x1792</option>
                  <option value="1792x1024">1792x1024</option>
                  <option value="1024x1024">1024x1024</option>
                </select>
              </label>
              <label>每镜头图片数
                <select id="imageCount">
                  <option value="1" selected>1 张</option>
                  <option value="2">2 张备选</option>
                  <option value="3">3 张备选</option>
                  <option value="4">4 张备选</option>
                </select>
              </label>
            </div>
            <div class="provider-grid">
              <label>生图风格
                <input id="imageStyle" placeholder="例如 写实商业、电影感、干净明亮、赛博科技、国风插画" />
              </label>
              <label>生图质量
                <select id="imageQuality">
                  <option value="standard" selected>标准</option>
                  <option value="high">高清/高质量</option>
                  <option value="draft">草图/快速预览</option>
                </select>
              </label>
              <label>生图平台 API Key
                <input id="imageApiKey" type="password" autocomplete="off" spellcheck="false" placeholder="预留：当前不调用生图 API，会保存到本浏览器" />
              </label>
            </div>
            <div class="provider-grid">
              <label>生图平台 Base URL
                <input id="imageBaseUrl" autocomplete="off" spellcheck="false" placeholder="预留：未来接入生图 API 使用，可留空" />
              </label>
              <label>负面提示词
                <input id="imageNegativePrompt" placeholder="例如 水印、畸形手指、低清晰度、脸部变形、错误文字" />
              </label>
              <label>一致性重点
                <input id="imageConsistency" placeholder="例如 保持同一人物脸型、服装、产品外观和主色调" />
              </label>
            </div>
          </div>
        </details>
        <details>
          <summary><strong>视频生成配置</strong> <span class="muted small">用于 06_分镜生图设计师和 07_视频生成执行员</span></summary>
          <div class="details-body">
            <div class="video-grid">
              <label>视频工具
                <select id="videoTool">
                  <option value="prompt_only" selected>仅生成提示词/制作包</option>
                  <option value="sora">Sora</option>
                  <option value="runway">Runway</option>
                  <option value="pika">Pika</option>
                  <option value="seedance">Seedance</option>
                  <option value="kling">可灵 Kling</option>
                  <option value="jimeng">即梦 Jimeng</option>
                  <option value="hailuo">海螺 Hailuo</option>
                  <option value="luma">Luma</option>
                  <option value="custom">其他/自定义</option>
                </select>
              </label>
              <label>视频模型
                <input id="videoModel" list="videoModelOptions" placeholder="例如 seedance-2-0-pro / kling 2.0，可留空" />
                <datalist id="videoModelOptions">
                  <option value="seedance-2-0-pro" label="Seedance 2.0 Pro"></option>
                  <option value="seedance-2-0-lite" label="Seedance 2.0 Lite"></option>
                  <option value="sora" label="Sora"></option>
                  <option value="runway-gen-3" label="Runway Gen-3"></option>
                  <option value="pika" label="Pika"></option>
                  <option value="kling-2.0" label="可灵 2.0"></option>
                  <option value="jimeng" label="即梦"></option>
                  <option value="hailuo" label="海螺"></option>
                  <option value="luma" label="Luma"></option>
                </datalist>
              </label>
              <label>画幅
                <select id="videoAspect">
                  <option value="9:16" selected>9:16 竖屏</option>
                  <option value="16:9">16:9 横屏</option>
                  <option value="1:1">1:1 方屏</option>
                  <option value="4:5">4:5 信息流</option>
                </select>
              </label>
              <label>目标时长
                <select id="videoDuration">
                  <option value="15s">15 秒</option>
                  <option value="30s" selected>30 秒</option>
                  <option value="45s">45 秒</option>
                  <option value="60s">60 秒</option>
                  <option value="custom">按脚本自动拆分</option>
                </select>
              </label>
            </div>
            <div class="provider-grid">
              <label>视频风格
                <input id="videoStyle" placeholder="例如 真人口播、科技感、写实商业、国风、美妆种草" />
              </label>
              <label>视频平台 API Key
                <input id="videoApiKey" type="password" autocomplete="off" spellcheck="false" placeholder="预留：当前不调用视频 API，会保存到本浏览器" />
              </label>
              <label>视频平台 Base URL
                <input id="videoBaseUrl" autocomplete="off" spellcheck="false" placeholder="预留：未来接入视频 API 使用，可留空" />
              </label>
            </div>
            <div class="provider-grid">
              <label>参考图
                <input id="referenceImages" type="file" accept="image/png,image/jpeg,image/webp" multiple />
              </label>
              <label>参考图用途
                <select id="referenceRole">
                  <option value="人物一致性" selected>人物一致性</option>
                  <option value="产品参考">产品参考</option>
                  <option value="视觉风格参考">视觉风格参考</option>
                  <option value="场景参考">场景参考</option>
                  <option value="封面参考">封面参考</option>
                </select>
              </label>
              <label>参考图说明
                <input id="referenceNote" placeholder="例如：第一张固定人物参考图，后续镜头保持同一角色" />
              </label>
            </div>
            <div class="reference-list" id="referenceList"></div>
          </div>
        </details>
        <div class="row">
          <button class="primary" id="runBtn">运行工作流</button>
          <button id="sampleBtn">填入示例</button>
          <button id="clearSettingsBtn">清除已保存配置</button>
          <span id="status" class="status">准备就绪</span>
        </div>
        <div class="progress-box" id="progressBox" hidden>
          <div class="progress-head">
            <strong id="progressTitle">等待运行</strong>
            <span id="progressMeta">0/0</span>
          </div>
          <div class="progress-bar"><div class="progress-fill" id="progressFill"></div></div>
          <div class="progress-list" id="progressList"></div>
        </div>
      </div>

      <div class="panel viewer">
        <div class="viewer-head">
          <div>
            <strong id="viewerTitle">未选择任务</strong>
            <div class="muted small" id="viewerMeta">运行后会在这里查看输出文件</div>
          </div>
          <div class="file-tabs" id="fileTabs"></div>
        </div>
        <pre id="fileContent">选择左侧任务，或运行一个新任务。</pre>
      </div>
    </section>
  </main>

  <script>
    const els = {
      env: document.getElementById('env'),
      workflow: document.getElementById('workflow'),
      provider: document.getElementById('provider'),
      model: document.getElementById('model'),
      customModel: document.getElementById('customModel'),
      taskTitle: document.getElementById('taskTitle'),
      apiKey: document.getElementById('apiKey'),
      baseUrl: document.getElementById('baseUrl'),
      userInput: document.getElementById('userInput'),
      useMemory: document.getElementById('useMemory'),
      inheritTask: document.getElementById('inheritTask'),
      inheritMode: document.getElementById('inheritMode'),
      imageTool: document.getElementById('imageTool'),
      imageModel: document.getElementById('imageModel'),
      imageSize: document.getElementById('imageSize'),
      imageCount: document.getElementById('imageCount'),
      imageStyle: document.getElementById('imageStyle'),
      imageQuality: document.getElementById('imageQuality'),
      imageApiKey: document.getElementById('imageApiKey'),
      imageBaseUrl: document.getElementById('imageBaseUrl'),
      imageNegativePrompt: document.getElementById('imageNegativePrompt'),
      imageConsistency: document.getElementById('imageConsistency'),
      videoTool: document.getElementById('videoTool'),
      videoModel: document.getElementById('videoModel'),
      videoAspect: document.getElementById('videoAspect'),
      videoDuration: document.getElementById('videoDuration'),
      videoStyle: document.getElementById('videoStyle'),
      videoApiKey: document.getElementById('videoApiKey'),
      videoBaseUrl: document.getElementById('videoBaseUrl'),
      referenceImages: document.getElementById('referenceImages'),
      referenceRole: document.getElementById('referenceRole'),
      referenceNote: document.getElementById('referenceNote'),
      referenceList: document.getElementById('referenceList'),
      runBtn: document.getElementById('runBtn'),
      sampleBtn: document.getElementById('sampleBtn'),
      clearSettingsBtn: document.getElementById('clearSettingsBtn'),
      status: document.getElementById('status'),
      progressBox: document.getElementById('progressBox'),
      progressTitle: document.getElementById('progressTitle'),
      progressMeta: document.getElementById('progressMeta'),
      progressFill: document.getElementById('progressFill'),
      progressList: document.getElementById('progressList'),
      taskList: document.getElementById('taskList'),
      refreshTasks: document.getElementById('refreshTasks'),
      viewerTitle: document.getElementById('viewerTitle'),
      viewerMeta: document.getElementById('viewerMeta'),
      fileTabs: document.getElementById('fileTabs'),
      fileContent: document.getElementById('fileContent'),
    };
    let selectedTask = null;
    let selectedFile = null;
    let selectedReferenceFiles = [];
    let referencePreviewUrls = new Map();
    let progressTimer = null;
    const SETTINGS_KEY = 'my_workspace.workflow_settings.v1';

    function setStatus(text, isError = false) {
      els.status.textContent = text;
      els.status.classList.toggle('error', isError);
    }

    function resetProgress() {
      if (progressTimer) {
        clearTimeout(progressTimer);
        progressTimer = null;
      }
      els.progressBox.hidden = true;
      els.progressTitle.textContent = '等待运行';
      els.progressMeta.textContent = '0/0';
      els.progressFill.style.width = '0%';
      els.progressList.innerHTML = '';
    }

    function renderProgress(job) {
      els.progressBox.hidden = false;
      const total = job.total_steps || 0;
      const completed = job.completed_steps || 0;
      const percent = total ? Math.round((completed / total) * 100) : 0;
      const statusText = {
        queued: '排队中',
        running: '运行中',
        completed: '已完成',
        failed: '失败',
      }[job.status] || job.status || '运行中';
      const jobTitle = job.task_title || job.workflow_name || '';
      els.progressTitle.textContent = `${statusText}${jobTitle ? `：${jobTitle}` : ''}`;
      els.progressMeta.textContent = `${completed}/${total} 步 · ${percent}%`;
      els.progressFill.style.width = `${percent}%`;
      els.progressList.innerHTML = '';

      const steps = job.steps || [];
      for (const step of steps) {
        const item = document.createElement('div');
        item.className = `progress-step ${step.status || ''}`;
        const left = document.createElement('span');
        left.textContent = `${step.step}. ${step.agent_name || step.agent_id || '等待中'}`;
        const right = document.createElement('span');
        right.className = 'muted small';
        right.textContent = step.status === 'done' ? '完成' : step.status === 'active' ? '执行中' : step.status === 'error' ? '失败' : '等待';
        item.appendChild(left);
        item.appendChild(right);
        els.progressList.appendChild(item);
      }
      if (job.error) {
        const err = document.createElement('div');
        err.className = 'progress-step error';
        err.textContent = job.error;
        els.progressList.appendChild(err);
      }
    }

    async function pollRunStatus(runId) {
      const job = await api(`/api/run-status?id=${encodeURIComponent(runId)}`);
      renderProgress(job);
      if (job.status === 'completed') {
        setStatus(`完成：${job.task_title || job.workflow_name}，${job.step_count || job.completed_steps} 步`);
        await loadTasks();
        if (job.task_name) await selectTask(job.task_name);
        els.runBtn.disabled = false;
        progressTimer = null;
        return;
      }
      if (job.status === 'failed') {
        setStatus(job.error || '工作流运行失败', true);
        els.runBtn.disabled = false;
        progressTimer = null;
        return;
      }
      progressTimer = setTimeout(() => {
        pollRunStatus(runId).catch(err => {
          setStatus(err.message, true);
          els.runBtn.disabled = false;
          progressTimer = null;
        });
      }, 1000);
    }

    async function api(path, options) {
      const res = await fetch(path, options);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
      return body;
    }

    async function loadConfig() {
      const data = await api('/api/config');
      els.env.textContent = data.openai_configured ? 'OpenAI 已配置' : 'OpenAI 未配置，默认离线模式';
      els.workflow.innerHTML = data.workflows.map(w => `<option value="${w.stem}">${w.name}</option>`).join('');
      restoreSettings();
    }

    function readSettings() {
      try {
        return JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}');
      } catch {
        return {};
      }
    }

    function saveSettings() {
      const settings = {
        workflow: els.workflow.value,
        provider: els.provider.value,
        model: els.model.value,
        customModel: els.customModel.value,
        apiKey: els.apiKey.value,
        baseUrl: els.baseUrl.value,
        useMemory: els.useMemory.value,
        inheritTask: els.inheritTask.value,
        inheritMode: els.inheritMode.value,
        imageTool: els.imageTool.value,
        imageModel: els.imageModel.value,
        imageSize: els.imageSize.value,
        imageCount: els.imageCount.value,
        imageStyle: els.imageStyle.value,
        imageQuality: els.imageQuality.value,
        imageApiKey: els.imageApiKey.value,
        imageBaseUrl: els.imageBaseUrl.value,
        imageNegativePrompt: els.imageNegativePrompt.value,
        imageConsistency: els.imageConsistency.value,
        videoTool: els.videoTool.value,
        videoModel: els.videoModel.value,
        videoAspect: els.videoAspect.value,
        videoDuration: els.videoDuration.value,
        videoStyle: els.videoStyle.value,
        videoApiKey: els.videoApiKey.value,
        videoBaseUrl: els.videoBaseUrl.value,
        referenceRole: els.referenceRole.value,
        referenceNote: els.referenceNote.value,
      };
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    }

    function restoreSettings() {
      const settings = readSettings();
      setIfExists(els.workflow, settings.workflow);
      setIfExists(els.provider, settings.provider);
      setIfExists(els.model, settings.model);
      els.customModel.value = settings.customModel || '';
      els.taskTitle.value = '';
      els.apiKey.value = settings.apiKey || '';
      els.baseUrl.value = settings.baseUrl || '';
      setIfExists(els.useMemory, settings.useMemory);
      setIfExists(els.inheritTask, settings.inheritTask);
      setIfExists(els.inheritMode, settings.inheritMode);
      setIfExists(els.imageTool, settings.imageTool);
      els.imageModel.value = settings.imageModel || '';
      setIfExists(els.imageSize, settings.imageSize);
      setIfExists(els.imageCount, settings.imageCount);
      els.imageStyle.value = settings.imageStyle || '';
      setIfExists(els.imageQuality, settings.imageQuality);
      els.imageApiKey.value = settings.imageApiKey || '';
      els.imageBaseUrl.value = settings.imageBaseUrl || '';
      els.imageNegativePrompt.value = settings.imageNegativePrompt || '';
      els.imageConsistency.value = settings.imageConsistency || '';
      setIfExists(els.videoTool, settings.videoTool);
      els.videoModel.value = settings.videoModel || '';
      setIfExists(els.videoAspect, settings.videoAspect);
      setIfExists(els.videoDuration, settings.videoDuration);
      els.videoStyle.value = settings.videoStyle || '';
      els.videoApiKey.value = settings.videoApiKey || '';
      els.videoBaseUrl.value = settings.videoBaseUrl || '';
      setIfExists(els.referenceRole, settings.referenceRole);
      els.referenceNote.value = settings.referenceNote || '';
      syncCustomModelState(false);
    }

    function setIfExists(control, value) {
      if (!value) return;
      const values = Array.from(control.options || []).map(option => option.value);
      if (!values.length || values.includes(value)) control.value = value;
    }

    function bindSettingsPersistence() {
      [
        els.workflow,
        els.provider,
        els.model,
        els.customModel,
        els.taskTitle,
        els.apiKey,
        els.baseUrl,
        els.useMemory,
        els.inheritTask,
        els.inheritMode,
        els.imageTool,
        els.imageModel,
        els.imageSize,
        els.imageCount,
        els.imageStyle,
        els.imageQuality,
        els.imageApiKey,
        els.imageBaseUrl,
        els.imageNegativePrompt,
        els.imageConsistency,
        els.videoTool,
        els.videoModel,
        els.videoAspect,
        els.videoDuration,
        els.videoStyle,
        els.videoApiKey,
        els.videoBaseUrl,
        els.referenceRole,
        els.referenceNote,
      ].forEach(control => {
        control.addEventListener('change', saveSettings);
        control.addEventListener('input', saveSettings);
      });
    }

    function renderReferenceFiles() {
      els.referenceList.innerHTML = '';
      if (!selectedReferenceFiles.length) {
        els.referenceList.innerHTML = '<div class="muted small">未选择参考图</div>';
        return;
      }
      selectedReferenceFiles.forEach((file, index) => {
        if (!referencePreviewUrls.has(file)) {
          referencePreviewUrls.set(file, URL.createObjectURL(file));
        }
        const item = document.createElement('div');
        item.className = 'reference-item';
        const preview = document.createElement('img');
        preview.className = 'reference-preview';
        preview.src = referencePreviewUrls.get(file);
        preview.alt = file.name;
        const info = document.createElement('div');
        info.className = 'reference-info';
        const name = document.createElement('div');
        name.className = 'reference-name';
        name.textContent = file.name;
        const meta = document.createElement('div');
        meta.className = 'muted small';
        meta.textContent = `${Math.max(1, Math.round(file.size / 1024))} KB`;
        const remove = document.createElement('button');
        remove.className = 'icon-btn danger';
        remove.type = 'button';
        remove.title = '移除参考图';
        remove.textContent = '×';
        remove.onclick = () => {
          const previewUrl = referencePreviewUrls.get(file);
          if (previewUrl) URL.revokeObjectURL(previewUrl);
          referencePreviewUrls.delete(file);
          selectedReferenceFiles.splice(index, 1);
          renderReferenceFiles();
        };
        info.appendChild(name);
        info.appendChild(meta);
        item.appendChild(preview);
        item.appendChild(info);
        item.appendChild(remove);
        els.referenceList.appendChild(item);
      });
    }

    function clearReferenceFiles() {
      referencePreviewUrls.forEach(url => URL.revokeObjectURL(url));
      referencePreviewUrls = new Map();
      selectedReferenceFiles = [];
      els.referenceImages.value = '';
      renderReferenceFiles();
    }

    function fileToBase64(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          const result = String(reader.result || '');
          resolve(result.includes(',') ? result.split(',')[1] : result);
        };
        reader.onerror = () => reject(reader.error || new Error('读取参考图失败'));
        reader.readAsDataURL(file);
      });
    }

    async function uploadReferenceImages() {
      const uploaded = [];
      for (const file of selectedReferenceFiles) {
        const contentBase64 = await fileToBase64(file);
        const result = await api('/api/upload-reference-image', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: file.name,
            content_base64: contentBase64,
            role: els.referenceRole.value,
            note: els.referenceNote.value.trim(),
          }),
        });
        uploaded.push(result);
      }
      return uploaded;
    }

    function syncCustomModelState(focusWhenCustom = true) {
      const custom = els.model.value === 'custom';
      els.customModel.disabled = !custom;
      if (custom && focusWhenCustom) els.customModel.focus();
    }

    async function loadTasks() {
      const data = await api('/api/tasks');
      if (!data.tasks.length) {
        els.taskList.innerHTML = '<div class="muted small">暂无任务输出</div>';
        syncInheritTaskOptions([]);
        return;
      }
      els.taskList.innerHTML = '';
      syncInheritTaskOptions(data.tasks);
      for (const task of data.tasks) {
        const btn = document.createElement('button');
        btn.className = `item ${selectedTask === task.name ? 'active' : ''}`;
        const title = task.task_title || task.workflow || task.name;
        const meta = task.task_title ? `${task.workflow || ''} / ${task.name}` : task.name;
        btn.innerHTML = `<span class="item-main"><span class="item-title">${title}</span><span class="item-meta">${meta}</span></span><span class="icon-btn danger" title="删除任务" aria-label="删除任务">×</span>`;
        btn.onclick = () => selectTask(task.name);
        btn.querySelector('.icon-btn').onclick = (event) => {
          event.stopPropagation();
          deleteTask(task.name);
        };
        els.taskList.appendChild(btn);
      }
    }

    function syncInheritTaskOptions(tasks) {
      const current = els.inheritTask.value;
      els.inheritTask.innerHTML = '<option value="">不继承</option>';
      for (const task of tasks) {
        const option = document.createElement('option');
        option.value = task.name;
        option.textContent = `${task.task_title || task.workflow || task.name} / ${task.name}`;
        els.inheritTask.appendChild(option);
      }
      setIfExists(els.inheritTask, current);
    }

    async function deleteTask(name) {
      if (!confirm(`确定删除任务输出？\n\n${name}`)) return;
      setStatus('正在删除任务');
      try {
        await api('/api/delete-task', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name }),
        });
        if (selectedTask === name) {
          selectedTask = null;
          selectedFile = null;
          els.viewerTitle.textContent = '未选择任务';
          els.viewerMeta.textContent = '运行后会在这里查看输出文件';
          els.fileTabs.innerHTML = '';
          els.fileContent.textContent = '选择左侧任务，或运行一个新任务。';
        }
        setStatus('任务已删除');
        await loadTasks();
      } catch (err) {
        setStatus(err.message, true);
      }
    }

    async function selectTask(name) {
      selectedTask = name;
      selectedFile = null;
      await loadTasks();
      const data = await api(`/api/task?name=${encodeURIComponent(name)}`);
      els.viewerTitle.textContent = data.summary.task_title || data.summary.workflow || name;
      els.viewerMeta.textContent = name;
      renderFiles(data.files);
      const first = data.files.find(f => f.endsWith('final_output.md')) || data.files[0];
      if (first) await openFile(first);
    }

    function renderFiles(files) {
      els.fileTabs.innerHTML = '';
      for (const file of files) {
        const btn = document.createElement('button');
        btn.textContent = file;
        btn.className = selectedFile === file ? 'active' : '';
        btn.onclick = () => openFile(file);
        els.fileTabs.appendChild(btn);
      }
    }

    async function openFile(file) {
      if (!selectedTask) return;
      selectedFile = file;
      const data = await api(`/api/file?task=${encodeURIComponent(selectedTask)}&file=${encodeURIComponent(file)}`);
      els.fileContent.textContent = data.content;
      for (const btn of els.fileTabs.querySelectorAll('button')) {
        btn.classList.toggle('active', btn.textContent === file);
      }
    }

    async function runWorkflow() {
      const input = els.userInput.value.trim();
      if (!input) {
        setStatus('请输入原始需求', true);
        return;
      }
      const model = els.model.value === 'custom' ? els.customModel.value.trim() : els.model.value;
      if (els.model.value === 'custom' && !model) {
        setStatus('请输入自定义模型名', true);
        return;
      }
      els.runBtn.disabled = true;
      saveSettings();
      resetProgress();
      setStatus('工作流运行中');
      try {
        const referenceImages = await uploadReferenceImages();
        const imageConfig = {
          tool: els.imageTool.value,
          model: els.imageModel.value.trim(),
          size: els.imageSize.value,
          count_per_shot: els.imageCount.value,
          style: els.imageStyle.value.trim(),
          quality: els.imageQuality.value,
          negative_prompt: els.imageNegativePrompt.value.trim(),
          consistency: els.imageConsistency.value.trim(),
          api_key_provided: Boolean(els.imageApiKey.value.trim()),
          base_url_provided: Boolean(els.imageBaseUrl.value.trim()),
        };
        const videoConfig = {
          tool: els.videoTool.value,
          model: els.videoModel.value.trim(),
          aspect_ratio: els.videoAspect.value,
          duration: els.videoDuration.value,
          style: els.videoStyle.value.trim(),
          api_key_provided: Boolean(els.videoApiKey.value.trim()),
          base_url_provided: Boolean(els.videoBaseUrl.value.trim()),
        };
        const result = await api('/api/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            workflow: els.workflow.value,
            task_title: els.taskTitle.value.trim(),
            input,
            provider: els.provider.value,
            model,
            api_key: els.apiKey.value.trim(),
            base_url: els.baseUrl.value.trim(),
            use_memory: els.useMemory.value === 'on',
            inherit_task: els.inheritTask.value,
            inherit_mode: els.inheritMode.value,
            image_config: imageConfig,
            video_config: videoConfig,
            reference_images: referenceImages,
            image_api_key: els.imageApiKey.value.trim(),
            image_base_url: els.imageBaseUrl.value.trim(),
            video_api_key: els.videoApiKey.value.trim(),
            video_base_url: els.videoBaseUrl.value.trim(),
          }),
        });
        setStatus('工作流已开始，正在执行第 1 步');
        renderProgress(result);
        await pollRunStatus(result.run_id);
      } catch (err) {
        setStatus(err.message, true);
        els.runBtn.disabled = false;
      } finally {
      }
    }

    els.runBtn.onclick = runWorkflow;
    els.refreshTasks.onclick = loadTasks;
    els.model.onchange = () => {
      syncCustomModelState();
      saveSettings();
    };
    els.sampleBtn.onclick = () => {
      els.userInput.value = '我要做一条抖音短视频，推广 AI 自动化开发服务。目标客户是中小企业老板，他们想降本增效但不知道怎么落地。视频目标是让客户私信咨询，风格专业、直接、有案例感，不要夸大承诺。';
      els.taskTitle.value = 'AI自动化获客短视频';
      els.imageTool.value = 'prompt_only';
      els.imageModel.value = '';
      els.imageSize.value = '9:16';
      els.imageCount.value = '1';
      els.imageStyle.value = '写实商业，干净明亮，统一人物形象';
      els.imageQuality.value = 'standard';
      els.imageNegativePrompt.value = '水印、畸形手指、低清晰度、脸部变形、错误文字';
      els.imageConsistency.value = '保持同一人物脸型、服装、产品外观和主色调';
      els.videoTool.value = 'prompt_only';
      els.videoModel.value = '';
      els.videoAspect.value = '9:16';
      els.videoDuration.value = '30s';
      els.videoStyle.value = '真人口播，商业科技感，干净明亮';
      els.referenceRole.value = '人物一致性';
      els.referenceNote.value = '固定人物参考图，后续镜头保持同一角色与风格';
      saveSettings();
    };
    els.clearSettingsBtn.onclick = () => {
      if (!confirm('确定清除本浏览器保存的 API Key、Base URL、模型、生图配置和视频配置？')) return;
      localStorage.removeItem(SETTINGS_KEY);
      els.provider.value = 'auto';
      els.model.value = 'gpt-5.5';
      els.customModel.value = '';
      els.taskTitle.value = '';
      els.apiKey.value = '';
      els.baseUrl.value = '';
      els.useMemory.value = 'on';
      els.inheritTask.value = '';
      els.inheritMode.value = 'final_output';
      els.imageTool.value = 'prompt_only';
      els.imageModel.value = '';
      els.imageSize.value = '9:16';
      els.imageCount.value = '1';
      els.imageStyle.value = '';
      els.imageQuality.value = 'standard';
      els.imageApiKey.value = '';
      els.imageBaseUrl.value = '';
      els.imageNegativePrompt.value = '';
      els.imageConsistency.value = '';
      els.videoTool.value = 'prompt_only';
      els.videoModel.value = '';
      els.videoAspect.value = '9:16';
      els.videoDuration.value = '30s';
      els.videoStyle.value = '';
      els.videoApiKey.value = '';
      els.videoBaseUrl.value = '';
      els.referenceRole.value = '人物一致性';
      els.referenceNote.value = '';
      clearReferenceFiles();
      syncCustomModelState(false);
      setStatus('已清除本地保存配置');
    };
    els.referenceImages.onchange = () => {
      referencePreviewUrls.forEach(url => URL.revokeObjectURL(url));
      referencePreviewUrls = new Map();
      selectedReferenceFiles = Array.from(els.referenceImages.files || []);
      renderReferenceFiles();
    };
    bindSettingsPersistence();
    renderReferenceFiles();

    (async function init() {
      try {
        await loadConfig();
        await loadTasks();
      } catch (err) {
        setStatus(err.message, true);
      }
    })();
  </script>
</body>
</html>
"""


class WorkflowWebHandler(BaseHTTPRequestHandler):
    server_version = "MyWorkflowWeb/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_html(INDEX_HTML)
            elif parsed.path == "/api/config":
                self._send_json(self._config())
            elif parsed.path == "/api/tasks":
                self._send_json({"tasks": self._tasks()})
            elif parsed.path == "/api/task":
                query = parse_qs(parsed.query)
                self._send_json(self._task_detail(self._single(query, "name")))
            elif parsed.path == "/api/file":
                query = parse_qs(parsed.query)
                self._send_json(self._file_content(self._single(query, "task"), self._single(query, "file")))
            elif parsed.path == "/api/run-status":
                query = parse_qs(parsed.query)
                self._send_json(self._run_status(self._single(query, "id")))
            else:
                self.send_error(404)
        except Exception as exc:
            self._send_error(exc)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/delete-task":
                self._delete_task(str(payload.get("name") or "").strip())
                self._send_json({"ok": True})
                return

            if parsed.path == "/api/upload-reference-image":
                self._send_json(self._upload_reference_image(payload))
                return

            if parsed.path != "/api/run":
                self.send_error(404)
                return

            workflow = str(payload.get("workflow") or "").strip()
            task_title = str(payload.get("task_title") or "").strip()
            user_input = str(payload.get("input") or "").strip()
            use_memory = bool(payload.get("use_memory"))
            inherit_task = str(payload.get("inherit_task") or "").strip()
            inherit_mode = str(payload.get("inherit_mode") or "final_output").strip()
            if use_memory:
                user_input = self._append_long_term_memory(user_input)
            if inherit_task:
                user_input = self._append_inherited_task(user_input, inherit_task, inherit_mode)
            image_config = payload.get("image_config") or {}
            if image_config:
                user_input = self._append_image_config(user_input, image_config)
            video_config = payload.get("video_config") or {}
            if video_config:
                user_input = self._append_video_config(user_input, video_config)
            reference_images = payload.get("reference_images") or []
            if reference_images:
                user_input = self._append_reference_images(user_input, reference_images)
            provider = str(payload.get("provider") or "auto").strip()
            model = str(payload.get("model") or "").strip() or None
            api_key = str(payload.get("api_key") or "").strip() or None
            base_url = str(payload.get("base_url") or "").strip() or None

            if not workflow:
                raise ValueError("workflow is required")
            if not user_input:
                raise ValueError("input is required")

            run_id = uuid4().hex
            job = {
                "run_id": run_id,
                "status": "queued",
                "workflow": workflow,
                "task_title": task_title,
                "workflow_name": "",
                "created_at": time.time(),
                "updated_at": time.time(),
                "total_steps": 0,
                "completed_steps": 0,
                "steps": [],
            }
            with RUN_JOBS_LOCK:
                RUN_JOBS[run_id] = job

            worker = threading.Thread(
                target=self._run_workflow_job,
                args=(run_id, workflow, user_input, task_title, provider, model, api_key, base_url),
                daemon=True,
            )
            worker.start()
            self._send_json(job)
        except Exception as exc:
            self._send_error(exc)

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def _config(self) -> dict:
        import os

        workflows = []
        for path in sorted(WORKFLOW_ROOT.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            workflows.append(
                {
                    "stem": path.stem,
                    "file": path.name,
                    "name": data.get("name") or path.stem,
                    "description": data.get("description") or "",
                }
            )
        staff = [path.name for path in sorted(STAFF_ROOT.iterdir()) if path.is_dir()]
        return {
            "workflows": workflows,
            "staff": staff,
            "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
            "base_url_configured": bool(os.getenv("OPENAI_BASE_URL")),
            "default_model": os.getenv("OPENAI_MODEL") or "gpt-5.5",
            "default_base_url": os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        }

    def _run_status(self, run_id: str) -> dict:
        with RUN_JOBS_LOCK:
            job = RUN_JOBS.get(run_id)
            if not job:
                raise FileNotFoundError(f"Run not found: {run_id}")
            return json.loads(json.dumps(job, ensure_ascii=False))

    @staticmethod
    def _update_job(run_id: str, updates: dict) -> None:
        with RUN_JOBS_LOCK:
            job = RUN_JOBS.get(run_id)
            if not job:
                return
            job.update(updates)
            job["updated_at"] = time.time()

    @classmethod
    def _apply_progress(cls, run_id: str, event: dict) -> None:
        with RUN_JOBS_LOCK:
            job = RUN_JOBS.get(run_id)
            if not job:
                return
            kind = event.get("event")
            if kind == "started":
                total = int(event.get("total_steps") or 0)
                job.update(
                    {
                        "status": "running",
                        "workflow_name": event.get("workflow_name") or job.get("workflow_name", ""),
                        "task_title": event.get("task_title") or job.get("task_title", ""),
                        "task_dir": event.get("task_dir", ""),
                        "task_name": Path(event.get("task_dir", "")).name if event.get("task_dir") else "",
                        "total_steps": total,
                        "completed_steps": 0,
                        "steps": [
                            {"step": step_no, "status": "pending", "agent_id": "", "agent_name": ""}
                            for step_no in range(1, total + 1)
                        ],
                    }
                )
            elif kind == "step_started":
                step_no = int(event.get("step") or 0)
                cls._set_step(job, step_no, event, "active")
            elif kind == "step_completed":
                step_no = int(event.get("step") or 0)
                cls._set_step(job, step_no, event, "done")
                job["completed_steps"] = max(int(job.get("completed_steps") or 0), step_no)
            elif kind == "completed":
                job.update(
                    {
                        "status": "completed",
                        "workflow_name": event.get("workflow_name") or job.get("workflow_name", ""),
                        "task_title": event.get("task_title") or job.get("task_title", ""),
                        "task_dir": event.get("task_dir") or job.get("task_dir", ""),
                        "task_name": Path(event.get("task_dir", "")).name if event.get("task_dir") else job.get("task_name", ""),
                        "provider": event.get("provider", ""),
                        "step_count": event.get("step_count", 0),
                        "completed_steps": event.get("step_count", job.get("completed_steps", 0)),
                        "final_output": event.get("final_output", ""),
                    }
                )
            job["updated_at"] = time.time()

    @staticmethod
    def _set_step(job: dict, step_no: int, event: dict, status: str) -> None:
        if step_no <= 0:
            return
        steps = job.setdefault("steps", [])
        while len(steps) < step_no:
            steps.append({"step": len(steps) + 1, "status": "pending", "agent_id": "", "agent_name": ""})
        steps[step_no - 1].update(
            {
                "step": step_no,
                "status": status,
                "agent_id": event.get("agent_id", ""),
                "agent_name": event.get("agent_name", ""),
                "task": event.get("task", ""),
                "expected_output": event.get("expected_output", ""),
                "output_path": event.get("output_path", ""),
            }
        )

    def _run_workflow_job(
        self,
        run_id: str,
        workflow: str,
        user_input: str,
        task_title: str,
        provider: str,
        model: str | None,
        api_key: str | None,
        base_url: str | None,
    ) -> None:
        try:
            self._update_job(run_id, {"status": "running"})
            engine = WorkflowEngine(
                WORKSPACE_ROOT,
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
            )
            engine.run(
                workflow,
                user_input,
                task_title=task_title,
                progress_callback=lambda event: self._apply_progress(run_id, event),
            )
        except Exception as exc:
            with RUN_JOBS_LOCK:
                job = RUN_JOBS.get(run_id)
                if job:
                    job["status"] = "failed"
                    job["error"] = str(exc)
                    job["traceback"] = traceback.format_exc()
                    for step in job.get("steps", []):
                        if step.get("status") == "active":
                            step["status"] = "error"
                    job["updated_at"] = time.time()

    def _tasks(self) -> list[dict]:
        if not OUTPUT_ROOT.exists():
            return []

        tasks = []
        for path in sorted(OUTPUT_ROOT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.is_dir():
                continue
            summary_path = path / "run_summary.json"
            summary = {}
            if summary_path.exists():
                try:
                    summary = json.loads(summary_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    summary = {}
            tasks.append(
                {
                    "name": path.name,
                    "task_title": summary.get("task_title") or "",
                    "workflow": summary.get("workflow") or path.name,
                    "provider": summary.get("provider") or "",
                    "mtime": path.stat().st_mtime,
                }
            )
        return tasks

    def _task_detail(self, name: str) -> dict:
        task_dir = self._safe_task_dir(name)
        files = []
        for path in sorted(task_dir.rglob("*")):
            if path.is_file():
                files.append(path.relative_to(task_dir).as_posix())

        summary_path = task_dir / "run_summary.json"
        summary = {}
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return {"name": name, "summary": summary, "files": files}

    def _file_content(self, task: str, file_name: str) -> dict:
        task_dir = self._safe_task_dir(task)
        target = (task_dir / file_name).resolve()
        if not target.is_file() or not self._is_relative_to(target, task_dir.resolve()):
            raise FileNotFoundError(file_name)

        content_type = mimetypes.guess_type(target.name)[0] or "text/plain"
        if not content_type.startswith("text/") and target.suffix.lower() not in {".json", ".md"}:
            raise ValueError(f"Unsupported file type: {target.name}")
        return {"file": file_name, "content": target.read_text(encoding="utf-8", errors="replace")}

    @staticmethod
    def _append_image_config(user_input: str, image_config: dict) -> str:
        def value(key: str, default: str = "未填写") -> str:
            item = image_config.get(key)
            return str(item).strip() if item not in (None, "") else default

        api_note = "已填写，当前版本仅记录为可用条件，不保存密钥、不直接调用生图 API" if image_config.get("api_key_provided") else "未填写"
        base_url_note = "已填写，当前版本仅记录为可用条件，不保存地址到输出" if image_config.get("base_url_provided") else "未填写"
        return (
            f"{user_input}\n\n"
            "## 生图配置\n"
            f"- 生图工具：{value('tool')}\n"
            f"- 生图模型：{value('model')}\n"
            f"- 图片尺寸/画幅：{value('size', '9:16')}\n"
            f"- 每镜头图片数：{value('count_per_shot', '1')}\n"
            f"- 生图风格：{value('style')}\n"
            f"- 生图质量：{value('quality', 'standard')}\n"
            f"- 负面提示词：{value('negative_prompt')}\n"
            f"- 一致性重点：{value('consistency')}\n"
            f"- 生图平台 API Key：{api_note}\n"
            f"- 生图平台 Base URL：{base_url_note}\n"
            "- 执行要求：当前阶段由 06_分镜生图设计师输出分镜总表、关键帧生图提示词、参考图使用策略和连续性控制说明；不要声称已经生成图片文件。\n"
        )

    @staticmethod
    def _append_video_config(user_input: str, video_config: dict) -> str:
        def value(key: str, default: str = "未填写") -> str:
            item = video_config.get(key)
            return str(item).strip() if item not in (None, "") else default

        api_note = "已填写，当前版本仅记录为可用条件，不保存密钥、不直接调用视频 API" if video_config.get("api_key_provided") else "未填写"
        base_url_note = "已填写，当前版本仅记录为可用条件，不保存地址到输出" if video_config.get("base_url_provided") else "未填写"
        return (
            f"{user_input}\n\n"
            "## 视频生成配置\n"
            f"- 视频工具：{value('tool')}\n"
            f"- 视频模型：{value('model')}\n"
            f"- 画幅：{value('aspect_ratio', '9:16')}\n"
            f"- 目标时长：{value('duration', '30s')}\n"
            f"- 视频风格：{value('style')}\n"
            f"- 视频平台 API Key：{api_note}\n"
            f"- 视频平台 Base URL：{base_url_note}\n"
            "- 执行要求：当前阶段由 06_分镜生图设计师输出分镜生图方案，再由 07_视频生成执行员输出视频生成提示词、镜头清单、TTS 配音稿、SRT 字幕草案和剪辑说明；不要声称已经生成 mp4。\n"
        )

    def _upload_reference_image(self, payload: dict) -> dict:
        filename = str(payload.get("filename") or "").strip()
        content_base64 = str(payload.get("content_base64") or "").strip()
        role = str(payload.get("role") or "参考图").strip()
        note = str(payload.get("note") or "").strip()
        if not filename or not content_base64:
            raise ValueError("filename and content_base64 are required")

        suffix = Path(filename).suffix.lower()
        allowed = {".jpg", ".jpeg", ".png", ".webp"}
        if suffix not in allowed:
            raise ValueError(f"Unsupported image type: {suffix}")

        image_bytes = base64.b64decode(content_base64, validate=True)
        if len(image_bytes) > 12 * 1024 * 1024:
            raise ValueError("Reference image is too large; max size is 12 MB")

        REFERENCE_ROOT.mkdir(parents=True, exist_ok=True)
        safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in Path(filename).stem)[:80]
        target = REFERENCE_ROOT / f"{safe_stem}_{uuid4().hex[:8]}{suffix}"
        target.write_bytes(image_bytes)

        relative_path = target.relative_to(WORKSPACE_ROOT).as_posix()
        return {
            "filename": filename,
            "stored_path": relative_path,
            "role": role,
            "note": note,
            "size_bytes": len(image_bytes),
        }

    @staticmethod
    def _append_reference_images(user_input: str, reference_images: list[dict]) -> str:
        lines = ["## 参考图", "以下参考图由管理台上传到本地，供 06_分镜生图设计师和 07_视频生成执行员作为角色/产品/风格参考："]
        for index, image in enumerate(reference_images, start=1):
            lines.extend(
                [
                    f"{index}. 文件名：{image.get('filename', '')}",
                    f"   - 本地路径：{image.get('stored_path', '')}",
                    f"   - 用途：{image.get('role', '参考图')}",
                    f"   - 说明：{image.get('note', '') or '无'}",
                ]
            )
        lines.append("执行要求：如果视频工具支持参考图或图生视频，应在镜头提示词中明确使用这些参考图保持人物、产品或视觉风格一致；不要声称已经分析图片内容。")
        return f"{user_input}\n\n" + "\n".join(lines) + "\n"

    def _delete_task(self, name: str) -> None:
        task_dir = self._safe_task_dir(name)
        if task_dir == OUTPUT_ROOT.resolve():
            raise ValueError("Refusing to delete output root")

        for path in sorted(task_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        task_dir.rmdir()

    def _append_long_term_memory(self, user_input: str) -> str:
        if not MEMORY_ROOT.exists():
            return user_input

        sections = []
        for path in sorted(MEMORY_ROOT.glob("*.md")):
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                sections.append(f"### {path.name}\n{content}")

        if not sections:
            return user_input
        return f"{user_input}\n\n## 长期记忆\n" + "\n\n".join(sections) + "\n"

    def _append_inherited_task(self, user_input: str, task_name: str, inherit_mode: str) -> str:
        task_dir = self._safe_task_dir(task_name)
        files = ["final_output.md"]
        if inherit_mode == "input_and_final":
            files = ["input.md", "final_output.md"]

        sections = []
        for file_name in files:
            path = task_dir / file_name
            if path.exists() and path.is_file():
                content = path.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    sections.append(f"### {task_name}/{file_name}\n{content}")

        if not sections:
            return user_input
        return f"{user_input}\n\n## 继承历史任务记忆\n" + "\n\n".join(sections) + "\n"

    def _safe_task_dir(self, name: str) -> Path:
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError("Invalid task name")
        task_dir = (OUTPUT_ROOT / name).resolve()
        output_root = OUTPUT_ROOT.resolve()
        if not self._is_relative_to(task_dir, output_root) or not task_dir.is_dir():
            raise FileNotFoundError(name)
        return task_dir

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    @staticmethod
    def _single(query: dict[str, list[str]], key: str) -> str:
        values = query.get(key)
        if not values:
            raise ValueError(f"Missing query parameter: {key}")
        return values[0]

    @staticmethod
    def _is_relative_to(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, exc: Exception) -> None:
        traceback.print_exc()
        self._send_json({"error": str(exc)}, status=400)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    import argparse

    parser = argparse.ArgumentParser(description="Start my_workspace visual workflow manager.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), WorkflowWebHandler)
    print(f"自媒体工作流管理台: http://{args.host}:{args.port}")
    print("按 Ctrl+C 停止服务")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
