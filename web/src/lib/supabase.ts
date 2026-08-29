import { createClient } from "@supabase/supabase-js";

// Both values are browser-public by design (RLS is what protects data).
// Fail loudly at startup if they're missing rather than at first use.
const url = import.meta.env.VITE_SUPABASE_URL as string | undefined;
const publishableKey = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined;

if (!url || !publishableKey) {
  throw new Error(
    "Missing VITE_SUPABASE_URL / VITE_SUPABASE_PUBLISHABLE_KEY -- copy web/.env.example to web/.env.local and fill it in.",
  );
}

export const supabase = createClient(url, publishableKey);
