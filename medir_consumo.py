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

# Precio relativo de API, con el input en 1. El volumen bruto dice cuanto se releyo;
# esto dice cuanto pesa: un token de output vale 50 de cache_read. La escritura de
# cache se cobra segun su TTL: 1,25 a 5 minutos y 2 a 1 hora. El desglose viene en
# usage.cache_creation; si falta (transcripts viejos), se asume 5 minutos.
PRECIO = {"input_tokens": 1.0, "cache_creation_input_tokens": 1.25,
          "cache_read_input_tokens": 0.1, "output_tokens": 5.0}
PRECIO_CACHE_1H = 2.0


def medir_sesion(ruta):
    """Devuelve dict con el consumo de un transcript, o None si no tiene uso."""
    # Claude Code escribe una línea por bloque de contenido (texto, tool_use) y
    # repite en cada una el mismo `usage`: sumar líneas contaba cada llamada ~1,9
    # veces. Se agrupa por requestId (o message.id si falta) y vale el último.
    usos = {}          # clave de la llamada -> usage, en orden de aparición
    sin_clave = 0
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
            clave = d.get("requestId") or msg.get("id")
            if not clave:
                sin_clave += 1
                clave = ("sin_clave", sin_clave)
            usos[clave] = u
            ts = d.get("timestamp") or ts
            if primero is None:
                primero = d.get("timestamp")
            ultimo = d.get("timestamp") or ultimo
    llamadas = len(usos)
    if not llamadas:
        return None
    suma = dict((c, 0) for c in CAMPOS)
    cache_1h = 0
    contextos = []
    for u in usos.values():
        for c in CAMPOS:
            suma[c] += u.get(c) or 0
        desglose = u.get("cache_creation")
        if isinstance(desglose, dict):
            cache_1h += desglose.get("ephemeral_1h_input_tokens") or 0
        contextos.append((u.get("input_tokens") or 0)
                         + (u.get("cache_creation_input_tokens") or 0)
                         + (u.get("cache_read_input_tokens") or 0))
    return {
        "archivo": os.path.basename(ruta),
        "proyecto": os.path.basename(os.path.dirname(ruta)),
        "inicio": (primero or "")[:16],
        "fin": (ultimo or ts or "")[:16],
        "llamadas": llamadas,
        "total": sum(suma.values()),
        "input": suma["input_tokens"],
        "cache_creation": suma["cache_creation_input_tokens"],
        "cache_creation_1h": cache_1h,
        "cache_read": suma["cache_read_input_tokens"],
        "output": suma["output_tokens"],
        "ponderado": ponderar(suma, cache_1h),
        "ctx_prom": sum(contextos) / float(llamadas),
        "ctx_max": max(contextos),
        "contextos": contextos,
    }


def ponderar(suma, cache_1h=0):
    """Costo en tokens-equivalentes de input, a precio relativo de API. `cache_1h` es
    la parte de cache_creation escrita con TTL de 1 hora, que sube de 1,25 a 2."""
    return (sum(suma[c] * PRECIO[c] for c in CAMPOS)
            + cache_1h * (PRECIO_CACHE_1H - PRECIO["cache_creation_input_tokens"]))


def percentil(xs, p):
    if not xs:
        return 0
    ys = sorted(xs)
    return ys[min(len(ys) - 1, int(len(ys) * p))]


def main():
    ap = argparse.ArgumentParser(description="Consumo de tokens de Claude Code")
    ap.add_argument("--dias", type=int, default=0,
                    help="solo sesiones cuyo ultimo mensaje sea de los ultimos N dias")
    ap.add_argument("--top", type=int, default=8, help="sesiones en el ranking")
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
    comp = {"input": sum(s["input"] for s in sesiones),
            "cache_creation": sum(s["cache_creation"] for s in sesiones),
            "cache_read": sum(s["cache_read"] for s in sesiones),
            "output": sum(s["output"] for s in sesiones)}
    cache_1h = sum(s["cache_creation_1h"] for s in sesiones)
    peso = {"input": comp["input"] * PRECIO["input_tokens"],
            "cache_creation": comp["cache_creation"] * PRECIO["cache_creation_input_tokens"]
                              + cache_1h * (PRECIO_CACHE_1H
                                            - PRECIO["cache_creation_input_tokens"]),
            "cache_read": comp["cache_read"] * PRECIO["cache_read_input_tokens"],
            "output": comp["output"] * PRECIO["output_tokens"]}
    ponderado = sum(peso.values())
    ctx_todos = [c for s in sesiones for c in s["contextos"]]
    pisos = sorted(s["contextos"][0] for s in sesiones)

    # Un promedio sobre una ventana que cruza un cambio de mecanismo no describe
    # ningun regimen. El banner sale arriba para que se vea antes que las cifras.
    from regimen import avisar
    avisar(dias=args.dias or None)

    # Salida compacta a proposito (unas 25 lineas): se lee desde el chat y cada
    # linea se relee en los turnos que siguen. El detalle por sesion va al --csv.
    ambito = "ultimos %d dias" % args.dias if args.dias else "historico completo"
    print("CONSUMO DE TOKENS (%s): %d sesiones, %s llamadas"
          % (ambito, len(sesiones), format(llamadas, ",")))
    print("  volumen bruto %.1f M | cache_read %.1f M (%.0f%%) | cache_creation %.1f M "
          "(%.0f%% a 1 h) | output %.1f M (%.1f%%)"
          % (total / 1e6, comp["cache_read"] / 1e6, 100.0 * comp["cache_read"] / total,
             comp["cache_creation"] / 1e6,
             100.0 * cache_1h / max(1, comp["cache_creation"]),
             comp["output"] / 1e6, 100.0 * comp["output"] / total))
    print("  costo ponderado (API relativo; input 1, cache_creation 1,25 a 5 min y 2 a 1 h, "
          "cache_read 0,1, output 5): %.1f M eq." % (ponderado / 1e6))
    print("     " + " | ".join("%s %.0f%%" % (k, 100.0 * peso[k] / max(1, ponderado))
                              for k in ("cache_read", "cache_creation", "output", "input")))
    sobre100 = sum(1 for c in ctx_todos if c > 100000)
    print("  contexto por llamada: prom %.0fk | mediana %.0fk | p90 %.0fk | max %.0fk | "
          ">100k %.0f%%"
          % (sum(ctx_todos) / len(ctx_todos) / 1000.0,
             percentil(ctx_todos, .5) / 1000.0,
             percentil(ctx_todos, .9) / 1000.0,
             max(ctx_todos) / 1000.0,
             100.0 * sobre100 / len(ctx_todos)))
    print("  piso fijo (1a llamada): mediana %.0fk | max %.0fk -> ~%.0f M pagados en "
          "cada llamada (%.0f%%)"
          % (percentil(pisos, .5) / 1000.0, max(pisos) / 1000.0,
             percentil(pisos, .5) * llamadas / 1e6,
             100.0 * percentil(pisos, .5) * llamadas / total))

    sesiones.sort(key=lambda s: -s["total"])
    tramos = ["top %d %.0f%%" % (n, 100.0 * sum(s["total"] for s in sesiones[:n]) / total)
              for n in (5, 10, 20) if n <= len(sesiones)]
    cortas = [s for s in sesiones if s["llamadas"] <= 30]
    if cortas:
        tramos.append("<=30 llamadas: %d sesiones (%.0f%%) = %.0f%% del gasto"
                      % (len(cortas), 100.0 * len(cortas) / len(sesiones),
                         100.0 * sum(s["total"] for s in cortas) / total))
    print("  concentracion: " + " | ".join(tramos))

    print()
    print("TOP %d SESIONES" % min(args.top, len(sesiones)))
    print("  %-16s %6s %9s %9s %9s  %s"
          % ("inicio", "llam.", "tokens", "pond.", "ctx prom", "id"))
    for s in sesiones[:args.top]:
        print("  %-16s %6d %8.1fM %8.1fM %8.0fk  %s"
              % (s["inicio"], s["llamadas"], s["total"] / 1e6, s["ponderado"] / 1e6,
                 s["ctx_prom"] / 1000.0, s["archivo"][:8]))

    print()
    print("QUE HACER: sesion nueva por tema (/clear al cambiar), salidas grandes a archivo,")
    print("grep+sed en vez de leer archivos enteros.")

    if args.csv:
        with io.open(args.csv, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["proyecto", "sesion", "inicio", "fin", "llamadas",
                        "tokens_total", "input", "cache_creation", "cache_creation_1h",
                        "cache_read", "output", "costo_ponderado", "ctx_promedio", "ctx_max"])
            for s in sesiones:
                w.writerow([s["proyecto"], s["archivo"], s["inicio"], s["fin"],
                            s["llamadas"], s["total"], s["input"], s["cache_creation"],
                            s["cache_creation_1h"], s["cache_read"], s["output"], int(s["ponderado"]),
                            int(s["ctx_prom"]), s["ctx_max"]])
        print("CSV escrito en %s" % args.csv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
