# Phase 6 -- Frontend Integration & Polish

**Goal:** Wire the React frontend to real backend APIs (remove mock fallback),
add auth state management with JWT tokens, surface live bounty/workflow status,
and add route protection for authenticated actions.

**Depends on:** Phases 2-5 complete -- especially Phase 3 (Temporal workflows
expose `get_status` query for live polling) and Phase 5 (Authentik enterprise
login + Supabase GitHub OAuth produce JWT tokens the frontend must store).

---

## Prerequisites

Before starting, verify:

1. Backend `/auth/login` returns a Supabase GitHub OAuth URL
2. Backend `/auth/me` returns a `UserResponse` given a valid Bearer token
3. Backend `/submissions/{id}/status` returns workflow state (post-Phase 3)
4. Backend `/auth/enterprise/login` and `/auth/enterprise/callback` exist (post-Phase 5)

---

## Step 1 -- Add TypeScript types for auth

**File:** `frontend/src/api/types.ts` (modify)

Append these types to match the backend `UserResponse` and `TokenResponse`
models from `sciona/api/models.py`:

```typescript
// --- APPEND after the existing PayoutRecipient and PaginatedResponse interfaces ---

export interface UserProfile {
  user_id: string;
  github_login: string;
  display_name: string;
  avatar_url: string;
  identity_tier: string;
  effective_tier: string;
  reputation_score: number;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  refresh_token: string;
  expires_in: number;
}

export interface WorkflowStatus {
  submission_id: string;
  verification_status: string;
  runs: VerificationRun[];
}

export interface VerificationRun {
  status: string;
  metric_values: Record<string, number> | null;
  output_hash: string | null;
  is_deterministic: boolean | null;
}
```

### Exact edit

In `frontend/src/api/types.ts`, after the closing brace of `PaginatedResponse<T>`:

```
old:
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

new:
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface UserProfile {
  user_id: string;
  github_login: string;
  display_name: string;
  avatar_url: string;
  identity_tier: string;
  effective_tier: string;
  reputation_score: number;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  refresh_token: string;
  expires_in: number;
}

export interface WorkflowStatus {
  submission_id: string;
  verification_status: string;
  runs: VerificationRun[];
}

export interface VerificationRun {
  status: string;
  metric_values: Record<string, number> | null;
  output_hash: string | null;
  is_deterministic: boolean | null;
}
```

---

## Step 2 -- Create auth context

**File:** `frontend/src/auth/AuthContext.tsx` (create)

This context manages the full auth lifecycle: token persistence in
localStorage, user profile hydration from `/auth/me`, login redirect via
`/auth/login`, and logout with token cleanup.

```tsx
import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { UserProfile } from "../api/types";

const TOKEN_KEY = "sciona_access_token";
const REFRESH_KEY = "sciona_refresh_token";

export interface AuthContextValue {
  /** Current user, or null if not logged in. */
  user: UserProfile | null;
  /** Raw JWT access token, or null. */
  token: string | null;
  /** True while we are checking localStorage / calling /auth/me on mount. */
  loading: boolean;
  /** Redirect the browser to the GitHub OAuth flow via the backend. */
  login: () => Promise<void>;
  /** Redirect the browser to the Authentik enterprise SSO flow. */
  loginEnterprise: (orgSlug: string) => Promise<void>;
  /** Clear local token and user state. */
  logout: () => void;
  /** Store a token received from an OAuth callback and fetch the profile. */
  handleToken: (accessToken: string, refreshToken?: string) => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue>({
  user: null,
  token: null,
  loading: true,
  login: async () => {},
  loginEnterprise: async () => {},
  logout: () => {},
  handleToken: async () => {},
});

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function fetchMe(token: string): Promise<UserProfile> {
  const res = await fetch(`${API_BASE}/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`/auth/me failed: ${res.status}`);
  return res.json();
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // On mount, check localStorage for an existing token and hydrate user.
  useEffect(() => {
    const stored = localStorage.getItem(TOKEN_KEY);
    if (!stored) {
      setLoading(false);
      return;
    }
    setToken(stored);
    fetchMe(stored)
      .then(setUser)
      .catch(() => {
        // Token expired or invalid -- clear it.
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(REFRESH_KEY);
        setToken(null);
      })
      .finally(() => setLoading(false));
  }, []);

  const handleToken = useCallback(
    async (accessToken: string, refreshToken?: string) => {
      localStorage.setItem(TOKEN_KEY, accessToken);
      if (refreshToken) localStorage.setItem(REFRESH_KEY, refreshToken);
      setToken(accessToken);
      const profile = await fetchMe(accessToken);
      setUser(profile);
    },
    [],
  );

  const login = useCallback(async () => {
    const res = await fetch(`${API_BASE}/auth/login`);
    if (!res.ok) throw new Error("Failed to get login URL");
    const { url } = await res.json();
    window.location.href = url;
  }, []);

  const loginEnterprise = useCallback(async (orgSlug: string) => {
    window.location.href = `${API_BASE}/auth/enterprise/login?org_slug=${encodeURIComponent(orgSlug)}`;
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
    setToken(null);
    setUser(null);
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ user, token, loading, login, loginEnterprise, logout, handleToken }),
    [user, token, loading, login, loginEnterprise, logout, handleToken],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
```

---

## Step 3 -- Create useAuth hook

**File:** `frontend/src/auth/useAuth.ts` (create)

```typescript
import { useContext } from "react";
import { AuthContext, type AuthContextValue } from "./AuthContext";

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
```

---

## Step 4 -- Create RequireAuth route guard

**File:** `frontend/src/auth/RequireAuth.tsx` (create)

```tsx
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./useAuth";

export default function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return <p className="text-muted p-8">Authenticating...</p>;
  }

  if (!user) {
    // Redirect to home with a return-to hint so we can bounce back after login.
    return <Navigate to="/" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}
```

---

## Step 5 -- Create OAuth callback page

**File:** `frontend/src/pages/AuthCallback.tsx` (create)

This page handles the redirect back from Supabase/Authentik OAuth. The
token arrives as a URL fragment (`#access_token=...`) for Supabase implicit
flow, or as a query parameter for the enterprise OIDC callback.

```tsx
import { useEffect } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth/useAuth";

export default function AuthCallback() {
  const { handleToken } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    async function process() {
      // Supabase implicit flow: token in URL hash fragment.
      const hash = window.location.hash.substring(1);
      const hashParams = new URLSearchParams(hash);
      let accessToken = hashParams.get("access_token");
      let refreshToken = hashParams.get("refresh_token");

      // Enterprise OIDC callback: token in query params.
      if (!accessToken) {
        const searchParams = new URLSearchParams(location.search);
        accessToken = searchParams.get("access_token");
        refreshToken = searchParams.get("refresh_token");
      }

      if (accessToken) {
        await handleToken(accessToken, refreshToken ?? undefined);
        // Navigate to the page the user was trying to reach, or home.
        const from = (location.state as { from?: { pathname: string } })?.from?.pathname ?? "/";
        navigate(from, { replace: true });
      } else {
        // No token found -- go home.
        navigate("/", { replace: true });
      }
    }
    process();
  }, [handleToken, navigate, location]);

  return <p className="text-muted p-8">Completing login...</p>;
}
```

---

## Step 6 -- Modify App.tsx: wrap in AuthProvider, add protected routes

**File:** `frontend/src/App.tsx` (modify)

### Full replacement

```tsx
import { Routes, Route } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import RequireAuth from "./auth/RequireAuth";
import Layout from "./components/Layout";
import Home from "./pages/Home";
import BountyList from "./pages/BountyList";
import BountyDetail from "./pages/BountyDetail";
import AtomList from "./pages/AtomList";
import AtomDetail from "./pages/AtomDetail";
import Leaderboard from "./pages/Leaderboard";
import ESGDashboard from "./pages/ESGDashboard";
import OriginatorProfile from "./pages/OriginatorProfile";
import AuthCallback from "./pages/AuthCallback";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route element={<Layout />}>
          {/* Public routes */}
          <Route index element={<Home />} />
          <Route path="bounties" element={<BountyList />} />
          <Route path="bounties/:id" element={<BountyDetail />} />
          <Route path="atoms" element={<AtomList />} />
          <Route path="atoms/:fqdn" element={<AtomDetail />} />
          <Route path="leaderboard" element={<Leaderboard />} />
          <Route path="esg" element={<ESGDashboard />} />
          <Route path="originator/:id" element={<OriginatorProfile />} />

          {/* Protected routes -- require authentication */}
          <Route
            path="bounties/new"
            element={
              <RequireAuth>
                {/* CreateBounty page -- to be built; placeholder for now */}
                <div className="text-muted">Create Bounty (coming soon)</div>
              </RequireAuth>
            }
          />
          <Route
            path="bounties/:id/submit"
            element={
              <RequireAuth>
                {/* SubmitToBounty page -- to be built; placeholder for now */}
                <div className="text-muted">Submit to Bounty (coming soon)</div>
              </RequireAuth>
            }
          />
        </Route>

        {/* Auth callback -- outside Layout to avoid flashing sidebar during redirect */}
        <Route path="auth/callback" element={<AuthCallback />} />
      </Routes>
    </AuthProvider>
  );
}
```

### Exact edit (if preferred over full replacement)

Replace the entire file content. The diff is:

1. Add imports: `AuthProvider`, `RequireAuth`, `AuthCallback`
2. Wrap `<Routes>` in `<AuthProvider>`
3. Add `bounties/new` and `bounties/:id/submit` as protected routes
4. Add `auth/callback` route outside the Layout

---

## Step 7 -- Modify Layout.tsx: add login/logout to sidebar

**File:** `frontend/src/components/Layout.tsx` (modify)

### Full replacement

```tsx
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/useAuth";

const links = [
  { to: "/", label: "Home" },
  { to: "/bounties", label: "Bounties" },
  { to: "/atoms", label: "Atoms" },
  { to: "/leaderboard", label: "Leaderboard" },
  { to: "/esg", label: "ESG Dashboard" },
];

export default function Layout() {
  const { user, loading, login, logout } = useAuth();

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <nav className="w-60 shrink-0 bg-panel border-r border-border flex flex-col">
        <div className="p-5 border-b border-border">
          <h1 className="text-accent font-bold text-lg tracking-tight">
            Algorithmic Commons
          </h1>
        </div>
        <ul className="flex-1 py-3">
          {links.map((l) => (
            <li key={l.to}>
              <NavLink
                to={l.to}
                end={l.to === "/"}
                className={({ isActive }) =>
                  `block px-5 py-2.5 text-sm transition-colors ${
                    isActive
                      ? "text-accent bg-panel-soft border-r-2 border-accent"
                      : "text-muted hover:text-gray-200 hover:bg-panel-soft"
                  }`
                }
              >
                {l.label}
              </NavLink>
            </li>
          ))}
        </ul>

        {/* Auth section */}
        <div className="p-4 border-t border-border">
          {loading ? (
            <p className="text-xs text-muted">Loading...</p>
          ) : user ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                {user.avatar_url && (
                  <img
                    src={user.avatar_url}
                    alt={user.display_name}
                    className="w-6 h-6 rounded-full"
                  />
                )}
                <span className="text-sm text-gray-200 truncate">
                  {user.display_name || user.github_login}
                </span>
              </div>
              <button
                onClick={logout}
                className="text-xs text-muted hover:text-gray-200 transition-colors"
              >
                Sign out
              </button>
            </div>
          ) : (
            <button
              onClick={() => login()}
              className="text-sm text-accent hover:underline transition-colors"
            >
              Sign in with GitHub
            </button>
          )}
        </div>

        <div className="p-4 border-t border-border text-xs text-muted">
          v0.1.0
        </div>
      </nav>

      {/* Main content */}
      <main className="flex-1 p-8 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
```

### Exact edits

**Edit 1:** Add useAuth import at top.

```
old:
import { NavLink, Outlet } from "react-router-dom";

new:
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
```

**Edit 2:** Add useAuth call inside the component.

```
old:
export default function Layout() {
  return (

new:
export default function Layout() {
  const { user, loading, login, logout } = useAuth();

  return (
```

**Edit 3:** Replace the version footer with auth section + version footer.

```
old:
        <div className="p-4 border-t border-border text-xs text-muted">
          v0.1.0
        </div>
      </nav>

new:
        {/* Auth section */}
        <div className="p-4 border-t border-border">
          {loading ? (
            <p className="text-xs text-muted">Loading...</p>
          ) : user ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                {user.avatar_url && (
                  <img
                    src={user.avatar_url}
                    alt={user.display_name}
                    className="w-6 h-6 rounded-full"
                  />
                )}
                <span className="text-sm text-gray-200 truncate">
                  {user.display_name || user.github_login}
                </span>
              </div>
              <button
                onClick={logout}
                className="text-xs text-muted hover:text-gray-200 transition-colors"
              >
                Sign out
              </button>
            </div>
          ) : (
            <button
              onClick={() => login()}
              className="text-sm text-accent hover:underline transition-colors"
            >
              Sign in with GitHub
            </button>
          )}
        </div>

        <div className="p-4 border-t border-border text-xs text-muted">
          v0.1.0
        </div>
      </nav>
```

---

## Step 8 -- Modify client.ts: remove mock conditional, add auth token header

**File:** `frontend/src/api/client.ts` (modify)

### Full replacement

```typescript
import type {
  BountyResponse,
  BountySummaryResponse,
  AtomDetailResponse,
  AtomSummaryResponse,
  AtomVersionResponse,
  LeaderboardEntry,
  OriginatorImpact,
  ComputePreserved,
  BenchmarkRecord,
  SubmissionLeaderboardEntry,
  SettlementInfo,
  PaginatedResponse,
  WorkflowStatus,
} from "./types";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
const TOKEN_KEY = "sciona_access_token";

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem(TOKEN_KEY);
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { ...authHeaders() },
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`);
  return res.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`API ${res.status}: ${path}`);
  return res.json() as Promise<T>;
}

export const api = {
  // --- Bounties ---

  async getBounties(params?: {
    status?: string;
    domain_tag?: string;
    limit?: number;
    offset?: number;
  }): Promise<PaginatedResponse<BountySummaryResponse>> {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.domain_tag) qs.set("domain_tag", params.domain_tag);
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    return get(`/bounties?${qs}`);
  },

  async getBounty(id: string): Promise<BountyResponse> {
    return get(`/bounties/${id}`);
  },

  async getBountyLeaderboard(id: string): Promise<SubmissionLeaderboardEntry[]> {
    return get(`/bounties/${id}/leaderboard`);
  },

  async getBountySettlement(id: string): Promise<SettlementInfo> {
    return get(`/bounties/${id}/settlement`);
  },

  // --- Atoms ---

  async getAtoms(params?: {
    search?: string;
    domain_tag?: string;
    limit?: number;
    offset?: number;
  }): Promise<PaginatedResponse<AtomSummaryResponse>> {
    const qs = new URLSearchParams();
    if (params?.search) qs.set("q", params.search);
    if (params?.domain_tag) qs.set("domain_tag", params.domain_tag);
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    return get(`/atoms?${qs}`);
  },

  async getAtom(fqdn: string): Promise<AtomDetailResponse> {
    return get(`/atoms/${fqdn}`);
  },

  async getAtomVersions(fqdn: string): Promise<AtomVersionResponse[]> {
    return get(`/atoms/${fqdn}/versions`);
  },

  async getAtomBenchmarks(fqdn: string): Promise<BenchmarkRecord[]> {
    return get(`/dashboard/atom/${fqdn}/benchmarks`);
  },

  async getAtomBibtex(fqdn: string): Promise<string> {
    const res = await fetch(`${API_BASE}/dashboard/atom/${fqdn}/bibtex`, {
      headers: { ...authHeaders() },
    });
    return res.text();
  },

  // --- Dashboard ---

  async getLeaderboard(limit?: number): Promise<LeaderboardEntry[]> {
    const qs = limit ? `?limit=${limit}` : "";
    return get(`/dashboard/leaderboard${qs}`);
  },

  async getComputePreserved(): Promise<ComputePreserved> {
    return get("/dashboard/compute-preserved");
  },

  async getOriginatorImpact(id: string): Promise<OriginatorImpact> {
    return get(`/dashboard/originator/${id}/impact`);
  },

  // --- Verification status (Temporal workflow query) ---

  async getSubmissionStatus(submissionId: string): Promise<WorkflowStatus> {
    return get(`/submissions/${submissionId}/status`);
  },
};
```

### Key changes from the original

1. **Removed** `import { mockApi } from "./mock"` -- no more mock import
2. **Removed** `const USE_MOCK = ...` -- no more mock flag
3. **Removed** every `if (USE_MOCK) return mockApi.xxx(...)` branch
4. **Added** `authHeaders()` helper that reads JWT from localStorage
5. **Added** `post<T>()` helper for authenticated POST requests
6. **Added** `getSubmissionStatus()` method for workflow polling
7. **Modified** `get<T>()` to include auth headers on every request

The `mock.ts` file is kept unchanged for Storybook and test use.

---

## Step 9 -- Create WorkflowTimeline component

**File:** `frontend/src/components/WorkflowTimeline.tsx` (create)

Displays the ordered stages of a bounty workflow with the current stage
highlighted. Used in `BountyDetail.tsx`.

```tsx
const STAGES = [
  { key: "draft", label: "Draft" },
  { key: "open", label: "Open" },
  { key: "submitted", label: "Submitted" },
  { key: "verification", label: "Verifying" },
  { key: "verified", label: "Verified" },
  { key: "settled", label: "Settled" },
] as const;

interface Props {
  currentStatus: string;
}

export default function WorkflowTimeline({ currentStatus }: Props) {
  const currentIdx = STAGES.findIndex((s) => s.key === currentStatus);

  return (
    <div className="bg-panel border border-border rounded-lg p-5">
      <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">
        Workflow Progress
      </h3>
      <div className="flex items-center gap-1">
        {STAGES.map((stage, i) => {
          const isPast = i < currentIdx;
          const isCurrent = i === currentIdx;
          const isFuture = i > currentIdx;

          return (
            <div key={stage.key} className="flex items-center gap-1 flex-1">
              {/* Stage dot */}
              <div className="flex flex-col items-center gap-1 flex-1">
                <div
                  className={`w-3 h-3 rounded-full border-2 ${
                    isCurrent
                      ? "bg-accent border-accent"
                      : isPast
                        ? "bg-accent/50 border-accent/50"
                        : "bg-transparent border-border"
                  }`}
                />
                <span
                  className={`text-xs ${
                    isCurrent
                      ? "text-accent font-semibold"
                      : isPast
                        ? "text-muted"
                        : "text-muted/50"
                  }`}
                >
                  {stage.label}
                </span>
              </div>
              {/* Connector line */}
              {i < STAGES.length - 1 && (
                <div
                  className={`h-0.5 flex-1 ${
                    isPast ? "bg-accent/50" : "bg-border"
                  }`}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

---

## Step 10 -- Create VerificationRunList component

**File:** `frontend/src/components/VerificationRunList.tsx` (create)

Displays the list of verification runs returned by the workflow status
endpoint, showing per-run status and metrics.

```tsx
import type { VerificationRun } from "../api/types";

interface Props {
  runs: VerificationRun[];
}

const statusColors: Record<string, string> = {
  passed: "text-green-400",
  failed: "text-red-400",
  running: "text-yellow-400",
  pending: "text-muted",
};

export default function VerificationRunList({ runs }: Props) {
  if (runs.length === 0) {
    return <p className="text-muted text-sm">No verification runs yet.</p>;
  }

  return (
    <div className="bg-panel border border-border rounded-lg p-5">
      <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">
        Verification Runs
      </h3>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-muted border-b border-border">
            <th className="pb-2 pr-4">#</th>
            <th className="pb-2 pr-4">Status</th>
            <th className="pb-2 pr-4">Metrics</th>
            <th className="pb-2">Deterministic</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run, i) => (
            <tr key={i} className="border-b border-border/50">
              <td className="py-2 pr-4 text-muted">{i + 1}</td>
              <td className={`py-2 pr-4 font-mono ${statusColors[run.status] ?? "text-muted"}`}>
                {run.status}
              </td>
              <td className="py-2 pr-4 font-mono text-xs">
                {run.metric_values
                  ? Object.entries(run.metric_values)
                      .map(([k, v]) => `${k}: ${v}`)
                      .join(", ")
                  : "--"}
              </td>
              <td className="py-2 text-muted">
                {run.is_deterministic === null ? "--" : run.is_deterministic ? "Yes" : "No"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

---

## Step 11 -- Modify BountyDetail.tsx: add live status polling

**File:** `frontend/src/pages/BountyDetail.tsx` (modify)

### Full replacement

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  BountyResponse,
  SubmissionLeaderboardEntry,
  SettlementInfo,
  WorkflowStatus,
} from "../api/types";
import StatusBadge from "../components/StatusBadge";
import StatCard from "../components/StatCard";
import WorkflowTimeline from "../components/WorkflowTimeline";
import VerificationRunList from "../components/VerificationRunList";

/** Statuses where the workflow is still in progress and we should poll. */
const ACTIVE_STATUSES = new Set(["active", "submitted", "verification", "open"]);

/** Polling interval in milliseconds. */
const POLL_INTERVAL_MS = 5_000;

export default function BountyDetail() {
  const { id } = useParams<{ id: string }>();
  const [bounty, setBounty] = useState<BountyResponse | null>(null);
  const [leaderboard, setLeaderboard] = useState<SubmissionLeaderboardEntry[]>([]);
  const [settlement, setSettlement] = useState<SettlementInfo | null>(null);
  const [workflowStatus, setWorkflowStatus] = useState<WorkflowStatus | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Fetch core bounty data on mount.
  useEffect(() => {
    if (!id) return;
    api.getBounty(id).then(setBounty);
    api.getBountyLeaderboard(id).then(setLeaderboard);
    api.getBountySettlement(id).then(setSettlement).catch(() => {});
  }, [id]);

  // Poll submission status when bounty is in an active workflow state.
  const pollStatus = useCallback(async (submissionId: string) => {
    try {
      const status = await api.getSubmissionStatus(submissionId);
      setWorkflowStatus(status);
      // If verification is complete, stop polling.
      if (status.verification_status === "verified" || status.verification_status === "failed") {
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      }
    } catch {
      // Silently ignore -- endpoint may not be available yet.
    }
  }, []);

  useEffect(() => {
    if (!bounty) return;
    if (!ACTIVE_STATUSES.has(bounty.status)) return;

    // If there's a leading submission, poll its workflow status.
    // Use the top leaderboard entry's submission_id, if any.
    const submissionId = leaderboard[0]?.submission_id;
    if (!submissionId) return;

    // Initial fetch.
    pollStatus(submissionId);

    // Set up polling interval.
    pollRef.current = setInterval(() => pollStatus(submissionId), POLL_INTERVAL_MS);

    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [bounty, leaderboard, pollStatus]);

  if (!bounty) return <p className="text-muted">Loading...</p>;

  const budgetPct = bounty.verification_budget_total
    ? Math.round((bounty.verification_budget_used / bounty.verification_budget_total) * 100)
    : 0;

  return (
    <div className="space-y-8">
      <div>
        <div className="flex items-center gap-3 mb-2">
          <h2 className="text-xl font-bold">{bounty.title}</h2>
          <StatusBadge status={bounty.status} />
        </div>
        <p className="text-muted text-sm font-mono">{bounty.bounty_id}</p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Escrow" value={`$${bounty.escrow_amount.toLocaleString()}`} />
        <StatCard label="Tier" value={bounty.tier} />
        <StatCard label="Deadline" value={bounty.deadline} />
        <StatCard label="Created" value={bounty.created_at} />
      </div>

      {/* Tags */}
      <div className="flex gap-2">
        {bounty.domain_tags.map((t) => (
          <span key={t} className="px-3 py-1 bg-panel-soft rounded-full text-xs text-muted border border-border">
            {t}
          </span>
        ))}
      </div>

      {/* Workflow timeline */}
      <WorkflowTimeline currentStatus={bounty.status} />

      {/* Verification budget */}
      <div className="bg-panel border border-border rounded-lg p-5">
        <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-3">Verification Budget</h3>
        <div className="flex items-center gap-4">
          <div className="flex-1 h-2 bg-panel-soft rounded-full overflow-hidden">
            <div
              className="h-full bg-accent rounded-full transition-all"
              style={{ width: `${budgetPct}%` }}
            />
          </div>
          <span className="text-sm text-muted font-mono">
            {bounty.verification_budget_used}/{bounty.verification_budget_total}
          </span>
        </div>
      </div>

      {/* Live verification runs */}
      {workflowStatus && (
        <VerificationRunList runs={workflowStatus.runs} />
      )}

      {/* Submission leaderboard */}
      {leaderboard.length > 0 && (
        <div className="bg-panel border border-border rounded-lg p-5">
          <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">Submission Leaderboard</h3>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted border-b border-border">
                <th className="pb-2 pr-4">#</th>
                <th className="pb-2 pr-4">Architect</th>
                <th className="pb-2 pr-4">Metrics</th>
                <th className="pb-2">Verified</th>
              </tr>
            </thead>
            <tbody>
              {leaderboard.map((s) => (
                <tr key={s.submission_id} className="border-b border-border/50">
                  <td className="py-2 pr-4 text-muted">{s.rank}</td>
                  <td className="py-2 pr-4 text-accent">{s.architect_id}</td>
                  <td className="py-2 pr-4 font-mono text-xs">
                    {Object.entries(s.metric_values)
                      .map(([k, v]) => `${k}: ${v}`)
                      .join(", ")}
                  </td>
                  <td className="py-2 text-muted">{s.verified_at}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Settlement */}
      {settlement && settlement.status === "settled" && (
        <div className="bg-panel border border-border rounded-lg p-5">
          <h3 className="text-sm font-semibold text-muted uppercase tracking-wide mb-4">Settlement Breakdown</h3>
          <p className="text-sm text-muted mb-3">
            Winning submission: <span className="font-mono text-accent">{settlement.winning_submission_id}</span>
          </p>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-muted border-b border-border">
                <th className="pb-2 pr-4">Recipient</th>
                <th className="pb-2 pr-4">Role</th>
                <th className="pb-2">Amount</th>
              </tr>
            </thead>
            <tbody>
              {settlement.payouts.map((p) => (
                <tr key={p.recipient_id} className="border-b border-border/50">
                  <td className="py-2 pr-4 font-mono">{p.recipient_id}</td>
                  <td className="py-2 pr-4">{p.role}</td>
                  <td className="py-2 font-mono">${p.amount.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

### Key changes from the original

1. **Added** `WorkflowTimeline` component showing the bounty lifecycle stage
2. **Added** `VerificationRunList` component showing live run results
3. **Added** `workflowStatus` state and `pollRef` for interval-based polling
4. **Added** second `useEffect` that polls `/submissions/{id}/status` every
   5 seconds when the bounty is in an active workflow state
5. **Stops polling** when verification completes (status = `verified` or `failed`)
6. **Cleans up** interval on unmount via the effect's return function

---

## Step 12 -- Modify vite.config.ts: VITE_API_URL for production

**File:** `frontend/vite.config.ts` (modify)

No code change needed in vite.config.ts itself -- the proxy config is already
correct for development. The `VITE_API_URL` env var is already read in
`client.ts` via `import.meta.env.VITE_API_URL`.

For production builds, set the variable at build time:

```bash
VITE_API_URL=https://api.yourdomain.com npm run build
```

Create a `.env.production` example file for documentation:

**File:** `frontend/.env.production.example` (create)

```
# Production API base URL -- set at build time
VITE_API_URL=https://api.yourdomain.com
```

---

## Step 13 -- Dependencies

No new npm dependencies are required. The implementation uses only:

- `react` (useState, useEffect, useCallback, useRef, useMemo, createContext, useContext)
- `react-router-dom` (Navigate, useLocation, useNavigate)
- `localStorage` (built-in browser API)
- `fetch` (built-in browser API)
- `setInterval` / `clearInterval` (built-in browser API)

The existing `package.json` dependencies are sufficient. No polling library
(e.g., react-query/tanstack-query) is introduced to keep the dependency
footprint minimal. If polling becomes more complex later, consider migrating
to `@tanstack/react-query` for automatic refetch, caching, and stale-while-
revalidate.

---

## File Summary

| File | Action | Step |
|---|---|---|
| `frontend/src/api/types.ts` | Modify (append 4 interfaces) | 1 |
| `frontend/src/auth/AuthContext.tsx` | Create | 2 |
| `frontend/src/auth/useAuth.ts` | Create | 3 |
| `frontend/src/auth/RequireAuth.tsx` | Create | 4 |
| `frontend/src/pages/AuthCallback.tsx` | Create | 5 |
| `frontend/src/App.tsx` | Modify (AuthProvider wrap, protected routes, callback route) | 6 |
| `frontend/src/components/Layout.tsx` | Modify (auth section in sidebar) | 7 |
| `frontend/src/api/client.ts` | Modify (remove mock, add auth headers, add post, add getSubmissionStatus) | 8 |
| `frontend/src/components/WorkflowTimeline.tsx` | Create | 9 |
| `frontend/src/components/VerificationRunList.tsx` | Create | 10 |
| `frontend/src/pages/BountyDetail.tsx` | Modify (workflow timeline, live polling) | 11 |
| `frontend/.env.production.example` | Create | 12 |
| `frontend/src/api/mock.ts` | No change (kept for Storybook/tests) | -- |

**New files: 7** | **Modified files: 5**

---

## Execution Order

Steps 1-5 can be executed independently (no cross-dependencies).
Steps 6-8 depend on steps 2-4 (auth files must exist before importing them).
Steps 9-10 are independent.
Step 11 depends on steps 9-10 (imports the new components).
Step 12 is independent.

Optimal parallel execution:

```
Batch 1 (parallel):  Steps 1, 2, 3, 4, 5, 9, 10, 12
Batch 2 (parallel):  Steps 6, 7, 8
Batch 3:             Step 11
```

---

## Verification

After all steps, run:

```bash
cd frontend && npm run build
```

This type-checks the entire frontend (`tsc -b`) and produces a production
bundle. It will catch any broken imports or type errors introduced by the
edits.

Manual verification checklist:

1. `npm run dev` -- sidebar shows "Sign in with GitHub" when logged out
2. Click "Sign in" -- redirects to Supabase GitHub OAuth
3. After callback, sidebar shows avatar + display name + "Sign out"
4. Navigate to `/bounties/new` while logged out -- redirects to home
5. Navigate to `/bounties/new` while logged in -- shows placeholder
6. Navigate to `/bounties/b-001` -- shows WorkflowTimeline component
7. For active bounties with submissions, verify polling indicator in Network tab
8. `VITE_API_URL=http://other:8000 npm run build` -- build succeeds with custom URL
