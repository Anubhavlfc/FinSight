import type { ReactNode } from "react";
import { ApiError } from "../api/client";
import type { Evidence, Phase, ReviewStatus } from "../api/types";
import { Icon, type IconName } from "./Icon";

type Tone = "teal" | "amber" | "coral" | "neutral";

export function Badge({ tone = "neutral", icon, children, square, title }: {
  tone?: Tone; icon?: IconName; children: ReactNode; square?: boolean; title?: string;
}) {
  return (
    <span className={`badge${tone === "neutral" ? "" : ` tone-${tone}`}${square ? " square" : ""}`} title={title}>
      {icon && <Icon name={icon} size={12} />}
      {children}
    </span>
  );
}

export function Notice({ tone = "neutral", icon, title, children, actions, role }: {
  tone?: Tone; icon?: IconName; title: string; children?: ReactNode; actions?: ReactNode; role?: "alert" | "status";
}) {
  const fallback: IconName = tone === "coral" ? "alert" : tone === "amber" ? "alert" : tone === "teal" ? "check" : "info";
  return (
    <div className={`notice${tone === "neutral" ? "" : ` tone-${tone}`}`} role={role}>
      <Icon name={icon ?? fallback} />
      <p className="notice-title">{title}</p>
      {children && <div className="notice-body">{children}</div>}
      {actions && <div className="notice-actions">{actions}</div>}
    </div>
  );
}

/** One consistent error presentation: what happened, what to do next, and a retry when it can help. */
export function ErrorNotice({ error, onRetry, title, extra }: {
  error: ApiError; onRetry?: () => void; title?: string; extra?: ReactNode;
}) {
  return (
    <Notice
      tone={error.network ? "amber" : "coral"}
      title={title ?? (error.network ? "The analysis service is not reachable" : "That did not work")}
      role="alert"
      actions={
        (onRetry || extra) && (
          <>
            {onRetry && (
              <button type="button" className="btn btn-sm" onClick={onRetry}>
                <Icon name="refresh" size={14} /> Retry
              </button>
            )}
            {extra}
          </>
        )
      }
    >
      <p>{error.message}</p>
      {error.nextSteps.length > 0 && (
        <ul>
          {error.nextSteps.map((step) => (
            <li key={step}>{step}</li>
          ))}
        </ul>
      )}
    </Notice>
  );
}

const EVIDENCE: Record<Evidence, { tone: Tone; icon: IconName }> = {
  supported: { tone: "teal", icon: "check" },
  partially_supported: { tone: "amber", icon: "half" },
  unsupported: { tone: "coral", icon: "ring" },
  not_investigated: { tone: "neutral", icon: "dash" },
};

export function EvidenceBadge({ evidence, label }: { evidence: Evidence; label: string }) {
  const e = EVIDENCE[evidence];
  return (
    <Badge tone={e.tone} icon={e.icon}>
      {label}
    </Badge>
  );
}

const PHASE: Record<Phase, { tone: Tone; icon: IconName; label: string }> = {
  queued: { tone: "neutral", icon: "clock", label: "Queued" },
  running: { tone: "teal", icon: "dot", label: "Running" },
  ready_for_review: { tone: "teal", icon: "check", label: "Complete" },
  partial: { tone: "amber", icon: "half", label: "Partial results" },
  approved: { tone: "teal", icon: "check", label: "Complete" },
  failed: { tone: "coral", icon: "x", label: "Failed" },
  needs_input: { tone: "amber", icon: "alert", label: "Needs your input" },
  interrupted: { tone: "amber", icon: "alert", label: "Interrupted" },
};

export function PhaseBadge({ phase }: { phase: Phase }) {
  const p = PHASE[phase];
  return (
    <Badge tone={p.tone} icon={p.icon}>
      {p.label}
    </Badge>
  );
}

export function ReviewBadge({ review }: { review: ReviewStatus }) {
  const map: Record<ReviewStatus["state"], { tone: Tone; icon: IconName }> = {
    approved: { tone: "teal", icon: "shield" },
    awaiting_review: { tone: "amber", icon: "clock" },
    needs_attention: { tone: "coral", icon: "alert" },
    not_reviewed: { tone: "neutral", icon: "dash" },
  };
  const m = map[review.state];
  return (
    <Badge tone={m.tone} icon={m.icon}>
      {review.label}
    </Badge>
  );
}

export function SyntheticBadge() {
  return (
    <Badge tone="amber" icon="layers" square title="Made-up company, figures and filing text, used to demonstrate the workflow">
      Synthetic sample
    </Badge>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({ icon, title, children, actions }: {
  icon: IconName; title: string; children?: ReactNode; actions?: ReactNode;
}) {
  return (
    <div className="empty">
      <Icon name={icon} size={22} />
      <p className="empty-title">{title}</p>
      {children && <div className="empty-body">{children}</div>}
      {actions && <div className="empty-actions">{actions}</div>}
    </div>
  );
}

export function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer noopener" className="ext">
      {children}
      <Icon name="external" size={12} label="(opens in a new tab)" />
    </a>
  );
}
