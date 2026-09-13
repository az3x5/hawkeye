"""The identity decision layer.

Pure logic: no database, no model, no HTTP. These tests pin the rules that
turn measurements into proposals — the part a contested decision will be
argued about.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.domain.identity import (
    Candidate,
    CaptureAssurance,
    DecisionOutcome,
    DecisionThresholds,
    IdentityDecision,
    ReviewOutcome,
    aggregate_candidates,
    decide,
)
from app.domain.vectors import VectorMatch

POLICY = DecisionThresholds(accept_at=0.62, review_at=0.42, policy_version="test-v1")


def _match(score: float, person: UUID | None = None, sample: UUID | None = None) -> VectorMatch:
    return VectorMatch(
        face_sample_uuid=sample or uuid4(),
        person_uuid=person or uuid4(),
        score=score,
    )


class TestThresholds:
    def test_accept_must_sit_above_review(self) -> None:
        with pytest.raises(ValueError, match="must be above"):
            DecisionThresholds(accept_at=0.4, review_at=0.6, policy_version="v1")

    def test_equal_thresholds_leave_no_review_band(self) -> None:
        with pytest.raises(ValueError, match="no band"):
            DecisionThresholds(accept_at=0.5, review_at=0.5, policy_version="v1")

    @pytest.mark.parametrize("bad", [-1.5, 1.5])
    def test_thresholds_must_be_similarities(self, bad: float) -> None:
        with pytest.raises(ValueError, match=r"\[-1, 1\]"):
            DecisionThresholds(accept_at=bad, review_at=-0.9, policy_version="v1")

    def test_a_policy_version_is_required(self) -> None:
        with pytest.raises(ValueError, match="policy_version"):
            DecisionThresholds(accept_at=0.6, review_at=0.4, policy_version="  ")

    def test_negative_thresholds_are_permitted(self) -> None:
        """Cosine similarity is signed; the domain does not assume otherwise."""
        policy = DecisionThresholds(accept_at=-0.1, review_at=-0.5, policy_version="v1")
        assert policy.accept_at == pytest.approx(-0.1)


class TestAggregation:
    def test_a_person_is_scored_by_their_best_sample(self) -> None:
        person = uuid4()
        best = uuid4()
        candidates = aggregate_candidates(
            [
                _match(0.30, person),
                _match(0.80, person, best),
                _match(0.55, person),
            ]
        )
        assert len(candidates) == 1
        assert candidates[0].score == pytest.approx(0.80)
        assert candidates[0].face_sample_uuid == best

    def test_a_weak_sample_does_not_drag_a_person_down(self) -> None:
        """Averaging would punish the sample diversity the system collects."""
        person = uuid4()
        candidates = aggregate_candidates([_match(0.90, person), _match(0.05, person)])
        assert candidates[0].score == pytest.approx(0.90)

    def test_sample_count_reports_the_evidence_seen(self) -> None:
        person = uuid4()
        candidates = aggregate_candidates([_match(0.5, person), _match(0.6, person)])
        assert candidates[0].sample_count == 2

    def test_candidates_are_ordered_by_score(self) -> None:
        candidates = aggregate_candidates([_match(0.2), _match(0.9), _match(0.5)])
        assert [c.score for c in candidates] == pytest.approx([0.9, 0.5, 0.2])

    def test_ordering_is_deterministic_for_tied_scores(self) -> None:
        first, second = uuid4(), uuid4()
        ordering = [
            [c.person_uuid for c in aggregate_candidates([_match(0.5, first), _match(0.5, second)])]
            for _ in range(5)
        ]
        assert all(result == ordering[0] for result in ordering)

    def test_no_matches_gives_no_candidates(self) -> None:
        assert aggregate_candidates([]) == ()


class TestDecisionBands:
    def test_a_strong_match_is_accepted(self) -> None:
        outcome = decide([_match(0.91)], POLICY, CaptureAssurance.SUPERVISED).outcome
        assert outcome is DecisionOutcome.ACCEPT

    def test_a_middling_match_goes_to_review(self) -> None:
        assert decide([_match(0.50)], POLICY).outcome is DecisionOutcome.REVIEW

    def test_a_weak_match_is_rejected(self) -> None:
        assert decide([_match(0.10)], POLICY).outcome is DecisionOutcome.REJECT

    def test_nobody_at_all_is_rejected(self) -> None:
        decision = decide([], POLICY)
        assert decision.outcome is DecisionOutcome.REJECT
        assert decision.best is None

    def test_the_accept_boundary_is_inclusive(self) -> None:
        decision = decide([_match(POLICY.accept_at)], POLICY, CaptureAssurance.SUPERVISED)
        assert decision.outcome is DecisionOutcome.ACCEPT

    def test_just_below_accept_asks_for_review(self) -> None:
        assert decide([_match(POLICY.accept_at - 1e-9)], POLICY).outcome is DecisionOutcome.REVIEW

    def test_the_review_boundary_is_inclusive(self) -> None:
        assert decide([_match(POLICY.review_at)], POLICY).outcome is DecisionOutcome.REVIEW

    def test_just_below_review_is_rejected(self) -> None:
        assert decide([_match(POLICY.review_at - 1e-9)], POLICY).outcome is DecisionOutcome.REJECT

    def test_only_the_top_candidate_sets_the_outcome(self) -> None:
        decision = decide(
            [_match(0.95), _match(0.10), _match(0.05)], POLICY, CaptureAssurance.SUPERVISED
        )
        assert decision.outcome is DecisionOutcome.ACCEPT
        assert len(decision.candidates) == 3


class TestPolicyIsNotBakedIn:
    def test_the_same_evidence_decides_differently_under_another_policy(self) -> None:
        evidence = [_match(0.55)]
        lenient = DecisionThresholds(accept_at=0.50, review_at=0.30, policy_version="lenient")
        strict = DecisionThresholds(accept_at=0.90, review_at=0.80, policy_version="strict")

        supervised = CaptureAssurance.SUPERVISED
        assert decide(evidence, lenient, supervised).outcome is DecisionOutcome.ACCEPT
        assert decide(evidence, strict, supervised).outcome is DecisionOutcome.REJECT

    def test_the_decision_carries_the_policy_that_produced_it(self) -> None:
        decision = decide([_match(0.7)], POLICY)
        assert decision.thresholds is POLICY
        assert decision.thresholds.policy_version == "test-v1"


class TestScoresAreNotProbabilities:
    def test_a_negative_similarity_is_representable(self) -> None:
        decision = decide([_match(-0.4)], POLICY)
        assert decision.candidates[0].score == pytest.approx(-0.4)
        assert decision.outcome is DecisionOutcome.REJECT

    def test_scores_are_not_normalised_across_candidates(self) -> None:
        """Nothing sums to one: these are measurements, not a distribution."""
        decision = decide([_match(0.8), _match(0.7), _match(0.6)], POLICY)
        assert sum(c.score for c in decision.candidates) > 1.0

    def test_an_out_of_range_score_is_refused(self) -> None:
        with pytest.raises(ValueError, match=r"\[-1, 1\]"):
            Candidate(person_uuid=uuid4(), face_sample_uuid=uuid4(), score=1.2, sample_count=1)


class TestMargin:
    def test_margin_reports_the_gap_to_the_runner_up(self) -> None:
        decision = decide([_match(0.90), _match(0.85)], POLICY)
        assert decision.margin == pytest.approx(0.05)

    def test_a_lone_candidate_has_no_margin(self) -> None:
        assert decide([_match(0.9)], POLICY).margin is None

    def test_no_candidates_have_no_margin(self) -> None:
        assert decide([], POLICY).margin is None

    def test_a_narrow_margin_does_not_change_the_outcome_by_itself(self) -> None:
        """The number is surfaced for a reviewer; acting on it is their call."""
        decision = decide([_match(0.95), _match(0.9499)], POLICY, CaptureAssurance.SUPERVISED)
        assert decision.outcome is DecisionOutcome.ACCEPT
        assert decision.margin is not None
        assert decision.margin < 0.01


class TestCandidateValidation:
    def test_a_candidate_needs_at_least_one_sample(self) -> None:
        with pytest.raises(ValueError, match="at least one sample"):
            Candidate(person_uuid=uuid4(), face_sample_uuid=uuid4(), score=0.5, sample_count=0)


def test_review_outcomes_are_confirmed_or_rejected() -> None:
    assert {o.value for o in ReviewOutcome} == {"confirmed", "rejected"}


def test_decision_is_immutable() -> None:
    decision = IdentityDecision(outcome=DecisionOutcome.ACCEPT, thresholds=POLICY, candidates=())
    with pytest.raises(Exception):  # noqa: B017 - frozen dataclass
        decision.outcome = DecisionOutcome.REJECT  # type: ignore[misc]


class TestCaptureAssurance:
    """Without presentation-attack detection, provenance has to do that work."""

    def test_an_unsupervised_capture_never_reaches_accept(self) -> None:
        decision = decide([_match(0.99)], POLICY, CaptureAssurance.UNSUPERVISED)
        assert decision.outcome is DecisionOutcome.REVIEW

    def test_the_cap_is_the_default_for_a_caller_that_says_nothing(self) -> None:
        """Silence must be the cautious reading, not the permissive one."""
        assert decide([_match(0.99)], POLICY).outcome is DecisionOutcome.REVIEW

    def test_a_supervised_capture_can_be_accepted(self) -> None:
        decision = decide([_match(0.99)], POLICY, CaptureAssurance.SUPERVISED)
        assert decision.outcome is DecisionOutcome.ACCEPT

    def test_a_capped_decision_says_the_score_was_not_the_problem(self) -> None:
        decision = decide([_match(0.99)], POLICY, CaptureAssurance.UNSUPERVISED)
        assert decision.capped_by_assurance

    def test_an_ordinary_review_is_not_reported_as_capped(self) -> None:
        """A score genuinely in the review band was not held back by provenance."""
        middling = (POLICY.accept_at + POLICY.review_at) / 2
        decision = decide([_match(middling)], POLICY, CaptureAssurance.UNSUPERVISED)
        assert decision.outcome is DecisionOutcome.REVIEW
        assert not decision.capped_by_assurance

    def test_the_cap_does_not_promote_a_rejection(self) -> None:
        """Capping lowers an accept; it must never raise a reject into review."""
        decision = decide([_match(0.01)], POLICY, CaptureAssurance.UNSUPERVISED)
        assert decision.outcome is DecisionOutcome.REJECT
        assert not decision.capped_by_assurance

    def test_the_assurance_is_carried_on_the_decision(self) -> None:
        decision = decide([_match(0.99)], POLICY, CaptureAssurance.SUPERVISED)
        assert decision.assurance is CaptureAssurance.SUPERVISED

    def test_supervision_does_not_lower_the_threshold(self) -> None:
        """Attesting to a capture buys accept-eligibility, not a weaker bar."""
        below = POLICY.accept_at - 1e-9
        decision = decide([_match(below)], POLICY, CaptureAssurance.SUPERVISED)
        assert decision.outcome is DecisionOutcome.REVIEW
