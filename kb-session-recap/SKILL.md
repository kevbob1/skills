---
name: kb-session-recap
description: "Create a local-time recap of OpenCode and Codex sessions, with optional live Jira checks and follow-up items."
disable-model-invocation: true
---

# Session recap

Collects OpenCode and Codex sessions active in a date range, then summarizes
what was asked, what was produced, and what still needs attention.

## Steps

1. **Get the range.** Ask for both dates if either is missing. Use local time,
   inclusive `YYYY-MM-DD` dates. Done when both dates are known and the start is
   not after the end.

2. **Collect the digest.** Run the bundled collector, excluding the session
   running the recap when its ID is available:

   ```sh
   python3 <skill-directory>/scripts/gather.py START END --exclude <current-session-or-thread-id>
   ```

   Omit `--exclude` when the current ID is unavailable. OpenCode session IDs
   start with `ses_`. Done when the output has `## OpenCode`, `## Codex`, and
   `## Coverage` sections. Report any `⚠ … failed` warning and continue with
   results from the other source.

3. **Read the digest.** Each session includes prompts from the range and its
   last reply, truncated for readability. Open the full session only when a
   truncated reply hides the outcome. Done when every listed session's request
   and outcome are understood, or its outcome is marked unclear.

4. **Check Jira when available.** Collect every ticket ID in the digest. If an
   authorized Jira integration is configured, fetch each ticket's current
   status, assignee, story points, issue links, and changelog since the range
   start. If no integration is available or a fetch fails, say Jira was not
   checked; do not infer live ticket state. Compare session notes with current
   status to identify drift, linked blockers, and fields blocking a transition.
   Done when every ticket has live details or is explicitly marked unchecked.

5. **Write the recap.** Account for every session in one of these sections:
   - **OpenCode:** session and repository/directory, when, request, outcome,
     and ticket IDs with live status when checked.
   - **Codex:** thread, when, request, outcome, and ticket IDs with live status
     when checked. Group subagent reviews under their parent thread.
   - **Needs attention:** open PRs, unmerged work, unresolved questions,
     conflicting facts, and Jira drift. State a next action for each item.
   - **Coverage:** include the collector's coverage summary and any log-only
     threads, whose prompt text may not be recoverable.

## Privacy

The digest contains prompt and reply excerpts, local paths, and usage metadata,
and may include sensitive information. Treat it accordingly and review it
before sharing. The collector reads local session stores, makes no network
requests, and does not modify the source session databases. It copies Codex
SQLite files to a temporary directory before querying them.

## Data sources

- OpenCode: `~/.local/share/opencode/opencode.db` (`session_v2` and
  `session_message` tables).
- Codex: `~/.codex/sessions/**/rollout-*.jsonl`, `state_5.sqlite`,
  `history.jsonl`, `session_index.jsonl`, and optional `logs_2.sqlite`.

Some Codex threads appear only in `logs_2.sqlite`; their prompts are not
recoverable. Subagent review threads may have no prompts of their own; their
replies contain the findings.
