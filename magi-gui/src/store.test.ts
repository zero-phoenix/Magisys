/**
 * E1 — el plan vivo es de SU conversación, no del programa.
 *
 * Antes el store guardaba un único `plan` global: el último `task.plan` que
 * llegara se pintaba en todas las conversaciones, incluidas las que no tenían
 * ninguno. Abrir otra tarea enseñaba el plan de la anterior — peor que no
 * enseñar nada, porque parece información y no lo es.
 */
import { beforeEach, describe, expect, it } from "vitest";

import { useMagiStore } from "./store";

const planDe = (taskId: string, descripcion: string) => ({
  task_id: taskId,
  items: [{ id: "parte_1", descripcion, estado: "pendiente", motivo: "" }],
});

describe("planes por conversación", () => {
  beforeEach(() => {
    useMagiStore.setState({ planes: {}, activeConversationId: "default" });
  });

  it("dos conversaciones conservan cada una su plan", () => {
    const { setPlan } = useMagiStore.getState();

    setPlan(planDe("t-1", "instrumentar el SH2"));
    setPlan(planDe("t-2", "dibujar la portada"));

    const { planes } = useMagiStore.getState();
    expect(planes["t-1"].items[0].descripcion).toBe("instrumentar el SH2");
    expect(planes["t-2"].items[0].descripcion).toBe("dibujar la portada");
  });

  it("el plan de una tarea no pisa el de otra", () => {
    const { setPlan } = useMagiStore.getState();

    setPlan(planDe("t-1", "primero"));
    setPlan(planDe("t-2", "segundo"));

    expect(Object.keys(useMagiStore.getState().planes)).toHaveLength(2);
  });

  it("un plan sin task_id se guarda en la conversación activa, nunca en otra", () => {
    useMagiStore.setState({ activeConversationId: "t-activa" });

    useMagiStore.getState().setPlan({ items: [{ id: "p", descripcion: "x" }] });

    const { planes } = useMagiStore.getState();
    expect(planes["t-activa"]).toBeTruthy();
    expect(Object.keys(planes)).toEqual(["t-activa"]);
  });

  it("un plan nulo no borra los que ya había", () => {
    const { setPlan } = useMagiStore.getState();
    setPlan(planDe("t-1", "sigue aquí"));

    setPlan(null);

    expect(useMagiStore.getState().planes["t-1"]).toBeTruthy();
  });
});
