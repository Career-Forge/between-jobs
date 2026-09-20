import { CROSS_NAV_HASH } from "../lib/applicationsBoard";
import { HiringSignalsPanel } from "./HiringSignalsPanel";

// The "Hiring posts" slot of an application's workspace (Hiring Signals P4): the
// anchor a cross-nav link scrolls to, and the panel inside it -- which exists only
// while the server is known to have the feature on. With it off (or not yet known)
// the panel never mounts: no saved-posts request goes out, no empty card takes the
// slot, and the panel never appears just to disappear. The wrapper is always
// rendered (the hash target must exist); the app's stylesheet hides it while empty.
// A component of its own so a test can render it and see what each answer mounts.

export function HiringPostsSlot({
  enabled,
  applicationId,
}: {
  enabled: boolean;
  applicationId: string;
}) {
  return (
    <div id={CROSS_NAV_HASH.hiringPosts}>
      {enabled && <HiringSignalsPanel applicationId={applicationId} />}
    </div>
  );
}
