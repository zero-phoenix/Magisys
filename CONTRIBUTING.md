# Contribuir a Magisys

## El desarrollo en cuatro comandos

```bash
pip install -r requirements.txt      # runtime + tests (de la lista, nunca a mano)
python -m pytest tests/ -m "not slow"   # la suite rápida (~1000 tests)
ruff check magi/ tests/ scripts/        # bloqueante, deuda a cero
pyright magi/core                       # bloqueante, a cero
```

Los tests marcados `slow` compilan un `.exe` real con PyInstaller: corren en
el job semanal `lentos` y en el release, no hace falta pasarlos en local en
cada cambio.

## Cómo se publica una versión (UNA sola vía)

1. Bump de versión en `pyproject.toml` **y** sección nueva `# vX.Y.Z` al
   principio de `RELEASE_NOTES.md`. El gate del release exige que coincidan
   con el tag — una versión, una fuente de verdad.
2. Push del commit y CI verde.
3. `git tag vX.Y.Z && git push origin vX.Y.Z`.

Eso dispara `release.yml`, que:

- corre la suite completa en ubuntu (requirements.txt: "¿upstream rompió algo HOY?"),
- corre la suite rápida **en Windows sobre `requirements.lock`** — el entorno
  exacto con el que se compila el binario,
- compila con `Magisys.spec`, verifica el inventario de PyInstaller,
- **ejecuta** `dist/Magisys.exe --selftest` (incluido el intérprete embebido),
- publica el zip versionado `Magisys-vX.Y.Z-win64.zip` + CHECKSUMS + las notas
  del tag.

Si algo de eso falla, **no hay release** — y esa compuerta no se debilita:
`tests/test_la_compuerta.py` pone el CI en rojo si alguien lo intenta.

`scripts/rescate_release.py` (antes `publicar.py`) es la vía de emergencia
para publicar SIN Actions. Se llama "rescate" para que nadie lo confunda con
la receta: dos recetas para el mismo binario es una que se queda atrás.

## Reglas del proyecto que el CI vigila

- **Jamás modelos locales** (`tests/test_nunca_modelos_locales.py`): no
  KoboldCpp, no Ollama, no llama.cpp. Groq (nube, con clave) es la única
  excepción de clave permitida, y solo para subagentes.
- **Groq es SOLO-subagente** (`tests/test_groq_solo_subagente.py`): el
  enjambre (Melchior/Balthasar/Casper/Naoko/Ritsuko) es g4f puro.
- Sin rutas absolutas a máquinas concretas (`magi.core.paths`).
- Sin `magi_brain.db` commiteado.
- Dependencias desde `requirements.txt` en los workflows, nunca enumeradas a
  mano (la lista se quedó atrás dos veces).
- El lint cubre `magi/`, `tests/` y `scripts/` — el código que decide si se
  publica no puede ser el único sin revisar.

## Tests: la frontera se declara

- `frontera`: el test toca red/disco/navegador a propósito y no puede afirmar
  nada que dependa de lo instalado en la máquina.
- `catalogo_real`: el test lee el catálogo real de proveedores (por defecto
  los demás ven uno congelado).
- `slow`: compila artefactos pesados.
