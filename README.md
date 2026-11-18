# Skills

Reusable agent skills by [@kevbob1](https://github.com/kevbob1).

## Available skills

### `kb-session-recap`

Create a local-time recap of OpenCode and Codex sessions for a date range. The
collector reads local session stores and prints prompts and the last replies so
an agent can summarize outcomes and follow-ups. Live Jira checks are optional
and use whatever Jira integration is configured in the agent environment.

#### Install for OpenCode

```sh
git clone https://github.com/kevbob1/skills.git ~/skills
mkdir -p ~/.config/opencode/skills
cp -R ~/skills/kb-session-recap ~/.config/opencode/skills/
```

Then ask OpenCode to use `kb-session-recap` for the date range you want.

#### Privacy

The collector reads local OpenCode and Codex session stores. Its output can
contain sensitive prompt and response text, local paths, and usage metadata;
review it before sharing. It makes no network requests and does not modify the
source session databases.
