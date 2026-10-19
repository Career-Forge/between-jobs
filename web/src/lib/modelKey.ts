// What a GET /credentials answer says about the person's model keys, read one way for everything
// that asks: the setup checklist, the next-step band's "no usable key" row, and Today. (The Settings
// page reads each key's state itself, with the same llmSetupState, because it draws every provider.)
//
// A key is usable when it is checked AND has a model chosen: a checked key with no model is saved
// and still fails every generation (the API's `needs_model`). So "has a key row" is never the test.

import { llmSetupState } from "./settings/llmProviders";

// What the action for a saved key with no model is called wherever it is offered: the setup checklist,
// the band's "no usable key" row and the Settings page's band all say it in these words.
export const MODEL_CHOICE_LABEL = "Choose a model";

// "ready": some key is usable. Otherwise the furthest any key got: "needs_model" (checked, no model),
// "needs_key" (saved but the provider did not confirm it), or "none" (no model key row at all).
export type ModelKeyReading = "ready" | "needs_model" | "needs_key" | "none";

export function modelKeyOf(rows: readonly Record<string, unknown>[]): ModelKeyReading {
  const states = rows
    .filter((row) => row.service === "llm")
    .map((row) =>
      llmSetupState({
        is_validated: row.is_validated === true,
        model: typeof row.model === "string" && row.model !== "" ? row.model : null,
        setup_state: row.setup_state,
      }),
    );
  if (states.includes("ready")) return "ready";
  if (states.includes("needs_model")) return "needs_model";
  if (states.includes("needs_key")) return "needs_key";
  return "none";
}
