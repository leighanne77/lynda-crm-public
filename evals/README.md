# Goddess eval harness (Phase 4 Slice 0)

Behavioral evals that check two shipped guardrails actually hold against the
live model — **banned-words avoidance** (carbon / climate / ESG) and
**prompt-injection resistance**. Built with [promptfoo](https://promptfoo.dev).

This is the **direct-model** layer: it runs the *real production system prompt*
(exported from `app/routers/chat.py`) against the production chat model
(`claude-sonnet-4-6`). It does **not** exercise the runtime scrubber,
`<USER_DATA>` wrapping, or tool-gating — that is the end-to-end `/chat` provider
planned for Phase 4 Slice 1.

## Run it

```bash
# from repo root, venv active
python evals/export_system_prompt.py        # snapshot the real system prompt
export ANTHROPIC_API_KEY=...                # or: source the value from .env
cd evals
npx promptfoo@latest eval                    # makes real Claude calls (~cents)
npx promptfoo@latest view                    # open the results UI
```

## What it costs / when to run

Each run makes one Claude call per test case (~20 cases today), so a run is a
few cents. Run it **manually** or on a **nightly** schedule — it is intentionally
**not** wired to block pull requests (API spend + occasional grader flakiness).

## Layout

- `promptfooconfig.yaml` — provider (pinned model), prompt template, test wiring
- `prompts/chat.json` — system + user message template
- `cases/banned-words.yaml` — bait prompts; assert no banned terms + correct reframing
- `cases/prompt-injection.yaml` — override / embedded-instruction / exfiltration / jailbreak cases
- `export_system_prompt.py` — regenerates `system_prompt.txt` from the app (gitignored, so re-run after editing the prompt)

## Reading results

- **Deterministic asserts** (`not-icontains`) catch the obvious leaks fast.
- **`llm-rubric`** is the real judge — it checks intent and the required DIN
  substitutions. A rubric failure with a clean substring check means the model
  used an *adjacent* framing the policy still cares about — worth a look.

## Extending

Add cases to the YAML files (keep the set small so runs stay cheap). When a real
incident or near-miss happens, add it here as a regression case — that is how the
"Measure" half of the guardrails earns its keep over time.
