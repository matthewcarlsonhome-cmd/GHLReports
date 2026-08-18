// The only two user-initiated writes in the system (spec v3 9.5), both
// insert-only under RLS: acknowledgements and account notes.

import { supabase } from "./supabase";

export async function acknowledgeFlag(options: {
  locationId: string;
  code: string;
  ackedBy: string;
  note?: string;
  snoozeDays: 7 | 14 | 30;
}): Promise<string | null> {
  const snooze = new Date();
  snooze.setDate(snooze.getDate() + options.snoozeDays);
  const { error } = await supabase.from("flag_acks").insert({
    location_id: options.locationId,
    code: options.code,
    acked_by: options.ackedBy,
    note: options.note || null,
    snooze_until: snooze.toISOString().slice(0, 10),
  });
  return error ? error.message : null;
}

export async function addNote(locationId: string, author: string, body: string): Promise<string | null> {
  const { error } = await supabase.from("account_notes").insert({
    location_id: locationId,
    author,
    body,
  });
  return error ? error.message : null;
}
