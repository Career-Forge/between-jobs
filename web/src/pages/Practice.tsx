import { Link } from "react-router-dom";

export default function Practice() {
  return (
    <div>
      <h1>Practice</h1>
      <div className="bj-empty">
        <h2>Practice lives inside each application</h2>
        <p>
          Interview practice is grounded in a specific job and your resume evidence for it, so
          there's no company-blind practice here. Open an application and use its Interview
          practice card to start a session.
        </p>
        <Link to="/applications">Go to Applications</Link>
      </div>
    </div>
  );
}
