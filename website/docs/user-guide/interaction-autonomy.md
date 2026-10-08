# Interaction controls (local implementation checkpoint)

This checkout contains a partial follow-up implementation. Operator installation,
full CLI/Ink acceptance, and live configured inference remain separate and unverified.
No installed profile or credential configuration is changed by these source defaults.

## Mode and timing

AUTO and Always use the effective native YOLO state, including process-locked YOLO.
Always enables the native user control explicitly; it never blindly toggles an
already-enabled mode off. Native YOLO also bypasses ordinary command approval;
hardline/security floors still apply. Once requests one configured solver decision
and does not enable YOLO. Revoking workflow authority does not disable native YOLO.

Human waits default to 600 seconds with one reminder after 300 seconds. Silence,
reminders, and expiry never select an action. On Once/AUTO handoff the human wait
ends: the owner approved a separate 600-second solver deadline. Handoff at elapsed 590 gives
the solver its own 600 seconds, not the human wait's remaining 10 seconds. A failed
solver does not restart the original human reminder or timeout. Security, approval,
password, and verification requests remain human-owned and nondelegable. The native
status channel emits solver/blocked/settled phases without extending the human wait.

## Source configuration

The owner profile's `interaction` section accepts these validated fields:

- `decision_profile`: existing named profile, default `solver`.
- `wait_timeout_seconds`: positive integer at most 600, default 600.
- `reminder_after_seconds`: positive integer below the human timeout, default 300.
- `decision_timeout_seconds`: positive integer at most 600, default 600.
- `max_attempts`: positive integer at most 3, default 3.
- `max_output_tokens`: positive integer at most 150000, default 150000. This is
  the per-request output cap on supported capped routes; it does not limit
  generation on the native openai-codex HTTP route.
- `show_countdown`: boolean, default true for the native Ink human-wait card.
- `decision_prompt`: bounded operational preference, never an authorization override.
- `task_sources`: unique project-local names, default `todo`, `grooming`, `freeze`.
- `menu`: ordered stable-ID action objects. Each has `id`, `label`, `description`,
  `enabled`, `handler`, and `prompt`. Unknown fields and executable handlers are rejected.

Defaults include `continue`, `new_task`, `review_changes`, and protected native
`stop`. The exact default review label is `Проверить изменения`. The English review
scenario asks for a read-only review of current project changes and appropriate
sanctioned checks, with evidence. It forbids product edits, commits, deployment,
and service restarts; test artifacts must stay in approved scratch.

A custom action uses a unique stable ID and `handler: prompt`; no product source
edit is needed. No eval, shell, or import handler exists. Existing native handlers
cannot be overridden or aliased; Once/Always/YOLO IDs are reserved. Stop is restored
if omitted and always enabled even if configured otherwise. Stop never asks a model
or a confirmation. It cancels owned work and fences late results; process-locked
YOLO may remain on. Only a new explicit task can establish fresh workflow scope.

Continue is dispatched as a separate native turn after completed work. New task
uses the existing active project and configured task-source order; discovery is
not promotion, unfreezing, dependency bypass, or authorization. Current scenarios
are prompt-backed rather than a new remote queue or local task scheduler. These
behavioral prompt constraints are not an OS sandbox and require integration review.

Settings are frozen for the host lifetime with a request policy fingerprint.
The native settings JSON editor and task-source list apply changes to new sessions.
Ink renders stable-ID labels/descriptions and the human countdown; it does not
select an action on expiry. Offline UI/host tests are not live inference evidence.
Native transport ownership, request generation, input priority, and message history
remain separate from model decision contents. The implementation adds no model tool
for changing mode or native command approval rules.

## Configured inference limits

Supported routes with an enforceable output-token parameter have a fixed aggregate
ceiling of 150000 generated/output tokens across physical attempts, including
retries and configured fallbacks. On those routes, the outgoing cap
is no larger than the configured per-request cap, an explicit lower caller cap,
or the remaining aggregate reservation. Each dispatched attempt consumes its
entire reservation conservatively, without usage refunds; failed or unknown usage
cannot release budget. A full-size first attempt can therefore exhaust the budget
before the finite maximum of three attempts. Smaller caps can leave room for retries.
Provider-reported completion usage includes reasoning where counted; reasoning is
not added a second time. Usage above an actual wire cap is rejected.

Input byte bounds remain separate. This is not an exact combined input/output or
billing cap. Response bytes remain finite, independently of generated-token counts:
65,536 bytes for uncapped Codex and a cap-scaled framing allowance on supported
capped routes. This byte bound does not cap server-side generation. The final parsed
choice remains short and strict. The solver deadline remains 600 seconds from
successful handoff (or a lower configured timeout), never renewed by retries,
chooser delay, or callback delay.

The existing openai-codex HTTP route is an owner-approved exception: its native
adapter cannot transmit an output-token cap. That route does not reserve a
provider-side generation/billing cap; this implementation makes no guarantee of
a maximum generated-token count. It still uses finite attempts, deadline/cancellation,
bounded input and response bytes, no tools, isolated SDK retries, and result
validation. Other supported routes remain fail-closed when an enforceable cap is
absent. Synthetic offline fixtures are not live-provider evidence; no live solver
inference is authorized or claimed here.

Telegram and other external button delivery, installed profile activation, and
service restart/deployment are outside this implementation checkpoint.
