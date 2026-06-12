from __future__ import annotations

import argparse
import sys
from pathlib import Path

from my_codex_core.workflow_engine import WorkflowEngine


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local my_workspace workflow.")
    parser.add_argument("--workflow", required=True, help="Workflow name, stem, or JSON file name.")
    parser.add_argument("--input", help="User request text.")
    parser.add_argument("--input-file", help="Path to a UTF-8 text file containing the user request.")
    parser.add_argument("--provider", choices=["auto", "offline", "openai"], default="auto")
    parser.add_argument("--model", help="Model name when provider=openai or OPENAI_API_KEY is set.")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    args = parse_args()
    workspace_root = Path(__file__).resolve().parent

    if args.input_file:
        user_input = Path(args.input_file).read_text(encoding="utf-8")
    elif args.input:
        user_input = args.input
    else:
        user_input = sys.stdin.read()

    user_input = user_input.strip()
    if not user_input:
        print("ERROR: provide --input, --input-file, or stdin content.", file=sys.stderr)
        return 2

    engine = WorkflowEngine(workspace_root=workspace_root, provider=args.provider, model=args.model)
    result = engine.run(args.workflow, user_input)

    print(f"workflow: {result.workflow_name}")
    print(f"provider: {result.provider}")
    print(f"steps: {result.step_count}")
    print(f"task_dir: {result.task_dir}")
    print(f"final_output: {result.final_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
