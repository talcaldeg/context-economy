# -*- coding: utf-8 -*-
"""¿Conviene seguir con la tarea siguiente en la misma sesión en vez de `/clear`?

Simula, sobre pares de sesiones consecutivas de la misma carpeta y con menos de una hora
entre ambas, la regla que no mira el futuro: "si la sesión va bajo T sobre el piso,
juntar la siguiente". Juntar ahorra la escritura propia del arranque de la segunda (a 1,9,
descontados ~3k que el prompt nuevo escribe igual) y cuesta releer el exceso de la primera
a 0,1 en cada llamada de la segunda. No mide el costo en calidad de mezclar temas.

Los pares solapados (la segunda sesión arrancó antes de que la primera terminara) no se
pueden juntar y se cuentan aparte. La columna "con solapes" los deja adentro para mostrar
cuánto inflan: en la primera versión de esta simulación daban ~0,4 puntos de más.

    python medir_juntar.py --desde "2026-09-23 13:03" [--hasta "..."] [--excluir <id>]
"""

from __future__ import print_function

import argparse
import collections
import datetime as dt
import glob
import os
import statistics

from medir_consumo import RAIZ, PRECIO, PRECIO_CACHE_1H
from medir_paralelismo import leer_sesion
from regimen import avisar, leer_desde, marca


def pond(u):
    cc = u.get("cache_creation_input_tokens") or 0
    d = u.get("cache_creation") or {}
    h = (d.get("ephemeral_1h_input_tokens") or 0) if isinstance(d, dict) else 0
    return ((u.get("input_tokens") or 0) + cc * PRECIO["cache_creation_input_tokens"]
            + h * (PRECIO_CACHE_1H - PRECIO["cache_creation_input_tokens"])
            + (u.get("cache_read_input_tokens") or 0) * 0.1 + (u.get("output_tokens") or 0) * 5)


def ctx(u):
    return sum(u.get(c) or 0 for c in ("input_tokens", "cache_creation_input_tokens",
                                        "cache_read_input_tokens"))


def cargar(desde, hasta, excluir):
    ses, proy = [], {}
    for ruta in glob.glob(os.path.join(RAIZ, "*", "*.jsonl")):
        if (excluir and excluir in ruta) or os.path.getmtime(ruta) < desde.timestamp():
            continue
        calls, humano = [], False
        for e in leer_sesion(ruta):
            if e[0] == "humano":
                humano = True
            elif e[0] == "llamada":
                ll = e[1]
                m = marca(ll.get("ts"))
                if m is None or m <= desde or (hasta and m > hasta) or "usage" not in ll:
                    humano = False
                    continue
                calls.append(dict(ll=ll, ctx=ctx(ll["usage"]), pond=pond(ll["usage"]),
                                  prompt=humano, ts=m))
                humano = False
        if calls:
            n = os.path.basename(ruta)
            ses.append((n, calls))
            proy[n] = os.path.basename(os.path.dirname(ruta))
    ses.sort(key=lambda s: s[1][0]["ts"])
    return ses, proy


def regla(ses, proy, T, con_solapes):
    n = neg = sol = 0
    net = 0.0
    for (na, ca), (nb, cb) in zip(ses, ses[1:]):
        gap = (cb[0]["ts"] - ca[-1]["ts"]).total_seconds()
        if gap > 3600 or proy[na] != proy[nb]:
            continue
        if gap < 0:
            sol += 1
            if not con_solapes:
                continue
        E = ca[-1]["ctx"] - ca[0]["ctx"]
        if E >= T:
            continue
        w = cb[0]["ll"]["usage"].get("cache_creation_input_tokens") or 0
        d = 1.9 * max(0, w - 3000) - 0.1 * E * len(cb)
        n += 1
        net += d
        neg += d < 0
    return n, neg, net, sol


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--desde", type=leer_desde, required=True)
    ap.add_argument("--hasta", type=leer_desde)
    ap.add_argument("--excluir", help="id (o parte) de la sesión en curso, para no medirse")
    args = ap.parse_args()
    avisar(desde=args.desde, hasta=args.hasta)

    ses, proy = cargar(args.desde, args.hasta, args.excluir)
    if not ses:
        print("Sin sesiones en la ventana.")
        return
    total = sum(c["pond"] for _, cs in ses for c in cs)
    fin = args.hasta or dt.datetime.now().astimezone()
    print("Ventana de %.1f días: %d sesiones, %d llamadas, %.1f M eq ponderados" % (
        (fin - args.desde).total_seconds() / 86400, len(ses),
        sum(len(cs) for _, cs in ses), total / 1e6))
    arr = sum(cs[0]["pond"] for _, cs in ses)
    uno = sum(1 for _, cs in ses if sum(c["prompt"] for c in cs) <= 1)
    print("Arranque %.1f %% del ponderado; %d de %d sesiones con un solo prompt" % (
        100 * arr / total, uno, len(ses)))
    print("Exceso al cierre: mediana %.0fk; escritura propia del arranque: mediana %.0fk" % (
        statistics.median(cs[-1]["ctx"] - cs[0]["ctx"] for _, cs in ses) / 1e3,
        statistics.median((cs[0]["ll"]["usage"].get("cache_creation_input_tokens") or 0)
                          for _, cs in ses) / 1e3))
    print("Sesiones por carpeta: %s" % ", ".join(
        "%s %d" % (k.split("-")[-1], v)
        for k, v in collections.Counter(proy[n] for n, _ in ses).most_common()))
    print("\nRegla: misma carpeta, <1 h entre ambas, exceso bajo T (neto % del ponderado)")
    print("   T    sin solapes (juntables)       con solapes (inflado)")
    for T in (20e3, 30e3, 40e3):
        y = regla(ses, proy, T, False)
        x = regla(ses, proy, T, True)
        print("  %3.0fk  %2d juntas, %2d pierden, %+5.2f %%   %2d juntas, %+5.2f %%  [%d solapados]" % (
            T / 1e3, y[0], y[1], 100 * y[2] / total, x[0], 100 * x[2] / total, x[3]))


if __name__ == "__main__":
    main()
