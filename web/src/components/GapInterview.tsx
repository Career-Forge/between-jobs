import { useState } from "react";
import { apiFetch } from "../lib/api";
import type {
  AtsAttempt,
  EntityCandidate,
  GapAnswerDraftResponse,
  GapInterviewQuestionsResponse,
  GapQuestion,
} from "../lib/generateTypes";
import type { ResumeDocument } from "../lib/headerComposerTypes";
import { latestAttempt } from "../lib/scoreBreakdown";

// Gap Interview (S4, honest-score-surfaces.md) -- Reality Check artboards
// 2-3 ("Close the gaps" / "Approve the wording"), ported from the reviewed
// design canvas (PLANNED.md, "Honest low-score flow"). Three real API
// calls, in order, each an explicit human action:
//   1. POST /resume-documents/{id}/gap-interview (S4a) -- up to 3 questions,
//      deterministically picked, LLM-worded. Real cost -> gated behind the
//      "Close the gaps" click, never fired automatically.
//   2. POST /profile/gap-interview/draft (S4b) -- one bullet drafted
//      strictly from the candidate's own words, plus a proposed entity.
//   3. POST /profile/gap-interview/approve (S4b) -- the deterministic
//      apply, only after explicit approval (possibly edited first). Always
//      a PENDING profile_version; activation is its own separate click
//      against the existing /profile/versions/{id}/activate.
//
// D3's trigger free half (rating < 70) gates whether this renders at all;
// the cluster-zero-coverage half needs a real Step0 call, so it isn't
// checked until the candidate actually asks.
//
// The design canvas showed a specific "67 -> ~71 estimated" score delta --
// deliberately NOT reproduced here. Nothing in this flow computes what a
// closed gap is actually worth (that depends on the NEXT real LLM
// extraction's judgment, not a formula), so showing a precise-looking
// number would be exactly the fabrication this whole feature exists to
// refuse. The addressed cluster name is real and shown instead.

type Phase =
  | "idle"
  | "loading"
  | "empty"
  | "error"
  | "questions"
  | "drafting"
  | "draft-error"
  | "reviewing"
  | "approving"
  | "approve-error"
  | "approved"
  | "exhausted";

interface Draft {
  bullet: string;
  entityPointer: string;
  candidates: EntityCandidate[];
  clusterName: string;
}

export function GapInterview({
  applicationId,
  attempts,
  onRegenerate,
}: {
  applicationId: string;
  attempts: AtsAttempt[];
  onRegenerate: () => void;
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [errorMessage, setErrorMessage] = useState("");
  const [queue, setQueue] = useState<GapQuestion[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [answering, setAnswering] = useState(false);
  const [answerText, setAnswerText] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editing, setEditing] = useState(false);
  const [editedBullet, setEditedBullet] = useState("");
  const [editedPointer, setEditedPointer] = useState("");
  const [newVersionId, setNewVersionId] = useState<string | null>(null);
  const [activating, setActivating] = useState(false);
  const [activated, setActivated] = useState(false);

  const final = latestAttempt(attempts);
  if (!final || final.overall_score >= 70) return null;

  async function loadQuestions() {
    setPhase("loading");
    try {
      const document = await apiFetch<ResumeDocument>(
        `/resume-documents/mine?application_id=${applicationId}`,
      );
      const result = await apiFetch<GapInterviewQuestionsResponse>(
        `/resume-documents/${document.id}/gap-interview`,
        { method: "POST" },
      );
      if (result.questions.length === 0) {
        setPhase("empty");
      } else {
        setQueue(result.questions);
        setActiveIndex(0);
        setPhase("questions");
      }
    } catch (e) {
      setErrorMessage(e instanceof Error ? e.message : "Failed to load questions");
      setPhase("error");
    }
  }

  function advanceOrExhaust() {
    setAnswering(false);
    setAnswerText("");
    if (activeIndex + 1 < queue.length) {
      setActiveIndex((i) => i + 1);
    } else {
      setPhase("exhausted");
    }
  }

  async function draftFact() {
    const question = queue[activeIndex];
    setPhase("drafting");
    try {
      const result = await apiFetch<GapAnswerDraftResponse>("/profile/gap-interview/draft", {
        method: "POST",
        body: JSON.stringify({ question: question.question, answer: answerText }),
      });
      setDraft({
        bullet: result.bullet,
        entityPointer: result.entity_pointer,
        candidates: result.candidates,
        clusterName: question.cluster_name,
      });
      setEditedBullet(result.bullet);
      setEditedPointer(result.entity_pointer);
      setEditing(false);
      setPhase("reviewing");
    } catch (e) {
      setErrorMessage(e instanceof Error ? e.message : "Failed to draft that");
      setPhase("draft-error");
    }
  }

  async function approveFact() {
    if (!draft) return;
    setPhase("approving");
    try {
      const version = await apiFetch<{ id: string }>("/profile/gap-interview/approve", {
        method: "POST",
        body: JSON.stringify({ bullet: editedBullet, entity_pointer: editedPointer }),
      });
      setNewVersionId(version.id);
      setPhase("approved");
    } catch (e) {
      setErrorMessage(e instanceof Error ? e.message : "Failed to save that");
      setPhase("approve-error");
    }
  }

  async function activateVersion() {
    if (!newVersionId) return;
    setActivating(true);
    try {
      await apiFetch(`/profile/versions/${newVersionId}/activate`, { method: "POST" });
      setActivated(true);
    } catch (e) {
      setErrorMessage(e instanceof Error ? e.message : "Failed to activate");
    } finally {
      setActivating(false);
    }
  }

  function discardDraft() {
    setDraft(null);
    advanceOrExhaust();
    setPhase(queue.length > 0 && activeIndex + 1 < queue.length ? "questions" : "exhausted");
  }

  return (
    <div className="bj-gap-interview">
      {phase === "idle" && (
        <div className="bj-gap-idle">
          <button onClick={() => void loadQuestions()}>Close the gaps</button>
          <div className="bj-muted bj-small">
            Up to 3 questions, drafted from your own profile -- a yes becomes a fact you
            approve first; a no costs you nothing.
          </div>
        </div>
      )}

      {phase === "loading" && <div className="bj-muted bj-small">Finding real gaps...</div>}

      {phase === "error" && (
        <div className="bj-error">
          {errorMessage}{" "}
          <button onClick={() => void loadQuestions()}>Try again</button>
        </div>
      )}

      {phase === "empty" && (
        <div className="bj-muted bj-small">
          No honest gap to ask about right now -- nothing here traces to a real, adjacent
          skill in your profile.
        </div>
      )}

      {phase === "exhausted" && (
        <div className="bj-muted bj-small">That's every question for this application.</div>
      )}

      {(phase === "questions" ||
        phase === "drafting" ||
        phase === "draft-error" ||
        phase === "reviewing" ||
        phase === "approving" ||
        phase === "approve-error" ||
        phase === "approved") && (
        <div className="bj-gap-header">
          <h3 className="bj-gap-title">Close the gaps</h3>
          <span className="bj-badge-violet">✦ AI-drafted</span>
        </div>
      )}

      {(phase === "questions" || phase === "drafting" || phase === "draft-error") && (
        <>
          <div className="bj-gap-question-card">
            <div>{queue[activeIndex].question}</div>
            <div className="bj-gap-provenance">
              <span className="bj-badge-gold">addresses</span>
              <span className="bj-muted bj-small">{queue[activeIndex].cluster_name}</span>
            </div>
            {!answering ? (
              <div className="bj-actions">
                <button
                  className="bj-gap-yes"
                  onClick={() => setAnswering(true)}
                  disabled={phase === "drafting"}
                >
                  Yes, add detail
                </button>
                <button onClick={advanceOrExhaust} disabled={phase === "drafting"}>
                  No
                </button>
                <button className="bj-gap-ghost" onClick={advanceOrExhaust} disabled={phase === "drafting"}>
                  Not sure
                </button>
              </div>
            ) : (
              <div className="bj-gap-answer">
                <div className="bj-muted bj-small">One honest line on what you actually did:</div>
                <textarea
                  value={answerText}
                  onChange={(e) => setAnswerText(e.target.value)}
                  rows={2}
                  placeholder="e.g. Preprocessed 12k+ image slices with OpenCV -- denoising, normalization, augmentation."
                  disabled={phase === "drafting"}
                  autoFocus
                />
                <div className="bj-actions">
                  <button
                    className="bj-primary"
                    onClick={() => void draftFact()}
                    disabled={phase === "drafting" || answerText.trim().length === 0}
                  >
                    {phase === "drafting" ? "Drafting..." : "Draft the fact →"}
                  </button>
                </div>
              </div>
            )}
            {phase === "draft-error" && <div className="bj-error">{errorMessage}</div>}
          </div>

          {queue.length > activeIndex + 1 && (
            <div className="bj-gap-queue">
              {queue.slice(activeIndex + 1).map((q, i) => (
                <button
                  key={q.cluster_name}
                  className="bj-gap-queue-row"
                  onClick={() => {
                    setActiveIndex(activeIndex + 1 + i);
                    setAnswering(false);
                    setAnswerText("");
                  }}
                >
                  {q.question}
                </button>
              ))}
            </div>
          )}

          <div className="bj-muted bj-small">Hard cap: 3 questions per application.</div>
          <div className="bj-gap-callout bj-small">
            Answers land in career memory as dated, sourced facts -- the resume only ever
            cites them. Nothing here is pasted into a document unreviewed.
          </div>
        </>
      )}

      {(phase === "reviewing" ||
        phase === "approving" ||
        phase === "approve-error" ||
        phase === "approved") &&
        draft && (
          <div className="bj-gap-review-card">
            {phase !== "approved" ? (
              <>
                <span className="bj-badge-emerald bj-gap-review-badge">New fact</span>
                {!editing ? (
                  <div>{editedBullet}</div>
                ) : (
                  <textarea
                    value={editedBullet}
                    onChange={(e) => setEditedBullet(e.target.value)}
                    rows={2}
                  />
                )}
                <div className="bj-gap-provenance">
                  <span className="bj-badge-gold">from your answer</span>
                  <span className="bj-muted bj-small">gap interview</span>
                </div>
                <div className="bj-gap-lands-in">
                  <span className="bj-gap-lands-in-label">Lands in</span>
                  {!editing ? (
                    <span>
                      {draft.candidates.find((c) => c.pointer === editedPointer)?.label ??
                        editedPointer}
                    </span>
                  ) : (
                    <select
                      value={editedPointer}
                      onChange={(e) => setEditedPointer(e.target.value)}
                    >
                      {draft.candidates.map((c) => (
                        <option key={c.pointer} value={c.pointer}>
                          {c.label}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
                <div className="bj-muted bj-small">
                  Addresses {draft.clusterName} -- confirmed on your next generation. An
                  estimate never pretends to be a score.
                </div>
                {phase === "approve-error" && <div className="bj-error">{errorMessage}</div>}
                <div className="bj-gap-review-actions">
                  <div className="bj-actions">
                    <button
                      className="bj-primary"
                      onClick={() => void approveFact()}
                      disabled={phase === "approving" || editedBullet.trim().length === 0}
                    >
                      {phase === "approving" ? "Saving..." : "Approve & save to profile"}
                    </button>
                    <button onClick={() => setEditing((v) => !v)} disabled={phase === "approving"}>
                      {editing ? "Done editing" : "Edit wording"}
                    </button>
                    <button
                      className="bj-gap-ghost"
                      onClick={discardDraft}
                      disabled={phase === "approving"}
                    >
                      Discard
                    </button>
                  </div>
                  <div className="bj-muted bj-small">
                    Creates a new immutable profile version -- the old one stays, and every
                    future application benefits, not just this one.
                  </div>
                </div>
              </>
            ) : (
              <>
                <span className="bj-badge-emerald bj-gap-review-badge">Saved</span>
                <div className="bj-muted bj-small">
                  Saved as a new profile version, pending -- your resumes still use the old
                  one until you activate it.
                </div>
                {!activated ? (
                  <div className="bj-actions">
                    <button
                      className="bj-primary"
                      onClick={() => void activateVersion()}
                      disabled={activating}
                    >
                      {activating ? "Activating..." : "Activate now"}
                    </button>
                  </div>
                ) : (
                  <div className="bj-actions">
                    <span className="bj-badge-emerald">Active</span>
                    <button className="bj-primary" onClick={onRegenerate}>
                      Generate again
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        )}
    </div>
  );
}
