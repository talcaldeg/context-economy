#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Mide el consumo de tokens de las sesiones de Claude Code y dice dónde se va.

Existe para no tener que rederivar el análisis a mano cada vez (rederivarlo cuesta
tokens, que es justo lo que se quiere ahorrar). Reglas del diagnóstico:

  - El grueso del gasto es `cache_read`: releer el contexto acumulado en cada turno.
  - El costo crece con la duración de la sesión, no con el trabajo hecho: el ajuste
    sobre 313 sesiones dio `costo ∝ turnos^1,20`, no cuadrático como se supuso al
    principio (el piso fijo por sesión diluye la parte que crece).
  - Por eso las palancas son (a) sesiones cortas por tema y (b) bajar el piso fijo.

Uso:
    python medir_consumo.py                # todos los proyectos
    python medir_consumo.py --dias 30      # solo lo reciente
    python medir_consumo.py --top 20       # más sesiones en el ranking
    python medir_consumo.py --csv salida.csv

No modifica nada: solo lee los .jsonl de ~/.claude/projects.
"""

from __future__ import print_function

import argparse
import csv
import datetime as dt
import glob
import io
import json
import os
import sys

RAIZ = os.path.join(os.path.expanduser("~"), ".claude", "projects")

CAMPOS = ("input_tokens", "output_tokens",
          "cache_creation_input_tokens", "cache_read_input_tokens")


def medir_sesion(ruta):
    """Devuelve dict con el consumo de un transcript, o None si no tiene uso."""
    llamadas = 0
    suma = dict((c, 0) for c in CAMPOS)
    contextos = []
    primero = None
    ultimo = None
    ts = None
    with io.open(ruta, "r", encoding="utf-8", errors="ignore") as fh:
        for linea in fh:
            if '"usage"' not in linea:
                continue
            try:
                d = json.loads(linea)
            except Exception:
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                continue
            u = msg.get("usage")
            if not isinstance(u, dict):
                continue
            llamadas += 1
            for c in CAMPOS:
                suma[c] += u.get(c) or 0
            ctx = ((u.get("input_tokens") or 0)
                   + (u.get("cache_creation_input_tokens") or 0)
                   + (u.get("cache_read_input_tokens") or 0))
            contextos.append(ctx)
            ts = d.get("timestamp") or ts
            if primero is None:
                primero = d.get("timestamp")
            ultimo = d.get("timestamp") or ultimo
    if not llamadas:
        return None
    return {
        "archivo": os.path.basename(ruta),
        "proyecto": os.path.basename(os.path.dirname(ruta)),
        "inicio": (primero or "")[:16],
        "fin": (ultimo or ts or "")[:16],
        "llamadas": llamadas,
        "total": sum(suma.values()),
        "cache_read": suma["cache_read_input_tokens"],
        "output": suma["output_tokens"],
        "ctx_prom": sum(contextos) / float(llamadas),
        "ctx_max": max(contextos),
        "contextos": contextos,
    }


def percentil(xs, p):
    if not xs:
        return 0
    ys = sorted(xs)
    return ys[min(len(ys) - 1, int(len(ys) * p))]


def main():
    ap = argparse.ArgumentParser(description="Consumo de tokens de Claude Code")
    ap.add_argument("--dias", type=int, default=0,
                    help="solo sesiones cuyo ultimo mensaje sea de los ultimos N dias")
    ap.add_argument("--top", type=int, default=12, help="sesiones en el ranking")
    ap.add_argument("--csv", help="volcar el detalle por sesion a un CSV")
    ap.add_argument("--raiz", default=RAIZ, help="carpeta de proyectos de Claude Code")
    args = ap.parse_args()

    patron = os.path.join(args.raiz, "*", "*.jsonl")
    rutas = glob.glob(patron)
    if not rutas:
        print("No se encontraron transcripts en %s" % args.raiz)
        return 1

    corte = None
    if args.dias:
        corte = (dt.datetime.now(dt.timezone.utc)
                 - dt.timedelta(days=args.dias)).strftime("%Y-%m-%d")

    sesiones = []
    for r in rutas:
        try:
            s = medir_sesion(r)
        except Exception:
            continue
        if not s:
            continue
        if corte and s["fin"][:10] < corte:
            continue
        sesiones.append(s)

    if not sesiones:
        print("Sin sesiones en el rango pedido.")
        return 0

    total = sum(s["total"] for s in sesiones)
    llamadas = sum(s["llamadas"] for s in sesiones)
    cache_read = sum(s["cache_read"] for s in sesiones)
    salida = sum(s["output"] for s in sesiones)
    ctx_todos = [c for s in sesiones for c in s["contextos"]]
    pisos = sorted(s["contextos"][0] for s in sesiones)

    ambito = "ultimos %d dias" % args.dias if args.dias else "historico completo"
    print("=" * 68)
    print("CONSUMO DE TOKENS  (%s)" % ambito)
    print("=" * 68)
    print("  sesiones ............ %d" % len(sesiones))
    print("  llamadas a la API ... %s" % format(llamadas, ","))
    print("  tokens brutos ....... %.1f M" % (total / 1e6))
    print("  cache_read .......... %.1f M  (%.0f%% del total)  <- releer contexto"
          % (cache_read / 1e6, 100.0 * cache_read / total))
    print("  output .............. %.1f M  (%.1f%%)" % (salida / 1e6, 100.0 * salida / total))
    print()
    print("  contexto por llamada: prom %.0fk | mediana %.0fk | p90 %.0fk | max %.0fk"
          % (sum(ctx_todos) / len(ctx_todos) / 1000.0,
             percentil(ctx_todos, .5) / 1000.0,
             percentil(ctx_todos, .9) / 1000.0,
             max(ctx_todos) / 1000.0))
    sobre100 = sum(1 for c in ctx_todos if c > 100000)
    print("  llamadas sobre 100k . %s (%.0f%%)"
          % (format(sobre100, ","), 100.0 * sobre100 / len(ctx_todos)))
    print("  piso fijo (1a llamada de cada sesion): mediana %.0fk | max %.0fk"
          % (percentil(pisos, .5) / 1000.0, max(pisos) / 1000.0))
    print("     -> ese piso se paga en CADA llamada: ~%.0f M de tokens (%.0f%% del total)"
          % (percentil(pisos, .5) * llamadas / 1e6,
             100.0 * percentil(pisos, .5) * llamadas / total))

    sesiones.sort(key=lambda s: -s["total"])
    print()
    print("CONCENTRACION DEL GASTO")
    for n in (5, 10, 20):
        if n <= len(sesiones):
            print("  top %-2d sesiones = %.0f%% del consumo"
                  % (n, 100.0 * sum(s["total"] for s in sesiones[:n]) / total))
    cortas = [s for s in sesiones if s["llamadas"] <= 30]
    if cortas:
        print("  sesiones de <=30 llamadas: %d (%.0f%% de las sesiones) = %.0f%% del gasto"
              % (len(cortas), 100.0 * len(cortas) / len(sesiones),
                 100.0 * sum(s["total"] for s in cortas) / total))

    print()
    print("TOP %d SESIONES" % min(args.top, len(sesiones)))
    print("  %-16s %6s %9s %9s  %s" % ("inicio", "llam.", "tokens", "ctx prom", "id"))
    for s in sesiones[:args.top]:
        print("  %-16s %6d %8.1fM %8.0fk  %s"
              % (s["inicio"], s["llamadas"], s["total"] / 1e6,
                 s["ctx_prom"] / 1000.0, s["archivo"][:8]))

    print()
    print("QUE HACER: sesion nueva por tema (/clear al cambiar), salidas grandes a")
    print("archivo, grep+sed en vez de leer archivos enteros, modelo por tarea.")
    print("Detalle en la memoria feedback_token_efficiency.md.")

    if args.csv:
        with io.open(args.csv, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["proyecto", "sesion", "inicio", "fin", "llamadas",
                        "tokens_total", "cache_read", "output", "ctx_promedio", "ctx_max"])
            for s in sesiones:
                w.writerow([s["proyecto"], s["archivo"], s["inicio"], s["fin"],
                            s["llamadas"], s["total"], s["cache_read"], s["output"],
                            int(s["ctx_prom"]), s["ctx_max"]])
        print("\nCSV escrito en %s" % args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
