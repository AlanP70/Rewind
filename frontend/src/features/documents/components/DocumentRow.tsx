"use client";

import { AlertTriangle } from "lucide-react";

import type { DocumentDating } from "../api/dating";
import { CandidateOffer } from "./CandidateOffer";
import { DateCell } from "./DateCell";
import { ManualDateInput } from "./ManualDateInput";
import { describeDate } from "./describeDate";

/**
 * One document: its name, its date or lack of one, and what can be done about it.
 *
 * The reason is printed **verbatim** from the API. The backend writes those
 * sentences to be read by a person -- "only one lecture in this course, so
 * lecture 3 has no range to sit in" -- and rewording them here would give one
 * situation two descriptions that drift apart. Same rule `UploadRow` follows for
 * a failed document's error.
 */
export function DocumentRow({
  document,
  startsOn,
  endsOn,
  onSetDate,
  pending,
}: {
  document: DocumentDating;
  startsOn: string;
  endsOn: string;
  onSetDate: (documentId: string, occurredOn: string) => void;
  pending: boolean;
}) {
  const display = describeDate(document);
  const accept = (occurredOn: string) => onSetDate(document.document_id, occurredOn);

  return (
    <li className="rounded-md border border-black/10 p-3 text-sm dark:border-white/15">
      <div className="flex items-start gap-3">
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{document.title}</span>
          <span className="block truncate font-mono text-xs opacity-60">
            {document.filename}
          </span>
        </span>
        <DateCell display={display} />
      </div>

      {display.kind !== "stored" && (
        <div className="mt-3 flex flex-col gap-2 border-t border-black/5 pt-3 dark:border-white/10">
          {display.kind === "disputed" && (
            <p className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-400">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              <span>{display.reason}</span>
            </p>
          )}

          {display.kind !== "disputed" && display.reason && (
            <p className="text-xs opacity-70">{display.reason}</p>
          )}

          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            {display.kind === "offered" && (
              <CandidateOffer
                candidates={[display.offer]}
                onAccept={accept}
                disabled={pending}
              />
            )}
            {display.kind === "disputed" && (
              <CandidateOffer
                candidates={display.offers}
                onAccept={accept}
                disabled={pending}
              />
            )}

            <ManualDateInput
              startsOn={startsOn}
              endsOn={endsOn}
              onSubmit={accept}
              disabled={pending}
            />
          </div>
        </div>
      )}
    </li>
  );
}
