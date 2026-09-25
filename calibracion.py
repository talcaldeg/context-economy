"""Caracteres por token: la unica fuente de la constante para los medidores.

Los transcripts de Claude Code traen el `usage` de cada llamada, pero no cuantos tokens pesa
cada resultado de herramienta. Para eso hay que convertir caracteres a tokens, y la regla que
circula (unos 4 caracteres por token) no sirve para este corpus: hasta el 25-sep-2026
`medir_tool_results.py` dividia por 4,0 y los tokens de cada resultado quedaban bajos cerca
de 1,9 veces.

Calibracion: para dos llamadas consecutivas A y B con solo resultados de texto entre medio,
ctx(B) - ctx(A) - salida(A) son los tokens de esos resultados. Sobre 894 pares (resultados de
6.000 caracteres o mas, salidas de hasta 400 tokens, agosto y septiembre de 2026), la mediana
quedo entre 2,11 y 2,21 caracteres por token en Opus 5, Opus 5.5 y Sonnet 5, sin diferencia
entre codigo, prosa y el resto. Es texto mayormente en espanol: con otro idioma o otro
modelo, conviene recalibrar con el mismo metodo.
"""

CHARS_POR_TOKEN = 2.1


def tokens(texto):
    """Tokens estimados de un texto."""
    return int(len(texto or "") / CHARS_POR_TOKEN)
