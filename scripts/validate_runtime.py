#!/usr/bin/env python3
"""Validate matrix shape, prose identities and generated lossless documentation."""
import argparse
import json
from pathlib import Path
from context_pack import ROOT, MATRIX, DOC, validate

PATH = ROOT / 'docs/11-runtime-matrix.md'

def reference(matrix):
    # Canonical data-only view. Exact equality verifies all 56 positions and metadata, not interpretation.
    return '# Матрица runtime: проверяемое представление\n\nСгенерировано из `' + MATRIX + '`. Не редактировать вручную.\nСтатусы не повышены; это рабочая версия, не новый фундамент.\n\n```json\n' + json.dumps(matrix, ensure_ascii=False, indent=2).replace('`','\\u0060') + '\n```\n'

def main():
    p=argparse.ArgumentParser();p.add_argument('--write-reference',action='store_true');a=p.parse_args()
    matrix=validate(json.loads((ROOT/MATRIX).read_text()),(ROOT/DOC).read_text())
    expected=reference(matrix)
    if a.write_reference: PATH.write_text(expected,encoding='utf-8')
    if not PATH.exists() or PATH.read_text(encoding='utf-8') != expected:
        raise SystemExit('Runtime docs/JSON mismatch: regenerate scripts/validate_runtime.py --write-reference')
    print('Runtime matrix: 7×4×2, source/status checks and exact generated documentation match PASS')

if __name__=='__main__': main()
