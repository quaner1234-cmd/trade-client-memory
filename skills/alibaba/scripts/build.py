#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the release package for the Alibaba skill and audit it.

Usage:  python build.py [outdir]

The package deliberately EXCLUDES tests/: test_offline.py carries fake-leak
fixtures (example.org / made-up tokens) used to prove `audit` detects leaks.
Shipping those strings would make the package fail its own privacy scan.
Everything else — including tests/ being absent — is intentional: the
released skill must scan clean with zero exceptions.
"""
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent   # skill root
ROOT = SRC.parent
PY = sys.executable

# Shipped content. tests/ intentionally NOT included (see docstring).
PARTS = ["SKILL.md", "README.md", "references", "scripts", "samples"]


def main(outdir=None):
    out = Path(outdir) if outdir else (ROOT.parent / "dist-alibaba")
    stage = out / "trade-client-memory-alibaba"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    for rel in PARTS:
        s = SRC / rel
        if s.is_dir():
            shutil.copytree(s, stage / rel,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        elif s.is_file():
            shutil.copy2(s, stage / rel)
        else:
            print(f"⚠️  缺失 {rel}")
    lic = ROOT / "LICENSE"
    if lic.exists():
        shutil.copy2(lic, stage / "LICENSE")

    files = sorted(p.relative_to(stage) for p in stage.rglob("*") if p.is_file())
    print(f"打包 {len(files)} 个文件 → {stage}")
    for f in files:
        print("   ", f)

    print("\n运行发布包隐私扫描…")
    r = subprocess.run([PY, str(stage / "scripts" / "tcm_ali.py"), "--base", str(out),
                        "audit", "--audit-path", str(stage)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        print("\n❌ 发布包未通过隐私扫描，不要发布。")
        return 1
    print("\n✅ 发布包通过隐私扫描。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
