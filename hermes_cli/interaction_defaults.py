"""Pure default data for host-owned human interactions and bounded decisions."""

INTERACTION_MAX_OUTPUT_TOKENS = 150000

INTERACTION_DEFAULTS = {
    "decision_profile": "solver",
    "wait_timeout_seconds": 600,
    "reminder_after_seconds": 300,
    "decision_timeout_seconds": 600,
    "max_attempts": 3,
    "max_output_tokens": INTERACTION_MAX_OUTPUT_TOKENS,
    "show_countdown": True,
    "decision_prompt": "Select the most useful already-authorized next action for the accepted task.",
    "task_sources": ["todo", "grooming", "freeze"],
    "menu": [
        {"id": "continue", "label": "Continue", "description": "Resume remaining accepted work",
         "enabled": True, "handler": "continue_current",
         "prompt": "Continue remaining work in the accepted current task. Respect its existing scope and approvals. If complete, report that once and wait."},
        {"id": "new_task", "label": "New task", "description": "Discover eligible project-local work",
         "enabled": True, "handler": "discover_task",
         "prompt": "Search existing tasks in the active project's configured task sources. Respect actual stage, dependencies, scope, and recorded authorization. Never silently unfreeze or execute unresolved draft work."},
        {"id": "review_changes", "label": "Проверить изменения", "description": "Read-only review and sanctioned checks",
         "enabled": True, "handler": "prompt",
         "prompt": "Review the current project changes and run appropriate sanctioned checks. Report defects, evidence, and any unverified checks. Do not modify product files, commit, deploy, or restart services. Keep any test artifacts in the approved scratch location."},
        {"id": "stop", "label": "Stop", "description": "Cancel owned work immediately",
         "enabled": True, "handler": "stop", "prompt": ""},
    ],
}
