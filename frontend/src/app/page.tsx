"use client";

import { useQuery } from "@tanstack/react-query";

type Health = { status: string; db: string; redis: string };

export default function Home() {
  const { data, isPending, error } = useQuery<Health>({
    queryKey: ["health"],
    queryFn: async () => {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/health`);
      // /health is always 200, so this only fires if the backend itself is
      // unreachable. A down dependency arrives as data, not as an error.
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    },
  });

  return (
    <main className="flex flex-1 items-center justify-center p-8">
      <div className="w-full max-w-sm rounded-lg border border-black/10 p-6 dark:border-white/15">
        <h1 className="text-lg font-medium">Rewind</h1>
        <p className="mt-1 text-sm opacity-60">Backend health</p>

        {isPending && <p className="mt-4 text-sm">Checking&hellip;</p>}
        {error && <p className="mt-4 text-sm">Backend unreachable: {error.message}</p>}
        {data && (
          <dl className="mt-4 space-y-1 font-mono text-sm">
            {Object.entries(data).map(([key, value]) => (
              <div key={key} className="flex justify-between gap-4">
                <dt className="opacity-60">{key}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </main>
  );
}
