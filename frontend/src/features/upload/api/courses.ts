/** Mirrors `backend/app/schemas/courses.py`. */

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

/** "Algorithms (CS161) — Fall 2024", skipping whichever parts are null. */
export function courseLabel(course: CourseSummary): string {
  const name = course.code ? `${course.name} (${course.code})` : course.name;
  return course.term ? `${name} — ${course.term}` : name;
}
