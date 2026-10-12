import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "./api";
import {
  afterReconnectRefusal,
  gmailConnection,
  type GmailConnection,
  type GmailCredentialRow,
} from "./gmailAccess";

// Reads the person's Gmail connection from GET /credentials once, for a screen that offers or
// withholds "Create Gmail draft" (the contact panel). The reading and deciding is
// gmailAccess.ts; this is the wiring, and the only place here that imports the API client.
//
// `enabled` false (Gmail is not for this person, so there is nothing to ask) reads nothing.
// A failed read leaves the connection "unknown", which the screen treats as before: the button is
// offered and the server's answer is shown if it refuses. `markReconnectNeeded` is for the moment
// a request itself proves it (the server answered that Google stopped accepting the connection),
// so the screen need not wait for a reload to say so.

export function useGmailConnection(enabled: boolean): {
  connection: GmailConnection;
  markReconnectNeeded: () => void;
} {
  const [rows, setRows] = useState<GmailCredentialRow[] | null>(null);
  const [proven, setProven] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    apiFetch<GmailCredentialRow[]>("/credentials")
      .then((loaded) => {
        if (!cancelled) setRows(loaded);
      })
      .catch(() => {
        // unknown stays unknown
      });
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  const markReconnectNeeded = useCallback(() => setProven(true), []);
  return { connection: afterReconnectRefusal(gmailConnection(rows), proven), markReconnectNeeded };
}
