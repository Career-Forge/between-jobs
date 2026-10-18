// The first tab stop on a page: a link that jumps past the navigation to the page's <main>. It is
// hidden until it has focus. The target must carry MAIN_CONTENT_ID and tabIndex={-1}, so that focus
// really moves there (a plain fragment jump scrolls but, in some browsers, leaves focus behind).
// The handler does the focus itself and keeps the address bar as it was: a #main-content fragment
// would otherwise be left in the URL of every page the visitor then shares or reloads.

export const MAIN_CONTENT_ID = "main-content";

export function SkipLink() {
  return (
    <a
      className="bj-skip-link"
      href={`#${MAIN_CONTENT_ID}`}
      onClick={(event) => {
        const target = document.getElementById(MAIN_CONTENT_ID);
        if (target === null) return;
        event.preventDefault();
        target.focus();
      }}
    >
      Skip to content
    </a>
  );
}
