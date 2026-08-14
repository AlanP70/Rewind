"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { fetchCourseDating, setDocumentDate, type CourseDating } from "../api/dating";

export function useCourseDating(courseId: string | null) {
  return useQuery<CourseDating>({
    queryKey: ["dating", courseId],
    queryFn: () => fetchCourseDating(courseId!),
    enabled: courseId !== null,
  });
}

/**
 * Set a date, then refetch the whole course.
 *
 * The list is invalidated rather than patched in place, because one date changes
 * more than one row. Candidates are interpolations over the documents that are
 * still undated, so dating one document can change what is offered for the
 * others -- and a locally-patched cache would keep showing the suggestions that
 * were computed before the click.
 */
export function useSetDocumentDate(courseId: string | null) {
  const client = useQueryClient();

  return useMutation({
    mutationFn: ({ documentId, occurredOn }: { documentId: string; occurredOn: string }) =>
      setDocumentDate(documentId, occurredOn),
    onSuccess: () => client.invalidateQueries({ queryKey: ["dating", courseId] }),
  });
}
