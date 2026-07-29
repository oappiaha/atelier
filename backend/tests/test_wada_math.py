"""Wada permutation maths vs the TDD's own published numbers.

TDD §12: "Permutation algebra: property tests against the exact table in 8.4.
For every (K,C) in 2..5 x 2..4, assert the generated count equals the closed
form. This is pure maths and must never be wrong."

Pure module — no DB, no app, no fixtures.
"""

from decimal import Decimal

import pytest

from app.wada import permutations as perm
from app.wada.color import delta_e_2000, hex_to_lab

# ── the §8.4 table, verbatim (K, C, perms, naive calls, naive $, trie, trie $, saved %)
TDD_84_TABLE = [
    (2, 2, 2, 4, "0.27", 4, "0.27", 0),
    (2, 3, 6, 12, "0.81", 10, "0.68", 17),
    (2, 4, 12, 24, "1.62", 18, "1.22", 25),
    (3, 2, 6, 12, "0.81", 12, "0.81", 0),
    (3, 3, 6, 18, "1.22", 15, "1.01", 17),
    (3, 4, 24, 72, "4.86", 48, "3.24", 33),
    (4, 2, 14, 28, "1.89", 28, "1.89", 0),
    (4, 3, 36, 108, "7.29", 82, "5.54", 24),
    (4, 4, 24, 96, "6.48", 64, "4.32", 33),
    (5, 3, 150, 450, "30.38", 325, "21.94", 28),
    (5, 4, 240, 960, "64.80", 575, "38.81", 40),
]


@pytest.mark.parametrize("k,c,perms,naive,naive_d,trie,trie_d,saved", TDD_84_TABLE)
def test_tdd_84_table_row(k, c, perms, naive, naive_d, trie, trie_d, saved):
    """Every cell of the §8.4 cost table, at Seedream's $0.0675/edit."""
    assert perm.perm_count(k, c) == perms
    assert perm.naive_call_count(k, c) == naive
    assert perm.cost_dollars(naive) == Decimal(naive_d)
    assert perm.trie_call_count(k, c) == trie
    assert perm.cost_dollars(trie) == Decimal(trie_d)
    assert perm.saved_pct(k, c) == saved


def test_tdd_88_trie_examples():
    """§8.8 verbatim: '6 permutations x 3 steps = 18 naive calls -> 15 trie
    nodes. K=4,C=4: 96 naive -> 64 nodes. 33% saved, deterministically.'"""
    assert perm.naive_call_count(3, 3) == 18
    assert perm.trie_call_count(3, 3) == 15
    assert perm.naive_call_count(4, 4) == 96
    assert perm.trie_call_count(4, 4) == 64
    assert perm.saved_pct(4, 4) == 33  # decision log: "33% saved at K=C=4"


def test_k3c3_trie_cost_is_1_dollar_01():
    """The M3-research-validated number: K=C=3 → 15 trie calls → $1.01 at
    $0.0675/call ('3 slots x 3-colour palette = 6 permutations, ~$1.01')."""
    calls = perm.trie_call_count(3, 3)
    assert calls == 15
    assert perm.cost_dollars(calls, Decimal("6.75")) == Decimal("1.01")


def test_prd_41_naive_cost_table():
    """PRD §4.1 table: naive cost at $0.0675/edit for the published rows."""
    rows = [
        (2, 2, 2, "0.27"),
        (3, 3, 6, "1.22"),
        (4, 4, 24, "6.48"),
        (4, 3, 36, "7.29"),
        (5, 3, 150, "30.38"),
        (5, 4, 240, "64.80"),
    ]
    for k, c, perms, dollars in rows:
        assert perm.perm_count(k, c) == perms, (k, c)
        assert perm.cost_dollars(perm.naive_call_count(k, c)) == Decimal(dollars), (k, c)


def test_prd_42_pedagogy_36_vs_24_vs_6():
    """PRD §4.2: '4 slots and a 3-colour palette → 36' (surjective grows
    fastest), K=C=4 → 24 (recommended shape), K=C=3 → 6."""
    assert perm.perm_count(4, 3) == 36
    assert perm.perm_count(4, 4) == 24
    assert perm.perm_count(3, 3) == 6


def test_tdd_83_slot_pedagogy_540():
    """§8.3/decision log: 6 regions left as 6 slots with a 3-colour palette
    is 540 permutations; composed into 3 slots it is 6."""
    assert perm.perm_count(6, 3) == 540
    assert perm.perm_count(3, 3) == 6


def test_tdd_85_anchor_cuts_24_to_6():
    """§8.5 rule 1: K=4,C=4 → 24; anchor body=Prussian Blue → K=3,C=3 → 6.
    One tap, 75% cheaper."""
    assert perm.perm_count(4, 4) == 24
    k, c = perm.apply_anchors(4, 4, 1)
    assert (k, c) == (3, 3)
    assert perm.perm_count(k, c) == 6
    assert perm.perm_count(k, c) / perm.perm_count(4, 4) == 0.25


def test_closed_form_equals_enumeration_for_all_regimes():
    """§12: for every (K,C) in 2..5 × 2..4 the closed form equals the
    enumerated count, and every enumerated assignment is regime-valid."""
    for k in range(2, 6):
        for c in range(2, 5):
            space = perm.enumerate_assignments(k, c)
            assert len(space) == perm.perm_count(k, c), (k, c)
            assert len(set(space)) == len(space), (k, c)  # no dupes
            for a in space:
                if k <= c:
                    assert len(set(a)) == k, (k, c, a)  # injective
                else:
                    assert set(a) == set(range(c)), (k, c, a)  # surjective


def test_trie_leaves_map_one_to_one_to_permutations():
    """§12 'Trie correctness': node count for (4,4) is exactly 64; every leaf
    maps to exactly one permutation; no orphans."""
    for k, c in [(3, 3), (4, 4), (4, 3), (2, 4)]:
        space = perm.enumerate_assignments(k, c)
        nodes = perm.trie_prefixes(space)
        depth = perm.steps_per_colorway(k, c)
        leaves = {n for n in nodes if len(n) == depth}
        # leaves == full chains == permutations, bijectively
        assert len(leaves) == len(space), (k, c)
        assert leaves == {perm.chain_for(a) for a in space}, (k, c)
        # no orphans: every node is a prefix of some leaf
        for node in nodes:
            assert any(leaf[: len(node)] == node for leaf in leaves), (k, c, node)
    assert perm.trie_call_count(4, 4) == 64


def test_chains_are_darkest_first():
    """§8.8: colour rank 0 = darkest; chains are strictly ordered by rank."""
    for a in perm.enumerate_assignments(4, 3):
        ranks = [colour for colour, _slots in perm.chain_for(a)]
        assert ranks == sorted(ranks)


def test_twin_dedupe_halves_a_twin_palette():
    """§8.5 rule 2: two palette colours within ΔE 8 collapse permutations
    that differ only by swapping them. K=C=3 with one twin pair: 6 → 3."""
    labs = [(50.0, 0.0, 0.0), (52.0, 1.0, 0.0), (20.0, 40.0, 40.0)]
    assert delta_e_2000(labs[0], labs[1]) < 8.0
    assert delta_e_2000(labs[0], labs[2]) >= 8.0
    classes = perm.twin_classes(labs)
    assert classes[0] == classes[1] != classes[2]
    space = perm.enumerate_assignments(3, 3)
    groups = perm.dedupe_groups(space, classes)
    assert len(groups) == 3
    assert all(len(g) == 2 for g in groups.values())
    reps = perm.representatives(space, classes)
    assert len(reps) == 3
    assert reps == sorted(reps)  # deterministic representative order


def test_no_twins_means_no_collapse():
    labs = [(10.0, 0.0, 0.0), (50.0, 40.0, 0.0), (90.0, -40.0, 40.0)]
    reps = perm.representatives(perm.enumerate_assignments(3, 3), perm.twin_classes(labs))
    assert len(reps) == 6


def test_ranking_is_deterministic_and_area_weighted():
    """§8.7: a loud colour on the body outranks a loud colour on a buckle."""
    labs = [(20.0, 50.0, 30.0), (85.0, 5.0, 5.0)]  # 0 = loud/dark, 1 = quiet/light
    areas = [0.9, 0.1]  # slot 0 is the body
    space = perm.enumerate_assignments(2, 2)
    ranked = perm.rank(space, labs, areas)
    assert ranked[0] == (0, 1)  # loud colour on the big slot wins
    assert ranked == perm.rank(list(reversed(space)), labs, areas)  # order-independent


def test_capped_diverse_selection():
    """§8.5 rule 4 + §8.7: cap 12 (default), seeded with the top score,
    deterministic, and eager-2 split per §8.6."""
    labs = [(15.0, 40.0, 30.0), (45.0, -30.0, 25.0), (70.0, 10.0, -40.0), (92.0, 2.0, 2.0)]
    areas = [0.5, 0.3, 0.15, 0.05]
    plan = perm.plan_study(labs, areas, cap=12, eager=2, twin_threshold=None)
    assert plan["perm_total"] == 24
    assert plan["after_dedupe"] == 24  # no twins in this palette
    assert len(plan["selected"]) == 12  # the cap
    assert len(set(plan["selected"])) == 12
    ranked = perm.rank(perm.enumerate_assignments(4, 4), labs, areas)
    assert plan["selected"][0] == ranked[0]  # seeded with argmax score
    assert plan["eager"] == plan["selected"][:2]
    assert plan["deferred"] == plan["selected"][2:]
    # deterministic across runs
    assert plan["selected"] == perm.plan_study(labs, areas, cap=12, twin_threshold=None)["selected"]
    # trie planning over the selected subset never exceeds the full-space trie
    assert plan["trie_calls_selected"] <= perm.trie_call_count(4, 4)


def test_selection_below_cap_returns_everything_ranked():
    labs = [(15.0, 40.0, 30.0), (45.0, -30.0, 25.0), (70.0, 10.0, -40.0)]
    areas = [0.6, 0.3, 0.1]
    plan = perm.plan_study(labs, areas, cap=12, twin_threshold=None)
    assert plan["selected"] == perm.rank(perm.enumerate_assignments(3, 3), labs, areas)


def test_cost_parameterised_by_cost_cents_per_call():
    """§8.9: the adapter declares its price; the maths must follow it."""
    assert perm.cost_dollars(15, Decimal("6.75")) == Decimal("1.01")
    assert perm.cost_dollars(15, Decimal("13.50")) == Decimal("2.03")  # 2x price
    assert perm.cost_dollars(15, Decimal(2)) == Decimal("0.30")
    est = perm.estimate(3, 3, cost_cents_per_call=Decimal("6.75"))
    assert est["perms"] == 6
    assert est["trie_calls"] == 15
    assert est["trie_cost"] == Decimal("1.01")
    assert est["naive_cost"] == Decimal("1.22")
    assert est["planned"] == 6  # under the cap of 12


def test_ciede2000_reference_pairs():
    """Sharma, Wu & Dalal (2005) published test data — pins the ΔE2000
    implementation the twin rule and palette contrast columns rely on."""
    cases = [
        ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
        ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
        ((50.0, 2.8361, -74.02), (50.0, 0.0, -82.7485), 3.4412),
        ((50.0, 2.5, 0.0), (50.0, 0.0, -2.5), 4.3065),
        ((90.8027, -2.0831, 1.441), (91.1528, -1.6435, 0.0447), 1.4441),
    ]
    for lab1, lab2, expected in cases:
        assert delta_e_2000(lab1, lab2) == pytest.approx(expected, abs=1e-4)
        assert delta_e_2000(lab2, lab1) == pytest.approx(expected, abs=1e-4)


def test_hex_to_lab_anchors():
    """sRGB D65 anchors: white=100/0/0, black=0/0/0, mid grey is neutral."""
    lum, a, b = hex_to_lab("#ffffff")
    assert lum == pytest.approx(100.0, abs=0.01)
    assert a == pytest.approx(0.0, abs=0.01)
    assert b == pytest.approx(0.0, abs=0.01)
    assert hex_to_lab("#000000") == pytest.approx((0.0, 0.0, 0.0), abs=1e-6)
    lum, a, b = hex_to_lab("#808080")
    assert lum == pytest.approx(53.59, abs=0.01)
    assert abs(a) < 0.01 and abs(b) < 0.01
