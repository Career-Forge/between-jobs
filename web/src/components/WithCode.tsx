// A sentence from the content files with its backtick-quoted words drawn as code: "start `uvicorn`" is
// "start " and <code>uvicorn</code>. A sentence with no backtick comes out unchanged.

export function WithCode({ text }: { text: string }) {
  return (
    <>
      {text.split(/`([^`]+)`/).map((part, index) => (index % 2 === 1 ? <code key={index}>{part}</code> : part))}
    </>
  );
}
