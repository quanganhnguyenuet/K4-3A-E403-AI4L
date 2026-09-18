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
from lesson_pipeline import build_validated_structure, extract_document, generate_lesson_from_structure, validate_draft


def metadata(index: int, filename: str) -> tuple[str, str, str]:
    stem = re.sub(r"\s*\(\d+\)$", "", Path(filename).stem).strip()
    match = re.match(r"Lecture\s+(\d+(?:\.\d+)?)", stem, flags=re.I)
    lecture = f"Lecture {match.group(1)}" if match else stem
    title = f"Day {index} · {stem}"
    # Keep the original filename visible in the lesson description, including duplicated uploads.
    return f"day-{index}-{lecture.lower().replace(' ', '-').replace('.', '-')}", title, f"Day {index}"


def repair_evidence_quotes(lesson: dict, segments: list[dict]) -> None:
    """The model selects source IDs semantically; preserve exact source text for validation."""
    source_by_id = {segment["id"]: segment["text"] for segment in segments}
    for point in lesson.get("points", []):
        for evidence in point.get("support_evidence", []):
            source = source_by_id.get(evidence.get("source_id"), "")
            quote = str(evidence.get("quote", "")).strip()
            if source and (len(quote) < 12 or quote not in source):
                evidence["quote"] = source[: min(280, len(source))]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replace", action="store_true", help="replace knowledge/lesson_catalog.json")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()
    files = sorted(list((ROOT / "data").glob("*.pptx")) + list((ROOT / "data").glob("*.pdf")))
    if not files:
        raise SystemExit("Không tìm thấy PDF/PPTX trong data")
    provider = OpenAIResponsesProvider(model=args.model)
    lessons = []
    lessons_by_hash = {}
    for index, path in enumerate(files, 1):
        print(f"Generating {index}/{len(files)}: {path.name}", flush=True)
        parsed = extract_document(path.name, path.read_bytes())
        segments, units = parsed["segments"], parsed["units"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lesson_id, title, track = metadata(index, path.name)
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
            validated, errors = validate_draft({"lesson": lesson}, segments, {item["id"] for item in lessons})
            if errors or validated is None:
                raise RuntimeError("; ".join(errors))
        except RuntimeError as exc:
            print(f"  BỎ QUA file này vì lỗi: {exc}", flush=True)
            continue
        lessons.append(validated)
        lessons_by_hash[digest] = copy.deepcopy(validated)
    if not lessons:
        raise SystemExit("Không có lesson nào hợp lệ được tạo ra")
    payload = {"schema_version": "2.0", "lessons": lessons}
    output = ROOT / "knowledge" / "lesson_catalog.json"
    if not args.replace:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(lessons)} lessons to {output}")


if __name__ == "__main__":
    main()
