"""Obsidian importer: pure planning against a synthetic vault in tmp_path.

Covers classification (design folder / bare canvas / UNSURE), date inference
precedence (filename timestamp > YYYY-MM bucket > mtime), the unsure list,
dry-run report counts, and that a dry run performs ZERO network calls
(sockets are hard-blocked while the CLI runs).

No API, no DB, no MinIO — scan_vault/render_report are pure local reads.
"""

import json
import os
import socket
from datetime import datetime
from pathlib import Path

import pytest

from scripts.import_obsidian import (
    LOCAL_TZ,
    UNSURE_PATTERNS,
    bucket_from_path,
    classify_unsure,
    entry_marker,
    hash_images,
    infer_image_date,
    main,
    render_report,
    scan_vault,
    strip_frontmatter,
    timestamp_from_filename,
)
from tests.util import make_png

TS_IMG = "Pasted image 20240916120000.png"  # filename timestamp wins
BUCKET_IMG = "plain.png"                    # only the 2024-10 bucket
LOOSE_IMG = "loose.png"                     # neither -> mtime
LOOSE_MTIME = datetime(2023, 5, 4, 3, 2, 1, tzinfo=LOCAL_TZ)


def _canvas(*nodes: dict) -> str:
    return json.dumps({"nodes": list(nodes), "edges": []})


def _file_node(ref: str) -> dict:
    return {"id": os.urandom(4).hex(), "type": "file", "file": ref,
            "x": 0, "y": 0, "width": 100, "height": 100}


def _text_node(text: str) -> dict:
    return {"id": os.urandom(4).hex(), "type": "text", "text": text,
            "x": 0, "y": 0, "width": 100, "height": 100}


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """Tiny synthetic vault: 2 categories, 1 design folder (canvas + md),
    1 bare canvas, 1 UNSURE folder, _attachments with a timestamped file."""
    v = tmp_path / "vault"
    att = v / "_attachments"
    (att / "2024-09").mkdir(parents=True)
    (att / "2024-10").mkdir()
    (att / "2024-09" / TS_IMG).write_bytes(make_png(rgb=(1, 2, 3)))
    (att / "2024-10" / BUCKET_IMG).write_bytes(make_png(rgb=(4, 5, 6)))
    (att / LOOSE_IMG).write_bytes(make_png(rgb=(7, 8, 9)))
    os.utime(att / LOOSE_IMG, (LOOSE_MTIME.timestamp(), LOOSE_MTIME.timestamp()))

    bags = v / "Designs" / "BAGS"
    shoes = v / "Designs" / "SHOES"
    moon = bags / "Moon Bag"
    moon.mkdir(parents=True)
    shoes.mkdir(parents=True)

    # design folder: canvas (2 real images, 1 missing ref, texts) + 2 notes
    (moon / "moon.canvas").write_text(
        _canvas(
            _file_node(f"_attachments/2024-09/{TS_IMG}"),
            _text_node("crescent flap"),
            _file_node(f"_attachments/2024-10/{BUCKET_IMG}"),
            _text_node("   "),  # whitespace-only: dropped
            _file_node("_attachments/2024-11/gone.png"),  # missing on disk
        )
    )
    (moon / "Notes.md").write_text(
        f"hardware ideas pending\n\n![[_attachments/2024-10/{BUCKET_IMG}]]\n"
    )
    (moon / "Dated.md").write_text("---\ndate: 2025-03-05\n---\nlining sourced\n")
    (moon / "Empty.md").write_text("\n\n")  # skipped, reported

    # bare canvas at category level -> its own design
    (shoes / "BOW.canvas").write_text(
        _canvas(_file_node(f"_attachments/{LOOSE_IMG}"), _text_node("bow boots"))
    )

    # UNSURE folder: must import nothing
    research = bags / "Research"
    research.mkdir()
    (research / "chanel.canvas").write_text(
        _canvas(_file_node(f"_attachments/2024-09/{TS_IMG}"))
    )
    return v


# ── date inference precedence ────────────────────────────────────────────────

def test_filename_timestamp_beats_bucket(vault: Path):
    assert timestamp_from_filename(TS_IMG) == datetime(2024, 9, 16, 12, 0, 0, tzinfo=LOCAL_TZ)
    # the file sits in the 2024-09 bucket, but the filename timestamp wins
    got = infer_image_date(f"_attachments/2024-09/{TS_IMG}", vault / "_attachments/2024-09" / TS_IMG)
    assert got == datetime(2024, 9, 16, 12, 0, 0, tzinfo=LOCAL_TZ)


def test_bucket_beats_mtime(vault: Path):
    got = infer_image_date(
        f"_attachments/2024-10/{BUCKET_IMG}", vault / "_attachments/2024-10" / BUCKET_IMG
    )
    assert got == datetime(2024, 10, 1, tzinfo=LOCAL_TZ)
    assert bucket_from_path("_attachments/2024-10/x.png") == datetime(2024, 10, 1, tzinfo=LOCAL_TZ)
    assert bucket_from_path("_attachments/nope/x.png") is None


def test_mtime_is_last_resort(vault: Path):
    got = infer_image_date(f"_attachments/{LOOSE_IMG}", vault / "_attachments" / LOOSE_IMG)
    assert got == LOOSE_MTIME


def test_frontmatter_strip_and_date():
    body, dt = strip_frontmatter("---\ndate: 2025-03-05\ntags: [a]\n---\nhello\n")
    assert body == "hello\n"
    assert dt == datetime(2025, 3, 5, tzinfo=LOCAL_TZ)
    body, dt = strip_frontmatter("no frontmatter here")
    assert (body, dt) == ("no frontmatter here", None)


# ── classification ───────────────────────────────────────────────────────────

def test_classification(vault: Path):
    plan = scan_vault(vault)
    assert set(plan.projects) == {"Bags", "Shoes"}  # CATEGORY -> title-cased project
    bags = {d.name for d in plan.projects["Bags"]}
    shoes = {d.name for d in plan.projects["Shoes"]}
    assert bags == {"Moon Bag"}          # design folder, original casing kept
    assert shoes == {"BOW"}              # bare canvas -> design named by file stem
    # nothing from Research leaked into designs
    assert all("Research" not in d.source_rel for ds in plan.projects.values() for d in ds)


def test_unsure_list(vault: Path):
    plan = scan_vault(vault)
    assert len(plan.unsure) == 1
    u = plan.unsure[0]
    assert u.rel_path == "Designs/BAGS/Research"
    assert u.matched == "research"
    assert (u.canvases, u.image_refs) == (1, 1)
    for name in ("Inspo.canvas", "OFFICIAL SEASONS", "Shoe design concepts", "ideas board"):
        assert classify_unsure(name) is not None, name
    assert classify_unsure("in organic shoulder") is None  # no false positive
    assert "research" in UNSURE_PATTERNS


def test_entries_and_dates(vault: Path):
    plan = scan_vault(vault)
    moon = plan.projects["Bags"][0]
    by_src = {Path(e.source_rel).name: e for e in moon.entries}
    assert set(by_src) == {"moon.canvas", "Notes.md", "Dated.md"}  # Empty.md skipped

    canvas = by_src["moon.canvas"]
    assert canvas.phase == "moodboard"
    assert canvas.occurred_at == datetime(2024, 9, 16, 12, 0, 0, tzinfo=LOCAL_TZ)  # earliest image
    assert "crescent flap" in canvas.body
    assert len(canvas.images) == 3 and sum(i.exists for i in canvas.images) == 2

    note = by_src["Notes.md"]
    assert note.phase == "note"
    assert len(note.images) == 1                       # ![[...]] embed attached
    assert note.occurred_at == datetime(2024, 10, 1, tzinfo=LOCAL_TZ)   # image beats mtime

    dated = by_src["Dated.md"]
    assert dated.occurred_at == datetime(2025, 3, 5, tzinfo=LOCAL_TZ)   # frontmatter date
    assert "---" not in dated.body

    bow = plan.projects["Shoes"][0].entries[0]
    assert bow.occurred_at == LOOSE_MTIME              # image mtime fallback

    assert ("Designs/BAGS/Moon Bag/moon.canvas", "_attachments/2024-11/gone.png") in plan.missing_files
    assert "Designs/BAGS/Moon Bag/Empty.md" in plan.empty_sources


def test_entry_marker_is_deterministic():
    m = entry_marker("Designs/BAGS/Moon Bag/moon.canvas")
    assert m == "[from Obsidian: Designs/BAGS/Moon Bag/moon.canvas]"
    assert entry_marker("Designs/BAGS/Moon Bag/moon.canvas") == m


# ── dry-run report ───────────────────────────────────────────────────────────

def test_dry_run_report_counts(vault: Path):
    plan = scan_vault(vault)
    hash_images(plan)
    report = render_report(plan)
    # 2 projects, 2 designs, 4 entries (moon.canvas, Notes.md, Dated.md, BOW.canvas);
    # 4 attachments but plain.png is shared between the canvas and the md embed
    # -> 3 sha-unique media
    assert "Would create 2 projects, 2 designs, 4 entries, 3 media" in report
    assert "(4 image attachments, deduped by sha256)" in report
    assert "`Designs/BAGS/Research` (matched 'research')" in report
    assert "gone.png" in report            # missing file surfaced
    assert "Empty.md" in report            # empty source surfaced
    assert "HEIC images encountered: 0" in report


def test_dry_run_makes_zero_network_calls(vault: Path, tmp_path: Path, monkeypatch, capsys):
    """The full CLI dry run must not open a single socket."""
    def _blocked(*a, **k):
        raise AssertionError("network call attempted during dry run")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)

    report_path = tmp_path / "report.md"
    monkeypatch.chdir(tmp_path)
    rc = main(["--vault", str(vault), "--report", str(report_path)])
    assert rc == 0
    assert report_path.exists()
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "no API calls were made" in out


def test_write_requires_api_base_and_token(vault: Path):
    with pytest.raises(SystemExit):
        main(["--vault", str(vault), "--write"])
