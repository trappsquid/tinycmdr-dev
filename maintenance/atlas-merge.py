"""Merge curated notes into a host's atlas.md, keeping the generated half.

The atlas has two halves on purpose. `## host` and `## layout` are generated on the host by
tinycmdr itself (`ensure_atlas()`, written only when the file is missing, so it never fights a
human). `## notes` is curated: facts a probe cannot know, from real incidents only.

This script rewrites ONLY the notes section, so a rollout can push curated notes to a host
without disturbing, or hand-copying, the generated half.

    python maintenance/atlas-merge.py <atlas.md> <notes.md>

notes.md is plain markdown: `#` lines and prose paragraphs are ignored, and only bullets
(plus their indented continuation lines) are merged, so a file's own explanation never
arrives in the atlas as facts.
"""
import sys
from pathlib import Path


def notes_from(text):
    """Bullets, plus indented continuation lines of a bullet. Prose between them is the file's
    own explanation to a human and must NOT become a note (it did once: an intro paragraph
    arrived in the atlas as three `note:` lines about the merge script itself)."""
    out, in_bullet = [], False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            in_bullet = False
            continue
        if line.startswith("#"):
            in_bullet = False
            continue
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            out.append("- " + stripped[2:].strip())
            in_bullet = True
            continue
        if in_bullet and line.startswith(("  ", "\t")):
            out[-1] += " " + stripped
            continue
        in_bullet = False
    return out


def merge(atlas_path, notes_path):
    atlas = Path(atlas_path)
    body = atlas.read_text(encoding="utf-8") if atlas.exists() else \
        "# Machine atlas\n\n## host\n\n## layout\n\n## notes\n"
    head, _, _old = body.partition("## notes")
    kept = head.rstrip("\n")
    merged = kept + "\n\n## notes\n" + "\n".join(notes_from(
        Path(notes_path).read_text(encoding="utf-8"))) + "\n"
    atlas.write_text(merged, encoding="utf-8", newline="\n")
    return len(merged)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    n = merge(sys.argv[1], sys.argv[2])
    print("merged %s into %s (%d chars)" % (sys.argv[2], sys.argv[1], n))
