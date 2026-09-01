#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""스킬 배포 스크립트.

skill/kaic-kinder-info/ (원본)를 Claude Code와 Codex의 스킬 폴더에 복사한다.
복사하면서 {{TOOL_DIR}} 자리표시자를 이 저장소의 실제 절대 경로로 바꾼다.
덕분에 저장소에는 개인 경로가 남지 않고, 배포본은 바로 실행 가능한 경로를 갖는다.

스킬을 수정하면 원본(skill/kaic-kinder-info/SKILL.md)만 고치고 이 스크립트를 다시 실행하면 된다.
"""
import shutil
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "skill" / "kaic-kinder-info"
TARGETS = [
    Path.home() / ".claude" / "skills" / "kaic-kinder-info",
    Path.home() / ".codex" / "skills" / "kaic-kinder-info",
]

if not (SRC / "SKILL.md").exists():
    sys.exit(f"[오류] 스킬 원본이 없습니다: {SRC}")

src_files = {p.relative_to(SRC) for p in SRC.rglob("*") if p.is_file()}

for target in TARGETS:
    target.mkdir(parents=True, exist_ok=True)
    # 원본에서 사라진 파일만 골라 지운다. 폴더 자체는 지우지 않는다 —
    # 에이전트가 스킬을 로드 중이면 Windows 가 폴더 삭제를 거부한다.
    for path in target.rglob("*"):
        if path.is_file() and path.relative_to(target) not in src_files:
            path.unlink()
    shutil.copytree(SRC, target, dirs_exist_ok=True)
    for path in target.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("{{TOOL_DIR}}", str(ROOT)), encoding="utf-8")
    print(f"배포 완료: {target}")

print(f"\n도구 경로: {ROOT}")
