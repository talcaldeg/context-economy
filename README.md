# context-economy

Three read-only scripts that measure where a coding agent's tokens actually go. They read the
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
| `graficos_articulo.py` | reuses both of the above and draws the four charts, printing the exact figures the article quotes |
| `regimen.py` | warns, above the figures, when the window you measure crosses a change in your setup, because an average over two regimes describes neither |

```bash
python medir_consumo.py --dias 30
python medir_tool_results.py --dias 30 --top 25
```

The console output is compact on purpose (about 25 lines, `--top 8`), because it is usually read
from inside an agent's chat, where every line is re-read on every later turn. The full detail
goes to `--csv`.

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
- **Connector names are readable.** The `mcp__<uuid>__` prefix is stripped before the tool name
  is shortened; with the raw name, every tool of a connector looked the same.
- **The window can lie.** `regimen.py` looks at the modification date of
  `~/.claude/settings.json`, `~/.claude/CLAUDE.md` and everything in `~/.claude/hooks/`. Add your
  own pieces (a repo's `CLAUDE.md`, a memory index) with `CONTEXT_ECONOMY_PIEZAS`, paths separated
  by `os.pathsep`. If one changed inside the window, measure again from that date before deciding
  anything.

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
chart labels take `--idioma es|en`. The scripts are short, around two hundred lines each, and
the logic is the interesting part rather than the wording: if it is easier to rewrite them in
your own language than to read mine, that is a perfectly good outcome.

## License

MIT, see [LICENSE](LICENSE).
