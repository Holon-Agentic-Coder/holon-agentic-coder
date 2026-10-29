# Entropy and Expected Value, Explained Plainly

This is the plain-language companion to [Metrics](metrics.md), which stays the formal reference. It assumes nothing
beyond everyday arithmetic.

## The short version

A few words first. An **intent** is a piece of work someone asks for. A **plan** is a proposed way to do it. The
**flow** is the automated pipeline that plans the work, checks the plan and then runs it.

Holon gives every plan two kinds of score before it runs. **Entropy** is a mess-and-risk score: how much disorder the
plan is likely to cause. **Expected Value (EV)** is a worth-doing score: how much good the plan is likely to do once
everything is weighed up. Entropy is subtracted inside EV, so the more mess a plan is predicted to make, the lower its
EV. Each intent also has an **entropy budget**: a plan predicted to make more mess than that must be split into smaller
pieces before it can run.

## What entropy means here

In Holon, entropy is a score for how much mess and risk a change creates in the shared codebase. It is not the physics
meaning (heat and disorder in matter) or the information-theory meaning (surprise in a message). It is just a name for
"how likely is this to make a mess".

Think of several people building LEGO in one shared room. A small model on your own table makes little mess. Pulling
apart the big model in the middle of the room, which everyone else is also working on, makes a lot.

Precisely: entropy is a non-negative score for the disorder and risk a plan is expected to add to the repository and to
the work of other agents.

Five ingredients feed into it:

- **State Surface Area (SSA):** how much code and config the plan touches. More files, more mess.
- **Irreversibility (IRR):** how hard the change is to undo. A new document is easy to remove; a data migration is not.
- **Conflict Likelihood (CL):** how likely the change is to collide with other work going on at the same time.
- **Sandbox Escape Risk (SER):** how likely the work is to break out of the safety sandbox it runs in.
- **Novelty (NOV):** how unfamiliar the territory is. Work the system has done many times before is less risky.

The documented weights say how much each ingredient counts: SSA 30 %, IRR 25 %, CL 20 %, SER 15 % and NOV 10 %. These
weights are documented in `holon-config/metrics/entropy_config.json` but are not yet loaded by the runtime (the running
software does not use them yet); today each plan's entropy number is the planner's own estimate, made with these five
ingredients in mind.

A higher number means more mess. Zero is the floor: no plan has negative entropy.

## What Expected Value (EV) means here

EV is a single score for whether a plan is worth doing. It is built from five parts:

- **Chance of success:** how likely the plan is to work, from `0` (never) to `1` (certainly).
- **Impact:** the good it does if it works.
- **Learning value:** what the system learns even if the plan fails. A failed experiment can still teach something.
- **Entropy:** the mess it makes, as described above.
- **Cost:** what it uses up, such as time, compute and tokens.

Impact, learning value and cost are scores in the same consistent units, not real money, so they can be added and
subtracted.

In words: EV equals the chance of success times the impact, plus half the learning value, minus three tenths of the
entropy, minus the cost.

In symbols: `EV = P(success) * Impact + mu * LearningValue - lambda * entropy - Cost`

Here `mu` is `0.5` (the "half") and `lambda` is `0.3` (the "three tenths"). Both are read from
`holon-config/metrics/ev_config.json`.

Think of deciding whether a weekend project is worth it. You weigh how likely you are to finish, how much you would
enjoy the result and what you would pick up along the way, against the mess in the garage and the money and hours it
takes.

Precisely: when several plans compete for the same intent, the one with the higher EV is preferred.

## How entropy and EV fit together

Entropy sits inside EV as a penalty. If two plans are equal in every other way, the one predicted to make more mess has
the lower EV.

The aim is the most value for the least mess, not zero mess. A plan that makes some mess but does a lot of good can
still have the highest EV and win.

Why does high entropy lower EV? Because entropy is subtracted: every extra point of entropy takes `0.3` off EV.

Valuable but messy work is not abandoned. Instead it is split into smaller pieces, called sub-intents, each of which
makes less mess on its own. Think of moving house box by box instead of trying to carry the wardrobe, the bed and the
fridge through the door in one go.

Precisely: when the work is too messy to do in one plan, the plan lists smaller child intents, and the flow runs each of
them as its own plan, one at a time.

## What the entropy budget does

Each intent carries a mess allowance called `entropy_budget`, and its plan records the same value. If neither the plan
nor the intent sets one, the default is `15.0` (`DEFAULT_ENTROPY_BUDGET` in
`apps/sandbox-executor/src/sandbox_executor/budgets.py`).

- A plan predicted at or below its budget runs as one plan.
- A plan predicted above its budget must list sub-intents (in a `## Sub-intent list` section) before it can execute.
  Without that list it is sent back for re-planning at most twice, and then it fails.
- Each listed child then runs through the full pipeline, one at a time. A child may be split again, but only two levels
  below the original intent; splitting any deeper is refused and that run fails.

The budget is not a penalty. The penalty (`0.3` per point of entropy) lowers the EV score; the budget is a hard limit
that decides whether a plan may run in one piece at all.

Think of a spending allowance. You may buy anything within it in one go; anything bigger has to be broken into smaller
purchases.

Precisely: the predicted entropy of a plan is compared with its `entropy_budget`, and a plan over the budget is not
executed as a single plan.

For how the budget is resolved and enforced, see [Turn and Stage Budgets](budgets.md#entropy-budget).

## A worked example from this repository's ledger

Plan `P-1793223729-claude-agent-claude-opus-5-5` is recorded in `holon-knowledge/ledger/plans.jsonl` with these values:

| part              | value   |
| ----------------- | ------- |
| chance of success | `0.7`   |
| impact            | `90.0`  |
| learning value    | `6.0`   |
| entropy           | `10.6`  |
| cost              | `21.5`  |
| entropy budget    | `14.0`  |
| recorded EV       | `41.32` |

Working it through, one part at a time:

1. Chance of success times impact: `0.7 * 90 = 63`
2. Half the learning value: `0.5 * 6 = 3`
3. Three tenths of the entropy: `0.3 * 10.6 = 3.18`
4. Put together, minus the cost: `63 + 3 - 3.18 - 21.5 = 41.32`

That matches the recorded EV of `41.32`.

Budget check: the entropy of `10.6` is within the budget of `14.0` (`10.6 <= 14.0`), so the plan could run as a single
plan with no sub-intents.

Reading it: the entropy penalty of `3.18` is small next to the `63` of expected good, so the mess was worth it. If the
entropy had been above `14.0`, the plan would have had to be split before it could execute.

## Check your understanding

- **What is entropy here?** A score for how much mess and risk a plan is expected to add to the shared codebase and to
  other agents' work.
- **What is EV?** A single score for whether a plan is worth doing, weighing its chance of success, impact and learning
  value against its mess and cost.
- **Why does high entropy lower EV?** Entropy is subtracted inside EV, so each extra point of entropy takes `0.3` off
  the score.

## Where the formal definitions live

- Expected Value: [Metrics §1.2](metrics.md#12-expected-value-ev)
- Per-intent entropy: [Metrics §2.1](metrics.md#21-meaning) and
  [Metrics §2.2](metrics.md#22-predicted-entropy-formula-bootstrap)
- All formulas and defaults on one screen: [Metrics §11 Quick Reference](metrics.md#11-quick-reference)
- The five entropy ingredients: [Appendix: Intent Entropy Components](appendix.md#intent-entropy-components)
