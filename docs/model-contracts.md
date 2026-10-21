# Model contracts: what an endpoint actually asks for

Lives in the source tree, not in an install. Its job is to let a request for a new model or
provider be answered from facts: what the wire demands, which field carries what, and what was
measured here - instead of a reading of someone's README. Every row is either graded by a suite
(`tests/test_endpoint_personas.py`) or marked with where the claim comes from. Vendor-documented
behaviour that this box has not reproduced is labelled **doc**; anything else was observed.

## The table

| Endpoint family | History it demands (append-only rule) | Thinking / effort field | Output cap | Cached tokens appear in | Malformed-call habit |
|---|---|---|---|---|---|
| OpenAI `/chat/completions` (current) | append-only; `tool` result must follow every `tool_calls`; assistant `reasoning_content` optional | `reasoning_effort` (a level string) | `max_tokens`; truncation reports `finish_reason: "length"` | `prompt_tokens_details.cached_tokens` | well-formed |
| OpenAI `/responses` | `input` items, append-only; no `messages` field at all | `reasoning: {effort}` | `max_output_tokens` | `response.usage.input_tokens_details.cached_tokens` | well-formed |
| Anthropic `/v1/messages` | `system` is a top-level field, not a message; a `tool_use` block must be answered by a `tool_result` block; **preserved thinking binds a thinking block to the exact system+tools+messages prefix that produced it** - editing any of them before the block is a 400 (or the block is dropped in non-strict mode), so compaction, injected reminders and mid-session tool edits are the named breakers (vendor doc, 2026) | `thinking: {type: "enabled", budget_tokens: N}` - a token **budget**, floor 1,024 (vendor rule, **doc**) | `max_tokens` is REQUIRED; without it the request 400s | `usage.cache_read_input_tokens` (**doc**) | well-formed |
| DeepSeek-class | assistant turns MUST carry `reasoning_content` back in thinking mode: missing it is a 400 naming the field; the echoed field is inert unless the paired flag (`clear_thinking: false`) rides (vendor docs) | `reasoning_effort`; thinking mode is the model's own switch | `max_tokens` | `prompt_cache_hit_tokens` (this harness reads it) | well-formed |
| Groq-class | assistant turns must NOT carry `reasoning_content`: present is a 400 (`Unrecognized key`) | nothing portable; the server decides | `max_tokens` | `prompt_tokens_details.cached_tokens` when the server reports it | well-formed |
| Z.AI / Fireworks-class | keeps `reasoning_content` and needs its own paired flag to make the echo mean something (`clear_thinking: false` / `reasoning_history: "preserved"`, vendor docs) | server-specific | `max_tokens` | as reported | well-formed |
| Kimi K3 (`kimi-k3`, OpenAI- and Anthropic-compatible) | **the complete assistant message**, `reasoning_content` AND `tool_calls` passed back as-is - not just `content`; always thinking (vendor README, 2026-07-21) | `reasoning_effort` = low/high/max, default max | `max_tokens`; 1M-token context | automatic block cache, TTL 5m/1h (vendor guide, **doc**) | well-formed |
| Mistral-class | strict about ids: a `tool_call_id` that is not exactly 9 alphanumeric characters is a 400; a dangling tool call is a 400/500 | `reasoning_effort` on the thinking models only | `max_tokens` | as reported | refuses, does not repair |
| llama.cpp-compatible (local) | tolerant; rebuilds `<|im_start|>assistant\n<think>…</think>` from `reasoning_content` in history | template switches ride in `chat_template_kwargs` (`enable_thinking`, `preserve_thinking`); `reasoning_effort` is a template variable, not a server field | `max_tokens` / `n_predict`; `default_generation_settings.n_predict` is often `-1` (unbounded) | `usage.prompt_tokens_details.cached_tokens` AND `timings.cache_n` | tolerant: emits id-less and name-less call fragments |
| vLLM | tolerant | server-side | `max_tokens` | only when `stream_options.include_usage` is asked for | id-less SSE fragments, no `usage` unless asked |
| anonymous (declares nothing) | the rules above that every server shares, and nothing else | nothing is sent | `max_tokens` | nothing | unknown, so assume nothing |

The harness decides between these by **what the endpoint answers** - `/models`, `/props`, a 400
that names a field - never by the model's name. `tests/test_name_hygiene.py` grades that rule in
both directions: eight different model names produce one byte-identical request, and a
model-family literal anywhere in the code needs its place on that suite's allowlist.

## Measured on this box, 2026-10-19: preserved reasoning

The endpoint is a llama.cpp-compatible server whose `/props` declares
`chat_template_caps.supports_preserve_reasoning: true`. What that declaration means, measured with
the same 6,759-token prompt:

| Arm | prompt tokens | `timings.cache_n` | `prompt_ms` |
|---|---|---|---|
| history carries `reasoning_content` (what ships) | 6,759 | 0 | 6,196 |
| same, `chat_template_kwargs: {"preserve_reasoning": false}` | 6,758 | 0 | 6,196 |
| same, `chat_template_kwargs: {"preserve_thinking": false}` | 6,759 | 0 | 68 (cache hit on the arm above) |
| `reasoning_content` stripped from history | 5,475 | 0 | 5,789 |

Readings:

* The flag is **inert on this endpoint** - the prompt is byte-identical with `preserve_reasoning`
  sent, with `preserve_thinking` sent, and with neither. (`chat_template_kwargs` itself is
  forwarded: `enable_thinking` moved the prompt 6,759 → 6,795 and stopped the reasoning stream, so
  the kwargs are not being dropped.)
* What the declaration really means is that the server keeps the reasoning **the client sends**.
  The trace rides because of the client's copy, so the client-side replay stays and this item
  closes as a recorded trade-off, not a fix.
* The cost of the replay is **+1,284 tokens on a 5,475-token prompt (+23.5%)**, about **1.2 s of
  prefill per turn** at the ~1,100 tok/s this box sustains for a cold prompt.
* KV-cache reuse on a growing session was **zero on eight consecutive turns** (`cache_n: 0`),
  while an immediately-repeated identical prompt reused **6,752 of 6,759** tokens in 68 ms. Prefix
  reuse here is a same-prompt effect, not a prefix-extension effect, so the replay's tokens are
  re-prefilled on ordinary turn-to-turn traffic.

`tinycmdr doctor` prints what the endpoint declares (`template : the endpoint declares …`) from the
same `/props` reply the vision and slot probes already ask for. Nothing branches on the caps.

## The replay policy and Kimi K3 (2026-10-20)

The turn IN FLIGHT - the newest assistant message the server just generated - is always replayed
whole: the template rebuilds its `<think>` block from the field, and a slice is a different
message. Every OLDER assistant message, including the earlier calls of the same tool loop, is
stale context: it is replayed only where the endpoint has proved it needs the echo - a 400 naming
the field, or a reply that carries `reasoning_content` (K3's first reply does) - or when
`llm.replay_reasoning` is `"always"`; otherwise it is dropped. That is the normal chat shape and it
stops a local model re-buying its own old thinking (measured 2026-10-20: replaying a whole 25-call
loop took one turn to 115k characters of reasoning, growing every call). Where the echo is proven,
the whole in-flight loop stays whole, because that contract requires it. `replay_reasoning_max_chars`
applies only to a completed turn that is replayed; 0 (the default) sends it whole, which is K3's
contract, and a positive value is the cost bound. The session history keeps the reasoning with the
answer, so a K3 conversation replays the complete turn on the next message with no switch. The
override is a verb on every surface: `reasoning` prints the state, `reasoning replay always|auto|off`
sets it, `reasoning cap <chars>` bounds it (chat writes `/reasoning replay always`; the host drops
the slash). `doctor`
prints the same state. Whatever the harness learns about a model - the reasoning echo, the
`/responses` switch, a field it rejected, a streaming downgrade - is also said once as a
`Model note:` on the chat surface, and `doctor` lists the same notes; the user is never handed a
config key. The two sides of the same rule: the compat wire does not return thinking
blocks (vendor doc), so Claude-direct preserved thinking cannot bind there; the doctor note names the
cache and reasoning-effort losses, and a translating gateway that does preserve thinking must keep
the system prompt, tools and earlier messages byte-stable.

## The picker: profiles are data, and the endpoint still wins (2026-10-20)

A user cannot be expected to know which flags a model needs, so `model add` recognizes a
known model from the id it advertises and pre-sets what that family needs. The catalog is
`assets/model-profiles.json` - data, shipped with the build, never read by the request
path; a model name is only the picker's key. `tinycmdr model profiles` prints it, the
interactive add offers it when the id matches nothing, the page's key form has the same
list, and `--profile <id>` (or `--profile none`) decides by hand.

A row seeds the facts the request path already reads: the reasoning echo (`on` where the
field must come back, `off` where it is rejected), the fields an endpoint refuses, and the
documented context window (used only when the endpoint reports none). Selecting a row is a
STARTING POINT, not a setting: a 400, `/props` or `/models` still overwrites it, a learned
value is never undone by a profile, and a disagreement is said once as a `Model note:` -
"the profile expected X; this endpoint answered Y - the harness used the endpoint's
answer". `doctor` prints the row in force and what it seeded. Rows that only describe a
family (the wire, vision, the complete-message rule, sampling) are checked against the
endpoint when the endpoint is added: a messages-only server and a vision veto each become
a note, and `sampling` is always `omit` because this build sends no sampling keys.

No row seeds a reply cap: the cap is sized from the window and `llm.max_tokens`, and a
number nobody measured would be worse than the measured default.



Four requests, none of which generates more than a few tokens:

1. `GET /models` - does it list the configured id, and does the entry carry a context length
   (`max_model_len`, `n_ctx`, `context_window`, or `meta.n_ctx` - all four are in the wild)?
2. `GET /props` (root, not under `/v1`) - is this llama.cpp, how many slots, does the template
   declare `chat_template_caps`, `modalities.vision`?
3. `POST /chat/completions` with `tools` + `reasoning_effort` + `stream_options` + 1 token - the
   400 text names the field, and the field names the family. A 400 naming BOTH `tools` and
   `reasoning_effort` means the server wants `/responses`.
4. `POST /chat/completions` with a two-turn history whose assistant turn does and does not carry
   `reasoning_content` - which of the two 400s says whether the echo is required or forbidden.

Then pin what came back as a persona row in `tests/test_endpoint_personas.py`, before writing any
branch. A persona row is the cheap form of support; a code branch is the expensive one.
