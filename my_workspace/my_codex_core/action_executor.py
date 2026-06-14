from __future__ import annotations

import json
import os
import re
import webbrowser
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib import request as urllib_request
from urllib.parse import urlparse


@dataclass
class ActionResult:
    action: str
    status: str
    target: str
    message: str


class ActionExecutor:
    """Execute a conservative subset of actions requested by agents.

    All paths are constrained to action_root. The executor intentionally starts
    small: directories, text files, JSON files, and a few browser-oriented
    actions. Shell commands, deletes, overwrites without explicit opt-in, paid
    API calls, and arbitrary filesystem access are left for a later approval
    layer.
    """

    SUPPORTED_ACTIONS = {
        "mkdir",
        "create_file",
        "write_json",
        "open_url",
        "fetch_url",
        "open_workspace_path",
    }
    MAX_FETCH_BYTES = 1_000_000

    def __init__(self, action_root: Path) -> None:
        self.action_root = action_root.resolve()
        self.action_root.mkdir(parents=True, exist_ok=True)

    def execute_from_text(self, text: str, task_dir: Path) -> list[dict[str, Any]]:
        actions = self.extract_actions(text)
        results = [asdict(self.execute(action)) for action in actions]
        if results:
            log_path = task_dir / "action_log.json"
            existing: list[dict[str, Any]] = []
            if log_path.exists():
                existing = json.loads(log_path.read_text(encoding="utf-8"))
            existing.extend(results)
            log_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return results

    def extract_actions(self, text: str) -> list[dict[str, Any]]:
        blocks = self._json_blocks(text)
        actions: list[dict[str, Any]] = []
        for block in blocks:
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and isinstance(data.get("actions"), list):
                actions.extend(item for item in data["actions"] if isinstance(item, dict))
            elif isinstance(data, list):
                actions.extend(item for item in data if isinstance(item, dict))
        return actions

    def execute(self, item: dict[str, Any]) -> ActionResult:
        action = str(item.get("action") or "").strip()
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        if action not in self.SUPPORTED_ACTIONS:
            return ActionResult(action=action or "unknown", status="skipped", target="", message="unsupported action")

        try:
            if action == "mkdir":
                target = self._safe_path(str(params.get("path") or ""))
                target.mkdir(parents=True, exist_ok=True)
                return ActionResult(action=action, status="done", target=str(target), message="directory created")

            if action == "create_file":
                target = self._safe_path(str(params.get("path") or ""))
                overwrite = bool(params.get("overwrite"))
                if target.exists() and not overwrite:
                    return ActionResult(action=action, status="blocked", target=str(target), message="file exists; overwrite not enabled")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(params.get("content") or ""), encoding="utf-8")
                return ActionResult(action=action, status="done", target=str(target), message="file written")

            if action == "write_json":
                target = self._safe_path(str(params.get("path") or ""))
                overwrite = bool(params.get("overwrite"))
                if target.exists() and not overwrite:
                    return ActionResult(action=action, status="blocked", target=str(target), message="file exists; overwrite not enabled")
                data = params.get("data")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return ActionResult(action=action, status="done", target=str(target), message="json written")

            if action == "open_url":
                url = self._safe_url(str(params.get("url") or ""))
                opened = webbrowser.open(url, new=2 if params.get("new_tab", True) else 0, autoraise=True)
                status = "done" if opened else "blocked"
                message = "browser open requested" if opened else "browser refused open request"
                return ActionResult(action=action, status=status, target=url, message=message)

            if action == "fetch_url":
                url = self._safe_url(str(params.get("url") or ""))
                target = self._safe_path(str(params.get("path") or self._default_fetch_path(url)))
                overwrite = bool(params.get("overwrite"))
                if target.exists() and not overwrite:
                    return ActionResult(action=action, status="blocked", target=str(target), message="file exists; overwrite not enabled")
                timeout = self._safe_timeout(params.get("timeout"))
                text = self._fetch_url_text(url, timeout=timeout)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text.rstrip() + "\n", encoding="utf-8")
                return ActionResult(action=action, status="done", target=str(target), message=f"web text saved from {url}")

            if action == "open_workspace_path":
                target = self._safe_path(str(params.get("path") or ""))
                if not target.exists():
                    return ActionResult(action=action, status="blocked", target=str(target), message="path does not exist")
                os.startfile(str(target))
                return ActionResult(action=action, status="done", target=str(target), message="workspace path open requested")
        except Exception as exc:
            return ActionResult(action=action, status="error", target=str(params.get("path") or ""), message=str(exc))

        return ActionResult(action=action, status="skipped", target="", message="no handler")

    def _safe_url(self, url_text: str) -> str:
        url = url_text.strip()
        if not url:
            raise ValueError("url is required")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("only http and https URLs are allowed")
        if not parsed.netloc:
            raise ValueError("url host is required")
        return url

    def _fetch_url_text(self, url: str, timeout: int) -> str:
        req = urllib_request.Request(
            url,
            headers={
                "User-Agent": "agency-agents-zh-action-executor/1.0",
                "Accept": "text/html,text/plain,application/json,*/*;q=0.8",
            },
            method="GET",
        )
        with urllib_request.urlopen(req, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            raw = response.read(self.MAX_FETCH_BYTES + 1)
        if len(raw) > self.MAX_FETCH_BYTES:
            raw = raw[: self.MAX_FETCH_BYTES]
        charset = self._charset_from_content_type(content_type)
        text = raw.decode(charset, errors="replace")
        if "html" in content_type.lower() or text.lstrip().lower().startswith(("<!doctype html", "<html")):
            text = _HTMLTextExtractor.to_text(text)
        return text

    @staticmethod
    def _safe_timeout(value: Any) -> int:
        try:
            timeout = int(value or 20)
        except (TypeError, ValueError):
            timeout = 20
        return min(max(timeout, 3), 60)

    @staticmethod
    def _charset_from_content_type(content_type: str) -> str:
        match = re.search(r"charset=([\w.-]+)", content_type, flags=re.IGNORECASE)
        return match.group(1) if match else "utf-8"

    @staticmethod
    def _default_fetch_path(url: str) -> str:
        parsed = urlparse(url)
        host = re.sub(r"[^A-Za-z0-9._-]+", "_", parsed.netloc).strip("_") or "page"
        path = re.sub(r"[^A-Za-z0-9._-]+", "_", parsed.path).strip("_")
        name = f"{host}_{path}" if path else host
        return f"web/{name[:120]}.txt"

    def _safe_path(self, path_text: str) -> Path:
        if not path_text:
            raise ValueError("path is required")
        if "\x00" in path_text:
            raise ValueError("invalid path")
        path = Path(path_text)
        if path.is_absolute():
            raise ValueError("absolute paths are not allowed")
        target = (self.action_root / path).resolve()
        try:
            target.relative_to(self.action_root)
        except ValueError as exc:
            raise ValueError("path escapes action workspace") from exc
        return target

    @staticmethod
    def _json_blocks(text: str) -> list[str]:
        blocks = re.findall(r"```json\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            blocks.append(stripped)
        return blocks


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    @classmethod
    def to_text(cls, html: str) -> str:
        parser = cls()
        parser.feed(html)
        lines = [line.strip() for line in "".join(parser._chunks).splitlines()]
        text = "\n".join(line for line in lines if line)
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", text)).strip()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "h4"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self._chunks.append(text)
