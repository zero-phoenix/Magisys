"""
¿Lo que acabas de escribir responde a la pregunta pendiente, o es otra cosa?

EL FALLO
========
Cuando el enjambre termina una ronda se queda en `WAITING_USER_APPROVAL` y
espera tu visto bueno. Hasta ahora, TODO lo que escribieras en ese estado se
absorbía como respuesta a esa tarea. Ocurrió tal cual:

    root@system:~# dime por que la soledad duele
    [SWARM] task_84hkn8xp se trata como respuesta a task_29ceb5d6
    [SWARM] Feedback del usuario recibido. Reanudando debate (Ronda 2)

Una pregunta nueva, sin ninguna relación con la propuesta pendiente, se gastó
como comentario a esa propuesta. Nunca se contestó. Desde fuera se ve como que
el sistema no responde —«hice una pregunta pero nadie me responde»— y no hay
forma de enterarse de lo que ha pasado.

EL CRITERIO
===========
Absorber solo cuando de verdad parece una respuesta:

  · Aprobar o rechazar        -> «sí», «apruebo», «no», «rehazlo»
  · Pedir un cambio concreto  -> «cámbialo», «añade», «quita», «mejor usa»
  · Un mensaje muy corto sin verbo interrogativo, que en ese contexto solo
    puede ser una reacción a lo anterior.

Y NO absorber cuando es una petición nueva: pregunta con interrogación, verbo
imperativo de encargo («dime», «explica», «hazme», «crea»), o simplemente algo
largo y autónomo que no menciona la propuesta.

Ante la duda se trata como NUEVA. Equivocarse hacia «pregunta nueva» cuesta una
tarea de más, visible y cancelable; equivocarse hacia «respuesta» se traga la
petición en silencio, que es el fallo que estamos cerrando.
"""
from __future__ import annotations

import re
import unicodedata

__all__ = ["es_respuesta_a_aprobacion", "aprueba", "pide_artefacto",
           "APROBACION", "RECHAZO"]


def _plano(s: str) -> str:
    """Minúsculas y sin acentos: 'Sí' y 'si' son la misma respuesta."""
    sin = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in sin if not unicodedata.combining(c))


#: Aprobación explícita. Se comparan como PALABRA ENTERA — sin eso, el «si»
#: de «siempre» o el «ok» de «okupa» aprobarían una propuesta sin querer.
APROBACION = {
    "si", "sii", "sip", "claro", "ok", "oka", "okey", "vale", "dale",
    "apruebo", "aprobado", "adelante", "correcto", "perfecto", "exacto",
    "hazlo", "ejecuta", "ejecutalo", "procede",
    "yes", "yeah", "sure", "go", "approve", "approved", "lgtm",
}

#: Palabras que aprueban SOLO si son el mensaje entero.
#:
#: «sigue» y «continúa» estaban en APROBACION y eso convertía «sigue así de
#: lento» —una queja— en una aprobación que cerraba la tarea y lanzaba la
#: ejecución automática de su código. Solas sí valen: un «continúa» a secas es
#: seguir adelante.
APROBACION_SOLAS = {"continua", "sigue", "seguimos", "next"}

#: Palabras que APRUEBAN cuando MANDAN la primera palabra de una frase larga
#: (v5.29, regla 2c de es_respuesta_a_aprobacion). Es APROBACION sin los
#: verbos que lideran encargos con naturalidad: «dale caña al render», «go
#: fetch», «ejecuta los tests y dime». Y SIN las SOLAS: «sigue así de lento»
#: ya mordió una vez como queja; solo aprueban si son el mensaje entero.
APROBACION_CABEZA = APROBACION - {"dale", "go", "hazlo", "ejecuta",
                                  "ejecutalo"}

#: Rechazo o petición de otra ronda. También es una respuesta a la propuesta.
RECHAZO = {
    "no", "nop", "nope", "rechazo", "rechazado", "cancela", "cancelar",
    "para", "detente", "alto", "mal", "incorrecto", "rehazlo", "otra",
    "reject", "rejected", "stop",
}

#: Verbos con los que se pide un CAMBIO sobre lo propuesto. Son respuesta,
#: aunque vengan en una frase larga.
_REVISION = (
    "cambia", "cambialo", "modifica", "corrige", "arregla", "ajusta",
    "anade", "agrega", "quita", "elimina", "reduce", "amplia", "mejora",
    "mejor usa", "en vez de", "en lugar de", "sustituye", "reemplaza",
    "revisa", "rehaz", "vuelve a", "hazlo de nuevo", "simplifica",
    "la propuesta", "tu propuesta", "el plan", "esa idea", "eso que",
)

#: Verbos con los que se encarga algo NUEVO. Si aparecen al principio, es una
#: petición, no un comentario.
_ENCARGO = (
    "dime", "cuentame", "explica", "explicame", "describe", "define",
    "hazme", "haz un", "haz una", "crea", "genera", "escribe", "programa",
    "construye", "disena", "analiza", "optimiza", "propon",
    "implementa", "busca", "investiga", "compara",
    "traduce", "resume", "calcula", "muestrame", "ensename", "por que",
    "porque", "que es", "quien es", "como se", "cuando", "donde",
    # Escribir «naoko» es hablarle a la supervisora, no responder a una
    # propuesta del enjambre. Sin esto, «hola naoko» (2 palabras) caía en la
    # regla de «mensaje corto = reacción» y se absorbía como feedback de la
    # tarea que estuviera esperando aprobación.
    "naoko", "hola naoko",
)

_INTERROGACION = re.compile(r"[?¿]")
_PALABRA = re.compile(r"[^\W\d_]+", re.UNICODE)


def aprueba(texto: str) -> bool:
    """
    True SOLO si el texto aprueba de forma inequívoca.

    Antes se comprobaba con `any(w in command.lower() for w in [...])`, es
    decir por SUBCADENA: el «si» de «siempre», de «análisis» o de «sigue así»
    daba por aprobada la propuesta, cerraba la tarea y disparaba la ejecución
    automática de sus bloques de código. Aprobar por accidente algo que va a
    ejecutarse en la máquina del usuario es de los errores más caros que puede
    cometer este sistema, así que aquí se comparan palabras enteras.
    """
    palabras = _PALABRA.findall(_plano(texto))
    if not palabras:
        return False
    if any(p in RECHAZO for p in palabras):
        return False        # «no, apruebo» no aprueba nada
    if any(p in APROBACION for p in palabras):
        return True
    # Las ambiguas solo cuentan si son el mensaje entero: «sigue» aprueba,
    # «sigue así de lento» es una queja.
    return len(palabras) == 1 and palabras[0] in APROBACION_SOLAS


def es_respuesta_a_aprobacion(texto: str) -> bool:
    """
    True si `texto` responde a una propuesta pendiente de aprobación.

    Ante la duda devuelve False: abrir una tarea de más es visible y se puede
    cancelar; tragarse una pregunta no.
    """
    t = _plano(texto).strip()
    if not t:
        return False

    palabras = _PALABRA.findall(t)
    n = len(palabras)

    # 1. Una o dos palabras que son exactamente aprobación o rechazo.
    if n <= 3 and any(p in APROBACION or p in RECHAZO or p in APROBACION_SOLAS
                      for p in palabras):
        return True

    # 2. Un encargo nuevo manda sobre todo lo demás. Se mira el principio: es
    #    donde va el verbo de la petición. «dime por que la soledad duele»
    #    empieza por «dime» y no es una respuesta a nada.
    cabeza = " ".join(palabras[:3])
    if any(cabeza.startswith(v) or t.startswith(v) for v in _ENCARGO):
        return False

    # 2b. Mencionar a Naoko es hablarle a la supervisora, no responder al
    # enjambre. Cualquier mensaje que la cite abre su propio camino, por corto
    # que sea («oye naoko», «naoko ayuda»).
    if "naoko" in palabras:
        return False

    # 2c (v5.29): la aprobación empieza hablando de aprobar. Medido en la
    # auditoría del Tetris: «sí, apruebo: continúa con la siguiente ronda
    # del plan vivo» (>3 palabras) caía a tarea NUEVA y la pendiente quedaba
    # zombi — tres tareas de basura y ~20 minutos de bloqueo. Si la PRIMERA
    # palabra es aprobación o rechazo inequívoco y ningún verbo de encargo
    # manda la frase (regla 2, ya pasada), es una respuesta a lo pendiente.
    # El encargo que EMPIEZA con «dale/sí» coloquial asume el coste: es
    # mucho más caro el zombi que la absorción.
    if palabras[0] in APROBACION_CABEZA or palabras[0] in RECHAZO:
        return True

    # 3. Pedir un cambio SOBRE lo propuesto sí es responder. Pero el verbo
    #    de revisión tiene que MANDAR la frase —en la cabeza o en un mensaje
    #    corto— porque un encargo largo puede mencionar «mejora» de pasada.
    #    Medido el 2-sep-2026: «optimiza ... propon una mejora por cada
    #    filosofía...» se tragó como aprobación de una tarea zombi y el
    #    encargo nuevo jamás arrancó. Una persona común no sabe que hay
    #    una aprobación pendiente: su encargo no puede salir devorado.
    if any(v in t for v in _REVISION) and (
            n <= 8 or any(v in cabeza for v in _REVISION)):
        return True

    # 4. Una pregunta con interrogación y sin referencia a la propuesta es
    #    materia nueva.
    if _INTERROGACION.search(texto or ""):
        return False

    # 5. Mensajes muy cortos sin verbo de encargo: en un estado de espera solo
    #    pueden ser una reacción a lo anterior.
    if n <= 4:
        return True

    # 6. Todo lo demás —una frase larga y autónoma— se trata como nuevo.
    return False


# ---------------------------------------------------------------------------
# C4 — ¿ME ESTÁN PIDIENDO UN PRODUCTO O UNA EXPLICACIÓN?
# ---------------------------------------------------------------------------
#
# Distinguirlo cambia lo que significa «terminado». Si me piden un .exe, una
# descripción del .exe no es una entrega parcial: es no haber entregado.
#
# Medido el 2026-08-20, dos veces: «haz una réplica de Tetris en un .exe
# portable» y «crea un ping pong a color de 16 bits en un .exe portable»
# terminaron con cero bloques de código, cero artefactos y el árbitro diciendo
# «se compiló exitosamente el binario». Nada lo detectó porque nada sabía que
# se había pedido un binario.

#: Sustantivos que nombran un ENTREGABLE, no un tema.
_COSAS = ("exe", ".exe", "ejecutable", "binario", "instalador", "portable",
          "aplicacion", "app", "programa", "juego", "script", "fichero",
          "archivo", "libreria", "paquete")

#: Verbos de construir. Sin uno de estos, hablar de un .exe es hablar de un
#: .exe, no pedirlo.
_CONSTRUIR = ("crea", "crear", "haz", "hazme", "hacer", "construye", "construir",
              "genera", "generar", "compila", "compilar", "empaqueta",
              "empaquetar", "programa", "programame", "desarrolla", "escribe",
              "escribeme", "implementa", "build", "make", "create")

#: Y estos mandan por encima: si la frase empieza pidiendo entender, no pide
#: producto por mucho que nombre un .exe. «Explica cómo se hace un exe» es una
#: pregunta, y tratarla como encargo de producto la respondería con un binario
#: que nadie pidió.
_EXPLICAR = ("explica", "explicame", "describe", "compara", "analiza",
             "por que", "porque", "como funciona", "que es", "diferencia",
             "ventajas", "resume", "opina")


def pide_artefacto(texto: str) -> bool:
    """
    True si el encargo espera un fichero al final, no un texto.

    Se exigen las dos cosas —verbo de construir Y sustantivo de entregable—
    porque cada una por separado se dispara sola: «escribe un resumen» tiene
    verbo y no pide binario; «el ejecutable de PPSSPP» nombra uno y no pide
    nada. Y `_EXPLICAR` gana siempre: preguntar cómo se hace algo no es
    pedirlo.
    """
    t = _plano(texto)
    if not t.strip():
        return False
    cabeza = t[:60]
    if any(v in cabeza for v in _EXPLICAR):
        return False
    palabras = {p.strip(".,;:!?¿¡()\"'") for p in t.split()}
    hay_verbo = bool(palabras & set(_CONSTRUIR))
    hay_cosa = bool(palabras & set(_COSAS)) or ".exe" in t
    return hay_verbo and hay_cosa
