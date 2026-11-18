#!/usr/bin/env python3
"""Digest OpenCode and Codex sessions active in a local date range. Read-only."""

import argparse
import atexit
import glob
import json
import os
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
OPENCODE_DB = os.path.join(HOME, ".local/share/opencode/opencode.db")
CODEX = os.path.join(HOME, ".codex")
PROMPT_CHARS = 240
REPLY_CHARS = 500
MAX_PROMPTS = 12
PREAMBLES = ("# AGENTS.md instructions", "<INSTRUCTIONS>", "<environment_context>")


def day_epoch(day, offset=0):
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=offset)).timestamp()


def stamp(epoch):
    return datetime.fromtimestamp(epoch).strftime("%m/%d %H:%M")


def utc_epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def clip(text, limit):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def connect_ro(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def connect_copy(path):
    """Query a copy: a read-only open fails without -wal/-shm sidecars, and a normal open writes them into ~/.codex."""
    tmp = tempfile.mkdtemp(prefix="kb-session-recap-")
    atexit.register(shutil.rmtree, tmp, True)
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            shutil.copy2(path + suffix, tmp)
    return sqlite3.connect(os.path.join(tmp, os.path.basename(path)))


def jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def render(heading, meta, prompts, reply):
    lines = [f"### {heading}"] + [f"- {m}" for m in meta]
    if prompts:
        lines.append(f"- Prompts in range ({len(prompts)}):")
        lines += [f"  - {stamp(t)} {clip(text, PROMPT_CHARS)}" for t, text in prompts[:MAX_PROMPTS]]
        if len(prompts) > MAX_PROMPTS:
            lines.append(f"  - … {len(prompts) - MAX_PROMPTS} more")
    else:
        lines.append("- Prompts in range: none")
    if reply:
        lines.append(f"- Last reply: {clip(reply, REPLY_CHARS)}")
    return "\n".join(lines)


def opencode(start, end, exclude):
    db = connect_ro(OPENCODE_DB)
    s_ms, e_ms = int(start * 1000), int(end * 1000)
    sessions = db.execute(
        "SELECT id, parent_id, title, directory, time_created, time_updated, cost, tokens_input, tokens_output "
        "FROM session_v2 WHERE time_updated >= ? AND time_created < ? ORDER BY time_created",
        (s_ms, e_ms),
    ).fetchall()
    blocks = []
    for sid, parent, title, directory, created, updated, cost, tok_in, tok_out in sessions:
        if sid in exclude:
            continue
        prompts = [
            (t / 1000, text)
            for t, text in db.execute(
                "SELECT time_created, json_extract(data, '$.text') FROM session_message "
                "WHERE session_id = ? AND type = 'user' AND time_created >= ? AND time_created < ? ORDER BY seq",
                (sid, s_ms, e_ms),
            )
            if text
        ]
        reply = None
        for (data,) in db.execute(
            "SELECT data FROM session_message WHERE session_id = ? AND type = 'assistant' "
            "AND time_created >= ? AND time_created < ? ORDER BY seq DESC LIMIT 8",
            (sid, s_ms, e_ms),
        ):
            texts = [p["text"] for p in json.loads(data).get("content", []) if p.get("type") == "text" and p.get("text")]
            if texts:
                reply = texts[-1]
                break
        meta = [
            f"{directory} · {stamp(created / 1000)} → {stamp(updated / 1000)} · "
            f"${cost:.2f} · {tok_in:,} in / {tok_out:,} out"
        ]
        if parent:
            meta.append(f"subagent of `{parent}`")
        blocks.append(render(f"{title or '(untitled)'} — `{sid}`", meta, prompts, reply))
    return blocks, f"OpenCode: {len(blocks)} sessions active in range (session_v2)"


def codex_rollouts(start, end):
    rollouts = {}
    for path in glob.glob(os.path.join(CODEX, "sessions", "**", "rollout-*.jsonl"), recursive=True):
        if os.path.getmtime(path) < start:
            continue
        records = list(jsonl(path))
        meta = next((r["payload"] for r in records if r.get("type") == "session_meta"), None)
        if not meta or utc_epoch(meta["timestamp"]) >= end:
            continue
        prompts, reply = [], None
        for rec in records:
            payload = rec.get("payload") or {}
            if rec.get("type") != "response_item" or payload.get("type") != "message":
                continue
            ts = utc_epoch(rec["timestamp"])
            if not start <= ts < end:
                continue
            content = payload.get("content") or []
            if payload.get("role") == "user":
                text = " ".join(c["text"] for c in content if c.get("text"))
                if text and not text.lstrip().startswith(PREAMBLES):
                    prompts.append((ts, text))
            elif payload.get("role") == "assistant":
                texts = [c["text"] for c in content if c.get("type") == "output_text" and c.get("text")]
                if texts:
                    reply = texts[-1]
        if not prompts and reply is None:
            continue
        source = meta.get("source")
        spawn = (source.get("subagent") or {}).get("thread_spawn") if isinstance(source, dict) else None
        rollouts[meta["id"]] = {
            "created": utc_epoch(meta["timestamp"]),
            "last": os.path.getmtime(path),
            "cwd": meta.get("cwd"),
            "spawn": spawn,
            "prompts": prompts,
            "reply": reply,
        }
    return rollouts


def codex_state(start, end):
    path = os.path.join(CODEX, "state_5.sqlite")
    if not os.path.exists(path):
        return {}
    db = connect_copy(path)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """SELECT id, COALESCE(NULLIF(title, ''), NULLIF(name, ''), first_user_message) AS title,
                  cwd, model, tokens_used
           FROM threads WHERE updated_at_ms >= ? AND created_at_ms < ?""",
        (int(start * 1000), int(end * 1000)),
    ).fetchall()
    return {row["id"]: dict(row) for row in rows}


def codex_titles():
    path = os.path.join(CODEX, "session_index.jsonl")
    if not os.path.exists(path):
        return {}
    return {rec["id"]: rec.get("thread_name") for rec in jsonl(path)}


def codex_history(start, end):
    by_session = defaultdict(list)
    path = os.path.join(CODEX, "history.jsonl")
    if os.path.exists(path):
        for rec in jsonl(path):
            if start <= rec["ts"] < end:
                by_session[rec["session_id"]].append((rec["ts"], rec["text"]))
    return by_session


def codex_logs(start, end):
    path = os.path.join(CODEX, "logs_2.sqlite")
    if not os.path.exists(path):
        return []
    db = connect_copy(path)
    return db.execute(
        """SELECT thread_id, MIN(ts), MAX(ts), COUNT(*),
                  SUM(target = 'codex_core::session::handlers' AND feedback_log_body LIKE '%operation="turn_input"%')
           FROM logs WHERE ts >= ? AND ts < ? AND thread_id IS NOT NULL GROUP BY thread_id""",
        (int(start), int(end)),
    ).fetchall()


def codex(start, end, exclude):
    rollouts = codex_rollouts(start, end)
    state = codex_state(start, end)
    titles = codex_titles()
    history = codex_history(start, end)
    logs = codex_logs(start, end)

    blocks = []
    for sid, r in sorted(rollouts.items(), key=lambda kv: kv[1]["created"]):
        if sid in exclude:
            continue
        st = state.get(sid, {})
        spawn = r["spawn"] or {}
        meta = [
            f"{stamp(r['created'])} → {stamp(r['last'])} · {st.get('cwd') or r['cwd'] or '?'} · "
            f"model {st.get('model') or '?'} · {st.get('tokens_used') or 0:,} tokens"
        ]
        if spawn:
            meta.append(
                f"subagent `{spawn.get('agent_nickname') or '?'}` at `{spawn.get('agent_path')}` "
                f"of `{spawn.get('parent_thread_id')}`"
            )
        title = st.get("title") or titles.get(sid) or spawn.get("agent_path") or (clip(r["prompts"][0][1], 80) if r["prompts"] else "(untitled)")
        blocks.append(render(f"{clip(title, 90)} — `{sid}`", meta, r["prompts"], r["reply"]))

    extra = sorted((set(state) | set(history)) - set(rollouts) - exclude)
    for sid in extra:
        st = state.get(sid, {})
        prompts = history.get(sid, [])
        title = st.get("title") or titles.get(sid) or (clip(prompts[0][1], 80) if prompts else "(untitled)")
        meta = [
            f"{st.get('cwd') or '?'} · model {st.get('model') or '?'} · {st.get('tokens_used') or 0:,} tokens",
            "no rollout file in range",
        ]
        blocks.append(render(f"{clip(title, 90)} — `{sid}`", meta, prompts, None))

    known = set(rollouts) | set(state) | set(history)
    orphans = [row for row in logs if row[0] not in known and row[0] not in exclude]
    if orphans:
        lines = [
            "### Log-only threads (no rollout, state row, or history entry)",
            "| Thread | Turns | First → last | Log lines |",
            "| --- | --- | --- | --- |",
        ]
        for tid, first, last, lines_count, turns in sorted(orphans, key=lambda row: row[1]):
            lines.append(f"| `{tid}` | {turns} | {stamp(first)} → {stamp(last)} | {lines_count} |")
        blocks.append("\n".join(lines))

    stats = (
        f"Codex: {len(rollouts)} rollouts, {len(state)} state threads, "
        f"{len(extra)} without rollout, {len(orphans)} log-only"
    )
    return blocks, stats


def attempt(label, fn, *args):
    try:
        return fn(*args)
    except Exception as exc:
        return [f"⚠ {label} failed: {exc}"], f"{label} failed"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("start", help="first local day, YYYY-MM-DD (inclusive)")
    parser.add_argument("end", help="last local day, YYYY-MM-DD (inclusive)")
    parser.add_argument(
        "--exclude", action="append", default=[], metavar="ID",
        help="session or thread id to skip, such as the session running this recap",
    )
    args = parser.parse_args()
    start, end = day_epoch(args.start), day_epoch(args.end, 1)
    exclude = set(args.exclude)

    oc_blocks, oc_stats = attempt("OpenCode", opencode, start, end, exclude)
    cx_blocks, cx_stats = attempt("Codex", codex, start, end, exclude)

    print(f"# Session digest {args.start} → {args.end} (local time, inclusive)")
    print("\n## OpenCode\n")
    print("\n\n".join(oc_blocks) or "No sessions.")
    print("\n## Codex\n")
    print("\n\n".join(cx_blocks) or "No threads.")
    print(f"\n## Coverage\n- {oc_stats}\n- {cx_stats}")


if __name__ == "__main__":
    main()
