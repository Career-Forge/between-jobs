import { Link } from "react-router-dom";

// What Today says when there is nothing in it yet. For someone who has just signed up that
// is the first thing they see, so it opens with the first step -- the profile, which every
// resume, score and practice session is built from -- rather than with a list of what the
// page will one day show.
export function TodayEmptyState() {
  return (
    <div className="bj-empty">
      <h2>Nothing here yet</h2>
      <p>
        Start with your <Link to="/profile">profile</Link>: every resume, score and practice
        session is built from it. Then add a model key in{" "}
        <Link to="/profile/integrations">Integrations</Link>, find a job on{" "}
        <Link to="/discover">Discover</Link>, and track it.
      </p>
      <p>
        After that, Today shows what actually happened: a job you tracked, a resume that generated
        (or didn't), a stage change, a high-fit new job found for one of your saved searches, or a
        Gmail reply worth a second look -- on Telegram, Discord or the web. It doesn't yet cover
        interview prep or stale-application nudges, since those don't exist yet.
      </p>
    </div>
  );
}
