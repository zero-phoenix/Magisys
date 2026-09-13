/**
 * Tipos de la aprobación con contexto (§7.4).
 *
 * Espejo de `magi/core/approval.py`. Los dos lados tienen que decir lo mismo,
 * y hay un test en Python que comprueba que estos campos coinciden con los
 * que el backend publica de verdad — porque un contrato entre dos lenguajes
 * que nadie verifica se desincroniza a la primera.
 */

export interface FileChange {
  path: string;
  before: string;
  after: string;
  /** creado | modificado | borrado */
  kind: string;
  /** Motivo por el que no se muestra el contenido (binario, ilegible…). */
  note: string;
  /** Si el journal conserva con qué revertirlo (§4.2). */
  revertible: boolean;
}

/**
 * Procedencia del artefacto (v11 A1).
 *
 * El packager escribe `<exe>.manifest.json` con el sha256 del ejecutable y el
 * de cada fuente. Hasta la v5.28 eso se calculaba y se quedaba en disco: quien
 * aprobaba un binario no veía de dónde salía. Va resumido a propósito — el
 * manifiesto entero son cientos de hashes y aquí solo sirve lo que se
 * comprueba de un vistazo.
 *
 * Vacío (`{}`) cuando la tarea no produjo ningún artefacto, que es lo normal.
 */
export interface ManifiestoArtefacto {
  exe?: string;
  sha256?: string;
  entry?: string;
  fuentes?: number;
  /** Dónde está el manifiesto entero, para quien quiera abrirlo. */
  ruta?: string;
}

export interface ApprovalRequest {
  task_id: string;
  summary: string;
  changes: FileChange[];
  commands: string[];
  tests_ran: boolean;
  tests_passed: boolean;
  tests_detail: string;
  reversible: boolean;
  /** Motivo si el journal no se pudo leer. Con esto, "no toca ningún fichero"
   *  deja de ser una afirmación y pasa a ser un "no lo sé". */
  journal_error: string;
  files_touched: number;
  /** A1: procedencia del .exe. `{}` si la tarea no generó ninguno. */
  manifiesto: ManifiestoArtefacto;
}

/**
 * Qué debería hacer el usuario, dicho sin rodeos.
 *
 * Un panel de aprobación que se limita a mostrar datos deja el trabajo a
 * medias: lo que importa es si hay motivo para desconfiar. Los tests en rojo
 * y la irreversibilidad son las dos cosas que cambian la decisión, así que se
 * dicen arriba y no enterradas entre líneas de código.
 */
export function approvalWarnings(a: ApprovalRequest): string[] {
  const avisos: string[] = [];
  if (!a.tests_ran) {
    avisos.push(
      "No se ejecutaron tests: no hay ninguna evidencia de que esto no rompa algo.",
    );
  } else if (!a.tests_passed) {
    avisos.push(
      `Los tests están EN ROJO. ${a.tests_detail || ""}`.trim(),
    );
  }
  if (a.journal_error) {
    avisos.push(
      `No se pudo leer el journal (${a.journal_error}). No se sabe qué ` +
      "ficheros toca esto ni si se puede deshacer. Trátalo como irreversible.",
    );
  } else if (!a.reversible) {
    avisos.push(
      "Sin copia previa en el journal: esto NO se puede deshacer solo.",
    );
  }
  if (a.commands.length) {
    avisos.push(
      `Se ejecutarán ${a.commands.length} órdenes en tu máquina. Léelas antes.`,
    );
  }
  if (!a.changes.length && !a.commands.length && !a.journal_error) {
    avisos.push(
      "No toca ningún fichero ni ejecuta nada: no hay cambio que aprobar.",
    );
  }
  return avisos;
}
