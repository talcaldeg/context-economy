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

```bash
python medir_consumo.py --dias 30
python medir_tool_results.py --dias 30 --top 25
```

## Reproducing the article's numbers

The measurement window is frozen with an exclusive cut-off date, so the figures do not drift
every time the script runs (without it, the session that writes the article counts itself):

```bash
python graficos_articulo.py --dias 120 --hasta 2026-08-27 --idioma en --salida img
```

Against my own transcripts that prints 313 sessions, 2,356 M tokens, 94 % `cache_read`, a fit of
`cost ∝ turns^1.20` with R² 0.97, and 54 % of the carry coming from reading whole files. Against
yours it will print something else, which is the point: what is worth having is the ranking of
your own transcripts, not mine.

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
