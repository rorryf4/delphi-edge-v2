import "server-only";
import { createClient } from "@supabase/supabase-js";

// Server-side client using the publishable key: row-level security makes it
// read-only, and it can only see the public scorecard tables and views.
export function supabase() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_PUBLISHABLE_KEY;
  if (!url || !key) {
    throw new Error("Set SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY in web/.env.local");
  }
  return createClient(url, key, { auth: { persistSession: false } });
}
