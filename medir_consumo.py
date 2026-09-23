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
    python medir_consumo.py --desde "2026-09-10 22:25"   # desde un cambio
    python medir_consumo.py --desde "2026-09-13 13:54" --hasta "2026-09-16 19:47"
    python medir_consumo.py --top 20       # más sesiones en el ranking
    python medir_consumo.py --csv salida.csv

`--dias` se queda con las sesiones cuyo último mensaje cae en la ventana, enteras.
`--desde` (hora local) corta por llamada: de una sesión que cruza el cambio cuenta solo
lo posterior, que es lo que hace falta para medir un régimen nuevo de pocos días.
`--hasta` cierra la ventana por arriba con el mismo corte por llamada, y con los dos se
aísla un régimen que ya terminó: sin él, el régimen viejo queda mezclado con todo lo que
vino después de su último día.

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


def medir_sesion(ruta, desde=None, hasta=None):
    """Devuelve dict con el consumo de un transcript, o None si no tiene uso. Con
    `desde` (datetime con zona) cuenta solo las llamadas posteriores, y con `hasta`
    solo las anteriores o iguales a ese momento."""
    # Claude Code escribe una línea por bloque de contenido (texto, tool_use) y
    # repite en cada una el mismo `usage`: sumar líneas contaba cada llamada ~1,9
    # veces. Se agrupa por requestId (o message.id si falta) y vale el último.
    usos = {}          # clave de la llamada -> (usage, timestamp), en orden de aparición
    sin_clave = 0
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
            ts = d.get("timestamp") or ts
            usos[clave] = (u, ts)
    cortada = False
    if desde is not None or hasta is not None:
        from regimen import marca
        antes = len(usos)
        if desde is not None:
            usos = dict((k, v) for k, v in usos.items()
                        if (marca(v[1]) or desde) > desde)
            # Solo el corte por abajo invalida el piso: la primera llamada que queda
            # ya trae contexto acumulado. Recortar la cola no mueve la primera.
            cortada = len(usos) < antes
        if hasta is not None:
            usos = dict((k, v) for k, v in usos.items()
                        if (marca(v[1]) or hasta) <= hasta)
    llamadas = len(usos)
    if not llamadas:
        return None
    marcas = sorted(m for _u, m in usos.values() if m)
    suma = dict((c, 0) for c in CAMPOS)
    cache_1h = 0
    contextos = []
    escrituras = []    # cache_creation de cada llamada, en orden: el panel la parte
    # Costo ponderado por día local y por componente: lo usa el panel para el gráfico
    # diario. Una sesión que cruza la medianoche reparte sus llamadas entre los dos días.
    from regimen import marca as _marca
    por_dia = {}
    for u, m in usos.values():
        for c in CAMPOS:
            suma[c] += u.get(c) or 0
        desglose = u.get("cache_creation")
        uno_h = 0
        if isinstance(desglose, dict):
            uno_h = desglose.get("ephemeral_1h_input_tokens") or 0
            cache_1h += uno_h
        momento = _marca(m)
        if momento is not None:
            dia = por_dia.setdefault(momento.astimezone().date(),
                                     dict((c, 0.0) for c in CAMPOS))
            for c in CAMPOS:
                dia[c] += (u.get(c) or 0) * PRECIO[c]
            dia["cache_creation_input_tokens"] += uno_h * (
                PRECIO_CACHE_1H - PRECIO["cache_creation_input_tokens"])
        escrituras.append(u.get("cache_creation_input_tokens") or 0)
        contextos.append((u.get("input_tokens") or 0)
                         + (u.get("cache_creation_input_tokens") or 0)
                         + (u.get("cache_read_input_tokens") or 0))
    return {
        "archivo": os.path.basename(ruta),
        "proyecto": os.path.basename(os.path.dirname(ruta)),
        "inicio": (marcas[0] if marcas else "")[:16],
        "fin": (marcas[-1] if marcas else "")[:16],
        "cortada": cortada,
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
        "escrituras": escrituras,
        "por_dia": por_dia,
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
    from regimen import avisar, leer_desde, local
    ap = argparse.ArgumentParser(description="Consumo de tokens de Claude Code")
    ventana = ap.add_mutually_exclusive_group()
    ventana.add_argument("--dias", type=int, default=0,
                         help="solo sesiones cuyo ultimo mensaje sea de los ultimos N dias")
    ventana.add_argument("--desde", type=leer_desde, metavar="'AAAA-MM-DD HH:MM'",
                         help="solo las llamadas posteriores a ese momento (hora local)")
    ap.add_argument("--hasta", type=leer_desde, metavar="'AAAA-MM-DD HH:MM'",
                    help="cierra la ventana por arriba, con el mismo corte por llamada")
    ap.add_argument("--top", type=int, default=8, help="sesiones en el ranking")
    ap.add_argument("--csv", help="volcar el detalle por sesion a un CSV")
    ap.add_argument("--raiz", default=RAIZ, help="carpeta de proyectos de Claude Code")
    args = ap.parse_args()
    if args.hasta and args.dias:
        ap.error("--hasta corta por llamada y --dias por sesion: va con --desde")
    if args.hasta and args.desde and args.hasta <= args.desde:
        ap.error("--hasta tiene que ser posterior a --desde")

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
            s = medir_sesion(r, args.desde, args.hasta)
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
    # El piso es la primera llamada de la sesion: de una sesion cortada por --desde,
    # la primera llamada que quedo ya trae contexto acumulado y no es piso.
    pisos = sorted(s["contextos"][0] for s in sesiones if not s["cortada"]) or [0]

    # Un promedio sobre una ventana que cruza un cambio de mecanismo no describe
    # ningun regimen. El banner sale arriba para que se vea antes que las cifras.
    avisar(dias=args.dias or None, desde=args.desde, hasta=args.hasta)

    # Salida compacta a proposito (unas 25 lineas): se lee desde el chat y cada
    # linea se relee en los turnos que siguen. El detalle por sesion va al --csv.
    if args.desde and args.hasta:
        ambito = "de %s a %s" % (local(args.desde), local(args.hasta))
    elif args.desde:
        ambito = "desde %s" % local(args.desde)
    elif args.hasta:
        ambito = "hasta %s" % local(args.hasta)
    else:
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
