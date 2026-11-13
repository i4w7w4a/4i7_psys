#!/usr/bin/env python3
"""Проверка базовых структурных инвариантов исследовательской карты 4i7."""

import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
data = json.loads((root / "model" / "4i7.json").read_text(encoding="utf-8"))
directions = ["front", "right", "back", "left"]
pillars = data["pillars"]
assert data["observer"]["not_a_pillar"] is True
assert [d["id"] for d in data["directions"]] == directions
assert [p["id"] for p in pillars] == list(range(1, 8))
position_ids = set()
for p in pillars:
    assert list(p["side_positions"]) == directions, p["node_id"]
    assert (root / p["document"]).is_file(), p["document"]
    for direction, position in p["side_positions"].items():
        assert position["id"] == f"P{p['id']}.{direction}"
        assert position["status"] in ("unassigned", "hypothesis", "author-approved")
        assert position["id"] not in position_ids
        position_ids.add(position["id"])
assert len(position_ids) == 28
nodes = {f"P{n}" for n in range(1, 8)}
for relation in data["relations"]:
    assert relation["source"] in nodes
    assert relation["target"] in nodes
for name, route in data["route_examples"].items():
    if name.endswith("_start_front"):
        assert len(route) == 7
        assert all(pos in position_ids for pos in route)
        assert [p.split(".")[0] for p in route] == [f"P{n}" for n in range(1, 8)]
print("OK: 1 observer, 7 pillars, 4 directions, 28 unique positions, valid references")
