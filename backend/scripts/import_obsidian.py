"""One-time Obsidian -> Atelier importer (reworked for the REORGANIZED vault).

Usage (from backend/):

    python -m scripts.import_obsidian --vault PATH                # DRY RUN (default)
    python -m scripts.import_obsidian --vault PATH \
        --api-base http://localhost:8000 --token JWT --write      # real import

The vault is treated as strictly READ-ONLY.

Dry run needs NO network and performs NO API calls: it walks the vault, plans
the whole import in memory (pure planning — see scan_vault/render_report), and
writes a human-readable report to stdout and to a markdown file.

Mapping (Beezy's convention, 2026-08: "a design belongs in a folder that
exists on a canvas — infer the design name from the direct parent folder of
the canvas, or the name of the canvas file itself"):

- EVERYTHING imports into the ONE existing project "rei by Rei" (PROJECT_NAME).
  Design names are therefore unique per project — collisions get the category
  suffixed in parens and are flagged. The category (title-cased) is recorded
  per design in the plan and sent as the design's `category` on creation;
  --backfill-categories patches it onto designs imported before the field
  existed (see ApiImporter.backfill_categories).
- Each top-level folder under Designs is a CATEGORY. At any depth below:
  1. UNSURE_PATTERNS names (checked at every level) are listed and skipped
     whole — nothing inside them is imported.
  2. A folder whose DIRECT children include >=1 .canvas and NO subfolders is a
     DESIGN named after the folder (provenance 'folder'). Its direct canvases
     each become one moodboard entry (body prefixed with the canvas name when
     the design ends up with more than one canvas); its direct .md files
     become note entries. Subfolders are never recursively merged.
  3. A folder with NO direct canvases but WITH subfolders is a GROUPING, not a
     design: we recurse into its subfolders.
  4. A MIXED folder (direct canvases AND subfolders): subfolders classify per
     2/3; the folder's own direct canvases are conservatively fuzzy-matched
     (see fuzzy_match) against the sibling subfolder names — a canvas matching
     exactly one subfolder is ASSIGNED into that subfolder's design; unmatched
     or ambiguous canvases are NOT imported and go to the report's
     "QUESTIONS — need your call" section with the candidates considered.
  5. A bare .canvas directly at CATEGORY level fuzzy-matches against that
     category's design folders first; if uniquely matched it is assigned into
     that folder's design, else it IS a design named after the canvas stem
     (provenance 'canvas name'). Ambiguous matches go to QUESTIONS.
  6. A bare .md at category/grouping/mixed level not caught by the skip-list
     goes to QUESTIONS (not imported by default). Blank files are skipped.
  7. A folder with no canvas and no subfolder is still created as a design
     SHELL (Beezy makes placeholder folders deliberately) and flagged
     "empty design shell" — EXCEPT names normalizing to "untitled", which are
     skipped and listed (as are untitled bare canvases at container level).

Dates: image filename timestamp (Pasted image YYYYMMDDHHMMSS) beats the
_attachments/YYYY-MM bucket (day=01) beats file mtime. An entry's occurred_at
is the earliest date among its attached images, else the note file's
frontmatter date, else its mtime.

Idempotency of --write: the project/designs are matched by name; every
imported entry body ends with a visible marker line (entry_marker) that
re-runs detect via the timeline; media dedupes on sha256 inside the API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── visible constants (the contract) ────────────────────────────────────────

#: The single project every design imports into (Beezy 2026-08-03).
PROJECT_NAME = "rei by Rei"

#: Case-insensitive substring patterns marking a folder/file as NOT a design.
UNSURE_PATTERNS = ("research", "inspo", "concept", "official seasons", "ideas")

# Beezy's rulings on the dry-run v2 QUESTIONS (2026-08-05), keyed by
# vault-relative path. 'ignore' = leave in the vault; 'design' = the loose
# canvas becomes its own design in its category. Loose notes are ignored
# ("ignore the category level notes"); medium bow + mini bow bag are
# superseded by their named subfolders.
RESOLVED: dict[str, str] = {
    "rei by Rei/Designs/BAGS/BOW BOWLING BAG/medium bow.canvas": "ignore",
    "rei by Rei/Designs/BAGS/BOW BOWLING BAG/mini bow bag.canvas": "ignore",
    "rei by Rei/Designs/BAGS/BOW BOWLING BAG/Marketing.md": "ignore",
    "rei by Rei/Designs/BAGS/Trims.md": "ignore",
    "rei by Rei/Designs/DRESSES/Legends.md": "ignore",
    "rei by Rei/Designs/BAGS/rei's message/cotton bags.canvas": "design",
    "rei by Rei/Designs/BAGS/SCRUNCHED/scrunched up paper.canvas": "design",
    "rei by Rei/Designs/DRESSES/MERMAIDS/Mermaid Scales.canvas": "design",
    "rei by Rei/Designs/DRESSES/MERMAIDS/The Dress.canvas": "design",
}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".heic"}
HEIC_EXTS = {".heic"}
CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".heic": "image/heic",
}
#: All inferred dates are wall-clock in the machine's local timezone.
LOCAL_TZ = datetime.now().astimezone().tzinfo

CANVAS_PHASE = "moodboard"
NOTE_PHASE = "note"
SOURCE_APP = "obsidian"
TEXT_SEP = "\n\n"

_UNSURE_RE = re.compile("|".join(re.escape(p) for p in UNSURE_PATTERNS), re.IGNORECASE)
_TS_RE = re.compile(r"(20\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})")
_BUCKET_RE = re.compile(r"^(20\d{2})-(\d{2})$")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_FM_DATE_RE = re.compile(r"^date:\s*['\"]?(\d{4}-\d{2}-\d{2})", re.MULTILINE)
_MD_EMBED_RE = re.compile(r"!\[\[([^\]|#]+?)(?:\|[^\]]*)?\]\]")

#: Possessive/brand tokens dropped by normalize_name ("Rei's Classic Biker"
#: and "REI CLASSIC BIKER" both normalize to "classic biker").
_BRAND_TOKENS = {"rei", "reis", "reii", "reiis"}
_APOSTROPHES = "'’"


def entry_marker(source_rel: str) -> str:
    """Deterministic, visible 'from Obsidian' marker appended to entry bodies.
    Doubles as the idempotency key for --write re-runs."""
    return f"[from Obsidian: {source_rel}]"


# ── name normalization + conservative fuzzy matching (pure) ─────────────────

def normalize_name(name: str) -> str:
    """Lowercase, drop apostrophes, strip punctuation/emoji to spaces, drop
    rei/rei's brand tokens, collapse whitespace."""
    s = name.lower()
    for ch in _APOSTROPHES:
        s = s.replace(ch, "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    tokens = [t for t in s.split() if t not in _BRAND_TOKENS]
    return " ".join(tokens)


def is_untitled(name: str) -> bool:
    """True for names that normalize to 'untitled' (digits allowed: 'Untitled 1')."""
    tokens = [t for t in normalize_name(name).split() if not t.isdigit()]
    return tokens == ["untitled"]


def fuzzy_match(stem: str, candidates: list[str]) -> tuple[str | None, list[str]]:
    """Conservative tiered match of a canvas stem against sibling folder names.

    Tiers (highest wins): 3 = exact normalized equality; 2 = containment
    (substring either way, or one token set a subset of the other);
    1 = token overlap covering at least half of the combined tokens
    (Jaccard >= 0.5). Returns (winner|None, matches at the best tier) —
    a winner exists only when exactly ONE candidate sits at the best tier.
    """
    a = normalize_name(stem)
    if not a:
        return None, []
    a_tok = set(a.split())
    best_tier = 0
    best: list[str] = []
    for cand in candidates:
        b = normalize_name(cand)
        if not b:
            continue
        b_tok = set(b.split())
        if a == b:
            tier = 3
        elif a in b or b in a or a_tok <= b_tok or b_tok <= a_tok:
            tier = 2
        elif len(a_tok & b_tok) / len(a_tok | b_tok) >= 0.5:
            tier = 1
        else:
            continue
        if tier > best_tier:
            best_tier, best = tier, [cand]
        elif tier == best_tier:
            best.append(cand)
    if len(best) == 1:
        return best[0], best
    return None, best


# ── date inference (pure) ────────────────────────────────────────────────────

def timestamp_from_filename(name: str) -> datetime | None:
    """'Pasted image 20241019165418.png' -> 2024-10-19 16:54:18."""
    m = _TS_RE.search(name)
    if not m:
        return None
    try:
        return datetime(*(int(g) for g in m.groups()), tzinfo=LOCAL_TZ)
    except ValueError:
        return None


def bucket_from_path(rel_path: str) -> datetime | None:
    """'_attachments/2024-09/x.png' -> 2024-09-01 (any YYYY-MM path segment)."""
    for seg in Path(rel_path).parts:
        m = _BUCKET_RE.match(seg)
        if m:
            year, month = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12:
                return datetime(year, month, 1, tzinfo=LOCAL_TZ)
    return None


def infer_image_date(rel_path: str, abs_path: Path | None) -> datetime | None:
    """Precedence: filename timestamp > YYYY-MM bucket > file mtime."""
    dt = timestamp_from_filename(Path(rel_path).name)
    if dt is None:
        dt = bucket_from_path(rel_path)
    if dt is None and abs_path is not None and abs_path.exists():
        dt = datetime.fromtimestamp(abs_path.stat().st_mtime, tz=LOCAL_TZ)
    return dt


def strip_frontmatter(text: str) -> tuple[str, datetime | None]:
    """Remove Obsidian YAML frontmatter; return (body, frontmatter date|None)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return text, None
    fm_date = None
    dm = _FM_DATE_RE.search(m.group(1))
    if dm:
        try:
            fm_date = datetime.fromisoformat(dm.group(1)).replace(tzinfo=LOCAL_TZ)
        except ValueError:
            fm_date = None
    return text[m.end():], fm_date


# ── plan model ───────────────────────────────────────────────────────────────

@dataclass
class PlannedImage:
    rel_path: str            # vault-root-relative (as referenced)
    abs_path: Path | None
    exists: bool
    is_heic: bool
    inferred_at: datetime | None
    sha256: str | None = None   # filled when hashing is possible


@dataclass
class PlannedEntry:
    kind: str                # 'canvas' | 'note'
    phase: str
    source_rel: str          # vault-relative path of the .canvas/.md
    body: str                # WITHOUT the marker line (appended at write time)
    images: list[PlannedImage] = field(default_factory=list)
    occurred_at: datetime | None = None
    text_nodes: int = 0
    link_nodes: int = 0


@dataclass
class PlannedDesign:
    category: str            # title-cased, e.g. 'Fun Stuff' (the design's category)
    category_dir: str        # raw category folder name, e.g. 'FUN STUFF'
    name: str
    source_rel: str
    provenance: str          # 'folder' | 'canvas name'
    entries: list[PlannedEntry] = field(default_factory=list)
    assigned_sources: list[str] = field(default_factory=list)  # fuzzy-assigned canvases

    @property
    def date_span(self) -> tuple[datetime, datetime] | None:
        dts = [e.occurred_at for e in self.entries if e.occurred_at]
        return (min(dts), max(dts)) if dts else None


@dataclass
class _DesignSpec:
    """Pre-materialization design: which files feed it (internal to scanning)."""
    category: str            # raw category folder name
    name: str
    source_rel: str
    provenance: str          # 'folder' | 'canvas name'
    canvases: list[Path] = field(default_factory=list)
    notes: list[Path] = field(default_factory=list)
    assigned: list[Path] = field(default_factory=list)


@dataclass
class AssignedCanvas:
    """A container-level canvas fuzzy-assigned into a sibling folder's design."""
    source_rel: str
    spec: _DesignSpec

    @property
    def design_name(self) -> str:
        return self.spec.name


@dataclass
class Question:
    """Something we deliberately did NOT import — needs Beezy's call."""
    rel_path: str
    kind: str                # 'canvas' | 'note'
    reason: str
    candidates: list[str] = field(default_factory=list)  # sibling folders considered
    matches: list[str] = field(default_factory=list)     # tied candidates (ambiguous)


@dataclass
class UnsureItem:
    category: str
    rel_path: str
    matched: str
    canvases: int
    notes: int
    image_refs: int


@dataclass
class VaultPlan:
    vault: Path
    designs_root_rel: str
    project_name: str = PROJECT_NAME
    designs: list[PlannedDesign] = field(default_factory=list)
    assigned: list[AssignedCanvas] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    resolved_ignored: list[str] = field(default_factory=list)  # Beezy's 'ignore' rulings
    unsure: list[UnsureItem] = field(default_factory=list)
    skipped_untitled: list[str] = field(default_factory=list)
    name_collisions: list[tuple[str, str]] = field(default_factory=list)  # (old, new)
    missing_files: list[tuple[str, str]] = field(default_factory=list)   # (source, ref)
    non_image_refs: list[tuple[str, str]] = field(default_factory=list)  # (source, ref)
    empty_sources: list[str] = field(default_factory=list)  # md/canvas with no content
    stray_files: list[str] = field(default_factory=list)    # unexpected files

    # totals
    @property
    def all_entries(self) -> list[PlannedEntry]:
        return [e for d in self.designs for e in d.entries]

    @property
    def heic_count(self) -> int:
        return sum(1 for e in self.all_entries for i in e.images if i.is_heic)

    @property
    def link_node_count(self) -> int:
        return sum(e.link_nodes for e in self.all_entries)


# ── vault scanning (pure planning: local reads only, never writes) ──────────

def find_designs_root(vault: Path) -> Path:
    """<vault>/Designs or <vault>/<anything>/Designs (Beezy's is nested)."""
    candidates = [vault / "Designs", *sorted(vault.glob("*/Designs"))]
    for c in candidates:
        if c.is_dir():
            return c
    raise SystemExit(f"no Designs directory found under {vault}")


def classify_unsure(name: str) -> str | None:
    """Return the matched UNSURE pattern, or None if the name is a design."""
    m = _UNSURE_RE.search(name)
    return m.group(0).lower() if m else None


def _load_canvas(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _count_unsure(vault: Path, item: Path, plan: VaultPlan) -> tuple[int, int, int]:
    """(canvases, notes, image refs) inside an UNSURE folder/file — for the report.
    Missing file refs are still surfaced (nothing is imported for UNSURE items)."""
    canvases = [item] if item.suffix == ".canvas" else sorted(item.rglob("*.canvas"))
    notes = [item] if item.suffix == ".md" else sorted(item.rglob("*.md"))
    image_refs = 0
    for cv in canvases:
        try:
            data = _load_canvas(cv)
        except (OSError, json.JSONDecodeError):
            continue
        for n in data.get("nodes", []):
            if n.get("type") != "file":
                continue
            image_refs += 1
            ref = n.get("file", "")
            if not (vault / ref).is_file():
                plan.missing_files.append((f"{cv.relative_to(vault)} (UNSURE)", ref))
    return len(canvases), len(notes), image_refs


def plan_canvas_entry(
    vault: Path, canvas: Path, plan: VaultPlan, name_prefix: str | None
) -> PlannedEntry | None:
    source_rel = str(canvas.relative_to(vault))
    try:
        data = _load_canvas(canvas)
    except (OSError, json.JSONDecodeError) as exc:
        plan.missing_files.append((source_rel, f"unreadable canvas: {exc}"))
        return None

    entry = PlannedEntry(kind="canvas", phase=CANVAS_PHASE, source_rel=source_rel, body="")
    texts: list[str] = []
    links: list[str] = []
    for node in data.get("nodes", []):
        ntype = node.get("type")
        if ntype == "file":
            ref = node.get("file", "")
            ext = Path(ref).suffix.lower()
            if ext not in IMAGE_EXTS:
                plan.non_image_refs.append((source_rel, ref))
                continue
            abs_path = vault / ref
            exists = abs_path.is_file()
            if not exists:
                plan.missing_files.append((source_rel, ref))
            entry.images.append(
                PlannedImage(
                    rel_path=ref,
                    abs_path=abs_path if exists else None,
                    exists=exists,
                    is_heic=ext in HEIC_EXTS,
                    inferred_at=infer_image_date(ref, abs_path if exists else None),
                )
            )
        elif ntype == "text":
            txt = (node.get("text") or "").strip()
            if txt:
                texts.append(txt)
                entry.text_nodes += 1
        elif ntype == "link":
            url = (node.get("url") or "").strip()
            if url:
                links.append(url)
                entry.link_nodes += 1

    if not entry.images and not texts and not links:
        plan.empty_sources.append(source_rel)
        return None

    body = TEXT_SEP.join(texts)
    if links:
        body = (body + TEXT_SEP if body else "") + "\n".join(links)
    if name_prefix:
        body = f"{name_prefix}{TEXT_SEP}{body}" if body else name_prefix
    entry.body = body

    image_dates = [i.inferred_at for i in entry.images if i.inferred_at]
    entry.occurred_at = (
        min(image_dates)
        if image_dates
        else datetime.fromtimestamp(canvas.stat().st_mtime, tz=LOCAL_TZ)
    )
    return entry


def plan_note_entry(vault: Path, md: Path, plan: VaultPlan) -> PlannedEntry | None:
    source_rel = str(md.relative_to(vault))
    try:
        raw = md.read_text(encoding="utf-8")
    except OSError as exc:
        plan.missing_files.append((source_rel, f"unreadable md: {exc}"))
        return None
    body, fm_date = strip_frontmatter(raw)
    body = body.strip()

    entry = PlannedEntry(kind="note", phase=NOTE_PHASE, source_rel=source_rel, body=body)
    # attach ![[...]] image embeds so the images (and their dates) aren't lost
    for ref in _MD_EMBED_RE.findall(raw):
        ref = ref.strip()
        ext = Path(ref).suffix.lower()
        if ext not in IMAGE_EXTS:
            continue
        abs_path = vault / ref
        if not abs_path.is_file():  # embeds may be vault-relative or name-only
            hits = (
                sorted((vault / "_attachments").rglob(Path(ref).name))
                if (vault / "_attachments").is_dir()
                else []
            )
            abs_path = hits[0] if hits else abs_path
        exists = abs_path.is_file()
        if not exists:
            plan.missing_files.append((source_rel, ref))
        rel = str(abs_path.relative_to(vault)) if exists else ref
        entry.images.append(
            PlannedImage(
                rel_path=rel,
                abs_path=abs_path if exists else None,
                exists=exists,
                is_heic=ext in HEIC_EXTS,
                inferred_at=infer_image_date(rel, abs_path if exists else None),
            )
        )

    if not body and not entry.images:
        plan.empty_sources.append(source_rel)
        return None

    image_dates = [i.inferred_at for i in entry.images if i.inferred_at]
    if image_dates:
        entry.occurred_at = min(image_dates)
    elif fm_date is not None:
        entry.occurred_at = fm_date
    else:
        entry.occurred_at = datetime.fromtimestamp(md.stat().st_mtime, tz=LOCAL_TZ)
    return entry


def _split_children(
    vault: Path, category: str, folder: Path, plan: VaultPlan
) -> tuple[list[Path], list[Path], list[Path]]:
    """One directory level: record UNSURE/untitled-dir/stray/hidden items,
    return the remaining (subdirs, canvases, mds)."""
    subdirs: list[Path] = []
    canvases: list[Path] = []
    mds: list[Path] = []
    for item in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if item.name.startswith("."):
            continue
        if item.is_file() and item.suffix.lower() not in (".canvas", ".md"):
            plan.stray_files.append(str(item.relative_to(vault)))
            continue
        name = item.name if item.is_dir() else item.stem
        matched = classify_unsure(name)
        if matched:
            n_canvases, n_notes, refs = _count_unsure(vault, item, plan)
            plan.unsure.append(
                UnsureItem(
                    category=category,
                    rel_path=str(item.relative_to(vault)),
                    matched=matched,
                    canvases=n_canvases,
                    notes=n_notes,
                    image_refs=refs,
                )
            )
            continue
        if item.is_dir():
            if is_untitled(item.name):  # rule 7 exception: never a design/shell
                plan.skipped_untitled.append(str(item.relative_to(vault)))
                continue
            subdirs.append(item)
        elif item.suffix.lower() == ".canvas":
            canvases.append(item)
        else:
            mds.append(item)
    return subdirs, canvases, mds


def _classify_folder(
    vault: Path, category: str, folder: Path, plan: VaultPlan
) -> tuple[_DesignSpec | None, list[_DesignSpec]]:
    """Classify one folder below category level.

    Returns (folder_spec|None, all specs found inside). folder_spec is set only
    when the folder itself IS a design (or shell) — the assignable targets for
    sibling-canvas fuzzy matching. Groupings/mixed folders return None + their
    nested designs.
    """
    subdirs, canvases, mds = _split_children(vault, category, folder, plan)
    if subdirs:  # grouping (no direct canvases) or mixed (both) — never a design
        specs = _resolve_container(
            vault, category, subdirs, canvases, mds, plan, at_category_level=False
        )
        return None, specs
    # design (>=1 direct canvas), notes-only design, or deliberate empty shell
    spec = _DesignSpec(
        category=category,
        name=folder.name,
        source_rel=str(folder.relative_to(vault)),
        provenance="folder",
        canvases=canvases,
        notes=mds,
    )
    return spec, [spec]


def _resolve_container(
    vault: Path,
    category: str,
    subdirs: list[Path],
    canvases: list[Path],
    mds: list[Path],
    plan: VaultPlan,
    at_category_level: bool,
) -> list[_DesignSpec]:
    """Category / grouping / mixed folder: classify subfolders, then place the
    folder's own direct canvases (fuzzy-assign, own design, or QUESTIONS) and
    loose .md files (QUESTIONS)."""
    specs: list[_DesignSpec] = []
    folder_specs: dict[str, _DesignSpec] = {}
    for sub in subdirs:
        spec, sub_specs = _classify_folder(vault, category, sub, plan)
        if spec is not None:
            folder_specs[sub.name] = spec
        specs.extend(sub_specs)

    for cv in canvases:
        rel = str(cv.relative_to(vault))
        if is_untitled(cv.stem):  # would otherwise become a design named 'Untitled'
            plan.skipped_untitled.append(rel)
            continue
        ruling = RESOLVED.get(rel)
        if ruling == "ignore":
            plan.resolved_ignored.append(rel)
            continue
        if ruling == "design":
            specs.append(
                _DesignSpec(
                    category=category,
                    name=cv.stem,
                    source_rel=rel,
                    provenance="canvas name",
                    canvases=[cv],
                )
            )
            continue
        target, matches = fuzzy_match(cv.stem, list(folder_specs))
        if target is not None:
            folder_specs[target].assigned.append(cv)
            plan.assigned.append(AssignedCanvas(source_rel=rel, spec=folder_specs[target]))
        elif len(matches) > 1:
            plan.questions.append(
                Question(
                    rel_path=rel,
                    kind="canvas",
                    reason="ambiguous fuzzy match — matches more than one sibling folder",
                    candidates=sorted(folder_specs),
                    matches=matches,
                )
            )
        elif at_category_level:  # rule 5: unmatched bare canvas IS a design
            specs.append(
                _DesignSpec(
                    category=category,
                    name=cv.stem,
                    source_rel=rel,
                    provenance="canvas name",
                    canvases=[cv],
                )
            )
        else:  # rule 4: unmatched canvas in a mixed folder — Beezy's call
            plan.questions.append(
                Question(
                    rel_path=rel,
                    kind="canvas",
                    reason="no fuzzy match to a sibling design folder",
                    candidates=sorted(folder_specs),
                )
            )

    for md in mds:
        rel = str(md.relative_to(vault))
        try:
            raw = md.read_text(encoding="utf-8")
        except OSError as exc:
            plan.missing_files.append((rel, f"unreadable md: {exc}"))
            continue
        body, _ = strip_frontmatter(raw)
        if not body.strip():
            plan.empty_sources.append(rel)
            continue
        if RESOLVED.get(rel) == "ignore":
            plan.resolved_ignored.append(rel)
            continue
        where = "category level" if at_category_level else "a grouping/mixed folder"
        plan.questions.append(
            Question(
                rel_path=rel,
                kind="note",
                reason=f"loose note at {where} — no design to attach it to",
                candidates=sorted(folder_specs),
            )
        )
    return specs


def _dedupe_design_names(specs: list[_DesignSpec], plan: VaultPlan) -> None:
    """Design names must be unique inside the single project: collisions get
    the (title-cased) category suffixed and are flagged in the plan."""
    seen: set[str] = set()
    for spec in specs:
        key = spec.name.strip().lower()
        if key in seen:
            new_name = f"{spec.name} ({spec.category.title()})"
            n = 2
            while new_name.lower() in seen:
                new_name = f"{spec.name} ({spec.category.title()} {n})"
                n += 1
            plan.name_collisions.append((spec.name, new_name))
            spec.name = new_name
            key = new_name.lower()
        seen.add(key)


def _materialize_design(vault: Path, spec: _DesignSpec, plan: VaultPlan) -> PlannedDesign:
    design = PlannedDesign(
        category=spec.category.title(),
        category_dir=spec.category,
        name=spec.name,
        source_rel=spec.source_rel,
        provenance=spec.provenance,
        assigned_sources=[str(cv.relative_to(vault)) for cv in spec.assigned],
    )
    all_canvases = [*spec.canvases, *spec.assigned]
    multi = len(all_canvases) > 1
    for cv in all_canvases:
        entry = plan_canvas_entry(vault, cv, plan, name_prefix=cv.stem if multi else None)
        if entry:
            design.entries.append(entry)
    for md in spec.notes:
        entry = plan_note_entry(vault, md, plan)
        if entry:
            design.entries.append(entry)
    return design


def scan_vault(vault: Path) -> VaultPlan:
    """Pure planning pass: walks the vault (read-only), no network, no API."""
    vault = vault.resolve()
    designs_root = find_designs_root(vault)
    plan = VaultPlan(vault=vault, designs_root_rel=str(designs_root.relative_to(vault)))

    all_specs: list[_DesignSpec] = []
    for category_dir in sorted(p for p in designs_root.iterdir() if p.is_dir()):
        category = category_dir.name
        subdirs, canvases, mds = _split_children(vault, category, category_dir, plan)
        all_specs.extend(
            _resolve_container(
                vault, category, subdirs, canvases, mds, plan, at_category_level=True
            )
        )
    _dedupe_design_names(all_specs, plan)
    for spec in all_specs:
        plan.designs.append(_materialize_design(vault, spec, plan))
    return plan


def hash_images(plan: VaultPlan) -> None:
    """Fill sha256 for every existing planned image (local reads only) so the
    report can count unique media the way the API's dedupe will."""
    cache: dict[Path, str] = {}
    for entry in plan.all_entries:
        for img in entry.images:
            if not img.exists or img.abs_path is None:
                continue
            if img.abs_path not in cache:
                try:
                    cache[img.abs_path] = hashlib.sha256(img.abs_path.read_bytes()).hexdigest()
                except OSError as exc:
                    plan.missing_files.append(
                        (entry.source_rel, f"unreadable: {img.rel_path}: {exc}")
                    )
                    img.exists = False
                    continue
            img.sha256 = cache[img.abs_path]


# ── report rendering (pure) ──────────────────────────────────────────────────

def _fmt_span(span: tuple[datetime, datetime] | None) -> str:
    if span is None:
        return "no dates"
    lo, hi = span[0].date(), span[1].date()
    return f"{lo}" if lo == hi else f"{lo} → {hi}"


def render_report(plan: VaultPlan, write_mode: bool = False) -> str:
    lines: list[str] = []
    mode = "WRITE" if write_mode else "DRY RUN"
    lines.append(f"# Obsidian → Atelier import — {mode}")
    lines.append("")
    lines.append(f"- Vault: `{plan.vault}`")
    lines.append(f"- Designs root: `{plan.designs_root_rel}`")
    lines.append(f"- Project: **{plan.project_name}** (everything imports into this ONE project)")
    lines.append(f"- UNSURE patterns: {', '.join(UNSURE_PATTERNS)}")
    lines.append("")

    total_entries = 0
    attach_total = 0
    shells: list[PlannedDesign] = []
    shas: set[str] = set()
    unhashed_unique = 0

    by_category: dict[str, list[PlannedDesign]] = {}
    for d in plan.designs:
        by_category.setdefault(d.category, []).append(d)

    for category, designs in by_category.items():
        lines.append(f"## {category}  (from `{designs[0].category_dir}/`)")
        for d in designs:
            n_canvas = sum(1 for e in d.entries if e.kind == "canvas")
            n_notes = sum(1 for e in d.entries if e.kind == "note")
            images = sum(len(e.images) for e in d.entries)
            ok_images = sum(1 for e in d.entries for i in e.images if i.exists)
            miss = f", {images - ok_images} missing" if ok_images != images else ""
            extra = ""
            if d.assigned_sources:
                extra = f" · +{len(d.assigned_sources)} canvas fuzzy-assigned in"
            shell = ""
            if not d.entries:
                shells.append(d)
                shell = "  ⚠ empty design shell"
            lines.append(
                f"- **{d.name}** ({d.provenance}) — {n_canvas} canvas, {n_notes} note, "
                f"{ok_images} images{miss}{extra} · {_fmt_span(d.date_span)}{shell}"
            )
            total_entries += len(d.entries)
            for e in d.entries:
                for i in e.images:
                    if not i.exists:
                        continue
                    attach_total += 1
                    if i.sha256:
                        shas.add(i.sha256)
                    else:
                        unhashed_unique += 1
        lines.append("")

    lines.append("## Fuzzy-assigned canvases")
    if plan.assigned:
        for a in plan.assigned:
            lines.append(f"- assigned: `{a.source_rel}` → design **{a.design_name}** (assigned into)")
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## QUESTIONS — need your call")
    if plan.questions:
        lines.append("Nothing below is imported until you decide.")
        for q in plan.questions:
            cand = (
                f"; candidates considered: {', '.join(q.candidates)}"
                if q.candidates
                else "; no sibling design folders to match against"
            )
            tied = f"; tied between: {', '.join(q.matches)}" if q.matches else ""
            lines.append(f"- `{q.rel_path}` ({q.kind}) — {q.reason}{tied}{cand}")
    else:
        lines.append("- none")
    lines.append("")

    if plan.resolved_ignored:
        lines.append("## Resolved by Beezy — ignored (2026-08-05 rulings)")
        lines.extend(f"- `{p}`" for p in sorted(plan.resolved_ignored))
        lines.append("")

    lines.append("## UNSURE — skipped, importing nothing (review by hand)")
    if plan.unsure:
        for u in plan.unsure:
            lines.append(
                f"- `{u.rel_path}` (matched '{u.matched}') — "
                f"{u.canvases} canvas, {u.notes} md, {u.image_refs} image refs"
            )
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## Untitled items skipped (name normalizes to 'untitled')")
    if plan.skipped_untitled:
        for s in plan.skipped_untitled:
            lines.append(f"- `{s}`")
    else:
        lines.append("- none")
    lines.append("")

    lines.append("## Missing / unreadable files")
    if plan.missing_files:
        for src, ref in plan.missing_files:
            lines.append(f"- `{src}` → `{ref}`")
    else:
        lines.append("- none")
    lines.append("")

    if plan.empty_sources:
        lines.append("## Empty sources skipped (no text, no images)")
        for s in plan.empty_sources:
            lines.append(f"- `{s}`")
        lines.append("")

    if plan.stray_files:
        lines.append("## Stray non-canvas/md files ignored")
        for s in plan.stray_files:
            lines.append(f"- `{s}`")
        lines.append("")

    unique_media = len(shas) + unhashed_unique
    dupes = attach_total - len(shas) - unhashed_unique if shas else 0
    lines.append("## Notes")
    lines.append(f"- Empty design shells kept (placeholders are deliberate): {len(shells)}")
    if plan.name_collisions:
        for old, new in plan.name_collisions:
            lines.append(f"- ⚠ design name collision: '{old}' imported as '{new}'")
    lines.append(f"- HEIC images encountered: {plan.heic_count}")
    lines.append(f"- Link-node URLs appended to entry bodies: {plan.link_node_count}")
    if shas:
        lines.append(f"- Duplicate image references (same sha256, deduped by the API): {dupes}")
    lines.append(f"- Media provenance: source_app='{SOURCE_APP}', source_url=vault-relative path")
    lines.append("")

    lines.append("## Grand totals")
    lines.append(
        f"**Would import into project '{plan.project_name}': {len(plan.designs)} designs, "
        f"{total_entries} entries, {unique_media} media** "
        f"({attach_total} image attachments, deduped by sha256); "
        f"{len(plan.assigned)} canvases fuzzy-assigned, {len(plan.questions)} open questions."
    )
    return "\n".join(lines) + "\n"


# ── write mode (the only code that touches the network) ─────────────────────

class ApiImporter:
    """Drives the real API exactly like the app does: create project/design/
    entry, then presigned PUT + /media/commit per image (sha256-deduped)."""

    def __init__(self, api_base: str, token: str) -> None:
        import httpx  # imported here so dry run never needs network deps

        self.http = httpx.Client(
            base_url=api_base.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        self.raw = httpx.Client(timeout=120)  # presigned PUTs (no auth header)
        self.counts = {
            "projects_created": 0, "designs_created": 0, "entries_created": 0,
            "entries_skipped": 0, "media_created": 0, "media_deduped": 0,
            "media_failed": 0, "heic_skipped": 0,
        }

    def _check(self, r, what: str):
        if r.status_code not in (200, 201):
            raise SystemExit(f"API error during {what}: {r.status_code} {r.text}")
        return r.json()

    def ensure_project(self, name: str) -> str:
        projects = self._check(self.http.get("/projects"), "list projects")
        for p in projects:
            if p["name"] == name:
                return p["id"]
        p = self._check(self.http.post("/projects", json={"name": name}), f"create project {name}")
        self.counts["projects_created"] += 1
        print(f"  + project {name!r}")
        return p["id"]

    def ensure_design(self, project_id: str, design: PlannedDesign) -> str:
        existing = self._check(
            self.http.get(f"/projects/{project_id}/designs"), "list designs"
        )
        for d in existing:
            if d["name"] == design.name:
                return d["id"]
        payload: dict = {
            "project_id": project_id,
            "name": design.name,
            "category": design.category,
        }
        span = design.date_span
        if span:
            payload["started_at"] = span[0].date().isoformat()
        d = self._check(self.http.post("/designs", json=payload), f"create design {design.name}")
        self.counts["designs_created"] += 1
        print(f"  + design {design.name!r}")
        return d["id"]

    def existing_markers(self, design_id: str) -> set[str]:
        timeline = self._check(
            self.http.get(f"/designs/{design_id}/timeline"), "read timeline"
        )
        found: set[str] = set()
        for e in timeline:
            body = e.get("body") or ""
            for m in re.findall(r"\[from Obsidian: [^\]]+\]", body):
                found.add(m)
        return found

    def create_entry(self, design_id: str, entry: PlannedEntry) -> str:
        body = (entry.body + TEXT_SEP if entry.body else "") + entry_marker(entry.source_rel)
        payload = {
            "design_id": design_id,
            "phase": entry.phase,
            "body": body,
            "occurred_at": entry.occurred_at.isoformat() if entry.occurred_at else None,
        }
        e = self._check(self.http.post("/entries", json=payload), f"create entry {entry.source_rel}")
        self.counts["entries_created"] += 1
        return e["id"]

    @staticmethod
    def _dimensions(data: bytes) -> tuple[int | None, int | None]:
        try:
            import io

            from PIL import Image

            with Image.open(io.BytesIO(data)) as im:
                return im.width, im.height
        except Exception:  # noqa: BLE001 — dims are best-effort metadata
            return None, None

    def upload_image(self, img: PlannedImage, entry_id: str) -> None:
        if img.is_heic:
            # HEIC would need pillow-heif for dims/thumbs; skip cleanly per plan
            self.counts["heic_skipped"] += 1
            print(f"    ~ HEIC skipped: {img.rel_path}")
            return
        if not img.exists or img.abs_path is None:
            self.counts["media_failed"] += 1
            return
        data = img.abs_path.read_bytes()
        digest = img.sha256 or hashlib.sha256(data).hexdigest()
        content_type = CONTENT_TYPES[img.abs_path.suffix.lower()]
        u = self._check(
            self.http.post(
                "/media/upload-url",
                json={"sha256": digest, "content_type": content_type, "kind": "image"},
            ),
            f"upload-url {img.rel_path}",
        )
        if u.get("upload_url"):
            pr = self.raw.put(u["upload_url"], content=data, headers={"Content-Type": content_type})
            if pr.status_code != 200:
                self.counts["media_failed"] += 1
                print(f"    ! PUT failed ({pr.status_code}): {img.rel_path}")
                return
            deduped = False
        else:
            deduped = True  # bytes already in storage for this workspace
        width, height = self._dimensions(data)
        self._check(
            self.http.post(
                "/media/commit",
                json={
                    "sha256": digest,
                    "r2_key": u["r2_key"],
                    "kind": "image",
                    "width": width,
                    "height": height,
                    "source_url": img.rel_path,
                    "source_app": SOURCE_APP,
                    "entry_id": entry_id,
                },
            ),
            f"commit {img.rel_path}",
        )
        self.counts["media_deduped" if deduped else "media_created"] += 1

    def backfill_categories(self, plan: VaultPlan) -> None:
        """Standalone repair mode (--backfill-categories): designs imported
        before the `category` field existed get theirs PATCHed in. Only
        designs that already EXIST by name in the project are touched, and
        only when their category is currently null — a hand-set category is
        never overwritten. Creates nothing."""
        projects = self._check(self.http.get("/projects"), "list projects")
        project = next((p for p in projects if p["name"] == plan.project_name), None)
        if project is None:
            raise SystemExit(
                f"project {plan.project_name!r} not found — nothing to backfill"
            )
        existing = {
            d["name"]: d
            for d in self._check(
                self.http.get(f"/projects/{project['id']}/designs"), "list designs"
            )
        }
        filled = already = absent = 0
        for design in plan.designs:
            d = existing.get(design.name)
            if d is None:
                absent += 1
                continue
            if d.get("category") is not None:
                already += 1
                continue
            self._check(
                self.http.patch(
                    f"/designs/{d['id']}", json={"category": design.category}
                ),
                f"backfill category on {design.name}",
            )
            filled += 1
            print(f"  ~ {design.name!r} category -> {design.category!r}")
        print(
            f"\nBackfill complete: {filled} set, {already} already had one, "
            f"{absent} planned designs not in the project"
        )

    def run(self, plan: VaultPlan) -> None:
        print(f"project {plan.project_name!r} ({len(plan.designs)} designs)")
        project_id = self.ensure_project(plan.project_name)
        for design in plan.designs:
            design_id = self.ensure_design(project_id, design)
            markers = self.existing_markers(design_id)
            for entry in design.entries:
                if entry_marker(entry.source_rel) in markers:
                    self.counts["entries_skipped"] += 1
                    print(f"    = entry exists, skipping: {entry.source_rel}")
                    continue
                entry_id = self.create_entry(design_id, entry)
                print(f"    + entry [{entry.phase}] {entry.source_rel} "
                      f"({len(entry.images)} images)")
                for img in entry.images:
                    self.upload_image(img, entry_id)
        print("\nImport complete:")
        for k, v in self.counts.items():
            print(f"  {k}: {v}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m scripts.import_obsidian",
        description="One-time Obsidian vault -> Atelier importer (dry-run by default).",
    )
    ap.add_argument("--vault", required=True, type=Path, help="path to the Obsidian vault root")
    ap.add_argument("--api-base", help="Atelier API base URL (required with --write)")
    ap.add_argument("--token", help="JWT bearer token (required with --write)")
    ap.add_argument("--write", action="store_true",
                    help="perform the real import through the API (default: dry run)")
    ap.add_argument("--backfill-categories", action="store_true",
                    help="standalone repair: PATCH the planned category onto designs "
                         "that already exist by name and have none (creates nothing)")
    ap.add_argument("--report", type=Path, default=None,
                    help="markdown report path (default: ./obsidian_import_report.md)")
    args = ap.parse_args(argv)

    if args.write and args.backfill_categories:
        ap.error("--write and --backfill-categories are mutually exclusive")
    if (args.write or args.backfill_categories) and not (args.api_base and args.token):
        ap.error("--write/--backfill-categories require --api-base and --token")
    if not args.vault.is_dir():
        ap.error(f"vault not found: {args.vault}")

    plan = scan_vault(args.vault)
    hash_images(plan)  # local reads only — lets the report count sha-unique media

    report = render_report(plan, write_mode=args.write)
    report_path = args.report or Path("obsidian_import_report.md")
    report_path = report_path.resolve()
    report_path.write_text(report, encoding="utf-8")
    sys.stdout.write(report)
    print(f"\nReport written to: {report_path}")

    if args.write:
        print(f"\n--write: importing into {args.api_base} ...\n")
        importer = ApiImporter(args.api_base, args.token)
        importer.run(plan)
    elif args.backfill_categories:
        print(f"\n--backfill-categories: patching categories on {args.api_base} ...\n")
        importer = ApiImporter(args.api_base, args.token)
        importer.backfill_categories(plan)
    else:
        print("\nDry run only — no API calls were made. Re-run with --write "
              "--api-base URL --token JWT to import.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
