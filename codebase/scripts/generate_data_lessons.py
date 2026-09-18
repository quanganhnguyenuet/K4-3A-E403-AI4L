"""Generate a reviewed lesson catalog from every PDF/PPTX in ``data``.

Run from the repository root:
    python codebase/scripts/generate_data_lessons.py --replace
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "codebase"))
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")

from agent_core import OpenAIResponsesProvider
from lesson_pipeline import (
    build_validated_structure,
    extract_document,
    generate_lesson_from_structure,
    repair_evidence_quotes,
    validate_draft,
)


def metadata(index: int, filename: str) -> tuple[str, str, str]:
    stem = re.sub(r"\s*\(\d+\)$", "", Path(filename).stem).strip()
    match = re.match(r"Lecture\s+(\d+(?:\.\d+)?)", stem, flags=re.I)
    lecture = f"Lecture {match.group(1)}" if match else stem
    title = f"Day {index} · {stem}"
    # Keep the original filename visible in the lesson description, including duplicated uploads.
    return f"day-{index}-{lecture.lower().replace(' ', '-').replace('.', '-')}", title, f"Day {index}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--replace", action="store_true",
        help="write results into knowledge/lesson_catalog.json (merged with existing lessons, matched by id)",
    )
    parser.add_argument("--model", default=None)
    args = parser.parse_args()
    files = sorted(list((ROOT / "data").glob("*.pptx")) + list((ROOT / "data").glob("*.pdf")))
    if not files:
        raise SystemExit("Không tìm thấy PDF/PPTX trong data")
    output = ROOT / "knowledge" / "lesson_catalog.json"
    existing_lessons = []
    if output.is_file():
        existing_lessons = json.loads(output.read_text(encoding="utf-8")).get("lessons", [])
    existing_ids = {item["id"] for item in existing_lessons}
    provider = OpenAIResponsesProvider(model=args.model)
    lessons = []
    lessons_by_hash = {}
    for index, path in enumerate(files, 1):
        print(f"Generating {index}/{len(files)}: {path.name}", flush=True)
        parsed = extract_document(path.name, path.read_bytes())
        segments, units = parsed["segments"], parsed["units"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lesson_id, title, track = metadata(index, path.name)
        # This file's own generated id never counts as a collision with itself, since it is
        # about to replace whatever earlier version of this same lesson already exists.
        other_ids = (existing_ids | {item["id"] for item in lessons}) - {lesson_id}
        if digest in lessons_by_hash:
            lesson = copy.deepcopy(lessons_by_hash[digest])
            lesson["id"], lesson["title"], lesson["track"] = lesson_id, title, track
            lesson["description"] = f"Ôn tập từ file {path.name}."
            for source in lesson["sources"]:
                source["file"] = path.name
            lessons.append(lesson)
            print("  Reused generated content because this file is an exact duplicate.", flush=True)
            continue
        session_id = f"data-{index}"
        try:
            structure, unresolved = build_validated_structure(units, segments, provider, session_id=session_id)
            if unresolved:
                print("  Critic still has open issues after refinement:", flush=True)
                for item in unresolved:
                    print(f"    - {item}", flush=True)
            generated = generate_lesson_from_structure(structure, segments, provider, session_id=session_id)
            lesson = generated["lesson"]
            repair_evidence_quotes(lesson, segments)
            if len(lesson.get("points", [])) < 2:
                raise RuntimeError("lesson cần ít nhất 2 khái niệm lớn")
            lesson["id"], lesson["title"], lesson["track"] = lesson_id, title, track
            lesson["description"] = f"Ôn tập từ file {path.name}."
            validated, errors = validate_draft({"lesson": lesson}, segments, other_ids)
            if errors or validated is None:
                raise RuntimeError("; ".join(errors))
        except RuntimeError as exc:
            print(f"  BỎ QUA file này vì lỗi: {exc}", flush=True)
            continue
        lessons.append(validated)
        lessons_by_hash[digest] = copy.deepcopy(validated)
    if not lessons:
        raise SystemExit("Không có lesson nào hợp lệ được tạo ra")
    if not args.replace:
        print(json.dumps({"schema_version": "2.0", "lessons": lessons}, ensure_ascii=False, indent=2))
        return
    # Merge into the existing catalog instead of replacing it wholesale: a lesson generated
    # from data/ overwrites its own previous version (same id), everything else -- including
    # hand-authored lessons that this script never touches -- is left exactly as it was.
    generated_ids = {item["id"] for item in lessons}
    merged = [item for item in existing_lessons if item["id"] not in generated_ids] + lessons
    payload = {"schema_version": "2.0", "lessons": merged}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(merged)} lessons to {output} ({len(lessons)} generated just now, {len(merged) - len(lessons)} kept from before)")


if __name__ == "__main__":
    main()
