import { useCallback, useEffect, useId, useRef, useState, type RefObject } from "react";
import { apiFetch } from "../lib/api";
import { buildSessionReport, starApplies, starSummary } from "../lib/interviewPracticeReport";
import type {
  InterviewQuestion,
  InterviewSession,
  ListSessionsResponse,
  SessionDetailResponse,
  SessionReport,
  StartSessionResponse,
  SubmitAnswerResponse,
} from "../lib/interviewPracticeTypes";
import { QUESTION_TYPE_LABELS } from "../lib/interviewPracticeTypes";
import { friendlyApiMessage } from "../lib/rateLimitMessage";
import { answerTrail, failedTrail, sessionTrail } from "../lib/researchTrails";
import { failureOf, type Failure } from "../lib/setupRequired";
import { formatTrailDate } from "../lib/trail";
import { useNow } from "../lib/useNow";
import { BlockTrail } from "./paper/DocumentLayout";
import { SegmentedBar } from "./paper/SegmentedBar";
import { FailureState, LoadingState } from "./paper/StateViews";
import { GlyphMark, TrailLine } from "./paper/TrailLine";
import { EmptyBlock } from "./research/EmptyBlock";
import { SetupRequiredNotice } from "./SetupRequiredNotice";
import "../styles/workspace.css";
import "../styles/research.css";

// Interview practice: a fixed five-question practice session per application, questions grounded in the
// person's own resume evidence and, when one was found, a cited interview-process brief for the company;
// each answer scored by the person's model against that evidence.
//
// On mount the panel reads the most recent session and picks up exactly where it was: an in-progress one
// lands on its first unanswered question with the answers already given above it (each carries the score
// and feedback the server stored), a completed one shows its report. `POST /sessions` always makes a brand
// new session, so "Practice again" is genuinely new.
//
// What each part says about who did it: the questions are the model's (the block's trail), and each answer
// carries its own line, "Scored by your model" with the time the server stored it. The score, the STAR
// parts and the comments are a model's judgment, so the STAR parts are gold segments with the words, never
// a green tick, and the caption says so. Grounding in company research says that research is shared.
//
// States: a placeholder while the sessions are read; empty teaching before the first session; a failed
// read replaces the panel with a Retry; a failed start or answer keeps what is on screen (and the draft of
// the answer) and says what failed beside it; a missing key or profile is a notice with the link that
// fixes it and no Retry.

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "idle" }
  | { kind: "active"; session: InterviewSession; questions: InterviewQuestion[] };

function currentQuestion(questions: InterviewQuestion[]): InterviewQuestion | null {
  return questions.find((q) => q.answered_at === null) ?? null;
}

type FocusTarget = "answer" | "feedback";

export function InterviewPracticePanel({ applicationId, now }: { applicationId: string; now?: Date }) {
  const clock = useNow(now);
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [starting, setStarting] = useState(false);
  const [startProblem, setStartProblem] = useState<Failure | null>(null);
  const [answerDraft, setAnswerDraft] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitProblem, setSubmitProblem] = useState<Failure | null>(null);
  // The question whose answer was just scored and whose feedback is on screen until "Next question".
  const [pendingId, setPendingId] = useState<string | null>(null);
  // Questions answered in this visit: the server returned no time for them, so their line has none.
  const [answeredHere, setAnsweredHere] = useState<ReadonlySet<string>>(new Set());
  const [openIds, setOpenIds] = useState<ReadonlySet<string>>(new Set());
  const [lastSessionReport, setLastSessionReport] = useState<SessionReport | null>(null);
  const answerRef = useRef<HTMLTextAreaElement>(null);
  const feedbackRef = useRef<HTMLHeadingElement>(null);
  const wantFocus = useRef<FocusTarget | null>(null);
  const working = useRef(false);

  // A control that is replaced by its own result hands keyboard focus on, so it is not dropped on the page.
  useEffect(() => {
    const target = wantFocus.current;
    if (target === null) return;
    const element = target === "answer" ? answerRef.current : feedbackRef.current;
    if (element === null) return;
    wantFocus.current = null;
    element.focus();
  });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    setStartProblem(null);
    try {
      const list = await apiFetch<ListSessionsResponse>(`/applications/${applicationId}/interview-practice/sessions`);
      if (list.sessions.length === 0) {
        setState({ kind: "idle" });
        return;
      }
      const detail = await apiFetch<SessionDetailResponse>(`/applications/${applicationId}/interview-practice/sessions/${list.sessions[0].id}`);
      setPendingId(null);
      setAnsweredHere(new Set());
      setOpenIds(new Set());
      setLastSessionReport(null);
      setAnswerDraft("");
      setState({ kind: "active", session: detail.session, questions: detail.questions });
    } catch (e) {
      setState({ kind: "error", message: friendlyApiMessage(e, "Failed to load") });
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function startSession() {
    if (working.current) return;
    working.current = true;
    setStarting(true);
    setStartProblem(null);
    try {
      const result = await apiFetch<StartSessionResponse>(`/applications/${applicationId}/interview-practice/sessions`, { method: "POST" });
      setPendingId(null);
      setAnsweredHere(new Set());
      setOpenIds(new Set());
      setLastSessionReport(null);
      setAnswerDraft("");
      setSubmitProblem(null);
      wantFocus.current = "answer";
      setState({ kind: "active", session: result.session, questions: result.questions });
    } catch (e) {
      setStartProblem(failureOf(e, "Failed to start a practice session"));
    } finally {
      working.current = false;
      setStarting(false);
    }
  }

  async function submitAnswer() {
    if (state.kind !== "active" || working.current) return;
    const question = currentQuestion(state.questions);
    if (!question || answerDraft.trim().length === 0) return;

    working.current = true;
    setSubmitting(true);
    setSubmitProblem(null);
    try {
      const response = await apiFetch<SubmitAnswerResponse>(`/applications/${applicationId}/interview-practice/sessions/${state.session.id}/answers`, {
        method: "POST",
        body: JSON.stringify({ answer_text: answerDraft }),
      });
      // The server answers with the feedback and no time, so this visit's answer is marked answered with
      // the browser's clock for ordering only; its line says nothing of when (answeredHere).
      const answeredAt = new Date().toISOString();
      const updatedQuestions = state.questions.map((q) =>
        q.id === question.id ? { ...q, answer_text: answerDraft, score: response.feedback.score, feedback: response.feedback, answered_at: answeredAt } : q,
      );
      setAnsweredHere((previous) => new Set(previous).add(question.id));
      setPendingId(question.id);
      wantFocus.current = "feedback";
      setState({
        kind: "active",
        // A session finished in this visit has no completion time to show: none came back.
        session: response.session_report ? { ...state.session, status: "completed", completed_at: null } : state.session,
        questions: updatedQuestions,
      });
      if (response.session_report) setLastSessionReport(response.session_report);
      setAnswerDraft("");
    } catch (e) {
      setSubmitProblem(failureOf(e, "Failed to submit that answer"));
    } finally {
      working.current = false;
      setSubmitting(false);
    }
  }

  function nextQuestion() {
    setPendingId(null);
    setAnswerDraft("");
    wantFocus.current = "answer";
  }

  function toggleOpen(id: string) {
    setOpenIds((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const active = state.kind === "active" ? state : null;
  const questions = active === null ? [] : active.questions;
  const pending = active === null || pendingId === null ? null : (questions.find((q) => q.id === pendingId) ?? null);
  const current = active === null || pending !== null ? null : currentQuestion(questions);
  const earlier = questions.filter((q) => q.answered_at !== null && q.id !== pendingId);
  const finished = active !== null && current === null;
  const report = active === null ? null : (lastSessionReport ?? (finished ? buildSessionReport(questions) : null));

  const trail =
    active !== null
      ? sessionTrail(active.session, questions, clock)
      : state.kind === "error"
        ? failedTrail("Practice couldn't be read")
        : null;

  return (
    <div className="bj-rs bj-rs-practice">
      <p className="bj-ws-intro">
        Five mock questions for this role, asked by your model and grounded in your resume evidence and, when one was found, a cited interview-process brief for this company. Each answer is scored by your model
        on your key.
      </p>

      {state.kind === "loading" && <LoadingState rows={2} rowHeight={56} label="Loading practice sessions..." />}

      {state.kind === "error" && <FailureState message={state.message} onRetry={() => void load()} />}

      {state.kind === "idle" && (
        <>
          <EmptyBlock heading="No practice session yet">
            <p>
              A session is five questions for this role: behavioral, technical and situational. After each answer you get a score out of 10, comments on its structure and its specifics, the STAR parts it
              covers where STAR applies, and a stronger version of your answer.
            </p>
            <p>It needs a model key and an active profile.</p>
          </EmptyBlock>
          <div className="bj-actions">
            <button type="button" aria-disabled={starting ? true : undefined} onClick={() => void startSession()}>
              {starting ? "Starting..." : "Start practice session"}
            </button>
          </div>
        </>
      )}

      {active !== null && (
        <>
          {finished && active.session.completed_at !== null && (
            <p className="bj-rs-meta">Session completed {formatTrailDate(active.session.completed_at, { now: clock }) ?? ""}</p>
          )}
          {earlier.length > 0 && (
            <ol className="bj-rs-answers" aria-label="Answers so far">
              {earlier.map((question) => {
                const open = openIds.has(question.id);
                const panelId = `practice-answer-${question.id}`;
                const score = question.score ?? question.feedback?.score ?? null;
                return (
                  <li key={question.id} className="bj-rs-answer">
                    <button type="button" className="bj-rs-answer-row" aria-expanded={open} aria-controls={open ? panelId : undefined} onClick={() => toggleOpen(question.id)}>
                      <span className="bj-rs-answer-name">
                        Question {question.ordinal + 1} · {QUESTION_TYPE_LABELS[question.question_type]}
                      </span>{" "}
                      <span className="bj-rs-answer-score">{score === null ? "No score stored" : `${score} / 10`}</span>
                    </button>
                    {open && (
                      <div id={panelId}>
                        <AnswerReview question={question} now={clock} savedEarlier={!answeredHere.has(question.id)} headingLevel={4} />
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
          )}

          {pending !== null && (
            <div className="bj-rs-feedback">
              <AnswerReview question={pending} now={clock} savedEarlier={false} headingLevel={3} headingRef={feedbackRef} />
              {currentQuestion(questions) !== null && (
                <div className="bj-actions">
                  <button type="button" onClick={nextQuestion}>
                    Next question
                  </button>
                </div>
              )}
            </div>
          )}

          {pending === null && current !== null && (
            <QuestionForm
              question={current}
              total={questions.length}
              draft={answerDraft}
              onDraft={setAnswerDraft}
              submitting={submitting}
              problem={submitProblem}
              onSubmit={() => void submitAnswer()}
              textareaRef={answerRef}
            />
          )}

          {finished && report !== null && (
            <SessionReportSummary report={report} questions={questions} onPracticeAgain={() => void startSession()} starting={starting} />
          )}
        </>
      )}

      {state.kind !== "loading" && state.kind !== "error" && startProblem !== null && (
        <div className="bj-rs-issue">
          {startProblem.kind === "setup" ? <SetupRequiredNotice notice={startProblem.notice} /> : <FailureState message={startProblem.message} onRetry={() => void startSession()} />}
        </div>
      )}

      {trail !== null && <BlockTrail trail={trail.trail} receipt={trail.receipt} receiptTitle={trail.receiptTitle} />}
    </div>
  );
}

function QuestionForm({
  question,
  total,
  draft,
  onDraft,
  submitting,
  problem,
  onSubmit,
  textareaRef,
}: {
  question: InterviewQuestion;
  total: number;
  draft: string;
  onDraft: (value: string) => void;
  submitting: boolean;
  problem: Failure | null;
  onSubmit: () => void;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const questionId = useId();
  const empty = draft.trim().length === 0;
  return (
    <div className="bj-rs-question">
      <p className="bj-rs-eyebrow">
        Question {question.ordinal + 1} of {total} · {QUESTION_TYPE_LABELS[question.question_type]}
        {question.target_skill ? ` · Targets ${question.target_skill}` : ""}
      </p>
      <p id={questionId} className="bj-rs-text bj-rs-qtext">
        {question.question_text}
      </p>
      <p className="bj-ws-caption">
        {question.grounded_in
          ? `Grounded in interview-process research for this company, which is shared across users: "${question.grounded_in}"`
          : "General question -- no cited company-specific data behind this one."}
      </p>
      <textarea
        ref={textareaRef}
        value={draft}
        onChange={(e) => onDraft(e.target.value)}
        rows={5}
        placeholder="Answer as you would in the real interview."
        readOnly={submitting}
        aria-labelledby={questionId}
      />
      <div className="bj-actions">
        <button type="button" aria-disabled={submitting || empty ? true : undefined} onClick={onSubmit}>
          {submitting ? "Scoring..." : "Submit answer"}
        </button>
        {empty && !submitting && <span className="bj-ws-caption">Write an answer to submit it.</span>}
      </div>
      {problem !== null && (
        <div className="bj-rs-issue">
          {problem.kind === "setup" ? <SetupRequiredNotice notice={problem.notice} /> : <FailureState message={problem.message} onRetry={onSubmit} retryLabel="Send again" />}
        </div>
      )}
    </div>
  );
}

// One scored answer: the question, what was said, the score and the model's comments, with the line that
// says who scored it. Built from the question row alone, so an answer read back from an earlier visit shows
// exactly what a just-scored one does.
function AnswerReview({
  question,
  now,
  savedEarlier,
  headingLevel,
  headingRef,
}: {
  question: InterviewQuestion;
  now: Date;
  savedEarlier: boolean;
  headingLevel: 3 | 4;
  headingRef?: RefObject<HTMLHeadingElement | null>;
}) {
  const feedback = question.feedback;
  const score = question.score ?? feedback?.score ?? null;
  const star = feedback?.star_coverage ?? null;
  const Heading = `h${headingLevel}` as const;
  const trail = answerTrail(question, now, savedEarlier);
  return (
    <div className="bj-rs-review">
      <Heading ref={headingRef} tabIndex={-1} className="bj-rs-eyebrow bj-rs-review-title">
        Question {question.ordinal + 1} · {QUESTION_TYPE_LABELS[question.question_type]}
        {question.target_skill ? ` · Targets ${question.target_skill}` : ""}
      </Heading>
      <p className="bj-rs-text">{question.question_text}</p>
      {question.answer_text && (
        <>
          <h4 className="bj-rs-label">Your answer</h4>
          <p className="bj-rs-well">{question.answer_text}</p>
        </>
      )}
      {score !== null && <SegmentedBar label="Score" value={score} max={10} valueText={`${score} / 10`} />}
      {feedback !== null && (
        <>
          {feedback.structure_feedback && <p className="bj-rs-text bj-muted">{feedback.structure_feedback}</p>}
          {feedback.specificity_feedback && <p className="bj-rs-text bj-muted">{feedback.specificity_feedback}</p>}
          {!starApplies(question.question_type) ? (
            <p className="bj-ws-caption">STAR structure is not scored for technical questions.</p>
          ) : (
            star !== null && (
              <div className="bj-rs-star-block">
                <ul className="bj-rs-star" aria-label="STAR parts your model found in your answer">
                  {(
                    [
                      ["Situation", star.situation],
                      ["Task", star.task],
                      ["Action", star.action],
                      ["Result", star.result],
                    ] as const
                  ).map(([name, present]) => (
                    <li key={name} className={present ? "bj-rs-star-part bj-rs-star-part--on" : "bj-rs-star-part"}>
                      {name}
                      <span className="bj-visually-hidden">{present ? ": found" : ": not found"}</span>
                    </li>
                  ))}
                </ul>
                <p className="bj-ws-caption">
                  <GlyphMark name="model" /> Judged by your model
                </p>
              </div>
            )
          )}
          {feedback.improved_answer && (
            <>
              <h4 className="bj-rs-label">A stronger version, written by your model</h4>
              <p className="bj-rs-well">{feedback.improved_answer}</p>
            </>
          )}
        </>
      )}
      {feedback === null && score === null && <p className="bj-muted bj-small">No score or feedback was stored for this answer.</p>}
      <TrailLine trail={trail.trail} receipt={trail.receipt} receiptTitle={trail.receiptTitle} />
    </div>
  );
}

function SessionReportSummary({
  report,
  questions,
  onPracticeAgain,
  starting,
}: {
  report: SessionReport;
  questions: InterviewQuestion[];
  onPracticeAgain: () => void;
  starting: boolean;
}) {
  const star = starSummary(report, questions);
  return (
    <div className="bj-rs-report">
      <h3 className="bj-ws-block-title">Session complete</h3>
      <p className="bj-rs-meta">
        {report.answered_count} of {report.question_count} answered
        {report.average_score !== null && ` · average score ${report.average_score} / 10`}
        {star !== null && ` · ${star}`}
      </p>
      <div className="bj-actions">
        <button type="button" aria-disabled={starting ? true : undefined} onClick={onPracticeAgain}>
          {starting ? "Starting..." : "Practice again"}
        </button>
      </div>
    </div>
  );
}
