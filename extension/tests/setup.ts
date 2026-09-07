// jsdom has never implemented `DataTransfer` (a long-standing, documented
// gap -- confirmed directly against the installed jsdom version before
// writing this: `new (require("jsdom").JSDOM)().window.DataTransfer` is
// not a constructor). `attachResumeFile` (lib/lever.ts) is real browser
// code exercising the real spec-shaped API, but its unit test needs
// *something* implementing the shape to run under jsdom at all.
//
// This is a minimal shape polyfill, not a claim that it behaves
// identically to a real browser's DataTransfer in every respect -- it
// only covers what `attachResumeFile` actually calls (`items.add`,
// `.files`). It proves this project's OWN glue code (wraps a File,
// assigns `.files`, dispatches `change`) is correct; it cannot prove Chrome
// itself accepts a synthetic File the same way real DOM does. That's
// exactly the boundary between unit-test and live-browser verification,
// disclosed rather than silently blurred.
class FakeDataTransfer {
  private readonly _files: File[] = [];

  items = {
    add: (file: File) => {
      this._files.push(file);
    },
  };

  get files(): FileList {
    const files = this._files.slice();
    return Object.assign(files, {
      item: (index: number) => files[index] ?? null,
    }) as unknown as FileList;
  }
}

if (typeof globalThis.DataTransfer === "undefined") {
  // @ts-expect-error -- intentionally assigning a minimal shape, not the full lib.dom.d.ts interface
  globalThis.DataTransfer = FakeDataTransfer;
}
