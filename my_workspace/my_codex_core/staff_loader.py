from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StaffAgent:
    agent_id: str
    name: str
    folder: Path
    prompt: str
    flow_rule: dict


class StaffLoader:
    def __init__(self, staff_root: Path) -> None:
        self.staff_root = staff_root

    def load_all(self) -> dict[str, StaffAgent]:
        agents: dict[str, StaffAgent] = {}
        if not self.staff_root.exists():
            raise FileNotFoundError(f"Staff root not found: {self.staff_root}")

        for folder in sorted(self.staff_root.iterdir()):
            if not folder.is_dir():
                continue

            agent_file = folder / "agent.md"
            rule_file = folder / "flow_rule.json"
            if not agent_file.exists() or not rule_file.exists():
                continue

            prompt = agent_file.read_text(encoding="utf-8")
            flow_rule = json.loads(rule_file.read_text(encoding="utf-8"))
            agent_id = flow_rule.get("agent_id") or folder.name
            name = flow_rule.get("agent_name") or agent_id

            agents[agent_id] = StaffAgent(
                agent_id=agent_id,
                name=name,
                folder=folder,
                prompt=prompt,
                flow_rule=flow_rule,
            )

        if not agents:
            raise ValueError(f"No staff agents found in: {self.staff_root}")
        return agents

    @staticmethod
    def resolve_agent(agents: dict[str, StaffAgent], requested: str) -> StaffAgent:
        if requested in agents:
            return agents[requested]

        for agent_id, agent in agents.items():
            if requested == agent.name or requested in agent_id or agent.name in requested:
                return agent

        available = ", ".join(sorted(agents))
        raise KeyError(f"Agent not found: {requested}. Available: {available}")
