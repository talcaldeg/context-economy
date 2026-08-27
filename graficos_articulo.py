# -*- coding: utf-8 -*-
"""Graficos y cifras para el articulo sobre economia de contexto.

Reusa los dos medidores del repo (`medir_consumo.py` y `medir_tool_results.py`)
y produce las figuras que acompanan al texto, mas las cifras exactas que el
articulo cita. Existe para que el articulo sea reproducible: si alguien vuelve a
correrlo dentro de seis meses, los numeros se recalculan solos y se ve si el
patron aguanta.

    python graficos_articulo.py --dias 120 --salida articulo/img

Solo lee los .jsonl de ~/.claude/projects. No modifica nada.
"""

import argparse
import datetime as dt
import glob
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

import medir_consumo
import medir_tool_results

RAIZ = medir_consumo.RAIZ

# Paleta sobria, legible impresa en blanco y negro.
TINTA = "#14213d"
ACENTO = "#c1121f"
GRIS = "#8d99ae"
FONDO = "#ffffff"

plt.rcParams.update({
    "figure.facecolor": FONDO,
    "axes.facecolor": FONDO,
    "axes.edgecolor": GRIS,
    "axes.labelcolor": TINTA,
    "text.color": TINTA,
    "xtick.color": TINTA,
    "ytick.color": TINTA,
    "font.size": 11,
    "axes.spines.top": False,
    "axes.spines.right": False,
})


# Los rotulos de las figuras salen en el idioma del articulo que las usa. El
# reporte a consola queda siempre en espanol: lo lee el operador, no el lector.
IDIOMA = "es"

TEXTOS = {
    "curva.ajuste":    {"es": u"ajuste: costo ∝ turnos^%.2f  (R²=%.2f)",
                        "en": u"fit: cost ∝ turns^%.2f  (R²=%.2f)"},
    "curva.lineal":    {"es": u"si cada turno costara lo mismo (lineal)",
                        "en": u"if every turn cost the same (linear)"},
    "curva.x":         {"es": u"turnos de la sesión (llamadas al modelo)",
                        "en": u"turns in the session (calls to the model)"},
    "curva.y":         {"es": u"tokens consumidos por la sesión",
                        "en": u"tokens consumed by the session"},
    "curva.titulo":    {"es": u"Una sesión el doble de larga no cuesta el doble",
                        "en": u"A session twice as long does not cost twice as much"},
    "reparto.cache":   {"es": u"releer contexto ya\nacumulado (cache_read)",
                        "en": u"re-reading context\nalready there (cache_read)"},
    "reparto.nuevo":   {"es": u"contexto nuevo\n(entrada + escritura de caché)",
                        "en": u"new context\n(input + cache writes)"},
    "reparto.output":  {"es": u"lo que el modelo\nescribe (output)",
                        "en": u"what the model\nwrites (output)"},
    "reparto.x":       {"es": u"tokens (%s en total)",
                        "en": u"tokens (%s in total)"},
    "reparto.titulo":  {"es": u"El trabajo no es lo que se paga",
                        "en": u"The work is not what you pay for"},
    "deriva.arranque": {"es": u"contexto al arrancar",
                        "en": u"context at the start"},
    "deriva.x":        {"es": u"avance de la sesión (décimos de los turnos)",
                        "en": u"progress through the session (tenths of the turns)"},
    "deriva.y":        {"es": u"contexto leído en cada turno",
                        "en": u"context read on each turn"},
    "deriva.titulo":   {"es": u"El mismo trabajo, más caro cada vez  (%d sesiones de %d+ turnos)",
                        "en": u"The same work, more expensive every time  (%d sessions of %d+ turns)"},
    "arrastre.x":      {"es": u"arrastre: tokens del resultado × turnos que vinieron después",
                        "en": u"carry: result tokens × turns that came after"},
    "arrastre.titulo": {"es": u"Qué es lo que se relee tantas veces",
                        "en": u"What it is that gets re-read so many times"},
    "fam.archivos":    {"es": u"leer archivos enteros",
                        "en": u"reading whole files"},
    "fam.shell":       {"es": u"comandos de shell",
                        "en": u"shell commands"},
    "fam.correo":      {"es": u"conectores (correo, documentos)",
                        "en": u"connectors (mail, documents)"},
    "fam.web":         {"es": u"web y otros conectores",
                        "en": u"web and other connectors"},
    "fam.busqueda":    {"es": u"búsqueda filtrada",
                        "en": u"filtered search"},
    "fam.resto":       {"es": u"resto",
                        "en": u"the rest"},
}


def t(clave):
    return TEXTOS[clave][IDIOMA]


def _sesiones(dias, hasta=None):
    """Consumo por sesion, filtrado por fecha de inicio.

    `hasta` es exclusivo. Sirve para congelar la ventana de una medicion
    publicada: sin el, la sesion que escribe el articulo se cuenta a si misma
    y las cifras se mueven en cada corrida.
    """
    corte = (hasta or dt.date.today()) - dt.timedelta(days=dias)
    salida = []
    for ruta in glob.glob(os.path.join(RAIZ, "*", "*.jsonl")):
        try:
            s = medir_consumo.medir_sesion(ruta)
        except Exception:
            continue
        if not s:
            continue
        f = medir_tool_results._fecha(s["inicio"])
        if f is None or f < corte or (hasta and f >= hasta):
            continue
        salida.append(s)
    return salida


def _ajuste_potencia(xs, ys):
    """log y = a + b log x por minimos cuadrados. Devuelve (b, a, r2)."""
    lx = [math.log(x) for x in xs]
    ly = [math.log(y) for y in ys]
    n = float(len(lx))
    mx = sum(lx) / n
    my = sum(ly) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(lx, ly))
    sxx = sum((x - mx) ** 2 for x in lx)
    b = sxy / sxx
    a = my - b * mx
    syy = sum((y - my) ** 2 for y in ly)
    r2 = (sxy ** 2) / (sxx * syy) if syy else 0.0
    return b, a, r2


def _millones(v, _pos=None):
    if v >= 1e6:
        return "%dM" % (v / 1e6)
    if v >= 1e3:
        return "%dk" % (v / 1e3)
    return "%d" % v


def grafico_curva(sesiones, destino):
    """El grafico que sostiene el argumento: el costo no es lineal."""
    puntos = [(s["llamadas"], s["total"]) for s in sesiones
              if s["llamadas"] > 0 and s["total"] > 0]
    xs = [p[0] for p in puntos]
    ys = [p[1] for p in puntos]
    b, a, r2 = _ajuste_potencia(xs, ys)

    fig, ax = plt.subplots(figsize=(8, 5.2))
    ax.scatter(xs, ys, s=26, color=TINTA, alpha=0.45, edgecolors="none",
               zorder=3)

    rango = [min(xs), max(xs)]
    finos = [rango[0] * (rango[1] / float(rango[0])) ** (i / 100.0)
             for i in range(101)]
    ax.plot(finos, [math.exp(a) * x ** b for x in finos], color=ACENTO, lw=2.2,
            zorder=4, label=t("curva.ajuste") % (b, r2))
    # Referencia lineal anclada en la sesion mas corta: como se veria si cada
    # turno costara lo mismo que el primero.
    k = ys[xs.index(min(xs))] / float(min(xs))
    ax.plot(finos, [k * x for x in finos], color=GRIS, lw=1.6, ls="--",
            zorder=2, label=t("curva.lineal"))

    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel(t("curva.x"))
    ax.set_ylabel(t("curva.y"))
    ax.yaxis.set_major_formatter(FuncFormatter(_millones))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, p: "%d" % v))
    ax.set_title(t("curva.titulo"),
                 fontsize=14, loc="left", pad=14)
    ax.legend(frameon=False, loc="upper left", fontsize=10)
    ax.grid(True, which="major", color=GRIS, alpha=0.18)
    fig.tight_layout()
    fig.savefig(destino, dpi=170)
    plt.close(fig)
    return b, r2, len(puntos)


def grafico_reparto(sesiones, destino):
    """En que se va el gasto: casi todo es releer lo ya dicho."""
    total = sum(s["total"] for s in sesiones)
    cache = sum(s["cache_read"] for s in sesiones)
    out = sum(s["output"] for s in sesiones)
    resto = total - cache - out

    etiquetas = [t("reparto.cache"), t("reparto.nuevo"), t("reparto.output")]
    valores = [cache, resto, out]
    colores = [ACENTO, GRIS, TINTA]

    fig, ax = plt.subplots(figsize=(8, 3.4))
    izq = 0
    for etq, val, col in zip(etiquetas, valores, colores):
        ax.barh([0], [val], left=[izq], color=col, height=0.55)
        pct = 100.0 * val / total
        if pct > 10:
            ax.text(izq + val / 2.0, 0, u"%.0f %%" % pct, ha="center",
                    va="center", color="white", fontsize=13, fontweight="bold")
        izq += val

    ax.set_yticks([])
    ax.set_xlim(0, total)
    ax.set_xlabel(t("reparto.x") % _millones(total))
    ax.xaxis.set_major_formatter(FuncFormatter(_millones))
    ax.set_title(t("reparto.titulo"), fontsize=14, loc="left",
                 pad=14)
    manijas = [plt.Rectangle((0, 0), 1, 1, color=c) for c in colores]
    rotulos = [u"%s - %.1f %%" % (e.replace("\n", " "), 100.0 * v / total)
               for e, v in zip(etiquetas, valores)]
    ax.legend(manijas, rotulos,
              frameon=False, fontsize=10, loc="upper center",
              bbox_to_anchor=(0.5, -0.35), ncol=1)
    fig.tight_layout()
    fig.savefig(destino, dpi=170)
    plt.close(fig)
    return total, cache, out


def grafico_deriva(sesiones, destino, minimo=120, tramos=10):
    """El mecanismo: dentro de una misma sesion, cada turno cuesta mas.

    Se normaliza el avance de la sesion en tramos (deciles por defecto) para
    poder promediar sesiones de largos distintos: el tramo 1 es el primer 10 %
    de los turnos, el 10 el ultimo.
    """
    largas = [s["contextos"] for s in sesiones if s["llamadas"] >= minimo]
    if not largas:
        return None
    medias = []
    for tramo in range(tramos):
        vals = []
        for c in largas:
            n = len(c)
            seg = c[int(n * tramo / float(tramos)):
                    int(n * (tramo + 1) / float(tramos))]
            if seg:
                vals.append(sum(seg) / float(len(seg)))
        medias.append(sum(vals) / float(len(vals)))

    fig, ax = plt.subplots(figsize=(8, 4.6))
    xs = range(1, tramos + 1)
    ax.plot(xs, medias, color=ACENTO, lw=2.4, marker="o", ms=6, zorder=3)
    ax.fill_between(xs, [medias[0]] * tramos, medias, color=ACENTO, alpha=0.10)
    ax.axhline(medias[0], color=GRIS, ls="--", lw=1.4)
    ax.text(1.15, medias[0] * 1.03, t("deriva.arranque"), va="bottom",
            ha="left", color=GRIS, fontsize=10)
    ax.annotate(u"×%.1f" % (medias[-1] / medias[0]),
                xy=(tramos, medias[-1]), xytext=(-6, 10),
                textcoords="offset points", ha="right", fontsize=13,
                fontweight="bold", color=ACENTO)
    ax.set_xticks(list(xs))
    ax.set_xlabel(t("deriva.x"))
    ax.set_ylabel(t("deriva.y"))
    ax.set_ylim(0, max(medias) * 1.15)
    ax.yaxis.set_major_formatter(FuncFormatter(_millones))
    ax.set_title(t("deriva.titulo")
                 % (len(largas), minimo), fontsize=14, loc="left", pad=14)
    ax.grid(True, axis="y", color=GRIS, alpha=0.18)
    fig.tight_layout()
    fig.savefig(destino, dpi=170)
    plt.close(fig)
    return medias, len(largas)


def _familia(nombre):
    """Agrupa las herramientas en las categorias que le importan al lector."""
    if nombre in ("Read", "NotebookRead"):
        return t("fam.archivos")
    if nombre in ("Bash", "PowerShell"):
        return t("fam.shell")
    if nombre.startswith("mcp__") and ("outlook" in nombre or "sharepoint" in nombre
                                       or "email" in nombre or "teams" in nombre
                                       or "gmail" in nombre):
        return t("fam.correo")
    if nombre.startswith("mcp__") or nombre in ("WebFetch", "WebSearch"):
        return t("fam.web")
    if nombre in ("Grep", "Glob"):
        return t("fam.busqueda")
    return t("fam.resto")


def datos_arrastre(dias, hasta=None):
    """Arrastre agregado por familia de herramienta, y el detalle de correo."""
    corte = (hasta or dt.date.today()) - dt.timedelta(days=dias)
    por_familia = {}
    total = 0
    correo_conector = []   # tokens por llamada al conector de correo
    for ruta in glob.glob(os.path.join(RAIZ, "*", "*.jsonl")):
        try:
            filas, _turnos, inicio, _fin = medir_tool_results.medir_sesion(ruta)
        except Exception:
            continue
        f = medir_tool_results._fecha(inicio)
        if f is None or f < corte or (hasta and f >= hasta):
            continue
        for r in filas:
            fam = _familia(r["herramienta"])
            por_familia[fam] = por_familia.get(fam, 0) + r["arrastre"]
            total += r["arrastre"]
            if "email_search" in r["herramienta"] or "chat_message_search" in r["herramienta"]:
                correo_conector.append(r["tokens"])
    return por_familia, total, correo_conector


def grafico_arrastre(por_familia, total, destino):
    filas = sorted(por_familia.items(), key=lambda kv: kv[1])
    etiquetas = [k for k, _ in filas]
    valores = [v for _, v in filas]

    fig, ax = plt.subplots(figsize=(8, 4.4))
    colores = [ACENTO if e == t("fam.archivos") else GRIS
               for e in etiquetas]
    ax.barh(etiquetas, valores, color=colores, height=0.62)
    for etq, val in zip(etiquetas, valores):
        ax.text(val + total * 0.012, etq, u"%.0f %%" % (100.0 * val / total),
                va="center", fontsize=11)
    ax.set_xlim(0, max(valores) * 1.18)
    ax.set_xlabel(t("arrastre.x"))
    ax.xaxis.set_major_formatter(FuncFormatter(_millones))
    ax.set_title(t("arrastre.titulo"), fontsize=14,
                 loc="left", pad=14)
    ax.grid(True, axis="x", color=GRIS, alpha=0.18)
    fig.tight_layout()
    fig.savefig(destino, dpi=170)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dias", type=int, default=120)
    p.add_argument("--salida", default=os.path.join("articulo", "img"))
    p.add_argument("--idioma", choices=("es", "en"), default="es",
                   help="idioma de los rotulos de las figuras")
    p.add_argument("--hasta", default=None, metavar="AAAA-MM-DD",
                   help="fecha de corte EXCLUSIVA; congela la ventana para "
                        "que una medicion publicada sea reproducible")
    args = p.parse_args()

    global IDIOMA
    IDIOMA = args.idioma

    if not os.path.isdir(args.salida):
        os.makedirs(args.salida)

    hasta = None
    if args.hasta:
        hasta = dt.datetime.strptime(args.hasta, "%Y-%m-%d").date()

    sesiones = _sesiones(args.dias, hasta)
    if not sesiones:
        print("Sin sesiones en la ventana pedida.")
        return 1

    b, r2, n = grafico_curva(sesiones, os.path.join(args.salida, "curva.png"))
    total, cache, out = grafico_reparto(
        sesiones, os.path.join(args.salida, "reparto.png"))
    deriva = grafico_deriva(sesiones, os.path.join(args.salida, "deriva.png"))
    por_familia, arrastre, correo = datos_arrastre(args.dias, hasta)
    grafico_arrastre(por_familia, arrastre,
                     os.path.join(args.salida, "arrastre.png"))

    llamadas = sum(s["llamadas"] for s in sesiones)
    cortas = [s for s in sesiones if s["llamadas"] <= 30]
    ordenadas = sorted(sesiones, key=lambda s: -s["total"])
    top20 = sum(s["total"] for s in ordenadas[:20])

    print("=" * 66)
    print(u"CIFRAS DEL ARTÍCULO  (%d días hasta %s)"
          % (args.dias, (hasta or dt.date.today()).isoformat()))
    print("=" * 66)
    print(u"  sesiones ................ %d" % len(sesiones))
    print(u"  llamadas al modelo ...... %s" % medir_tool_results._miles(llamadas))
    print(u"  tokens totales .......... %.1f M" % (total / 1e6))
    print(u"  cache_read .............. %.1f M  (%.0f %%)"
          % (cache / 1e6, 100.0 * cache / total))
    print(u"  output .................. %.1f M  (%.1f %%)"
          % (out / 1e6, 100.0 * out / total))
    print(u"  exponente del ajuste .... %.2f  (R2 %.2f, n=%d)" % (b, r2, n))
    print(u"  top 20 sesiones ......... %.0f %% del gasto" % (100.0 * top20 / total))
    print(u"  sesiones de <=30 turnos . %d (%.0f %% de las sesiones) = %.0f %% del gasto"
          % (len(cortas), 100.0 * len(cortas) / len(sesiones),
             100.0 * sum(s["total"] for s in cortas) / total))
    if deriva:
        medias, n_largas = deriva
        print(u"  contexto por turno en sesiones largas (%d de 120+ turnos):"
              % n_largas)
        print(u"      primer décimo %.0fk -> último décimo %.0fk  (x%.1f)"
              % (medias[0] / 1e3, medias[-1] / 1e3, medias[-1] / medias[0]))
    print(u"  arrastre total .......... %.0f M" % (arrastre / 1e6))
    for fam, val in sorted(por_familia.items(), key=lambda kv: -kv[1]):
        print(u"      %-34s %5.1f %%" % (fam, 100.0 * val / arrastre))
    if correo:
        prom = sum(correo) / float(len(correo))
        print(u"  conector de correo: %d llamadas, %.0f tokens de promedio"
              % (len(correo), prom))
    print(u"\n  figuras en %s" % os.path.abspath(args.salida))
    return 0


if __name__ == "__main__":
    sys.exit(main())
