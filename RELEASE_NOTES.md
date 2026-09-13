# v5.28.0 — F2, F3 y F5 existían y no se ejecutaban: ahora sí

**Qué cambia:** el Enjambre v6 pasa de estar escrito a estar conectado. La
v5.27.0 publicó F2-F5 y las notas lo dieron por «consolidado»; medido contra el
código el 12-sep, ninguna de las cuatro fases operaba. Sus tests pasaban porque
inyectaban las dependencias a mano y ninguno miraba el orquestador.

**Lo que hacía de verdad cada una, antes de esta versión:**

| Fase | Qué pasaba |
|---|---|
| F2 | La única llamada a subagentes vivía bajo `elif False:`. Y debajo, sin ejecutor, el módulo devolvía `"Análisis de {mision}: verificado sin hallazgos críticos."` con `exito=True` |
| F3 | El plan se creaba y se publicaba a la interfaz, pero `para_el_prompt()` no tenía llamador: el enjambre no sabía en cuántas partes se había dividido el encargo |
| F5 | A Casper no se le ofrecía el cuarto veredicto en su prompt. Y si lo emitía, caía en el `else` de «tarea fallida» — que además no cortaba el bucle: el debate entero se repetía hasta agotar las rondas |

**Lo concreto:**

- **F5 — el cuarto veredicto opera.** «La pregunta era otra» cierra la ronda con
  su aviso y deja el hallazgo en `descartes.jsonl`, en vez de contarse como
  fallo y pagar tres arbitrajes para entregar igual. A Casper se le ofrece el
  veredicto con una redacción **estrecha**: tiene que nombrar la pregunta real y
  el dato que lo sostiene, y se le dice que una propuesta floja se rechaza con
  NECESITA REVISIÓN. Es la lección de la réplica que capitulaba el 100 % de las
  veces: un prompt que ofrece una salida cómoda la convierte en la única.
- **F2 — el subagente deja de fabricar.** Sin ejecutor devuelve fallo explícito,
  no un «no encontré nada» sin haber mirado. Se añade `ejecutor_de_nube()`, que
  pregunta de verdad a un modelo de la **misma familia** que el nodo, con un
  prompt que le prohíbe escribir y proponer, turno único a temperatura 0.2 y
  menos de 50 palabras. Se conecta donde estaba la rama muerta, detrás de
  `MAGI_SUBAGENTES` y **apagado por defecto**: la premisa de F2 —«ahorro neto de
  contexto»— nunca se midió contra un control, y cada subagente es una llamada
  más a proveedores gratuitos que ya se degradan solos.
- **F3 — el plan viaja en el prompt.** `inyecciones.acumuladas()` lo cierra con
  las partes del encargo, y solo cuando hay más de una: repetir un encargo
  indivisible arriba del prompt es ruido. *La otra mitad de F3 no entra en esta
  versión y es a propósito:* los estados siguen naciendo en `pendiente` y no hay
  ninguna señal automática honesta de que una parte concreta esté hecha.
  Marcarlas al entregar sería inventárselo, y el plan pasaría de estar vacío a
  estar mintiendo. La única fuente fiable es Casper declarándolo, y eso toca su
  prompt otra vez — dos cambios en el mismo prompt sin poder atribuir una
  degradación a ninguno es como se perdió la réplica durante catorce rondas.
- **El bloque de veredictos sale del bucle.** `orchestrator.py` iba 1545 de un
  techo de 1550 y las fases no cabían. El techo no se sube: se extrae
  `magi/modules/swarm/veredicto.py`. Refactorización con las transformaciones
  contadas una a una (10 `self.`→`orq.`, 3 `break`→`return True`, 1
  `continue`→`return False`) y los 36 tests del enjambre como red. La extracción
  lo dejó en **1483**; con F3 y F2 conectados encima queda en **1489**, con 61
  líneas de margen para lo que venga.
- **La compuerta local vuelve a medir lo mismo que el CI.** `verificar.py` solo
  pasaba el lint crítico mientras el CI pasa el completo y bloqueante desde el
  2026-08-16: un import sin usar pasaba en verde aquí y tumbaba Actions. Ahora
  corre las dos pasadas — y si tu ruff no es el del pin, el paso sale **NO
  HECHO** en vez de verde, porque con otra versión mide otra cosa (0.6.9 marca
  17 `UP038` que 0.16.5 no marca). `publicar.py` distingue ese código 2 de un
  rojo de verdad.

**Tres guardas nuevas, todas nacidas de un fallo medido esta misma tanda:**

- Ninguna rama de `magi/` puede estar apagada con una constante falsa. Es el
  patrón que escondió F2 durante una versión entera: `huerfanos.py` no podía
  cazarlo porque busca el nombre como texto, y `despachar_subagente` aparecía en
  sus propios tests.
- Una copia del repositorio dentro del repositorio no cuenta como uso. Un agente
  abrió su worktree en `.claude/worktrees/` y el índice de huérfanos se encontró
  una copia entera de `magi/`: **el conteo cayó de 80 a 0** y el trinquete quedó
  desarmado hasta que él mismo lo cantó. Es la segunda vez con otro directorio;
  la primera fue `.venv-lock` y se arregló sin dejar prueba.
- El lint completo no se mide con otra versión de ruff.

**Lo que NO entra, y por qué:** **F4** (compuerta obligatoria antes del «hecho»)
queda fuera a propósito. Ejecuta `verificar.py --rapido` antes de cada
aprobación, y medido hoy la suite tarda ~165 s y **falla 1 de cada 3 corridas en
paralelo** por un transporte de asyncio que se destruye sin cerrar. Cablearla
ahora convertiría ese fallo intermitente de infraestructura en el rechazo
aleatorio de una de cada tres entregas legítimas. Va detrás de la estabilidad de
la suite, no delante.

**67 herramientas** en el catálogo. **1855 tests en Python** + **138 en
TypeScript/GUI**. Techos: `kernel.py` 1069/1070, `orchestrator.py` **1489**/1550,
`ritsuko.py` 800/800, `builtin.py` 797/800; huérfanos en 80.

---

# v5.27.2 — El instrumento medía otra cosa: el CI llevaba tres días en rojo sin que nadie tocara el código

**Qué cambia:** se fija la versión de todas las herramientas que deciden si el
build es verde o compilan el binario, y se quita del cortafuegos de navegador la
dependencia del stub de terceros que provocó el rojo. Además se corrigen dos
afirmaciones falsas de las notas de la v5.27.0.

**La medición, con control.** Entre el 10 y el 13 de septiembre, tres corridas
seguidas de `main` en rojo. Los commits de esos tres días son `docs:` y
`magi/core/no_browser.py` no se tocaba desde el 6-sep. El único error era:

```
magi/core/no_browser.py:243:24 - error: "SyncCDPSession" is not a known
attribute of module ".cdp" (reportAttributeAccessIssue)
```

Contrastado contra las propias corridas, que es el control que había:

| Corrida | pyright | Resultado |
|---|---|---|
| `34312710220` · 9-sep | 1.1.411 | verde |
| `34458014802` · 10-sep | 1.1.413 | rojo |
| `34733894754` · 13-sep | 1.1.414 | rojo |

Lo que cambió fue el instrumento, no lo medido. El CI hacía `pip install pyright`
sin versión.

**Lo concreto:**

- **Pines en `requirements-dev.txt`:** `pyright==1.1.411` y `pip-audit==2.10.1`,
  las dos versiones que estaban corriendo en verde — no las últimas. Acompañan al
  `ruff==0.16.5` fijado el 31-ago por este mismo motivo, con la lección ya
  escrita ahí: «una version nueva puede poner el build en rojo sin que nadie
  toque una linea de codigo». Se aplicó a ruff y se dejaron las otras tres.
- **`ci.yml`:** los pasos «Audit de dependencias Python» y «Type check nucleo»
  —los dos bloqueantes— instalan desde `requirements-dev.txt` en vez de
  `pip install <herramienta>` suelto.
- **`release.yml`:** eliminado el `pip install pyinstaller pywebview` que venía
  **después** de `pip install -r requirements.lock` y pisaba los pines recién
  instalados. Las dos ya están en el lock (`pyinstaller==6.21.0`,
  `pywebview==4.4.1`), así que la línea no añadía nada y sí quitaba
  reproducibilidad: el comentario de ese mismo job promete «aquí no se quiere la
  última versión: se quiere exactamente la que se probó». Ahora lo cumple.
- **`magi/core/no_browser.py`:** los atributos opcionales del módulo `cdp` de g4f
  se leen con `getattr`/`setattr`, el mismo patrón que el bucle de al lado. En
  runtime no había fallo —había un `hasattr` guardando cada acceso—, pero
  escrito con punto el fichero dependía de lo que el stub declarase ese mes.
  Desaparecen cuatro `# type: ignore[method-assign]` que apagaban la
  comprobación justo en el cortafuegos de navegador. Los 16 tests de
  `test_no_browser.py` siguen verdes.
- **Guarda nueva `tests/test_entorno_fijado.py` (5 pruebas):** ningún workflow
  puede instalar una herramienta sin `==`; las tres de la compuerta tienen pin
  exacto; y ningún atributo guardado con `hasattr` se lee con punto. **Antes del
  arreglo la primera fallaba nombrando las cuatro**: pyright, pip-audit,
  pyinstaller y pywebview. Parsea el YAML en vez del texto crudo a propósito —
  los comentarios de estos workflows citan el patrón prohibido para explicarlo, y
  un regex sobre el fichero entero obligaría a borrar la explicación.
- **Desfase de versión resuelto:** `pyproject.toml` decía `5.27.0` con el tag
  `v5.27.1` ya publicado. Detectado el 10-sep y sin resolver hasta hoy.

**Errata sobre la v5.27.0.** Aquellas notas decían que F2-F5 quedaba
«consolidado». Medido contra el código el 12-sep: los cuatro módulos existen y
sus tests de unidad pasan, pero **ninguno está conectado al orquestador**. La
única llamada a subagentes vive bajo un `elif False:` (`orchestrator.py:1137`);
el retorno de la compuerta F4 se descarta, así que nunca puede rechazar nada
(`orchestrator.py:1455`) y `ejecutar_compuerta_rapida()` no tiene llamador; el
plan de F3 nace entero en `pendiente`, nadie llama a `actualizar_estado()` y no
se inyecta en el prompt; y a Casper no se le ofrece el cuarto veredicto de F5,
que además caería en la rama de «tarea fallida». Los tests pasaban porque
inyectan las dependencias a mano y ninguno mira el orquestador: verde sin
comprobar. Se cablea en la v5.28.0, con pruebas sobre el orquestador real.

Dos erratas menores del mismo repaso: D2 no usa «umbral de latencia» sino **tasa
de respuestas inservibles** (50 %, mínimo 4 muestras, `motor.py:75`), y el
presupuesto web de F1 no es «por ronda» sino por tarea y de por vida del proceso
(`WebBudget.reiniciar()` no tiene llamador, `web.py:61`).

**SIN COMPROBAR:** pyright no está instalado en la máquina de desarrollo y no se
descargó para esto (el runtime de Node no cabe en el disco disponible). Que el
tipado quede en verde lo mide GitHub Actions; el pin apunta a la versión que ya
pasó sobre este mismo código, y `getattr` no puede producir ese error. El lint
completo local tampoco vale como medida: el ruff de esta máquina es 0.6.9 y el
del CI 0.16.5 — marca 17 `UP038` que el CI no marca, ninguno en el código de
esta versión.

**67 herramientas** en el catálogo. **1813 tests en Python** + **131 en
TypeScript/GUI**. Techos intactos: `kernel.py` 1069/1070, `orchestrator.py`
1534/1550, `ritsuko.py` 800/800, `builtin.py` 797/800; huérfanos en 80.

---

# v5.27.1 — Interfaz táctica Evangelion, desacople de Venim y auditoría ortogonal de YabauseVita

**Qué cambia:** Se aplica la paleta visual canónica de las supercomputadoras MAGI de Evangelion (naranja ámbar `#FF6600`/`#FFA726` y azul turquesa táctico `#00D2C4`/`#005953`), se rotula y desacopla definitivamente la identidad del sistema frente a Venim, y se audita el pipeline de medición ortogonal con Vita3K sobre YabauseVita (775 ventanas de 5 s procesadas, mediana 44.5 FPS).

**Lo concreto:**

- **Paleta visual Evangelion en `magi-gui`:** reemplazo de los tonos azules genéricos por naranja ámbar puro (`#FF6600`) para deliberaciones, avisos y acentos, y azul turquesa de terminal táctico (`#00D2C4`) para enlaces de nodos, monitores de datos y gráficos de rondas. Superficie militar abisal (`#03060a` / `#070d14`) con resplandores CRT.
- **Desacople de marca Venim:** cabecera con distintivo `EVANGELION TACTICAL` y aclaración explícita de autonomía en subtítulo de ventana.
- **Auditoría de integridad 100% verde:** suite de 1788 tests en Python, 131 tests en TypeScript, tipado estricto pyright a 0 errores y techos de líneas intactos.
- **Pipeline ortogonal de YabauseVita comprobado:** verificación determinista mediante `tools/vita3k_ctl.py` y `scripts/ronda_emulador.py` con perfilado de ciclos SH2/VDP y mediciones reales en disco.

**67 herramientas** en el catálogo. **1788 tests en Python** + **131 tests en TypeScript/GUI**. Techos de líneas intactos (`kernel.py` 1069/1070, `orchestrator.py` 1534/1550, `builtin.py` 798/800); huérfanos en 80 exactos.

---

# v5.27.0 — Enjambre v6, percepción web, degradación de motor D2, plan vivo y Lilim Mielina

**Qué cambia:** Se consolida el Enjambre v6 (subagentes por familia de modelo, plan vivo por tarea, compuerta obligatoria automatizada y veredicto de desvío), se añade percepción web completa sin navegador ni dependencias pesadas, degradación de motor `deep` → `fast` por salud de proveedores, clon shallow de repositorios con procedencia en journal, visibilidad de cancelaciones e hitos en la GUI, y la expansión **Lilim Mielina** con inferencia neuronal local (KoboldCpp + Qwen 2.5 1.5B GGUF Q4_K_M) y sentidos periféricos (Ojos/Lens con PyMuPDF, Oídos acústicos y Brazos de workspace).

**Lo concreto:**

- **Lilim Mielina & Sentidos Tridimensionales:** Inferencia neuronal local ultrarrápida adaptada al hardware anfitrión (Intel Core i7-3770 sin AVX2 + NVIDIA GTX 1050 Pascal 2 GB VRAM) usando runtime KoboldCpp (`koboldcpp-oldpc.exe` / `cu11_oldcpu`) y pesos Qwen 2.5 1.5B Instruct GGUF Q4_K_M (~986 MB VRAM). Acelerador dialéctico que actúa como vaina de mielina: pre-propuesta para Melchior, pre-auditoría estática AST en 0 ms y contraejemplos para Balthasar, matriz sintética para Casper, foco visual para Naoko y clasificación heurística para Ritsuko. Tríada sensorial periférica: Ojos (rasterización e inspección visual tipo Google Lens a 150-300 DPI con PyMuPDF), Oídos (análisis de cabeceras WAV/MP3/OGG e integración WASAPI) y Brazos (generación de informes Markdown con procedencia, exportación a DOCX y sellado de integridad SHA-256).
- **D2 — Degradación de motor por salud:** cuando los proveedores gratuitos de `deep` sufren degradación o timeouts excesivos, el motor degrada automáticamente a `fast` registrando el origen y alertando al operador sin bloquear la máquina.
- **L2 — `repos_clonar`:** clon shallow directamente al workspace con procedencia completa en el WriteJournal de la tarea (cumpliendo la compuerta A3).
- **F1 — Percepción web sin navegador:** herramientas `web_search` y `web_read` HTTP con presupuesto por ronda y formato obligatorio de cita con URL y fecha. Si no hay red, resultado explícito `SIN COMPROBAR`.
- **F2 — Subagentes por familia:** Melchior y Balthasar pueden invocar subagentes de solo lectura de su misma familia de modelo con turno único y conclusión sintetizada.
- **F3 — `plan.md` vivo por tarea:** seguimiento de hitos de la tarea (`pendiente`, `haciendo`, `hecha`, `no se pudo`) inyectado en el prompt y emitido a la interfaz vía `task.plan` con su tarjeta visual `PlanCard`.
- **F4 — Compuerta obligatoria automatizada:** antes de que Casper emita veredicto final, se ejecuta programáticamente la compuerta de verificación sin relanzar el ejecutable (uso estricto de `python_executable()`).
- **F5 — Veredicto «la pregunta era otra»:** cuando las propuestas debaten sobre un aspecto no pertinente, se declara el cuarto veredicto, guardando los hallazgos rescatables en `magi/data/memoria/descartes.jsonl`.
- **C1-GUI — Informe de cancelación visible:** el reporte real de procesos liquidados y bucles detenidos (`CancelReport`) se visualiza en la conversación.
- **L3-L4 — Lilim enciclopédico:** verificación periódica de novedades tecnológicas contra fuentes reales y enciclopedia técnica estructurada por dominios.

**67 herramientas** en el catálogo. **1788 tests en Python** + **131 tests en TypeScript/GUI**. Techos de líneas intactos (`kernel.py` 1069/1070, `orchestrator.py` 1534/1550, `builtin.py` 798/800); huérfanos en 80 exactos.

---

# v5.26.0 — Lilim multimodal: el motor EPD, el traductor y la memoria de novedades

**Qué cambia:** Lilim pasa de índice a MOTOR: el ciclo encode→prefill→decode
de GLM-5.3-Flash traducido a un i7-3770 sin GPU — activación escasa reportada,
caché lineal de sesión y hechos multimodales deterministas. Y gana las tres
memorias que le pidieron: terminología en 6 idiomas, novedades tecnológicas
2023-2026 falsables, y el canal de contexto para todo el enjambre.

**Lo concreto:**

- **`lilim/motor.py` — el ciclo EPD local.** encode (qué dominios activa la
  pregunta) → prefill (atención lineal: el estado de sesión manda) → decode.
  Cada respuesta declara su métrica MoE: «[activado 1/4 dominios, 39,2 % de
  la memoria, 0,7 ms]». Medido en la máquina objetivo: respuestas de
  0,0-3,5 ms, caché a 0,0 ms, cero GPU, cero red, un solo hilo.
- **`lilim/rapida.py` — hechos multimodales deterministas.** Formato,
  dimensiones (cabeceras PNG/JPEG/GIF/BMP sin dependencias) y sha256 de
  cualquier fichero. El contenido SEMÁNTICO de una imagen se escala con la
  nota impresa — no se inventa.
- **Traductor de 6 idiomas** (`lilim_traduce`): es/en/de/ru/ja/zh sobre la
  memoria de terminología técnica (`idiomas.json`, 20 términos del dominio),
  con procedencia. Las palabras que no están se dejan tal cual y se dice que
  la FRASE completa va por el puente de nube — Lilim no inventa traducciones.
- **Novedades 2023-2026** (`lilim_novedades`): semilla de 13 hitos — GPT-4,
  Llama 2, AlphaFold 3, DeepSeek R1, baterías de sodio de CATL y la IA en la
  fábrica de baterías china (BYD/CATL), estado sólido, Switch 2, cierres
  legales de Yuzu/Ryujinx, Python free-threaded — cada una con su
  `fuente_verificar` y su bandera SIN COMPROBAR: la regla de la casa.
- **`lilim_ensenar` (M4)** — el enjambre guarda conocimiento VERIFICADO con
  su URL; sin URL no entra. La próxima consulta sobre ese tema se responde
  local, en ms.
- **CONTEXTO LILIM inyectado**: los tres nodos arrancan con los hechos
  locales de memoria que tocan a su encargo — empiezan sabiendo, no
  descubriendo. Si Lilim cae, la inyección sigue sin ella.
- **A2 con marca por fichero**: los exe de ventana (windowed) no tienen
  consola y su print muere en stdout nulo — la marca del autotest puede
  viajar en `autotest_ok.txt` junto al binario. Hallado con el propio
  fixture de Tetris; los tres builds slow en verde con ella.

**63 herramientas** (de 55 hace tres días). Suite completa a cero; techos
exactos; huérfanos en 80; ruff limpio.

---

# v5.25.0 — LILIM, la capa local superveloz, y la memoria enciclopédica falsable

**Qué cambia:** nace LILIM (megaplan v12, docs/MEGAPLAN-v12-lilim.md): la
capa LOCAL, determinista y sin red que responde al instante lo que MAGI ya
sabe — CON procedencia — y escala al enjambre lo que no sabe. Más
velocidad y más eficiencia sin perder precisión: lo que Lilim no está en su
memoria, NO lo inventa.

**Lo concreto:**

- **`magi/modules/lilim/`** — el núcleo: responde mandos de 17 consolas,
  configuración de PC para jugar, decompilación/puertos estilo dusklight
  (el flujo en 5 pasos hasta el port a Vita) y el índice de repos, todo con
  `fuente:` y `falsable contra:` en cada respuesta. Ante lo desconocido:
  «NO LO SÉ (local) — escala al enjambre». Cero red, cero cuota.
- **`repos_top.json`** — el índice curado de los mejores repos de GitHub
  para MAGI (emudev, decomp, gamedev, vita, tooling, ia): METADATOS con URL
  falsable, no clones — un repo se clona shallow a demanda al workspace
  (L2), nunca se descarga internet (C: tiene ~10 GB).
- **`lilim_pregunta` y `repos_de`** como herramientas del enjambre (58
  ahora): cualquier nodo puede consultar la memoria local en ms mid-tarea.
- **Controles ampliados**: PS2 (la fuente habitual de decomp estilo
  dusklight), `pc_jugando` (convención de teclado extendida, gamepad
  xinput/SDL/Steam, la regla de oro de los ports: UNA capa de entrada
  abstracta mapeada por plataforma) y el bloque completo
  `decompilacion_y_puertos` (split, matching con objdiff, byte-matching,
  recompila en PC, port con vitasdk).
- **Inyección selectiva**: el conocimiento de decomp entra al prompt SOLO
  cuando el encargo habla de eso (regex dedicada) — conocimiento denso en
  cada prompt sería ruido.
- **Auditoría de modelos**: g4f alineado al lock (8.1.1, paridad con CI) —
  los parches de compat aplican limpios y la sonda midió 32/32 candidatos.

---

**7 pruebas nuevas** (el contrato de Lilim: procedencia, repos con URL,
y las tres reglas del no-inventar). Suite completa a cero; huérfanos en 80;
58 herramientas declaradas y contadas.

---

# v5.24.0 — A2: los juegos se entregan jugados, no solo compilados

**Qué cambia:** el segundo bloque crítico del megaplan v11. La misión Tetris
entregó un .exe con la tecla de reinicio rota y «tests en verde» — porque
los tests solo comprobaban que el fichero existía. Ahora un juego se
verifica JUGÁNDOLO.

**Lo concreto:**

- **`magi/modules/studio/interactivo.py`** — el autotest de un juego lanza
  el artefacto con `--autotest` y decide en TRES estados: `verde` (código 0
  Y la marca GAME_AUTOTEST_OK), `rojo` (corrió y no llegó: el juego no
  recorrió su ciclo con teclas) y `sin_comprobar` (cuelgue o exe de ventana
  sin consola — la regla de la casa: no comprobado NO es roto).
- **El packager lo ejecuta tras compilar** cualquier proyecto pygame/
  tkinter: `rojo` frena la entrega con la salida del juego; `sin_comprobar`
  avanza avisando «SIN COMPROBAR — no cuenta como probado».
- **El contrato de aceptación lo exige de antemano** (criterio nuevo junto
  al de `--autotest 200`): el juego debe simular SUS teclas dentro del
  autotest — mover, rotar, forzar game over, pulsar su reinicio — y solo
  entonces imprimir la marca.
- **Verificado contra los propios tests del repo**: los dos fixtures pygame
  tuvieron que volverse conformes — A2 cazó a su primer juego en el primer
  build. La compuerta completa quedó en verde con la suite entera a cero.

**Nota:** también se releyó el hallazgo T8 de la misión: PARAR ESTA sí
funciona cuando hay bucle vivo; lo que pareció «no para nada» era la espera
de aprobación (bucle ya cerrado por diseño) sobre una tarea zombi. El plan
de procedencia (A1/A3) y esta A2 cierran el ciclo de entrega honesta; C1
queda reformulado como honestidad del informe de cancelación (el informe ya
dice la verdad — falta pintarlo mejor en la GUI).

---

**4 pruebas nuevas** (los tres estados del autotest + lanzamiento
imposible). Suite completa a cero fallos; huérfanos en 80; techos intactos.

---

# v5.23.1 —

**Errata (v5.23.1):** el release v5.23.0 se etiquetó antes de que la
compuerta local terminara y el CI lo tumbó con razón: el E2E del Tetris
compilaba sin journal. Arreglado registrando el fuente como hace un agente
real tras `write_file` — y de paso el test verifica ahora el manifiesto A1.

# v5.23.0 — el plan v11 en acción: comandos, procedencia y compuerta sin humo

**Qué cambia:** se ejecutan los bloques críticos del megaplan v11 — los que
la misión Tetris (5-sep) destapó operando MAGI en vivo. Cada arreglo nace de
un fallo reproducido y trae la prueba que lo caza.

**Lo concreto:**

- **B1 — los comandos escritos se EJECUTAN, no se debaten.** `task.cancel
  [id]`, «parar todo», `EMERGENCY_STOP` y «emergencia» tecleados en el input
  se interceptan y ejecutan como órdenes (magi/core/comandos.py). Era el
  agujero por el que «task.cancel X» arrancó una tarea que propuso
  desregistrar una tarea programada de Windows.
- **A1 — procedencia obligatoria de artefactos.** Toda compilación deja
  `<exe>.manifest.json` con el sha256 del binario y de cada fuente .py del
  proyecto. El tetris.exe del 5-sep no se podía regenerar de lo que el
  sistema guardaba: eso no vuelve a pasar sin quedar escrito.
- **A3 — código antes del build.** `build_project_exe` se niega si la tarea
  no escribió ningún .py (journal): el 5-sep se invocó dos veces sin un solo
  fichero y el binario nació huérfano.
- **C3 — a un producto sin artefacto ni código NO se le pregunta «¿apruebo?»**
  — la ronda se cierra con el motivo (era lo que hizo preguntar humo con
  «0 fichero(s) · tests en verde»).
- **D1 — el INFO de infraestructura deja de inundar el Terminal** (fallbacks
  de proveedores, canarios, sonda): solo WARNING+ sube al bus. El panel
  vuelve a ser legible y el guardián de rondas dormidas deja de estar
  enmascarado por el spam.
- **B3 — SYS_EXEC ▾ ya no es decorativo**: despliega Parar esta tarea /
  Parar TODO / escribir task.cancel.
- **C2 — aprobar o cancelar lleva motivo**: si hay texto en el input, viaja
  con la decisión (un «cancelar» desnudo dejó a Melchior sin saber por qué).

---

**15 pruebas nuevas** (comandos de input ×6, manifiesto, journal por tarea,
humo ×3, filtro de ruido ×3). Suite completa en verde con .exe incluidos;
`kernel.py` 1070/1070 y `orchestrator.py` 1549/1550 sin subir techos; ruff
limpio; huérfanos en 80; 131 tests de interfaz.

---

# v5.22.0 — la misión Tetris: MAGI operado en vivo por una persona, de punta a punta

**Qué cambia:** esta versión no añade una función — documenta y planifica.
Una persona operó MAGI v5.21.1 en vivo (teclar, pulsar botones, aprobar,
rechazar, parar) con el encargo «crea un Tetris en un .exe portable», jugó
el artefacto resultante, y todo lo aprendido quedó en
docs/MEGAPLAN-v11-tetris.md: 11 hallazgos con evidencia, la comparativa con
cómo lo habría hecho un agente con manos, y el plan A-E para cerrar cada
uno.

**Lo que la misión destapó (resumen; el detalle y su evidencia en el megaplan):**

- El .exe entregado JUEGA (gravedad, colisiones, score, game over) pero su
  reinicio (tecla R) está roto y su fuente no está donde el sistema lo
  guardó — el artefacto no tiene procedencia. T1/T5.
- El build se invocó antes de escribir código; la propuesta era prosa sin
  ficheros y aun así llegó a preguntar «¿apruebo?». T2/T3.
- `task.cancel` tecleado arrancó una tarea que propuso DESREGISTRAR una
  tarea programada de Windows (frenada por Balthasar). T6.
- SYS_EXEC es decorativo; PARAR no corta llamadas en vuelo; el Terminal
  inunda el bus y enmascara al guardián; ronda profunda >45 min sin salida.
  T7-T11.

**El plan (bloques A-E):** procedencia obligatoria de artefactos (manifiesto
con hashes de binario y fuentes), autotest de INTERFAZ para juegos (teclas
simuladas — habría cazado el reinicio roto), código-antes-del-build,
comandos reconocidos en el input + SYS_EXEC desplegable de verdad, PARAR que
para de verdad, rechazo con motivo, compuerta que no pregunta humo, canal de
infraestructura separado y degradación de motor por salud.

**Verificado en vivo durante la misión:** el E-STOP desde la GUI, el pie con
«⏸ ESPERA TU APROBACIÓN», el recon de Balthasar en paralelo, la crítica
multi-eje 4/4, la réplica con concesión, y Balthasar frenando una propuesta
peligrosa con 2 objeciones de seguridad.

---

**0 pruebas nuevas** — esta versión es el plan; las pruebas nacen con cada
paquete (la compuerta de cada uno exige reproducir el fallo que cura).
Suite completa en verde al publicar.

---

# v5.21.1 — el pase de Balthasar: la interfaz puesta a prueba a si misma

**Qué cambia:** la reconstruccion de la v5.21.0 se examino a si misma
ejecutandola como la usaria una persona, y cinco fallos reales cayeron —
incluido uno que hacia invisible la decision mas importante del sistema.

**Los cinco, con su evidencia:**

- **La aprobacion quedaba detras del cajon cerrado.** Mi cirugia movio los
  paneles a un cajon plegable y los efectos que cambiaban de pestana al
  llegar una aprobacion seguian cambiandola A CIEGAS: el diff de la decision
  que bloquea el sistema, invisible. Ahora la aprobacion ABRE el cajon, y
  la linea de estado anuncia «ESPERA TU APROBACION».
- **«Ir a X» de la paleta era una orden muerta** con el cajon cerrado.
  Ahora lo abre.
- **Las rutas del flujo son clicables** (principio #7 de la
  deconstruccion): «docs/BITACORA-OPTIMIZACION.md» en cualquier mensaje se
  convierte en enlace, y clicarlo abre el panel Codigo con el fichero.
  Dos lecciones del camino: el sanitizador de react-markdown tumba el
  esquema open: (urlTransform lo respeta ahora), y el backend exigia rutas
  absolutas — las relativas se resuelven contra el workspace.
- **El cajon recuerda su estado** entre sesiones (localStorage).
- **App.tsx volvio a su techo extrayendo** MotoresPanel — cuya tabla de
  modelos era decorativa (nombres escritos a mano junto a telemetria viva);
  el panel ahora usa el reparto real.

**Verificado en vivo:** mencion mandada por la GUI, ruta subrayada en la
burbuja, clic, panel Codigo abierto con el explorador.

---

**9 pruebas nuevas** (6 de rutas/lib, 2 de componente con renderToString,
3 de resolucion relativa en el handler). Suite Python completa en verde,
131 tests de interfaz, tsc y build limpios, App.tsx 839/900, huerfanos 80.

---

# v5.21.0 — la conversación es la columna vertebral

**Qué cambia:** la interfaz se deconstruyó y se reconstruyó desde cero con
el paradigma del agente que la supervisa (docs/DECONSTRUCCION-INTERFAZ.md:
los diez principios operativos, qué se adoptó, qué queda para v2). Y el
arranque de ronda ya no puede colgarse por un proveedor muerto.

**Lo concreto:**

- **Doce paneles dejan de competir por pantalla.** La tercera columna fija
  (Plan, Código, Vista previa, Terminal, Naoko, Ritsuko, Configuración,
  Gráfico, Motores, Coste, Sistema, Mejoras) pasa a ser un **cajón lateral
  plegable**, cerrado por defecto; la conversación gana el ancho que le
  correspondía. Colores e icono intactos: es cirugía de arquitectura de la
  información, no de identidad.
- **Pie honesto**: pasa de texto decorativo a línea de estado operativa —
  conexión real, motor, tarea activa y versión del kernel, siempre visible.
- **El guardián de la ronda dormida se pinta**: `ritsuko.ronda_dormida`
  llega al panel de Ritsuko como mensaje de auditoría con tareas y
  segundos. El trinquete del mapa de interfaz lo exigió al nacer invisible
  — y tenía razón: una alarma que nadie ve no es una alarma.
- **El estilo ya no puede colgar el arranque**: tope de 20 s a la llamada
  de Naoko; pasado, el estilo de la interfaz y la ronda arranca. Medido en
  vivo la misma mañana: «estilo decidido por fallback» y tarea iniciada al
  momento, contra la madrugada en la que un proveedor muerto dejó el
  arranque en suspenso para siempre.

**Verificado en vivo:** la ronda supervisada de optimización del emulador
arrancó con la GUI reconstruida puesta y el motor rápido seleccionado.

---

**6 pruebas nuevas** (tope de estilo, guardián, deconstrucción). Suite
completa en verde incluidos los .exe, 122 tests de interfaz, tsc y build
limpios, huérfanos en 80, ningún techo subido.

---

# v5.20.0 — autonomía: el encargo de una persona común no puede salir devorado

**Qué cambia:** la sesión supervisó a MAGI usándolo como lo usaría una
persona — mandarle mejorar el emulador con las tres filosofías y mirar. El
fallo que eso destapó era el más grave posible para la autonomía, y la
filosofía C quedó libre por medición.

**Lo concreto:**

- **El devorador de encargos, cazado en vivo.** Una tarea zombi pendiente de
  aprobación CAPTURABA el siguiente comando del usuario como su respuesta:
  «optimiza… propon una mejora por cada filosofía…» contenía «mejora» y la
  regla de revisión, por SUBCADENA, se lo comía entero. El encargo nuevo
  jamás arrancaba y nadie veía por qué. Ahora el verbo de revisión tiene que
  mandar la frase (cabeza o mensaje corto), «optimiza/propon/implementa»
  entran en la lista de encargos, y una persona que no sabe que existe una
  aprobación pendiente ya no pierde su trabajo en ella.
- **La filosofía C (repartir mejor) queda LIBRE por medición.** A9 cayó:
  `drawn=94 presented=94 dropped=0` a 34,7 FPS, impresos por
  `VIDGPUVdp2LogTiming` y capturados por `vita3k_ctl` — la línea existía
  desde el 18-jul y A9 describía una build anterior. Bitácora del emulador
  actualizada con la medición; A y B siguen suspendidas por R6 (render =
  1,27 %, A7).
- **El motor se adapta al encargo.** Los triviales («crea holamundo.py…»)
  arrancan al instante: sin la llamada LLM de estilo (3-22 s) y en modo
  rápido — medido antes: 20+ min para un hola mundo. Determinista,
  conservador (ante la duda, nada es trivial) y solo baja la marcha: a
  quien pidió prisa no se le sube.
- **README reescrito** con la sección de autonomía y las cifras reales.

**Supervisión con ojos:** 8 pestañas pulsadas sin errores, tres envíos del
encargo real (los dos primeros enseñaron el devorador), capturas de cada
estado, y la ronda de optimización arrancando por fin con las tres
filosofías sobre la bitácora viva.

---

**7 pruebas nuevas** (motor trivial, devorador de encargos). Suite completa
en verde con `verificar.py --todo` incluidos los .exe; ruff limpio;
huérfanos en 80; `kernel.py` 1070/1070 sin subir el techo.

---

# v5.19.0 — auditar el sistema usándolo, y los fallos que solo así se ven

**Qué cambia:** la sesión empezó ejecutando el megaplan v10 y siguió
auditando MAGI usándolo como lo usa el usuario: abrir la interfaz, mandar una
tarea real y mirar lo que sale. Cuatro fallos que ningún test veía salieron
así, C6 se resolvió porque su precondición se cumplió sola, y Ritsuko ganó
los dos roles que el megaplan v9 le tenía reservados.

**Lo concreto:**

- **El pie de página decía «v3.0» con el sistema en la 5.18.** Una constante
  del GUI que nadie actualizaba. Ahora la versión la dice el kernel
  (`sys.config` → `version`), y el test del contrato RPC lo vigila.
- **«Vista previa» nacía rota.** El panel pedía los artefactos al montarse,
  ANTES de que el WebSocket abriera: «sin conexión con el kernel» quedaba
  pegado para siempre junto a un «Leyendo el workspace…» que ya no era verdad.
  Recarga cuando la conexión llega, y mientras no hay datos no dice que está
  leyendo.
- **C6, resuelto tras 14 días aplazado** — su precondición era «reproducible»,
  y una ronda real lo reprodujo: `HuggingSpace: TypeError: argument of type
  'NoneType' is not iterable` con la familia `hf` dada por agotada, y quince
  minutos después el MISMO HuggingSpace respondiendo con normalidad por otra
  familia. Causa raíz: g4f 7.9.4 deja `model_aliases = None` y hace `model in
  provider.model_aliases`. El parche de compat rellena con `{}` los alias que
  falten —sin pisar los reales—, igual que el de `JsonConversation`.
- **`pertinente()` colaba por nombrar la consola.** Medido: 3 de 10 encargos
  de prueba («acelera la web que muestra la velocidad de la Vita», «artículo
  sobre la velocidad del Sega Saturn», «dashboard del rendimiento de Saturn»)
  se habrían repartido en tres filosofías del camino de render, con reglas de
  otro proyecto encima. `vita` suelta deja de bastar y una guardia de
  entregable ajeno (web/artículo/dashboard/…) corta el resto. Los positivos
  históricos quedan como control del test.
- **R7 entra en `REGLAS`**: «no leer `GPU timing` como µs por fotograma» era
  detectable en el texto de una propuesta y no estaba. Exige un VALOR con
  unidades («1250 µs por fotograma»), así que citar la regla honestamente
  («no son µs por fotograma») no es flagrado.
- **El comentario de `Regla.exige` prometía un contador de estorbo que no
  existía.** Corregido a lo que hay: el diseño de `exige` ES la mitigación.
  Un contador sin rondas reales que contar sería una promesa, no un mecanismo.
- **Ritsuko R2 — el reloj que el usuario percibe.** Dos relojes: el eco del
  chat de Naoko (>2 s es hallazgo; el caso real del 23-ago tardó 10,6 s y el
  detector fue el usuario con una captura) y el arranque de ronda (>30 s es
  «recibido y sin empezar». Una cola que AVISA que es cola no es cuelgue).
- **Ritsuko R4 — segunda firma en las entregas.** Al cerrar una tarea firma
  lo que constaba: VERIFICADA (hay artefacto), DECLARADA_INCOMPLETA (el
  sistema avisó) o SIN_ARTEFACTO (la evidencia no está — hecho auditable, no
  acusación). Viaja como `ritsuko.firma_entrega`, nunca como orden.

## Lo que se midió usándolo de verdad

La tarea real («crea holamundo.py que imprima los 25 primeros números primos,
ejecútalo») recorrió el pipeline entero con ojos: dossier del abanico recogido
mientras Melchior redactaba, `write_file` y `run_command` OK, crítica
multi-eje 4/4, réplica con concesión real («reemplazar `y` por `and`»),
failover en vivo de familia (command → hf → gemini) y el guardián
`no_browser` bloqueando un Chrome que g4f intentó abrir. El eco del usuario
fue instantáneo: el arreglo G de la v9 sigue en pie.

## Lo que la auditoría dejó anotado y NO se tocó

- **La sonda sigue midiendo con tareas vivas**: canarios a los cuatro minutos
  de arrancar la ronda. Es el conflicto del v9 §3; G4 lo mitiga (tregua de
  arranque) pero no lo elimina. Es exactamente el trabajo de R3: Ritsuko,
  portera de la sonda.
- **Una tarea trivial en modo profundo tardó 20+ minutos** con proveedores
  gratuitos. No es un fallo: es el modo elegido. Pero Naoko podría clasificar
  «trivial» y bajar a `fast` — anotado como mejora futura, no construido.

---

**15 pruebas nuevas** entre filosofías (casos borde negativos de
`pertinente`, R7 y su negación honesta), compat (regresión de HuggingSpace),
RPC (la versión en `sys.config`) y Ritsuko (los dos relojes y la firma).
Suite entera en verde, ruff limpio, huérfanos en 80, `ritsuko.py` en 800/800
y `orchestrator.py` en 1550/1550 sin subir ningún techo.

---

# v5.18.0 — las tres filosofías dejan de depender de la semilla

**Qué cambia:** la §2 de la bitácora del emulador exige que las tres
propuestas de cada ronda sean «tres formas distintas de atacar el mismo
cuello, mutuamente excluyentes por diseño». Melchior las diversificaba con
`seed + n*101`. Eso da tres redacciones, no tres ataques: nada impedía que
las tres recortaran `composite` con otras palabras, y la ronda gastaba tres
compilaciones para medir una sola idea.

Ahora se **asignan**. La variante 0 ataca `composite`, la 1 `upload`, la 2
`dropped`, cada una con su riesgo característico y su predicción falsable.

**Lo concreto:**

- **Tres variantes, no dos.** `_n_variantes` devuelve 2 desde D6, y con 2 la
  filosofía C —«repartir mejor»— **no se exploraría nunca**: `asignada`
  cicla, así que 0 y 1 se llevan A y B y C se queda fuera siempre. El
  mecanismo habría dicho «ortogonal» ignorando un tercio de sus ejes. D6 midió
  variantes diversificadas por semilla —tres textos de la misma idea—, que es
  justo el desperdicio que esto viene a quitar.
- **§6 de la bitácora, implementada.** Decía: «si alguna choca con una regla
  de §5.2, se rechaza sin llegar a compilar». No lo hacía nadie. Ahora R1,
  R6, R14 y R15 se comprueban sobre el texto de cada propuesta y el choque
  llega **pegado** a la propuesta que lo comete, no en un resumen al final.
  Rechazar sin compilar es lo que ahorra el ciclo: un `.vpk` y una corrida
  verificada por propuesta.
- **Las tres están suspendidas hoy, y el prompt lo dice.** R6 tapa A y B
  (el camino de render es el 1,27 % del tiempo, A7); A9 tapa C, cuya métrica
  `dropped` ni siquiera se imprime en el log. Cada variante recibe qué regla
  la suspende, qué haría falta para levantarla, y la instrucción de proponer
  en su lugar lo que levantaría la suspensión. **Un mecanismo que produce
  tres propuestas prohibidas en silencio es peor que uno que no produce
  ninguna.**
- **Y el reparto se revisa a posteriori.** `revisar` mira lo que las
  variantes hicieron de verdad, no lo que se les pidió. Si colapsan en el
  mismo mecanismo, Balthasar recibe el aviso: comparar tres versiones de la
  misma idea no informa de nada.

## Dos cosas que encontré probando lo que ya había escrito

**Escribí el test esperando dos choques con R6 y el comprobador dijo tres.**
Tenía razón: la filosofía C mueve el `composite` entre núcleos, y eso sigue
siendo camino de render. Es exactamente lo que la ronda 0 concluyó midiendo
—«las tres filosofías quedan en suspenso»—, redescubierto solo desde el texto
de las propuestas. La expectativa equivocada era la mía.

**Y las reglas del emulador se aplicaban a rondas que no son del emulador.**
R6 exige `(composite|upload|display)` más un verbo de optimizar, así que
disparaban las tres: «optimizar el display del Tetris», «reducir el tiempo de
upload al servidor», «acelerar el display en la web». Balthasar habría
recibido la orden de rechazar sin compilar una propuesta válida citando una
regla de otro proyecto. Las reglas quedan acotadas a las rondas que reparten
filosofías. Una regla que bloquea trabajo bueno se desactiva sola a la tercera
vez que estorba.

## Que no se trabe

El fan-out pasa de 2 a 3 ramas concurrentes, así que se prueba lo que ahí
puede salir mal: una variante que revienta no se lleva a las hermanas; si
revientan las tres se falla rápido en vez de esperar; y las tres esperas se
solapan, medido **contra un control en la misma corrida** (n=1 frente a n=3).

Ni un umbral absoluto en todo el fichero. El único número de reloj es un tope
anticuelgue de 20 s sobre un trabajo de 0,3 s, que solo distingue «terminó» de
«se quedó esperando para siempre» — la lección de la v5.17.1, donde
`t_melchior_ms < 900` medía el runner y no el código.

---

**18 pruebas nuevas**, incluida una ronda completa por el orquestador real:
encargo → reparto → tres prompts distintos → propuesta fundida → Balthasar.
Suite entera en verde, ruff limpio, huérfanos en 80, `orchestrator.py` en
1550/1550 sin subir el techo.

---

# v5.17.1 — dos cosas que dije y no eran ciertas

Ninguna de las dos era del mecanismo. Las dos eran de cómo lo estaba midiendo,
que es peor: un instrumento que miente no avisa de que miente.

## 1. Un test de tiempos que medía el runner, no el código

`test_el_recon_cabe_en_la_ventana_de_melchior` afirmaba `t_melchior_ms < 900`.
El runner de `windows-latest / 3.10` midió **4531** y tumbó el CI.

El arreglo no era subir el umbral. Un umbral absoluto **no distingue** «el
recon se absorbió» de «la máquina va cargada», así que no puede decidir nada.

Y la prueba de que el mecanismo estaba bien la di yo al medirlo otra vez en
esta misma máquina, donde había pasado: **1218 ms**. Por encima de los 900 que
el test exigía. O sea que el umbral tampoco valía aquí — las veces que pasó
fue por suerte, no porque midiera algo. Los dos jobs de Ubuntu pasaron, y el
`lint` y el `gui` también; cayó solo `windows-latest / 3.10`, que es el runner
más caro y más cargado de la matriz. El número que cambiaba era el de la
máquina, no el del código.

Reescrito contra un **control medido en la misma corrida**: el mismo montaje
con el recon a 5,0 s en vez de 0,3 s. Si el recon corriera dentro de la
ventana de Melchior, la fase crecería ~4,7 s. Medido: **decrece 578 ms**, que
es la firma de un recon cancelado por llegar tarde. Un runner lento escala los
dos lados igual, así que la comparación sigue diciendo lo mismo.

Y buscando hermanos apareció otro con el mismo defecto en la misma fase —
`pared_con < 3.0`— que habría caído en el push siguiente. Retirado: la
comparación relativa de la línea de al lado ya cubría el caso entero, así que
la constante solo aportaba fragilidad.

Es la regla **R12**, la que se aprendió midiendo input en el emulador: *se mide
contra un control, no contra una constante*. La tenía escrita y la incumplí.
Está anotada en el automodelo como afirmación **refutada**, con su evidencia.

## 2. El registro de la compuerta estaba sucio, y yo dije que estaba vacío

Las notas de la v5.17.0 decían que `replica.jsonl` estaba «vacío a propósito».
No lo estaba: llevaba **10 filas** con `task_id: "t-r"`.

Yo mismo había quitado dos a mano días antes, y volvieron. Esa era la señal y
no la leí: quitar el síntoma sin cerrar la fuente no arregla nada. La fuente
era que `_ronda` solo aislaba `MAGI_MEMORIA` **cuando el test quería leer el
registro**; las otras tres rondas escribían en el fichero del repo, dos filas
por cada corrida de la suite.

Importa porque ese fichero es la única evidencia que puede **retirar** la
réplica. Medido sobre datos de prueba, mide la prueba — que es literalmente lo
que la propia cabecera del fichero prohíbe.

Ahora el aislamiento es el valor **por defecto**, no una opción, y hay una
prueba que falla si el fichero vuelve a ensuciarse. Comprobado como se
comprueba una compuerta: ensuciándolo a propósito para ver que salta, y
corriendo la suite entera después para ver que ya no entra nada.

---

**1632 pruebas, cero fallos**, ruff limpio, huérfanos en 80. Sin cambios de
comportamiento: las dos fases hacen lo mismo que en la v5.17.0.

---

# v5.17.0 — el enjambre deja de esperar en fila, y Melchior contesta

**Qué cambia:** las dos fases que quedaban del plan de rendimiento y calidad.

**Lo concreto:**

- **Fase 7, abanico paralelo.** Se solapa lo que no depende: la recogida de
  evidencia con la redacción de la tesis, las variantes entre sí, los ejes de
  crítica entre sí, y la verificación **en cascada** según llegan las variantes.
  Medido a través del orquestador real: **3141 ms → 1937 ms, un 38 % menos**.
- **38 %, no el 66 % que yo había prometido.** Ese 66 % salió de un banco
  sintético de tres esperas independientes; la ronda real tiene una dependencia
  irreducible —Balthasar no puede refutar una tesis que aún no existe— y esa no
  se paraleliza. Cuando la medida sintética y la del sistema real discrepan,
  gana la del sistema real.
- **`MAGI_ABANICO=0`** vuelve al modo serial. Una optimización sin forma de
  apagarla no se puede comparar consigo misma.
- **Fase 8, la réplica.** Melchior contesta a la objeción **antes** de que
  Casper arbitre. Condicional (solo si hay objeciones reales, firmadas con
  `OBJECIONES: N`), acotada (1400 caracteres de extracto, 900 de réplica, sin
  herramientas), una sola vuelta, y con salida: si empieza con `CONCESIÓN:`, el
  debate cierra antes del arbitraje.
- **Su compuerta viene armada.** `MAGI_REPLICA_SOMBRA=1` corre el arbitraje
  contrafactual y anota si el veredicto cambió. Si Casper no cambia al menos 1
  de cada 5, la réplica se retira. El registro está **vacío a propósito**: aún
  no ha corrido ninguna ronda real, y una compuerta medida sobre datos de
  prueba mide la prueba.
  > **Errata (v5.17.1).** Esa última frase era falsa cuando se escribió: el
  > fichero llevaba dentro 10 filas de prueba. Corregido en la v5.17.1, con la
  > fuente cerrada y un guardián que lo impide.

- **El techo de líneas obligó a mejorar el diseño.** `orchestrator.py` estaba a
  7 líneas de su tope: se extrajo `contraste.py` y la mecánica del cierre pasó a
  `replica.py`. Quedó más corto que antes con más funcionalidad.
- **El trinquete de huérfanos señaló tres piezas sin sitio de llamada externo.**
  No se escondieron haciéndolas privadas: se les escribió prueba directa, y ahí
  aparecieron sus modos de fallo — si la réplica revienta la ronda se arbitra
  sin ella, y la réplica trabaja sobre una copia para no dejar a Melchior con
  `hedge=False` el resto de la ronda.

**27 pruebas nuevas** entre las tres fases. Ruff limpio, huérfanos en 80,
suite completa en verde antes de publicar.

---

# v5.16.0 — buscar sin gastar red, y saber qué de uno mismo es falso

**Qué cambia:** dos capacidades que no consumen cuota, y una corrección honesta
de lo que yo mismo había propuesto en el megaplan.

**Lo concreto:**

- **`magi/modules/memory/indice.py`** — FTS5 sobre bitácora, memoria, docs y
  código. Responde «¿esto ya se intentó?» **sin gastar una llamada de red**.
  Medido sobre el corpus real: 224 documentos, 2,7 MB, índice completo
  reconstruido en **100 ms**, consulta en **1 ms**.
- **Sin persistencia, sin embeddings, sin GPU — y eso es la decisión, no una
  limitación.** Reconstruir cuesta menos que razonar sobre si el índice está al
  día. Y proponer un modelo de 90 MB para buscar en 2,7 MB era sobre-ingeniería
  mía: la Fase 9 del megaplan queda **retirada**, con su motivo escrito.
- **Buscar `1.27` daba `fts5: syntax error near "."`.** Un buscador que falla
  cuando le pasas un número es un buscador que nadie usa dos veces. Ahora sanea
  la consulta y conserva `AND`, `OR`, `NOT`, `NEAR` y las comillas.
- **`magi/modules/swarm/automodelo.py`** — lo que MAGI cree de MAGI, con la
  prueba que puede tumbarlo. **Una afirmación sin prueba no se admite**: «soy
  bueno razonando» es una opinión, no una afirmación sobre uno mismo.
- **Sembrado con esta sesión: 8 afirmaciones, 4 refutadas por la realidad.** El
  dynarec no arranca (tres builds), NiGHTS no llega al título, el experimento de
  input no aísla la pulsación — y *«corro la compuerta antes de publicar»*, que
  es sobre quien escribe esto y la desmintieron cuatro rebotes en un día.
- **`sin_comprobar` es un estado de primera clase**, no «verdadera hasta que se
  demuestre lo contrario». Y al prompt solo viaja lo refutado y lo frágil: lo
  que se sostiene sin fallar ocupa contexto y no cambia ninguna decisión.
- **Sexta inyección.** La secuencia pasa a aceptación → caja → bitácora → ronda
  → memoria → automodelo, con su test de lista exacta.

Cierra el hueco que la Ronda 0 dejó al descubierto: cuando la medición invalidó
el plan entero, el sistema no tenía dónde anotarlo.

**32 pruebas nuevas.** Ruff 0.16.5 limpio, huérfanos en 80, trinquetes en verde.

---

# v5.15.0 — vista: leer la pantalla como la lee una persona

**Qué cambia:** R9 dio ojos («¿hay algo y se mueve?») y R16 oídos. Ninguno de
los dos sabía **qué** estaba pasando en pantalla. Ahora sí.

**Lo concreto:**

- **`magi/modules/percepcion/vista.py`** con `classify_screen` registrada en el
  enjambre. De una captura saca: en qué clase de pantalla estamos (negro,
  carga, licencia, menú, título, partida), en qué **idioma** habla el juego, y
  **qué botón está pidiendo** — validado contra la memoria de mandos de esa
  consola, para no mandar al agente a pulsar una tecla que el mando no tiene.
- **«NiGHTS se queda en la licencia» pasa a ser comprobable.** En la Ronda 2
  eso hubo que averiguarlo mirando una captura a mano y describiéndola; ahora
  lo dice una función.
- **`Zonas`: FPS por clase de pantalla.** «Va lento» sin decir dónde no es
  diagnóstico: un juego a 60 en el menú y a 17 en partida tiene media 38, y 38
  no ocurre nunca. El informe señala la zona lenta y **avisa por escrito** de
  que su propia media entre clases no describe ninguna pantalla.
- **El idioma se detecta con confianza declarada.** «START» acierta en inglés y
  en nada más, pero un empate a uno entre dos idiomas es una moneda al aire, no
  una detección: la confianza mide la distancia al segundo candidato, no los
  aciertos. Y el japonés se detecta por escritura (kana), no por palabras.
- **Un fallo que solo aparece con OCR real.** Tesseract leyó «PULSA START PARA
  JUGAR» como `PULSASTARTPARA JUGAR`. Un patrón con `\b` detrás del verbo no
  encuentra nada ahí, y ese es el caso normal, no el raro. Hay segunda pasada
  para texto pegado y test de regresión con las cadenas sucias de verdad.
- **Sin OCR se dice SIN COMPROBAR**, igual que los oídos. Una capacidad ausente
  no es un resultado negativo.

**36 pruebas nuevas.** El juicio vive separado de la captura, así que se prueba
con imágenes sintéticas y con OCR real, sin emulador delante.

---

# v5.14.0 — oídos, y el mapa del cable entre la pantalla y el núcleo

**Qué cambia:** R9 puso ojos a las corridas. Faltaban dos cosas: **oír** si el
audio sale entero, y saber qué partes de la interfaz están **realmente
cableadas** al núcleo.

**Lo concreto:**

- **`magi/modules/percepcion/`** — los oídos, con `listen_audio` y
  `audio_available` registradas en el enjambre (52 herramientas). Capturan el
  loopback WASAPI y distinguen tres cosas: `has_sound` (¿salió audio?),
  `choppy` (¿salió entero?) y `sonando_pct`. El log no puede: en YabauseVita
  `scsp_th` gasta los mismos 1,1-1,4 s por ventana con audio limpio que con
  audio a trompicones.
- **«Sin oídos» ≠ «no suena».** Si la máquina no tiene backend (medio CI corre
  en Linux), la herramienta devuelve **SIN COMPROBAR** y lo dice. Inventar un
  veredicto negativo era el fallo que R9 corrigió del lado de la imagen.
- **El veredicto se prueba sin tarjeta de sonido** — vive separado de la
  captura y se le pasan señales sintéticas: continua, silencio, y troceada.
  Un juicio que solo se puede probar con el hardware delante es un juicio que
  nadie prueba, y miente el día que importa.
- **R16 en el protocolo de corrida** — `ronda_verificada` ahora exige el
  veredicto de sonido junto a los de imagen y movimiento.
- **`magi/modules/gui/mapa.py` + `docs/MAPA-INTERFAZ.md`** — el mapa del
  cableado por topics entre `magi-gui/src` y `magi/`, generado y no escrito a
  mano. Resultado medido: **19 comandos conectados, 23 eventos conectados, 0
  topics sin destinatario** y 25 capacidades que el backend emite y ningún
  panel pinta.
- **La primera versión del mapa mentía y por eso hay test.** Contaba un solo
  sentido y declaró 21 «paneles muertos», entre ellos `task.archive`, que
  tiene handler en `kernel.py:72` — la UI lo **manda**, no lo escucha. Un mapa
  que confunde las direcciones manda al enjambre a arreglar 19 fallos
  inexistentes. Ahora comandos y eventos se cuentan aparte, con trinquete.

**34 pruebas nuevas** (18 de oídos, 16 del mapa).

---

# v5.13.0 — memoria permanente: lo descartado deja de perderse

**Qué cambia:** MAGI gana memoria que sobrevive a la tarea, a la sesión y a la
máquina. Hasta ahora `EpisodicMemory` respondía «no repitas esto» dentro de un
`task_id` y moría con él; lo que faltaba era el otro lado: **qué se salvó de
cada enfoque descartado**.

**Lo concreto:**

- **`magi/modules/swarm/memoria_persistente.py`** — lee `magi/data/memoria/`,
  versionado en git, y lo inyecta arriba del prompt. Vive en el repo a
  propósito: la memoria que vive en `%APPDATA%` no viaja con el sistema, no se
  revisa en un diff y se pierde al reinstalar — que es exactamente cómo se
  perdieron los scripts de la sesión del 30-ago.
- **`descartes.jsonl` con campo `rescatable`** — un enfoque que pierde deja
  conocimiento igual que uno que gana, y suele dejar más. Sembrado con los 7
  descartes reales de YabauseVita R1-R3, cada uno con su medición y con lo que
  sobrevive. Ejemplo: revertir `-Ofast -flto -ffast-math` no arregló el cuelgue
  del dynarec — pero queda **falsado** que los flags fueran la causa, y sin ese
  registro el siguiente que los vea en el historial pierde un ciclo
  sospechando de ellos primero.
- **JSONL y no JSON** — se añade con un append, sin releer ni reescribir el
  fichero. Un formato que obliga a reescribirlo todo para añadir una entrada
  acaba con entradas que nadie añade. Una línea corrupta no invalida el
  histórico: se salta.
- **`controles.json` deja de ser huérfano** — existía en disco desde el 30-ago
  y **no lo leía ningún prompt**. Tercer caso de la misma clase tras
  `bitacora.py` (v5.11.0) y el trinquete de versionado (v5.12.0). Ahora entra
  por `inyecciones.acumuladas()` y hay un test que se pone rojo si se
  desconecta.
- **La quinta inyección** — la secuencia del prompt pasa a ser aceptación →
  caja → bitácora → ronda → memoria. Sobre el encargo de la Ronda 4 son 15.076
  caracteres de contexto que el enjambre no tenía que redescubrir.
- **Un test lee la memoria REAL del repo**, no una de mentira: un `descartes.jsonl`
  malformado en un commit se caza antes del release, no después.

**15 pruebas nuevas.** Suite completa en verde, trinquete de líneas incluido.

---

# v5.12.0 — la Ronda 2 ejecutada y supervisada, y el huérfano que rompía el CI

**Qué cambia:** la Ronda 2 de YabauseVita se ejecutó con el procedimiento del
sistema (`scripts/ronda_emulador.py`) y la supervisión encontró dos fallos del
propio procedimiento — que ya no están. Además, el release v5.11.0 destapó una
clase de huérfano nueva, que ahora tiene trinquete.

**Lo concreto:**

- **Experimento de input rediseñado** — la v5.11.0 medía diff antes/después en
  el attract de Sonic R, que ya se mueve: la pulsación quedaba diluida y el
  veredicto era falso. Ahora: capturas a 1 s, PICO de transición y CONTROL de
  6 s sin pulsar. Resultado medido con el protocolo correcto: ENTER en el
  attract no produce transición (pico 5,31 % vs control 5,16 %) — con el matiz
  declarado de que el attract puede ignorar START (regla R13: todo veredicto
  dice en qué pantalla se midió).
- **Resultados de la Ronda 2** (verificados con capturas): NiGHTS no llega al
  título en 3 min — queda en la pantalla de licencia SEGA esperando al disco;
  el dynarec cuelga al primer frame por tercera build independiente; y el
  perfil SH2 queda fijado: 69,9 % (Sonic R), 64,7 % (Panzer, que aun así corre
  a 59,8 FPS) y 90,7 % (NiGHTS) del hilo principal.
- **El harness expone los diffs por captura** (`diffs_por_captura`) — sin eso
  no hay picos de transición, solo medianas.
- **Trinquete de versionado** (`test_nada_sin_versionar.py`) — `bitacora.py`
  existía en el disco de desarrollo y NO en git: la suite local pasaba y el CI
  reventaba con ImportError en el release v5.11.0. Un módulo con test pero sin
  versionar pasa la suite local siempre, presente o no en el repo. Ahora todo
  `.py` bajo `magi/` y `tests/` que git no conozca es un fallo con nombre.
- **Refactor con el trinquete contento** — la cuarta inyección inline hizo
  saltar el límite de líneas del orquestador; la secuencia de inyecciones
  (aceptación, caja, bitácora, protocolo de corrida) vive ahora en
  `inyecciones.py`: el orquestador bajó de 1557 a 1543 líneas sin subir el
  techo.

**Compatibilidad:** sin cambios de interfaz ni de configuración.
