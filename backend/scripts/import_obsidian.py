"""One-time Obsidian -> Atelier importer.

Usage (from backend/):

    python -m scripts.import_obsidian --vault PATH                # DRY RUN (default)
    python -m scripts.import_obsidian --vault PATH \
        --api-base http://localhost:8000 --token JWT --write      # real import

The vault is treated as strictly READ-ONLY.

Dry run needs NO network and performs NO API calls: it walks the vault, plans
the whole import in memory (pure planning — see scan_vault/render_report), and
writes a human-readable report to stdout and to a markdown file.

Mapping (agreed with Beezy):
- <designs root>/<CATEGORY>/            -> Atelier PROJECT (title-cased name)
- <CATEGORY>/<Design Folder>/           -> DESIGN (all .canvas/.md inside, recursively)
- <CATEGORY>/<bare>.canvas|.md          -> DESIGN of its own (name = file stem)
- each .canvas -> ONE moodboard entry: image nodes attached as media in node
  order, text nodes concatenated into the body (link-node URLs appended),
  prefixed with the canvas name when the design has more than one canvas
- each .md     -> a 'note' entry; body = content minus Obsidian frontmatter;
  ![[...]] image embeds are attached as media
- names matching UNSURE_PATTERNS are classified UNSURE and imported as nothing

Dates: image filename timestamp (Pasted image YYYYMMDDHHMMSS) beats the
_attachments/YYYY-MM bucket (day=01) beats file mtime. An entry's occurred_at
is the earliest date among its attached images, else the note file's
frontmatter date, else its mtime.

Idempotency of --write: projects/designs are matched by name; every imported
entry body ends with a visible marker line (entry_marker) that re-runs detect
via the timeline; media dedupes on sha256 inside the API itself.
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

#: Case-insensitive substring patterns marking a folder/file as NOT a design.
UNSURE_PATTERNS = ("research", "inspo", "concept", "official seasons", "ideas")

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


def entry_marker(source_rel: str) -> str:
    """Deterministic, visible 'from Obsidian' marker appended to entry bodies.
    Doubles as the idempotency key for --write re-runs."""
    return f"[from Obsidian: {source_rel}]"


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
    category: str
    project_name: str
    name: str
    source_rel: str
    entries: list[PlannedEntry] = field(default_factory=list)

    @property
    def date_span(self) -> tuple[datetime, datetime] | None:
        dts = [e.occurred_at for e in self.entries if e.occurred_at]
        return (min(dts), max(dts)) if dts else None


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
    projects: dict[str, list[PlannedDesign]] = field(default_factory=dict)  # project -> designs
    unsure: list[UnsureItem] = field(default_factory=list)
    missing_files: list[tuple[str, str]] = field(default_factory=list)   # (source, ref)
    non_image_refs: list[tuple[str, str]] = field(default_factory=list)  # (source, ref)
    empty_sources: list[str] = field(default_factory=list)  # md/canvas with no content
    stray_files: list[str] = field(default_factory=list)    # unexpected files at category level

    # totals
    @property
    def all_entries(self) -> list[PlannedEntry]:
        return [e for designs in self.projects.values() for d in designs for e in d.entries]

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


def plan_canvas_entry(vault: Path, canvas: Path, plan: VaultPlan, name_prefix: str | None) -> PlannedEntry | None:
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
            hits = sorted((vault / "_attachments").rglob(Path(ref).name)) if (vault / "_attachments").is_dir() else []
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


def plan_design(vault: Path, category: str, project_name: str, item: Path, plan: VaultPlan) -> PlannedDesign:
    """A design folder (all canvases/notes inside, recursively) or a bare file."""
    if item.is_dir():
        name = item.name
        canvases = sorted(item.rglob("*.canvas"))
        notes = sorted(item.rglob("*.md"))
    else:
        name = item.stem
        canvases = [item] if item.suffix == ".canvas" else []
        notes = [item] if item.suffix == ".md" else []

    design = PlannedDesign(
        category=category,
        project_name=project_name,
        name=name,
        source_rel=str(item.relative_to(vault)),
    )
    multi = len(canvases) > 1
    for cv in canvases:
        entry = plan_canvas_entry(vault, cv, plan, name_prefix=cv.stem if multi else None)
        if entry:
            design.entries.append(entry)
    for md in notes:
        entry = plan_note_entry(vault, md, plan)
        if entry:
            design.entries.append(entry)
    return design


def scan_vault(vault: Path) -> VaultPlan:
    """Pure planning pass: walks the vault (read-only), no network, no API."""
    vault = vault.resolve()
    designs_root = find_designs_root(vault)
    plan = VaultPlan(vault=vault, designs_root_rel=str(designs_root.relative_to(vault)))

    for category_dir in sorted(p for p in designs_root.iterdir() if p.is_dir()):
        category = category_dir.name
        project_name = category.title()
        designs: list[PlannedDesign] = []
        for item in sorted(category_dir.iterdir(), key=lambda p: p.name.lower()):
            if item.name.startswith("."):
                continue
            if item.is_file() and item.suffix not in (".canvas", ".md"):
                plan.stray_files.append(str(item.relative_to(vault)))
                continue
            name = item.name if item.is_dir() else item.stem
            matched = classify_unsure(name)
            if matched:
                canvases, notes, refs = _count_unsure(vault, item, plan)
                plan.unsure.append(
                    UnsureItem(
                        category=category,
                        rel_path=str(item.relative_to(vault)),
                        matched=matched,
                        canvases=canvases,
                        notes=notes,
                        image_refs=refs,
                    )
                )
                continue
            designs.append(plan_design(vault, category, project_name, item, plan))
        plan.projects[project_name] = designs
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
                    plan.missing_files.append((entry.source_rel, f"unreadable: {img.rel_path}: {exc}"))
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
    lines.append(f"- UNSURE patterns: {', '.join(UNSURE_PATTERNS)}")
    lines.append("")

    total_designs = total_entries = 0
    attach_total = 0
    shas: set[str] = set()
    unhashed_unique = 0

    for project_name, designs in plan.projects.items():
        category = designs[0].category if designs else project_name.upper()
        lines.append(f"## {project_name}  (from `{category}/`)")
        if not designs:
            lines.append("- (no importable designs)")
        for d in designs:
            canvases = sum(1 for e in d.entries if e.kind == "canvas")
            notes = sum(1 for e in d.entries if e.kind == "note")
            images = sum(len(e.images) for e in d.entries)
            ok_images = sum(1 for e in d.entries for i in e.images if i.exists)
            miss = f", {images - ok_images} missing" if ok_images != images else ""
            shell = "  ⚠ empty design shell (sources had no content)" if not d.entries else ""
            lines.append(
                f"- **{d.name}** — {canvases} canvas, {notes} note, "
                f"{ok_images} images{miss} · {_fmt_span(d.date_span)}{shell}"
            )
            total_designs += 1
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
        lines.append("## Stray category-level files ignored")
        for s in plan.stray_files:
            lines.append(f"- `{s}`")
        lines.append("")

    unique_media = len(shas) + unhashed_unique
    dupes = attach_total - len(shas) - unhashed_unique if shas else 0
    lines.append("## Notes")
    lines.append(f"- HEIC images encountered: {plan.heic_count}")
    lines.append(f"- Link-node URLs appended to entry bodies: {plan.link_node_count}")
    if shas:
        lines.append(f"- Duplicate image references (same sha256, deduped by the API): {dupes}")
    lines.append(f"- Media provenance: source_app='{SOURCE_APP}', source_url=vault-relative path")
    lines.append("")

    lines.append("## Grand totals")
    lines.append(
        f"**Would create {len(plan.projects)} projects, {total_designs} designs, "
        f"{total_entries} entries, {unique_media} media** "
        f"({attach_total} image attachments, deduped by sha256)."
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
        payload: dict = {"project_id": project_id, "name": design.name}
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

    def run(self, plan: VaultPlan) -> None:
        for project_name, designs in plan.projects.items():
            print(f"project {project_name!r} ({len(designs)} designs)")
            project_id = self.ensure_project(project_name)
            for design in designs:
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
    ap.add_argument("--report", type=Path, default=None,
                    help="markdown report path (default: ./obsidian_import_report.md)")
    args = ap.parse_args(argv)

    if args.write and not (args.api_base and args.token):
        ap.error("--write requires --api-base and --token")
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
    else:
        print("\nDry run only — no API calls were made. Re-run with --write "
              "--api-base URL --token JWT to import.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
