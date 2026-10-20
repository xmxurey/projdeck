# projdeck (`proj`)

One place to see every project and Claude Code session on your Mac and SSH hosts,
jump into one in a new iTerm2 tab, and clean stray untracked files reversibly.

## Install (Mac)

```bash
brew install fzf
git clone git@github.com:xmxurey/projdeck.git ~/dev/projdeck
ln -s ~/dev/projdeck/bin/proj /usr/local/bin/proj   # or any dir on PATH
```
Remote hosts need only `python3` and `git` (the agent is piped over ssh).

## Configure `~/.config/proj/config.json`

```json
{
  "hosts": [
    {"name": "mac", "local": true, "roots": ["~/dev"]},
    {"name": "sg",  "ssh": "sg",   "roots": ["~"]}
  ]
}
```
`ssh` is an alias from `~/.ssh/config` (key auth, `BatchMode`). Optional keys:
`tiers_days`, `scan_depth`, `include_plain`, `auto_sync_minutes`, `ssh_timeout`,
`suspicious_patterns`, `trash_dir`.

## Use

| command | what |
|---|---|
| `proj` | all projects; Enter open · ^R resume latest session · ^S pick session · ^X clean · ^U sync |
| `proj sessions` | search all sessions by title, resume |
| `proj sync [host…]` | refresh (auto in background after 30 min) |
| `proj report` | markdown: unpushed, no upstream, junk, abandoned, deleted |
| `proj clean HOST:PATH` | move untracked junk to `~/.proj-trash` (never ignored files) |
| `proj restore [host]` | put a batch back |
| `proj trash [--purge --older 30d]` | list / permanently delete old batches |

`proj` groups projects by host (in config order), newest first within each host. With
iTerm2 the list stays open after opening a tab, so you can jump between tasks; Esc quits.

The preview shows where you left off in the latest session (your last prompts and
Claude's last reply).

Remote projects open inside tmux (`proj-<name>-<hash>` per project, one more per resumed
session), so claude keeps running when the connection drops; opening again re-attaches.
`▶n` marks projects with live proj tmux sessions. Detach with `Ctrl-b d`; set
`"tmux": false` on a host to use a plain shell. Hosts without tmux fall back automatically.

`proj report` ends with a summary of your own usage (opens, resumes, cleans) from the
local log `~/.cache/proj/usage.jsonl`; nothing leaves the machine.

`proj clean` works on repository roots only (not subdirectories or plain folders).
On macOS, iTerm2 asks for Automation permission the first time proj opens a tab.

Tip: set `"cleanupPeriodDays": 365` in Claude Code settings, otherwise transcripts older
than 30 days are deleted and can't be listed.
