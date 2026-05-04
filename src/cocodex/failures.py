from __future__ import annotations


def format_failure_handling(
    *,
    reason: str,
    session: str | None = None,
    state: str | None = None,
    active_task: str | None = None,
) -> str:
    lines = ["", "Cocodex sync refused:"]
    if session:
        lines.append(f"- Session: {session}")
    if state:
        lines.append(f"- State: {state}")
    if active_task:
        lines.append(f"- Task: {active_task}")

    reason_lower = reason.lower()
    if "integration busy" in reason_lower:
        owner = _integration_busy_owner(reason)
        if "disconnected" in reason_lower:
            owner_name = owner or "<lock-owner>"
            lines.extend(
                [
                    f"- This is {owner_name}'s sync, not this worktree's sync.",
                    f"- Ask {owner_name} to run `cocodex join {owner_name}` from the project root if their Codex session is closed.",
                    f"- Then {owner_name} should run `cocodex sync` from their managed worktree.",
                    "- Keep this worktree unchanged and retry `cocodex sync` after that session finishes.",
                    "- For details, run `cocodex status` or `cocodex log` from the project repository.",
                ]
            )
        else:
            owner_name = owner or "the lock owner"
            lines.extend(
                [
                    f"- This is {owner_name}'s sync, not this worktree's sync.",
                    "- Keep this worktree unchanged.",
                    "- Retry `cocodex sync` from this worktree after that session finishes.",
                    "- Do not reset this worktree or manually merge main; your local work is still protected here.",
                    "- To see the current owner, run `cocodex status` from the project repository.",
                ]
            )
    elif "sync already in progress" in reason_lower:
        lines.extend(
            [
                "- This is a transient Cocodex task state; keep the worktree unchanged and retry shortly.",
                "- If it persists, run `cocodex status` and `cocodex log` from the project repository.",
                "- Restart the daemon and then run `cocodex join <name>` for the affected session if startup was interrupted.",
            ]
        )
    elif "no tmux target" in reason_lower or "prompt" in reason_lower and "inject" in reason_lower:
        name = session or "<name>"
        lines.extend(
            [
                f"- Restart this developer from their own tmux pane with `cocodex join {name}`.",
                "- Then retry `cocodex sync` from the managed worktree.",
                "- Cocodex restored the session snapshot before refusing; do not reset the worktree.",
            ]
        )
    elif "unknown session baseline" in reason_lower:
        lines.extend(
            [
                "- Preserve this managed worktree unchanged.",
                "- Run `cocodex status` and inspect the session branch against main.",
                "- If the branch is valid, create a backup ref before manually normalizing the baseline.",
            ]
        )
    elif "version mismatch" in reason_lower:
        name = session or "<name>"
        lines.extend(
            [
                f"- Restart `cocodex join {name}` after upgrading Cocodex.",
                "- Do not keep the stale join process running.",
                "- After restart, run `cocodex status` to confirm the agent version matches the daemon.",
            ]
        )
    elif "daemon is not running" in reason_lower:
        lines.extend(
            [
                "- Start the coordinator from the project repository: `cocodex daemon`.",
                "- Keep the daemon terminal open so failures and recovery events are visible.",
                "- Then retry `cocodex sync` from the managed worktree.",
            ]
        )
    elif active_task:
        lines.extend(
            [
                "- Same session: complete the task requirements, then run `cocodex sync` again from this worktree.",
                "- Run `cocodex status` from the project repository to see task file, validation file, and recovery refs.",
                "- Keep the worktree and task files intact; Cocodex has not published this candidate.",
            ]
        )
    elif state in {"blocked", "recovery_required"} or "blocked" in reason_lower:
        name = session or "<name>"
        lines.extend(
            [
                f"- This is legacy state; run `cocodex join {name}` if the session is not running.",
                f"- Then run `cocodex sync` from {name}'s managed worktree.",
                "- Inspect `cocodex status` and `cocodex log` before changing files.",
            ]
        )
    elif "cocodex protects main" in reason_lower:
        lines.extend(
            [
                "- Do not commit, merge, cherry-pick, rebase, or push main directly.",
                "- Do developer work inside `.cocodex/worktrees/<name>`.",
                "- Publish through `cocodex sync` from the managed worktree.",
            ]
        )
    else:
        lines.extend(
            [
                "- Run `cocodex status` from the project repository.",
                "- Run `cocodex log` and inspect the most recent event for the affected session.",
                "- Preserve the worktree as-is until the next action is clear.",
            ]
        )
    return "\n".join(lines) + "\n"


def next_step_for_session(
    *,
    session: str,
    state: str,
    active_task: str | None,
    blocked_reason: str | None,
) -> str:
    reason = (blocked_reason or "").lower()
    if "version mismatch" in reason:
        return f"Next step: restart `cocodex join {session}` after upgrading Cocodex."
    if active_task and state in {"fusing", "verifying", "publishing"}:
        return (
            "Next step: the same session completes the task, commits the candidate, "
            "writes validation, then runs `cocodex sync`."
        )
    if active_task and state == "blocked":
        return f"Next step: run `cocodex join {session}` if needed, then `cocodex sync` from that managed worktree."
    if active_task and state == "recovery_required":
        return f"Next step: run `cocodex join {session}` if needed, then `cocodex sync` from that managed worktree."
    if state == "blocked":
        return f"Next step: run `cocodex join {session}` if needed, then `cocodex sync` from that managed worktree."
    if state == "recovery_required":
        return f"Next step: run `cocodex join {session}` if needed, then `cocodex sync` from that managed worktree."
    if state == "queued":
        return "Next step: rerun `cocodex sync`; Cocodex no longer keeps persistent sync queues."
    if state == "clean":
        return "Next step: no failure is recorded for this session."
    return "Next step: inspect `cocodex status` and `cocodex log` before changing this worktree."


def _integration_busy_owner(reason: str) -> str | None:
    prefix = "integration busy:"
    if not reason.lower().startswith(prefix):
        return None
    rest = reason[len(prefix):].strip()
    if not rest:
        return None
    return rest.split(maxsplit=1)[0]
