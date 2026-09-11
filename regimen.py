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
llama a `avisar()` y el banner sale arriba del resultado, con la fecha del cambio y el
`--dias` que habría que usar.

    from regimen import avisar
    avisar(dias=30)

Qué cuenta como mecanismo: por defecto, `~/.claude/settings.json`, `~/.claude/CLAUDE.md`
y todo lo que haya en `~/.claude/hooks/`. Las piezas propias (el `CLAUDE.md` de un repo,
un índice de memoria, un script de ruteo) se suman con la variable de entorno
`CONTEXT_ECONOMY_PIEZAS`, rutas separadas por `os.pathsep` (`;` en Windows, `:` en el
resto). Se mira la fecha de modificación de cada una.

Solo lee fechas de archivos. No modifica nada.
"""

from __future__ import print_function

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


def _fecha_mtime(ruta):
    try:
        return dt.date.fromtimestamp(os.path.getmtime(ruta))
    except OSError:
        return None


def cambios(desde):
    """[(fecha, pieza)] de lo que cambió desde esa fecha, del más nuevo al más viejo."""
    fuera = []
    for ruta in piezas():
        f = _fecha_mtime(ruta)
        if f and f >= desde:
            fuera.append((f, os.path.basename(ruta)))
    fuera.sort(reverse=True)
    return fuera


def avisar(dias=None, salida=None):
    """Imprime el banner si la ventana cruza un cambio. Devuelve la fecha del más
    nuevo, o None si la ventana está limpia.

    `dias=None` es para cuando se mide todo el histórico: ahí siempre puede haber
    mezcla de regímenes, así que se mira una ventana larga y se avisa igual.
    """
    salida = salida or sys.stdout
    hoy = dt.date.today()
    ventana = dias if dias else 120
    desde = hoy - dt.timedelta(days=ventana)
    lista = cambios(desde)
    if not lista:
        return None

    # Tres lineas y no mas: el banner sale en cada corrida y, si se lee desde el chat
    # de un agente, se relee en cada turno posterior.
    ultimo = lista[0][0]
    dias_utiles = (hoy - ultimo).days + 1
    detalle = ", ".join("%s %s" % (f.isoformat(), p) for f, p in lista[:2])
    if len(lista) > 2:
        detalle += " (+%d)" % (len(lista) - 2)
    print("[!] Cambio de mecanismo dentro de la ventana: %s" % detalle, file=salida)
    if dias:
        medida = "Se miden %d dias y el regimen actual empezo hace %d" % (
            dias, dias_utiles - 1)
    else:
        medida = "El conjunto mezcla regimenes; el actual empezo hace %d dias" % (
            dias_utiles - 1)
    print("    %s: un promedio mezclado no describe a ninguno." % medida, file=salida)
    if dias_utiles < 3:
        print("    Regimen nuevo de menos de 3 dias: sirve para describir, NO para "
              "recomendar.", file=salida)
    else:
        print("    Antes de recomendar, repetir con --dias %d y decidir con esa."
              % dias_utiles, file=salida)
    return ultimo


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    if avisar(dias=n) is None:
        print("Ventana de %d dias limpia: ningun cambio de mecanismo adentro." % n)
