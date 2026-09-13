"""
Catálogo de herramientas (Plan MAGI 9.0 §2.2, §4.1).

ACCESO A LA MÁQUINA: SIN RESTRICCIONES
======================================
Es la máquina del usuario y su autorización. No hay allowlist de directorios,
ni puertas de permiso, ni capacidades denegadas. Lo único que se añade es
REVERSIBILIDAD (journal.py): toda mutación se puede deshacer.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..paths import python_executable, workspace_dir
from .journal import WriteJournal
from .registry import Access, ToolRegistry, ToolResult

#: Ver `paths.python_executable`: dentro del bundle `sys.executable` es el
#: propio .exe y lanzarlo relanzaría MAGI en vez de ejecutar Python.
_SIN_PYTHON = (
    "no hay un intérprete de Python en esta máquina. Dentro del .exe de "
    "MAGI, lanzar `sys.executable` relanzaría MAGI en vez de ejecutar "
    "esto. Instala Python y vuelve a intentarlo.")
MAX_READ_BYTES = 400_000


@dataclass
class ToolContext:
    """Estado que comparten las herramientas durante un turno."""
    task_id: str | None = None
    cwd: Path = field(default_factory=workspace_dir)
    journal: WriteJournal | None = None
    dry_run: bool = False
    env: dict[str, str] = field(default_factory=dict)

    def resolve(self, path: str | Path) -> Path:
        p = Path(os.path.expandvars(str(path))).expanduser()
        return p if p.is_absolute() else (self.cwd / p)

    def get_journal(self) -> WriteJournal:
        if self.journal is None:
            self.journal = WriteJournal(task_id=self.task_id)
        return self.journal


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()

    # ------------------------------------------------------------- lectura

    @reg.tool("controles_de",
              "Consulta la memoria permanente de mandos: cómo se juega en una "
              "consola, cómo se mapea a teclado/PC y las convenciones de entrada "
              "por plataforma (teclado, xinput, sceCtrl). Uso: controles_de "
              "\"sega_saturn\" o \"pc\" para la configuración de computadora.",
              {"type": "object", "properties": {
                  "consola": {"type": "string",
                              "description": "nombre/clave de consola, o 'pc' "
                                             "para jugar en computadora"}},
               "required": ["consola"]}, access={"read"})
    def controles_de(consola: str, ctx: ToolContext):
        import json

        from ...modules.swarm.memoria_persistente import raiz as _raiz_memoria
        _raiz = _raiz_memoria()
        ruta = _raiz / "controles.json" if _raiz else None
        if ruta is None or not ruta.exists():
            return ToolResult(False, "", error="memoria de controles no encontrada")
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        clave = consola.strip().lower().replace(" ", "_")
        seccion = (datos.get("consolas") or {}).get(clave)
        if seccion is None and clave in ("pc", "pc_jugando", "computadora"):
            seccion = datos.get("pc_jugando")
        if seccion is None:
            # búsqueda difusa por subcadena
            for k, v in (datos.get("consolas") or {}).items():
                if clave in k:
                    seccion = v
                    break
        if seccion is None:
            disponibles = ", ".join(sorted(datos.get("consolas") or {}))
            return ToolResult(False, "", error=f"no conozco '{consola}'. Disponibles: {disponibles}; 'pc'")
        cuerpo = seccion if isinstance(seccion, str) else json.dumps(seccion, ensure_ascii=False, indent=2)
        extra = ""
        decomp = datos.get("decompilacion_y_puertos") or {}
        if decomp and any(p in clave for p in ("decomp", "port", "puerto")):
            extra = "\n\nDECOMP/PENDIENTE-DE-PORT: ver decompilacion_y_puertos."
        return ToolResult(True, f"{clave}: {cuerpo}{extra}")

    # LILIM (v12) y Percepción Web (F1)
    from .lilim_tools import registrar as _registrar_lilim
    from .web_tools import registrar as _registrar_web
    _registrar_lilim(reg)
    _registrar_web(reg)

    @reg.tool("read_file", "Lee un fichero de texto. Usa offset/limit para ficheros grandes.",
              {"type": "object", "properties": {
                  "path": {"type": "string"},
                  "offset": {"type": "integer", "description": "línea inicial (1-based)"},
                  "limit": {"type": "integer", "description": "número de líneas"}},
               "required": ["path"]}, access={"read"})
    def read_file(path: str, ctx: ToolContext, offset: int = 1, limit: int = 0):
        p = ctx.resolve(path)
        if not p.exists():
            return ToolResult(False, "", error=f"no existe: {p}")
        if p.is_dir():
            return ToolResult(False, "", error=f"es un directorio: {p}")
        if p.stat().st_size > MAX_READ_BYTES:
            data = p.read_bytes()[:MAX_READ_BYTES].decode("utf-8", errors="replace")
            note = f"\n… [truncado, fichero de {p.stat().st_size} bytes]"
        else:
            data, note = p.read_text(encoding="utf-8", errors="replace"), ""
        lines = data.splitlines()
        if offset > 1 or limit:
            end = (offset - 1 + limit) if limit else len(lines)
            lines = lines[offset - 1:end]
        numbered = "\n".join(f"{i + offset:>6}\t{ln}" for i, ln in enumerate(lines))
        return ToolResult(True, numbered + note, meta={"lines": len(lines), "path": str(p)})

    @reg.tool("list_dir", "Lista un directorio.",
              {"type": "object", "properties": {
                  "path": {"type": "string"}, "recursive": {"type": "boolean"}},
               "required": ["path"]}, access={"read"})
    def list_dir(path: str, ctx: ToolContext, recursive: bool = False):
        p = ctx.resolve(path)
        if not p.is_dir():
            return ToolResult(False, "", error=f"no es un directorio: {p}")
        out, count = [], 0
        it = p.rglob("*") if recursive else p.iterdir()
        for child in sorted(it):
            if any(part in {".git", "node_modules", "__pycache__", ".venv"}
                   for part in child.parts):
                continue
            rel = child.relative_to(p)
            out.append(f"{'d' if child.is_dir() else '-'} {rel}"
                       + ("" if child.is_dir() else f"  ({child.stat().st_size}b)"))
            count += 1
            if count >= 500:
                out.append("… [500+ entradas, acota la ruta]")
                break
        return ToolResult(True, "\n".join(out) or "(vacío)", meta={"count": count})

    @reg.tool("grep", "Busca un patrón (regex) en ficheros.",
              {"type": "object", "properties": {
                  "pattern": {"type": "string"}, "path": {"type": "string"},
                  "glob": {"type": "string", "description": "p.ej. *.py"}},
               "required": ["pattern"]}, access={"read"})
    def grep(pattern: str, ctx: ToolContext, path: str = ".", glob: str = "*"):
        root = ctx.resolve(path)
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return ToolResult(False, "", error=f"regex inválida: {e}")
        hits, files = [], 0
        targets = [root] if root.is_file() else root.rglob(glob)
        for f in targets:
            if not f.is_file() or any(x in f.parts for x in
                                      {".git", "node_modules", "__pycache__"}):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            files += 1
            for n, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f}:{n}: {line.strip()[:200]}")
                    if len(hits) >= 200:
                        hits.append("… [200+ coincidencias]")
                        return ToolResult(True, "\n".join(hits),
                                          meta={"files": files})
        return ToolResult(True, "\n".join(hits) or "(sin coincidencias)",
                          meta={"files_scanned": files, "hits": len(hits)})

    @reg.tool("glob", "Busca ficheros por patrón de nombre.",
              {"type": "object", "properties": {
                  "pattern": {"type": "string"}, "path": {"type": "string"}},
               "required": ["pattern"]}, access={"read"})
    def glob_tool(pattern: str, ctx: ToolContext, path: str = "."):
        root = ctx.resolve(path)
        found = [str(p) for p in root.rglob(pattern)
                 if ".git" not in p.parts and "node_modules" not in p.parts][:300]
        return ToolResult(True, "\n".join(found) or "(sin resultados)",
                          meta={"count": len(found)})

    # ------------------------------------------------------------- escritura
    # Todas pasan por el journal: reversibles, no restringidas.

    @reg.tool("write_file", "Escribe un fichero (lo crea o lo reemplaza). Reversible.",
              {"type": "object", "properties": {
                  "path": {"type": "string"}, "content": {"type": "string"}},
               "required": ["path", "content"]},
              access={"write"}, dangerous=True)
    def write_file(path: str, content: str, ctx: ToolContext):
        p = ctx.resolve(path)
        if ctx.dry_run:
            return ToolResult(True, f"[dry-run] escribiría {len(content)}b en {p}")
        p.parent.mkdir(parents=True, exist_ok=True)
        entry = ctx.get_journal().record(p, "write" if p.exists() else "create",
                                         tool="write_file")
        p.write_text(content, encoding="utf-8")
        return ToolResult(True, f"escrito {p} ({len(content)} bytes)",
                          meta={"undo_id": entry.op_id, "path": str(p)})

    @reg.tool("edit_file", "Reemplaza una cadena exacta dentro de un fichero. Reversible.",
              {"type": "object", "properties": {
                  "path": {"type": "string"}, "old": {"type": "string"},
                  "new": {"type": "string"}, "all": {"type": "boolean"}},
               "required": ["path", "old", "new"]},
              access={"write"}, dangerous=True)
    def edit_file(path: str, old: str, new: str, ctx: ToolContext, all: bool = False):
        p = ctx.resolve(path)
        if not p.exists():
            return ToolResult(False, "", error=f"no existe: {p}")
        text = p.read_text(encoding="utf-8", errors="replace")
        n = text.count(old)
        if n == 0:
            return ToolResult(False, "", error="la cadena 'old' no aparece en el fichero")
        if n > 1 and not all:
            return ToolResult(False, "", error=(
                f"'old' aparece {n} veces; usa all=true o amplía el contexto "
                f"para que sea único"))
        if ctx.dry_run:
            return ToolResult(True, f"[dry-run] sustituiría {n} ocurrencia(s) en {p}")
        entry = ctx.get_journal().record(p, "write", tool="edit_file")
        p.write_text(text.replace(old, new) if all else text.replace(old, new, 1),
                     encoding="utf-8")
        return ToolResult(True, f"editado {p} ({n if all else 1} sustitución/es)",
                          meta={"undo_id": entry.op_id})

    @reg.tool("delete_path", "Borra un fichero o directorio. Reversible.",
              {"type": "object", "properties": {"path": {"type": "string"}},
               "required": ["path"]}, access={"write"}, dangerous=True)
    def delete_path(path: str, ctx: ToolContext):
        p = ctx.resolve(path)
        if not p.exists():
            return ToolResult(False, "", error=f"no existe: {p}")
        if ctx.dry_run:
            return ToolResult(True, f"[dry-run] borraría {p}")
        entry = ctx.get_journal().record(p, "delete", tool="delete_path")
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()
        return ToolResult(True, f"borrado {p}", meta={"undo_id": entry.op_id})

    @reg.tool("undo", "Deshace la última mutación, o todas las de esta tarea.",
              {"type": "object", "properties": {
                  "scope": {"type": "string", "enum": ["last", "task"]}}},
              access={"write"})
    def undo(ctx: ToolContext, scope: str = "last"):
        j = ctx.get_journal()
        if scope == "task" and ctx.task_id:
            return ToolResult(True, f"revertidas {j.undo_task(ctx.task_id)} operaciones")
        e = j.undo_last()
        return ToolResult(bool(e), f"revertido: {e.target}" if e else "",
                          error=None if e else "nada que deshacer")

    # ------------------------------------------------------------- ejecución

    @reg.tool("run_command", "Ejecuta un comando de shell y devuelve su salida.",
              {"type": "object", "properties": {
                  "command": {"type": "string"}, "cwd": {"type": "string"},
                  "timeout": {"type": "integer"}},
               "required": ["command"]}, access={"exec"}, dangerous=True)
    async def run_command(command: str, ctx: ToolContext,
                          cwd: str | None = None, timeout: int = 120):
        workdir = ctx.resolve(cwd) if cwd else ctx.cwd
        workdir.mkdir(parents=True, exist_ok=True)
        if ctx.dry_run:
            return ToolResult(True, f"[dry-run] ejecutaría en {workdir}: {command}")
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_shell(
                command, cwd=str(workdir),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, **ctx.env})
            # §7.3 — inscribir el proceso para que la parada de emergencia
            # pueda alcanzarlo. Sin esto, pulsar "parar" dejaba corriendo
            # cualquier cosa que hubiera lanzado el agente.
            from ..cancel import supervisor
            supervisor().register_process(ctx.task_id, proc)
            try:
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            finally:
                supervisor().forget_process(ctx.task_id, proc)
            text = out.decode("utf-8", errors="replace")
            return ToolResult(proc.returncode == 0,
                              f"$ {command}\n{text}\n[rc={proc.returncode}]",
                              error=None if proc.returncode == 0
                              else f"rc={proc.returncode}",
                              meta={"rc": proc.returncode})
        except asyncio.TimeoutError:
            # kill() sin wait() deja el transporte sin limpiar: el proceso queda
            # zombi y asyncio lanza "Event loop is closed" al recolectarlo.
            if proc is not None:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
            return ToolResult(False, "", error=f"timeout tras {timeout}s")

    @reg.tool("python_exec", "Ejecuta código Python en un proceso aparte.",
              {"type": "object", "properties": {"code": {"type": "string"}},
               "required": ["code"]}, access={"exec"}, dangerous=True)
    async def python_exec(code: str, ctx: ToolContext):
        # `python_executable()` y NO `sys.executable`: dentro del .exe este
        # último es el propio .exe, así que la herramienta con la que el
        # enjambre ejecuta Python relanzaba MAGI y devolvía su salida como si
        # fuera la del código. Ver `paths.python_executable`.
        interprete = python_executable()
        if interprete is None:
            return ToolResult(False, "", error=_SIN_PYTHON)
        script = ctx.cwd / f"_magi_exec_{os.getpid()}.py"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(code, encoding="utf-8")
        try:
            return await run_command(f'"{interprete}" "{script.name}"', ctx=ctx,
                                     timeout=120)
        finally:
            script.unlink(missing_ok=True)

    @reg.tool("run_tests", "Ejecuta pytest sobre una ruta. Es la herramienta que "
                           "convierte una opinión sobre el código en evidencia.",
              {"type": "object", "properties": {
                  "path": {"type": "string"}, "k": {"type": "string"}}},
              access={"exec"})
    async def run_tests(ctx: ToolContext, path: str = "tests", k: str = ""):
        # Sin esto, en el .exe la herramienta que «convierte una opinión en
        # evidencia» devolvía la salida de MAGI arrancando. Balthasar critica
        # habiendo ejecutado: eso es lo que le da autoridad, y en el binario
        # publicado no ejecutaba nada.
        from magi.core.paths import pytest_argv
        # Directorio temporal propio: Balthasar puede estar ejecutando esto
        # mientras Naoko verifica una reparación, y sin aislarlos la corrida
        # que arranca después borra el tmp de la que ya estaba dentro. Las dos
        # acaban con FileNotFoundError en cada test que use tmp_path — es
        # decir, en casi todos — y la crítica «habiendo ejecutado», que es lo
        # que da autoridad a Balthasar, se apoya en una suite falsamente roja.
        argv = pytest_argv(path)
        if argv is None:
            return ToolResult(False, "", error=_SIN_PYTHON)
        cmd = " ".join(f'"{a}"' if " " in a else a for a in argv)
        if k:
            cmd += f' -k "{k}"'
        return await run_command(cmd, ctx=ctx, timeout=300)

    # ------------------------------------------------------------------- red

    @reg.tool("web_fetch", "Descarga una URL y devuelve su texto.",
              {"type": "object", "properties": {"url": {"type": "string"}},
               "required": ["url"]}, access={"net"})
    async def web_fetch(url: str):
        try:
            import httpx
        except ImportError:
            return ToolResult(False, "", error="httpx no instalado")
        # User-Agent de navegador real: con "MAGI/9.0" los sitios devuelven
        # 403 (visto con britannica.com) porque lo identifican como bot.
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/131.0.0.0 Safari/537.36"),
            "Accept": ("text/html,application/xhtml+xml,application/xml;"
                       "q=0.9,*/*;q=0.8"),
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        }
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30,
                                         headers=headers) as c:
                r = await c.get(url)
                r.raise_for_status()
                text = re.sub(r"<script.*?</script>|<style.*?</style>", "",
                              r.text, flags=re.DOTALL | re.I)
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                return ToolResult(True, text[:20000], meta={"status": r.status_code})
        except Exception as e:
            return ToolResult(False, "", error=str(e))

    # -------------------------------------------------- git / gh / build
    # Acceso real al sistema de desarrollo: git y gh CLI con las credenciales
    # del usuario (heredadas vía os.environ en run_command), compilación del
    # binario y entornos aislados. Todas se apoyan en run_command, que ya
    # inscribe el proceso en el supervisor de parada (§7.3).

    @reg.tool("git", "Ejecuta un comando de git en el repo. Operaciones comunes: "
              "status, add, commit -m, push, pull, log, diff, branch, checkout.",
              {"type": "object",
               "properties": {"args": {"type": "string",
                                       "description": "argumentos tras 'git', ej: 'status --short'"}},
               "required": ["args"]}, access={"exec"}, dangerous=True)
    async def git(args: str, ctx: ToolContext):
        return await run_command(f"git {args}", ctx=ctx)

    @reg.tool("gh", "Ejecuta la GitHub CLI. Para runs de Actions, releases, "
              "workflows y gestión del repo. Ej: 'run list', 'workflow run "
              "release.yml -f tag=v5.1.1', 'release list'.",
              {"type": "object",
               "properties": {"args": {"type": "string",
                                       "description": "argumentos tras 'gh'"}},
               "required": ["args"]}, access={"exec"}, dangerous=True)
    async def gh(args: str, ctx: ToolContext):
        return await run_command(f"gh {args}", ctx=ctx)

    @reg.tool("build_exe", "Compila el ejecutable de MAGI con PyInstaller "
              "(onefile, noconsole). Reproduce el build del workflow release.yml. "
              "Devuelve la ruta del .exe generado.",
              {"type": "object",
               "properties": {"name": {"type": "string",
                                       "description": "nombre base (sin .exe)",
                                       "default": "Magisys"}},
               "required": []}, access={"exec"}, dangerous=True)
    async def build_exe(name: str = "Magisys", ctx: ToolContext | None = None):
        if ctx is None:
            return ToolResult(False, "", error="sin contexto")
        raiz = ctx.cwd
        front = raiz / "magi-gui" / "dist"
        if not front.exists():
            return ToolResult(False, "",
                              error="falta magi-gui/dist: ejecuta primero el build del frontend (npm run build)")
        # Las exclusiones son las mismas que release.yml, para que compilar
        # desde el enjambre y compilar en CI den el mismo binario. Sin ellas,
        # PyInstaller se cuelga importando torch al resolver DLLs.
        excluidos = ("torch torchvision torchaudio tensorflow transformers "
                     "onnxruntime markitdown magika PyQt5 PySide2 PySide6")
        cmd = (f'python -m PyInstaller --clean --onefile --noconsole '
               f'--name "{name}" --icon "assets/icon.ico" '
               f'--add-data "assets;assets" '
               f'--add-data "magi-gui/dist;magi-gui/dist" '
               + "".join(f'--exclude-module {m} ' for m in excluidos.split())
               + 'magi/main.py')
        res = await run_command(cmd, ctx=ctx, timeout=600)
        if res.ok:
            exe = raiz / "dist" / f"{name}.exe"
            res.meta = {**(res.meta or {}), "exe": str(exe)}
        return res

    @reg.tool("build_project_exe",
              "Empaqueta un proyecto Python a .exe onefile portable. Lee "
              "requirements.txt, instala deps en venv temporal y devuelve la "
              "ruta del .exe. Detecta GUI (pygame/tkinter) automáticamente.",
              {"type": "object",
               "properties": {
                   "path": {"type": "string",
                            "description": "directorio del proyecto Python"},
                   "entry": {"type": "string",
                             "description": "script de entrada (default: main.py)"},
                   "output": {"type": "string",
                              "description": "ruta del .exe final (default: dist/<nombre>.exe)"},
                   "name": {"type": "string",
                            "description": "nombre base del ejecutable"},
                   "icon": {"type": "string",
                            "description": "ruta a un .ico opcional"},
                   "console": {"type": "boolean",
                               "description": "True para mostrar consola"},
                   "requirements": {"type": "array",
                                    "items": {"type": "string"},
                                    "description": "dependencias adicionales a instalar"},
                   "hiddenimports": {"type": "array",
                                     "items": {"type": "string"}},
               },
               "required": ["path"]},
              access={"exec"}, dangerous=True)
    async def build_project_exe(
            path: str,
            ctx: ToolContext,
            entry: str = "",
            output: str = "",
            name: str = "",
            icon: str = "",
            console: bool = False,
            requirements: list | None = None,
            hiddenimports: list | None = None):
        from ...core.paths import python_executable
        from ...modules.studio.packager import build_project_exe as _build

        project_dir = ctx.resolve(path)
        if not project_dir.is_dir():
            return ToolResult(False, "", error=f"no existe el directorio: {project_dir}")

        # MEGAPLAN v11 A3 — codigo ANTES del build. El 5-sep se invoco dos
        # veces sin un solo fichero escrito y el binario nacio sin fuente.
        if ctx.task_id:
            fuentes = ctx.get_journal().fuentes_de_tarea(
                ctx.task_id, bajo=str(project_dir))
            if not fuentes:
                return ToolResult(
                    False, "",
                    error=("no hay fichero .py escrito por esta tarea en "
                           f"{project_dir}: escribe el codigo con write_file "
                           "ANTES de compilar, o el .exe nacera sin fuente "
                           "que lo respalde (v11 A3)"))

        output_exe = ctx.resolve(output) if output else None
        icon_path = ctx.resolve(icon) if icon else None

        # Si no hay Python real disponible y no se proporcionó uno, advertir.
        if python_executable() is None:
            return ToolResult(
                False,
                "",
                error=(
                    "no hay intérprete Python disponible. "
                    "Dentro del .exe de MAGI se necesita Python embebido o "
                    "Python instalado en el sistema."
                ),
            )

        result = await _build(
            project_dir,
            entry=entry or None,
            output_exe=output_exe,
            name=name or None,
            icon=icon_path,
            console=console,
            requirements=requirements,
            hiddenimports=hiddenimports,
        )
        return result.to_tool_result()

    @reg.tool("create_venv", "Crea un entorno virtual Python limpio para "
              "reproducir el entorno de CI. Devuelve la ruta del python del venv.",
              {"type": "object",
               "properties": {"path": {"type": "string",
                                       "description": "ruta del venv (defecto: .venv)",
                                       "default": ".venv"},
                              "python": {"type": "string",
                                         "description": "intérprete base",
                                         "default": "python"}},
               "required": []}, access={"exec"}, dangerous=True)
    async def create_venv(path: str = ".venv", python: str = "python",
                          ctx: ToolContext | None = None):
        if ctx is None:
            return ToolResult(False, "", error="sin contexto")
        res = await run_command(f'"{python}" -m venv "{path}"', ctx=ctx, timeout=120)
        if res.ok:
            venv_python = (ctx.cwd / path / "Scripts" / "python.exe"
                           if sys.platform == "win32"
                           else ctx.cwd / path / "bin" / "python")
            res.meta = {**(res.meta or {}), "python": str(venv_python)}
        return res

    # §5.3 — toolchain de ingeniería inversa y emuladores.
    # Se registra aquí para que los tres nodos del enjambre lo tengan: sin este
    # enganche, todo magi/modules/reverse/ sería código correcto que ningún
    # agente puede invocar.
    # §6 — conocimiento del mundo: macro, actualidad, fundamentales y el
    # registro de tesis calibrado. Mismo motivo que el enganche de abajo: sin
    # esta línea, todo magi/modules/world/ sería andamiaje.
    try:
        from magi.modules.world.tools import register_world_tools
        register_world_tools(reg)
    except Exception as e:            # pragma: no cover
        import logging
        logging.getLogger(__name__).warning(
            "[tools] herramientas del mundo no disponibles: %s", e)

    try:
        from magi.modules.reverse.tools import register_reverse_tools
        register_reverse_tools(reg)
    except Exception as e:            # pragma: no cover
        import logging
        logging.getLogger(__name__).warning(
            "[tools] toolchain de RE no disponible: %s", e)

    # §5 — fábrica de artefactos con bucle de observación. Es lo que permite
    # que Balthasar ARRANQUE un juego y mire la captura en vez de opinar sobre
    # el código.
    try:
        from magi.modules.studio.tools import register_studio_tools
        register_studio_tools(reg)
    except Exception as e:            # pragma: no cover
        import logging
        logging.getLogger(__name__).warning(
            "[tools] fábrica de artefactos no disponible: %s", e)

    # R16 — los oídos. R9 puso ojos a las corridas; el sonido es la otra
    # mitad y el log no lo ve: `scsp_th` gasta lo mismo con audio limpio que
    # con audio a trompicones. El backend solo existe en Windows, así que el
    # registro va en try como el resto: en Linux la herramienta no está y se
    # dice, en vez de fingir un veredicto.
    try:
        from magi.modules.percepcion.tools import register_percepcion_tools
        register_percepcion_tools(reg)
    except Exception as e:            # pragma: no cover
        import logging
        logging.getLogger(__name__).warning(
            "[tools] oídos no disponibles: %s", e)

    # Fase 6 — buscar en la memoria sin gastar red. Medido: 224 documentos,
    # 2,7 MB, índice completo en 100 ms y consulta en 1 ms. Sin este enganche
    # `indice.py` seria otro modulo escrito, probado y que nadie llama — que es
    # lo que `test_wiring` caza y ya paso cuatro veces.
    try:
        from magi.modules.memory.tools import register_memory_tools
        register_memory_tools(reg)
    except Exception as e:            # pragma: no cover
        import logging
        logging.getLogger(__name__).warning(
            "[tools] busqueda en memoria no disponible: %s", e)

    return reg


# Perfiles por rol (Plan MAGI 9.0 §2.2).
MELCHIOR_TOOLS = None                      # todo: propone y construye
BALTHASAR_DENY: set[Access] = {"write"}    # lee y ejecuta, no escribe
CASPER_TOOLS = {"read_file", "list_dir", "grep", "glob", "run_tests",
                "run_command",
                # el árbitro necesita poder comprobar afirmaciones sobre
                # arquitecturas sin fiarse de lo que digan los otros dos
                "binary_identify", "console_profile", "analyze_port",
                "compare_consoles",
                # el árbitro debe poder mirar el artefacto, no fiarse del acta
                "observe_artifact", "inspect_image",
                # Y DEBE PODER ENTREGAR, no solo dictaminar.
                #
                # Casper es quien le habla al usuario: su síntesis ES la
                # respuesta. Con un perfil de solo lectura, lo máximo que podía
                # producir era una recomendación —«implementa el enfoque B»— y
                # el usuario se quedaba con un veredicto sobre algo que nadie
                # le había entregado.
                #
                # La síntesis dialéctica no es elegir entre la tesis y la
                # antítesis: es CONSTRUIR la superación de ambas. Para eso hace
                # falta escribir el fichero y ejecutarlo, evaluando lo que
                # propuso Melchior y lo que refutó Balthasar.
                #
                # No rompe la separación de roles: Balthasar sigue sin poder
                # escribir, que es lo que le da autoridad como crítico. El que
                # decide es también el que responde por lo que entrega.
                "write_file", "build_project_exe", "undo"}

# Los dominios de herramientas viven en `dominios.py` desde el 13-sep-2026:
# este fichero iba 839/800 tras repartir las herramientas que estaban fuera de
# toda caja, y el techo no se sube. Se reexportan porque media suite y varios
# módulos los importan desde aquí, y mover el import de todos ellos en el mismo
# commit habría mezclado dos cambios.
from .dominios import (  # noqa: F401
    ALL_DOMAINS,
    CORE_TOOLS,
    DEVOPS_TOOLS,
    REVERSE_TOOLS,
    STUDIO_TOOLS,
    WORLD_TOOLS,
    _DOMAIN_HINTS,
    _DOMAIN_TOOLSETS,
    domains_for,
)


def registry_for_role(role: str, task_hint: str = "") -> ToolRegistry:
    """
    Catálogo de un nodo, acotado al dominio de la tarea cuando se conoce.

    `task_hint` es el enunciado del usuario. Con él, una tarea de emuladores no
    carga el compositor de manga y viceversa: menos tokens por turno y menos
    ruido para el modelo.
    """
    base = build_registry()
    domains = domains_for(task_hint)

    allowed: set[str] | None = None
    if domains != ALL_DOMAINS:
        allowed = set(CORE_TOOLS)
        for dominio in domains:
            allowed |= _DOMAIN_TOOLSETS.get(dominio, set())

    r = role.upper()
    if r == "BALTHASAR":
        return base.subset(allowed=allowed, deny_access=BALTHASAR_DENY)
    if r == "CASPER":
        keep = CASPER_TOOLS if allowed is None else (CASPER_TOOLS & allowed)
        return base.subset(allowed=keep)
    return base.subset(allowed=allowed)
