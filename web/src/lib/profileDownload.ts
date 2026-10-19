// Saves a value as a JSON file through a link (the blank template, an export of the profile). A blob
// saved through a link is not something the Content-Security-Policy governs, and nothing is sent anywhere.

export function downloadJson(data: unknown, filename: string): void {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// A link's address as the browser should follow it: one with no scheme is taken to be https.
export function withProtocol(url: string): string {
  return /^https?:\/\//i.test(url) ? url : `https://${url}`;
}
