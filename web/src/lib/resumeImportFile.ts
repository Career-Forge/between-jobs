// What is sent when a person chooses a resume file (PDF or DOCX) to import: the request, and the
// refusals that need no server.
//
// The route takes the file's raw bytes as the request body (POST /profile/import-document), with
// a Content-Type and an optional `?filename=`. The server decides what the file is from its bytes
// and answers 415 when the Content-Type or the extension disagrees, so this side must never pass
// along what a browser guessed. The Content-Type here comes from the first bytes of the file:
// `%PDF-` is a PDF, and the zip signature (every .docx is a zip) is a DOCX, which the server then
// checks properly (a zip that is not a Word document is refused there). The rules and the
// sentences are the server's (src/between_jobs/api/profile_import_extract.py, body_limit.py), so
// a refusal reads the same whichever side makes it.
//
// WHAT IS SENT OF THE FILE NAME. The server only uses the name to compare its extension with the
// bytes, and a file is often named after its owner ("Pat Example Resume.pdf"). A query string is
// written to access logs on the way, so the name is not sent: `resume.pdf` or `resume.docx` is,
// which keeps the check that matters (a file called .docx whose bytes are a PDF is still caught)
// and leaves the person's name out of the address. The review screen shows the real name, which
// never leaves the browser.
//
// Pure module: no React, no network. The file is read only through `slice(...).arrayBuffer()`.

export const PDF_CONTENT_TYPE = "application/pdf";
export const DOCX_CONTENT_TYPE =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export const IMPORT_PATH = "/profile/import-document";

// The server's cap on the body of this route (RESUME_UPLOAD_MAX_BYTES in body_limit.py).
export const MAX_IMPORT_BYTES = 5 * 1024 * 1024;

// The server's sentence for a request over its cap (`too_large_error`), word for word, so the
// refusal made here and the one the server would make are the same. The 5 MiB is the same number.
export const FILE_TOO_LARGE_MESSAGE =
  "That request is too large to send (the limit is 5 MiB). Shorten it and try again.";

export const EMPTY_FILE_MESSAGE = "That file is empty.";
export const NOT_PDF_OR_DOCX_MESSAGE =
  "That file is not a PDF or a DOCX. Only PDF or DOCX is supported.";
export const LEGACY_DOC_MESSAGE =
  "That looks like a legacy Word .doc file. Only PDF or DOCX is supported: open it in Word and save it as .docx or export it as a PDF.";
export const UNREADABLE_FILE_MESSAGE = "That file could not be read in this browser. Try choosing it again.";

export type DocumentKind = "pdf" | "docx";

export type SniffedKind = DocumentKind | "legacy_doc" | "empty" | "unknown";

// How many bytes decide what a file is: the longest signature below.
export const SNIFF_BYTES = 8;

const PDF_MAGIC = [0x25, 0x50, 0x44, 0x46, 0x2d]; // %PDF-
const OLE_MAGIC = [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]; // a legacy Word .doc
const ZIP_MAGICS = [
  [0x50, 0x4b, 0x03, 0x04], // PK\x03\x04
  [0x50, 0x4b, 0x05, 0x06], // PK\x05\x06
];

function startsWith(bytes: Uint8Array, signature: readonly number[]): boolean {
  return bytes.length >= signature.length && signature.every((byte, index) => bytes[index] === byte);
}

// What a file is, from its first bytes alone.
export function sniffKind(head: Uint8Array): SniffedKind {
  if (head.length === 0) return "empty";
  if (startsWith(head, PDF_MAGIC)) return "pdf";
  if (startsWith(head, OLE_MAGIC)) return "legacy_doc";
  if (ZIP_MAGICS.some((signature) => startsWith(head, signature))) return "docx";
  return "unknown";
}

export function contentTypeFor(kind: DocumentKind): string {
  return kind === "pdf" ? PDF_CONTENT_TYPE : DOCX_CONTENT_TYPE;
}

// The extension of a file name, lower-case with its dot ("" when it has none): the same reading
// the server makes of the name it is sent.
function extensionOf(name: string): string {
  const base = name.slice(Math.max(name.lastIndexOf("/"), name.lastIndexOf("\\")) + 1);
  const dot = base.lastIndexOf(".");
  return dot === -1 ? "" : base.slice(dot).toLowerCase();
}

// The name that goes in the address: `resume` plus the file's own extension when it is one the
// server compares (.pdf or .docx), and none at all otherwise. Never any part of the real name.
export function requestFilename(name: string): string | null {
  const extension = extensionOf(name);
  return extension === ".pdf" || extension === ".docx" ? `resume${extension}` : null;
}

export function importPath(name: string): string {
  const filename = requestFilename(name);
  return filename === null ? IMPORT_PATH : `${IMPORT_PATH}?filename=${encodeURIComponent(filename)}`;
}

// What `prepareImport` needs of a file: a browser's `File` is one, and so is a `Blob` with a name.
export interface ImportableFile {
  readonly name: string;
  readonly size: number;
  slice(start: number, end: number): { arrayBuffer(): Promise<ArrayBuffer> };
}

export interface ImportRequest<F> {
  path: string;
  contentType: string;
  // The file itself, sent as the body as it is.
  body: F;
  kind: DocumentKind;
}

export type PreparedImport<F> =
  | { ok: true; request: ImportRequest<F> }
  | { ok: false; message: string };

// Decides what to send for a chosen file, or why nothing can be: too large (over the server's cap),
// empty, a legacy .doc, or neither a PDF nor a DOCX by its bytes. The check on size comes first and
// reads nothing; the Content-Type is the one the first bytes give, never the browser's.
export async function prepareImport<F extends ImportableFile>(file: F): Promise<PreparedImport<F>> {
  if (file.size > MAX_IMPORT_BYTES) return { ok: false, message: FILE_TOO_LARGE_MESSAGE };
  if (file.size === 0) return { ok: false, message: EMPTY_FILE_MESSAGE };

  let head: Uint8Array;
  try {
    head = new Uint8Array(await file.slice(0, SNIFF_BYTES).arrayBuffer());
  } catch {
    return { ok: false, message: UNREADABLE_FILE_MESSAGE };
  }

  const kind = sniffKind(head);
  if (kind === "empty") return { ok: false, message: EMPTY_FILE_MESSAGE };
  if (kind === "legacy_doc") return { ok: false, message: LEGACY_DOC_MESSAGE };
  if (kind === "unknown") return { ok: false, message: NOT_PDF_OR_DOCX_MESSAGE };
  return {
    ok: true,
    request: { path: importPath(file.name), contentType: contentTypeFor(kind), body: file, kind },
  };
}
