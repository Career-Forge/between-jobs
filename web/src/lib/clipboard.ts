// Copying text to the clipboard without ever throwing. `navigator.clipboard` is missing outside a
// secure context (a self-hosted server opened over plain http on a local network) and its write
// is rejected when the browser denies the permission; a caller that awaits it bare gets an
// unhandled rejection and a button that seems to do nothing. This answers instead whether it
// worked, so the caller can tell the person.

/** Whether `text` was copied. False when there is no clipboard here or the browser refused. */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (!navigator.clipboard) return false;
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
