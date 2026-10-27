"""Bounded exclusive-evidence search shared by ray groups and track assignments."""

from __future__ import annotations

import math
from collections.abc import Hashable
from dataclasses import dataclass


@dataclass(frozen=True)
class ExclusiveChoice:
    resources: frozenset[Hashable]
    rank: tuple[int, int]
    cost: float


@dataclass(frozen=True)
class ExclusiveComponent:
    indices: frozenset[int]
    alternatives: tuple[frozenset[int], ...]
    truncated: bool


def exclusive_components(
    choices: tuple[ExclusiveChoice, ...], *, margin: float, maximum_nodes: int
) -> tuple[ExclusiveComponent, ...]:
    """Maximize integer rank, then minimize cost; retain near-optimal alternatives.

    Independent conflict components are solved separately. An exhausted component
    has no asserted solution, even if its visited prefix appeared unambiguous.
    """
    owners: dict[Hashable, set[int]] = {}
    for index, choice in enumerate(choices):
        for resource in choice.resources:
            owners.setdefault(resource, set()).add(index)
    conflicts = [
        set().union(*(owners[resource] for resource in choice.resources)) for choice in choices
    ]
    remaining = set(range(len(choices)))
    results = []
    while remaining:
        component: set[int] = set()
        pending = {min(remaining)}
        while pending:
            index = pending.pop()
            if index not in component:
                component.add(index)
                pending.update(conflicts[index] - component)
        remaining.difference_update(component)
        best_rank = (-1, -1)
        best_cost = math.inf
        solutions: list[tuple[frozenset[int], float]] = []
        stack: list[tuple[frozenset[int], frozenset[int], tuple[int, int], float]] = [
            (frozenset(component), frozenset(), (0, 0), 0.0)
        ]
        nodes = 0
        while stack and nodes < maximum_nodes:
            available, selected, rank, cost = stack.pop()
            nodes += 1
            upper = (
                rank[0] + sum(choices[i].rank[0] for i in available),
                rank[1] + sum(choices[i].rank[1] for i in available),
            )
            tolerance = 32 * math.ulp(max(1.0, abs(best_cost))) if math.isfinite(best_cost) else 0.0
            if upper < best_rank or (upper == best_rank and cost > best_cost + margin + tolerance):
                continue
            if not available:
                if rank > best_rank:
                    best_rank, best_cost, solutions = rank, cost, [(selected, cost)]
                elif rank == best_rank:
                    best_cost = min(best_cost, cost)
                    solutions.append((selected, cost))
                continue
            index = min(
                available,
                key=lambda i: (-choices[i].rank[0], -choices[i].rank[1], choices[i].cost, i),
            )
            choice = choices[index]
            stack.append((available - {index}, selected, rank, cost))
            stack.append(
                (
                    available - conflicts[index],
                    selected | {index},
                    (rank[0] + choice.rank[0], rank[1] + choice.rank[1]),
                    cost + choice.cost,
                )
            )
        tolerance = 32 * math.ulp(max(1.0, abs(best_cost))) if math.isfinite(best_cost) else 0.0
        alternatives = (
            ()
            if stack
            else tuple(
                selected for selected, cost in solutions if cost <= best_cost + margin + tolerance
            )
        )
        results.append(ExclusiveComponent(frozenset(component), alternatives, bool(stack)))
    return tuple(results)
