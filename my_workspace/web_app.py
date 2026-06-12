from __future__ import annotations

import json
import mimetypes
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from my_codex_core.workflow_engine import WorkflowEngine


WORKSPACE_ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = WORKSPACE_ROOT / "my_task_output"
WORKFLOW_ROOT = WORKSPACE_ROOT / "my_workflows"
STAFF_ROOT = WORKSPACE_ROOT / "my_custom_staff"


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
      grid-template-columns: 1fr 160px 240px;
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
        <label>原始需求
          <textarea id="userInput" placeholder="例如：我要做一条抖音短视频，推广 AI 自动化开发服务，目标客户是中小企业老板，目标是让客户私信咨询。"></textarea>
        </label>
        <details open>
          <summary><strong>视频生成配置</strong> <span class="muted small">用于 06_视频生成执行员生成制作包</span></summary>
          <div class="video-grid" style="margin-top: 12px;">
            <label>视频工具
              <select id="videoTool">
                <option value="prompt_only" selected>仅生成提示词/制作包</option>
                <option value="sora">Sora</option>
                <option value="runway">Runway</option>
                <option value="pika">Pika</option>
                <option value="kling">可灵 Kling</option>
                <option value="jimeng">即梦 Jimeng</option>
                <option value="hailuo">海螺 Hailuo</option>
                <option value="luma">Luma</option>
                <option value="custom">其他/自定义</option>
              </select>
            </label>
            <label>视频模型
              <input id="videoModel" placeholder="例如 sora / runway gen-3 / kling 2.0，可留空" />
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
          <div class="provider-grid" style="margin-top: 12px;">
            <label>视频风格
              <input id="videoStyle" placeholder="例如 真人口播、科技感、写实商业、国风、美妆种草" />
            </label>
            <label>视频平台 API Key
              <input id="videoApiKey" type="password" autocomplete="off" spellcheck="false" placeholder="预留：当前不调用视频 API，不保存" />
            </label>
            <label>视频平台 Base URL
              <input id="videoBaseUrl" autocomplete="off" spellcheck="false" placeholder="预留：未来接入视频 API 使用，可留空" />
            </label>
          </div>
        </details>
        <div class="row">
          <button class="primary" id="runBtn">运行工作流</button>
          <button id="sampleBtn">填入示例</button>
          <span id="status" class="status">准备就绪</span>
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
      apiKey: document.getElementById('apiKey'),
      baseUrl: document.getElementById('baseUrl'),
      userInput: document.getElementById('userInput'),
      videoTool: document.getElementById('videoTool'),
      videoModel: document.getElementById('videoModel'),
      videoAspect: document.getElementById('videoAspect'),
      videoDuration: document.getElementById('videoDuration'),
      videoStyle: document.getElementById('videoStyle'),
      videoApiKey: document.getElementById('videoApiKey'),
      videoBaseUrl: document.getElementById('videoBaseUrl'),
      runBtn: document.getElementById('runBtn'),
      sampleBtn: document.getElementById('sampleBtn'),
      status: document.getElementById('status'),
      taskList: document.getElementById('taskList'),
      refreshTasks: document.getElementById('refreshTasks'),
      viewerTitle: document.getElementById('viewerTitle'),
      viewerMeta: document.getElementById('viewerMeta'),
      fileTabs: document.getElementById('fileTabs'),
      fileContent: document.getElementById('fileContent'),
    };
    let selectedTask = null;
    let selectedFile = null;

    function setStatus(text, isError = false) {
      els.status.textContent = text;
      els.status.classList.toggle('error', isError);
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
    }

    async function loadTasks() {
      const data = await api('/api/tasks');
      if (!data.tasks.length) {
        els.taskList.innerHTML = '<div class="muted small">暂无任务输出</div>';
        return;
      }
      els.taskList.innerHTML = '';
      for (const task of data.tasks) {
        const btn = document.createElement('button');
        btn.className = `item ${selectedTask === task.name ? 'active' : ''}`;
        btn.innerHTML = `<span class="item-main"><span class="item-title">${task.workflow || task.name}</span><span class="item-meta">${task.name}</span></span><span class="icon-btn danger" title="删除任务" aria-label="删除任务">×</span>`;
        btn.onclick = () => selectTask(task.name);
        btn.querySelector('.icon-btn').onclick = (event) => {
          event.stopPropagation();
          deleteTask(task.name);
        };
        els.taskList.appendChild(btn);
      }
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
      els.viewerTitle.textContent = data.summary.workflow || name;
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
      setStatus('工作流运行中');
      try {
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
            input,
            provider: els.provider.value,
            model,
            api_key: els.apiKey.value.trim(),
            base_url: els.baseUrl.value.trim(),
            video_config: videoConfig,
            video_api_key: els.videoApiKey.value.trim(),
            video_base_url: els.videoBaseUrl.value.trim(),
          }),
        });
        setStatus(`完成：${result.workflow_name}，${result.step_count} 步`);
        await loadTasks();
        await selectTask(result.task_name);
      } catch (err) {
        setStatus(err.message, true);
      } finally {
        els.runBtn.disabled = false;
      }
    }

    els.runBtn.onclick = runWorkflow;
    els.refreshTasks.onclick = loadTasks;
    els.model.onchange = () => {
      const custom = els.model.value === 'custom';
      els.customModel.disabled = !custom;
      if (custom) els.customModel.focus();
    };
    els.sampleBtn.onclick = () => {
      els.userInput.value = '我要做一条抖音短视频，推广 AI 自动化开发服务。目标客户是中小企业老板，他们想降本增效但不知道怎么落地。视频目标是让客户私信咨询，风格专业、直接、有案例感，不要夸大承诺。';
      els.videoTool.value = 'prompt_only';
      els.videoModel.value = '';
      els.videoAspect.value = '9:16';
      els.videoDuration.value = '30s';
      els.videoStyle.value = '真人口播，商业科技感，干净明亮';
    };

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

            if parsed.path != "/api/run":
                self.send_error(404)
                return

            workflow = str(payload.get("workflow") or "").strip()
            user_input = str(payload.get("input") or "").strip()
            video_config = payload.get("video_config") or {}
            if video_config:
                user_input = self._append_video_config(user_input, video_config)
            provider = str(payload.get("provider") or "auto").strip()
            model = str(payload.get("model") or "").strip() or None
            api_key = str(payload.get("api_key") or "").strip() or None
            base_url = str(payload.get("base_url") or "").strip() or None

            if not workflow:
                raise ValueError("workflow is required")
            if not user_input:
                raise ValueError("input is required")

            engine = WorkflowEngine(
                WORKSPACE_ROOT,
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
            )
            result = engine.run(workflow, user_input)
            task_name = Path(result.task_dir).name
            self._send_json(
                {
                    "task_name": task_name,
                    "task_dir": result.task_dir,
                    "workflow_name": result.workflow_name,
                    "provider": result.provider,
                    "step_count": result.step_count,
                    "final_output": result.final_output,
                }
            )
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
            "- 执行要求：当前阶段由 06_视频生成执行员输出视频生成提示词、镜头清单、TTS 配音稿、SRT 字幕草案和剪辑说明；不要声称已经生成 mp4。\n"
        )

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
