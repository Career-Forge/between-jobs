import { Link } from "react-router-dom";
import { PROFILE_PATH } from "../lib/enrollment";
import { MODEL_PROVIDER_PATH } from "../lib/settingsPaths";
import { KIND_LABELS } from "../lib/todayFeed";
import { EmptyState } from "./paper/StateViews";

// What Today says when there is nothing in it yet. For someone who has just signed up that is the
// first thing they see, so it opens with the first step -- the profile, which every resume, score
// and practice session is built from -- and then names what the page will show, by the real kinds
// of item and what each one needs. No example rows and no sample numbers: nothing here is theirs.
// The band holds the next step's button, so the links here are the ones inside the sentence.
export function TodayEmptyState() {
  return (
    <EmptyState heading="Nothing here yet">
      <p>
        Start with your <Link to={PROFILE_PATH}>profile</Link>: every resume, score and practice
        session is built from it. Then add a model key in{" "}
        <Link to={MODEL_PROVIDER_PATH}>Settings</Link>, find a job on{" "}
        <Link to="/discover">Discover</Link>, and track it.
      </p>
      <p>After that, Today shows what actually happened, whether it came from Telegram, Discord or the web:</p>
      <ul className="bj-today-kinds">
        <li>
          {KIND_LABELS.high_fit_job} -- needs a saved search in Discover
        </li>
        <li>
          {KIND_LABELS.status_proposal} -- needs Gmail connected
        </li>
        <li>{KIND_LABELS.resume_ready}</li>
        <li>{KIND_LABELS.resume_failed}</li>
        <li>{KIND_LABELS.job_tracked}</li>
        <li>{KIND_LABELS.stage_changed}</li>
      </ul>
      <p>It doesn&apos;t yet cover interview prep or stale-application nudges, since those don&apos;t exist yet.</p>
    </EmptyState>
  );
}
