import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, session, setUnauthorizedHandler, type AuthUser } from "../api";

type AuthState = {
  user: AuthUser | null;
  checking: boolean;
  signingIn: boolean;
  signingOut: boolean;
  error: string;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  clearError: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checking, setChecking] = useState(true);
  const [signingIn, setSigningIn] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState("");

  // Restore an existing tab session before rendering the guarded routes.
  useEffect(() => {
    let cancelled = false;
    const token = session.get();
    if (!token) {
      setChecking(false);
      return () => {
        cancelled = true;
      };
    }
    void api
      .me()
      .then((result) => {
        if (!cancelled) setUser(result.user);
      })
      .catch(() => {
        if (!cancelled) {
          session.clear();
          setUser(null);
        }
      })
      .finally(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Any 401 from any request drops us back to the login screen.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setUser(null);
      setError("Your session has expired. Please sign in again.");
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    setSigningIn(true);
    setError("");
    try {
      const result = await api.login(email.trim(), password);
      session.set(result.token);
      setUser(result.user);
    } catch (err) {
      setUser(null);
      session.clear();
      setError(err instanceof Error ? err.message : "Sign in failed");
      throw err;
    } finally {
      setSigningIn(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    setSigningOut(true);
    try {
      await api.logout();
    } catch {
      // A revoked or expired token is already effectively signed out.
    } finally {
      // Clear locally even if the revoke call failed, so the UI never strands
      // the user on a dashboard they believe they have left.
      session.clear();
      setUser(null);
      setError("");
      setSigningOut(false);
    }
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user,
      checking,
      signingIn,
      signingOut,
      error,
      signIn,
      signOut,
      clearError: () => setError(""),
    }),
    [user, checking, signingIn, signingOut, error, signIn, signOut],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (context === null) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}
