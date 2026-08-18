import { useEffect, useState } from "react";

import { supabase } from "./supabase";

// "Data as of Tue 05:31 CT. Next run 05:30." — trust depends on freshness
// (spec v3 9.2). Reads the latest finished collector run.
export function useSnapshotAge(): string | null {
  const [banner, setBanner] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      const { data } = await supabase
        .from("collector_runs")
        .select("finished_at")
        .not("finished_at", "is", null)
        .order("finished_at", { ascending: false })
        .limit(1);
      if (!alive) return;
      const finished = data?.[0]?.finished_at;
      if (!finished) {
        setBanner("No collector run recorded yet.");
        return;
      }
      const stamp = new Date(finished).toLocaleString("en-US", {
        weekday: "short",
        hour: "numeric",
        minute: "2-digit",
        timeZone: "America/Chicago",
        timeZoneName: "short",
      });
      setBanner(`Data as of ${stamp}. Next run 05:30 CT.`);
    }
    void load();
    const timer = setInterval(load, 15 * 60 * 1000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return banner;
}
