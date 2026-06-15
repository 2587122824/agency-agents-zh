from __future__ import annotations

import base64
import json
import mimetypes
import shutil
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from my_codex_core.workflow_engine import WorkflowEngine


WORKSPACE_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = WORKSPACE_ROOT / "my_task_output"
WORKFLOW_ROOT = WORKSPACE_ROOT / "my_workflows"
STAFF_ROOT = WORKSPACE_ROOT / "my_custom_staff"
MEMORY_ROOT = WORKSPACE_ROOT / "my_memory"
REFERENCE_ROOT = WORKSPACE_ROOT / "my_reference_images"
VOICE_SAMPLE_ROOT = WORKSPACE_ROOT / "my_voice_samples"
KNOWLEDGE_ROOT = WORKSPACE_ROOT / "my_knowledge_base"
LOCAL_MODEL_PRESETS = WORKSPACE_ROOT / "my_local_models" / "local_model_presets.json"
RUN_JOBS: dict[str, dict] = {}
RUN_JOBS_LOCK = threading.RLock()


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>自定义工作流管理台</title>
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
      --ok: #166534;
      --warn: #92400e;
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
    .brand {
      display: flex;
      align-items: center;
      gap: 18px;
      min-width: 0;
    }
    h1 {
      margin: 0;
      font-size: 18px;
      font-weight: 650;
      white-space: nowrap;
    }
    .top-nav {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 4px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f8fafc;
    }
    .nav-btn {
      min-height: 30px;
      padding: 5px 10px;
      border: 0;
      background: transparent;
      color: var(--muted);
      font-size: 13px;
    }
    .nav-btn.active {
      background: #fff;
      color: var(--accent);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      font-weight: 650;
    }
    main {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      min-height: calc(100vh - 56px);
    }
    body[data-view="run"] main,
    body[data-view="staff"] main,
    body[data-view="workflow"] main,
    body[data-view="system"] main {
      grid-template-columns: 1fr;
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
    .view[hidden], aside[hidden] { display: none; }
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
    .run-form {
      gap: 16px;
    }
    .run-section {
      border-bottom: 1px solid var(--line);
      padding: 0 0 14px;
      display: grid;
      gap: 12px;
      min-width: 0;
    }
    .run-section:last-of-type {
      border-bottom: 0;
      padding-bottom: 0;
    }
    .run-section-head {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 12px;
      flex-wrap: wrap;
    }
    .run-section-head strong {
      font-size: 15px;
    }
    .run-primary-grid {
      display: grid;
      grid-template-columns: minmax(180px, .8fr) minmax(240px, 1fr) minmax(240px, 1fr);
      gap: 12px;
    }
    .run-model-grid {
      display: grid;
      grid-template-columns: minmax(160px, .65fr) minmax(280px, 1fr) minmax(160px, .65fr);
      gap: 12px;
    }
    .run-input textarea {
      min-height: 150px;
    }
    .run-actions {
      position: sticky;
      bottom: 0;
      z-index: 2;
      margin: 0 -16px -16px;
      padding: 12px 16px;
      border-top: 1px solid var(--line);
      background: rgba(255, 255, 255, .96);
      backdrop-filter: blur(6px);
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
    [hidden] {
      display: none !important;
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
      grid-template-rows: auto auto minmax(320px, 1fr);
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
    .output-dashboard {
      display: grid;
      gap: 12px;
      padding: 12px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfd;
    }
    .output-summary-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(140px, 1fr));
      gap: 10px;
    }
    .output-card {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 10px;
      display: grid;
      gap: 5px;
      min-width: 0;
    }
    .output-card .label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .output-card .value {
      font-size: 15px;
      font-weight: 700;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .output-sections {
      display: grid;
      grid-template-columns: minmax(260px, 1fr) minmax(260px, 1fr);
      gap: 10px;
      min-width: 0;
    }
    .output-section {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 10px;
      display: grid;
      gap: 8px;
      min-width: 0;
    }
    .output-section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }
    .output-link-list {
      display: grid;
      gap: 6px;
      max-height: 180px;
      overflow: auto;
    }
    .output-link {
      display: grid;
      gap: 2px;
      text-align: left;
      min-height: 58px;
      padding: 9px 10px;
      border-radius: 6px;
      background: #fff;
      align-content: center;
      min-width: 0;
      overflow: hidden;
    }
    .output-link.active {
      border-color: var(--accent);
      color: var(--accent);
      box-shadow: inset 3px 0 0 var(--accent);
    }
    .output-link span {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .output-link .output-link-title {
      font-weight: 650;
      line-height: 1.3;
    }
    .output-link .output-link-subtitle {
      display: block;
      line-height: 1.25;
    }
    .staff-manager {
      display: grid;
      grid-template-columns: minmax(300px, 360px) minmax(0, 1fr);
      gap: 14px;
      align-items: start;
      width: 100%;
      min-width: 0;
      overflow: hidden;
    }
    .manager-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      padding-bottom: 2px;
    }
    .manager-title {
      display: grid;
      gap: 3px;
    }
    .manager-title strong {
      font-size: 16px;
    }
    .manager-actions {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }
    .staff-sidebar {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfd;
      padding: 10px;
      display: grid;
      gap: 10px;
      min-width: 0;
    }
    .staff-list {
      display: grid;
      gap: 6px;
      align-content: start;
      max-height: calc(100vh - 250px);
      overflow: auto;
      padding-right: 4px;
    }
    .staff-card {
      text-align: left;
      padding: 9px 10px;
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 6px;
      display: grid;
      gap: 4px;
      min-width: 0;
      min-height: 76px;
      align-content: start;
      overflow: hidden;
    }
    .staff-card strong {
      font-size: 14px;
      line-height: 1.25;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .staff-card .staff-meta {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .staff-card .staff-role {
      justify-self: start;
      max-width: 100%;
      margin-top: 2px;
      padding: 2px 6px;
      border-radius: 6px;
      background: #e6f4f1;
      color: var(--accent);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .staff-card.active {
      border-color: var(--accent);
      box-shadow: 0 0 0 2px rgba(15, 118, 110, .12);
      background: #f0fdfa;
    }
    .staff-editor {
      display: grid;
      gap: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
      padding: 12px;
      min-width: 0;
      max-width: 100%;
      overflow: hidden;
    }
    .staff-editor label {
      min-width: 0;
    }
    .staff-editor input,
    .staff-editor textarea {
      min-width: 0;
      max-width: 100%;
    }
    .staff-editor textarea {
      min-height: 220px;
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 13px;
      overflow: auto;
      white-space: pre;
    }
    .workflow-step {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 10px;
      display: grid;
      gap: 10px;
    }
    .workflow-step-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
    }
    .workflow-step-grid {
      display: grid;
      grid-template-columns: minmax(180px, 240px) minmax(220px, 1fr) minmax(220px, 1fr);
      gap: 10px;
    }
    .health-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(180px, 1fr));
      gap: 12px;
    }
    .health-card {
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 12px;
      display: grid;
      gap: 6px;
      min-height: 96px;
    }
    .health-card.ok { border-color: #bbf7d0; background: #f0fdf4; }
    .health-card.warn { border-color: #fde68a; background: #fffbeb; }
    .health-card.error { border-color: #fecdca; background: #fef3f2; }
    .health-card strong {
      font-size: 14px;
    }
    .health-state {
      font-weight: 650;
      color: var(--muted);
    }
    .health-card.ok .health-state { color: var(--ok); }
    .health-card.warn .health-state { color: var(--warn); }
    .health-card.error .health-state { color: var(--danger); }
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
    .file-editor {
      min-height: 520px;
      border: 0;
      border-radius: 0;
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 13px;
      line-height: 1.55;
      background: #fff;
      resize: vertical;
    }
    .muted { color: var(--muted); }
    .small { font-size: 12px; }
    @media (max-width: 860px) {
      header {
        height: auto;
        align-items: flex-start;
        gap: 10px;
        padding: 12px;
        flex-direction: column;
      }
      .brand {
        width: 100%;
        align-items: flex-start;
        gap: 10px;
        flex-direction: column;
      }
      .top-nav { width: 100%; overflow-x: auto; }
      main { grid-template-columns: 1fr; }
      aside { border-right: 0; border-bottom: 1px solid var(--line); }
      .split { grid-template-columns: 1fr; }
      .run-primary-grid { grid-template-columns: 1fr; }
      .run-model-grid { grid-template-columns: 1fr; }
      .run-actions { position: static; margin: 0; padding: 0; border-top: 0; background: transparent; }
      .provider-grid { grid-template-columns: 1fr; }
      .video-grid { grid-template-columns: 1fr; }
      .staff-manager { grid-template-columns: 1fr; }
      .output-summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .output-sections { grid-template-columns: 1fr; }
      .workflow-step-grid { grid-template-columns: 1fr; }
      .health-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body data-view="run">
  <header>
    <div class="brand">
      <h1>自定义工作流管理台</h1>
      <nav class="top-nav" aria-label="主功能">
        <button class="nav-btn active" data-view-target="run" type="button">运行工作流</button>
        <button class="nav-btn" data-view-target="staff" type="button">数字员工</button>
        <button class="nav-btn" data-view-target="workflow" type="button">工作流</button>
        <button class="nav-btn" data-view-target="output" type="button">任务输出</button>
        <button class="nav-btn" data-view-target="system" type="button">系统状态</button>
      </nav>
    </div>
    <div class="muted small" id="env">加载中</div>
  </header>
  <main>
    <aside id="taskSidebar" hidden>
      <div class="row">
        <strong>任务输出</strong>
        <button id="refreshTasks" title="刷新任务列表">刷新</button>
      </div>
      <div class="list" id="taskList"></div>
    </aside>
    <section class="stack">
      <div class="panel form run-form view" data-view="run">
        <div class="run-section">
          <div class="run-section-head">
            <strong>任务基础信息</strong>
            <span class="muted small">先确定要跑什么，再配置模型和生产选项</span>
          </div>
          <div class="run-primary-grid">
            <label>产品类型
              <select id="productTemplate">
                <option value="short_video" selected>短视频</option>
                <option value="xiaohongshu">小红书图文</option>
                <option value="game_steam">Unity 3D Steam 游戏</option>
                <option value="software_market">软件市场分析</option>
                <option value="agent_platform">AI 员工平台</option>
              </select>
            </label>
            <label>工作流
              <select id="workflow"></select>
            </label>
            <label>任务名称
              <input id="taskTitle" autocomplete="off" spellcheck="false" placeholder="例如 AI自动化获客短视频-第1版，可留空" />
            </label>
          </div>
          <div class="run-model-grid">
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
            <label>模型超时
              <select id="modelTimeout">
                <option value="120">120 秒（云端默认）</option>
                <option value="300">300 秒</option>
                <option value="600">600 秒</option>
                <option value="900" selected>900 秒（本地模型推荐）</option>
                <option value="1800">1800 秒</option>
              </select>
            </label>
          </div>
        </div>
        <div class="run-section run-input">
          <div class="run-section-head">
            <strong>原始需求</strong>
            <span class="muted small">这里写清目标用户、平台、风格、交付目标</span>
          </div>
          <label>需求内容
            <textarea id="userInput" placeholder="例如：我要做一条抖音短视频，推广 AI 自动化开发服务，目标客户是中小企业老板，目标是让客户私信咨询。"></textarea>
          </label>
        </div>
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
            <div class="row">
              <button id="localOfflineBtn" type="button">一键本地离线模式</button>
              <span class="muted small">自动使用 Ollama + qwen3:8b-q4_K_M + 项目内 runtime/models</span>
            </div>
            <div class="provider-grid">
              <label>本地模型服务
                <select id="localModelPreset">
                  <option value="">不使用本地预设</option>
                </select>
              </label>
              <label>本地模型名
                <select id="localModelName">
                  <option value="">先选择本地模型服务</option>
                </select>
              </label>
              <label>连接测试
                <button id="testModelBtn" type="button">测试当前模型接口</button>
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
                  <option value="video_output" selected>仅视频输出阶段使用 my_memory</option>
                  <option value="off">不使用长期记忆</option>
                  <option value="all">全流程使用 my_memory（高级）</option>
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
            <div class="provider-grid">
              <label>本地知识库
                <select id="useKnowledge">
                  <option value="off" selected>不追加知识库</option>
                  <option value="on">追加 my_knowledge_base</option>
                </select>
              </label>
              <label>上传知识文件
                <input id="knowledgeFile" type="file" accept=".md,.txt,.json,.csv" />
              </label>
              <label>知识库操作
                <button id="uploadKnowledgeBtn" type="button">上传到知识库</button>
              </label>
            </div>
            <div class="reference-list" id="knowledgeList"></div>
          </div>
        </details>
        <details>
          <summary><strong>全自动生成</strong> <span class="muted small">按成本生成素材、配音字幕，并输出剪辑成片方案</span></summary>
          <div class="details-body">
            <div class="provider-grid">
              <label>自动生成模式
                <select id="autoProductionMode">
                  <option value="off" selected>关闭</option>
                  <option value="package_only">只生成视频制作包</option>
                  <option value="audio_package">生成视频 + 语音字幕制作包</option>
                  <option value="api_ready">调用生图/生视频 API</option>
                  <option value="comfy_full">ComfyUI 全自动成片（高算力预留）</option>
                </select>
              </label>
              <label>剪辑/合成工具
                <select id="composeTool">
                  <option value="ffmpeg" selected>ffmpeg</option>
                  <option value="runninghub">RunningHub / 云端 ComfyUI（素材/预览）</option>
                  <option value="jianying">剪映工程（预留）</option>
                  <option value="manual">只生成清单</option>
                </select>
              </label>
              <label>最终视频文件名
                <input id="finalVideoName" autocomplete="off" spellcheck="false" placeholder="final_video.mp4" />
              </label>
            </div>
            <div class="provider-grid">
              <label>成片平台密钥
                <input id="comfyApiKey" type="password" autocomplete="off" spellcheck="false" placeholder="RunningHub 或云端 ComfyUI API Key" />
              </label>
              <label>成片平台接口地址
                <input id="comfyBaseUrl" autocomplete="off" spellcheck="false" placeholder="RunningHub: https://www.runninghub.cn/openapi/v2" />
              </label>
              <label>成片工作流接口
                <input id="comfyWorkflowEndpoint" autocomplete="off" spellcheck="false" placeholder="/run/workflow/你的全自动成片工作流ID 或 /run/ai-app/你的应用ID" />
              </label>
            </div>
            <div class="provider-grid">
              <label>本地配音
                <select id="voiceMode">
                  <option value="off" selected>不生成配音音频</option>
                  <option value="voxcpm2">VoxCPM2 本地仿声</option>
                </select>
              </label>
              <label>本人参考音频
                <input id="voiceReferenceFile" type="file" accept="audio/wav,audio/mpeg,audio/mp4,audio/flac,audio/ogg,.wav,.mp3,.m4a,.flac,.ogg" />
              </label>
              <label>已上传参考音频路径
                <input id="voiceReferenceAudioPath" autocomplete="off" spellcheck="false" placeholder="上传后自动填入，也可手动填 my_voice_samples/xxx.wav" />
              </label>
            </div>
            <div class="provider-grid">
              <label>参考音频原文
                <input id="voiceReferenceText" autocomplete="off" spellcheck="false" placeholder="可选：参考音频里本人说的话，能提高仿声稳定性" />
              </label>
              <label>VoxCPM2 命令模板
                <input id="voiceCommandTemplate" autocomplete="off" spellcheck="false" placeholder="voxcpm clone --text-file {text_file} --reference-audio {reference_audio} --output {output_file}" />
              </label>
              <label>配音超时
                <select id="voiceTimeout">
                  <option value="900">15 分钟</option>
                  <option value="1800" selected>30 分钟</option>
                  <option value="3600">60 分钟</option>
                  <option value="7200">120 分钟</option>
                </select>
              </label>
            </div>
            <div class="provider-grid">
              <label>ComfyUI 节点映射 JSON
                <textarea id="comfyNodeInfoList" spellcheck="false" placeholder='[]; 可使用 {{prompt}}、{{negative_prompt}}、{{reference_image}}、{{voice_text}}、{{subtitle_srt}}、{{payload}}'></textarea>
              </label>
              <label>导入 API JSON 自动识别
                <input id="comfyApiWorkflowFile" type="file" accept=".json,application/json" />
              </label>
              <label>成片轮询超时
                <select id="comfyPollTimeout">
                  <option value="900">15 分钟</option>
                  <option value="1800">30 分钟</option>
                  <option value="3600" selected>60 分钟</option>
                  <option value="7200">120 分钟</option>
                </select>
              </label>
            </div>
            <div class="reference-list" id="comfyParameterMapper"></div>
          </div>
        </details>
        <details hidden>
          <summary><strong>生图配置</strong> <span class="muted small">只填正向提示词；参考图在下方单独上传，其余参数在 ComfyUI 工作流里配置</span></summary>
          <div class="details-body">
            <div class="provider-grid">
              <label>生图正向提示词
                <input id="imagePositivePrompt" autocomplete="off" spellcheck="false" placeholder="例如：专业、真实、干净明亮的AI自动化服务宣传画面，人物和产品风格统一" />
              </label>
            </div>
            <div class="video-grid" hidden>
              <label>生图工具
                <select id="imageTool">
                  <option value="prompt_only" selected>仅生成生图提示词</option>
                  <option value="gpt-image">GPT Image</option>
                  <option value="midjourney">Midjourney</option>
                  <option value="stable-diffusion">Stable Diffusion</option>
                  <option value="flux">FLUX</option>
                  <option value="runninghub">RunningHub 云端 ComfyUI</option>
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
            <div class="provider-grid" hidden>
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
              <label>生图平台密钥
                <input id="imageApiKey" type="password" autocomplete="off" spellcheck="false" placeholder="预留：当前不调用生图 API，会保存到本浏览器" />
              </label>
            </div>
            <div class="provider-grid" hidden>
              <label>生图平台接口地址
                <input id="imageBaseUrl" autocomplete="off" spellcheck="false" placeholder="RunningHub: https://www.runninghub.cn/openapi/v2" />
              </label>
              <label hidden>RunningHub Workflow Endpoint
                <input id="imageWorkflowEndpoint" autocomplete="off" spellcheck="false" placeholder="/run/workflow/2048294089858228226" />
              </label>
              <label hidden>RunningHub Instance Type
                <select id="imageInstanceType">
                  <option value="default" selected>default - 24G VRAM</option>
                  <option value="plus">plus - 48G VRAM</option>
                </select>
              </label>
            </div>
            <div class="provider-grid" hidden>
              <label>负面提示词
                <input id="imageNegativePrompt" placeholder="例如 水印、畸形手指、低清晰度、脸部变形、错误文字" />
              </label>
              <label>一致性重点
                <input id="imageConsistency" placeholder="例如 保持同一人物脸型、服装、产品外观和主色调" />
              </label>
            </div>
            <div class="provider-grid" hidden>
              <label>RunningHub nodeInfoList JSON
                <textarea id="imageNodeInfoList" spellcheck="false" placeholder='[]; use {{prompt}} inside JSON strings to inject the generated prompt'></textarea>
              </label>
              <label>RunningHub Poll Timeout
                <select id="imagePollTimeout">
                  <option value="300">5 min</option>
                  <option value="900" selected>15 min</option>
                  <option value="1800">30 min</option>
                  <option value="3600">60 min</option>
                </select>
              </label>
            </div>
            <div class="provider-grid" hidden>
              <label>Image Seed
                <input id="imageSeed" autocomplete="off" spellcheck="false" placeholder="Leave blank for random; fixed seed improves repeatability" />
              </label>
              <label>Image Guidance / CFG
                <input id="imageGuidance" autocomplete="off" spellcheck="false" placeholder="Example: 3.5 / 7 / 12; blank uses workflow default" />
              </label>
              <label>Image Steps
                <input id="imageSteps" autocomplete="off" spellcheck="false" placeholder="Example: 20 / 30; blank uses workflow default" />
              </label>
            </div>
            <div class="provider-grid" hidden>
              <label>Image Denoise Strength
                <input id="imageDenoise" autocomplete="off" spellcheck="false" placeholder="Image-to-image repaint strength, e.g. 0.35 / 0.65" />
              </label>
              <label>Image Sampler
                <input id="imageSampler" autocomplete="off" spellcheck="false" placeholder="Example: euler / dpmpp_2m; blank uses workflow default" />
              </label>
              <label>Image LoRA / Control
                <input id="imageControl" autocomplete="off" spellcheck="false" placeholder="LoRA, ControlNet, IP-Adapter, face reference notes" />
              </label>
            </div>
          </div>
        </details>
        <details hidden>
          <summary><strong>视频生成配置</strong> <span class="muted small">只填正向提示词；镜头、尺寸、采样等参数在视频工作流里配置</span></summary>
          <div class="details-body">
            <div class="provider-grid">
              <label>生视频正向提示词
                <input id="videoPositivePrompt" autocomplete="off" spellcheck="false" placeholder="例如：专业真实的短视频，人物口播自然，镜头稳定，突出AI自动化服务价值" />
              </label>
            </div>
            <div class="video-grid" hidden>
              <label>视频工具
                <select id="videoTool">
                  <option value="prompt_only" selected>仅生成提示词/制作包</option>
                  <option value="sora">Sora</option>
                  <option value="runway">Runway</option>
                  <option value="pika">Pika</option>
                  <option value="seedance">Seedance</option>
                  <option value="runninghub">RunningHub 视频应用</option>
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
            <div class="provider-grid" hidden>
              <label>视频风格
                <input id="videoStyle" placeholder="例如 真人口播、科技感、写实商业、国风、美妆种草" />
              </label>
              <label>视频画面与运动要求
                <input id="videoPromptNotes" autocomplete="off" spellcheck="false" placeholder="例如 半身口播推近到产品特写，人物动作自然，节奏干净，避免夸张运镜" />
              </label>
              <label>视频平台密钥
                <input id="videoApiKey" type="password" autocomplete="off" spellcheck="false" placeholder="预留：当前不调用视频 API，会保存到本浏览器" />
              </label>
              <label>视频平台接口地址
                <input id="videoBaseUrl" autocomplete="off" spellcheck="false" placeholder="RunningHub: https://www.runninghub.cn/openapi/v2" />
              </label>
            </div>
            <div class="provider-grid" hidden>
              <label>视频负面提示词
                <input id="videoNegativePrompt" autocomplete="off" spellcheck="false" placeholder="例如 水印、闪烁、畸形手、错误文字、脸部变形" />
              </label>
              <label hidden>Video Seed
                <input id="videoSeed" autocomplete="off" spellcheck="false" placeholder="Leave blank for random; fixed seed improves repeatability" />
              </label>
              <label hidden>Video FPS
                <select id="videoFps">
                  <option value="">Workflow default</option>
                  <option value="24">24</option>
                  <option value="30" selected>30</option>
                  <option value="60">60</option>
                </select>
              </label>
            </div>
            <div class="provider-grid" hidden>
              <label>运动强度
                <select id="videoMotionStrength">
                  <option value="low">低：稳定轻微运动</option>
                  <option value="medium" selected>中：自然运动</option>
                  <option value="high">高：强运动或强转场</option>
                </select>
              </label>
              <label>镜头运动
                <select id="videoCameraMotion">
                  <option value="static">固定机位</option>
                  <option value="push_in" selected>推近</option>
                  <option value="pull_out">拉远</option>
                  <option value="pan">横移/摇镜</option>
                  <option value="orbit">环绕</option>
                  <option value="handheld">手持感</option>
                </select>
              </label>
              <label hidden>Video Resolution
                <select id="videoResolution">
                  <option value="">Workflow default</option>
                  <option value="720p">720p</option>
                  <option value="1080p" selected>1080p</option>
                  <option value="4k">4K</option>
                </select>
              </label>
            </div>
            <div class="provider-grid" hidden>
              <label>Video Guidance
                <input id="videoGuidance" autocomplete="off" spellcheck="false" placeholder="Prompt guidance strength; blank uses workflow default" />
              </label>
              <label>Video Frames
                <input id="videoFrames" autocomplete="off" spellcheck="false" placeholder="Frame count; blank derives from duration and fps" />
              </label>
              <label>Image Strength
                <input id="videoImageStrength" autocomplete="off" spellcheck="false" placeholder="Reference or first-frame strength, e.g. 0.45 / 0.75" />
              </label>
            </div>
            <div class="provider-grid" hidden>
              <label>Camera Path / Shot Notes
                <input id="videoCameraPath" autocomplete="off" spellcheck="false" placeholder="Complex camera path, shot array, first/last-frame notes" />
              </label>
              <label>Audio / Subtitle Notes
                <input id="videoAudioNotes" autocomplete="off" spellcheck="false" placeholder="Voiceover, subtitles, background music, or mute notes" />
              </label>
              <label>Advanced Model Params
                <input id="videoAdvancedParams" autocomplete="off" spellcheck="false" placeholder="motion_bucket, motion_scale, or provider-specific params" />
              </label>
            </div>
            <div class="provider-grid" hidden>
              <label>RunningHub Video Endpoint
                <input id="videoWorkflowEndpoint" autocomplete="off" spellcheck="false" placeholder="/run/ai-app/2066043648160133122" />
              </label>
              <label>RunningHub Video nodeInfoList JSON
                <textarea id="videoNodeInfoList" spellcheck="false" placeholder='[]; use {{prompt}} inside JSON strings to inject the generated video prompt'></textarea>
              </label>
              <label>RunningHub Video Poll Timeout
                <select id="videoPollTimeout">
                  <option value="900">15 min</option>
                  <option value="1800" selected>30 min</option>
                  <option value="3600">60 min</option>
                  <option value="7200">120 min</option>
                </select>
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
        <div class="row run-actions">
          <button class="primary" id="runBtn">运行工作流</button>
          <button id="sampleBtn">填入示例</button>
          <button id="gameSampleBtn">游戏示例</button>
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

      <div class="panel form view" data-view="staff" hidden>
        <div class="manager-toolbar">
          <div class="manager-title">
            <strong>数字员工管理</strong>
            <span id="staffStatus" class="status">管理 my_custom_staff</span>
          </div>
          <div class="manager-actions">
            <button id="refreshStaffBtn">刷新员工</button>
            <button id="newStaffBtn">新建员工</button>
            <button class="danger" id="deleteStaffBtn" disabled>删除员工</button>
          </div>
        </div>
        <div class="staff-manager">
          <div class="staff-sidebar">
            <label>员工搜索
              <input id="staffFilter" autocomplete="off" spellcheck="false" placeholder="按名称、编号或角色筛选" />
            </label>
            <div class="staff-list" id="staffList"></div>
          </div>
          <div class="staff-editor">
            <label>员工文件夹名
              <input id="staffName" autocomplete="off" spellcheck="false" placeholder="例如 20_销售话术专员" />
            </label>
            <label>agent.md
              <textarea id="staffAgentMd" spellcheck="false" placeholder="选择一个员工后查看或编辑 agent.md"></textarea>
            </label>
            <label>flow_rule.json
              <textarea id="staffFlowRule" spellcheck="false" placeholder="选择一个员工后查看或编辑 flow_rule.json"></textarea>
            </label>
            <div class="row">
              <button class="primary" id="saveStaffBtn">保存员工</button>
              <span class="muted small">保存后会直接写入 my_custom_staff；flow_rule.json 必须是合法 JSON。</span>
            </div>
          </div>
        </div>
      </div>

      <div class="panel form view" data-view="workflow" hidden>
        <div class="row">
          <strong>工作流编辑器</strong>
          <button id="refreshWorkflowsBtn" type="button">刷新工作流</button>
          <button id="newWorkflowBtn" type="button">新建工作流</button>
          <button id="addWorkflowStepBtn" type="button">新增步骤</button>
          <button class="danger" id="deleteWorkflowBtn" type="button" disabled>删除工作流</button>
          <span id="workflowEditorStatus" class="status">管理 my_workflows</span>
        </div>
        <div class="staff-manager">
          <div class="staff-list" id="workflowList"></div>
          <div class="staff-editor">
            <div class="provider-grid">
              <label>工作流文件名
                <input id="workflowFile" autocomplete="off" spellcheck="false" placeholder="例如 workflow_短视频全流程" />
              </label>
              <label>工作流名称
                <input id="workflowName" autocomplete="off" spellcheck="false" placeholder="例如 短视频全流程" />
              </label>
              <label>说明
                <input id="workflowDescription" autocomplete="off" spellcheck="false" placeholder="这个工作流用于什么场景" />
              </label>
            </div>
            <div class="row">
              <strong>执行步骤</strong>
              <span class="muted small">每一步选择一个数字员工，填写它要完成的任务和输出物。</span>
            </div>
            <div class="reference-list" id="workflowSteps"></div>
            <div class="row">
              <button class="primary" id="saveWorkflowBtn" type="button">保存工作流</button>
              <span class="muted small">保存后写入 my_workflows/*.json，并同步到“运行工作流”的下拉列表。</span>
            </div>
          </div>
        </div>
      </div>

      <div class="panel viewer view" data-view="output" hidden>
        <div class="viewer-head">
          <div>
            <strong id="viewerTitle">未选择任务</strong>
            <div class="muted small" id="viewerMeta">运行后会在这里查看输出文件</div>
          </div>
          <div class="row">
            <button id="saveFileBtn" type="button" disabled>保存当前文件</button>
            <button id="rebuildFinalBtn" type="button" disabled>重建最终汇总</button>
            <button id="rerunStepBtn" type="button" disabled>重跑当前步骤</button>
            <button id="exportTaskBtn" type="button" disabled>导出产品包</button>
          </div>
          <div class="file-tabs" id="fileTabs"></div>
        </div>
        <div class="output-dashboard" id="outputDashboard">
          <div class="output-summary-grid" id="outputSummaryGrid"></div>
          <div class="output-sections">
            <div class="output-section">
              <div class="output-section-head">
                <strong>步骤输出</strong>
                <span class="muted small" id="stepOutputMeta">0 个步骤</span>
              </div>
              <div class="output-link-list" id="stepOutputList">
                <div class="muted small">选择任务后显示每个员工的输出。</div>
              </div>
            </div>
            <div class="output-section">
              <div class="output-section-head">
                <strong>产品包文件</strong>
                <span class="muted small" id="packageOutputMeta">未生成</span>
              </div>
              <div class="output-link-list" id="packageOutputList">
                <div class="muted small">点击“导出产品包”后显示可交付文件。</div>
              </div>
            </div>
          </div>
        </div>
        <textarea class="file-editor" id="fileContent" spellcheck="false">选择左侧任务，或运行一个新任务。</textarea>
      </div>

      <div class="panel form view" data-view="system" hidden>
        <div class="row">
          <strong>系统状态</strong>
          <button id="refreshHealthBtn" type="button">刷新状态</button>
          <span id="healthStatus" class="status">检查本地运行环境</span>
        </div>
        <div class="health-grid" id="healthGrid"></div>
        <details open>
          <summary><strong>首次启动向导</strong> <span class="muted small">面向一站式本地部署</span></summary>
          <div class="details-body">
            <div class="reference-list">
              <div class="reference-item">
                <div class="reference-info">
                  <div class="reference-name">1. 一键启动</div>
                  <div class="muted small">Windows 用户可双击项目根目录的 start_local.bat；它会启动 Ollama、启动管理台并打开浏览器。</div>
                </div>
              </div>
              <div class="reference-item">
                <div class="reference-info">
                  <div class="reference-name">2. 选择本地模型</div>
                  <div class="muted small">点击“运行工作流 -> 模型接口配置 -> 一键本地离线模式”，系统会自动填好 Ollama、local、Base URL 和 qwen3:8b-q4_K_M。</div>
                </div>
              </div>
              <div class="reference-item">
                <div class="reference-info">
                  <div class="reference-name">3. 测试模型接口</div>
                  <div class="muted small">点击“测试当前模型接口”。通过后再运行工作流；如果失败，先看系统状态里的 Ollama 和模型服务提示。</div>
                </div>
              </div>
              <div class="reference-item">
                <div class="reference-info">
                  <div class="reference-name">4. 上传知识库</div>
                  <div class="muted small">把公司资料、产品说明、话术 SOP 上传到 my_knowledge_base，运行时选择“追加 my_knowledge_base”。</div>
                </div>
              </div>
              <div class="reference-item">
                <div class="reference-info">
                  <div class="reference-name">5. 运行示例工作流</div>
                  <div class="muted small">先用 offline 检查流程，再切到 openai/本地模型执行。输出统一写入 my_task_output。</div>
                </div>
              </div>
            </div>
          </div>
        </details>
      </div>
    </section>
  </main>

  <script>
    const els = {
      env: document.getElementById('env'),
      productTemplate: document.getElementById('productTemplate'),
      workflow: document.getElementById('workflow'),
      provider: document.getElementById('provider'),
      model: document.getElementById('model'),
      customModel: document.getElementById('customModel'),
      taskTitle: document.getElementById('taskTitle'),
      apiKey: document.getElementById('apiKey'),
      baseUrl: document.getElementById('baseUrl'),
      modelTimeout: document.getElementById('modelTimeout'),
      localModelPreset: document.getElementById('localModelPreset'),
      localModelName: document.getElementById('localModelName'),
      localOfflineBtn: document.getElementById('localOfflineBtn'),
      testModelBtn: document.getElementById('testModelBtn'),
      userInput: document.getElementById('userInput'),
      useMemory: document.getElementById('useMemory'),
      inheritTask: document.getElementById('inheritTask'),
      inheritMode: document.getElementById('inheritMode'),
      useKnowledge: document.getElementById('useKnowledge'),
      knowledgeFile: document.getElementById('knowledgeFile'),
      uploadKnowledgeBtn: document.getElementById('uploadKnowledgeBtn'),
      knowledgeList: document.getElementById('knowledgeList'),
      autoProductionMode: document.getElementById('autoProductionMode'),
      composeTool: document.getElementById('composeTool'),
      finalVideoName: document.getElementById('finalVideoName'),
      comfyApiKey: document.getElementById('comfyApiKey'),
      comfyBaseUrl: document.getElementById('comfyBaseUrl'),
      comfyWorkflowEndpoint: document.getElementById('comfyWorkflowEndpoint'),
      comfyNodeInfoList: document.getElementById('comfyNodeInfoList'),
      comfyApiWorkflowFile: document.getElementById('comfyApiWorkflowFile'),
      comfyParameterMapper: document.getElementById('comfyParameterMapper'),
      comfyPollTimeout: document.getElementById('comfyPollTimeout'),
      voiceMode: document.getElementById('voiceMode'),
      voiceReferenceFile: document.getElementById('voiceReferenceFile'),
      voiceReferenceAudioPath: document.getElementById('voiceReferenceAudioPath'),
      voiceReferenceText: document.getElementById('voiceReferenceText'),
      voiceCommandTemplate: document.getElementById('voiceCommandTemplate'),
      voiceTimeout: document.getElementById('voiceTimeout'),
      imageTool: document.getElementById('imageTool'),
      imagePositivePrompt: document.getElementById('imagePositivePrompt'),
      imageModel: document.getElementById('imageModel'),
      imageSize: document.getElementById('imageSize'),
      imageCount: document.getElementById('imageCount'),
      imageStyle: document.getElementById('imageStyle'),
      imageQuality: document.getElementById('imageQuality'),
      imageApiKey: document.getElementById('imageApiKey'),
      imageBaseUrl: document.getElementById('imageBaseUrl'),
      imageWorkflowEndpoint: document.getElementById('imageWorkflowEndpoint'),
      imageInstanceType: document.getElementById('imageInstanceType'),
      imageNodeInfoList: document.getElementById('imageNodeInfoList'),
      imagePollTimeout: document.getElementById('imagePollTimeout'),
      imageNegativePrompt: document.getElementById('imageNegativePrompt'),
      imageConsistency: document.getElementById('imageConsistency'),
      imageSeed: document.getElementById('imageSeed'),
      imageGuidance: document.getElementById('imageGuidance'),
      imageSteps: document.getElementById('imageSteps'),
      imageDenoise: document.getElementById('imageDenoise'),
      imageSampler: document.getElementById('imageSampler'),
      imageControl: document.getElementById('imageControl'),
      videoTool: document.getElementById('videoTool'),
      videoPositivePrompt: document.getElementById('videoPositivePrompt'),
      videoModel: document.getElementById('videoModel'),
      videoAspect: document.getElementById('videoAspect'),
      videoDuration: document.getElementById('videoDuration'),
      videoStyle: document.getElementById('videoStyle'),
      videoPromptNotes: document.getElementById('videoPromptNotes'),
      videoApiKey: document.getElementById('videoApiKey'),
      videoBaseUrl: document.getElementById('videoBaseUrl'),
      videoWorkflowEndpoint: document.getElementById('videoWorkflowEndpoint'),
      videoNodeInfoList: document.getElementById('videoNodeInfoList'),
      videoPollTimeout: document.getElementById('videoPollTimeout'),
      videoNegativePrompt: document.getElementById('videoNegativePrompt'),
      videoSeed: document.getElementById('videoSeed'),
      videoFps: document.getElementById('videoFps'),
      videoMotionStrength: document.getElementById('videoMotionStrength'),
      videoCameraMotion: document.getElementById('videoCameraMotion'),
      videoResolution: document.getElementById('videoResolution'),
      videoGuidance: document.getElementById('videoGuidance'),
      videoFrames: document.getElementById('videoFrames'),
      videoImageStrength: document.getElementById('videoImageStrength'),
      videoCameraPath: document.getElementById('videoCameraPath'),
      videoAudioNotes: document.getElementById('videoAudioNotes'),
      videoAdvancedParams: document.getElementById('videoAdvancedParams'),
      referenceImages: document.getElementById('referenceImages'),
      referenceRole: document.getElementById('referenceRole'),
      referenceNote: document.getElementById('referenceNote'),
      referenceList: document.getElementById('referenceList'),
      runBtn: document.getElementById('runBtn'),
      sampleBtn: document.getElementById('sampleBtn'),
      gameSampleBtn: document.getElementById('gameSampleBtn'),
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
      outputSummaryGrid: document.getElementById('outputSummaryGrid'),
      stepOutputMeta: document.getElementById('stepOutputMeta'),
      stepOutputList: document.getElementById('stepOutputList'),
      packageOutputMeta: document.getElementById('packageOutputMeta'),
      packageOutputList: document.getElementById('packageOutputList'),
      saveFileBtn: document.getElementById('saveFileBtn'),
      rebuildFinalBtn: document.getElementById('rebuildFinalBtn'),
      rerunStepBtn: document.getElementById('rerunStepBtn'),
      exportTaskBtn: document.getElementById('exportTaskBtn'),
      refreshStaffBtn: document.getElementById('refreshStaffBtn'),
      newStaffBtn: document.getElementById('newStaffBtn'),
      deleteStaffBtn: document.getElementById('deleteStaffBtn'),
      saveStaffBtn: document.getElementById('saveStaffBtn'),
      staffStatus: document.getElementById('staffStatus'),
      staffFilter: document.getElementById('staffFilter'),
      staffList: document.getElementById('staffList'),
      staffName: document.getElementById('staffName'),
      staffAgentMd: document.getElementById('staffAgentMd'),
      staffFlowRule: document.getElementById('staffFlowRule'),
      refreshWorkflowsBtn: document.getElementById('refreshWorkflowsBtn'),
      newWorkflowBtn: document.getElementById('newWorkflowBtn'),
      addWorkflowStepBtn: document.getElementById('addWorkflowStepBtn'),
      deleteWorkflowBtn: document.getElementById('deleteWorkflowBtn'),
      saveWorkflowBtn: document.getElementById('saveWorkflowBtn'),
      workflowEditorStatus: document.getElementById('workflowEditorStatus'),
      workflowList: document.getElementById('workflowList'),
      workflowFile: document.getElementById('workflowFile'),
      workflowName: document.getElementById('workflowName'),
      workflowDescription: document.getElementById('workflowDescription'),
      workflowSteps: document.getElementById('workflowSteps'),
      taskSidebar: document.getElementById('taskSidebar'),
      refreshHealthBtn: document.getElementById('refreshHealthBtn'),
      healthStatus: document.getElementById('healthStatus'),
      healthGrid: document.getElementById('healthGrid'),
    };
    const navButtons = Array.from(document.querySelectorAll('[data-view-target]'));
    const views = Array.from(document.querySelectorAll('[data-view]'));
    let selectedTask = null;
    let selectedFile = null;
    let selectedTaskSummary = {};
    let selectedStaff = null;
    let selectedWorkflow = null;
    let workflowEditorSteps = [];
    let workflowEditorBase = {};
    let staffOptions = [];
    let selectedReferenceFiles = [];
    let referencePreviewUrls = new Map();
    let comfyParameterCandidates = [];
    let progressTimer = null;
    let localModelPresets = [];
    const DEFAULT_LOCAL_MODEL = 'qwen3:8b-q4_K_M';
    const OLLAMA_BASE_URL = 'http://127.0.0.1:11434/v1';
    const SETTINGS_KEY = 'my_workspace.workflow_settings.v1';
    const PRODUCT_TEMPLATES = {
      short_video: {
        workflow: 'workflow_短视频全流程',
        taskTitle: '短视频内容生产',
        sample: '我要做一条抖音短视频，推广 AI 自动化开发服务。目标客户是中小企业老板，他们想降本增效但不知道怎么落地。视频目标是让客户私信咨询，风格专业、直接、有案例感，不要夸大承诺。',
        autoProductionMode: 'package_only',
        imageSize: '9:16',
        videoAspect: '9:16',
        videoDuration: '30s',
      },
      xiaohongshu: {
        workflow: 'workflow_小红书图文',
        taskTitle: '小红书图文内容',
        sample: '我要做一篇小红书图文笔记，主题是 AI 自动化如何帮小团队节省重复工作。目标读者是创业者、自由职业者和中小企业老板，风格真实、具体、可收藏。',
        autoProductionMode: 'off',
        imageSize: '4:5',
        videoAspect: '4:5',
        videoDuration: 'custom',
      },
      game_steam: {
        workflow: 'workflow_Unity3D游戏Steam上架',
        taskTitle: 'Unity3D探索解谜Steam游戏立项',
        sample: '我想做一款 Unity 3D 第三人称探索解谜游戏，上架 Steam。目标玩家是喜欢低多边形、轻剧情、环境谜题和短流程独立游戏的玩家。团队规模按单人或两人小团队考虑，优先做 20-30 分钟可玩 Demo，用于 Steam 商店页、愿望单和后续众筹/抢先体验验证。',
        autoProductionMode: 'off',
        imageSize: '16:9',
        videoAspect: '16:9',
        videoDuration: '30s',
      },
      software_market: {
        workflow: 'workflow_软件市场机会分析',
        taskTitle: '软件市场机会分析',
        sample: '目标中国中小企业和个人开发者市场，团队1-2人，擅长 Python、Web 和 AI API，希望找可 MVP 验证的软件方向。请从痛点强度、付费意愿、获客难度、交付复杂度和差异化角度筛选机会。',
        autoProductionMode: 'off',
        imageSize: '16:9',
        videoAspect: '16:9',
        videoDuration: 'custom',
      },
      agent_platform: {
        workflow: 'workflow_AI员工工作流平台设计',
        taskTitle: 'AI员工工作流平台设计',
        sample: '我要做中小企业 AI 员工工作流平台，以 my_custom_staff 里的自定义员工为核心，能管理数字员工、运行工作流、查看任务输出，先自用跑通再销售。',
        autoProductionMode: 'off',
        imageSize: '16:9',
        videoAspect: '16:9',
        videoDuration: 'custom',
      },
    };

    function setStatus(text, isError = false) {
      els.status.textContent = text;
      els.status.classList.toggle('error', isError);
    }

    function showView(viewName) {
      document.body.dataset.view = viewName;
      for (const view of views) {
        view.hidden = view.dataset.view !== viewName;
      }
      for (const btn of navButtons) {
        btn.classList.toggle('active', btn.dataset.viewTarget === viewName);
      }
      els.taskSidebar.hidden = viewName !== 'output';
      if (viewName === 'output') {
        loadTasks().catch(err => setStatus(err.message, true));
      }
      if (viewName === 'system') {
        loadSystemHealth().catch(err => setHealthStatus(err.message, true));
      }
      if (viewName === 'workflow') {
        loadWorkflowList().catch(err => setWorkflowEditorStatus(err.message, true));
      }
    }

    function setHealthStatus(text, isError = false) {
      els.healthStatus.textContent = text;
      els.healthStatus.classList.toggle('error', isError);
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
        if (job.task_name) {
          showView('output');
          await selectTask(job.task_name);
        }
        els.runBtn.disabled = false;
        progressTimer = null;
        return;
      }
      if (job.status === 'failed') {
        setStatus(job.error || '工作流运行失败', true);
        await loadTasks();
        if (job.task_name) {
          showView('output');
          await selectTask(job.task_name);
        }
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
      localModelPresets = data.local_model_presets || [];
      staffOptions = data.staff || [];
      els.env.textContent = data.openai_configured ? 'OpenAI 已配置' : 'OpenAI 未配置，默认离线模式';
      els.workflow.innerHTML = data.workflows.map(w => `<option value="${w.stem}">${w.name}</option>`).join('');
      renderLocalModelPresets();
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
        productTemplate: els.productTemplate.value,
        workflow: els.workflow.value,
        provider: els.provider.value,
        model: els.model.value,
        customModel: els.customModel.value,
        apiKey: els.apiKey.value,
        baseUrl: els.baseUrl.value,
        modelTimeout: els.modelTimeout.value,
        localModelPreset: els.localModelPreset.value,
        localModelName: els.localModelName.value,
        useMemory: els.useMemory.value,
        inheritTask: els.inheritTask.value,
        inheritMode: els.inheritMode.value,
        useKnowledge: els.useKnowledge.value,
        autoProductionMode: els.autoProductionMode.value,
        composeTool: els.composeTool.value,
        finalVideoName: els.finalVideoName.value,
        comfyApiKey: els.comfyApiKey.value,
        comfyBaseUrl: els.comfyBaseUrl.value,
        comfyWorkflowEndpoint: els.comfyWorkflowEndpoint.value,
        comfyNodeInfoList: els.comfyNodeInfoList.value,
        comfyPollTimeout: els.comfyPollTimeout.value,
        voiceMode: els.voiceMode.value,
        voiceReferenceAudioPath: els.voiceReferenceAudioPath.value,
        voiceReferenceText: els.voiceReferenceText.value,
        voiceCommandTemplate: els.voiceCommandTemplate.value,
        voiceTimeout: els.voiceTimeout.value,
        imageTool: els.imageTool.value,
        imagePositivePrompt: els.imagePositivePrompt.value,
        imageModel: els.imageModel.value,
        imageSize: els.imageSize.value,
        imageCount: els.imageCount.value,
        imageStyle: els.imageStyle.value,
        imageQuality: els.imageQuality.value,
        imageApiKey: els.imageApiKey.value,
        imageBaseUrl: els.imageBaseUrl.value,
        imageWorkflowEndpoint: els.imageWorkflowEndpoint.value,
        imageInstanceType: els.imageInstanceType.value,
        imageNodeInfoList: els.imageNodeInfoList.value,
        imagePollTimeout: els.imagePollTimeout.value,
        imageNegativePrompt: els.imageNegativePrompt.value,
        imageConsistency: els.imageConsistency.value,
        imageSeed: els.imageSeed.value,
        imageGuidance: els.imageGuidance.value,
        imageSteps: els.imageSteps.value,
        imageDenoise: els.imageDenoise.value,
        imageSampler: els.imageSampler.value,
        imageControl: els.imageControl.value,
        videoTool: els.videoTool.value,
        videoPositivePrompt: els.videoPositivePrompt.value,
        videoModel: els.videoModel.value,
        videoAspect: els.videoAspect.value,
        videoDuration: els.videoDuration.value,
        videoStyle: els.videoStyle.value,
        videoPromptNotes: els.videoPromptNotes.value,
        videoApiKey: els.videoApiKey.value,
        videoBaseUrl: els.videoBaseUrl.value,
        videoWorkflowEndpoint: els.videoWorkflowEndpoint.value,
        videoNodeInfoList: els.videoNodeInfoList.value,
        videoPollTimeout: els.videoPollTimeout.value,
        videoNegativePrompt: els.videoNegativePrompt.value,
        videoSeed: els.videoSeed.value,
        videoFps: els.videoFps.value,
        videoMotionStrength: els.videoMotionStrength.value,
        videoCameraMotion: els.videoCameraMotion.value,
        videoResolution: els.videoResolution.value,
        videoGuidance: els.videoGuidance.value,
        videoFrames: els.videoFrames.value,
        videoImageStrength: els.videoImageStrength.value,
        videoCameraPath: els.videoCameraPath.value,
        videoAudioNotes: els.videoAudioNotes.value,
        videoAdvancedParams: els.videoAdvancedParams.value,
        referenceRole: els.referenceRole.value,
        referenceNote: els.referenceNote.value,
      };
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    }

    function restoreSettings() {
      const settings = readSettings();
      setIfExists(els.productTemplate, settings.productTemplate);
      setIfExists(els.workflow, settings.workflow);
      setIfExists(els.provider, settings.provider);
      setIfExists(els.model, settings.model);
      els.customModel.value = settings.customModel || '';
      els.taskTitle.value = '';
      els.apiKey.value = settings.apiKey || '';
      els.baseUrl.value = settings.baseUrl || '';
      setIfExists(els.modelTimeout, settings.modelTimeout);
      setIfExists(els.localModelPreset, settings.localModelPreset);
      renderLocalModelNames();
      setIfExists(els.localModelName, settings.localModelName);
      setIfExists(els.useMemory, settings.useMemory === 'on' ? 'video_output' : settings.useMemory);
      setIfExists(els.inheritTask, settings.inheritTask);
      setIfExists(els.inheritMode, settings.inheritMode);
      setIfExists(els.useKnowledge, settings.useKnowledge);
      setIfExists(els.autoProductionMode, settings.autoProductionMode);
      setIfExists(els.composeTool, settings.composeTool);
      els.finalVideoName.value = settings.finalVideoName || '';
      els.comfyApiKey.value = settings.comfyApiKey || '';
      els.comfyBaseUrl.value = settings.comfyBaseUrl || '';
      els.comfyWorkflowEndpoint.value = settings.comfyWorkflowEndpoint || '';
      els.comfyNodeInfoList.value = settings.comfyNodeInfoList || '';
      setIfExists(els.comfyPollTimeout, settings.comfyPollTimeout);
      setIfExists(els.voiceMode, settings.voiceMode);
      els.voiceReferenceAudioPath.value = settings.voiceReferenceAudioPath || '';
      els.voiceReferenceText.value = settings.voiceReferenceText || '';
      els.voiceCommandTemplate.value = settings.voiceCommandTemplate || '';
      setIfExists(els.voiceTimeout, settings.voiceTimeout);
      setIfExists(els.imageTool, settings.imageTool);
      els.imagePositivePrompt.value = settings.imagePositivePrompt || '';
      els.imageModel.value = settings.imageModel || '';
      setIfExists(els.imageSize, settings.imageSize);
      setIfExists(els.imageCount, settings.imageCount);
      els.imageStyle.value = settings.imageStyle || '';
      setIfExists(els.imageQuality, settings.imageQuality);
      els.imageApiKey.value = settings.imageApiKey || '';
      els.imageBaseUrl.value = settings.imageBaseUrl || '';
      els.imageWorkflowEndpoint.value = settings.imageWorkflowEndpoint || '';
      setIfExists(els.imageInstanceType, settings.imageInstanceType);
      els.imageNodeInfoList.value = settings.imageNodeInfoList || '';
      setIfExists(els.imagePollTimeout, settings.imagePollTimeout);
      els.imageNegativePrompt.value = settings.imageNegativePrompt || '';
      els.imageConsistency.value = settings.imageConsistency || '';
      els.imageSeed.value = settings.imageSeed || '';
      els.imageGuidance.value = settings.imageGuidance || '';
      els.imageSteps.value = settings.imageSteps || '';
      els.imageDenoise.value = settings.imageDenoise || '';
      els.imageSampler.value = settings.imageSampler || '';
      els.imageControl.value = settings.imageControl || '';
      setIfExists(els.videoTool, settings.videoTool);
      els.videoPositivePrompt.value = settings.videoPositivePrompt || '';
      els.videoModel.value = settings.videoModel || '';
      setIfExists(els.videoAspect, settings.videoAspect);
      setIfExists(els.videoDuration, settings.videoDuration);
      els.videoStyle.value = settings.videoStyle || '';
      els.videoPromptNotes.value = settings.videoPromptNotes || '';
      els.videoApiKey.value = settings.videoApiKey || '';
      els.videoBaseUrl.value = settings.videoBaseUrl || '';
      els.videoWorkflowEndpoint.value = settings.videoWorkflowEndpoint || '';
      els.videoNodeInfoList.value = settings.videoNodeInfoList || '';
      setIfExists(els.videoPollTimeout, settings.videoPollTimeout);
      els.videoNegativePrompt.value = settings.videoNegativePrompt || '';
      els.videoSeed.value = settings.videoSeed || '';
      setIfExists(els.videoFps, settings.videoFps);
      setIfExists(els.videoMotionStrength, settings.videoMotionStrength);
      setIfExists(els.videoCameraMotion, settings.videoCameraMotion);
      setIfExists(els.videoResolution, settings.videoResolution);
      els.videoGuidance.value = settings.videoGuidance || '';
      els.videoFrames.value = settings.videoFrames || '';
      els.videoImageStrength.value = settings.videoImageStrength || '';
      els.videoCameraPath.value = settings.videoCameraPath || '';
      els.videoAudioNotes.value = settings.videoAudioNotes || '';
      els.videoAdvancedParams.value = settings.videoAdvancedParams || '';
      setIfExists(els.referenceRole, settings.referenceRole);
      els.referenceNote.value = settings.referenceNote || '';
      syncCustomModelState(false);
      applyImageProviderDefaults();
      applyVideoProviderDefaults();
      applyComfyProviderDefaults();
      renderComfyParameterMapper();
    }

    function setIfExists(control, value) {
      if (!value) return;
      const values = Array.from(control.options || []).map(option => option.value);
      if (!values.length || values.includes(value)) control.value = value;
    }

    function bindSettingsPersistence() {
      [
        els.workflow,
        els.productTemplate,
        els.provider,
        els.model,
        els.customModel,
        els.taskTitle,
        els.apiKey,
        els.baseUrl,
        els.modelTimeout,
        els.localModelPreset,
        els.localModelName,
        els.useMemory,
        els.inheritTask,
        els.inheritMode,
        els.useKnowledge,
        els.autoProductionMode,
        els.composeTool,
        els.finalVideoName,
        els.comfyApiKey,
        els.comfyBaseUrl,
        els.comfyWorkflowEndpoint,
        els.comfyNodeInfoList,
        els.comfyPollTimeout,
        els.voiceMode,
        els.voiceReferenceAudioPath,
        els.voiceReferenceText,
        els.voiceCommandTemplate,
        els.voiceTimeout,
        els.imageTool,
        els.imagePositivePrompt,
        els.imageModel,
        els.imageSize,
        els.imageCount,
        els.imageStyle,
        els.imageQuality,
        els.imageApiKey,
        els.imageBaseUrl,
        els.imageWorkflowEndpoint,
        els.imageInstanceType,
        els.imageNodeInfoList,
        els.imagePollTimeout,
        els.imageNegativePrompt,
        els.imageConsistency,
        els.imageSeed,
        els.imageGuidance,
        els.imageSteps,
        els.imageDenoise,
        els.imageSampler,
        els.imageControl,
        els.videoTool,
        els.videoPositivePrompt,
        els.videoModel,
        els.videoAspect,
        els.videoDuration,
        els.videoStyle,
        els.videoPromptNotes,
        els.videoApiKey,
        els.videoBaseUrl,
        els.videoWorkflowEndpoint,
        els.videoNodeInfoList,
        els.videoPollTimeout,
        els.videoNegativePrompt,
        els.videoSeed,
        els.videoFps,
        els.videoMotionStrength,
        els.videoCameraMotion,
        els.videoResolution,
        els.videoGuidance,
        els.videoFrames,
        els.videoImageStrength,
        els.videoCameraPath,
        els.videoAudioNotes,
        els.videoAdvancedParams,
        els.referenceRole,
        els.referenceNote,
      ].forEach(control => {
        control.addEventListener('change', saveSettings);
        control.addEventListener('input', saveSettings);
      });
    }

    function applyImageProviderDefaults() {
      if (els.imageTool.value !== 'runninghub') return;
      if (!els.imageBaseUrl.value.trim()) {
        els.imageBaseUrl.value = 'https://www.runninghub.cn/openapi/v2';
      }
      if (!els.imageWorkflowEndpoint.value.trim()) {
        els.imageWorkflowEndpoint.value = '/run/workflow/2048294089858228226';
      }
      if (!els.imageNodeInfoList.value.trim()) {
        els.imageNodeInfoList.value = '[]';
      }
      saveSettings();
    }

    function applyVideoProviderDefaults() {
      if (els.videoTool.value !== 'runninghub') return;
      if (!els.videoBaseUrl.value.trim()) {
        els.videoBaseUrl.value = 'https://www.runninghub.cn/openapi/v2';
      }
      if (!els.videoWorkflowEndpoint.value.trim()) {
        els.videoWorkflowEndpoint.value = '/run/ai-app/2066043648160133122';
      }
      if (!els.videoNodeInfoList.value.trim()) {
        els.videoNodeInfoList.value = '[]';
      }
      saveSettings();
    }

    function applyComfyProviderDefaults() {
      if (els.composeTool.value !== 'runninghub' && els.autoProductionMode.value !== 'comfy_full') return;
      if (!els.comfyBaseUrl.value.trim()) {
        els.comfyBaseUrl.value = 'https://www.runninghub.cn/openapi/v2';
      }
      if (!els.comfyNodeInfoList.value.trim()) {
        els.comfyNodeInfoList.value = '[]';
      }
      saveSettings();
    }

    function isComfyConnection(value) {
      return Array.isArray(value) && value.length === 2 && (typeof value[0] === 'string' || typeof value[0] === 'number') && typeof value[1] === 'number';
    }

    function isMappableComfyValue(value) {
      return value === null || ['string', 'number', 'boolean'].includes(typeof value);
    }

    function guessComfySource(candidate, textIndex) {
      const type = candidate.classType.toLowerCase();
      const field = candidate.fieldName.toLowerCase();
      const value = String(candidate.value ?? '').trim();
      if (type.includes('cliptextencode') && field === 'text') {
        return value ? '{{prompt}}' : '{{negative_prompt}}';
      }
      if (type.includes('loadimage') && field === 'image') return '{{reference_image}}';
      if (field.includes('prompt') && field.includes('negative')) return '{{negative_prompt}}';
      if (field.includes('prompt')) return '{{prompt}}';
      return 'fixed';
    }

    function comfySourceLabel(value) {
      const labels = {
        fixed: '固定值',
        '{{prompt}}': '主提示词',
        '{{negative_prompt}}': '负向提示词',
        '{{image_prompt}}': '生图提示词',
        '{{video_prompt}}': '视频提示词',
        '{{reference_image}}': '参考图文件名/URL',
        '{{voice_text}}': '配音文本',
        '{{subtitle_srt}}': '字幕 SRT',
        '{{payload}}': '完整参数包',
      };
      return labels[value] || value;
    }

    function parseComfyManualValue(raw, original) {
      if (typeof original === 'number') {
        const parsed = Number(raw);
        return Number.isFinite(parsed) ? parsed : original;
      }
      if (typeof original === 'boolean') {
        return String(raw).trim().toLowerCase() === 'true';
      }
      if (raw === 'null') return null;
      return raw;
    }

    function extractComfyApiCandidates(data) {
      if (!data || typeof data !== 'object' || Array.isArray(data)) {
        throw new Error('JSON 顶层必须是对象');
      }
      const entries = Object.entries(data);
      const apiLike = entries.length > 0 && entries.every(([nodeId, node]) => /^\d+$/.test(String(nodeId)) && node && typeof node === 'object' && node.class_type);
      if (!apiLike) {
        throw new Error('这不是 API 格式 JSON。请在 ComfyUI 里导出 API 格式，而不是普通画布工作流 JSON。');
      }
      const candidates = [];
      let textIndex = 0;
      for (const [nodeId, node] of entries) {
        const inputs = node.inputs || {};
        for (const [fieldName, value] of Object.entries(inputs)) {
          if (isComfyConnection(value) || !isMappableComfyValue(value)) continue;
          const candidate = {
            id: `${nodeId}.${fieldName}`,
            nodeId: String(nodeId),
            classType: String(node.class_type || ''),
            fieldName,
            value,
            source: 'fixed',
            enabled: false,
          };
          candidate.source = guessComfySource(candidate, textIndex);
          candidate.enabled = candidate.source !== 'fixed' || ['width', 'height', 'seed', 'steps', 'cfg', 'denoise', 'batch_size'].includes(fieldName);
          if (candidate.classType.toLowerCase().includes('cliptextencode') && fieldName === 'text') textIndex += 1;
          candidates.push(candidate);
        }
      }
      const priority = ['text', 'image', 'width', 'height', 'batch_size', 'seed', 'steps', 'cfg', 'denoise'];
      candidates.sort((a, b) => {
        const ai = priority.indexOf(a.fieldName);
        const bi = priority.indexOf(b.fieldName);
        return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi) || Number(a.nodeId) - Number(b.nodeId);
      });
      return candidates;
    }

    function renderComfyParameterMapper() {
      if (!comfyParameterCandidates.length) {
        els.comfyParameterMapper.innerHTML = '<div class="muted small">导入 ComfyUI API JSON 后，这里会显示可传参节点。</div>';
        return;
      }
      els.comfyParameterMapper.innerHTML = '';
      const head = document.createElement('div');
      head.className = 'muted small';
      head.textContent = `已识别 ${comfyParameterCandidates.length} 个可传参字段。勾选要传给 RunningHub 的参数，系统会自动生成 nodeInfoList。`;
      els.comfyParameterMapper.appendChild(head);
      const sourceOptions = ['fixed', '{{prompt}}', '{{negative_prompt}}', '{{image_prompt}}', '{{video_prompt}}', '{{reference_image}}', '{{voice_text}}', '{{subtitle_srt}}', '{{payload}}'];
      comfyParameterCandidates.forEach((candidate, index) => {
        const item = document.createElement('div');
        item.className = 'reference-item';
        const left = document.createElement('label');
        left.style.display = 'grid';
        left.style.gap = '4px';
        left.style.flex = '1';
        const line = document.createElement('span');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = candidate.enabled;
        checkbox.onchange = () => {
          candidate.enabled = checkbox.checked;
          updateComfyNodeInfoFromCandidates();
        };
        line.appendChild(checkbox);
        line.append(` #${candidate.nodeId} ${candidate.classType}.${candidate.fieldName}`);
        const meta = document.createElement('span');
        meta.className = 'muted small';
        meta.textContent = `当前值：${String(candidate.value ?? '').slice(0, 80)}`;
        left.appendChild(line);
        left.appendChild(meta);

        const select = document.createElement('select');
        sourceOptions.forEach(value => {
          const option = document.createElement('option');
          option.value = value;
          option.textContent = comfySourceLabel(value);
          select.appendChild(option);
        });
        select.value = candidate.source;
        select.onchange = () => {
          candidate.source = select.value;
          updateComfyNodeInfoFromCandidates();
        };

        const input = document.createElement('input');
        input.value = String(candidate.value ?? '');
        input.placeholder = '固定值';
        input.oninput = () => {
          candidate.value = parseComfyManualValue(input.value, candidate.value);
          updateComfyNodeInfoFromCandidates();
        };

        item.appendChild(left);
        item.appendChild(select);
        item.appendChild(input);
        els.comfyParameterMapper.appendChild(item);
      });
      updateComfyNodeInfoFromCandidates();
    }

    function updateComfyNodeInfoFromCandidates() {
      const nodeInfo = comfyParameterCandidates
        .filter(candidate => candidate.enabled)
        .map(candidate => ({
          nodeId: candidate.nodeId,
          fieldName: candidate.fieldName,
          fieldValue: candidate.source === 'fixed' ? candidate.value : candidate.source,
        }));
      els.comfyNodeInfoList.value = JSON.stringify(nodeInfo, null, 2);
      saveSettings();
    }

    async function analyzeComfyApiWorkflowFile() {
      const file = els.comfyApiWorkflowFile.files && els.comfyApiWorkflowFile.files[0];
      if (!file) return;
      try {
        const text = await file.text();
        const data = JSON.parse(text);
        comfyParameterCandidates = extractComfyApiCandidates(data);
        renderComfyParameterMapper();
        setStatus(`已识别 ComfyUI API JSON：${file.name}`);
      } catch (err) {
        comfyParameterCandidates = [];
        renderComfyParameterMapper();
        setStatus(err.message, true);
      }
    }

    function renderLocalModelPresets() {
      const current = els.localModelPreset.value;
      els.localModelPreset.innerHTML = '<option value="">不使用本地预设</option>';
      for (const preset of localModelPresets) {
        const option = document.createElement('option');
        option.value = preset.id;
        option.textContent = preset.name || preset.id;
        els.localModelPreset.appendChild(option);
      }
      setIfExists(els.localModelPreset, current);
      renderLocalModelNames();
    }

    function renderLocalModelNames() {
      const current = els.localModelName.value;
      const preset = localModelPresets.find(item => item.id === els.localModelPreset.value);
      els.localModelName.innerHTML = '';
      if (!preset) {
        els.localModelName.innerHTML = '<option value="">先选择本地模型服务</option>';
        return;
      }
      const models = preset.models || [];
      for (const modelName of models) {
        const option = document.createElement('option');
        option.value = modelName;
        option.textContent = modelName;
        els.localModelName.appendChild(option);
      }
      if (!models.length) {
        els.localModelName.innerHTML = '<option value="">请手动输入模型名</option>';
      }
      setIfExists(els.localModelName, current);
    }

    function applyLocalModelPreset() {
      const preset = localModelPresets.find(item => item.id === els.localModelPreset.value);
      renderLocalModelNames();
      if (!preset) {
        saveSettings();
        return;
      }
      els.provider.value = 'openai';
      els.baseUrl.value = preset.base_url || '';
      els.apiKey.value = preset.api_key || 'local';
      els.model.value = 'custom';
      const modelName = els.localModelName.value || (preset.models || [])[0] || '';
      els.customModel.value = modelName;
      syncCustomModelState(false);
      saveSettings();
    }

    function applyLocalModelName() {
      if (els.localModelName.value) {
        els.model.value = 'custom';
        els.customModel.value = els.localModelName.value;
        syncCustomModelState(false);
      }
      saveSettings();
    }

    function applyLocalOfflineMode() {
      els.provider.value = 'openai';
      els.apiKey.value = 'local';
      els.baseUrl.value = OLLAMA_BASE_URL;
      els.modelTimeout.value = '900';
      setIfExists(els.localModelPreset, 'ollama');
      renderLocalModelNames();
      setIfExists(els.localModelName, DEFAULT_LOCAL_MODEL);
      els.model.value = 'custom';
      els.customModel.value = DEFAULT_LOCAL_MODEL;
      syncCustomModelState(false);
      saveSettings();
      setStatus(`已切换到本地离线模式：${DEFAULT_LOCAL_MODEL}`);
    }

    async function testModelConnection() {
      const model = els.model.value === 'custom' ? els.customModel.value.trim() : els.model.value;
      if (!model) {
        setStatus('请先选择或填写模型名', true);
        return;
      }
      setStatus('正在测试模型接口');
      try {
        const result = await api('/api/test-model', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            api_key: els.apiKey.value.trim(),
            base_url: els.baseUrl.value.trim(),
            model,
          }),
        });
        setStatus(`模型接口可用：${result.model}`);
      } catch (err) {
        setStatus(`模型接口不可用：${err.message}`, true);
      }
    }

    async function ensureLocalModelReady(model) {
      const isOllama = els.provider.value === 'openai' && els.baseUrl.value.trim().replace(/\/$/, '') === OLLAMA_BASE_URL;
      if (!isOllama) return;
      setStatus(`正在检测本地模型：${model}`);
      await api('/api/test-model', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          api_key: els.apiKey.value.trim() || 'local',
          base_url: OLLAMA_BASE_URL,
          model,
        }),
      });
    }

    async function loadKnowledgeList() {
      const data = await api('/api/knowledge');
      els.knowledgeList.innerHTML = '';
      if (!data.files.length) {
        els.knowledgeList.innerHTML = '<div class="muted small">my_knowledge_base 暂无知识文件</div>';
        return;
      }
      for (const file of data.files) {
        const item = document.createElement('div');
        item.className = 'reference-item';
        const info = document.createElement('div');
        info.className = 'reference-info';
        const name = document.createElement('div');
        name.className = 'reference-name';
        name.textContent = file.name;
        const meta = document.createElement('div');
        meta.className = 'muted small';
        meta.textContent = `${Math.max(1, Math.round(file.size / 1024))} KB · ${file.mtime}`;
        info.appendChild(name);
        info.appendChild(meta);
        item.appendChild(info);
        els.knowledgeList.appendChild(item);
      }
    }

    async function loadSystemHealth() {
      setHealthStatus('正在检查系统状态');
      const data = await api('/api/system-health');
      renderSystemHealth(data.checks || []);
      const errors = (data.checks || []).filter(item => item.status === 'error').length;
      const warns = (data.checks || []).filter(item => item.status === 'warn').length;
      if (errors) {
        setHealthStatus(`发现 ${errors} 项异常`, true);
      } else if (warns) {
        setHealthStatus(`发现 ${warns} 项提醒`);
      } else {
        setHealthStatus('系统状态正常');
      }
    }

    function renderSystemHealth(checks) {
      els.healthGrid.innerHTML = '';
      for (const check of checks) {
        const card = document.createElement('div');
        card.className = `health-card ${check.status || 'warn'}`;
        const title = document.createElement('strong');
        title.textContent = check.name || '';
        const state = document.createElement('div');
        state.className = 'health-state';
        state.textContent = check.label || check.status || '';
        const detail = document.createElement('div');
        detail.className = 'muted small';
        detail.textContent = check.detail || '';
        card.appendChild(title);
        card.appendChild(state);
        card.appendChild(detail);
        els.healthGrid.appendChild(card);
      }
    }

    async function uploadKnowledgeFile() {
      const file = (els.knowledgeFile.files || [])[0];
      if (!file) {
        setStatus('请选择 .md/.txt/.json/.csv 知识文件', true);
        return;
      }
      setStatus('正在上传知识文件');
      try {
        const contentBase64 = await fileToBase64(file);
        const result = await api('/api/upload-knowledge', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            filename: file.name,
            content_base64: contentBase64,
          }),
        });
        els.knowledgeFile.value = '';
        setStatus(`知识文件已上传：${result.name}`);
        await loadKnowledgeList();
      } catch (err) {
        setStatus(err.message, true);
      }
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

    async function uploadVoiceReferenceAudio() {
      const file = els.voiceReferenceFile.files && els.voiceReferenceFile.files[0];
      if (!file) return els.voiceReferenceAudioPath.value.trim();
      const contentBase64 = await fileToBase64(file);
      const result = await api('/api/upload-voice-sample', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: file.name,
          content_base64: contentBase64,
        }),
      });
      els.voiceReferenceAudioPath.value = result.stored_path || '';
      saveSettings();
      return els.voiceReferenceAudioPath.value.trim();
    }

    function defaultVoxCPM2CommandTemplate() {
      return 'voxcpm clone --text-file {text_file} --reference-audio {reference_audio} --output {output_file}';
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
          selectedTaskSummary = {};
          els.viewerTitle.textContent = '未选择任务';
          els.viewerMeta.textContent = '运行后会在这里查看输出文件';
          els.fileTabs.innerHTML = '';
          els.fileContent.value = '选择左侧任务，或运行一个新任务。';
          renderOutputOverview(null);
          syncOutputButtons();
        }
        setStatus('任务已删除');
        await loadTasks();
      } catch (err) {
        setStatus(err.message, true);
      }
    }

    function setStaffStatus(text, isError = false) {
      els.staffStatus.textContent = text;
      els.staffStatus.classList.toggle('error', isError);
    }

    async function loadStaffList() {
      const data = await api('/api/staff');
      const keyword = (els.staffFilter.value || '').trim().toLowerCase();
      const staffItems = (data.staff || []).filter(staff => {
        const text = `${staff.name || ''} ${staff.display_name || ''} ${staff.role || ''}`.toLowerCase();
        return !keyword || text.includes(keyword);
      });
      els.staffList.innerHTML = '';
      setStaffStatus(`共 ${data.staff.length} 位员工${keyword ? `，筛选出 ${staffItems.length} 位` : ''}`);
      if (!staffItems.length) {
        els.staffList.innerHTML = '<div class="muted small">暂无匹配员工</div>';
        return;
      }
      for (const staff of staffItems) {
        const btn = document.createElement('button');
        btn.className = `staff-card ${selectedStaff === staff.name ? 'active' : ''}`;
        const title = document.createElement('strong');
        title.textContent = staff.display_name || staff.name;
        const meta = document.createElement('span');
        meta.className = 'muted small staff-meta';
        meta.textContent = staff.name;
        btn.appendChild(title);
        btn.appendChild(meta);
        if (staff.role) {
          const role = document.createElement('span');
          role.className = 'small staff-role';
          role.textContent = staff.role;
          btn.appendChild(role);
        }
        btn.onclick = () => selectStaff(staff.name);
        els.staffList.appendChild(btn);
      }
    }

    async function selectStaff(name) {
      selectedStaff = name;
      const data = await api(`/api/staff-detail?name=${encodeURIComponent(name)}`);
      els.staffName.value = data.name;
      els.staffAgentMd.value = data.agent_md || '';
      els.staffFlowRule.value = data.flow_rule_json || '{}';
      els.deleteStaffBtn.disabled = false;
      setStaffStatus(`已选择：${name}`);
      await loadStaffList();
    }

    function defaultStaffAgentMd(name) {
      return `---\nname: ${name.replace(/^\\d+_/, '')}\ndescription: 请填写这个数字员工的职责。\nemoji: 🧩\ncolor: blue\n---\n\n# ${name.replace(/^\\d+_/, '')}\n\n## 核心职责\n\n- 请填写职责 1。\n- 请填写职责 2。\n\n## 输出格式\n\n请始终输出中文 Markdown。\n`;
    }

    function defaultStaffFlowRule(name) {
      return JSON.stringify({
        agent_id: name,
        agent_name: name.replace(/^\\d+_/, ''),
        role: 'custom_staff',
        inputs: ['用户需求'],
        outputs: ['员工输出'],
        handoff_to: [],
        quality_gate: ['输出清晰', '可交给下游继续使用'],
      }, null, 2);
    }

    function newStaff() {
      const name = prompt('请输入员工文件夹名，例如：20_销售话术专员');
      if (!name) return;
      selectedStaff = null;
      els.staffName.value = name.trim();
      els.staffAgentMd.value = defaultStaffAgentMd(name.trim());
      els.staffFlowRule.value = defaultStaffFlowRule(name.trim());
      els.deleteStaffBtn.disabled = true;
      setStaffStatus('正在编辑新员工，点击“保存员工”写入');
    }

    async function saveStaff() {
      const name = els.staffName.value.trim();
      if (!name) {
        setStaffStatus('员工文件夹名不能为空', true);
        return;
      }
      try {
        JSON.parse(els.staffFlowRule.value || '{}');
      } catch (err) {
        setStaffStatus(`flow_rule.json 不是合法 JSON：${err.message}`, true);
        return;
      }
      try {
        const result = await api('/api/save-staff', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            name,
            agent_md: els.staffAgentMd.value,
            flow_rule_json: els.staffFlowRule.value,
          }),
        });
        selectedStaff = result.name;
        setStaffStatus(`已保存：${result.name}`);
        await loadStaffList();
        await loadConfig();
      } catch (err) {
        setStaffStatus(err.message, true);
      }
    }

    async function deleteStaff() {
      if (!selectedStaff) return;
      if (!confirm(`确定删除这个数字员工？\n\n${selectedStaff}\n\n这会删除 my_custom_staff 下对应文件夹。`)) return;
      try {
        await api('/api/delete-staff', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: selectedStaff }),
        });
        selectedStaff = null;
        els.staffName.value = '';
        els.staffAgentMd.value = '';
        els.staffFlowRule.value = '';
        els.deleteStaffBtn.disabled = true;
        setStaffStatus('员工已删除');
        await loadStaffList();
        await loadConfig();
      } catch (err) {
        setStaffStatus(err.message, true);
      }
    }

    function setWorkflowEditorStatus(text, isError = false) {
      els.workflowEditorStatus.textContent = text;
      els.workflowEditorStatus.classList.toggle('error', isError);
    }

    async function loadWorkflowList() {
      const data = await api('/api/workflows');
      staffOptions = data.staff || staffOptions;
      els.workflowList.innerHTML = '';
      if (!data.workflows.length) {
        els.workflowList.innerHTML = '<div class="muted small">暂无工作流</div>';
        return;
      }
      for (const workflow of data.workflows) {
        const btn = document.createElement('button');
        btn.className = `staff-card ${selectedWorkflow === workflow.stem ? 'active' : ''}`;
        const title = document.createElement('strong');
        title.textContent = workflow.name || workflow.stem;
        const file = document.createElement('span');
        file.className = 'muted small';
        file.textContent = workflow.file || `${workflow.stem}.json`;
        const description = document.createElement('span');
        description.className = 'muted small';
        description.textContent = workflow.description || '';
        btn.appendChild(title);
        btn.appendChild(file);
        btn.appendChild(description);
        btn.onclick = () => selectWorkflow(workflow.stem);
        els.workflowList.appendChild(btn);
      }
    }

    async function selectWorkflow(name) {
      selectedWorkflow = name;
      const data = await api(`/api/workflow-detail?name=${encodeURIComponent(name)}`);
      const workflow = data.workflow || {};
      els.workflowFile.value = data.file || `${data.name}.json`;
      els.workflowName.value = workflow.name || data.name || '';
      els.workflowDescription.value = workflow.description || '';
      workflowEditorBase = workflow;
      workflowEditorSteps = normalizeWorkflowSteps(workflow.steps || []);
      els.deleteWorkflowBtn.disabled = false;
      renderWorkflowSteps();
      setWorkflowEditorStatus(`已选择：${data.file || data.name}`);
      await loadWorkflowList();
    }

    function normalizeWorkflowSteps(steps) {
      return steps.map((step, index) => ({
        step: index + 1,
        agent: String(step.agent || step.agent_id || '').trim(),
        task: String(step.task || step.instruction || '').trim(),
        output: String(step.output || step.expected_output || '').trim(),
      }));
    }

    function newWorkflow() {
      selectedWorkflow = null;
      const stamp = new Date().toISOString().slice(0, 10).replaceAll('-', '');
      els.workflowFile.value = `workflow_新工作流_${stamp}`;
      els.workflowName.value = '新工作流';
      els.workflowDescription.value = '';
      workflowEditorBase = {};
      workflowEditorSteps = [];
      els.deleteWorkflowBtn.disabled = true;
      renderWorkflowSteps();
      setWorkflowEditorStatus('正在编辑新工作流，点击“保存工作流”写入文件');
      loadWorkflowList().catch(err => setWorkflowEditorStatus(err.message, true));
    }

    function addWorkflowStep() {
      workflowEditorSteps.push({
        step: workflowEditorSteps.length + 1,
        agent: staffOptions[0] || '',
        task: '',
        output: '',
      });
      renderWorkflowSteps();
    }

    function moveWorkflowStep(index, delta) {
      const next = index + delta;
      if (next < 0 || next >= workflowEditorSteps.length) return;
      const current = workflowEditorSteps[index];
      workflowEditorSteps[index] = workflowEditorSteps[next];
      workflowEditorSteps[next] = current;
      renderWorkflowSteps();
    }

    function deleteWorkflowStep(index) {
      workflowEditorSteps.splice(index, 1);
      renderWorkflowSteps();
    }

    function renderWorkflowSteps() {
      els.workflowSteps.innerHTML = '';
      if (!workflowEditorSteps.length) {
        els.workflowSteps.innerHTML = '<div class="muted small">暂无步骤，点击“新增步骤”开始组装。</div>';
        return;
      }
      workflowEditorSteps.forEach((step, index) => {
        step.step = index + 1;
        const item = document.createElement('div');
        item.className = 'workflow-step';

        const head = document.createElement('div');
        head.className = 'workflow-step-head';
        const title = document.createElement('strong');
        title.textContent = `第 ${index + 1} 步`;
        const actions = document.createElement('div');
        actions.className = 'row';
        const up = document.createElement('button');
        up.type = 'button';
        up.textContent = '上移';
        up.disabled = index === 0;
        up.onclick = () => moveWorkflowStep(index, -1);
        const down = document.createElement('button');
        down.type = 'button';
        down.textContent = '下移';
        down.disabled = index === workflowEditorSteps.length - 1;
        down.onclick = () => moveWorkflowStep(index, 1);
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'danger';
        remove.textContent = '删除步骤';
        remove.onclick = () => deleteWorkflowStep(index);
        actions.appendChild(up);
        actions.appendChild(down);
        actions.appendChild(remove);
        head.appendChild(title);
        head.appendChild(actions);

        const grid = document.createElement('div');
        grid.className = 'workflow-step-grid';

        const agentLabel = document.createElement('label');
        agentLabel.textContent = '数字员工';
        const agentSelect = document.createElement('select');
        if (!staffOptions.length) {
          const option = document.createElement('option');
          option.value = '';
          option.textContent = '暂无员工';
          agentSelect.appendChild(option);
        } else {
          for (const staff of staffOptions) {
            const option = document.createElement('option');
            option.value = staff;
            option.textContent = staff;
            agentSelect.appendChild(option);
          }
        }
        agentSelect.value = step.agent;
        agentSelect.onchange = () => { step.agent = agentSelect.value; };
        agentLabel.appendChild(agentSelect);

        const taskLabel = document.createElement('label');
        taskLabel.textContent = '任务说明';
        const taskInput = document.createElement('input');
        taskInput.value = step.task;
        taskInput.placeholder = '这一位员工要完成什么';
        taskInput.oninput = () => { step.task = taskInput.value; };
        taskLabel.appendChild(taskInput);

        const outputLabel = document.createElement('label');
        outputLabel.textContent = '输出物';
        const outputInput = document.createElement('input');
        outputInput.value = step.output;
        outputInput.placeholder = '例如 需求拆解.md / 分镜脚本.md';
        outputInput.oninput = () => { step.output = outputInput.value; };
        outputLabel.appendChild(outputInput);

        grid.appendChild(agentLabel);
        grid.appendChild(taskLabel);
        grid.appendChild(outputLabel);
        item.appendChild(head);
        item.appendChild(grid);
        els.workflowSteps.appendChild(item);
      });
    }

    function workflowPayloadFromEditor() {
      const file = els.workflowFile.value.trim();
      const name = els.workflowName.value.trim();
      if (!file) throw new Error('工作流文件名不能为空');
      if (!name) throw new Error('工作流名称不能为空');
      if (!workflowEditorSteps.length) throw new Error('工作流至少需要 1 个步骤');
      const steps = workflowEditorSteps.map((step, index) => {
        const agent = String(step.agent || '').trim();
        const task = String(step.task || '').trim();
        const output = String(step.output || '').trim();
        if (!agent) throw new Error(`第 ${index + 1} 步未选择数字员工`);
        if (!task) throw new Error(`第 ${index + 1} 步任务说明不能为空`);
        if (!output) throw new Error(`第 ${index + 1} 步输出物不能为空`);
        return { step: index + 1, agent, task, output };
      });
      return {
        file,
        workflow: {
          ...workflowEditorBase,
          name,
          description: els.workflowDescription.value.trim(),
          steps,
        },
      };
    }

    async function saveWorkflow() {
      try {
        const payload = workflowPayloadFromEditor();
        const result = await api('/api/save-workflow', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        selectedWorkflow = result.name;
        setWorkflowEditorStatus(`已保存：${result.file}`);
        await loadConfig();
        await selectWorkflow(result.name);
      } catch (err) {
        setWorkflowEditorStatus(err.message, true);
      }
    }

    async function deleteWorkflow() {
      if (!selectedWorkflow) return;
      if (!confirm(`确定删除这个工作流？\n\n${selectedWorkflow}\n\n这会删除 my_workflows 下对应 JSON 文件。`)) return;
      try {
        await api('/api/delete-workflow', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: selectedWorkflow }),
        });
        selectedWorkflow = null;
        workflowEditorSteps = [];
        workflowEditorBase = {};
        els.workflowFile.value = '';
        els.workflowName.value = '';
        els.workflowDescription.value = '';
        els.deleteWorkflowBtn.disabled = true;
        renderWorkflowSteps();
        setWorkflowEditorStatus('工作流已删除');
        await loadConfig();
        await loadWorkflowList();
      } catch (err) {
        setWorkflowEditorStatus(err.message, true);
      }
    }

    async function selectTask(name) {
      showView('output');
      selectedTask = name;
      selectedFile = null;
      await loadTasks();
      const data = await api(`/api/task?name=${encodeURIComponent(name)}`);
      selectedTaskSummary = data.summary || {};
      els.viewerTitle.textContent = data.summary.task_title || data.summary.workflow || name;
      els.viewerMeta.textContent = name;
      renderFiles(data.files);
      renderOutputOverview(data);
      syncOutputButtons();
      const first = data.files.find(f => f.endsWith('final_output.md')) || data.files[0];
      if (first) await openFile(first);
    }

    function renderOutputOverview(data) {
      if (!data) {
        els.outputSummaryGrid.innerHTML = summaryCards([
          ['任务', '未选择'],
          ['工作流', '-'],
          ['步骤输出', '0'],
          ['产品包', '未生成'],
        ]);
        els.stepOutputMeta.textContent = '0 个步骤';
        els.stepOutputList.innerHTML = '<div class="muted small">选择任务后显示每个员工的输出。</div>';
        els.packageOutputMeta.textContent = '未生成';
        els.packageOutputList.innerHTML = '<div class="muted small">点击“导出产品包”后显示可交付文件。</div>';
        return;
      }

      const files = data.files || [];
      const summary = data.summary || {};
      const stepFiles = files.filter(file => /^step_\d+_.*\/output\.md$/.test(file));
      const packageFiles = files.filter(file => file.startsWith('export_package/') && !file.endsWith('/'));
      const finalReady = files.includes('final_output.md') ? '已生成' : '缺失';
      const packageReady = packageFiles.length ? `${packageFiles.length} 个文件` : '未生成';
      els.outputSummaryGrid.innerHTML = summaryCards([
        ['任务', summary.task_title || data.name],
        ['工作流', summary.workflow || '-'],
        ['最终输出', finalReady],
        ['产品包', packageReady],
      ]);

      els.stepOutputMeta.textContent = `${stepFiles.length} 个步骤`;
      els.stepOutputList.innerHTML = '';
      if (!stepFiles.length) {
        els.stepOutputList.innerHTML = '<div class="muted small">暂无步骤输出。先运行工作流，或检查 task_output 目录。</div>';
      } else {
        for (const file of stepFiles) {
          els.stepOutputList.appendChild(outputFileButton(file, stepFileLabel(file), '点击查看该员工 output.md'));
        }
      }

      els.packageOutputMeta.textContent = packageReady;
      els.packageOutputList.innerHTML = '';
      if (!packageFiles.length) {
        els.packageOutputList.innerHTML = '<div class="muted small">还没有产品包。点击右上角“导出产品包”生成可交付文件。</div>';
      } else {
        const priority = ['README.md', 'final_output.md', '视频制作包.md', '语音字幕制作包.md', 'ComfyUI素材编排.md', '剪辑成片执行方案.md', '小红书文案.md', 'GDD.md', '产品需求文档.md', 'manifest.json'];
        packageFiles.sort((a, b) => {
          const an = a.split('/').pop();
          const bn = b.split('/').pop();
          const ai = priority.indexOf(an);
          const bi = priority.indexOf(bn);
          if (ai !== -1 || bi !== -1) return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
          return a.localeCompare(b);
        });
        for (const file of packageFiles) {
          els.packageOutputList.appendChild(outputFileButton(file, file.replace('export_package/', ''), file));
        }
      }
    }

    function summaryCards(items) {
      return items.map(([label, value]) => `
        <div class="output-card">
          <span class="label">${escapeHtml(label)}</span>
          <span class="value" title="${escapeHtml(String(value))}">${escapeHtml(String(value))}</span>
        </div>
      `).join('');
    }

    function outputFileButton(file, title, subtitle) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `output-link ${selectedFile === file ? 'active' : ''}`;
      btn.dataset.file = file;
      const main = document.createElement('span');
      main.className = 'output-link-title';
      main.textContent = title;
      const sub = document.createElement('span');
      sub.className = 'muted small output-link-subtitle';
      sub.textContent = subtitle;
      btn.appendChild(main);
      btn.appendChild(sub);
      btn.onclick = () => openFile(file);
      return btn;
    }

    function stepFileLabel(file) {
      const match = String(file).match(/^step_(\d+)_(.*)\/output\.md$/);
      if (!match) return file;
      const agent = match[2].replaceAll('_', ' ');
      return `${Number(match[1])}. ${agent}`;
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, char => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[char]));
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
      els.fileContent.value = data.content;
      for (const btn of els.fileTabs.querySelectorAll('button')) {
        btn.classList.toggle('active', btn.textContent === file);
      }
      for (const btn of document.querySelectorAll('.output-link')) {
        btn.classList.toggle('active', btn.dataset.file === file);
      }
      syncOutputButtons();
    }

    function syncOutputButtons() {
      const hasTask = Boolean(selectedTask);
      const hasFile = Boolean(selectedTask && selectedFile);
      els.saveFileBtn.disabled = !hasFile;
      els.rebuildFinalBtn.disabled = !hasTask;
      els.exportTaskBtn.disabled = !hasTask;
      els.rerunStepBtn.disabled = !hasFile || !stepNumberFromFile(selectedFile);
    }

    function stepNumberFromFile(file) {
      const match = String(file || '').match(/^step_(\d+)_.*\/output\.md$/);
      return match ? Number(match[1]) : 0;
    }

    async function saveCurrentFile() {
      if (!selectedTask || !selectedFile) return;
      setStatus('正在保存当前输出文件');
      try {
        await api('/api/save-file', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task: selectedTask,
            file: selectedFile,
            content: els.fileContent.value,
          }),
        });
        setStatus(`已保存：${selectedFile}`);
      } catch (err) {
        setStatus(err.message, true);
      }
    }

    async function rebuildFinalOutput() {
      if (!selectedTask) return;
      setStatus('正在重建最终汇总');
      try {
        const result = await api('/api/rebuild-final-output', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ task: selectedTask }),
        });
        setStatus(`已重建：${result.file}`);
        await selectTask(selectedTask);
        await openFile('final_output.md');
      } catch (err) {
        setStatus(err.message, true);
      }
    }

    async function rerunCurrentStep() {
      if (!selectedTask || !selectedFile) return;
      const step = stepNumberFromFile(selectedFile);
      if (!step) return;
      const model = els.model.value === 'custom' ? els.customModel.value.trim() : els.model.value;
      if (els.model.value === 'custom' && !model) {
        setStatus('请输入自定义模型名', true);
        return;
      }
      if (!confirm(`确定重跑第 ${step} 步？\n\n系统会覆盖该步骤 output.md，并基于当前各步骤输出重建 final_output.md。`)) return;
      setStatus(`正在重跑第 ${step} 步`);
      els.rerunStepBtn.disabled = true;
      try {
        await ensureLocalModelReady(model);
        const result = await api('/api/rerun-step', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task: selectedTask,
            step,
            provider: els.provider.value,
            model,
            api_key: els.apiKey.value.trim(),
            base_url: els.baseUrl.value.trim(),
            timeout: Number(els.modelTimeout.value || 900),
          }),
        });
        setStatus(`第 ${step} 步已重跑：${result.file}`);
        await selectTask(selectedTask);
        await openFile(result.file);
      } catch (err) {
        setStatus(err.message, true);
      } finally {
        syncOutputButtons();
      }
    }

    async function exportCurrentTask() {
      if (!selectedTask) return;
      setStatus('正在导出产品包');
      try {
        const result = await api('/api/export-task', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            task: selectedTask,
            template: els.productTemplate.value,
          }),
        });
        setStatus(`已导出产品包：${result.export_dir}`);
        await selectTask(selectedTask);
      } catch (err) {
        setStatus(err.message, true);
      }
    }

    function applyProductTemplate(fillSample = false) {
      const template = PRODUCT_TEMPLATES[els.productTemplate.value];
      if (!template) return;
      setIfExists(els.workflow, template.workflow);
      els.taskTitle.value = template.taskTitle || '';
      setIfExists(els.autoProductionMode, template.autoProductionMode);
      setIfExists(els.imageSize, template.imageSize);
      setIfExists(els.videoAspect, template.videoAspect);
      setIfExists(els.videoDuration, template.videoDuration);
      if (fillSample) els.userInput.value = template.sample || '';
      saveSettings();
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
        await ensureLocalModelReady(model);
        const referenceImages = await uploadReferenceImages();
        const voiceReferenceAudio = els.voiceMode.value === 'voxcpm2' ? await uploadVoiceReferenceAudio() : '';
        const imageConfig = {
          tool: 'prompt_only',
          positive_prompt: '',
          model: '',
          size: '',
          count_per_shot: '',
          style: '',
          quality: '',
          negative_prompt: '',
          consistency: '',
          seed: '',
          guidance_scale: '',
          steps: '',
          denoise_strength: '',
          sampler: '',
          control: '',
          api_key_provided: false,
          base_url_provided: false,
          workflow_endpoint: '',
          instance_type: '',
          node_info_list_json: '',
          poll_timeout_seconds: 900,
        };
        const videoConfig = {
          tool: 'prompt_only',
          positive_prompt: '',
          model: '',
          aspect_ratio: '',
          duration: '',
          style: '',
          prompt_notes: '',
          negative_prompt: '',
          seed: '',
          fps: '',
          motion_strength: '',
          camera_motion: '',
          resolution: '',
          guidance_scale: '',
          frames: '',
          image_strength: '',
          camera_path: '',
          audio_notes: '',
          advanced_params: '',
          api_key_provided: false,
          base_url_provided: false,
          workflow_endpoint: '',
          node_info_list_json: '',
          poll_timeout_seconds: 1800,
        };
        const productionConfig = {
          mode: els.autoProductionMode.value,
          image_config: imageConfig,
          video_config: videoConfig,
          voice_config: {
            mode: els.voiceMode.value,
            provider: els.voiceMode.value === 'voxcpm2' ? 'voxcpm2' : '',
            reference_audio: voiceReferenceAudio,
            reference_text: els.voiceReferenceText.value.trim(),
            command_template: els.voiceCommandTemplate.value.trim() || defaultVoxCPM2CommandTemplate(),
            timeout_seconds: Number(els.voiceTimeout.value || 1800),
          },
          compose_config: {
            tool: els.composeTool.value,
            execution_mode: els.autoProductionMode.value,
            final_video_name: els.finalVideoName.value.trim() || 'final_video.mp4',
            api_key_provided: Boolean(els.comfyApiKey.value.trim()),
            base_url_provided: Boolean(els.comfyBaseUrl.value.trim()),
            base_url: els.comfyBaseUrl.value.trim(),
            workflow_endpoint: els.comfyWorkflowEndpoint.value.trim(),
            node_info_list_json: els.comfyNodeInfoList.value.trim(),
            poll_timeout_seconds: Number(els.comfyPollTimeout.value || 3600),
          },
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
            timeout: Number(els.modelTimeout.value || 900),
            memory_scope: els.useMemory.value,
            use_knowledge: els.useKnowledge.value === 'on',
            inherit_task: els.inheritTask.value,
            inherit_mode: els.inheritMode.value,
            production_config: productionConfig,
            image_config: imageConfig,
            video_config: videoConfig,
            reference_images: referenceImages,
            image_api_key: '',
            image_base_url: '',
            video_api_key: '',
            video_base_url: '',
            comfy_api_key: els.comfyApiKey.value.trim(),
            comfy_base_url: els.comfyBaseUrl.value.trim(),
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
    navButtons.forEach(button => {
      button.onclick = () => showView(button.dataset.viewTarget);
    });
    els.refreshTasks.onclick = loadTasks;
    els.saveFileBtn.onclick = saveCurrentFile;
    els.rebuildFinalBtn.onclick = rebuildFinalOutput;
    els.rerunStepBtn.onclick = rerunCurrentStep;
    els.exportTaskBtn.onclick = exportCurrentTask;
    els.refreshStaffBtn.onclick = loadStaffList;
    els.staffFilter.oninput = () => loadStaffList().catch(err => setStaffStatus(err.message, true));
    els.newStaffBtn.onclick = newStaff;
    els.saveStaffBtn.onclick = saveStaff;
    els.deleteStaffBtn.onclick = deleteStaff;
    els.refreshWorkflowsBtn.onclick = loadWorkflowList;
    els.newWorkflowBtn.onclick = newWorkflow;
    els.addWorkflowStepBtn.onclick = addWorkflowStep;
    els.saveWorkflowBtn.onclick = saveWorkflow;
    els.deleteWorkflowBtn.onclick = deleteWorkflow;
    els.localModelPreset.onchange = applyLocalModelPreset;
    els.localModelName.onchange = applyLocalModelName;
    els.imageTool.onchange = () => {
      applyImageProviderDefaults();
      saveSettings();
    };
    els.videoTool.onchange = () => {
      applyVideoProviderDefaults();
      saveSettings();
    };
    els.composeTool.onchange = () => {
      applyComfyProviderDefaults();
      saveSettings();
    };
    els.autoProductionMode.onchange = () => {
      if (els.autoProductionMode.value === 'comfy_full') {
        els.composeTool.value = 'runninghub';
      }
      applyComfyProviderDefaults();
      saveSettings();
    };
    els.comfyApiWorkflowFile.onchange = analyzeComfyApiWorkflowFile;
    els.localOfflineBtn.onclick = applyLocalOfflineMode;
    els.testModelBtn.onclick = testModelConnection;
    els.uploadKnowledgeBtn.onclick = uploadKnowledgeFile;
    els.refreshHealthBtn.onclick = loadSystemHealth;
    els.productTemplate.onchange = () => applyProductTemplate(false);
    els.model.onchange = () => {
      syncCustomModelState();
      saveSettings();
    };
    els.sampleBtn.onclick = () => {
      applyProductTemplate(true);
      if (els.productTemplate.value !== 'short_video') return;
      els.userInput.value = '我要做一条抖音短视频，推广 AI 自动化开发服务。目标客户是中小企业老板，他们想降本增效但不知道怎么落地。视频目标是让客户私信咨询，风格专业、直接、有案例感，不要夸大承诺。';
      els.taskTitle.value = 'AI自动化获客短视频';
      els.autoProductionMode.value = 'package_only';
      els.composeTool.value = 'ffmpeg';
      els.finalVideoName.value = 'final_video.mp4';
      els.comfyApiKey.value = '';
      els.comfyBaseUrl.value = '';
      els.comfyWorkflowEndpoint.value = '';
      els.comfyNodeInfoList.value = '[]';
      els.comfyPollTimeout.value = '3600';
      els.voiceMode.value = 'off';
      els.voiceReferenceAudioPath.value = '';
      els.voiceReferenceText.value = '';
      els.voiceCommandTemplate.value = defaultVoxCPM2CommandTemplate();
      els.voiceTimeout.value = '1800';
      els.imageTool.value = 'prompt_only';
      els.imagePositivePrompt.value = '写实商业，干净明亮，统一人物形象，突出 AI 自动化服务价值';
      els.imageModel.value = '';
      els.imageSize.value = '9:16';
      els.imageCount.value = '1';
      els.imageStyle.value = '写实商业，干净明亮，统一人物形象';
      els.imageQuality.value = 'standard';
      els.imageNegativePrompt.value = '水印、畸形手指、低清晰度、脸部变形、错误文字';
      els.imageConsistency.value = '保持同一人物脸型、服装、产品外观和主色调';
      els.videoTool.value = 'prompt_only';
      els.videoPositivePrompt.value = '真人口播，商业科技感，画面干净明亮，前半段人物口播稳定推进，中段切产品界面和案例画面，结尾推近到行动号召。';
      els.videoModel.value = '';
      els.videoAspect.value = '9:16';
      els.videoDuration.value = '30s';
      els.videoStyle.value = '真人口播，商业科技感，干净明亮';
      els.videoPromptNotes.value = '前半段人物口播稳定推进，中段切产品界面和案例画面，结尾推近到行动号召；镜头自然，节奏直接。';
      els.referenceRole.value = '人物一致性';
      els.referenceNote.value = '固定人物参考图，后续镜头保持同一角色与风格';
      saveSettings();
    };
    els.gameSampleBtn.onclick = () => {
      els.productTemplate.value = 'game_steam';
      applyProductTemplate(true);
      setIfExists(els.workflow, 'workflow_Unity3D游戏Steam上架');
      els.userInput.value = '我想做一款 Unity 3D 第三人称探索解谜游戏，上架 Steam。目标玩家是喜欢低多边形、轻剧情、环境谜题和短流程独立游戏的玩家。团队规模按单人或两人小团队考虑，优先做 20-30 分钟可玩 Demo，用于 Steam 商店页、愿望单和后续众筹/抢先体验验证。希望风格统一、开发范围可控，不做联网，不做大型开放世界。';
      els.taskTitle.value = 'Unity3D探索解谜Steam游戏立项';
      els.autoProductionMode.value = 'off';
      els.composeTool.value = 'manual';
      els.finalVideoName.value = '';
      els.comfyApiKey.value = '';
      els.comfyBaseUrl.value = '';
      els.comfyWorkflowEndpoint.value = '';
      els.comfyNodeInfoList.value = '[]';
      els.comfyPollTimeout.value = '3600';
      els.voiceMode.value = 'off';
      els.voiceReferenceAudioPath.value = '';
      els.voiceReferenceText.value = '';
      els.voiceCommandTemplate.value = defaultVoxCPM2CommandTemplate();
      els.voiceTimeout.value = '1800';
      els.imageTool.value = 'prompt_only';
      els.imagePositivePrompt.value = '低多边形 3D，温暖但带神秘感，清晰轮廓，适合 Steam 商店截图，角色和场景风格统一';
      els.imageModel.value = '';
      els.imageSize.value = '16:9';
      els.imageCount.value = '1';
      els.imageStyle.value = '低多边形 3D，温暖但带神秘感，清晰轮廓，适合 Steam 商店截图';
      els.imageQuality.value = 'standard';
      els.imageNegativePrompt.value = '水印、低清晰度、文字错误、角色比例异常、过度写实';
      els.imageConsistency.value = '保持同一主角造型、低多边形材质语言、统一色彩和关卡氛围';
      els.videoTool.value = 'prompt_only';
      els.videoPositivePrompt.value = 'Steam 商店预告片，从环境氛围开场，切到角色探索、谜题互动和关键机制，最后展示标题画面；镜头平稳，突出可玩内容。';
      els.videoModel.value = '';
      els.videoAspect.value = '16:9';
      els.videoDuration.value = '30s';
      els.videoStyle.value = 'Steam商店预告片，展示玩法循环、探索、谜题和关键氛围';
      els.videoPromptNotes.value = '从环境氛围开场，切到角色探索、谜题互动和关键机制，最后展示标题画面；镜头平稳，突出可玩内容。';
      els.referenceRole.value = '视觉风格参考';
      els.referenceNote.value = '用于统一角色、场景、美术风格和 Steam 宣传素材方向';
      saveSettings();
    };
    els.clearSettingsBtn.onclick = () => {
      if (!confirm('确定清除本浏览器保存的 API Key、Base URL、模型、生图配置和视频配置？')) return;
      localStorage.removeItem(SETTINGS_KEY);
      els.productTemplate.value = 'short_video';
      els.provider.value = 'auto';
      els.model.value = 'gpt-5.5';
      els.customModel.value = '';
      els.taskTitle.value = '';
      els.apiKey.value = '';
      els.baseUrl.value = '';
      els.modelTimeout.value = '900';
      els.localModelPreset.value = '';
      renderLocalModelNames();
      els.useMemory.value = 'video_output';
      els.useKnowledge.value = 'off';
      els.inheritTask.value = '';
      els.inheritMode.value = 'final_output';
      els.autoProductionMode.value = 'off';
      els.composeTool.value = 'ffmpeg';
      els.finalVideoName.value = '';
      els.comfyApiKey.value = '';
      els.comfyBaseUrl.value = '';
      els.comfyWorkflowEndpoint.value = '';
      els.comfyNodeInfoList.value = '[]';
      els.comfyPollTimeout.value = '3600';
      els.voiceMode.value = 'off';
      els.voiceReferenceAudioPath.value = '';
      els.voiceReferenceText.value = '';
      els.voiceCommandTemplate.value = defaultVoxCPM2CommandTemplate();
      els.voiceTimeout.value = '1800';
      els.imageTool.value = 'prompt_only';
      els.imagePositivePrompt.value = '';
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
      els.videoPositivePrompt.value = '';
      els.videoModel.value = '';
      els.videoAspect.value = '9:16';
      els.videoDuration.value = '30s';
      els.videoStyle.value = '';
      els.videoPromptNotes.value = '';
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
    renderOutputOverview(null);

    (async function init() {
      try {
        await loadConfig();
        await loadTasks();
        await loadStaffList();
        await loadWorkflowList();
        await loadKnowledgeList();
        await loadSystemHealth();
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
            elif parsed.path == "/api/system-health":
                self._send_json(self._system_health())
            elif parsed.path == "/api/tasks":
                self._send_json({"tasks": self._tasks()})
            elif parsed.path == "/api/knowledge":
                self._send_json({"files": self._knowledge_files()})
            elif parsed.path == "/api/staff":
                self._send_json({"staff": self._staff_list()})
            elif parsed.path == "/api/staff-detail":
                query = parse_qs(parsed.query)
                self._send_json(self._staff_detail(self._single(query, "name")))
            elif parsed.path == "/api/workflows":
                self._send_json({"workflows": self._workflow_list(), "staff": [item["name"] for item in self._staff_list()]})
            elif parsed.path == "/api/workflow-detail":
                query = parse_qs(parsed.query)
                self._send_json(self._workflow_detail(self._single(query, "name")))
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

            if parsed.path == "/api/upload-voice-sample":
                self._send_json(self._upload_voice_sample(payload))
                return

            if parsed.path == "/api/upload-knowledge":
                self._send_json(self._upload_knowledge(payload))
                return

            if parsed.path == "/api/test-model":
                self._send_json(self._test_model(payload))
                return

            if parsed.path == "/api/save-staff":
                self._send_json(self._save_staff(payload))
                return

            if parsed.path == "/api/delete-staff":
                self._delete_staff(str(payload.get("name") or "").strip())
                self._send_json({"ok": True})
                return

            if parsed.path == "/api/save-workflow":
                self._send_json(self._save_workflow(payload))
                return

            if parsed.path == "/api/delete-workflow":
                self._delete_workflow(str(payload.get("name") or "").strip())
                self._send_json({"ok": True})
                return

            if parsed.path == "/api/save-file":
                self._send_json(self._save_file(payload))
                return

            if parsed.path == "/api/rebuild-final-output":
                self._send_json(self._rebuild_final_output(str(payload.get("task") or "").strip()))
                return

            if parsed.path == "/api/rerun-step":
                self._send_json(self._rerun_step(payload))
                return

            if parsed.path == "/api/export-task":
                self._send_json(self._export_task(payload))
                return

            if parsed.path != "/api/run":
                self.send_error(404)
                return

            workflow = str(payload.get("workflow") or "").strip()
            task_title = str(payload.get("task_title") or "").strip()
            user_input = str(payload.get("input") or "").strip()
            memory_scope = str(payload.get("memory_scope") or "").strip()
            if not memory_scope and bool(payload.get("use_memory")):
                memory_scope = "all"
            use_knowledge = bool(payload.get("use_knowledge"))
            inherit_task = str(payload.get("inherit_task") or "").strip()
            inherit_mode = str(payload.get("inherit_mode") or "final_output").strip()
            if memory_scope == "all":
                user_input = self._append_long_term_memory(user_input)
            if use_knowledge:
                user_input = self._append_knowledge_base(user_input)
            if inherit_task:
                user_input = self._append_inherited_task(user_input, inherit_task, inherit_mode)
            production_config = payload.get("production_config") or {}
            if memory_scope == "video_output" and isinstance(production_config, dict):
                production_config["video_memory_context"] = self._long_term_memory_context()
            if isinstance(production_config, dict):
                production_image_config = production_config.get("image_config")
                if isinstance(production_image_config, dict):
                    production_image_config["api_key"] = str(payload.get("image_api_key") or "").strip()
                    production_image_config["base_url"] = str(payload.get("image_base_url") or "").strip()
                production_video_config = production_config.get("video_config")
                if isinstance(production_video_config, dict):
                    production_video_config["api_key"] = str(payload.get("video_api_key") or "").strip()
                    production_video_config["base_url"] = str(payload.get("video_base_url") or "").strip()
                production_compose_config = production_config.get("compose_config")
                if isinstance(production_compose_config, dict):
                    production_compose_config["api_key"] = str(payload.get("comfy_api_key") or "").strip()
                    production_compose_config["base_url"] = str(payload.get("comfy_base_url") or "").strip()
            image_config = payload.get("image_config") or {}
            if isinstance(image_config, dict) and str(image_config.get("positive_prompt") or "").strip():
                user_input = self._append_image_config(user_input, image_config)
            video_config = payload.get("video_config") or {}
            if isinstance(video_config, dict) and str(video_config.get("positive_prompt") or "").strip():
                user_input = self._append_video_config(user_input, video_config)
            if isinstance(production_config, dict):
                compose_config = production_config.get("compose_config") or {}
                if isinstance(compose_config, dict) and compose_config:
                    user_input = self._append_comfyui_config(user_input, production_config, compose_config)
            reference_images = payload.get("reference_images") or []
            if reference_images:
                user_input = self._append_reference_images(user_input, reference_images)
            provider = str(payload.get("provider") or "auto").strip()
            model = str(payload.get("model") or "").strip() or None
            api_key = str(payload.get("api_key") or "").strip() or None
            base_url = str(payload.get("base_url") or "").strip() or None
            timeout = int(payload.get("timeout") or 0) or None

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
                args=(run_id, workflow, user_input, task_title, production_config, provider, model, api_key, base_url, timeout),
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

        workflows = self._workflow_list()
        staff = [path.name for path in sorted(STAFF_ROOT.iterdir()) if path.is_dir()]
        return {
            "workflows": workflows,
            "staff": staff,
            "local_model_presets": self._local_model_presets(),
            "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
            "base_url_configured": bool(os.getenv("OPENAI_BASE_URL")),
            "default_model": os.getenv("OPENAI_MODEL") or "gpt-5.5",
            "default_base_url": os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        }

    @staticmethod
    def _local_model_presets() -> list[dict]:
        if not LOCAL_MODEL_PRESETS.exists():
            return []
        data = json.loads(LOCAL_MODEL_PRESETS.read_text(encoding="utf-8"))
        presets = data.get("presets") if isinstance(data, dict) else data
        return presets if isinstance(presets, list) else []

    def _system_health(self) -> dict:
        ollama_models = self._ollama_model_names()
        checks = [
            self._health_check("Python 运行时", "ok", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"),
            self._path_check("工作区目录", WORKSPACE_ROOT, must_be_writable=False),
            self._path_check("任务输出目录", OUTPUT_ROOT, must_be_writable=True),
            self._path_check("知识库目录", KNOWLEDGE_ROOT, must_be_writable=True),
            self._path_check("动作工作区", WORKSPACE_ROOT / "my_action_workspace", must_be_writable=True),
        ]

        bundled = WORKSPACE_ROOT.parent / "runtime" / "ollama" / "ollama.exe"
        installed = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
        ollama_path = shutil.which("ollama")
        if bundled.exists():
            checks.append(self._health_check("Ollama 命令", "ok", str(bundled)))
        elif ollama_path:
            checks.append(self._health_check("Ollama 命令", "ok", ollama_path))
        elif installed.exists():
            checks.append(self._health_check("Ollama 命令", "ok", str(installed)))
        else:
            checks.append(self._health_check("Ollama 命令", "warn", "未在 runtime/ollama/ollama.exe、PATH 或系统安装目录找到；可先安装 Ollama 或放入 runtime/ollama/"))

        checks.append(self._ollama_service_check(ollama_models))
        if "qwen3:8b-q4_K_M" in ollama_models:
            checks.append(self._health_check("推荐本地模型", "ok", "qwen3:8b-q4_K_M 已可用"))
        else:
            checks.append(self._health_check("推荐本地模型", "warn", "未发现 qwen3:8b-q4_K_M；可运行 start_local.ps1 自动拉取"))

        return {
            "checks": checks,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        }

    @staticmethod
    def _health_check(name: str, status: str, detail: str) -> dict:
        labels = {"ok": "正常", "warn": "提醒", "error": "异常"}
        return {"name": name, "status": status, "label": labels.get(status, status), "detail": detail}

    def _path_check(self, name: str, path: Path, must_be_writable: bool) -> dict:
        try:
            path.mkdir(parents=True, exist_ok=True)
            if must_be_writable:
                marker = path / f".health_{uuid4().hex[:8]}"
                marker.write_text("ok", encoding="utf-8")
                marker.unlink()
            return self._health_check(name, "ok", str(path))
        except Exception as exc:
            return self._health_check(name, "error", f"{path}: {exc}")

    def _ollama_model_names(self) -> list[str]:
        req = urllib_request.Request("http://127.0.0.1:11434/v1/models", method="GET")
        try:
            with urllib_request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
            models = data.get("data") if isinstance(data, dict) else []
            return [str(item.get("id") or item.get("name") or "") for item in models if isinstance(item, dict)]
        except Exception:
            return []

    def _ollama_service_check(self, names: list[str] | None = None) -> dict:
        if names is None:
            names = self._ollama_model_names()
        req = urllib_request.Request("http://127.0.0.1:11434/v1/models", method="GET")
        try:
            with urllib_request.urlopen(req, timeout=3):
                pass
            detail = "已连接 http://127.0.0.1:11434/v1"
            if names:
                detail += "；模型：" + ", ".join(names[:5])
            else:
                detail += "；暂未发现模型，可运行 start_local.ps1 自动拉取默认模型"
            return self._health_check("Ollama 模型服务", "ok", detail)
        except Exception as exc:
            return self._health_check("Ollama 模型服务", "warn", f"未连接 http://127.0.0.1:11434/v1；{exc}")

    def _workflow_list(self) -> list[dict]:
        if not WORKFLOW_ROOT.exists():
            return []

        workflows = []
        for path in sorted(WORKFLOW_ROOT.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = {}
            workflows.append(
                {
                    "stem": path.stem,
                    "file": path.name,
                    "name": data.get("name") or path.stem,
                    "description": data.get("description") or "",
                }
            )
        return workflows

    def _workflow_detail(self, name: str) -> dict:
        path = self._safe_workflow_path(name, must_exist=True)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Workflow JSON must be an object")
        return {"name": path.stem, "file": path.name, "workflow": data}

    def _save_workflow(self, payload: dict) -> dict:
        file_name = str(payload.get("file") or "").strip()
        workflow = payload.get("workflow")
        if not isinstance(workflow, dict):
            raise ValueError("workflow must be a JSON object")

        name = str(workflow.get("name") or "").strip()
        description = str(workflow.get("description") or "").strip()
        steps = workflow.get("steps")
        if not name:
            raise ValueError("Workflow name cannot be empty")
        if not isinstance(steps, list) or not steps:
            raise ValueError("Workflow must contain at least one step")

        staff_names = {item["name"] for item in self._staff_list()}
        normalized_steps = []
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                raise ValueError(f"Step {index} must be a JSON object")
            agent = str(step.get("agent") or step.get("agent_id") or "").strip()
            task = str(step.get("task") or step.get("instruction") or "").strip()
            output = str(step.get("output") or step.get("expected_output") or "").strip()
            if not agent:
                raise ValueError(f"Step {index} agent cannot be empty")
            if staff_names and agent not in staff_names:
                raise ValueError(f"Step {index} agent does not exist: {agent}")
            if not task:
                raise ValueError(f"Step {index} task cannot be empty")
            if not output:
                raise ValueError(f"Step {index} output cannot be empty")
            normalized_steps.append({"step": index, "agent": agent, "task": task, "output": output})

        path = self._safe_workflow_path(file_name, must_exist=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = dict(workflow)
        data["name"] = name
        data["description"] = description
        data["steps"] = normalized_steps
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"ok": True, "name": path.stem, "file": path.name}

    def _delete_workflow(self, name: str) -> None:
        path = self._safe_workflow_path(name, must_exist=True)
        path.unlink()

    def _staff_list(self) -> list[dict]:
        if not STAFF_ROOT.exists():
            return []

        staff = []
        for path in sorted(STAFF_ROOT.iterdir()):
            if not path.is_dir():
                continue
            rule_path = path / "flow_rule.json"
            rule = {}
            if rule_path.exists():
                try:
                    rule = json.loads(rule_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    rule = {}
            staff.append(
                {
                    "name": path.name,
                    "display_name": rule.get("agent_name") or path.name,
                    "agent_id": rule.get("agent_id") or path.name,
                    "role": rule.get("role") or "",
                }
            )
        return staff

    def _staff_detail(self, name: str) -> dict:
        staff_dir = self._safe_staff_dir(name, must_exist=True)
        agent_path = staff_dir / "agent.md"
        rule_path = staff_dir / "flow_rule.json"
        rule_text = rule_path.read_text(encoding="utf-8", errors="replace") if rule_path.exists() else "{}"
        if rule_text.strip():
            json.loads(rule_text)
        return {
            "name": staff_dir.name,
            "agent_md": agent_path.read_text(encoding="utf-8", errors="replace") if agent_path.exists() else "",
            "flow_rule_json": rule_text,
        }

    def _save_staff(self, payload: dict) -> dict:
        name = str(payload.get("name") or "").strip()
        agent_md = str(payload.get("agent_md") or "").strip()
        flow_rule_json = str(payload.get("flow_rule_json") or "{}").strip()
        if not agent_md:
            raise ValueError("agent.md cannot be empty")
        rule = json.loads(flow_rule_json)
        staff_dir = self._safe_staff_dir(name, must_exist=False)
        staff_dir.mkdir(parents=True, exist_ok=True)
        if not isinstance(rule, dict):
            raise ValueError("flow_rule.json must be a JSON object")
        rule.setdefault("agent_id", staff_dir.name)
        rule.setdefault("agent_name", staff_dir.name)
        (staff_dir / "agent.md").write_text(agent_md.rstrip() + "\n", encoding="utf-8")
        (staff_dir / "flow_rule.json").write_text(
            json.dumps(rule, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {"ok": True, "name": staff_dir.name}

    def _delete_staff(self, name: str) -> None:
        staff_dir = self._safe_staff_dir(name, must_exist=True)
        staff_root = STAFF_ROOT.resolve()
        if staff_dir == staff_root:
            raise ValueError("Refusing to delete staff root")

        for path in sorted(staff_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        staff_dir.rmdir()

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
                        "production_manifest": event.get("production_manifest", ""),
                        "production_status": event.get("production_status", "off"),
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
        production_config: dict,
        provider: str,
        model: str | None,
        api_key: str | None,
        base_url: str | None,
        timeout: int | None,
    ) -> None:
        try:
            self._update_job(run_id, {"status": "running"})
            engine = WorkflowEngine(
                WORKSPACE_ROOT,
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
            )
            engine.run(
                workflow,
                user_input,
                task_title=task_title,
                production_config=production_config,
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
                    summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
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
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                summary = {}
        return {"name": name, "summary": summary, "files": files}

    def _file_content(self, task: str, file_name: str) -> dict:
        target, _ = self._safe_task_file(task, file_name, must_exist=True)
        self._ensure_editable_file(target)
        return {"file": file_name, "content": target.read_text(encoding="utf-8", errors="replace")}

    def _save_file(self, payload: dict) -> dict:
        task = str(payload.get("task") or "").strip()
        file_name = str(payload.get("file") or "").strip()
        content = str(payload.get("content") or "")
        target, task_dir = self._safe_task_file(task, file_name, must_exist=True)
        self._ensure_editable_file(target)
        target.write_text(content.rstrip() + "\n", encoding="utf-8")
        return {"ok": True, "file": target.relative_to(task_dir).as_posix()}

    def _rebuild_final_output(self, task: str) -> dict:
        task_dir = self._safe_task_dir(task)
        workflow_path = task_dir / "workflow.json"
        input_path = task_dir / "input.md"
        if not workflow_path.is_file():
            raise FileNotFoundError("workflow.json")
        if not input_path.is_file():
            raise FileNotFoundError("input.md")
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        user_input = input_path.read_text(encoding="utf-8", errors="replace")
        step_outputs = WorkflowEngine._collect_step_outputs(workflow, task_dir)
        final_output = WorkflowEngine._build_final_output(workflow, user_input, step_outputs)
        final_path = task_dir / "final_output.md"
        final_path.write_text(final_output, encoding="utf-8")
        return {"ok": True, "file": final_path.relative_to(task_dir).as_posix()}

    def _rerun_step(self, payload: dict) -> dict:
        task = str(payload.get("task") or "").strip()
        step = int(payload.get("step") or 0)
        if step <= 0:
            raise ValueError("step is required")
        task_dir = self._safe_task_dir(task)
        engine = WorkflowEngine(
            WORKSPACE_ROOT,
            provider=str(payload.get("provider") or "auto").strip(),
            model=str(payload.get("model") or "").strip() or None,
            api_key=str(payload.get("api_key") or "").strip() or None,
            base_url=str(payload.get("base_url") or "").strip() or None,
            timeout=int(payload.get("timeout") or 0) or None,
        )
        result = engine.rerun_step(task_dir, step)
        result["ok"] = True
        return result

    def _export_task(self, payload: dict) -> dict:
        task = str(payload.get("task") or "").strip()
        template = str(payload.get("template") or "").strip()
        task_dir = self._safe_task_dir(task)
        export_dir = task_dir / "export_package"
        export_dir.mkdir(parents=True, exist_ok=True)

        summary = {}
        summary_path = task_dir / "run_summary.json"
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8-sig"))
            except json.JSONDecodeError:
                summary = {}
        workflow_name = str(summary.get("workflow") or "")
        if not template:
            template = self._infer_export_template(workflow_name)

        final_output = self._read_task_text(task_dir, "final_output.md")
        input_text = self._read_task_text(task_dir, "input.md")
        step_outputs = self._read_all_step_outputs(task_dir)
        files = self._write_export_files(export_dir, template, workflow_name, input_text, final_output, step_outputs)
        return {
            "ok": True,
            "template": template,
            "export_dir": export_dir.relative_to(task_dir).as_posix(),
            "files": [path.relative_to(task_dir).as_posix() for path in files],
        }

    @staticmethod
    def _ensure_editable_file(path: Path) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "text/plain"
        editable_suffixes = {".json", ".md", ".txt", ".csv", ".srt", ".log"}
        if not content_type.startswith("text/") and path.suffix.lower() not in editable_suffixes:
            raise ValueError(f"Unsupported file type: {path.name}")

    @staticmethod
    def _infer_export_template(workflow_name: str) -> str:
        if "小红书" in workflow_name:
            return "xiaohongshu"
        if "游戏" in workflow_name or "Steam" in workflow_name:
            return "game_steam"
        if "软件市场" in workflow_name:
            return "software_market"
        if "员工" in workflow_name or "平台" in workflow_name:
            return "agent_platform"
        return "short_video"

    @staticmethod
    def _read_task_text(task_dir: Path, relative: str) -> str:
        path = task_dir / relative
        return path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""

    @staticmethod
    def _read_all_step_outputs(task_dir: Path) -> list[dict]:
        outputs = []
        for path in sorted(task_dir.glob("step_*/output.md")):
            step_match = path.parent.name.split("_", 2)
            outputs.append(
                {
                    "step": step_match[1] if len(step_match) > 1 else "",
                    "agent": step_match[2] if len(step_match) > 2 else path.parent.name,
                    "file": path.relative_to(task_dir).as_posix(),
                    "content": path.read_text(encoding="utf-8", errors="replace"),
                }
            )
        return outputs

    def _write_export_files(
        self,
        export_dir: Path,
        template: str,
        workflow_name: str,
        input_text: str,
        final_output: str,
        step_outputs: list[dict],
    ) -> list[Path]:
        written: list[Path] = []

        def write(name: str, content: str) -> None:
            path = export_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content.rstrip() + "\n", encoding="utf-8")
            written.append(path)

        manifest = {
            "template": template,
            "workflow": workflow_name,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "files": [],
        }

        write("README.md", self._export_readme(template, workflow_name))
        write("final_output.md", final_output or "# 最终输出\n\n暂无 final_output.md。\n")

        if template == "short_video":
            write("视频制作包.md", final_output)
            write("字幕.srt", self._extract_srt_from_text(final_output))
            write("镜头清单.csv", self._shot_csv(step_outputs))
            write("生图提示词.json", json.dumps(self._prompt_json(step_outputs, "06_"), ensure_ascii=False, indent=2))
            write("视频提示词.json", json.dumps(self._prompt_json(step_outputs, "07_"), ensure_ascii=False, indent=2))
            write("语音字幕制作包.md", self._agent_output_text(step_outputs, "20_"))
            write("ComfyUI素材编排.md", self._agent_output_text(step_outputs, "21_"))
            write("ComfyUI参数包.json", json.dumps(self._prompt_json(step_outputs, "21_"), ensure_ascii=False, indent=2))
            write("剪辑成片执行方案.md", self._agent_output_text(step_outputs, "22_"))
        elif template == "xiaohongshu":
            write("小红书文案.md", final_output)
            write("标题列表.txt", self._extract_lines(final_output, ["标题", "选题"]))
            write("封面文案.txt", self._extract_lines(final_output, ["封面"]))
            write("发布检查清单.md", self._checklist("小红书图文"))
        elif template == "game_steam":
            write("GDD.md", final_output)
            write("Unity开发任务清单.md", self._extract_lines(final_output, ["Unity", "开发", "任务", "架构"]))
            write("Steam商店页文案.md", self._extract_lines(final_output, ["Steam", "商店", "愿望单"]))
            write("测试发行清单.md", self._checklist("Steam 游戏"))
        elif template == "software_market":
            write("软件机会排行榜.md", final_output)
            write("MVP验证计划.md", self._extract_lines(final_output, ["MVP", "验证", "获客", "风险"]))
            write("商业化假设.md", self._extract_lines(final_output, ["商业化", "定价", "付费"]))
        elif template == "agent_platform":
            write("产品需求文档.md", final_output)
            write("员工管理方案.md", self._extract_lines(final_output, ["员工", "管理", "权限"]))
            write("工作流架构.md", self._extract_lines(final_output, ["工作流", "状态机", "上下文"]))
            write("技术落地清单.md", self._extract_lines(final_output, ["技术", "架构", "API", "本地"]))
        else:
            write("产品包.md", final_output)

        write("原始需求.md", input_text)
        manifest["files"] = [path.name for path in written]
        write("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        return written

    @staticmethod
    def _export_readme(template: str, workflow_name: str) -> str:
        return "\n".join(
            [
                "# 产品导出包",
                "",
                f"- 类型：{template}",
                f"- 工作流：{workflow_name or '未知'}",
                "- 用途：把工作流输出整理成可继续制作、复制或交付的文件。",
                "",
                "建议先检查 `final_output.md`，再按具体产品类型查看拆分文件。",
            ]
        )

    @staticmethod
    def _extract_srt_from_text(text: str) -> str:
        import re

        match = re.search(r"```srt\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip() + "\n"
        return "1\n00:00:00,000 --> 00:00:03,000\n请根据视频制作包补充字幕。\n"

    @staticmethod
    def _shot_csv(step_outputs: list[dict]) -> str:
        rows = ['step,agent,file,summary']
        for item in step_outputs:
            summary = " ".join(str(item.get("content", "")).split())[:160].replace('"', '""')
            rows.append(f'{item.get("step","")},{item.get("agent","")},{item.get("file","")},"{summary}"')
        return "\n".join(rows)

    @staticmethod
    def _prompt_json(step_outputs: list[dict], agent_prefix: str) -> list[dict]:
        return [
            {
                "step": item.get("step"),
                "agent": item.get("agent"),
                "source_file": item.get("file"),
                "content": item.get("content", ""),
            }
            for item in step_outputs
            if str(item.get("agent", "")).startswith(agent_prefix)
        ]

    @staticmethod
    def _agent_output_text(step_outputs: list[dict], agent_prefix: str) -> str:
        for item in step_outputs:
            if str(item.get("agent", "")).startswith(agent_prefix):
                return str(item.get("content", "")).strip() + "\n"
        return f"# {agent_prefix} 输出\n\n当前任务没有找到该员工输出。\n"

    @staticmethod
    def _extract_lines(text: str, keywords: list[str]) -> str:
        lines = []
        for line in text.splitlines():
            if any(keyword in line for keyword in keywords):
                lines.append(line)
        if not lines:
            return text[:4000] if text else "暂无可提取内容。"
        return "\n".join(lines)

    @staticmethod
    def _checklist(name: str) -> str:
        return "\n".join(
            [
                f"# {name}交付检查清单",
                "",
                "- [ ] 需求和目标用户清楚",
                "- [ ] 核心内容可直接复制使用",
                "- [ ] 风险和待确认项已标记",
                "- [ ] 文件命名和版本可追踪",
                "- [ ] 已人工复核最终交付内容",
            ]
        )

    @staticmethod
    def _append_image_config(user_input: str, image_config: dict) -> str:
        def value(key: str, default: str = "未填写") -> str:
            item = image_config.get(key)
            return str(item).strip() if item not in (None, "") else default

        return (
            f"{user_input}\n\n"
            "## 生图配置\n"
            f"- 正向提示词：{value('positive_prompt')}\n"
            "- 参考图：如用户上传参考图，请优先按参考图说明保持人物、产品、风格或构图一致。\n"
            "- 参数来源：尺寸、模型、seed、steps、CFG、采样器、负向词等由 ComfyUI/RunningHub 工作流或导入的 API JSON 节点映射配置，不需要在员工输出中重复询问。\n"
            "- 执行要求：当前阶段由 06_分镜生图设计师输出分镜总表、关键帧正向提示词、参考图使用策略和连续性控制说明；不要声称已经生成图片文件。\n"
        )

    @staticmethod
    def _append_video_config(user_input: str, video_config: dict) -> str:
        def value(key: str, default: str = "未填写") -> str:
            item = video_config.get(key)
            return str(item).strip() if item not in (None, "") else default

        return (
            f"{user_input}\n\n"
            "## 视频生成配置\n"
            f"- 正向提示词：{value('positive_prompt')}\n"
            "- 参考图：如用户上传参考图，请把它作为首帧、角色一致性、产品一致性或风格参考来规划。\n"
            "- 参数来源：模型、画幅、时长、运动强度、镜头、seed、FPS、分辨率、负向词等由视频/ComfyUI 工作流或导入的 API JSON 节点映射配置，不需要在员工输出中重复询问。\n"
            "- 执行要求：当前阶段由 06_分镜生图设计师输出分镜生图方案，07_视频生成执行员输出视频画面正向提示词和镜头清单，20_语音字幕包装师输出 TTS、SRT、BGM 和音效方案，21_ComfyUI成片编排师整理一体化成片参数；不要声称已经生成 mp4。\n"
        )

    @staticmethod
    def _append_comfyui_config(user_input: str, production_config: dict, compose_config: dict) -> str:
        def value(key: str, default: str = "未填写") -> str:
            item = compose_config.get(key)
            return str(item).strip() if item not in (None, "") else default

        mode = str(production_config.get("mode") or "off").strip()
        api_note = "已填写，运行时可调用，不保存密钥" if compose_config.get("api_key_provided") else "未填写"
        base_url_note = "已填写，运行时可调用，不保存地址到输出" if compose_config.get("base_url_provided") else "未填写"
        node_info = str(compose_config.get("node_info_list_json") or "").strip()
        node_note = "已填写节点映射 JSON" if node_info and node_info != "[]" else "未填写，需后续按实际 ComfyUI 节点补齐"
        return (
            f"{user_input}\n\n"
            "## ComfyUI 素材/预览配置\n"
            f"- 自动生成模式：{mode or 'off'}\n"
            f"- 剪辑/合成工具：{value('tool', 'ffmpeg')}\n"
            f"- 成片工作流接口：{value('workflow_endpoint')}\n"
            f"- 成片平台密钥：{api_note}\n"
            f"- 成片平台接口地址：{base_url_note}\n"
            f"- 节点映射：{node_note}\n"
            f"- 轮询超时：{value('poll_timeout_seconds', '3600')} 秒\n"
            "- 执行要求：21_ComfyUI素材编排师需要输出可映射到 ComfyUI/RunningHub 的素材参数包；AI 图片和视频只是片段素材，最终剪辑成片交给 22_剪辑成片执行师。\n"
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

    def _upload_voice_sample(self, payload: dict) -> dict:
        filename = str(payload.get("filename") or "").strip()
        content_base64 = str(payload.get("content_base64") or "").strip()
        if not filename or not content_base64:
            raise ValueError("filename and content_base64 are required")

        suffix = Path(filename).suffix.lower()
        allowed = {".wav", ".mp3", ".m4a", ".flac", ".ogg"}
        if suffix not in allowed:
            raise ValueError(f"Unsupported voice sample type: {suffix}")

        audio_bytes = base64.b64decode(content_base64, validate=True)
        if len(audio_bytes) > 50 * 1024 * 1024:
            raise ValueError("Voice sample is too large; max size is 50 MB")

        VOICE_SAMPLE_ROOT.mkdir(parents=True, exist_ok=True)
        safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in Path(filename).stem)[:80]
        target = VOICE_SAMPLE_ROOT / f"{safe_stem}_{uuid4().hex[:8]}{suffix}"
        target.write_bytes(audio_bytes)

        relative_path = target.relative_to(WORKSPACE_ROOT).as_posix()
        return {
            "filename": filename,
            "stored_path": relative_path,
            "size_bytes": len(audio_bytes),
        }

    def _knowledge_files(self) -> list[dict]:
        if not KNOWLEDGE_ROOT.exists():
            return []

        files = []
        for path in sorted(KNOWLEDGE_ROOT.iterdir()):
            if not path.is_file() or path.name == ".gitignore":
                continue
            if path.suffix.lower() not in {".md", ".txt", ".json", ".csv"}:
                continue
            stat = path.stat()
            files.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                }
            )
        return files

    def _upload_knowledge(self, payload: dict) -> dict:
        filename = str(payload.get("filename") or "").strip()
        content_base64 = str(payload.get("content_base64") or "").strip()
        if not filename or not content_base64:
            raise ValueError("filename and content_base64 are required")

        suffix = Path(filename).suffix.lower()
        allowed = {".md", ".txt", ".json", ".csv"}
        if suffix not in allowed:
            raise ValueError(f"Unsupported knowledge file type: {suffix}")

        content_bytes = base64.b64decode(content_base64, validate=True)
        if len(content_bytes) > 5 * 1024 * 1024:
            raise ValueError("Knowledge file is too large; max size is 5 MB")
        content_bytes.decode("utf-8")

        KNOWLEDGE_ROOT.mkdir(parents=True, exist_ok=True)
        safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in Path(filename).stem)[:80]
        target = KNOWLEDGE_ROOT / f"{safe_stem}{suffix}"
        if target.exists():
            target = KNOWLEDGE_ROOT / f"{safe_stem}_{uuid4().hex[:8]}{suffix}"
        target.write_bytes(content_bytes)
        return {"ok": True, "name": target.name, "size_bytes": len(content_bytes)}

    def _test_model(self, payload: dict) -> dict:
        api_key = str(payload.get("api_key") or "").strip()
        base_url = str(payload.get("base_url") or "https://api.openai.com/v1").strip().rstrip("/")
        model = str(payload.get("model") or "").strip()
        if not api_key:
            raise ValueError("API Key is required for model test")
        if not base_url:
            raise ValueError("Base URL is required for model test")
        if not model:
            raise ValueError("model is required for model test")

        body = json.dumps(
            {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 8,
            }
        ).encode("utf-8")
        req = urllib_request.Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=20) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            raise ValueError(f"HTTP {exc.code}: {detail}") from exc
        except urllib_error.URLError as exc:
            raise ValueError(f"连接失败：{exc.reason}") from exc

        data = json.loads(raw)
        return {"ok": True, "model": model, "id": data.get("id", "")}

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
        context = self._long_term_memory_context()
        if not context:
            return user_input
        return f"{user_input}\n\n## 长期记忆\n{context}\n"

    def _long_term_memory_context(self) -> str:
        if not MEMORY_ROOT.exists():
            return ""

        sections = []
        for path in sorted(MEMORY_ROOT.glob("*.md")):
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                sections.append(f"### {path.name}\n{content}")

        if not sections:
            return ""
        return "\n\n".join(sections)

    def _append_knowledge_base(self, user_input: str) -> str:
        if not KNOWLEDGE_ROOT.exists():
            return user_input

        sections = []
        remaining = 20000
        for path in sorted(KNOWLEDGE_ROOT.iterdir()):
            if not path.is_file() or path.name == ".gitignore":
                continue
            if path.suffix.lower() not in {".md", ".txt", ".json", ".csv"}:
                continue
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if not content:
                continue
            clipped = content[:remaining]
            sections.append(f"### {path.name}\n{clipped}")
            remaining -= len(clipped)
            if remaining <= 0:
                break

        if not sections:
            return user_input
        return f"{user_input}\n\n## 本地知识库\n" + "\n\n".join(sections) + "\n"

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

    def _safe_task_file(self, task: str, file_name: str, must_exist: bool) -> tuple[Path, Path]:
        if not file_name or file_name.startswith("/") or file_name.startswith("\\"):
            raise ValueError("Invalid file name")
        task_dir = self._safe_task_dir(task)
        target = (task_dir / file_name).resolve()
        task_root = task_dir.resolve()
        if not self._is_relative_to(target, task_root):
            raise ValueError("Invalid task file path")
        if must_exist and not target.is_file():
            raise FileNotFoundError(file_name)
        return target, task_dir

    def _safe_workflow_path(self, name: str, must_exist: bool) -> Path:
        if not name:
            raise ValueError("Invalid workflow name")
        candidate = name.strip()
        if candidate.endswith(".json"):
            candidate = candidate[:-5]
        if not candidate or "/" in candidate or "\\" in candidate or candidate in {".", ".."}:
            raise ValueError("Invalid workflow name")
        path = (WORKFLOW_ROOT / f"{candidate}.json").resolve()
        workflow_root = WORKFLOW_ROOT.resolve()
        if not self._is_relative_to(path, workflow_root):
            raise ValueError("Invalid workflow path")
        if must_exist and not path.is_file():
            raise FileNotFoundError(name)
        return path

    def _safe_staff_dir(self, name: str, must_exist: bool) -> Path:
        if not name or "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError("Invalid staff name")
        staff_dir = (STAFF_ROOT / name).resolve()
        staff_root = STAFF_ROOT.resolve()
        if not self._is_relative_to(staff_dir, staff_root):
            raise ValueError("Invalid staff path")
        if must_exist and not staff_dir.is_dir():
            raise FileNotFoundError(name)
        return staff_dir

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
