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
    python medir_tool_results.py --desde "2026-09-10 22:25"
    python medir_tool_results.py --desde "2026-09-13 13:54" --hasta "2026-09-16 19:47"
    python medir_tool_results.py --sesion <archivo.jsonl>

`--dias` toma las sesiones que empezaron en la ventana, enteras. `--desde` (hora local)
corta por tool_result: de una sesion que cruza el cambio cuenta solo los posteriores, y
su arrastre sigue contando los turnos que vinieron despues, que ya son todos del regimen.
`--hasta` cierra la ventana por arriba y con el mismo criterio: deja fuera los
tool_result posteriores y tambien los turnos posteriores, asi que el arrastre que se
informa es el que ocurrio dentro de la ventana, no el que siguio corriendo despues.
Un arrastre cortado por arriba es un piso del costo real de esa llamada.
"""

import argparse
import codecs
import csv
import datetime as dt
import glob
import io
import json
import os
import re
import sys

RAIZ = os.path.join(os.path.expanduser("~"), ".claude", "projects")

# Caracteres por token: de calibracion.py, la unica fuente. Hasta el 25-sep-2026 aca era
# 4,0 y los tokens de cada resultado quedaban bajos cerca de 1,9 veces.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from calibracion import CHARS_POR_TOKEN  # noqa: E402

# Filas de la tabla por herramienta antes de agrupar el resto.
TOP_HERRAMIENTAS = 8

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


def _cerrar(tramo, turnos):
    """Fija el arrastre de los resultados de un tramo con los turnos al cerrarlo."""
    for r in tramo:
        posteriores = max(0, turnos - r.pop("_turno"))
        r["turnos_despues"] = posteriores
        r["arrastre"] = r["tokens"] * posteriores
    del tramo[:]


def medir_sesion(ruta, desde=None, hasta=None):
    """Lista de dicts, uno por tool_result, con su arrastre ya calculado. Con `desde`
    (datetime con zona) se omiten los tool_result anteriores; los turnos se cuentan
    igual, porque de ellos depende el arrastre de los que quedan. Con `hasta` se omiten
    los tool_result posteriores y tambien dejan de contarse los turnos posteriores: el
    arrastre informado es el que ocurrio dentro de la ventana."""
    if desde is not None or hasta is not None:
        from regimen import marca as _marca
    usos = {}          # tool_use_id -> (nombre, breve)
    resultados = []    # todos los tool_result de la sesion
    tramo = []         # los de este tramo entre compactaciones, aun sin arrastre
    turnos = 0         # llamadas al modelo vistas hasta ahora
    vistas = set()     # requestId (o message.id) ya contados
    inicio = None
    fin = None

    with io.open(ruta, "r", encoding="utf-8", errors="ignore") as fh:
        for linea in fh:
            if ('"tool_' not in linea and '"usage"' not in linea
                    and "compact_boundary" not in linea):
                continue
            try:
                d = json.loads(linea)
            except Exception:
                continue
            # Al compactar, el resumen reemplaza el contexto: lo que venia de antes
            # deja de releerse ahi. Sin este corte, un volcado del turno 3 de una
            # sesion compactada en el 40 cargaba el arrastre de los 200 turnos.
            if d.get("type") == "system" and d.get("subtype") == "compact_boundary":
                _cerrar(tramo, turnos)
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                continue

            marca = d.get("timestamp")
            if marca:
                if inicio is None:
                    inicio = marca
                fin = marca
            # Pasado el tope no se cuenta nada mas: ni turnos ni resultados. Se sigue
            # leyendo el archivo porque los tool_use de mas arriba ya estan en `usos`.
            if hasta is not None and not ((_marca(marca) or hasta) <= hasta):
                continue

            # Una llamada ocupa una línea por bloque de contenido, todas con el
            # mismo `usage`: se cuenta una vez por requestId (o message.id).
            if isinstance(msg.get("usage"), dict):
                clave = d.get("requestId") or msg.get("id")
                if not clave or clave not in vistas:
                    turnos += 1
                    if clave:
                        vistas.add(clave)

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
                    if desde is not None and not ((_marca(marca) or desde) > desde):
                        continue
                    nombre, breve = usos.get(bloque.get("tool_use_id"),
                                             ("?", ""))
                    chars = _texto_resultado(bloque.get("content"))
                    fila = {
                        "archivo": os.path.basename(ruta),
                        "proyecto": os.path.basename(os.path.dirname(ruta)),
                        "fecha": (marca or inicio or "")[:16],
                        "herramienta": nombre,
                        "llamada": breve,
                        "tokens": int(chars / CHARS_POR_TOKEN),
                        "_turno": turnos,
                    }
                    resultados.append(fila)
                    tramo.append(fila)

    _cerrar(tramo, turnos)
    return resultados, turnos, (inicio or "")[:16], (fin or "")[:16]


def _tabla(filas, columnas, anchos, salida):
    salida.write("  ".join(c.ljust(a) for c, a in zip(columnas, anchos)) + "\n")
    salida.write("  ".join("-" * a for a in anchos) + "\n")
    for fila in filas:
        salida.write("  ".join(str(v).ljust(a)[:a]
                               for v, a in zip(fila, anchos)) + "\n")


def _miles(n):
    return "{:,}".format(int(n)).replace(",", ".")


# Los conectores llegan como mcp__<uuid>__<herramienta>: cortando el nombre crudo en
# 34 caracteres quedaban todos iguales, "mcp__7b311208-cdc6-40d6-aaf1-bb29".
MCP_UUID = re.compile(r"^mcp__[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}__")


def _corto(nombre):
    return MCP_UUID.sub("", nombre or "?")


def _ambito(args):
    """La ventana pedida, para la primera linea del resumen."""
    from regimen import local
    if args.desde and args.hasta:
        return " | de %s a %s" % (local(args.desde), local(args.hasta))
    if args.desde:
        return " | desde %s" % local(args.desde)
    if args.hasta:
        return " | hasta %s" % local(args.hasta)
    return ""


def main():
    from regimen import avisar, leer_desde, local
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ventana = p.add_mutually_exclusive_group()
    ventana.add_argument("--dias", type=int, default=30,
                         help="ventana hacia atras (por defecto 30)")
    ventana.add_argument("--desde", type=leer_desde, metavar="'AAAA-MM-DD HH:MM'",
                         help="solo los tool_result posteriores a ese momento (hora local)")
    p.add_argument("--hasta", type=leer_desde, metavar="'AAAA-MM-DD HH:MM'",
                   help="cierra la ventana por arriba, con el mismo corte por tool_result")
    p.add_argument("--top", type=int, default=6,
                   help="cuantas llamadas individuales listar")
    p.add_argument("--csv", help="ruta para el detalle completo, un tool_result por fila")
    p.add_argument("--sesion", help="medir un solo transcript (ruta o nombre de archivo)")
    p.add_argument("--raiz", default=RAIZ)
    args = p.parse_args()
    if args.hasta and args.desde and args.hasta <= args.desde:
        p.error("--hasta tiene que ser posterior a --desde")

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
            filas, turnos, ini, _fin = medir_sesion(ruta, args.desde, args.hasta)
        except Exception as e:
            print("  (se omite %s: %s)" % (os.path.basename(ruta), e))
            continue
        if not filas:
            continue
        if not args.sesion and not args.desde and not args.hasta:
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

    avisar(dias=None if (args.desde or args.hasta) else (args.dias or None),
           desde=args.desde, hasta=args.hasta)

    # Salida compacta a proposito (unas 28 lineas): se lee desde el chat y cada
    # linea se relee en los turnos que siguen. El detalle completo va al --csv.
    print("Sesiones: %d | tool_result: %s | tokens entregados: %s | arrastre: %s%s"
          % (sesiones, _miles(len(todo)), _miles(total_tokens), _miles(total_arrastre),
             _ambito(args)))
    print("(arrastre = tokens x turnos posteriores, reiniciado en cada compactacion)")
    print("")

    # Por herramienta: las principales y el resto en una fila.
    def _vacio():
        return {"n": 0, "tokens": 0, "arrastre": 0, "max": 0}

    def _sumar(d, n, tokens, arrastre, peor):
        d["n"] += n
        d["tokens"] += tokens
        d["arrastre"] += arrastre
        d["max"] = max(d["max"], peor)

    por = {}
    for r in todo:
        _sumar(por.setdefault(_corto(r["herramienta"]), _vacio()),
               1, r["tokens"], r["arrastre"], r["tokens"])

    filas = sorted(por.items(), key=lambda kv: -kv[1]["arrastre"])
    if len(filas) > TOP_HERRAMIENTAS + 1:
        resto = _vacio()
        for _k, v in filas[TOP_HERRAMIENTAS:]:
            _sumar(resto, v["n"], v["tokens"], v["arrastre"], v["max"])
        filas = filas[:TOP_HERRAMIENTAS] + [
            ("resto (%d herramientas)" % (len(filas) - TOP_HERRAMIENTAS), resto)]
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
    _tabla([(r["fecha"][:10], _corto(r["herramienta"])[:22], _miles(r["tokens"]),
             r["turnos_despues"], _miles(r["arrastre"]), r["llamada"][:60])
            for r in peores],
           ["fecha", "herramienta", "tokens", "turnos", "arrastre", "llamada"],
           [10, 22, 8, 6, 11, 60], sys.stdout)

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
