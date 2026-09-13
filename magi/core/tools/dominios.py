"""
Qué dominio activa un encargo, y qué herramientas trae cada uno.

POR QUÉ ESTÁ AQUÍ Y NO EN `builtin.py`
======================================
`builtin.py` iba 797 líneas de un techo de 800 y el reparto de las herramientas
que estaban fuera de toda caja lo dejó en 839. El techo de un trinquete no se
sube nunca: se conecta, se adelgaza o se extrae. Esto es lo tercero, y el bloque
que sale es una unidad entera —las cajas, las pistas que las activan y
`domains_for`—, no un trozo cortado para ganar líneas.

QUÉ DECIDE ESTE FICHERO
=======================
El catálogo entra ENTERO en cada prompt, y la ventana de los proveedores
gratuitos son ~8k tokens. Así que no se ofrece todo a todos: cada encargo activa
sus dominios y ve su caja. Lo que aquí no esté, el enjambre no lo ve — por eso
`tests/test_herramientas_alcanzables.py` cuenta las que quedan fuera y congela
el número.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Dominios de herramientas (§2.2).
#
# El catálogo entra ENTERO en cada prompt de cada agente. Con 30 herramientas
# pasó de 3200 caracteres, y compactar las descripciones ya no daba más de sí.
# La respuesta correcta no es recortar texto: es no ofrecer el toolchain de
# ingeniería inversa a quien está escribiendo un informe, ni el compositor de
# manga a quien depura un dynarec.
# ---------------------------------------------------------------------------

CORE_TOOLS = {
    "read_file", "write_file", "edit_file", "delete_path", "list_dir", "grep",
    "glob", "run_command", "python_exec", "run_tests", "web_fetch", "undo",
}

# Herramientas de repositorio y publicación.
#
# Estaban en CORE_TOOLS, o sea en el prompt de TODOS los dominios, y eso puso
# rojo a test_catalog_stays_within_a_free_provider_window: el catálogo de
# reverse/MELCHIOR llegó a 2782 caracteres con el techo en 2700.
#
# El propio test dice qué hacer cuando salta: "reducir PARÁMETROS o afinar el
# dominio, no reescribir textos". Afinar el dominio es lo correcto aquí, y no
# solo por el número: quien está portando un dynarec de PSP a Vita no necesita
# `gh workflow run` ni `build_exe` en su prompt. Son herramientas de otra
# tarea, y ofrecerlas es ruido que empuja al modelo a usarlas.
DEVOPS_TOOLS = {
    "git", "gh", "build_exe", "build_project_exe", "create_venv",
}

REVERSE_TOOLS = {
    "binary_identify", "console_profile", "disassemble", "binary_strings",
    "emulate_code", "differential_test", "compare_consoles", "analyze_port",
    "suggest_port_base", "re_toolchain_status", "index_emulator",
    "locate_subsystem", "compare_emulators", "binary_entropy",
}

STUDIO_TOOLS = {
    "observe_artifact", "inspect_image", "studio_backends",
    "compose_manga_page", "validate_manga_layout",
    "render_animatic", "record_program",
}

WORLD_TOOLS = {
    "macro_snapshot", "fred_series", "compare_countries", "news_headlines",
    "company_fundamentals", "owner_earnings", "dcf_valuation",
    "quality_checklist", "record_thesis", "resolve_thesis",
    "calibration_report",
}

_DOMAIN_HINTS = {
    # Repositorio y publicación. Sin estas pistas, `git` y `gh` quedarían
    # inalcanzables cuando se los pide por su nombre — el mismo fallo que
    # tuvo "gasto militar" en el dominio del mundo.
    "devops": (
        "git", "commit", "rama", "branch", "push", "pull", "merge",
        "repositorio", "repo", "github", "actions", "workflow", "runner",
        "ci", "release", "publicar", "tag", "etiqueta", "compilar",
        "compila", "build", "ejecutable", ".exe", "pyinstaller", "venv",
        "entorno virtual", "despliegue", "desplegar", "versión", "version",
    ),
    "reverse": (
        "binario", "firmware", "rom", "emulador", "emular", "desensambl",
        "dynarec", "ensamblador", "ingenieria inversa", "ingeniería inversa",
        "psp", "vita", "nintendo", "gba", "nds", "n64", "playstation",
        "mips", "arm", "opcode", "instruccion", "instrucción", "elf", "dump",
        "decompil", "portar", "port ", "consola",
    ),
    "studio": (
        "juego", "videojuego", "manga", "cómic", "comic", "viñeta", "vineta",
        "imagen", "dibujo", "documento", "informe", "pdf", "docx", "vídeo",
        "video", "pantalla", "captura", "sprite", "render",
    ),
    # Estas pistas se comprueban en tests/test_wiring.py contra frases escritas
    # como se pregunta de verdad, no como me salió a mí al redactar la lista.
    # Así apareció que "gasto militar" —un indicador que el módulo SÍ ofrece—
    # no activaba el dominio: la herramienta existía y era inalcanzable.
    "world": (
        "macro", "economia", "economía", "inflacion", "inflación", "pib",
        "tipos de interes", "tipos de interés", "bono", "curva", "paro",
        "desempleo", "geopolit", "geopolít", "mercado", "bolsa", "accion",
        "acción", "acciones", "invertir", "inversion", "inversión", "valorar",
        "valoracion", "valoración", "empresa", "cotizada", "balance",
        "beneficio", "dividendo", "buffett", "dcf", "flujo de caja",
        "fundamentales", "actualidad", "noticia", "banco central", "fed",
        "bce", "reserva federal", "deuda", "divisa", "tipo de cambio",
        "tesis", "calibrac", "prediccion", "predicción", "pronostico",
        "pronóstico",
        # Indicadores del Banco Mundial: sin estas, el catálogo los ofrece y
        # el enrutado no llega a ellos.
        "militar", "armament", "poblacion", "población", "demograf",
        "exportacion", "exportación", "comercio", "arancel", "sancion",
        "sanción", "banco mundial", "per capita", "per cápita",
        "esperanza de vida", "renovable", "pais", "país", "paises", "países",
    ),
}


# Los dominios y sus conjuntos de herramientas, DERIVADOS de _DOMAIN_HINTS.
#
# Estaban escritos a mano como {"core", "reverse", "studio"} en dos sitios.
# Al añadir el dominio del mundo (§6) las dos copias quedaron desfasadas a la
# vez, y el síntoma habría sido silencioso: `domains_for("")` devolvía un
# conjunto que ya no era "todos", así que la rama de "sin pista, ofrécelo
# todo" empezaba a recortar el catálogo sin que nadie lo pidiera.
#
# Es la misma clase de fallo que la lista de andamiaje de test_wiring.py: una
# lista mantenida a mano que se desincroniza de la realidad. Si se deriva, no
# puede desincronizarse.
# Lo que se registró y no entró en ninguna caja.
#
# EL FALLO, MEDIDO EL 13-SEP-2026
# ===============================
# `registry_for_role` acota por dominio: `CORE_TOOLS` mas la caja del dominio
# que active el encargo. Una herramienta que no este en ninguna de las dos
# existe en el registry y es INALCANZABLE en cuanto la tarea menciona un
# dominio. Medido con "portar el dynarec del SH2 a la Vita":
#
#     67 herramientas en el registry
#     26 visibles para MELCHIOR
#     18 fuera de CORE y de toda caja  <- el 27 % del catalogo
#
# Entre las 18 estaba la capa Lilim entera, la memoria, los sentidos y
# `web_search`/`web_read`, que son de las que mas falta hacen justo en un
# encargo tecnico. Es el mismo fallo que ya tuvieron `git`/`gh` y el dominio
# del mundo con "gasto militar", y que el comentario de `_DOMAIN_HINTS` da por
# aprendido: aqui volvio a pasar con las herramientas nuevas de v5.25-v5.27.
#
# POR QUE NO ENTRAN TODAS
# =======================
# `CORE_TOOLS` va en TODOS los prompts y el catalogo tiene techo: 2700
# caracteres, que sale de la ventana de 8k de los proveedores gratuitos.
# Medido hoy, el peor caso es reverse/MELCHIOR con 2485 — quedan 215, y cada
# herramienta cuesta entre 75 y 163 caracteres. No caben las 18 en ningun
# sitio, y el propio test que vigila el techo dice que la salida es "reducir
# PARAMETROS o afinar el dominio, no reescribir textos".
#
# Asi que se reparten por dominio, cada una donde sirve, dentro del margen
# medido de cada catalogo. Lo que sigue fuera queda contado por
# `test_herramientas_alcanzables.py`, que congela el numero para que no crezca.
_TRANSVERSALES_WEB = {"web_search", "web_read"}
_REPOS = {"repos_clonar", "repos_desregistrar", "repos_de"}

_DOMAIN_TOOLSETS: dict[str, set[str]] = {
    # margen medido 1325: entra todo lo de repositorio y la web.
    "devops": DEVOPS_TOOLS | _TRANSVERSALES_WEB | _REPOS | {"entregar_artefacto"},
    # margen medido 215: solo cabe UNA, y es la que pide un encargo de porte
    # — clonar el emulador de referencia. `web_search` (126) no cabe junto a
    # ella (132): 258 > 215. Queda declarado, no disimulado.
    "reverse": REVERSE_TOOLS | {"repos_clonar"},
    # studio y world se quedan como estaban, y NO por olvido: se combinan.
    # El segundo techo del catalogo —3500 caracteres para un encargo que
    # activa dos dominios, medido con "escribe un juego y analiza su
    # rendimiento macro"— ya iba a 3160. Quedan 340, y meter los sentidos en
    # studio (361) o la web en world (253) lo revienta: con el reparto
    # completo se midio 4157. La salida que señala el propio test es reducir
    # PARAMETROS de las herramientas que ya estan, que es trabajo aparte y con
    # su medicion. Mientras tanto queda contado, no disimulado.
    "studio": STUDIO_TOOLS,
    "world": WORLD_TOOLS,
}
ALL_DOMAINS: set[str] = {"core"} | set(_DOMAIN_HINTS)


def domains_for(task_hint: str) -> set[str]:
    """
    Qué dominios de herramientas necesita una tarea.

    Sin pista, se ofrecen todos: es preferible un catálogo grande a que el
    agente no pueda hacer su trabajo por una heurística demasiado estrecha.
    """
    hint = (task_hint or "").lower()
    if not hint.strip():
        return set(ALL_DOMAINS)
    found = {"core"}
    for domain, needles in _DOMAIN_HINTS.items():
        if any(n in hint for n in needles):
            found.add(domain)
    return found
