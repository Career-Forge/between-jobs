import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { apiFetch, ApiError } from "../lib/api";
import { buildSessionReport } from "../lib/interviewPracticeReport";
import type {
  AnswerFeedback,
  InterviewQuestion,
  InterviewSession,
  ListSessionsResponse,
  SessionDetailResponse,
  SessionReport,
  StartSessionResponse,
  SubmitAnswerResponse,
} from "../lib/interviewPracticeTypes";
import { QUESTION_TYPE_LABELS } from "../lib/interviewPracticeTypes";
import { ScoreBar } from "./ScoreBreakdown";

// Interview practice panel (InterviewForge R4, interviewforge-v1.md) -- the
// frontend for R1-R3's already-shipped, live-verified engine: a fixed
// 5-question practice session per application, questions optionally
// grounded in a real cited interview_registry entry (R1), scored answer by
// answer against the candidate's own resume evidence (R2), persisted and
// served through the real credential-resolution flow (R3).
//
// On mount, this reads the most recent session (if any) and resumes into
// exactly where it left off -- an in-progress session lands straight on its
// first unanswered question, a completed one shows its report -- rather
// than making the candidate re-click "start" on every visit. `POST
// /sessions` always creates a brand-new session server-side (no dedup), so
// "Practice again" is a genuinely new session, same as every other
// generation-style action in this codebase (GeneratePanel's own
// `runGenerate`, CompanyIntelPanel's `generate`).

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string; setupRequired?: boolean; retry: () => void }
  | { kind: "idle" }
  | { kind: "active"; session: InterviewSession; questions: InterviewQuestion[] };

function currentQuestion(questions: InterviewQuestion[]): InterviewQuestion | null {
  return questions.find((q) => q.answered_at === null) ?? null;
}

export function InterviewPracticePanel({ applicationId }: { applicationId: string }) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [starting, setStarting] = useState(false);
  const [answerDraft, setAnswerDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [pendingFeedback, setPendingFeedback] = useState<AnswerFeedback | null>(null);
  const [lastSessionReport, setLastSessionReport] = useState<SessionReport | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const list = await apiFetch<ListSessionsResponse>(
        `/applications/${applicationId}/interview-practice/sessions`,
      );
      if (list.sessions.length === 0) {
        setState({ kind: "idle" });
        return;
      }
      const detail = await apiFetch<SessionDetailResponse>(
        `/applications/${applicationId}/interview-practice/sessions/${list.sessions[0].id}`,
      );
      setPendingFeedback(null);
      setLastSessionReport(null);
      setAnswerDraft("");
      setState({ kind: "active", session: detail.session, questions: detail.questions });
    } catch (e) {
      setState({
        kind: "error",
        message: e instanceof Error ? e.message : "Failed to load",
        retry: () => void load(),
      });
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function startSession() {
    setStarting(true);
    try {
      const result = await apiFetch<StartSessionResponse>(
        `/applications/${applicationId}/interview-practice/sessions`,
        { method: "POST" },
      );
      setPendingFeedback(null);
      setLastSessionReport(null);
      setAnswerDraft("");
      setSubmitError(null);
      setState({ kind: "active", session: result.session, questions: result.questions });
    } catch (e) {
      if (e instanceof ApiError && e.code === "SETUP_REQUIRED") {
        setState({
          kind: "error",
          message: e.message,
          setupRequired: true,
          retry: () => void startSession(),
        });
      } else {
        setState({
          kind: "error",
          message: e instanceof Error ? e.message : "Failed to start a practice session",
          retry: () => void startSession(),
        });
      }
    } finally {
      setStarting(false);
    }
  }

  async function submitAnswer() {
    if (state.kind !== "active") return;
    const question = currentQuestion(state.questions);
    if (!question) return;

    setSubmitting(true);
    setSubmitError(null);
    try {
      const response = await apiFetch<SubmitAnswerResponse>(
        `/applications/${applicationId}/interview-practice/sessions/${state.session.id}/answers`,
        { method: "POST", body: JSON.stringify({ answer_text: answerDraft }) },
      );
      const answeredAt = new Date().toISOString();
      const updatedQuestions = state.questions.map((q) =>
        q.id === question.id
          ? {
              ...q,
              answer_text: answerDraft,
              score: response.feedback.score,
              feedback: response.feedback,
              answered_at: answeredAt,
            }
          : q,
      );
      setState({
        kind: "active",
        session: response.session_report
          ? { ...state.session, status: "completed", completed_at: answeredAt }
          : state.session,
        questions: updatedQuestions,
      });
      setPendingFeedback(response.feedback);
      if (response.session_report) {
        setLastSessionReport(response.session_report);
      }
      setAnswerDraft("");
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "Failed to submit that answer");
    } finally {
      setSubmitting(false);
    }
  }

  function nextQuestion() {
    setPendingFeedback(null);
    setAnswerDraft("");
  }

  return (
    <div className="bj-card bj-interview-practice-panel">
      <h2>Interview practice</h2>
      <p className="bj-muted bj-small">
        Five mock questions for this role, grounded in your real resume evidence and -- when
        available -- a cited real interview-process brief for this company.
      </p>

      {state.kind === "loading" && <div className="bj-muted bj-small">Loading...</div>}

      {state.kind === "error" && (
        <div className="bj-error">
          {state.message}
          {state.setupRequired && (
            <>
              {" "}
              <Link to="/profile">Set up your profile</Link>
            </>
          )}{" "}
          <button onClick={() => state.retry()}>Try again</button>
        </div>
      )}

      {state.kind === "idle" && (
        <div className="bj-actions">
          <button className="bj-primary" onClick={() => void startSession()} disabled={starting}>
            {starting ? "Starting..." : "Start practice session"}
          </button>
        </div>
      )}

      {state.kind === "active" &&
        (() => {
          const question = currentQuestion(state.questions);

          if (pendingFeedback) {
            const star = pendingFeedback.star_coverage;
            const report = lastSessionReport ?? (question ? null : buildSessionReport(state.questions));
            return (
              <div className="bj-interview-feedback">
                <ScoreBar label="Your answer" value={pendingFeedback.score} max={10} />
                <div className="bj-muted bj-small">{pendingFeedback.structure_feedback}</div>
                <div className="bj-muted bj-small">{pendingFeedback.specificity_feedback}</div>
                <div className="bj-actions">
                  <span className={star.situation ? "bj-badge-emerald" : "bj-badge-muted"}>
                    Situation
                  </span>
                  <span className={star.task ? "bj-badge-emerald" : "bj-badge-muted"}>Task</span>
                  <span className={star.action ? "bj-badge-emerald" : "bj-badge-muted"}>
                    Action
                  </span>
                  <span className={star.result ? "bj-badge-emerald" : "bj-badge-muted"}>
                    Result
                  </span>
                </div>
                {pendingFeedback.improved_answer && (
                  <div>
                    <div className="bj-checklist-label">Improved answer</div>
                    <div className="bj-small">{pendingFeedback.improved_answer}</div>
                  </div>
                )}

                {report ? (
                  <SessionReportSummary
                    report={report}
                    onPracticeAgain={() => void startSession()}
                    starting={starting}
                  />
                ) : (
                  <div className="bj-actions">
                    <button className="bj-primary" onClick={nextQuestion}>
                      Next question
                    </button>
                  </div>
                )}
              </div>
            );
          }

          if (question) {
            return (
              <div className="bj-interview-question">
                <div className="bj-muted bj-small">
                  Question {question.ordinal + 1} of {state.questions.length} ·{" "}
                  {QUESTION_TYPE_LABELS[question.question_type]}
                </div>
                <div id={`interview-question-${question.id}`}>{question.question_text}</div>
                <div className="bj-muted bj-small">
                  {question.grounded_in
                    ? `Grounded in this company's real interview process: "${question.grounded_in}"`
                    : "General question -- no cited company-specific data behind this one."}
                </div>
                <textarea
                  value={answerDraft}
                  onChange={(e) => setAnswerDraft(e.target.value)}
                  rows={4}
                  placeholder="Answer as you would in the real interview."
                  disabled={submitting}
                  autoFocus
                  aria-labelledby={`interview-question-${question.id}`}
                />
                <div className="bj-actions">
                  <button
                    className="bj-primary"
                    onClick={() => void submitAnswer()}
                    disabled={submitting || answerDraft.trim().length === 0}
                  >
                    {submitting ? "Scoring..." : "Submit answer"}
                  </button>
                </div>
                {submitError && <div className="bj-error">{submitError}</div>}
              </div>
            );
          }

          // Resumed straight into a completed session with no just-submitted
          // feedback to show -- recompute the report client-side, mirroring
          // build_session_report exactly (see interviewPracticeReport.ts).
          const report = lastSessionReport ?? buildSessionReport(state.questions);
          return (
            <SessionReportSummary
              report={report}
              onPracticeAgain={() => void startSession()}
              starting={starting}
            />
          );
        })()}
    </div>
  );
}

function SessionReportSummary({
  report,
  onPracticeAgain,
  starting,
}: {
  report: SessionReport;
  onPracticeAgain: () => void;
  starting: boolean;
}) {
  return (
    <div className="bj-interview-report">
      <div className="bj-checklist-label">Session complete</div>
      <div className="bj-muted bj-small">
        {report.answered_count} of {report.question_count} answered
        {report.average_score !== null && ` · average score ${report.average_score} / 10`}
        {report.star_coverage_rate !== null &&
          ` · full STAR coverage on ${Math.round(report.star_coverage_rate * 100)}%`}
      </div>
      <div className="bj-actions">
        <button className="bj-primary" onClick={onPracticeAgain} disabled={starting}>
          {starting ? "Starting..." : "Practice again"}
        </button>
      </div>
    </div>
  );
}
