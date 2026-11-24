"use client";

import { useTranslations } from "next-intl";
import type { Review } from "@/lib/types";
import { Badge } from "./ui";

const DIMS = ["ai_tone", "native", "hook", "clarity", "style_fit", "risk", "publishable"];

/** The AI desk reviewer's verdict on a version: scores, quoted problems with fixes, and what happens next. */
export function ReviewCard({ review, compact = false }: { review: Review | null | undefined; compact?: boolean }) {
  const t = useTranslations("review");
  if (!review) return <p className="text-xs text-muted">{t("none")}</p>;
  const tone = review.verdict === "approve" ? "up" : review.verdict === "revise" ? "warn" : "down";
  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={tone}>{t(`verdict.${review.verdict}`)}</Badge>
        <span>{review.summary}</span>
        {review.model && <span className="text-muted">· {review.model}</span>}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-muted">
        {DIMS.filter((d) => review.scores[d] !== undefined).map((d) => (
          <span key={d} title={t(`dimHint.${d}`)} className={review.scores[d] <= 2 ? "text-down" : ""}>{t(`dim.${d}`)} {review.scores[d]}</span>
        ))}
      </div>
      {!compact && review.issues.map((x, i) => (
        <div key={i} className="rounded border border-border/60 p-2">
          <div className="text-muted">“{x.quote}”</div>
          <div>{x.problem}</div>
          <div className="text-accent">→ {x.fix}</div>
        </div>
      ))}
    </div>
  );
}
