#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""문서 동기화 점검 — 기능이 바뀌었는데 문서가 못 따라온 곳을 기계적으로 잡는다.

v1.5.1의 교훈: MCP 도구 목록, 저장소 구조 표, 검증 절차 같은 '파생 문서'는
기능을 고칠 때 조용히 낡는다. 릴리스 전에 이 스크립트를 돌려 어긋남을 찾는다.

점검 항목
  1. mcp_server.py 의 @mcp.tool() 함수가 README 에 모두 언급되는가
  2. 저장소 루트의 모든 *.py 가 AGENTS.md 구조 표에 있는가
  3. kinderinfo.__version__ 이 CHANGELOG 최신 항목과 일치하는가
  4. CLI 하위명령이 README 와 스킬 원본에 모두 언급되는가

사용: python check_docs.py   (어긋나면 종료코드 1)
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    skill = (ROOT / "skill" / "kaic-kinder-info" / "SKILL.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    server = (ROOT / "mcp_server.py").read_text(encoding="utf-8")
    cli = (ROOT / "kinderinfo.py").read_text(encoding="utf-8")
    errors = []

    tools = re.findall(r"@mcp\.tool\(\)\s*\ndef\s+(\w+)", server)
    for t in tools:
        if t not in readme:
            errors.append(f"README.md: MCP 도구 '{t}' 언급 없음")

    for f in sorted(ROOT.glob("*.py")):
        if f.name not in agents:
            errors.append(f"AGENTS.md: 구조 표에 '{f.name}' 없음")

    m_ver = re.search(r'__version__\s*=\s*"([^"]+)"', cli)
    m_log = re.search(r"## \[([\d.]+)\]", changelog)
    ver = m_ver.group(1) if m_ver else "?"
    latest = m_log.group(1) if m_log else "?"
    if ver != latest:
        errors.append(f"버전 불일치: kinderinfo.__version__={ver}, CHANGELOG 최신={latest}")

    subs = re.findall(r'add_parser\("(\w+)"', cli)
    for s in subs:
        for doc, name in ((readme, "README.md"), (skill, "SKILL.md(원본)")):
            if s not in doc:
                errors.append(f"{name}: CLI 명령 '{s}' 언급 없음")

    if errors:
        print("❌ 문서가 기능을 따라오지 못했습니다:")
        for e in errors:
            print(f"  - {e}")
        print("\n고친 뒤 다시 실행하세요. (기능과 문서는 같은 커밋에서!)")
        return 1
    print(f"✅ 문서 동기화 정상 — MCP 도구 {len(tools)}종, CLI 명령 {len(subs)}종, "
          f"버전 {ver} 모두 문서와 일치합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
