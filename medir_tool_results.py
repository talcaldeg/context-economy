# -*- coding: utf-8 -*-
"""Mide el peso de los tool_result en los transcripts de Claude Code.

`medir_consumo.py` responde "cuanto se gasto y en que sesion". Este responde la
pregunta siguiente: *que llamada concreta metio el bulto al contexto*.

Un tool_result no se paga una vez. Queda en el contexto y se relee en cada turno
posterior de la sesion, asi que su costo real es:

    tokens_del_resultado  x  turnos_que_vinieron_despues

Eso es lo que aqui se llama "arrastre", y es la columna que hay que mirar: un
volcado de 20k tokens en el turno 3 de una sesion de 80 turnos cuesta mas que uno
de 100k en el ultimo turno.

Solo lee los .jsonl de ~/.claude/projects. No modifica nada.

    python medir_tool_results.py --dias 30
    python medir_tool_results.py --dias 30 --top 25
    python medir_tool_results.py --dias 30 --csv detalle.csv
    python medir_tool_results.py --sesion <archivo.jsonl>
"""

import argparse
import codecs
import csv
import datetime as dt
import glob
import io
import json
import os
import sys

RAIZ = os.path.join(os.path.expanduser("~"), ".claude", "projects")

# 1 token ~ 4 caracteres. Sirve para ordenar, no para facturar.
CHARS_POR_TOKEN = 4.0

# Campos de input que identifican la llamada sin volcar el input entero.
CLAVES_BREVE = ("command", "file_path", "path", "pattern", "query", "url",
                "prompt", "id_mensaje", "notebook_path")


def _texto_resultado(contenido):
    """Largo en caracteres de un bloque tool_result, venga como venga."""
    if contenido is None:
        return 0
    if isinstance(contenido, str):
        return len(contenido)
    if isinstance(contenido, dict):
        return _texto_resultado(contenido.get("text") or contenido.get("content"))
    if isinstance(contenido, list):
        return sum(_texto_resultado(x) for x in contenido)
    return 0


def _breve(entrada):
    """Una linea que identifique la llamada, recortada."""
    if not isinstance(entrada, dict):
        return ""
    for clave in CLAVES_BREVE:
        valor = entrada.get(clave)
        if isinstance(valor, str) and valor.strip():
            valor = " ".join(valor.split())
            if len(valor) <= 110:
                return valor
            # En rutas lo que identifica es el final, no la unidad: se recorta
            # por delante. Costo una tarde de analisis equivocado descubrirlo.
            if clave in ("file_path", "path", "notebook_path"):
                return "..." + valor[-107:]
            return valor[:110]
    claves = ",".join(sorted(entrada.keys()))
    return claves[:110]


def _fecha(marca):
    if not marca:
        return None
    try:
        return dt.datetime.strptime(marca[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def medir_sesion(ruta):
    """Lista de dicts, uno por tool_result, con su arrastre ya calculado."""
    usos = {}          # tool_use_id -> (nombre, breve)
    resultados = []    # se completa el arrastre al final
    turnos = 0         # llamadas al modelo vistas hasta ahora
    inicio = None
    fin = None

    with io.open(ruta, "r", encoding="utf-8", errors="ignore") as fh:
        for linea in fh:
            if '"tool_' not in linea and '"usage"' not in linea:
                continue
            try:
                d = json.loads(linea)
            except Exception:
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                continue

            marca = d.get("timestamp")
            if marca:
                if inicio is None:
                    inicio = marca
                fin = marca

            if isinstance(msg.get("usage"), dict):
                turnos += 1

            contenido = msg.get("content")
            if not isinstance(contenido, list):
                continue

            for bloque in contenido:
                if not isinstance(bloque, dict):
                    continue
                tipo = bloque.get("type")
                if tipo == "tool_use":
                    usos[bloque.get("id")] = (bloque.get("name") or "?",
                                              _breve(bloque.get("input")))
                elif tipo == "tool_result":
                    nombre, breve = usos.get(bloque.get("tool_use_id"),
                                             ("?", ""))
                    chars = _texto_resultado(bloque.get("content"))
                    resultados.append({
                        "archivo": os.path.basename(ruta),
                        "proyecto": os.path.basename(os.path.dirname(ruta)),
                        "fecha": (marca or inicio or "")[:16],
                        "herramienta": nombre,
                        "llamada": breve,
                        "tokens": int(chars / CHARS_POR_TOKEN),
                        "_turno": turnos,
                    })

    for r in resultados:
        posteriores = max(0, turnos - r.pop("_turno"))
        r["turnos_despues"] = posteriores
        r["arrastre"] = r["tokens"] * posteriores

    return resultados, turnos, (inicio or "")[:16], (fin or "")[:16]


def _tabla(filas, columnas, anchos, salida):
    salida.write("  ".join(c.ljust(a) for c, a in zip(columnas, anchos)) + "\n")
    salida.write("  ".join("-" * a for a in anchos) + "\n")
    for fila in filas:
        salida.write("  ".join(str(v).ljust(a)[:a]
                               for v, a in zip(fila, anchos)) + "\n")


def _miles(n):
    return "{:,}".format(int(n)).replace(",", ".")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dias", type=int, default=30,
                   help="ventana hacia atras (por defecto 30)")
    p.add_argument("--top", type=int, default=15,
                   help="cuantas llamadas individuales listar")
    p.add_argument("--csv", help="ruta para el detalle completo, un tool_result por fila")
    p.add_argument("--sesion", help="medir un solo transcript (ruta o nombre de archivo)")
    p.add_argument("--raiz", default=RAIZ)
    args = p.parse_args()

    if hasattr(sys.stdout, "buffer"):
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "replace")

    if args.sesion:
        rutas = [args.sesion] if os.path.exists(args.sesion) else \
            glob.glob(os.path.join(args.raiz, "*", args.sesion))
    else:
        rutas = glob.glob(os.path.join(args.raiz, "*", "*.jsonl"))
    if not rutas:
        print("No se encontraron transcripts en %s" % args.raiz)
        return 1

    corte = dt.date.today() - dt.timedelta(days=args.dias)
    todo = []
    sesiones = 0
    for ruta in rutas:
        try:
            filas, turnos, ini, _fin = medir_sesion(ruta)
        except Exception as e:
            print("  (se omite %s: %s)" % (os.path.basename(ruta), e))
            continue
        if not filas:
            continue
        if not args.sesion:
            f = _fecha(ini)
            if f is None or f < corte:
                continue
        sesiones += 1
        todo.extend(filas)

    if not todo:
        print("Sin tool_result en la ventana pedida.")
        return 0

    total_tokens = sum(r["tokens"] for r in todo)
    total_arrastre = sum(r["arrastre"] for r in todo)

    print("")
    print("Sesiones analizadas: %d    tool_result: %s" % (sesiones, _miles(len(todo))))
    print("Tokens entregados por herramientas: %s" % _miles(total_tokens))
    print("Arrastre (esos tokens x turnos posteriores): %s" % _miles(total_arrastre))
    print("")

    # Por herramienta.
    por = {}
    for r in todo:
        d = por.setdefault(r["herramienta"], {"n": 0, "tokens": 0, "arrastre": 0,
                                              "max": 0})
        d["n"] += 1
        d["tokens"] += r["tokens"]
        d["arrastre"] += r["arrastre"]
        d["max"] = max(d["max"], r["tokens"])

    filas = sorted(por.items(), key=lambda kv: -kv[1]["arrastre"])
    print("=== Donde se genera el bulto, por herramienta ===")
    _tabla([(k[:34], _miles(v["n"]), _miles(v["tokens"]),
             _miles(v["tokens"] // max(1, v["n"])), _miles(v["max"]),
             _miles(v["arrastre"]),
             "%.0f%%" % (100.0 * v["arrastre"] / max(1, total_arrastre)))
            for k, v in filas],
           ["herramienta", "llamadas", "tokens", "prom", "peor", "arrastre", "%"],
           [34, 8, 10, 8, 9, 12, 5], sys.stdout)
    print("")

    # Llamadas individuales.
    peores = sorted(todo, key=lambda r: -r["arrastre"])[:args.top]
    print("=== Las %d llamadas que mas contexto arrastraron ===" % len(peores))
    _tabla([(r["fecha"][:10], r["herramienta"][:22], _miles(r["tokens"]),
             r["turnos_despues"], _miles(r["arrastre"]), r["llamada"][:60])
            for r in peores],
           ["fecha", "herramienta", "tokens", "turnos", "arrastre", "llamada"],
           [10, 22, 8, 6, 11, 60], sys.stdout)
    print("")

    if args.csv:
        with io.open(args.csv, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["archivo", "proyecto", "fecha",
                                               "herramienta", "llamada", "tokens",
                                               "turnos_despues", "arrastre"])
            w.writeheader()
            for r in sorted(todo, key=lambda r: -r["arrastre"]):
                w.writerow(r)
        print("Detalle completo en %s (%s filas)" % (args.csv, _miles(len(todo))))

    return 0


if __name__ == "__main__":
    sys.exit(main())
