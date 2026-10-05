import type { Session } from "@supabase/supabase-js";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import {
  RECOVERY_COMPLETED,
  changePassword,
  recoveryAfterEvent,
  requestPasswordReset,
  type ChangeOutcome,
  type ResetOutcome,
} from "./lib/passwordReset";
import { supabase } from "./lib/supabase";

// Session state via supabase-js's own listener -- the same pattern the
// command-center reference uses (onAuthStateChange + an initial
// getSession), which correctly covers refresh-token rotation and
// multi-tab sign-out without any custom persistence.

interface AuthState {
  session: Session | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<string | null>;
  signUp: (email: string, password: string) => Promise<SignUpResult>;
  signInWithGoogle: () => Promise<string | null>;
  signOut: () => Promise<void>;
  // Forgot password. Both go through lib/passwordReset.ts, which holds the checks and the
  // wording and takes the Supabase calls as an argument.
  resetPassword: (email: string) => Promise<ResetOutcome>;
  updatePassword: (password: string, confirmation: string) => Promise<ChangeOutcome>;
  // True from the moment supabase-js reports a PASSWORD_RECOVERY (the person followed an
  // emailed link) until they sign out or set the new password -- reported by supabase-js as
  // USER_UPDATED, in every tab, and by `clearRecovery` in the tab that set it. While it is
  // set, App sends them to /update-password wherever they land.
  recovery: boolean;
  clearRecovery: () => void;
}

export type SignUpResult =
  | { kind: "signed_in" }
  | { kind: "confirmation_required" }
  | { kind: "error"; message: string };

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [recovery, setRecovery] = useState(false);

  useEffect(() => {
    void supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });
    const { data: subscription } = supabase.auth.onAuthStateChange((event, next) => {
      setSession(next);
      setRecovery((current) => recoveryAfterEvent(current, event));
    });
    return () => subscription.subscription.unsubscribe();
  }, []);

  async function signIn(email: string, password: string): Promise<string | null> {
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    return error ? error.message : null;
  }

  async function signUp(email: string, password: string): Promise<SignUpResult> {
    const { data, error } = await supabase.auth.signUp({ email, password });
    if (error) {
      return { kind: "error", message: error.message };
    }
    // A project with email confirmation OFF returns a real session
    // immediately; with it ON, `session` is null until the user clicks
    // the confirmation link -- both are legitimate outcomes depending on
    // this project's own Supabase Auth settings, not something the
    // client can (or should) assume either way.
    return data.session ? { kind: "signed_in" } : { kind: "confirmation_required" };
  }

  async function signInWithGoogle(): Promise<string | null> {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: window.location.origin },
    });
    return error ? error.message : null;
  }

  async function signOut(): Promise<void> {
    await supabase.auth.signOut();
  }

  // The redirect is built from this page's own origin and nothing else (passwordReset.ts).
  function resetPassword(email: string): Promise<ResetOutcome> {
    return requestPasswordReset(supabase.auth, email, window.location.origin);
  }

  function updatePassword(password: string, confirmation: string): Promise<ChangeOutcome> {
    return changePassword(supabase.auth, password, confirmation);
  }

  // The same rule as the auth event for a completed update, so the two cannot disagree.
  function clearRecovery(): void {
    setRecovery((current) => recoveryAfterEvent(current, RECOVERY_COMPLETED));
  }

  return (
    <AuthContext.Provider
      value={{
        session,
        loading,
        signIn,
        signUp,
        signInWithGoogle,
        signOut,
        resetPassword,
        updatePassword,
        recovery,
        clearRecovery,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return ctx;
}
