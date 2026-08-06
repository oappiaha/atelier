"""Obsidian importer: pure planning against a synthetic vault in tmp_path.

Covers the REORGANIZED-vault classification (design folder vs grouping vs
mixed, conservative fuzzy assignment of loose canvases, untitled skips,
deliberate empty shells, single-project plan), date inference precedence
(filename timestamp > YYYY-MM bucket > mtime), the UNSURE list, the QUESTIONS
section, dry-run report counts, and that a dry run performs ZERO network
calls (sockets are hard-blocked while the CLI runs).

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
    PROJECT_NAME,
    UNSURE_PATTERNS,
    bucket_from_path,
    classify_unsure,
    entry_marker,
    fuzzy_match,
    hash_images,
    infer_image_date,
    is_untitled,
    main,
    normalize_name,
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
    """Synthetic vault exercising every classification rule:

    BAGS/
      Moon Bag/                 design folder (canvas + notes + empty md)
      Clutches Wallets/         GROUPING (no direct canvas, subfolders)
        Rei's Clutch/           nested design
        Rei's Card Holders/     nested design
      BOW BAG/                  MIXED (direct canvases + subfolders)
        Rei's Mini Bow/         nested design
        Rei's Empty Shell/      deliberate empty shell
        REI MINI BOW.canvas     -> fuzzy-ASSIGNED into Rei's Mini Bow
        random thing.canvas     -> no match -> QUESTIONS
      Research/                 UNSURE
      Trims.md                  loose category note -> QUESTIONS
      Untitled 1.canvas         untitled -> skipped
      Untitled/                 untitled folder -> skipped
    SHOES/
      Rei's Bow Boots/          design folder (empty -> shell w/ assigned canvas)
      BOW BOOTS.canvas          bare canvas, fuzzy match -> assigned into it
      gloves.canvas             bare canvas, no match -> its own design
      Moon Bag/                 design name collision with BAGS/Moon Bag
    """
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

    # plain design folder: canvas (2 real images, 1 missing ref, texts) + notes
    moon = bags / "Moon Bag"
    moon.mkdir(parents=True)
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

    # GROUPING: no direct canvas, only subfolders -> NOT a design itself
    cw = bags / "Clutches Wallets"
    (cw / "Rei's Clutch").mkdir(parents=True)
    (cw / "Rei's Clutch" / "clutches.canvas").write_text(
        _canvas(_text_node("fold-over clutch"))
    )
    (cw / "Rei's Card Holders").mkdir()
    (cw / "Rei's Card Holders" / "cards.canvas").write_text(
        _canvas(_text_node("card slots"))
    )

    # MIXED: direct canvases AND subfolders
    bow = bags / "BOW BAG"
    (bow / "Rei's Mini Bow").mkdir(parents=True)
    (bow / "Rei's Mini Bow" / "mini.canvas").write_text(_canvas(_text_node("mini bow")))
    (bow / "Rei's Empty Shell").mkdir()          # deliberate placeholder
    (bow / "REI MINI BOW.canvas").write_text(    # exact-normalized match -> assigned
        _canvas(_text_node("assigned moodboard"))
    )
    (bow / "random thing.canvas").write_text(    # no match -> QUESTIONS
        _canvas(_text_node("???"))
    )

    # UNSURE folder: must import nothing
    research = bags / "Research"
    research.mkdir()
    (research / "chanel.canvas").write_text(
        _canvas(_file_node(f"_attachments/2024-09/{TS_IMG}"))
    )

    # loose category-level note -> QUESTIONS; untitled items -> skipped
    (bags / "Trims.md").write_text("ball trims\nlogo trims\n")
    (bags / "Untitled 1.canvas").write_text(_canvas(_text_node("scratch")))
    (bags / "Untitled").mkdir()

    # SHOES: bare canvas fuzzy-assigned into an (empty) sibling design folder
    (shoes / "Rei's Bow Boots").mkdir(parents=True)
    (shoes / "BOW BOOTS.canvas").write_text(
        _canvas(_file_node(f"_attachments/{LOOSE_IMG}"), _text_node("bow boots"))
    )
    # bare canvas with no sibling match -> its own design (canvas-name provenance)
    (shoes / "gloves.canvas").write_text(_canvas(_text_node("long gloves")))
    # design name collision across categories (single project => must be unique)
    (shoes / "Moon Bag").mkdir()
    (shoes / "Moon Bag" / "moon shoe.canvas").write_text(_canvas(_text_node("moon shoe")))
    return v


def _by_name(plan) -> dict[str, object]:
    return {d.name: d for d in plan.designs}


# ── date inference precedence (unchanged behavior) ───────────────────────────

def test_filename_timestamp_beats_bucket(vault: Path):
    assert timestamp_from_filename(TS_IMG) == datetime(2024, 9, 16, 12, 0, 0, tzinfo=LOCAL_TZ)
    # the file sits in the 2024-09 bucket, but the filename timestamp wins
    got = infer_image_date(
        f"_attachments/2024-09/{TS_IMG}", vault / "_attachments/2024-09" / TS_IMG
    )
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


# ── normalization + fuzzy matching ───────────────────────────────────────────

def test_normalize_name():
    assert normalize_name("REI CLASSIC BIKER") == "classic biker"
    assert normalize_name("Rei's Classic Biker") == "classic biker"
    assert normalize_name("Rei's Two Piece 🧜🏽‍♀️") == "two piece"
    assert normalize_name("SKUNT!") == "skunt"


def test_is_untitled():
    assert is_untitled("Untitled")
    assert is_untitled("Untitled 1")
    assert is_untitled("untitled  2")
    assert not is_untitled("Untitled Dress")
    assert not is_untitled("Moon Bag")


def test_fuzzy_match_tiers():
    folders = ["Rei's Classic Biker", "Rei's Wet Biker Jacket", "Rei's Classic Moto"]
    # exact normalized equality beats weaker token overlaps with other folders
    assert fuzzy_match("REI CLASSIC BIKER", folders) == (
        "Rei's Classic Biker", ["Rei's Classic Biker"]
    )
    # token-subset containment: mini bow bag ⊆ mini bow bow bag, uniquely
    win, _ = fuzzy_match(
        "mini bow bag", ["Rei's Mini Bow Bow Bag", "Rei's Bow Bow Bowling Bag"]
    )
    assert win == "Rei's Mini Bow Bow Bag"
    # weak overlap (jaccard < 0.5) must NOT match: scales != lace
    win, matches = fuzzy_match("Mermaid Scales", ["Mermaid Lace", "Rei's Two Piece"])
    assert win is None and matches == []
    # ambiguity: same-tier tie yields no winner but reports both
    win, matches = fuzzy_match("bow", ["bow east", "bow west"])
    assert win is None and set(matches) == {"bow east", "bow west"}
    # no candidates / empty normalization
    assert fuzzy_match("Rei's", ["whatever"]) == (None, [])


# ── classification: designs, groupings, mixed, shells, untitled ─────────────

def test_single_project_plan(vault: Path):
    plan = scan_vault(vault)
    assert plan.project_name == PROJECT_NAME == "rei by Rei"
    # categories are recorded per design, title-cased (raw dir kept alongside)
    cats = {(d.category, d.category_dir) for d in plan.designs}
    assert cats == {("Bags", "BAGS"), ("Shoes", "SHOES")}


def test_classification(vault: Path):
    plan = scan_vault(vault)
    names = _by_name(plan)
    # design folders (provenance 'folder')
    assert names["Moon Bag"].provenance == "folder"
    # grouping folder is NOT a design; its subfolders are
    assert "Clutches Wallets" not in names
    assert {"Rei's Clutch", "Rei's Card Holders"} <= set(names)
    # mixed folder is NOT a design; its subfolders are
    assert "BOW BAG" not in names
    assert {"Rei's Mini Bow", "Rei's Empty Shell"} <= set(names)
    # bare canvas with no match -> its own design named after the stem
    assert names["gloves"].provenance == "canvas name"
    # nothing from Research leaked into designs
    assert all("Research" not in d.source_rel for d in plan.designs)


def test_mixed_folder_fuzzy_assignment_and_questions(vault: Path):
    plan = scan_vault(vault)
    names = _by_name(plan)
    # exact-normalized loose canvas assigned into the sibling subfolder design
    mini = names["Rei's Mini Bow"]
    assert mini.assigned_sources == ["Designs/BAGS/BOW BAG/REI MINI BOW.canvas"]
    assert any(a.design_name == "Rei's Mini Bow" for a in plan.assigned)
    # design now has 2 canvases -> both entry bodies get the canvas-stem prefix
    canvas_bodies = [e.body for e in mini.entries if e.kind == "canvas"]
    assert len(canvas_bodies) == 2
    assert any(b.startswith("mini") for b in canvas_bodies)
    assert any(b.startswith("REI MINI BOW") for b in canvas_bodies)
    # unmatched mixed-folder canvas is NOT imported -> QUESTIONS with candidates
    q = next(q for q in plan.questions if q.rel_path.endswith("random thing.canvas"))
    assert q.kind == "canvas"
    assert "no fuzzy match" in q.reason
    assert set(q.candidates) == {"Rei's Mini Bow", "Rei's Empty Shell"}
    assert "random thing" not in names


def test_bare_canvas_fuzzy_match_to_sibling_folder(vault: Path):
    plan = scan_vault(vault)
    names = _by_name(plan)
    boots = names["Rei's Bow Boots"]
    assert boots.provenance == "folder"
    assert boots.assigned_sources == ["Designs/SHOES/BOW BOOTS.canvas"]
    assert len(boots.entries) == 1          # the assigned canvas fills the shell
    assert boots.entries[0].occurred_at == LOOSE_MTIME
    # single canvas -> no name prefix on the body
    assert boots.entries[0].body == "bow boots"
    assert "BOW BOOTS" not in names         # not a design of its own


def test_untitled_skipped_and_shells_kept(vault: Path):
    plan = scan_vault(vault)
    names = _by_name(plan)
    assert "Untitled" not in names and "Untitled 1" not in names
    assert set(plan.skipped_untitled) == {
        "Designs/BAGS/Untitled",
        "Designs/BAGS/Untitled 1.canvas",
    }
    # deliberate placeholder folder still becomes a (flagged) design shell
    shell = names["Rei's Empty Shell"]
    assert shell.entries == []


def test_loose_note_goes_to_questions(vault: Path):
    plan = scan_vault(vault)
    q = next(q for q in plan.questions if q.rel_path == "Designs/BAGS/Trims.md")
    assert q.kind == "note"
    assert "loose note" in q.reason
    assert all(d.source_rel != "Designs/BAGS/Trims.md" for d in plan.designs)


def test_name_collision_gets_category_suffix(vault: Path):
    plan = scan_vault(vault)
    names = set(_by_name(plan))
    assert "Moon Bag" in names
    assert "Moon Bag (Shoes)" in names       # BAGS wins first, SHOES suffixed
    assert plan.name_collisions == [("Moon Bag", "Moon Bag (Shoes)")]
    assert len(names) == len(plan.designs)   # unique within the single project


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
    moon = _by_name(plan)["Moon Bag"]
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

    assert (
        "Designs/BAGS/Moon Bag/moon.canvas", "_attachments/2024-11/gone.png"
    ) in plan.missing_files
    assert "Designs/BAGS/Moon Bag/Empty.md" in plan.empty_sources


def test_entry_marker_is_deterministic():
    m = entry_marker("Designs/BAGS/Moon Bag/moon.canvas")
    assert m == "[from Obsidian: Designs/BAGS/Moon Bag/moon.canvas]"
    assert entry_marker("Designs/BAGS/Moon Bag/moon.canvas") == m


# ── dry-run report ───────────────────────────────────────────────────────────

def test_dry_run_report(vault: Path):
    plan = scan_vault(vault)
    hash_images(plan)
    report = render_report(plan)
    # 8 designs: Moon Bag, Rei's Clutch, Rei's Card Holders, Rei's Mini Bow,
    # Rei's Empty Shell, gloves, Rei's Bow Boots, Moon Bag (Shoes); entries:
    # moon.canvas, Notes.md, Dated.md, clutches, cards, mini, REI MINI BOW,
    # BOW BOOTS, gloves, moon shoe = 10; 4 image attachments (plain.png shared
    # between canvas and md embed) -> 3 sha-unique media
    assert "Would import into project 'rei by Rei': 8 designs, 10 entries, 3 media" in report
    assert "(4 image attachments, deduped by sha256)" in report
    assert "2 canvases fuzzy-assigned, 2 open questions" in report
    # provenance + assignment annotations
    assert "**gloves** (canvas name)" in report
    assert "**Moon Bag** (folder)" in report
    assert (
        "- assigned: `Designs/SHOES/BOW BOOTS.canvas` → design "
        "**Rei's Bow Boots** (assigned into)" in report
    )
    # sections
    assert "## QUESTIONS — need your call" in report
    assert "`Designs/BAGS/BOW BAG/random thing.canvas` (canvas)" in report
    assert "`Designs/BAGS/Trims.md` (note)" in report
    assert "⚠ empty design shell" in report
    assert "'Moon Bag' imported as 'Moon Bag (Shoes)'" in report
    assert "`Designs/BAGS/Untitled 1.canvas`" in report
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


# ── category: sent on creation; standalone --backfill-categories mode ────────
# (fake http client — the write path is exercised for real by the API suite;
# here we pin WHAT the importer sends, with zero network)

class _FakeResp:
    def __init__(self, payload):
        self.status_code = 200
        self.text = ""
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHttp:
    """Stands in for ApiImporter.http: canned GETs, recorded POSTs/PATCHes."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.posts: list[tuple[str, dict]] = []
        self.patches: list[tuple[str, dict]] = []

    def get(self, path):
        return _FakeResp(self.routes[path])

    def post(self, path, json):
        self.posts.append((path, json))
        return _FakeResp({"id": "new-design"})

    def patch(self, path, json):
        self.patches.append((path, json))
        return _FakeResp({})


def _importer_with(routes: dict):
    from scripts.import_obsidian import ApiImporter

    imp = ApiImporter("http://unused", "not-a-real-token")
    imp.http = _FakeHttp(routes)
    return imp


def test_write_sends_category_on_design_creation(vault: Path):
    plan = scan_vault(vault)
    moon = _by_name(plan)["Moon Bag"]
    imp = _importer_with({"/projects/p1/designs": []})  # nothing exists yet
    assert imp.ensure_design("p1", moon) == "new-design"
    (path, payload), = imp.http.posts
    assert path == "/designs"
    assert payload["category"] == "Bags"                # title-cased from BAGS/
    assert payload["name"] == "Moon Bag"
    assert payload["project_id"] == "p1"


def test_backfill_categories_patches_only_existing_null(vault: Path, capsys):
    plan = scan_vault(vault)
    imp = _importer_with({
        "/projects": [{"id": "p1", "name": PROJECT_NAME}],
        "/projects/p1/designs": [
            {"id": "d-moon", "name": "Moon Bag", "category": None},      # -> patched
            {"id": "d-gloves", "name": "gloves", "category": "Custom"},  # hand-set: kept
            {"id": "d-alien", "name": "Not In The Vault", "category": None},  # unplanned
        ],
    })
    imp.backfill_categories(plan)
    assert imp.http.patches == [("/designs/d-moon", {"category": "Bags"})]
    assert imp.http.posts == []                          # backfill creates nothing
    out = capsys.readouterr().out
    assert "1 set, 1 already had one, 6 planned designs not in the project" in out


def test_backfill_missing_project_is_a_clean_exit(vault: Path):
    plan = scan_vault(vault)
    imp = _importer_with({"/projects": []})
    with pytest.raises(SystemExit, match="not found"):
        imp.backfill_categories(plan)
    assert imp.http.patches == []


def test_backfill_requires_api_base_and_token(vault: Path):
    with pytest.raises(SystemExit):
        main(["--vault", str(vault), "--backfill-categories"])
    with pytest.raises(SystemExit):  # and it never combines with --write
        main(["--vault", str(vault), "--write", "--backfill-categories",
              "--api-base", "http://localhost:1", "--token", "t"])
