import { describe, expect, it } from "vitest";
import bodyLimit from "../../../src/between_jobs/api/body_limit.py?raw";
import extract from "../../../src/between_jobs/api/profile_import_extract.py?raw";
import {
  DOCX_CONTENT_TYPE,
  EMPTY_FILE_MESSAGE,
  FILE_TOO_LARGE_MESSAGE,
  IMPORT_PATH,
  LEGACY_DOC_MESSAGE,
  MAX_IMPORT_BYTES,
  NOT_PDF_OR_DOCX_MESSAGE,
  PDF_CONTENT_TYPE,
  SNIFF_BYTES,
  UNREADABLE_FILE_MESSAGE,
  contentTypeFor,
  importPath,
  prepareImport,
  requestFilename,
  sniffKind,
} from "./resumeImportFile";

const PDF = [0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x37, 0x0a, 0x25];
const ZIP = [0x50, 0x4b, 0x03, 0x04, 0x14, 0x00, 0x06, 0x00];
const OLE = [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1, 0x00, 0x00];

function file(bytes: readonly number[], name: string, type = ""): File {
  return new File([new Uint8Array(bytes)], name, { type });
}

describe("sniffKind", () => {
  it("reads a PDF by %PDF-, a DOCX by the zip signature, and nothing else as either", () => {
    expect(sniffKind(new Uint8Array(PDF))).toBe("pdf");
    expect(sniffKind(new Uint8Array(ZIP))).toBe("docx");
    expect(sniffKind(new Uint8Array([0x50, 0x4b, 0x05, 0x06, 0, 0]))).toBe("docx"); // an empty zip's signature
    expect(sniffKind(new Uint8Array(OLE))).toBe("legacy_doc");
    expect(sniffKind(new Uint8Array([]))).toBe("empty");
    for (const other of [
      [0x50, 0x4b, 0x07, 0x08], // PK, but not an archive start
      [0x50, 0x4b], // too short for the signature
      [0x25, 0x50, 0x44, 0x46], // "%PDF" with no dash
      [0x7b, 0x22, 0x70], // JSON
      [0xff, 0xd8, 0xff, 0xe0], // a JPEG
      [0x20, 0x25, 0x50, 0x44, 0x46, 0x2d], // a space before %PDF-
    ]) {
      expect(sniffKind(new Uint8Array(other)), JSON.stringify(other)).toBe("unknown");
    }
  });

  // The longest signature is the server's own (the legacy .doc one): read from its source, so that
  // neither side can change without this test saying so.
  const serverOle = (): number[] => {
    const hex = /_OLE_MAGIC = bytes\.fromhex\("([0-9a-f]+)"\)/.exec(extract);
    if (hex === null) throw new Error("profile_import_extract.py no longer has _OLE_MAGIC");
    return (hex[1].match(/../g) ?? []).map((pair) => parseInt(pair, 16));
  };

  it("looks at no more bytes than the longest signature needs", () => {
    const longest = Math.max(serverOle().length, "%PDF-".length, 4);
    expect(SNIFF_BYTES).toBe(longest);
    expect(longest).toBe(8);
  });

  it("takes a legacy .doc only when every byte of the server's signature is there, not seven of eight", () => {
    const signature = serverOle();
    expect(sniffKind(new Uint8Array(signature))).toBe("legacy_doc");
    for (let index = 0; index < signature.length; index += 1) {
      const altered = [...signature];
      altered[index] = altered[index] ^ 0xff;
      expect(sniffKind(new Uint8Array(altered)), `byte ${index}`).toBe("unknown");
    }
    // the first seven bytes and a different last one, which is not a legacy Word file
    expect(sniffKind(new Uint8Array([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0x00]))).toBe("unknown");
    // and a prefix of it is not enough
    expect(sniffKind(new Uint8Array(signature.slice(0, 7)))).toBe("unknown");
  });
});

describe("the request for a file", () => {
  it("is a PDF sent as application/pdf, whatever the browser called it", async () => {
    for (const browserType of ["", "application/octet-stream", "text/plain", "application/msword"]) {
      const prepared = await prepareImport(file(PDF, "resume.pdf", browserType));
      expect(prepared.ok).toBe(true);
      if (!prepared.ok) throw new Error("unreachable");
      expect(prepared.request.contentType).toBe("application/pdf");
      expect(prepared.request.kind).toBe("pdf");
    }
  });

  it("is a DOCX sent as the Word document type, whatever the browser called it", async () => {
    for (const browserType of ["", "application/zip", "application/pdf"]) {
      const prepared = await prepareImport(file(ZIP, "resume.docx", browserType));
      if (!prepared.ok) throw new Error(prepared.message);
      expect(prepared.request.contentType).toBe(DOCX_CONTENT_TYPE);
      expect(prepared.request.kind).toBe("docx");
    }
  });

  it("goes by the bytes, not the extension: a PDF called .docx is sent as a PDF, and the server says so", async () => {
    const prepared = await prepareImport(file(PDF, "resume.docx", DOCX_CONTENT_TYPE));
    if (!prepared.ok) throw new Error(prepared.message);
    expect(prepared.request.contentType).toBe(PDF_CONTENT_TYPE);
    expect(prepared.request.path).toBe("/profile/import-document?filename=resume.docx");
  });

  it("sends the file itself as the body, to the import route", async () => {
    const chosen = file(PDF, "x.pdf");
    const prepared = await prepareImport(chosen);
    if (!prepared.ok) throw new Error(prepared.message);
    expect(prepared.request.body).toBe(chosen);
    expect(prepared.request.path.startsWith(IMPORT_PATH)).toBe(true);
    expect(IMPORT_PATH).toBe("/profile/import-document");
  });

  it("matches the server's own content types and signatures", () => {
    expect(extract).toContain(`_PDF_CONTENT_TYPES = frozenset({"${PDF_CONTENT_TYPE}"})`);
    expect(extract).toContain(DOCX_CONTENT_TYPE);
    expect(extract).toContain('_PDF_MAGIC = b"%PDF-"');
    expect(extract).toContain('_ZIP_MAGICS = (b"PK\\x03\\x04", b"PK\\x05\\x06")');
    expect(extract).toContain('_OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")');
    expect(contentTypeFor("pdf")).toBe(PDF_CONTENT_TYPE);
    expect(contentTypeFor("docx")).toBe(DOCX_CONTENT_TYPE);
  });
});

describe("what is sent of the file's name", () => {
  it("is resume plus the real extension, never the person's own words", () => {
    expect(requestFilename("Pat Example Resume.pdf")).toBe("resume.pdf");
    expect(requestFilename("CV FINAL (2).DOCX")).toBe("resume.docx");
    expect(requestFilename("C:\\Users\\pat\\cv.pdf")).toBe("resume.pdf");
    expect(requestFilename("/home/pat/cv.docx")).toBe("resume.docx");
    expect(importPath("Pat Example Resume.pdf")).toBe("/profile/import-document?filename=resume.pdf");
  });

  it("is nothing for an extension the server does not compare, or none, so the bytes decide alone", () => {
    for (const name of ["", "resume", "resume.txt", "resume.doc", ".pdf.exe", "pdf", "notes.pdf.bak"]) {
      expect(requestFilename(name), name).toBeNull();
      expect(importPath(name), name).toBe("/profile/import-document");
    }
    // a bare extension is a file called ".pdf": no name, but the extension is the same
    expect(requestFilename(".pdf")).toBe("resume.pdf");
  });

  it("leaves no trace of an awkward name in the address, and cannot fail on one", () => {
    for (const name of [
      "R\u00e9sum\u00e9 de Jos\u00e9 \u{1F680}.pdf",
      "a b&c=d?e#f.pdf",
      "\ud800lone-surrogate.pdf", // encodeURIComponent would throw on the real name
      `${"x".repeat(1000)}.docx`,
      "..\\..\\etc\\passwd.pdf",
    ]) {
      const path = importPath(name);
      expect(path === "/profile/import-document?filename=resume.pdf" || path === "/profile/import-document?filename=resume.docx", name).toBe(true);
    }
  });
});

describe("a file that is refused here, before anything is sent", () => {
  it("is over the server's cap, with the server's own sentence", async () => {
    const big = { name: "big.pdf", size: MAX_IMPORT_BYTES + 1, slice: () => ({ arrayBuffer: async () => new Uint8Array(PDF).buffer }) };
    const prepared = await prepareImport(big);
    expect(prepared).toEqual({ ok: false, message: FILE_TOO_LARGE_MESSAGE });
    expect(MAX_IMPORT_BYTES).toBe(5 * 1024 * 1024);
  });

  it("is allowed at exactly the cap, which the server also allows", async () => {
    const edge = { name: "edge.pdf", size: MAX_IMPORT_BYTES, slice: () => ({ arrayBuffer: async () => new Uint8Array(PDF).buffer as ArrayBuffer }) };
    const prepared = await prepareImport(edge);
    expect(prepared.ok).toBe(true);
  });

  it("reads nothing of a file that is too large", async () => {
    let reads = 0;
    const big = {
      name: "big.pdf",
      size: MAX_IMPORT_BYTES + 1,
      slice: () => {
        reads += 1;
        return { arrayBuffer: async () => new ArrayBuffer(0) };
      },
    };
    await prepareImport(big);
    expect(reads).toBe(0);
  });

  it("uses the same sentence and the same number as the server", () => {
    expect(bodyLimit).toContain("RESUME_UPLOAD_MAX_BYTES = 5 * 1024 * 1024");
    expect(bodyLimit).toContain('f"That request is too large to send (the limit is {describe_bytes(limit)}). "');
    expect(bodyLimit).toContain('"Shorten it and try again."');
    expect(FILE_TOO_LARGE_MESSAGE).toBe(
      "That request is too large to send (the limit is 5 MiB). Shorten it and try again.",
    );
  });

  it("is empty", async () => {
    expect(await prepareImport(file([], "empty.pdf"))).toEqual({ ok: false, message: EMPTY_FILE_MESSAGE });
    expect(extract).toContain(`"${EMPTY_FILE_MESSAGE}"`);
  });

  it("reports a file that says it has bytes but gives none as empty, in the same words", async () => {
    // a size above zero and nothing read from it (a file changed or removed after it was chosen)
    const vanished = { name: "x.pdf", size: 10, slice: () => ({ arrayBuffer: async () => new ArrayBuffer(0) }) };
    expect(await prepareImport(vanished)).toEqual({ ok: false, message: EMPTY_FILE_MESSAGE });
  });

  it("is a legacy .doc, with the server's advice", async () => {
    expect(await prepareImport(file(OLE, "old.doc"))).toEqual({ ok: false, message: LEGACY_DOC_MESSAGE });
    expect(extract).toContain("That looks like a legacy Word .doc file. Only PDF or DOCX is supported: ");
    expect(extract).toContain('"open it in Word and save it as .docx or export it as a PDF."');
  });

  it("is neither a PDF nor a DOCX by its bytes, whatever its name says", async () => {
    for (const [bytes, name] of [
      [[0x7b, 0x22, 0x61, 0x22, 0x7d], "resume.pdf"],
      [[0xff, 0xd8, 0xff, 0xe0, 0, 0], "photo.docx"],
      [[0x68, 0x69], "notes.txt"],
    ] as const) {
      expect(await prepareImport(file(bytes, name, "application/pdf")), name).toEqual({
        ok: false,
        message: NOT_PDF_OR_DOCX_MESSAGE,
      });
    }
    expect(extract).toContain('"That file is not a PDF or a DOCX. Only PDF or DOCX is supported."');
  });

  it("cannot be read by the browser", async () => {
    const broken = {
      name: "x.pdf",
      size: 100,
      slice: () => ({
        arrayBuffer: async () => {
          throw new Error("NotReadableError");
        },
      }),
    };
    expect(await prepareImport(broken)).toEqual({ ok: false, message: UNREADABLE_FILE_MESSAGE });
  });
});
