import { useState } from "react";
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
  const { user, loading, login, loginEnterprise, logout } = useAuth();
  const [orgSlug, setOrgSlug] = useState("");
  const [error, setError] = useState("");

  async function handleLogin() {
    setError("");
    try {
      await login();
    } catch (loginError) {
      setError(
        loginError instanceof Error ? loginError.message : "Login failed",
      );
    }
  }

  async function handleEnterpriseLogin() {
    setError("");
    try {
      await loginEnterprise(orgSlug);
    } catch (loginError) {
      setError(
        loginError instanceof Error
          ? loginError.message
          : "Enterprise login failed",
      );
    }
  }

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
        <div className="p-4 border-t border-border space-y-3">
          {loading ? (
            <p className="text-xs text-muted">Checking session...</p>
          ) : user ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2 min-w-0">
                {user.avatar_url ? (
                  <img
                    src={user.avatar_url}
                    alt={user.display_name}
                    className="h-7 w-7 rounded-full border border-border"
                  />
                ) : null}
                <div className="min-w-0">
                  <p className="text-sm text-gray-200 truncate">
                    {user.display_name || user.github_login}
                  </p>
                  <p className="text-xs text-muted truncate">
                    {user.effective_tier}
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={logout}
                className="text-xs text-muted transition-colors hover:text-gray-200"
              >
                Sign out
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              <button
                type="button"
                onClick={handleLogin}
                className="w-full rounded border border-accent/40 bg-accent/10 px-3 py-2 text-sm text-accent transition-colors hover:bg-accent/20"
              >
                Sign in with GitHub
              </button>
              <div className="space-y-2">
                <label className="block text-xs uppercase tracking-wide text-muted">
                  Enterprise org slug
                </label>
                <input
                  value={orgSlug}
                  onChange={(event) => setOrgSlug(event.target.value)}
                  placeholder="sciona-platform"
                  className="w-full rounded border border-border bg-panel-soft px-3 py-2 text-sm text-gray-200 placeholder:text-muted focus:border-accent focus:outline-none"
                />
                <button
                  type="button"
                  onClick={handleEnterpriseLogin}
                  className="w-full rounded border border-border bg-panel-soft px-3 py-2 text-sm text-gray-200 transition-colors hover:border-accent/50 hover:text-white"
                >
                  Enterprise sign in
                </button>
              </div>
            </div>
          )}
          {error ? <p className="text-xs text-red-300">{error}</p> : null}
          <div className="pt-1 text-xs text-muted">v0.1.0</div>
        </div>
      </nav>

      {/* Main content */}
      <main className="flex-1 p-8 overflow-auto">
        <Outlet />
      </main>
    </div>
  );
}
