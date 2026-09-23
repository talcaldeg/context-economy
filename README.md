# context-economy

Read-only scripts that measure where a coding agent's tokens actually go. They read the
`.jsonl` transcripts that Claude Code writes to `~/.claude/projects`, which already carry the
`usage` block of every call to the model. Nothing is sent anywhere, nothing is modified, and no
vendor telemetry is needed.

They are the scripts behind this article:
[Context economy](https://talcaldeg.github.io/articulos/context-economy/)
([versión en español](https://talcaldeg.github.io/articulos/economia-de-contexto/)).

## What each one answers

| script | question |
| --- | --- |
| `medir_consumo.py` | how much did each session spend, and how much of it is re-reading context rather than producing anything |
| `medir_tool_results.py` | which specific call put the bulk into the context, ranked by *carry*: result tokens x the turns that came after it |
| `medir_paralelismo.py` | how many calls were wasted because two independent reads travelled in separate calls, and whether a hook that nudges the model to batch them would pay for itself |
| `medir_juntar.py` | whether continuing with the next task in the same session beats starting a fresh one (`/clear`) |
| `graficos_articulo.py` | reuses the first two and draws the four charts, printing the exact figures the article quotes |
| `regimen.py` | warns, above the figures, when the window you measure crosses a change in your setup, because an average over two regimes describes neither |

```bash
python medir_consumo.py --dias 30
python medir_tool_results.py --dias 30 --top 25
python medir_paralelismo.py --desde "2026-09-21 11:44" --muestra pares.csv
python medir_juntar.py --desde "2026-09-20 00:00"
```

The console output is compact on purpose (about 25 lines, `--top 8`), because it is usually read
from inside an agent's chat, where every line is re-read on every later turn. The full detail
goes to `--csv` (or `--muestra`).

## Measuring a window

`--dias N` keeps the sessions of the last N days, whole. That is fine for a long history but
wrong right after you change something: a session that started before the change drags the old
regime into the new one. `--desde "YYYY-MM-DD HH:MM"` (local time) cuts per call instead, so a
session that crosses the change only counts what came after it. `--hasta` closes the window from
above with the same per-call cut, which isolates a regime that has already ended. `--dias` and
`--desde` are mutually exclusive.

## How they count

- **One API call, one `usage`.** Claude Code writes one line per content block (text,
  `tool_use`) and repeats the same `usage` on each of them, so summing lines counted every call
  about 1.9 times. Calls are grouped by `requestId` (or `message.id`) and counted once.
- **Weighted cost next to raw volume.** Raw volume says how much was re-read; the weighted cost
  says how much it weighs, at relative API prices with input at 1: output 5, `cache_read` 0.1,
  and cache writes by TTL, 1.25 for 5 minutes and 2 for 1 hour (read from
  `usage.cache_creation`; older transcripts without that breakdown are priced at 5 minutes).
  On my last 30 days, `cache_read` is 96 % of the volume but 49 % of the weighted cost, and 99 %
  of the cache writes are 1-hour ones: pricing them all at 1.25 understated the total by 14 %.
- **Carry restarts at each compaction.** A `tool_result` is re-read on every later turn only
  until the conversation is compacted, when the summary replaces the context. The turn count
  restarts at each `compact_boundary`.
- **The floor is the first call of a session.** A session cut by `--desde` is left out of the
  floor statistics, because its first remaining call already carries accumulated context.
- **Connector names are readable.** The `mcp__<uuid>__` prefix is stripped before the tool name
  is shortened; with the raw name, every tool of a connector looked the same.
- **The window can lie.** `regimen.py` looks at the modification time of
  `~/.claude/settings.json`, `~/.claude/CLAUDE.md` and everything in `~/.claude/hooks/`. Add your
  own pieces (a repo's `CLAUDE.md`, a memory index) with `CONTEXT_ECONOMY_PIEZAS`, paths separated
  by `os.pathsep`. If one changed inside the window, the banner prints the exact
  `--desde "YYYY-MM-DD HH:MM"` to measure again from, rounded up to the next minute so the new
  window is clean. It works with hours rather than days because a regime a few days old is exactly
  the case where each hour of the old one weighs the most.

## Batching reads: `medir_paralelismo.py`

Every API call re-reads the whole context. If call B carries a single read, comes right after a
call A that also carried a single read, there was no user prompt in between and B's input uses
nothing that first appeared in A's result, then B could have gone inside A. Runs of independent
reads collapse to one call. The sum of those wasted calls is a **ceiling**: what any mechanism
would save if the model always obeyed.

- In a shell, a "read" is any command with no sign of writing (a blocklist, not an allowlist:
  real commands start with labels, variables and scripts that only query).
- B depends on A if it uses a term that just appeared in A's result, if A failed (B is usually
  the retry), or if it goes back to the same file (narrowing: `grep -n` then `sed -n`).
- `--herramientas a.py,b.ps1` (or `CONTEXT_ECONOMY_HERRAMIENTAS`) lists scripts you use to read
  *other* things, such as a mail reader or a database client: naming them says nothing about what
  is being worked on, so they do not count as "the same file".
- `--aviso N` prices a hook that injects an N-token nudge after every lone read. The nudge stays
  in the context and is re-read like any `tool_result`; the script prints the minimum obedience
  rate at which it breaks even.
- The heuristic leans towards "independent", which is right for a ceiling but inflates it: on my
  transcripts, of 30 pairs it flagged as independent, about 42 % really were. Check yours with
  `--muestra pares.csv` before trusting the figure.

## Session boundaries: `medir_juntar.py`

The advice "one session per task" has a cost: the first call of a new session writes its whole
floor to the cache again. The script takes pairs of consecutive sessions in the same folder with
less than an hour between them and simulates a rule that does not look ahead: "if this session
is less than T tokens above its floor, continue the next task here". Joining saves the second
session's start-up write (at 1.9, minus ~3k that the new prompt writes anyway) and costs
re-reading the first session's excess at 0.1 on every call of the second.

- Overlapping pairs (the second session started before the first one ended) cannot be joined
  and are reported apart; leaving them in inflates the result.
- `--excluir <id>` leaves out the session you are running it from.
- It measures tokens only. It does not measure what mixing two topics in one context costs in
  quality, which is the reason for the advice in the first place.

## Reproducing the article's numbers

The measurement window is frozen with an exclusive cut-off date, so the figures do not drift
every time the script runs (without it, the session that writes the article counts itself):

```bash
python graficos_articulo.py --dias 120 --hasta 2026-08-27 --idioma en --salida img
```

The article's figures were computed before the `requestId` deduplication and the compaction cut
above: 313 sessions, 2,356 M tokens, 94 % `cache_read`, a fit of `cost ∝ turns^1.20` with R² 0.97,
and 54 % of the carry coming from reading whole files. The same command against my transcripts
now prints 294 sessions, 1,212 M tokens, 96 % `cache_read`, `cost ∝ turns^1.19` with R² 0.97, and
55 % of a 181 M carry from reading whole files. The totals halve; the shape of the argument does
not move. Against your transcripts it will print something else, which is the point: what is
worth having is the ranking of your own transcripts, not mine.

## Requirements

Python 3.8 or newer. Only `graficos_articulo.py` needs a third-party library:

```bash
pip install matplotlib
```

## A note on the language

The code comments and the console output are in Spanish, which is the language I work in. The
chart labels take `--idioma es|en`. The scripts are short, a few hundred lines each, and the
logic is the interesting part rather than the wording: if it is easier to rewrite them in your
own language than to read mine, that is a perfectly good outcome.

## License

MIT, see [LICENSE](LICENSE).
