"""Wada permutation algebra, pruning, ranking and trie planning (TDD §8.3–§8.8).

Pure maths — no I/O, no model calls. Everything here is unit-tested against
the TDD's own published numbers (the §8.4 cost table, the PRD §4.1/§4.2
pedagogy copy, the §8.8 trie examples).

Vocabulary
    K            paint slots (locks never participate — §8.3)
    C            palette colours
    assignment   tuple of length K: slot index → colour index. Colour indices
                 are DARKNESS RANKS: 0 = darkest (lowest L*). §8.8 makes the
                 darkest-first chain order canonical, which is what allows the
                 prefix-sharing trie; ranking colours by darkness up front
                 keeps every chain a sorted sequence of colour indices.
    chain        the generation call sequence for one assignment: one step per
                 DISTINCT colour used, darkest first; each step paints the
                 union mask of the slots sharing that colour.

Regimes (§8.4)
    K = C  bijective   K!
    K < C  injective   P(C, K) = C!/(C-K)!
    K > C  surjective  C! · S(K, C)   (Stirling numbers of the second kind)

Cost is parameterised by cost_cents_per_call (TDD §8.9: the adapter declares
its own price). DEFAULT_COST_CENTS_PER_CALL is Seedream's $0.0675/edit — the
price every table in the TDD and PRD is quoted at.
"""

import math
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from itertools import permutations as _permutations
from itertools import product as _product

DEFAULT_COST_CENTS_PER_CALL = Decimal("6.75")  # Seedream 5.0 Pro, $0.0675/edit
DEFAULT_CAP = 12  # §8.5 rule 4 / §8.6
DEFAULT_EAGER = 2  # §8.6: generate this many immediately
DEFAULT_TWIN_THRESHOLD = 8.0  # §8.5 rule 2, ΔE2000

Assignment = tuple[int, ...]
Step = tuple[int, frozenset[int]]  # (colour rank, slots painted in this call)
Chain = tuple[Step, ...]


# ── counting (§8.4) ──────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def stirling2(n: int, k: int) -> int:
    """Stirling numbers of the second kind."""
    if n == k:
        return 1
    if n == 0 or k == 0:
        return 0
    return k * stirling2(n - 1, k) + stirling2(n - 1, k - 1)


def perm_count(k: int, c: int) -> int:
    """Closed-form permutation count for K slots × C colours (§8.4)."""
    _validate(k, c)
    if k <= c:  # bijective (K=C ⇒ K!) and injective are both P(C, K)
        return math.perm(c, k)
    return math.factorial(c) * stirling2(k, c)  # surjective


def steps_per_colorway(k: int, c: int) -> int:
    """One model call per DISTINCT colour used (§8.4 'Steps per colorway')."""
    _validate(k, c)
    return min(k, c)


def naive_call_count(k: int, c: int) -> int:
    """Model calls with no prefix sharing: perms × steps."""
    return perm_count(k, c) * steps_per_colorway(k, c)


def _validate(k: int, c: int) -> None:
    if k < 1 or c < 1:
        raise ValueError(f"K and C must be >= 1 (got K={k}, C={c})")


# ── enumeration ──────────────────────────────────────────────────────────────

def enumerate_assignments(k: int, c: int) -> list[Assignment]:
    """Every valid slot→colour assignment, lexicographically ordered.

    K ≤ C: injective (distinct colour per slot). K > C: surjective (every
    colour appears at least once). Deterministic order — §8.7 tie-breaking
    and the cache depend on it.
    """
    _validate(k, c)
    if k <= c:
        return sorted(_permutations(range(c), k))
    return [a for a in _product(range(c), repeat=k) if len(set(a)) == c]


def chain_for(assignment: Assignment) -> Chain:
    """The §8.8 call chain: distinct colours darkest-first, union-masked."""
    steps: dict[int, set[int]] = {}
    for slot, colour in enumerate(assignment):
        steps.setdefault(colour, set()).add(slot)
    return tuple((colour, frozenset(slots)) for colour, slots in sorted(steps.items()))


# ── trie planning (§8.8) ─────────────────────────────────────────────────────

def trie_prefixes(assignments: list[Assignment]) -> set[Chain]:
    """Every distinct non-empty chain prefix = one trie node = one model call.

    Two permutations whose chains agree on their first d steps produce the
    identical intermediate image; the trie caches it once (§8.8).
    """
    nodes: set[Chain] = set()
    for a in assignments:
        chain = chain_for(a)
        for d in range(1, len(chain) + 1):
            nodes.add(chain[:d])
    return nodes


def trie_call_count(k: int, c: int) -> int:
    """Trie node count for the full (unpruned) space. §8.8: K=C=3 → 15,
    K=C=4 → 64 ('33% saved, deterministically')."""
    return len(trie_prefixes(enumerate_assignments(k, c)))


def saved_pct(k: int, c: int) -> int:
    """Percentage of naive calls the trie avoids, as the §8.4 table prints it."""
    naive = naive_call_count(k, c)
    return round((1 - trie_call_count(k, c) / naive) * 100)


# ── cost (§8.4 table / §8.9 adapter pricing) ─────────────────────────────────

def cost_dollars(
    calls: int, cost_cents_per_call: Decimal = DEFAULT_COST_CENTS_PER_CALL
) -> Decimal:
    """Dollar cost of `calls` model calls, rounded to cents (half-up) —
    reproduces every $ cell in the §8.4 and PRD §4.1 tables."""
    cents = Decimal(calls) * cost_cents_per_call
    return (cents / 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def estimate(
    k: int,
    c: int,
    cost_cents_per_call: Decimal = DEFAULT_COST_CENTS_PER_CALL,
    cap: int | None = DEFAULT_CAP,
) -> dict:
    """The pure numbers behind every pre-commit UI readout (§8.13, §9).

    `planned` = what a capped study would actually generate; its cost is an
    upper bound (cap × steps — prefix sharing can only make it cheaper).
    """
    perms = perm_count(k, c)
    naive = naive_call_count(k, c)
    trie = trie_call_count(k, c)
    planned = perms if cap is None else min(perms, cap)
    return {
        "k": k,
        "c": c,
        "perms": perms,
        "steps_per_colorway": steps_per_colorway(k, c),
        "naive_calls": naive,
        "naive_cost": cost_dollars(naive, cost_cents_per_call),
        "trie_calls": trie,
        "trie_cost": cost_dollars(trie, cost_cents_per_call),
        "saved_pct": saved_pct(k, c),
        "planned": planned,
        "planned_max_cost": cost_dollars(
            planned * steps_per_colorway(k, c), cost_cents_per_call
        ),
    }


# ── pruning (§8.5) ───────────────────────────────────────────────────────────

def apply_anchors(k: int, c: int, n_anchors: int) -> tuple[int, int]:
    """Rule 1: each pinned colour→slot binding removes both from the domain.
    K=4,C=4 → 24 perms; one anchor → K=3,C=3 → 6. One tap, 75% cheaper."""
    if n_anchors < 0 or n_anchors > min(k, c):
        raise ValueError(f"anchors must be within 0..min(K,C) (got {n_anchors})")
    return k - n_anchors, c - n_anchors


def twin_classes(
    labs: list[tuple[float, float, float]],
    threshold: float = DEFAULT_TWIN_THRESHOLD,
) -> list[int]:
    """Rule 2: union-find equivalence classes of perceptual twins
    (ΔE2000 < threshold). Returns class id per colour (root index)."""
    from app.wada.color import delta_e_2000

    parent = list(range(len(labs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(labs)):
        for j in range(i + 1, len(labs)):
            if delta_e_2000(labs[i], labs[j]) < threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
    return [find(i) for i in range(len(labs))]


def dedupe_groups(
    assignments: list[Assignment], classes: list[int]
) -> dict[tuple[int, ...], list[Assignment]]:
    """Rule 2 (continued): permutations that differ ONLY by swapping members
    of a twin class share a dedupe group. Group key = per-slot class tuple.
    The first assignment in each group (lexicographic) is the representative
    (`is_representative=true` in gen_colorways — §8.7/§2.4)."""
    groups: dict[tuple[int, ...], list[Assignment]] = {}
    for a in sorted(assignments):
        groups.setdefault(tuple(classes[colour] for colour in a), []).append(a)
    return groups


def representatives(
    assignments: list[Assignment], classes: list[int]
) -> list[Assignment]:
    """One representative per dedupe group, deterministic order."""
    return sorted(g[0] for g in dedupe_groups(assignments, classes).values())


# ── ranking and diversity (§8.7) ─────────────────────────────────────────────

def prominence(lab: tuple[float, float, float]) -> float:
    """(100 − L*) + chroma: dark and saturated = loud."""
    l_star, a, b = lab
    return (100.0 - l_star) + math.hypot(a, b)


def score(
    assignment: Assignment,
    labs: list[tuple[float, float, float]],
    areas: list[float],
) -> float:
    """Σ over paint slots of prominence(colour) × area_fraction. A loud colour
    on the body outranks a loud colour on a buckle."""
    return sum(prominence(labs[colour]) * areas[slot] for slot, colour in enumerate(assignment))


def signature(
    assignment: Assignment,
    labs: list[tuple[float, float, float]],
    areas: list[float],
) -> tuple[float, ...]:
    """Area-weighted LAB vector: slots in canonical order (area desc, then
    slot index), concat [area·L, area·a, area·b]."""
    order = sorted(range(len(assignment)), key=lambda s: (-areas[s], s))
    sig: list[float] = []
    for slot in order:
        l_star, a, b = labs[assignment[slot]]
        area = areas[slot]
        sig.extend((area * l_star, area * a, area * b))
    return tuple(sig)


def rank(
    assignments: list[Assignment],
    labs: list[tuple[float, float, float]],
    areas: list[float],
) -> list[Assignment]:
    """Score desc; ties break lexicographically on the mapping tuple, so the
    ordering is DETERMINISTIC — which the cache depends on (§8.7)."""
    return sorted(assignments, key=lambda a: (-score(a, labs, areas), a))


def select_diverse(
    assignments: list[Assignment],
    labs: list[tuple[float, float, float]],
    areas: list[float],
    cap: int = DEFAULT_CAP,
) -> list[Assignment]:
    """Greedy farthest-point (max-min) sampling over signatures, seeded with
    the top-scoring assignment (§8.7). Deterministic: distance ties break on
    higher score, then lexicographic order."""
    if cap <= 0:
        return []
    ranked = rank(assignments, labs, areas)
    if len(ranked) <= cap:
        return ranked
    sigs = {a: signature(a, labs, areas) for a in ranked}
    selected = [ranked[0]]
    candidates = ranked[1:]
    while len(selected) < cap:
        best = max(
            candidates,
            key=lambda a: (
                min(math.dist(sigs[a], sigs[s]) for s in selected),
                score(a, labs, areas),
                tuple(-x for x in a),
            ),
        )
        selected.append(best)
        candidates.remove(best)
    return selected


def plan_study(
    labs: list[tuple[float, float, float]],
    areas: list[float],
    cap: int = DEFAULT_CAP,
    eager: int = DEFAULT_EAGER,
    twin_threshold: float | None = DEFAULT_TWIN_THRESHOLD,
) -> dict:
    """End-to-end §8.4→§8.7 planning for K=len(areas) slots × C=len(labs)
    colours: enumerate → collapse twins → rank + diversity-cap → eager split.
    Returns assignments only — generating them is M7's job."""
    k, c = len(areas), len(labs)
    space = enumerate_assignments(k, c)
    if twin_threshold is not None:
        space = representatives(space, twin_classes(labs, twin_threshold))
    selected = select_diverse(space, labs, areas, cap=cap)
    return {
        "perm_total": perm_count(k, c),
        "after_dedupe": len(space),
        "selected": selected,
        "eager": selected[:eager],
        "deferred": selected[eager:],
        "trie_calls_selected": len(trie_prefixes(selected)),
    }
