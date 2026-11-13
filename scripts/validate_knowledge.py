#!/usr/bin/env python3
"""Проверка целостности файловой памяти 4i7 без внешних пакетов."""

import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
idx = json.loads((root / "knowledge/index.json").read_text(encoding="utf-8"))
model = json.loads((root / "model/4i7.json").read_text(encoding="utf-8"))
assert idx["schema_version"] == "0.1.0"
statuses = {"captured", "under_review", "adopted", "rejected", "superseded"}
origins = {"author_intent", "book", "article", "code", "experiment", "discussion", "other"}
claims = {"requirement", "observation", "hypothesis", "proposal", "interpretation"}
nodes = {"i", "MODEL"} | {p["node_id"] for p in model["pillars"]}
nodes |= {v["id"] for p in model["pillars"] for v in p["side_positions"].values()}
ids = set()
for e in idx["entries"]:
    k = e["id"]
    assert re.fullmatch(r"KN-[0-9]{8}-[0-9]{3,}", k), k
    assert k not in ids, k
    ids.add(k)
    p = (root / e["path"]).resolve()
    assert p.is_relative_to(root.resolve()) and p.suffix == ".json" and p.is_file(), e["path"]
    v = json.loads(p.read_text(encoding="utf-8"))
    assert v["id"] == k and v["status"] in statuses, k
    assert v["claim_type"] in claims and v["origin_kind"] in origins, k
    assert v["statement"].strip(), k
    assert v["source"]["label"].strip() and v["source"]["reference"].strip(), k
    assert all(t in nodes for t in v["impacted_nodes"]), k
    assert all(link["kind"] in {"supports", "contradicts", "refines", "supersedes"} for link in v.get("links", [])), k
    if v["status"] == "adopted":
        assert v.get("decision_ref"), k
    if v["status"] == "superseded":
        assert v.get("superseded_by"), k
print(f"OK: {len(ids)} knowledge records, model links and statuses")
