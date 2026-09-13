# Threshold validation — cyber-ai, 2026-09-13

The decision thresholds were unvalidated from the first deployment until this
measurement. They were set to 0.62 / 0.42 when the gallery held fifteen test
people, and left unchanged as it grew to eighteen thousand real ones. The
policy version said so honestly — `cyber-ai-unvalidated-v1` — but an honest
label on an unmeasured number is still an unmeasured number.

This records what was measured, how, and what was set as a result.

## Method

`scripts/validate_thresholds.py`, run against the live gallery:

```
docker exec -i hawkeye-api-1 python - --probes 2000 < scripts/validate_thresholds.py
```

Each probe is an enrolled sample, searched against the whole gallery with its
own row excluded. Two rates come out of it:

- **FNMR** — probes whose own person scored below the threshold: a known person
  the system would fail to find. Only probes whose person has another sample on
  file can contribute, so the denominator is smaller than the probe count.
- **FPIR** — probes whose *top* candidate is the wrong person and reached the
  threshold: the system naming somebody who is not there. It is deliberately
  rank-1, because rank-1 is what the system acts on. An impostor scoring above
  the threshold but below the right person changes nothing.

A probe whose person has only one sample is an "unknown" probe: with its own
row excluded the gallery holds no right answer, so any impostor at or above the
threshold is a false identification. 95 of 2000 probes were of this kind.

This measures the population as enrolled. It is not a measure of the model in
general, and it will drift as the gallery grows — rerun it after any large
import.

## What was measured

Gallery: 17,907 people, 62,951 samples. 2,000 probes, 1,905 with a mate.

| threshold | FNMR | FPIR |
|---|---|---|
| 0.50 | 0.9% | 1.60% |
| 0.54 | 1.5% | 0.80% |
| 0.56 | 2.3% | 0.50% |
| 0.58 | 3.0% | 0.25% |
| **0.60** | **3.8%** | **0.20%** |
| 0.62 | 5.3% | 0.20% |
| 0.66 | 9.7% | 0.20% |
| 0.70 | 16.7% | 0.20% |
| 0.72 | 21.4% | 0.15% |
| 0.80 | 52.9% | 0.05% |

Score distributions: genuine p05 = 0.616, p50 = 0.795. Impostor p95 = 0.541,
p99 = 0.595, max = 0.812.

## What the numbers say

**FPIR stops improving at about 0.58.** From 0.58 to 0.70 it sits flat at 0.20%
while FNMR more than quadruples. Raising the accept threshold through that range
buys no protection and costs a great deal of recall.

The flat region is four probes that do not resolve until 0.80. Three of them
share a signature: a *different* person scored 0.79–0.81 while the subject's own
second photograph scored only 0.46–0.67. A stranger matching better than your own
mate is what duplicate enrolment looks like — the same human imported twice under
two identifiers — though a genuine lookalike produces the same pattern. A sweep of
400 further probes found no other cross-person pair at or above 0.75, so whatever
they are, they are rare and they are not reachable by threshold. **They are a data
problem, not a policy problem**, and the right fix is duplicate detection, not a
higher bar.

## What was set

```
FACEID_DECISION_ACCEPT_THRESHOLD=0.60
FACEID_DECISION_REVIEW_THRESHOLD=0.42
FACEID_DECISION_POLICY_VERSION=cyber-ai-2026-09-13-n17907
```

**Accept at 0.60**, lowered from 0.62. It carries the same 0.20% FPIR as 0.62 and
misses a third fewer people (3.8% against 5.3%). Nothing in the data justified the
higher bar.

**Review at 0.42**, unchanged. Only 0.26% of genuine mates fall below it, so as a
floor for "worth a human's attention" it is doing its job. Raising it to 0.46 would
cut reviewer noise at the cost of missing three times as many true matches; that is
a workload judgement rather than an accuracy one, and it belongs to whoever staffs
the queue.

**The policy version now names the population it was validated against.** When the
gallery changes materially, rerun the script and mint a new version — a decision
made under the old policy must stay readable against the rules that produced it.

## What this does not establish

Thresholds govern *similarity*. They say nothing about whether the thing in front
of the camera was a face or a photograph of one; there is no presentation-attack
detection in this system, and no threshold can substitute for it. That is why an
unsupervised capture cannot reach `accept` however high it scores — see
`CaptureAssurance` in `backend/app/domain/identity.py`.

Nor is a similarity a probability. 0.60 is not "60% sure", and none of these rates
convert a score into a likelihood that a named person is the right one. They are
frequencies observed over this gallery, and that is all.
