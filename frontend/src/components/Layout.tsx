import { useEffect, useState } from "react";
import { NavLink, Outlet, Link } from "react-router-dom";
import { useAuth } from "../auth/useAuth";
import { api } from "../api/client";
import type { GrandmasterStatus } from "../api/types";
import GrandmasterRing from "./GrandmasterRing";

const links = [
  { to: "/", label: "Overview", icon: "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" },
  { to: "/bounties", label: "Bounties", icon: "M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" },
  { to: "/atoms", label: "Atoms", icon: "M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" },
  { to: "/leaderboard", label: "Leaderboard", icon: "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" },
  { to: "/badges", label: "Badges", icon: "M5 3v4M3 5h4M6 17v4m-2-2h4m5-16l2.286 6.857L21 12l-5.714 2.143L13 21l-2.286-6.857L5 12l5.714-2.143L13 3z" },
  { to: "/esg", label: "Impact", icon: "M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z" },
];

function NavIcon({ d }: { d: string }) {
  return (
    <svg className="w-4 h-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path strokeLinecap="round" strokeLinejoin="round" d={d} />
    </svg>
  );
}

export default function Layout() {
  const { user, loading, login, loginEnterprise, logout } = useAuth();
  const [orgSlug, setOrgSlug] = useState("");
  const [error, setError] = useState("");
  const [mobileOpen, setMobileOpen] = useState(false);
  const [grandmaster, setGrandmaster] = useState<GrandmasterStatus | null>(null);

  useEffect(() => {
    if (user) {
      api.getGrandmasterStatus(user.user_id).then(setGrandmaster).catch(() => {});
    }
  }, [user]);

  async function handleLogin() {
    setError("");
    try { await login(); } catch (e) { setError(e instanceof Error ? e.message : "Login failed"); }
  }

  async function handleEnterpriseLogin() {
    setError("");
    try { await loginEnterprise(orgSlug); } catch (e) { setError(e instanceof Error ? e.message : "Enterprise login failed"); }
  }

  const sidebar = (
    <>
      {/* Logo */}
      <div className="p-5 pb-3">
        <Link to="/" className="flex items-center gap-3 group" onClick={() => setMobileOpen(false)}>
          <div className="w-9 h-9 rounded-lg bg-accent-gradient flex items-center justify-center shadow-glow relative">
            <span className="text-white font-bold text-sm tracking-tight">AC</span>
          </div>
          <div>
            <h1 className="text-sm font-bold text-white leading-tight tracking-tight">
              Algorithmic Commons
            </h1>
            <p className="text-[10px] text-muted/60 font-medium tracking-widest mt-0.5">RESEARCH PLATFORM</p>
          </div>
        </Link>
      </div>

      {/* Divider with glow */}
      <div className="mx-5 h-px bg-gradient-to-r from-transparent via-border-bright to-transparent" />

      {/* Navigation */}
      <nav className="flex-1 px-3 py-4">
        <ul className="space-y-0.5">
          {links.map((l) => (
            <li key={l.to}>
              <NavLink
                to={l.to}
                end={l.to === "/"}
                onClick={() => setMobileOpen(false)}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2.5 rounded-lg text-[13px] font-medium transition-all duration-200 ${
                    isActive
                      ? "bg-accent/8 text-accent border border-accent/15 shadow-glow"
                      : "text-muted/80 hover:text-gray-200 hover:bg-panel-soft border border-transparent"
                  }`
                }
              >
                <NavIcon d={l.icon} />
                {l.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      {/* User section */}
      <div className="mx-5 h-px bg-gradient-to-r from-transparent via-border-bright to-transparent" />
      <div className="p-4 space-y-3">
        {loading ? (
          <div className="space-y-2">
            <div className="skeleton h-3 w-24 rounded" />
            <div className="skeleton h-3 w-16 rounded" />
          </div>
        ) : user ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2.5 min-w-0">
              {user.avatar_url ? (
                <GrandmasterRing status={grandmaster}>
                  <img src={user.avatar_url} alt={user.display_name}
                    className="h-8 w-8 rounded-full border border-border-bright" />
                </GrandmasterRing>
              ) : (
                <div className="h-8 w-8 rounded-full bg-panel-soft border border-border-bright flex items-center justify-center text-xs font-bold text-accent">
                  {(user.display_name || user.github_login || "?")[0].toUpperCase()}
                </div>
              )}
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-200 truncate">{user.display_name || user.github_login}</p>
                <p className="text-[11px] text-muted/60 truncate capitalize">{user.effective_tier}</p>
              </div>
            </div>
            <button type="button" onClick={logout} className="text-xs text-muted/60 hover:text-gray-300 transition-colors">
              Sign out
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            <button type="button" onClick={handleLogin} className="btn-primary w-full justify-center text-xs">
              <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24"><path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" /></svg>
              Sign in with GitHub
            </button>
            <div className="space-y-2">
              <label className="block text-[10px] uppercase tracking-widest text-muted/50 font-medium">Enterprise SSO</label>
              <input value={orgSlug} onChange={(e) => setOrgSlug(e.target.value)} placeholder="org-slug" className="input-field text-xs py-2" />
              <button type="button" onClick={handleEnterpriseLogin} className="btn-secondary w-full justify-center text-xs">Enterprise sign in</button>
            </div>
          </div>
        )}
        {error && <p className="text-xs text-bad">{error}</p>}
        <p className="text-[10px] text-muted/30 pt-1">v0.1.0</p>
      </div>
    </>
  );

  return (
    <div className="flex min-h-screen">
      {/* Mobile header */}
      <div className="lg:hidden fixed top-0 left-0 right-0 z-40 flex items-center gap-3 px-4 py-3 bg-panel/95 backdrop-blur-md border-b border-border">
        <button type="button" onClick={() => setMobileOpen(!mobileOpen)}
          className="p-1.5 rounded-lg text-muted hover:text-gray-200 hover:bg-panel-soft transition-colors">
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            {mobileOpen
              ? <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              : <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />}
          </svg>
        </button>
        <span className="text-sm font-bold text-white">Algorithmic Commons</span>
      </div>

      {/* Mobile overlay */}
      {mobileOpen && (
        <div className="lg:hidden fixed inset-0 z-30 bg-black/60 backdrop-blur-sm" onClick={() => setMobileOpen(false)} />
      )}

      {/* Sidebar */}
      <aside className={`fixed lg:sticky top-0 z-40 lg:z-auto h-screen w-64 bg-sidebar-gradient border-r border-border flex flex-col transition-transform duration-200 ${
        mobileOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
      }`}>
        {sidebar}
      </aside>

      {/* Main content */}
      <main className="flex-1 min-w-0 pt-14 lg:pt-0">
        <div className="p-6 lg:p-8 max-w-7xl mx-auto animate-fade-in">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
