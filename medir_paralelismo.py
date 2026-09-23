# -*- coding: utf-8 -*-
"""Techo del paralelismo: cuantas llamadas sobran porque dos lecturas independientes
viajaron por separado, y cuanto costaria un aviso que lo corrija.

Una llamada a la API relee el contexto entero. Si la llamada B trae una sola lectura,
viene justo despues de otra llamada A que tambien traia una sola lectura, no hubo
prompt de por medio y el input de B no usa nada que recien aparecio en el resultado
de A, entonces B pudo ir dentro de A. Esa es una llamada que sobro.

El techo es la suma de esas llamadas sobrantes, contando rachas: A, B y C
independientes entre si colapsan a una sola llamada y ahorran dos. Es el maximo que
rinde cualquier mecanismo aunque funcione perfecto, porque supone que el modelo obedece
siempre.

Independencia (heuristica, sesgada a declarar independiente, que es lo correcto para
un techo): B depende de A si
  - el input de B contiene un termino (4+ caracteres, o un numero de 3+ cifras) que
    aparece en el resultado de A y no aparecia antes en la sesion;
  - A fallo (is_error) o su resultado parece un error: B suele ser el reintento;
  - B lee un archivo que A acababa de buscar (grep -> Read con offset).
La muestra que deja `--muestra` sirve para revisar a mano cuanto se equivoca.

Costo del aviso: un texto de `--aviso` tokens inyectado por un hook PostToolUse tras
cada llamada de lectura suelta. Queda en el contexto y se relee en todas las llamadas
siguientes del tramo, igual que un tool_result.

    python medir_paralelismo.py --desde "2026-09-14 00:00"
    python medir_paralelismo.py --desde "2026-09-21 11:44" --aviso 40
    python medir_paralelismo.py --dias 14 --muestra pares.csv

Solo lee los .jsonl de ~/.claude/projects. No modifica nada.
"""

import argparse
import csv
import datetime as dt
import glob
import io
import json
import os
import random
import re
import sys

from medir_consumo import PRECIO, RAIZ
from regimen import leer_desde, marca

TERMINO = re.compile(r"[A-Za-z0-9_.\-]{4,}|\d{2,}")

LECTURA_DIRECTA = {"Read", "Grep", "Glob", "WebFetch", "WebSearch"}

PARECE_ERROR = re.compile(r"^(error|traceback|exit code [1-9])|no such file|"
                          r"not found|cannot find|is not recognized|"
                          r"InputValidationError", re.I | re.M)


def _texto(contenido):
    if contenido is None:
        return ""
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, dict):
        return _texto(contenido.get("text") or contenido.get("content"))
    if isinstance(contenido, list):
        return "\n".join(_texto(x) for x in contenido)
    return ""


def _terminos(texto):
    return set(t.lower().strip(".-") for t in TERMINO.findall(texto or ""))


def _shell_lectura(comando):
    """True si el comando no muestra ninguna senal de escritura. Es lista negra y no
    blanca a proposito: los comandos reales empiezan con rotulos ("=== X"), variables
    y scripts de python que solo consultan, y una lista blanca los dejaba fuera. Para
    un techo, equivocarse hacia "lectura" es lo que corresponde."""
    return not ESCRIBE.search(comando)


ESCRIBE = re.compile(
    r"(?<![0-9&])>(?![&=])|\btee\b|Out-File|Set-Content|Add-Content|Export-Csv|"
    r"Remove-Item|New-Item|Copy-Item|Move-Item|Rename-Item|Start-Process|"
    r"Stop-Process|Register-ScheduledTask|Set-ItemProperty|\brm\b|\bmv\b|\bcp\b|"
    r"\bmkdir\b|sed\s+-i|\bgit\s+(add|commit|push|pull|checkout|reset|merge|rebase|"
    r"stash|tag)\b|\bpip\s+install|\bnpm\s+(install|run)|"
    r"\bgen_\w+\.py|generar\w*\.py|\.save\(|\.to_excel|\.to_csv|"
    r"open\([^)]*['\"][wa]b?['\"]|\bwrite_text\b|\bcurl\b.*-X\s*POST|-OutFile|"
    r"\bgh\s+(secret|workflow\s+run|pr\s+(create|merge)|release)|\bgh\s+api\b.*-X|"
    r"\bsleep\b|Start-Sleep",
    re.I)


def es_lectura(nombre, entrada):
    if nombre in LECTURA_DIRECTA:
        return True
    if nombre in ("Bash", "PowerShell") and isinstance(entrada, dict):
        return _shell_lectura(entrada.get("command") or "")
    return False


def _archivo(entrada):
    if not isinstance(entrada, dict):
        return ""
    return (entrada.get("file_path") or entrada.get("path") or "").lower()


def _es_humano(d, msg):
    if d.get("isMeta") or d.get("isCompactSummary"):
        return False
    c = msg.get("content")
    if isinstance(c, str):
        return not c.lstrip().startswith("<")
    if isinstance(c, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return False
        return any(isinstance(b, dict) and b.get("type") in ("text", "image") and
                   not _texto(b).lstrip().startswith("<") for b in c)
    return False


def leer_sesion(ruta):
    """Lista de eventos en orden: ('llamada', dict) | ('humano',) | ('corte',)."""
    eventos = []
    llamadas = {}
    resultados = {}
    with io.open(ruta, "r", encoding="utf-8", errors="ignore") as fh:
        for linea in fh:
            try:
                d = json.loads(linea)
            except Exception:
                continue
            if d.get("type") == "system" and d.get("subtype") == "compact_boundary":
                eventos.append(("corte",))
                continue
            msg = d.get("message")
            if not isinstance(msg, dict):
                continue
            if d.get("type") == "assistant" and isinstance(msg.get("usage"), dict):
                clave = d.get("requestId") or msg.get("id")
                ll = llamadas.get(clave)
                if ll is None:
                    ll = {"clave": clave, "ts": d.get("timestamp"), "usos": [],
                          "texto": ""}
                    llamadas[clave] = ll
                    eventos.append(("llamada", ll))
                ll["usage"] = msg["usage"]
                for b in msg.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use":
                        ll["usos"].append((b.get("id"), b.get("name") or "?",
                                           b.get("input")))
                    elif b.get("type") == "text":
                        ll["texto"] += b.get("text") or ""
            elif d.get("type") == "user":
                c = msg.get("content")
                if _es_humano(d, msg):
                    eventos.append(("humano", _texto(c)))
                elif isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            resultados[b.get("tool_use_id")] = (
                                _texto(b.get("content")), bool(b.get("is_error")))
                        else:
                            eventos.append(("contexto", _texto(b)))
                else:
                    eventos.append(("contexto", _texto(c)))
    for ev in eventos:
        if ev[0] == "llamada":
            ev[1]["resultados"] = [resultados.get(u[0], ("", False))
                                   for u in ev[1]["usos"]]
    return eventos


def _depende(b, previas, visto_antes):
    """Motivo por el que la llamada b depende de alguna de `previas`, o ''."""
    _id, nombre_b, entrada_b = b["usos"][0]
    txt_b = json.dumps(entrada_b, ensure_ascii=False)
    term_b = _terminos(txt_b)
    for a, visto in zip(previas, visto_antes):
        _ida, nombre_a, entrada_a = a["usos"][0]
        res, err = a["resultados"][0]
        if err or PARECE_ERROR.search(res[:400]):
            return "error_previo"
        nuevos = (term_b & _terminos(res)) - visto
        if nuevos:
            return "usa:" + ",".join(sorted(nuevos)[:3])
        # B vuelve sobre el mismo archivo que A: es afinar (grep -n y luego sed -n,
        # wc -l y luego Read con offset), y afinar necesita
        # haber visto el resultado aunque no copie ningun termino de el.
        if _objetos(entrada_a) & _objetos(entrada_b):
            return "mismo_objeto"
    return ""


OBJETO = re.compile(r"[\w.\-]+\.(?:py|md|ps1|sql|csv|json|jsonl|html|xlsx|txt|js|ts|"
                    r"yml|yaml|toml)\b", re.I)


def _objetos(entrada):
    """Archivos que nombra un input, por su nombre final."""
    txt = json.dumps(entrada, ensure_ascii=False) if not isinstance(entrada, str) else entrada
    salida = set()
    for m in OBJETO.finditer(txt):
        salida.add(m.group(0).lower().replace(".md", ""))
    return salida - HERRAMIENTAS


# Scripts que se usan para leer otra cosa (un lector de correo, un cliente de la base):
# nombrarlos no dice sobre que se trabaja. Se llena en main() desde --herramientas, que
# toma por defecto CONTEXT_ECONOMY_HERRAMIENTAS (nombres separados por coma).
HERRAMIENTAS = set()
HERRAMIENTAS_DEFECTO = ""


def medir(ruta, desde, hasta):
    eventos = leer_sesion(ruta)
    visto = set()
    salida = {"llamadas": 0, "ponderado": 0.0, "sueltas": 0, "pares": 0,
              "sobrantes": 0, "ahorro": 0.0, "arrastre_aviso": 0, "disparos": 0,
              "motivos": {}, "muestra": []}
    racha, racha_visto = [], []
    tramo_disparos = []           # indices (en llamadas del tramo) donde salto el aviso
    n_tramo = 0

    def cerrar_tramo():
        for i in tramo_disparos:
            salida["arrastre_aviso"] += n_tramo - i
        del tramo_disparos[:]

    prev = None                   # ultima llamada, si fue lectura suelta
    for ev in eventos:
        if ev[0] == "corte":
            cerrar_tramo()
            n_tramo = 0
            racha, racha_visto, prev = [], [], None
            continue
        if ev[0] in ("humano", "contexto"):
            visto |= _terminos(ev[1])
            if ev[0] == "humano":
                racha, racha_visto, prev = [], [], None
            continue
        ll = ev[1]
        m = marca(ll["ts"])
        dentro = ((desde is None or (m and m > desde)) and
                  (hasta is None or (m and m <= hasta)))
        u = ll.get("usage") or {}
        if dentro:
            salida["llamadas"] += 1
            salida["ponderado"] += sum((u.get(c) or 0) * PRECIO[c] for c in PRECIO)
            n_tramo += 1
        suelta = (len(ll["usos"]) == 1 and es_lectura(ll["usos"][0][1], ll["usos"][0][2]))
        visto_antes_res = set(visto)
        visto |= _terminos(ll["texto"])
        for _i, _n, e in ll["usos"]:
            visto |= _terminos(json.dumps(e, ensure_ascii=False))
        if suelta and dentro:
            salida["sueltas"] += 1
            salida["disparos"] += 1
            tramo_disparos.append(n_tramo)
        if suelta and prev is not None:
            if dentro:
                salida["pares"] += 1
            motivo = _depende(ll, racha, racha_visto)
            clave_m = motivo.split(":")[0] if motivo else "independiente"
            if dentro:
                salida["motivos"][clave_m] = salida["motivos"].get(clave_m, 0) + 1
                salida["muestra"].append({
                    "archivo": os.path.basename(ruta), "fecha": (ll["ts"] or "")[:16],
                    "a": _breve(racha[-1]), "b": _breve(ll), "veredicto": clave_m,
                    "detalle": motivo})
            if not motivo:
                if dentro:
                    salida["sobrantes"] += 1
                    salida["ahorro"] += (u.get("cache_read_input_tokens") or 0) * \
                        PRECIO["cache_read_input_tokens"]
                racha.append(ll)
                racha_visto.append(visto_antes_res)
            else:
                racha, racha_visto = [ll], [visto_antes_res]
        elif suelta:
            racha, racha_visto = [ll], [visto_antes_res]
        else:
            racha, racha_visto = [], []
        prev = ll if suelta else None
        for texto, _err in ll.get("resultados", []):
            visto |= _terminos(texto)
    cerrar_tramo()
    return salida


def _breve(ll):
    _i, nombre, e = ll["usos"][0]
    if isinstance(e, dict):
        for k in ("command", "file_path", "pattern", "path", "url", "query"):
            if isinstance(e.get(k), str):
                return (nombre + ": " + " ".join(e[k].split()))[:400]
    return nombre


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    v = ap.add_mutually_exclusive_group()
    v.add_argument("--dias", type=int, default=14)
    v.add_argument("--desde", type=leer_desde, metavar="'AAAA-MM-DD HH:MM'")
    ap.add_argument("--hasta", type=leer_desde, metavar="'AAAA-MM-DD HH:MM'")
    ap.add_argument("--aviso", type=int, default=40,
                    help="tokens del texto que inyectaria el hook (defecto 40)")
    ap.add_argument("--muestra", help="CSV con todos los pares, para revisar a mano")
    ap.add_argument("--herramientas", metavar="a.py,b.ps1",
                    default=os.environ.get("CONTEXT_ECONOMY_HERRAMIENTAS",
                                           HERRAMIENTAS_DEFECTO),
                    help="scripts que solo sirven para leer otra cosa; nombrarlos no "
                         "cuenta como volver sobre el mismo objeto")
    ap.add_argument("--raiz", default=RAIZ)
    args = ap.parse_args()
    HERRAMIENTAS.update(n.strip().lower() for n in args.herramientas.split(",")
                        if n.strip())

    desde = args.desde
    if desde is None:
        desde = (dt.datetime.now().astimezone() - dt.timedelta(days=args.dias))
    total = {"llamadas": 0, "ponderado": 0.0, "sueltas": 0, "pares": 0, "sobrantes": 0,
             "ahorro": 0.0, "arrastre_aviso": 0, "disparos": 0, "motivos": {}}
    muestra = []
    corte = desde.timestamp()
    for ruta in glob.glob(os.path.join(args.raiz, "*", "*.jsonl")):
        if os.path.getmtime(ruta) < corte:
            continue
        s = medir(ruta, desde, args.hasta)
        for k in total:
            if k == "motivos":
                for mk, mv in s["motivos"].items():
                    total["motivos"][mk] = total["motivos"].get(mk, 0) + mv
            else:
                total[k] += s[k]
        muestra.extend(s["muestra"])

    t = total
    if not t["llamadas"]:
        print("Sin llamadas en la ventana.")
        return
    costo_aviso = (t["arrastre_aviso"] * args.aviso * PRECIO["cache_read_input_tokens"]
                   + t["disparos"] * args.aviso * PRECIO["cache_creation_input_tokens"])
    pct = lambda x, y: 100.0 * x / y if y else 0.0
    print("Ventana desde %s%s" % (desde.strftime("%Y-%m-%d %H:%M"),
          " hasta " + args.hasta.strftime("%Y-%m-%d %H:%M") if args.hasta else ""))
    print("Llamadas a la API ............ %7d   ponderado %12.0f" % (t["llamadas"], t["ponderado"]))
    print("Con una sola lectura ......... %7d   (%.1f %%)" % (t["sueltas"], pct(t["sueltas"], t["llamadas"])))
    print("Pares lectura->lectura ....... %7d" % t["pares"])
    for mk, mv in sorted(t["motivos"].items(), key=lambda x: -x[1]):
        print("    %-24s %6d  (%.0f %%)" % (mk, mv, pct(mv, t["pares"])))
    print("Llamadas sobrantes (techo) ... %7d   (%.1f %% de las llamadas)" % (
        t["sobrantes"], pct(t["sobrantes"], t["llamadas"])))
    print("Ahorro techo, ponderado ...... %12.0f   (%.2f %% del gasto)" % (
        t["ahorro"], pct(t["ahorro"], t["ponderado"])))
    print("Costo del aviso (%d tok) ..... %12.0f   (%.2f %% del gasto; %d disparos)" % (
        args.aviso, costo_aviso, pct(costo_aviso, t["ponderado"]), t["disparos"]))
    print("Neto con obediencia total .... %12.0f   (%.2f %% del gasto)" % (
        t["ahorro"] - costo_aviso, pct(t["ahorro"] - costo_aviso, t["ponderado"])))
    if t["ahorro"] > 0:
        print("Obediencia minima para empatar: %.0f %%" % pct(costo_aviso, t["ahorro"]))

    if args.muestra:
        with io.open(args.muestra, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["archivo", "fecha", "veredicto",
                                               "detalle", "a", "b"])
            w.writeheader()
            w.writerows(muestra)
        print("Pares en %s" % args.muestra)


if __name__ == "__main__":
    main()
