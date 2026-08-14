"use client";

/**
 * The course list, mirroring `backend/app/schemas/courses.py`.
 *
 * Shared rather than owned by a feature. Two features need it now -- upload
 * picks a course to upload into, documents picks one to list -- and features do
 * not import from each other (`CLAUDE.md`, invariant 6). Copying the type into
 * both would give one backend schema two mirrors that drift apart, which is the
 * thing hand-mirroring a contract has to avoid.
 *
 * The query key is shared too, so the second page to mount reads the first
 * page's cache instead of re-fetching a list that changes about once a term.
 */

import { useQuery } from "@tanstack/react-query";

const API = process.env.NEXT_PUBLIC_API_URL;

export type CourseSummary = {
  id: string;
  name: string;
  code: string | null;
  term: string | null;
  starts_on: string;
  ends_on: string;
};

export async function fetchCourses(): Promise<CourseSummary[]> {
  const response = await fetch(`${API}/courses`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

export function useCourses() {
  return useQuery<CourseSummary[]>({
    queryKey: ["courses"],
    queryFn: fetchCourses,
  });
}

/** "Algorithms (CS161) — Fall 2024", skipping whichever parts are null. */
export function courseLabel(course: CourseSummary): string {
  const name = course.code ? `${course.name} (${course.code})` : course.name;
  return course.term ? `${name} — ${course.term}` : name;
}
