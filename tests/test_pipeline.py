"""SIMULATED scenes through regions -> features -> decide, after calibrating on simulated genuine.

These check the plumbing and the decision logic against the attacker models in synthetic.py.
They are not evidence that the physics holds; that is M1's job (PROJECT.md §4).
"""

import pytest

from lumen.decide import calibrate, decide
from pipeline import offline


@pytest.fixture(scope="module")
def thresholds():
    return calibrate([offline("genuine", seed=s) for s in (1, 2, 3)])


@pytest.mark.parametrize("scenario, decision, needle", [
    ("genuine", "verify", "lean"),
    ("swap_unlit", "reject", "L1"),
    ("swap_inherit", "reject", "L3"),
    ("replay", "reject", "replay"),
    ("lamp", "abstain", "lamp"),
    ("far", "abstain", "neck reference"),
    ("relit", "reject", "L2"),
])
def test_scenario_verdicts(thresholds, scenario, decision, needle):
    v = decide(offline(scenario, seed=7), thresholds)
    assert v.decision == decision, v.reason
    assert needle in v.reason


def test_single_probe_cannot_catch_a_swap_that_inherits_the_probe(thresholds):
    """Why the two-sided probe is the default: L1 and L2 alone pass the inheriting swap."""
    single = calibrate([offline("genuine", layout="single", seed=s) for s in (1, 2, 3)])
    v = decide(offline("swap_inherit", layout="single", seed=7), single)
    assert v.decision == "verify"
    assert "L3 not evaluated" in v.reason
