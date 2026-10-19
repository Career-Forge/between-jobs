import { PageHeader } from "../components/paper/PageHeader";
import { EmptyState } from "../components/paper/StateViews";
import { NOT_FOUND_PAGE_NAME } from "../lib/publicRoutes";

// What the signed-in shell draws at an address it has no page for (a mistyped address, an old bookmark): the
// page's one heading and a way out, instead of an empty column. It makes no request and has no next-step band
// (nothing here is a step), so the shell offers only "Skip to content".
export default function NotFound() {
  return (
    <>
      <PageHeader title={NOT_FOUND_PAGE_NAME} />
      <EmptyState heading="There is nothing at this address" link={{ label: "Go to Today", to: "/" }}>
        <p>Check the address for a typo, or use the menu to reach your own pages.</p>
      </EmptyState>
    </>
  );
}
