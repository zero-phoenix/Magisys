# MEGAPLAN COMPLETO — Magisys + YabauseVita
## Consolidado de las sesiones del 2-sep al 6-sep-2026 · Estado: **v5.27.2** (corregido el 13-sep contra el código)

**Para:** cualquier agente que continúe (Antigravity IDE, ZCode Desktop, u otro)
**Repositorios:** `zero-phoenix/Magisys` (público) y `zero-phoenix/yabausevita` (público)
**Clones locales de trabajo:** `C:\Users\D\Documents\GitHub\MAGI-System-IDE` (la carpeta
conserva el nombre anterior **a propósito**: renombrarla rompe rutas de la máquina)
y `C:\Users\D\Documents\GitHub\yabausevita-zp`
**Máquina:** `DESKTOP-B6D864U` · Windows 10 22H2 · i7-3770 (8 hilos) · GTX 1050 low profile 2 GB (NO se usa) · 24 GB RAM · C: ~10 GB libres

---

## 0. LA REGLA QUE ORDENA TODO

> **Un documento sobre el sistema no es el sistema. Lee el código.**

Si algo de este megaplan contradice al código, **gana el código** y se corrige
el documento en el mismo commit. Caso fundacional: `PORTING_NOTES.md` describía
un esqueleto y el código real tenía dynarec, CHD y audio — un plan entero se
tiró por creerle al documento.

## 1. QUÉ ES CADA COSA (mapa mínimo)

- **MAGI**: IDE con enjambre de 3 IA (MELCHIOR tesis/BALTHASAR antítesis/CASPER
  síntesis) + NAOKO (supervisora, repara) + RITSUKO (auditora, solo informa) +
  **LILIM** (capa local superveloz, v12 determinista + v13 mielina neuronal y
  sentidos periféricos). Inferencia gratuita vía g4f (alineado a 8.1.1) +
  KoboldCpp local opcional. 67 herramientas. 1786 pruebas Python + 131 GUI.
- **YabauseVita**: emulador de Sega Saturn para PS Vita. Bitácora:
  `yabausevita-zp/docs/BITACORA-OPTIMIZACION.md` (hallazgos A1-A27, reglas R1-R16).
- **LILIM** (v12-v13): capa LOCAL dual: 1) Determinista (0 ms-3,5 ms, sin GPU,
  sin red: motor EPD, traductor 6 idiomas, novedades falsables 2023-2026, M4
  con URL obligatoria, contexto inyectado); 2) Mielina neuronal y sentidos
  (v13): cliente KoboldCpp (Qwen 2.5 1.5B GGUF Q4_K_M adaptado a i7-3770 /
  GTX 1050 2GB), aceleración dialéctica pre-AST/propuesta/arbitraje, Ojos
  (rasterización PDF/imágenes 150-300 DPI tipo Google Lens), Oídos (WAV/MP3/OGG
  y WASAPI), Brazos (actuación de workspace, generación MD/DOCX, SHA-256).

## 2. ESTADO VERIFICADO AL CIERRE (6-sep-2026)

| Versión | Qué fue | Estado CI/Release |
|---|---|---|
| v5.19.0 | Auditoría en vivo: pie de versión real, Vista previa, C6 (HuggingSpace aliases), Ritsuko R2+R4 | ✅ |
| v5.20.0 | Devorador de encargos arreglado; filosofía C liberada por medición (A9 cayó: drawn/presented/dropped ya se imprimen); motor trivial→fast | ✅ |
| v5.21.0 | Interfaz reconstruida: conversación = columna vertebral, cajón de paneles, línea de estado honesta; tope de 20 s a la llamada de estilo | ✅ |
| v5.21.1 | Pase de Balthasar: aprobación visible, «Ir a X» abre cajón, rutas clicables, cajón persistente, MotoresPanel | ✅ |
| v5.22.0 | Megaplan v11 documentado (misión Tetris: 11 hallazgos con evidencia) | ✅ |
| v5.23.1 | B1 comandos, A1 manifiesto, A3 código-antes-del-build, C3 sin humo, D1 filtro de ruido, B3 SYS_EXEC real, C2 motivo | ✅ (v5.23.0 falló y quedó con errata) |
| v5.24.0 | A2: autotest de teclas de 3 estados (verde/rojo/sin_comprobar) ejecutado por el packager | ✅ |
| v5.25.0 | LILIM v1: `lilim_pregunta`, `repos_de`, controles ampliados (PS2, pc_jugando, decomp/puertos), g4f 8.1.1 auditado (32/32) | ✅ |
| v5.26.0 | LILIM multimodal: motor EPD, traductor 6 idiomas, novedades 2023-2026, lilim_ensenar (M4), CONTEXTO inyectado, A2 con marca por fichero | ✅ |
| v5.27.0 | Enjambre v6 (F1-F5), degradación D2, clon shallow L2, CancelReport C1-GUI, E2/E3 Ronda 4 CDB/SH2, LILIM v13 (Mielina neuronal KoboldCpp Qwen 2.5 1.5B, Ojos Google Lens con PyMuPDF, Oídos acústicos, Brazos workspace) | ✅ |

| v5.27.1 | Paleta Evangelion, desacople de Venim, auditoría ortogonal de YabauseVita | ✅ (tagueada con `pyproject` aún en 5.27.0) |
| v5.27.2 | Herramientas del CI con versión fija (pyright/pip-audit/pyinstaller), `no_browser` sin depender del stub de g4f, errata de F2-F5 | ✅ |
| v5.28.0 | F5, F3 y F2 cableados al orquestador (F4 no, a propósito); veredictos extraídos del bucle (1545→1489); compuerta local midiendo como el CI; tres guardas nuevas | ✅ |

**Todos los releases conservados (ninguno se borra NUNCA). El asset se llamó
`MAGI-IDE-v5.zip` hasta la v5.27.1 incluida; desde el renombrado a Magisys el
workflow genera `Magisys.zip`.**

> **Corregido el 13-sep-2026:** este documento decía «CI verde». Medido: `main`
> estuvo **en rojo del 10 al 13 de septiembre**, tres corridas seguidas, por
> `pip install pyright` sin versión fija — el instrumento cambió, no el código.
> Lo arregla la v5.27.2. Un documento no es el sistema, tampoco este.

### Los planes y su estado

| Plan | Hecho | Pendiente |
|---|---|---|
| **v6** (fases 1-11) | 6,7,8,10,11, **F1**, y en la v5.28.0 **F5** (cuarto veredicto), **F2** (sin fabricar, tras bandera apagada) y **F3** a medias (el plan se inyecta) | **F4** — bloqueada a propósito: ejecuta la suite antes de cada aprobación y la suite falla 1 de cada 3 corridas en paralelo. Va detrás de su estabilidad. **La otra mitad de F3**: los estados de las partes necesitan que Casper los declare, y eso toca su prompt otra vez — aparte y con medición |
| **v9** (Ritsuko) | R1, R2, R4 | **R3** portera de la sonda (tras rodar G4 en uso real) |
| **v10** (megaplan base) | D1-D6, M1, M2, P4, auditoría en vivo, **E1**, **E2**, **E3** | **E4** dynarec solo con Vita real; **P1-P3** diferidas con precondición |
| **v11** (Tetris) | A1, A2, A3, B1, B3, C2, C3, D1, **C1-GUI**, **D2** | **T5** el tetris.exe del Escritorio sigue con la R rota |
| **v12** (Lilim) | L1 + motor EPD + traductor + novedades + M4 + contexto + **L2** + **L3** + **L4** | **L5** panel de Lilim en la GUI |
| **v13** (Lilim Mielina) | Cliente KoboldCpp, acelerador mielina (Melchior/Balthasar/Casper/Naoko/Ritsuko), Ojos (Lens/PDF), Oídos (acústico), Brazos (workspace/MD/DOCX/SHA-256) | (Completado) |

### Pendientes prioritarios (reescritos el 13-sep-2026 sobre lo medido)

La lista anterior daba por pendientes D2, L2, F1, C1-GUI y E1: **todos se
escribieron el 6-sep y se publicaron en la v5.27.0**. Lo que queda de verdad:

1. ~~Cablear F2-F5~~ — **hecho en la v5.28.0 salvo F4**, y el orden cambió sobre
   la marcha: se planteó «F4 primero, que es la compuerta de las demás» y la
   medición lo tumbó. F4 ejecuta `verificar.py --rapido` antes de cada
   aprobación (~165 s) y la suite **falla 1 de cada 3 corridas en paralelo** por
   un transporte de asyncio que se destruye sin cerrar: cablearla convertiría un
   fallo de infraestructura en el rechazo aleatorio de 1 de cada 3 entregas
   legítimas. **F4 va detrás de la estabilidad de la suite**, que es trabajo
   aparte y ya abierto.
2. **Herramientas alcanzables** — hecho a medias, con el número delante. No eran
   cuatro: eran **18 de 67** fuera de `CORE_TOOLS` y de toda caja, incluida la
   capa Lilim entera. El reparto por dominio las deja en **12**, y las 12 no
   caben: el catálogo tiene techo (2700 por dominio, 3500 multidominio) y va a
   2617 y 3160. Para las que faltan, la salida que señala el propio test es
   **reducir parámetros** de las que ya están. `test_herramientas_alcanzables.py`
   congela el número para que no crezca.
3. ~~Guardas de escritura destructiva~~ — **hecho, las dos mitades.** Una
   escritura que deja el fichero por debajo del 25 % se rechaza y pide
   `truncar=true` (el caso medido: `vidgpu.c`, 14.267 → 523 bytes, el 3,7 %). Y
   los ficheros citados se comprueban en disco antes de arbitrar: el prompt de
   Casper recibe la lista de los que NO existen, que es lo que le faltó el 8-sep
   con `vita_gpu.h`. **Ya no bloquea soltarle el repositorio al enjambre.**
4. **E1** a medias (el plan se pinta una vez y no cambia de estado, y no filtra
   por conversación), **E2** (Naoko/Ritsuko son pestañas del cajón derecho, no
   hilos en la izquierda), **E3** (el manifiesto ni siquiera viaja en
   `swarm.approval_required`: faltan las dos mitades), **L5** (no hay panel de
   Lilim ni RPC: la capa existe y es inalcanzable desde la interfaz).
5. **E2/E3 del emulador** (instrumentar SH2LRU/dynarec; Ronda 4 BIOS/CDB de
   NiGHTS). **E4** solo con Vita real.
6. ~~A9 de la bitácora está caducado~~ — **no lo estaba, y el pendiente era
   mío.** Comprobado el 13-sep leyendo las dos fuentes: `vidgpu.c:340` imprime
   `drawn=%d presented=%d dropped=%d`, y la bitácora **ya lo dice desde el
   2-sep**, tachado y con su medición: «SUPERADO por medición: los imprime cada
   5 s desde el 18-jul… drawn=94, presented=94, dropped=0 a 34,7 FPS». Lo puse
   aquí copiándolo de `TRASPASO-ASTRA.md` §4.1 sin abrir la bitácora — que es
   exactamente el fallo que ese traspaso cuenta haber cometido con este MISMO
   hallazgo. La tabla de versiones de este documento (v5.20.0) ya lo daba por
   caído: me contradije en el mismo fichero.
7. ~~Tres umbrales absolutos de tiempo~~ — **hechos**, y eran cinco. Ahora se
   miden contra la serie medida en la misma corrida (R12); de paso se quitó el
   `skipif` de xdist que tapaba uno de ellos.

## 3. REGLAS NO NEGOCIABLES

1. **Ninguna corrida es evidencia sin ojos y oídos** (R9/R16): `has_image`,
   `has_motion`, veredicto de sonido.
2. **«No lo comprobé» ≠ «no funciona»** → se declara SIN COMPROBAR.
3. **Se mide contra un CONTROL en la misma corrida, nunca contra constantes
   de reloj** (R12 — aprendida cuatro veces).
4. **Compuerta completa antes de publicar**: `python -m ruff check magi/ tests/`
   · `python scripts/huerfanos.py --conteo` (techo 80) ·
   `python -m pytest tests/ -q` entera (~4 min en CI, ~25 local en serie) ·
   `python scripts/verificar.py --todo` (incluye .exe).
5. **Nunca subir el techo de un trinquete** ni tocar `KNOWN_ORPHANS`:
   conectar, adelgazar o extraer módulo. Techos actuales: `kernel.py` 1070,
   `orchestrator.py` 1550, `ritsuko.py` 800, `builtin.py` 800 (va en 798).
6. **Los FPS de Vita3K no son prueba de rendimiento** (R4).
7. **Ficheros con Python**, `newline='\n'`, comprobar BOM
   (`datos[:3] != b'\xef\xbb\xbf'`).
8. **No borrar releases anteriores. Nunca.** No escribir a mano documentos
   generados (AUTOMODELO, MAPA-INTERFAZ).
9. **Hardware no se toca; sin descargas grandes.** Lilim: stdlib pura, sin
   GPU, un hilo — no ahoga la máquina (i7-3770 + GTX 1050 2 GB).
10. **Si te equivocas, dilo y corrígelo en el mismo mensaje/commit.** Las
    erratas van visibles en las notas (v5.17.1, v5.23.1).

## 4. LA REGLA PERMANENTE DEL USUARIO (siempre, en cada versión)

> **Siempre que se compile localmente también se debe compilar en la nube
> (GitHub Actions); reescribir el README; y que en Releases aparezca el exe
> comprimido con la descripción de lo nuevo de la versión compilada en Actions.**

El flujo de publicación que ya está probado (v5.19 → v5.26):

```bash
# 1. compuerta local completa (incluye compilar el .exe en los slow tests)
python scripts/verificar.py --todo
# 2. versión en pyproject.toml + RELEASE_NOTES.md (notas CONCRETAS, nunca genéricas)
# 3. commit + push de main
git add -A && git commit -m "feat(vX.Y.Z): ..." && git push origin main
# 4. tag → Actions compila y publica el release con Magisys.zip + CHECKSUMS.txt
git tag -a vX.Y.Z -m "..." && git push origin vX.Y.Z
# 5. verificar: gh run list --limit 2 && gh release view vX.Y.Z --json assets
```

- `scripts/publicar.py` (vía local) exige el entorno alineado a
  `requirements.lock` (9 dependencias divergen al cierre: g4f local 8.1.1 ya
  alineado; el resto sigue divergiendo). El camino canónico es el tag + Actions.
- README: actualizar cifras reales (tests, herramientas — las cuenta
  `test_readme_claims.py` contra el registry) y la sección de novedades.
- El `.exe` del CI no incluye los commits de `main` posteriores al tag: si el
  fix importa para el binario, taguear después del fix.

## 5. TRAMPAS QUE YA COSTARON TIEMPO (no repetir)

| Trampa | Arreglo |
|---|---|
| BOM de PowerShell | escribir con Python, `newline='\n'`, verificar 3 primeros bytes |
| Heredocs de bash mangles `\n` y `\\` | generar ficheros con Write/Edit tools o Python con `chr(92)` — NUNCA heredoc para código con escapes |
| Ruff sin fijar | `ruff==0.16.5` en requirements-dev |
| `NODE_ENV=production` local | `_entorno()` de verificar.py lo limpia; `npm` es `npm.cmd` |
| Módulo en disco y no en git | `git add -A` antes de la suite; `test_nada_sin_versionar` caza |
| OpenSSL de Git mata Vita3K | lanzar sin `Git\mingw64\bin` en PATH |
| `.replace()` sin assert | verificar el efecto; el mapa de interfaz caza cadenas |
| Lanzar MAGI con `&` en shell efímera | usar mecanismo persistente del agente |
| Tareas recuperadas tras reinicio | el zombie-resume se cuelga (ticket #1 histórico); conversación nueva como solución operativa |
| Docker: `./vdpm` del checkout | usar el `vdpm` del PATH |

## 6. PROCEDIMIENTOS CLAVE

**Compilar YabauseVita** (Docker, validado): ver v10 §12.1 — vdpm del PATH.
**Corrida verificada del emulador**: `python tools/vita3k_ctl.py run --seconds 60 --windows 6`
(con `has_image`, `has_motion`); config.cfg sin BOM, `cpu_mode=2`, `auto_bios=0`.
**Abrir MAGI**: `python -m magi.main` (persistente, con log a fichero).
GUI en `http://127.0.0.1:1420` (la ventana nativa pywebview es la misma app).

**El ritual de cierre de cada ronda/tarea** (no opcional):
1. Descartes a `magi/data/memoria/descartes.jsonl` con medición y `rescatable`.
2. `contrastar(prueba, ok, evidencia)` en el automodelo — incluidas las
   afirmaciones sobre el propio agente.
3. Hallazgos y reglas a la bitácora del emulador EN EL MISMO COMMIT del cambio.
4. Release con notas concretas + README actualizado + conservar releases.
5. Actualizar el estado de este megaplan.

---

## 7. ÍNDICE DE DOCUMENTOS DEL PROYECTO

- `docs/MEGAPLAN-v10-continuacion.md` — plan base + estado D/E/M/F/P
- `docs/MEGAPLAN-v11-tetris.md` — misión Tetris: 11 hallazgos + plan A-E + estado
- `docs/MEGAPLAN-v12-lilim.md` — Lilim: principios, capas M1-M5, fases L1-L5
- `docs/DECONSTRUCCION-INTERFAZ.md` — los 10 principios de la interfaz reconstruida
- `docs/TRASPASO.md` — traspaso histórico (actualizar al cerrar sesión)
- `docs/BITACORA-OPTIMIZACION.md` (en yabausevita-zp) — A1-A27, R1-R16

---

*Fin del consolidado. Verificado contra el código el 6-sep-2026. Si el código
cambió desde entonces, gana el código.*
