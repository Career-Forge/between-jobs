// Where the Settings page lives. It was the Integrations page at /profile/integrations; the old
// address still works (the router sends it here, keeping its query and fragment) because the API
// names it in the `settings_path` of its setup errors and in links it has already sent.

export const SETTINGS_PATH = "/settings";
export const LEGACY_INTEGRATIONS_PATH = "/profile/integrations";

// The section of Settings where a model key is added and its model chosen (id="ai-provider").
export const MODEL_PROVIDER_PATH = `${SETTINGS_PATH}#ai-provider`;
