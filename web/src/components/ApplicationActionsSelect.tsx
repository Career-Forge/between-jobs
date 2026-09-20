import { useNavigate } from "react-router-dom";
import { CROSS_NAV_ITEMS } from "../lib/applicationsBoard";
import { crossNavItemsFor } from "../lib/hiringSignalsStatus";
import { useHiringSignalsEnabled } from "../lib/useHiringSignalsStatus";

// The "Actions..." menu of an application (a row in the list, a card on the
// board): a jump to one panel of that application's workspace. One component for
// both, so the rule that decides which jumps are offered exists once:
// "Find Hiring Posts" is only offered while the server is known to have the
// feature on (`crossNavItemsFor`), because otherwise it would scroll to a panel
// that never renders. It is a component, not a helper each caller applies, so a
// test can render it and see which options a person actually gets.

export function ApplicationActionsSelect({
  applicationId,
  title,
}: {
  applicationId: string;
  title: string | null | undefined;
}) {
  const navigate = useNavigate();
  const items = crossNavItemsFor(CROSS_NAV_ITEMS, useHiringSignalsEnabled());
  return (
    <select
      aria-label={`Actions for "${title ?? "Untitled"}"`}
      value=""
      onChange={(e) => {
        const hash = e.target.value;
        if (hash) navigate(`/applications/${applicationId}#${hash}`);
      }}
    >
      <option value="">Actions...</option>
      {items.map((item) => (
        <option key={item.hash} value={item.hash}>
          {item.label}
        </option>
      ))}
    </select>
  );
}
