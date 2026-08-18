// writes.ts — every mutation the browser can make, in one small file.
//
// The only two user-initiated writes in the system (spec v3 9.5), both
// insert-only under RLS: acknowledgements and account notes.
//
// "Insert-only under RLS" means the row-level-security policies on these two
// tables allow authenticated users to INSERT but never UPDATE or DELETE — the
// database enforces an append-only audit trail no matter what this code does.
// Everything else in the app is read-only.
//
// Both helpers return an error *message* (string) on failure or null on
// success, so callers can show the message inline without try/catch.

import { supabase } from "./supabase";

// Acknowledge (snooze) a flag on an account. An "ack" says "a human saw this
// flag and is handling it" — the flag stops counting as needing attention
// until snooze_until passes. Acks are per flag *code* per account, so a
// re-firing flag stays quiet for the whole snooze window.
export async function acknowledgeFlag(options: {
  locationId: string;
  code: string;
  ackedBy: string;
  note?: string;
  snoozeDays: 7 | 14 | 30;
}): Promise<string | null> {
  // Compute the snooze end date: today + N days, stored as a date-only string
  // (the .slice(0, 10) trims "YYYY-MM-DDTHH:MM..." down to "YYYY-MM-DD").
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

// Append a free-text note to an account. No edit and no delete on purpose —
// the notes list is a permanent running log (see the Account page UI).
export async function addNote(locationId: string, author: string, body: string): Promise<string | null> {
  const { error } = await supabase.from("account_notes").insert({
    location_id: locationId,
    author,
    body,
  });
  return error ? error.message : null;
}
