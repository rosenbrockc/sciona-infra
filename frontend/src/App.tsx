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
import BadgeCatalog from "./pages/BadgeCatalog";
import BadgeDetail from "./pages/BadgeDetail";
import Reputation from "./pages/Reputation";
import ReferralLanding from "./pages/ReferralLanding";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="bounties" element={<BountyList />} />
          <Route path="bounties/:id" element={<BountyDetail />} />
          <Route
            path="bounties/new"
            element={
              <RequireAuth>
                <div className="space-y-3">
                  <h2 className="text-xl font-bold">Create Bounty</h2>
                  <p className="text-muted">
                    This route is reserved for authenticated bounty creation.
                  </p>
                </div>
              </RequireAuth>
            }
          />
          <Route
            path="bounties/:id/submit"
            element={
              <RequireAuth>
                <div className="space-y-3">
                  <h2 className="text-xl font-bold">Submit to Bounty</h2>
                  <p className="text-muted">
                    This route is reserved for authenticated submissions.
                  </p>
                </div>
              </RequireAuth>
            }
          />
          <Route path="atoms" element={<AtomList />} />
          <Route path="atoms/:fqdn" element={<AtomDetail />} />
          <Route path="leaderboard" element={<Leaderboard />} />
          <Route path="esg" element={<ESGDashboard />} />
          <Route path="originator/:id" element={<OriginatorProfile />} />
          <Route path="badges" element={<BadgeCatalog />} />
          <Route path="badges/:badgeId" element={<BadgeDetail />} />
          <Route path="reputation" element={<Reputation />} />
        </Route>
        <Route path="auth/callback" element={<AuthCallback />} />
        <Route path="invite/:code" element={<ReferralLanding />} />
      </Routes>
    </AuthProvider>
  );
}
