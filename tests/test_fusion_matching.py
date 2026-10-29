"""Exclusive search versus exhaustive small problems; ties and budgets stay explicit."""

from __future__ import annotations

import itertools
import random
import unittest

from chongming.modules.fusion_matching import ExclusiveChoice, exclusive_components


class ExclusiveSearchTests(unittest.TestCase):
    def test_search_finds_global_solution_instead_of_a_greedy_edge(self) -> None:
        choices = (
            ExclusiveChoice(frozenset(("a", "b")), (3, 0), 0.0),
            ExclusiveChoice(frozenset(("a",)), (2, 0), 0.0),
            ExclusiveChoice(frozenset(("b",)), (2, 0), 0.0),
        )
        (component,) = exclusive_components(choices, margin=0, maximum_nodes=100)
        self.assertFalse(component.truncated)
        self.assertEqual(component.alternatives, (frozenset((1, 2)),))

    def test_near_ties_are_retained_and_independent_components_remain_separate(self) -> None:
        choices = (
            ExclusiveChoice(frozenset(("a",)), (1, 0), 0.0),
            ExclusiveChoice(frozenset(("a",)), (1, 0), 0.05),
            ExclusiveChoice(frozenset(("b",)), (1, 0), 0.0),
        )
        first, second = exclusive_components(choices, margin=0.1, maximum_nodes=100)
        self.assertCountEqual(first.alternatives, (frozenset((0,)), frozenset((1,))))
        self.assertEqual(second.alternatives, (frozenset((2,)),))
        first, _ = exclusive_components(choices, margin=0.01, maximum_nodes=100)
        self.assertEqual(first.alternatives, (frozenset((0,)),))

    def test_search_exhaustion_cannot_make_a_visited_prefix_unique(self) -> None:
        choices = (ExclusiveChoice(frozenset(("a",)), (1, 0), 0.0),)
        (component,) = exclusive_components(choices, margin=0, maximum_nodes=1)
        self.assertTrue(component.truncated)
        self.assertFalse(component.alternatives)

    def test_small_random_components_match_exhaustive_enumeration(self) -> None:
        rng = random.Random(42)
        for case in range(100):
            choices = tuple(
                ExclusiveChoice(
                    frozenset(rng.sample(range(6), rng.randint(1, 3))),
                    (rng.randint(1, 3), rng.randint(0, 1)),
                    rng.randint(0, 8) / 4,
                )
                for _ in range(rng.randint(1, 8))
            )
            for component in exclusive_components(choices, margin=0.25, maximum_nodes=10000):
                possible = []
                indices = sorted(component.indices)
                for count in range(len(indices) + 1):
                    for subset in itertools.combinations(indices, count):
                        resources = [
                            resource for index in subset for resource in choices[index].resources
                        ]
                        if len(set(resources)) == len(resources):
                            rank = (
                                sum(choices[index].rank[0] for index in subset),
                                sum(choices[index].rank[1] for index in subset),
                            )
                            possible.append(
                                (
                                    frozenset(subset),
                                    rank,
                                    sum(choices[index].cost for index in subset),
                                )
                            )
                best_rank = max(rank for _, rank, _ in possible)
                best_cost = min(cost for _, rank, cost in possible if rank == best_rank)
                expected = [
                    subset
                    for subset, rank, cost in possible
                    if rank == best_rank and cost <= best_cost + 0.25
                ]
                with self.subTest(case=case, component=indices):
                    self.assertFalse(component.truncated)
                    self.assertCountEqual(component.alternatives, expected)


if __name__ == "__main__":
    unittest.main()
