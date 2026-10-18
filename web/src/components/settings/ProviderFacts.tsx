import { providerCost, providerNotice } from "../../lib/providerStatus";

// What the page says about a model provider besides its key: who bills the person, and whether the
// integration has ever been seen working against the real service (lib/providerStatus.ts decides
// both; nothing here claims anything of its own).

export function ProviderFacts({ providerId, providerName }: { providerId: string; providerName: string }) {
  const cost = providerCost(providerId, providerName);
  const notice = providerNotice(providerId);
  return (
    <div className="bj-set-facts">
      <p className="bj-set-cost">
        {cost.text}
        {cost.link !== null && (
          <>
            {" "}
            <a href={cost.link.href} target="_blank" rel="noopener noreferrer">
              {cost.link.label}
              <span className="bj-visually-hidden"> (opens in a new tab)</span>
            </a>
          </>
        )}
      </p>
      {notice !== null && (
        <p className="bj-set-hint">
          <span className="bj-badge-muted">{notice.label}</span> {notice.detail}
          {notice.help !== null && ` ${notice.help}`}
        </p>
      )}
    </div>
  );
}
