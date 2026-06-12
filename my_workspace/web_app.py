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
      grid-template-columns: 1fr 180px 180px;
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
      display: grid;
      gap: 4px;
      width: 100%;
    }
    .item.active { border-color: var(--accent); box-shadow: inset 3px 0 0 var(--accent); }
    .item-title { font-weight: 650; overflow-wrap: anywhere; }
    .item-meta { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
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
            <input id="model" placeholder="默认 gpt-4.1-mini" />
          </label>
        </div>
        <label>原始需求
          <textarea id="userInput" placeholder="例如：我要做一条抖音短视频，推广 AI 自动化开发服务，目标客户是中小企业老板，目标是让客户私信咨询。"></textarea>
        </label>
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
      userInput: document.getElementById('userInput'),
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
        btn.innerHTML = `<span class="item-title">${task.workflow || task.name}</span><span class="item-meta">${task.name}</span>`;
        btn.onclick = () => selectTask(task.name);
        els.taskList.appendChild(btn);
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
      els.runBtn.disabled = true;
      setStatus('工作流运行中');
      try {
        const result = await api('/api/run', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            workflow: els.workflow.value,
            input,
            provider: els.provider.value,
            model: els.model.value.trim(),
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
    els.sampleBtn.onclick = () => {
      els.userInput.value = '我要做一条抖音短视频，推广 AI 自动化开发服务。目标客户是中小企业老板，他们想降本增效但不知道怎么落地。视频目标是让客户私信咨询，风格专业、直接、有案例感，不要夸大承诺。';
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
            if parsed.path != "/api/run":
                self.send_error(404)
                return

            payload = self._read_json()
            workflow = str(payload.get("workflow") or "").strip()
            user_input = str(payload.get("input") or "").strip()
            provider = str(payload.get("provider") or "auto").strip()
            model = str(payload.get("model") or "").strip() or None

            if not workflow:
                raise ValueError("workflow is required")
            if not user_input:
                raise ValueError("input is required")

            engine = WorkflowEngine(WORKSPACE_ROOT, provider=provider, model=model)
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
