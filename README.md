# Lattice

**Your AI forgets everything when you close the window. Lattice is where it remembers.**

Lattice is a folder structure, a small Python core, and a set of instructions. It
is not an app. There is nothing to log into, no account, no subscription, no
service. Any AI that can read files on your computer can run it — Claude,
ChatGPT, Codex, whatever you switch to next year.

`local-first` · `python 3.11+` · `zero dependencies` · `sqlite`

---

## The problem

You explain your project to an AI. It helps. You close the window and it's gone.

Next time you explain it again. And again. Six months in, the AI knows nothing
about the way you work, and you're still repeating yourself.

---

## The shape of it

```
                            YOU
             talking, typing, pasting, rambling
          (in a terminal, a chat window, on your phone)
                             │
                             ▼
        ╔═══════════════════════════════════════════╗
        ║                 LATTICE                   ║
        ║   projects · preferences · decisions      ║
        ║   ideas · tasks · everything you've said  ║
        ╚═══════════════════════════════════════════╝
                             │
                 ┌───────────┴───────────┐
                 ▼                       ▼
            THE INDEX               YOUR FILES
        what your AI reads       what you read and
       first, every session          correct by hand
        (small, generated)       (plain, permanent)
```

Everything above the box is just **you**, however you happen to be talking. The
tool you're using is a doorway, not a place things get stored. Everything below
the box is Lattice.

---

## What happens when you start something

1. **Your AI reads the index.** A few hundred words. It lists what projects exist
   and points at what you've said you prefer.
2. **It checks your preferences before it starts.** If you've said once how you
   like wiring diagrams drawn, or that you want the cheap option, or that you
   hate being handed a list of choices — it already knows.
3. **It opens only what's relevant.** One project summary and a handful of
   specific facts. Not your entire history, every time.
4. **It writes down what it learned.** A decision you made and why. A preference
   you expressed in passing. An idea you floated and moved on from. Filed
   without you having to ask.

Step 4 is the one that matters. You should never have to remember to make Lattice
remember.

---

## Quick start

```bash
git clone https://github.com/WilkinsonRyan/lattice-public.git ~/Lattice
cd ~/Lattice
python3 -m venv .venv && ./.venv/bin/pip install -e .
export PATH="$HOME/Lattice/.venv/bin:$PATH"     # add to your shell profile

lattice init                                     # create the data store
lattice project add ~/path/to/a/project          # start knowing things
lattice status                                   # see everything at once
```

No dependencies to install. No server to run. No account to make.

If you would rather not touch a terminal: paste this repository's URL into
whatever AI you use and say **"set this up on my computer."** It will ask which
features you want and do the rest.

`lattice init` creates a top-level split — two folders it calls `Work` and
`Personal` by default — recorded in `lattice_data/config.json`. Rename them,
add more, or reshape the split entirely; every place that reasons about "which
side of my life is this" reads that config, nothing is hardcoded.

---

## What you can do

### Know what you have

```bash
lattice project add ~/Desktop/Work/my-thing --track
lattice project list
lattice project inspect my-thing
```

Registering a project reads it: language, README summary, git branch, whether the
tree is dirty, work or personal. `--track` starts git history on anything that
doesn't have it yet.

```
Registered 'my-thing' (id 2)
  Path  /Users/you/Desktop/Work/my-thing
  Side  work   Kind  web
  About A small thing that does one job
  Git   not tracked  (add --track to start)
```

### Remember why

```bash
lattice memory add my-thing "The venv must use python.org's 3.13, not Homebrew's"
lattice memory search "venv"
```

Not chat history — engineering recall. Decisions, gotchas, commands that worked,
things that cost you an afternoon once and should never cost you one again.
Full-text search across every project you own.

### Track what's left

```bash
lattice task add my-thing "Rebuild the venv after the move"
lattice task list                # everything still open, everywhere
lattice task done 3
```

### Turn repetition into leverage

```bash
lattice pattern add my-thing "Rebuild a venv after moving a project"
lattice skill promote "Rebuild a venv after moving a project"
```

Record a workflow the second time you notice it. Lattice counts recurrences and
tells you when one has earned promotion into a reusable skill. Promotion is
**refused** for a pattern seen only once — that rule is what keeps the skills
folder full of things that matter instead of noise.

### Load context into an AI session

```bash
lattice router          # regenerate T0, the always-loaded index
lattice context my-thing  # the project's brief, plus live git and task state
```

`T0` (`lattice_data/T0.md`) is the ~600-token index designed to load automatically
at the start of every session via a `SessionStart` hook — every project by name,
open-task counts, and a trigger table saying which preferences a task needs.
Layered on purpose: T0 is cheap and always there, detail is pulled only for the
project you name.

### Briefs — the page a cold session reads instead of the repo

```bash
lattice brief list           # coverage and token budget for every project
lattice brief write my-thing # create or refresh my-thing/CONTEXT.md
lattice brief <name>         # fetch one project's brief, facts index and path
```

A brief is `CONTEXT.md` in the project folder. It is split in two:

```
everything above the marker   hand-written — what the project IS
<!-- lattice:generated -->
everything below              rewritten from the database — what is open
```

The split exists because the halves rot at different speeds. What a project is
changes rarely and only a human knows it; which tasks are open changes daily and
only the database knows it. `lattice brief write` rewrites the second half and
**never touches the first**.

The budget is ~800 tokens, because a brief is paid for by every future session
that touches the project. `lattice brief list` flags anything over budget.

### Decisions, ideas, and review

```bash
lattice decision add my-thing "Use SQLite over JSON" --why "needs cross-project queries"
lattice idea add "PCB generator" --trigger "eventually I'd like to build this"
lattice review my-thing
```

`review` assembles everything a reviewer — you, or an AI — needs before touching
the code: git state, open tasks, recent memory, a credential scan, and a
checklist. It deliberately doesn't do the review. It makes sure whoever does
starts with the full picture.

### The daily page

```bash
lattice digest --open        # today's priorities, in the browser
```

A tabbed page served on `127.0.0.1`: what needs you across every project, what
changed in the last 24 hours, mail that actually wants a reply, and a daily
sweep of the wider world for things Lattice should absorb. Every row is
actionable — approve, deny, defer — and every button says what it will do before
you click it.

### Work from more than one machine

```bash
./bin/lattice-sync        # pull, merge, publish — run on either machine
```

The SQLite file is machine-local and never committed. Knowledge exports to sorted
JSONL that git can actually merge and you can read in a text editor. Merging is
**additive** and identifies rows by their content, never by database id — ids
collide across machines, and a delete travelling between machines is how
knowledge silently disappears.

---

## The credential scanner

Every registration runs a scan. It catches AWS keys, GitHub and Slack tokens, API
keys, private key blocks, database URLs with passwords, and files that are a
problem by their very name — `client_secret*.json`, `.env`, `id_rsa`, `*.pem`.

```
  ⚠  1 possible credential found:
  [private key block]  config.js:24
```

```bash
lattice scan ~/Desktop/Work     # or point it anywhere
```

**It reports where and what kind — never the value.** No secret is printed,
logged, or written to the database. A scanner that echoes secrets has made the
problem worse.

> This is a tripwire, not a guarantee. It catches the common shapes and will miss
> a credential in an unusual format.

---

## Where things live

```
~/Lattice/
├── lattice/                  the code
│   ├── cli.py                parses commands, prints — no logic
│   ├── core.py                every workflow lives here
│   ├── router.py             T0, the always-loaded index
│   ├── storage.py            all SQL, nothing above touches it
│   ├── execution.py          git + project inspection (read-only)
│   ├── secrets.py            the credential scanner
│   ├── digest.py             what needs you today
│   ├── triage.py             approve / deny / defer, and the actions behind them
│   ├── server.py             the local page on 127.0.0.1
│   └── sweep.py              the daily read of the wider world
│
├── lattice_data/             ← your knowledge
│   ├── config.json           your roots, name tokens, and other per-install settings
│   ├── lattice.sqlite        structured: projects, memory, tasks, patterns
│   ├── T0.md                 the always-loaded index
│   ├── project_index.md      the same project list, readable without a database
│   ├── agents/               seven role definitions
│   ├── skills/               reusable workflows
│   └── backups/              pre-change copies, never committed
│
├── memory/                   one fact per file, plain markdown
├── prefs/preferences.txt     how you want things done — trigger-indexed, stable IDs
├── context/future-ideas/     projects worth building, captured before they evaporate
└── tests/                    57 tests, all passing
```

`project_index.md` is regenerated on every command. Open it on your phone, in a
text editor, anywhere — no tooling required.

---

## The seven roles

Lattice models work as roles, so behaviour stays consistent no matter which model
or session is doing it.

| Role | Owns |
|:--|:--|
| **Orchestrator** | Reads intent, picks the workflow, updates memory |
| **Architect** | Structure and tradeoffs; resists unnecessary complexity |
| **Planner** | Goals into sequenced tasks; names blockers |
| **Builder** | Implements; follows conventions; no unrelated edits |
| **Reviewer** | Bugs, regressions, missing tests, risk — findings before summaries |
| **Documenter** | Decisions and usage captured while they're fresh |
| **Skill Librarian** | Spots repetition; promotes a pattern only once it's proved |

Definitions live in `lattice_data/agents/`. Edit them — they're plain markdown.

---

## Why it's built this way

**Local-first.** Your projects stay on your machine. No cloud account, no service
to trust, no monthly bill. Remote access comes later through a deliberate
boundary, not by default.

**SQLite *and* markdown.** SQLite answers questions across projects — *which work
projects have open tasks?* Markdown means you can read your own knowledge without
any of this code.

**Full-text search, not vectors.** At one person's scale, plain search finds what
you need for none of the operational cost of a vector database.

**Zero dependencies.** Nothing to break in six months. Python's standard library
does all of it.

**The CLI holds no logic.** Commands parse input and call `core`. A phone client
or API server calls the same functions. Future interfaces are adapters, never
rewrites — that boundary is the one thing worth protecting.

**Nothing about you is hardcoded.** Folder names, what counts as "work" versus
"personal," even the words that mean "this is addressed to me" in an inbox — all
of it lives in `lattice_data/config.json`, not in the source. The same code runs
for anyone.

---

## The rules it follows

- **It never rewrites your words.** Anything you write by hand is yours. Lattice
  adds alongside it, indexes it, and leaves it alone.
- **Nothing leaves your machine.** There is no server beyond a loopback page only
  you can reach. If you want your files backed up, that's your choice to make.
- **Everything is plain text.** Delete Lattice tomorrow and you're left with
  readable folders that still explain themselves.
- **It writes things down unprompted.** That's the whole point. An AI that only
  remembers what you explicitly told it to remember is one you have to manage.

---

## Running the tests

```bash
./.venv/bin/python -m pytest tests/ -q
```

```
.........................................................     [100%]
57 passed
```

---

## Known limitations

- Context loads automatically at session start, but *writing* memory is still a
  deliberate act. Nothing is inferred silently.
- The scanner reads text files under 2 MB and skips `node_modules`, `.venv` and
  friends. Fast, but not exhaustive.
- Work/personal is inferred from the folders configured in `config.json`, so a
  project outside those roots lands as `unknown` until you pass `--side`.
- Single user. Multi-machine sync is additive only — a deletion on one machine
  does not propagate to the other.
- Synced project paths are absolute, so a project registered on one machine is
  *known* to another but not *reachable* from it. Fine for knowledge; jobs that
  touch files must run where the files are.
- `ALIAS`/`FOLD` in `lattice/memindex.py` — the vocabulary that classifies an
  untagged memory to the right project — start empty on a fresh install. Tag
  facts with a `**Project:**` line and this matters less; the alias table is a
  convenience for the ones you don't.

---

## What it isn't

Not a chatbot. Not a cloud service. Not a note-taking app you have to maintain —
if it needs maintaining, it's broken.
