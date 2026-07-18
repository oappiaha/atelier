"""Seed the Sanzo Wada corpus (TDD §2.3) — idempotent.

    python -m app.wada.seed_sanzo

Source: mattdesl/dictionary-of-colour-combinations (159 colours, 348
combinations), vendored at app/wada/data/colors.json — static reference
data, seeded once, never user-writable. The seed UPSERTs on primary key,
so re-running converges instead of duplicating.

Derivations (all pure python, app.wada.color):
- lab_l/a/b: from hex, sRGB → CIELAB, D65 illuminant, 2° observer. (The
  corpus's own `lab` field is CMYK-print-profile-derived and disagrees with
  the hex swatch by up to ΔE≈20; we keep hex↔lab consistent — see color.py.)
- chroma = √(a²+b²); hue_deg = atan2(b, a) normalised to [0,360).
- hue_family / temperature: heuristic bands documented in color.py.
- palettes.id = 'c{n}' for corpus combination n (1..348, TDD example 'c102').
- palettes.name: the corpus has no editorial combination names, so we join
  the member colour names with ' · '.
- color_ids: ordered by corpus colour order (= sanzo id ascending).
- max/min_delta_e: pairwise CIEDE2000 over member colours.
- volume/plate: NULL — the corpus JSON carries no provenance fields.
"""

import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.wada.color import (
    chroma,
    delta_e_2000,
    hex_to_lab,
    hue_deg,
    hue_family,
    palette_temperature,
)

CORPUS_PATH = Path(__file__).parent / "data" / "colors.json"


def build_rows(corpus: list[dict]) -> tuple[list[dict], list[dict]]:
    """Pure transform: corpus JSON → (sanzo_colors rows, palettes rows)."""
    colors: list[dict] = []
    labs: dict[int, tuple[float, float, float]] = {}
    combos: dict[int, list[int]] = {}

    for idx, entry in enumerate(corpus):
        cid = idx + 1  # Wada's own numbering, 1..159 (TDD §2.3)
        lab = hex_to_lab(entry["hex"])
        labs[cid] = lab
        l_star, a, b = lab
        colors.append(
            {
                "id": cid,
                "name": entry["name"],
                "hex": entry["hex"].lower(),
                "lab_l": l_star,
                "lab_a": a,
                "lab_b": b,
                "chroma": chroma(a, b),
                "hue_deg": hue_deg(a, b),
                "hue_family": hue_family(a, b),
                "cmyk": json.dumps(entry["cmyk"]),
            }
        )
        for combo_no in entry["combinations"]:
            combos.setdefault(combo_no, []).append(cid)

    names = {c["id"]: c["name"] for c in colors}
    palettes: list[dict] = []
    for combo_no in sorted(combos):
        member_ids = combos[combo_no]  # already in corpus (= id) order
        member_labs = [labs[cid] for cid in member_ids]
        pairwise = [
            delta_e_2000(member_labs[i], member_labs[j])
            for i in range(len(member_labs))
            for j in range(i + 1, len(member_labs))
        ]
        palettes.append(
            {
                "id": f"c{combo_no}",
                "name": " · ".join(names[cid] for cid in member_ids),
                "color_count": len(member_ids),
                "color_ids": member_ids,
                "mean_lab_l": sum(lab[0] for lab in member_labs) / len(member_labs),
                "mean_chroma": sum(chroma(lab[1], lab[2]) for lab in member_labs)
                / len(member_labs),
                "temperature": palette_temperature(member_labs),
                "max_delta_e": max(pairwise),
                "min_delta_e": min(pairwise),
                "volume": None,
                "plate": None,
            }
        )
    return colors, palettes


async def seed(session: AsyncSession) -> tuple[int, int]:
    """Upsert the corpus; returns (colour count, palette count) after seeding.
    Caller commits."""
    corpus = json.loads(CORPUS_PATH.read_text())
    colors, palettes = build_rows(corpus)

    await session.execute(
        text(
            """
            INSERT INTO sanzo_colors
              (id, name, hex, lab_l, lab_a, lab_b, chroma, hue_deg, hue_family, cmyk)
            VALUES
              (:id, :name, :hex, :lab_l, :lab_a, :lab_b, :chroma, :hue_deg,
               :hue_family, :cmyk)
            ON CONFLICT (id) DO UPDATE SET
              name = EXCLUDED.name, hex = EXCLUDED.hex,
              lab_l = EXCLUDED.lab_l, lab_a = EXCLUDED.lab_a,
              lab_b = EXCLUDED.lab_b, chroma = EXCLUDED.chroma,
              hue_deg = EXCLUDED.hue_deg, hue_family = EXCLUDED.hue_family,
              cmyk = EXCLUDED.cmyk
            """
        ),
        colors,
    )
    await session.execute(
        text(
            """
            INSERT INTO palettes
              (id, name, color_count, color_ids, mean_lab_l, mean_chroma,
               temperature, max_delta_e, min_delta_e, volume, plate)
            VALUES
              (:id, :name, :color_count, :color_ids, :mean_lab_l, :mean_chroma,
               :temperature, :max_delta_e, :min_delta_e, :volume, :plate)
            ON CONFLICT (id) DO UPDATE SET
              name = EXCLUDED.name, color_count = EXCLUDED.color_count,
              color_ids = EXCLUDED.color_ids, mean_lab_l = EXCLUDED.mean_lab_l,
              mean_chroma = EXCLUDED.mean_chroma,
              temperature = EXCLUDED.temperature,
              max_delta_e = EXCLUDED.max_delta_e,
              min_delta_e = EXCLUDED.min_delta_e,
              volume = EXCLUDED.volume, plate = EXCLUDED.plate
            """
        ),
        palettes,
    )
    counts = (
        await session.execute(
            text(
                "SELECT (SELECT COUNT(*) FROM sanzo_colors),"
                "       (SELECT COUNT(*) FROM palettes)"
            )
        )
    ).one()
    return counts[0], counts[1]


async def _main() -> None:
    from app.db import SessionLocal, engine

    async with SessionLocal() as session:
        n_colors, n_palettes = await seed(session)
        await session.commit()
    await engine.dispose()
    print(f"sanzo_colors: {n_colors}  palettes: {n_palettes}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(_main())
