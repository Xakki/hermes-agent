"""The clarify tool's whole question batch for a classic-CLI viewer's turn, in one request.

Main's HermesCLI answered ``clarify_callback(questions)`` with its batch panel: every question shown
at once, one active, answers revisitable until all are locked, ``{answers: {qid: raw | None},
outcome}``. The messaging path asks one card per question. For a classic-CLI local viewer the owner
sends the batch as the existing ``clarify`` server→client request (``ClarifyRequestParams`` →
``ClarifyResult``) through the same request/response machinery as the sudo prompt, so main's own panel
answers it unchanged, and blocks until it answers, the turn is interrupted, or the clarify timeout
(the same config both sides read) passes.
"""
from __future__ import annotations

import re
from collections import Counter

_QUESTION_KEYS = ("qid", "question", "choices", "multi_select", "custom_option_ids")
_OPTION_ID = re.compile(r"[a-z][a-z0-9_-]{0,39}")
_TIMEOUT_POLICIES = ("none", "recommended", "first", "recommended_or_first")


def _configured_option(entry):
    from agent.redact import redact_approval_display
    from tools.clarify_tool import strip_recommended

    if not isinstance(entry, dict) or entry.get("handler") != "prompt" or entry.get("enabled", True) is not True:
        return None
    option_id, label, instruction = (entry.get(key) for key in ("id", "label", "prompt"))
    if not isinstance(option_id, str) or not _OPTION_ID.fullmatch(option_id):
        return None
    if (not isinstance(label, str) or not label.strip() or len(label) > 80 or not label.isprintable()
            or strip_recommended(label) != label):
        return None
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 4000:
        return None
    instruction = instruction.strip()
    if (instruction.startswith(("/", "!", "@"))
            or any(not (char.isprintable() or char in "\n\t") for char in instruction)):
        return None
    if any(redact_approval_display(text, prose=True, force=True) != text for text in (label, instruction)):
        return None
    return {"id": option_id, "label": label, "instruction": instruction,
            "auto_select": entry.get("auto_select") is True, "recommended": entry.get("recommended") is True}


def _profile_options(questions, menu):
    from tools.clarify_tool import MAX_CHOICES, RECOMMENDED_LABEL, strip_recommended

    if not isinstance(menu, list):
        return questions, {}
    counts = Counter(question["question"] for question in questions)
    projected, configured = [], {}
    for question in questions:
        projected.append(question)
        key, qid = question["question"], question["qid"]
        choices = list(question.get("choices") or [])
        if not choices or question.get("multi_select") or counts[key] != 1 or not question.get("auto_select_safe", True):
            continue
        candidates = [option for item in menu[:64] if isinstance(item, dict) and item.get("question_key") == key
                      if (option := _configured_option(item)) is not None]
        ids = Counter(option["id"] for option in candidates)
        labels = Counter(option["label"].casefold() for option in candidates)
        builtin = {strip_recommended(choice).casefold() for choice in choices}
        accepted = [option for option in candidates if ids[option["id"]] == 1
                    and labels[option["label"].casefold()] == 1 and option["label"].casefold() not in builtin]
        by_label = {}
        for option in accepted[:max(0, MAX_CHOICES - len(choices))]:
            label = option["label"]
            display = f"{label} {RECOMMENDED_LABEL}" if option["recommended"] else label
            choices.append(display)
            by_label[display] = option
        if by_label:
            configured[qid] = {option["id"]: option for option in by_label.values()}
            projected[-1] = {**question, "choices": choices,
                "custom_option_ids": {label: option["id"] for label, option in by_label.items()}}
    return projected, configured


def _timeout_choices(questions, raw_policy, configured):
    from tools.clarify_tool import strip_recommended

    if type(raw_policy) is not str or raw_policy not in _TIMEOUT_POLICIES or raw_policy == "none":
        return {}
    selected = {}
    for question in questions:
        choices = question.get("choices") or []
        if not choices or question.get("multi_select") or not question.get("auto_select_safe", True):
            continue
        labels = [strip_recommended(choice) for choice in choices]
        recommended = [idx for idx, (choice, label) in enumerate(zip(choices, labels)) if choice.strip() != label]
        if (len({label.casefold() for label in labels}) != len(labels) or len(recommended) > 1
                or not all(label and label.isprintable() for label in labels)):
            continue
        qid = question["qid"]
        custom_ids = question.get("custom_option_ids", {})
        eligible = [idx for idx, label in enumerate(choices) if label not in custom_ids
                    or configured[qid][custom_ids[label]]["auto_select"]]
        if recommended and recommended[0] in eligible and raw_policy != "first":
            idx = recommended[0]
        elif eligible and raw_policy != "recommended":
            idx = eligible[0]
        else:
            continue
        selected[qid] = {"option_id": custom_ids.get(choices[idx], f"{qid}:{idx}"), "label": labels[idx]}
    return selected


def ask_clarify_batch(turn, questions) -> dict:
    from gateway.session_server_requests import ask_viewer_result
    from gateway.session_policy import active_policy
    from hermes_cli.config import load_config
    from tools.clarify_gateway import get_clarify_timeout
    authority, session_id, _generation = turn._approval_owner
    live = authority.sessions.get(session_id)
    agent = (getattr(turn._ctx, "agent_holder", None) or [None])[0]
    interrupted = (lambda: bool(getattr(agent, "_interrupt_requested", False))) if agent is not None else (
        lambda: False)
    if live is None:
        return {"answers": {}, "outcome": "cancelled"}
    timeout = get_clarify_timeout()
    policy = active_policy()
    ink = getattr(policy, "source", None) == "tui"
    interaction = load_config().get("interaction") if ink and not getattr(policy, "ignore_user_config", False) else None
    interaction = interaction if isinstance(interaction, dict) else {}
    displayed, configured = _profile_options(questions, interaction.get("menu"))
    params: dict = {"questions": [
        {key: entry[key] for key in _QUESTION_KEYS if entry.get(key) is not None} for entry in displayed
    ]}
    if ink and timeout > 0:
        choices = _timeout_choices(displayed, interaction.get("clarify_timeout_policy"), configured)
        if choices:
            params["timeout_choices"] = choices

    # ``qids``: the viewer's batch panel locks each answer with ``clarify.lock`` (#140400).
    result = ask_viewer_result(live.event_stream, session_id, "clarify", params,
                               cancelled=interrupted, timeout=timeout if timeout > 0 else None,
                               qids=[entry["qid"] for entry in questions], clarify_timing=ink)
    if result is None:
        if ink:
            return {"answers": {}, "outcome": "cancelled", "report_decisions": True}
        return {"answers": {}, "outcome": "cancelled" if interrupted() else "timed_out"}
    answers = result.get("answers")
    if not isinstance(answers, dict):
        return {"answers": {}, "outcome": "cancelled"}
    known = {entry["qid"] for entry in questions}
    clean = {qid: (value if isinstance(value, str) else None) for qid, value in answers.items() if qid in known}
    # ``session_server_requests`` settles a batch with its terminal outcome (an absent outcome on a frame
    # that carried answers is ``submitted``; a cancel-all or error is ``cancelled``).
    outcome = result.get("outcome") if result.get("outcome") in ("submitted", "timed_out", "cancelled") else "cancelled"
    reply = {"answers": clean, "outcome": outcome}
    if ink:
        reply["report_decisions"] = True
        if result.get("native_timeout") is True:
            reply["native_timeout"] = True
            reply["timeout_choices"] = result["timeout_choices"]
        if configured:
            reply["configured_options"] = configured
            reply["manual_option_ids"] = result.get("manual_option_ids", {})
    return reply
