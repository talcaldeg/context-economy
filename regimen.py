# -*- coding: utf-8 -*-
"""Avisa cuando la ventana que se está midiendo cruza un cambio de mecanismo.

El error que esto evita: se midieron 45 días de consumo, se sacaron conclusiones ("las
lecturas enteras de archivos de memoria son el 47 % del arrastre, hay que partirlos") y
resultó que dos días antes había entrado un hook que bloqueaba las relecturas. En el
régimen que de verdad estaba corriendo, esas lecturas habían caído de 754 a 2 y la
recomendación era trabajo con retorno cero. El promedio de una ventana que mezcla dos
regímenes no describe ninguno de los dos, y se parece más al viejo mientras más larga
sea.

La regla, entonces: **antes de recomendar sobre una medición, mirar si el mecanismo
cambió dentro de la ventana; si cambió, la ventana empieza en el cambio.** El histórico
sigue sirviendo para entender de dónde se viene, nunca para decidir qué hacer ahora.

Como depender de acordarse no funciona, esto lo dicen los propios medidores: cada uno
llama a `avisar()` y el banner sale arriba del resultado, con la hora del cambio y el
`--desde` exacto que habría que usar.

    from regimen import avisar, leer_desde
    avisar(dias=30)
    avisar(desde=leer_desde("2026-09-10 22:25"))

**Por qué con hora y no con días:** ante un cambio hecho a las 22:23, un banner que
sugiere `--dias 4` abre una ventana por fecha de fin de sesión, más ancha que el régimen y
no más angosta, justo cuando el régimen nuevo es corto y cada hora del viejo pesa más. La
hora de cada pieza sale de su fecha de modificación y la sugerencia se redondea al minuto
siguiente, para que repetir con ella deje la ventana limpia.

Qué cuenta como mecanismo: por defecto, `~/.claude/settings.json`, `~/.claude/CLAUDE.md`
y todo lo que haya en `~/.claude/hooks/`. Las piezas propias (el `CLAUDE.md` de un repo,
un índice de memoria, un script de ruteo) se suman con la variable de entorno
`CONTEXT_ECONOMY_PIEZAS`, rutas separadas por `os.pathsep` (`;` en Windows, `:` en el
resto).

Solo lee fechas de archivos. No modifica nada.
"""

from __future__ import print_function

import argparse
import datetime as dt
import os
import sys

CLAUDE = os.path.join(os.path.expanduser("~"), ".claude")


def piezas():
    """Rutas cuyo cambio cambia el comportamiento que los medidores miden."""
    rutas = [os.path.join(CLAUDE, "settings.json"), os.path.join(CLAUDE, "CLAUDE.md")]
    hooks = os.path.join(CLAUDE, "hooks")
    if os.path.isdir(hooks):
        rutas += [os.path.join(hooks, n) for n in sorted(os.listdir(hooks))
                  if os.path.isfile(os.path.join(hooks, n))]
    extra = os.environ.get("CONTEXT_ECONOMY_PIEZAS", "")
    rutas += [os.path.expanduser(r.strip()) for r in extra.split(os.pathsep) if r.strip()]
    return rutas


def leer_desde(texto):
    """'2026-09-10', '2026-09-10 22:24' o '2026-09-10T22:24', en hora local de este PC.
    Devuelve un datetime con zona, comparable con los timestamp de los transcripts, que
    vienen en UTC. Sirve de `type=` para argparse."""
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(texto.strip(), fmt).astimezone()
        except ValueError:
            pass
    raise argparse.ArgumentTypeError(
        "fecha no reconocida: %r (va 'AAAA-MM-DD HH:MM', hora local)" % texto)


def marca(ts):
    """Timestamp de un transcript ('2026-09-10T22:24:05.123Z') a datetime con zona, o
    None si falta o no se entiende."""
    if not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def local(momento):
    return momento.astimezone().strftime("%Y-%m-%d %H:%M")


def _hora_mtime(ruta):
    try:
        return dt.datetime.fromtimestamp(os.path.getmtime(ruta)).astimezone()
    except OSError:
        return None


def cambios_con_hora(desde, hasta=None):
    """[(momento, pieza)] de lo que cambió dentro de la ventana, del más nuevo al más
    viejo. `desde` y `hasta` son datetime con zona; sin `hasta` la ventana llega hasta
    ahora. Un cambio posterior a `hasta` no ensucia lo ya medido: por eso se descarta."""
    fuera = []
    for ruta in piezas():
        m = _hora_mtime(ruta)
        if m and m > desde and (hasta is None or m <= hasta):
            fuera.append((m, os.path.basename(ruta)))
    fuera.sort(reverse=True)
    return fuera


def cambios(desde):
    """Compatibilidad: [(fecha, pieza)] de lo que cambió desde una fecha (date)."""
    inicio = dt.datetime.combine(desde, dt.time()).astimezone()
    return [(m.date(), p) for m, p in cambios_con_hora(inicio)]


def sugerir_desde(momento):
    """El minuto siguiente al cambio, para que la ventana sugerida quede limpia."""
    m = momento.astimezone()
    if m.second or m.microsecond:
        m = m.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    return m


def avisar(dias=None, salida=None, desde=None, hasta=None):
    """Imprime el banner si la ventana cruza un cambio. Devuelve el momento del más
    nuevo, o None si la ventana está limpia.

    `desde` (datetime con zona) manda sobre `dias`. Sin ninguno de los dos es para cuando
    se mide todo el histórico: ahí siempre puede haber mezcla de regímenes, así que se
    mira una ventana larga y se avisa igual.

    `hasta` cierra la ventana por arriba: con él se juzga el régimen que corrió dentro,
    no los cambios que vinieron después, que ya no tocan lo medido.
    """
    salida = salida or sys.stdout
    ahora = dt.datetime.now().astimezone()
    inicio = desde or (ahora - dt.timedelta(days=dias if dias else 120))
    lista = cambios_con_hora(inicio, hasta)
    if not lista:
        return None

    # Tres lineas y no mas: el banner sale en cada corrida y, si se lee desde el chat
    # de un agente, se relee en cada turno posterior.
    ultimo = lista[0][0]
    edad = (ahora - ultimo).total_seconds() / 86400.0
    detalle = ", ".join("%s %s" % (local(m), p) for m, p in lista[:2])
    if len(lista) > 2:
        detalle += " (+%d)" % (len(lista) - 2)
    print("[!] Cambio de mecanismo dentro de la ventana: %s" % detalle, file=salida)
    if desde and hasta:
        medida = ("Se mide de %s a %s y adentro hubo un cambio"
                  % (local(desde), local(hasta)))
    elif desde:
        medida = "Se mide desde %s y el regimen actual empezo hace %.1f dias" % (
            local(desde), edad)
    elif dias:
        medida = "Se miden %d dias y el regimen actual empezo hace %.1f" % (dias, edad)
    else:
        medida = "El conjunto mezcla regimenes; el actual empezo hace %.1f dias" % edad
    print("    %s: un promedio mezclado no describe a ninguno." % medida, file=salida)
    opcion = '--desde "%s"' % local(sugerir_desde(ultimo))
    if hasta:
        opcion += ' --hasta "%s"' % local(hasta)
        print("    Antes de concluir, repetir con %s: la ventana pedida mezcla dos "
              "regimenes." % opcion, file=salida)
    elif edad < 3:
        print("    Regimen nuevo de menos de 3 dias: con %s sirve para describir, NO "
              "para recomendar." % opcion, file=salida)
    else:
        print("    Antes de recomendar, repetir con %s y decidir con esa." % opcion,
              file=salida)
    return ultimo


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "30"
    if arg.isdigit():
        if avisar(dias=int(arg)) is None:
            print("Ventana de %s dias limpia: ningun cambio de mecanismo adentro." % arg)
    elif avisar(desde=leer_desde(arg)) is None:
        print("Ventana desde %s limpia: ningun cambio de mecanismo adentro." % arg)
