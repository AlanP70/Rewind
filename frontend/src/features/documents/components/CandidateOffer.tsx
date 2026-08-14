"use client";

import { Button } from "@/components/ui/button";

import type { DateCandidate } from "../api/dating";
import { offerLabel } from "./describeDate";

/**
 * The buttons for a date nothing has stored.
 *
 * **A button is not a value.** These sit below the row, never in the date
 * column, and they say what clicking would do rather than showing a date as if
 * it were already the answer.
 *
 * With two candidates they render **side by side and identically weighted**. Not
 * stacked: vertical order reads as ranking, and a first option reads as the
 * default. There is no `variant="default"` on either one, no "recommended", and
 * no confidence figure -- the whole point of surfacing a disagreement is that
 * the system does not know which is right, and any visual tiebreak would be the
 * system quietly answering the question it just asked.
 */
export function CandidateOffer({
  candidates,
  onAccept,
  disabled,
}: {
  candidates: DateCandidate[];
  onAccept: (occurredOn: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {candidates.map((candidate) => (
        <Button
          key={`${candidate.source}-${candidate.occurred_on}`}
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => onAccept(candidate.occurred_on)}
        >
          {offerLabel(candidate)}
        </Button>
      ))}
    </div>
  );
}
