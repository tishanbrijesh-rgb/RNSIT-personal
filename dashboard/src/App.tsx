// Application shell and navigation for the ECDAT assurance console.
import { lazy, Suspense, useState, useEffect, useRef, useCallback } from "react";
import {
  logout,
  canWrite,
  getInitialSessionExpiry,
  hasSession,
  restoreSession,
  SESSION_EXPIRED,
} from "./api/client";
import { NavLink, Route, Routes, useSearchParams, useLocation } from "react-router-dom";
import { MotionConfig, AnimatePresence, motion } from "framer-motion";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ToastProvider, useToast } from "./components/Toast";
import { ConfirmDialog } from "./components/ConfirmDialog";
import ThemeToggle from "./components/ThemeToggle";
import Login from "./pages/Login";
import NotFound from "./pages/NotFound";

// ── Page transition configuration ──────────────────────────────
const pageVariants = {
  initial: { opacity: 0, y: 12, scale: 0.995 },
  animate: { opacity: 1, y: 0, scale: 1 },
  exit: { opacity: 0, y: -8, scale: 0.995 },
};

const pageTransition = {
  duration: 0.22,
  ease: [0.25, 0.1, 0.25, 1],
};

function PageTransition({ children, routeKey }: { children: React.ReactNode; routeKey: string }) {
  return (
    <motion.div
      key={routeKey}
      variants={pageVariants}
      initial="initial"
      animate="animate"
      exit="exit"
      transition={pageTransition}
    >
      {children}
    </motion.div>
  );
}

const Dashboard = lazy(() => import("./pages/Dashboard"));
const AssetDetail = lazy(() => import("./pages/AssetDetail"));
const ScanPage = lazy(() => import("./pages/ScanPage"));
const AssetsPage = lazy(() => import("./pages/AssetsPage"));
const RiskReportPage = lazy(() => import("./pages/RiskReport"));
const CbomPage = lazy(() => import("./pages/CbomPage"));
const ScanDetailPage = lazy(() => import("./pages/ScanDetailPage"));
const ScanHistoryPage = lazy(() => import("./pages/ScanHistoryPage"));
const EvidenceGraphPage = lazy(() => import("./pages/EvidenceGraphPage"));

function AppInner() {
  const [authState, setAuthState] = useState<"checking" | "signed-in" | "signed-out">(() =>
    hasSession() ? "checking" : "signed-out",
  );
  const [loginMessage, setLoginMessage] = useState(() => getInitialSessionExpiry());
  const [confirmLogout, setConfirmLogout] = useState(false);
  const [accountMenu, setAccountMenu] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const { toast } = useToast();
  const mainRef = useRef<HTMLElement>(null);
  const mobileNavRef = useRef<HTMLElement>(null);
  const mobileNavToggleRef = useRef<HTMLButtonElement>(null);
  const [navParams] = useSearchParams();
  const rawNavScanId = navParams.get("scan_id");
  const scanQuery = rawNavScanId && /^\d+$/.test(rawNavScanId) ? `?scan_id=${rawNavScanId}` : "";
  const location = useLocation();

  // Focus main content after sign-in so screen readers announce the page.
  useEffect(() => {
    if (authState === "signed-in") {
      mainRef.current?.focus();
    }
  }, [authState]);

  useEffect(() => {
    const expired = (event: Event) => {
      setLoginMessage(
        (event as CustomEvent<string>).detail || "Your session expired. Please sign in again.",
      );
      setAuthState("signed-out");
    };
    window.addEventListener(SESSION_EXPIRED, expired);
    return () => window.removeEventListener(SESSION_EXPIRED, expired);
  }, []);

  useEffect(() => {
    if (authState !== "checking") return;
    let active = true;
    void restoreSession().then((restored) => {
      if (active) setAuthState(restored ? "signed-in" : "signed-out");
    });
    return () => {
      active = false;
    };
  }, [authState]);

  const closeMobileNav = useCallback((restoreFocus = true) => {
    setMobileNavOpen(false);
    if (restoreFocus) requestAnimationFrame(() => mobileNavToggleRef.current?.focus());
  }, []);

  // Close account menu and mobile nav on Escape key.
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        if (accountMenu) setAccountMenu(false);
        if (mobileNavOpen) closeMobileNav();
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [accountMenu, closeMobileNav, mobileNavOpen]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const firstDestination = mobileNavRef.current?.querySelector<HTMLAnchorElement>("a");
    requestAnimationFrame(() => firstDestination?.focus());
  }, [mobileNavOpen]);

  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname, location.search]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const handleOutsidePointer = (event: PointerEvent) => {
      const target = event.target as Node;
      if (
        !mobileNavRef.current?.contains(target) &&
        !mobileNavToggleRef.current?.contains(target)
      ) {
        closeMobileNav(false);
      }
    };
    const handleFocusTransfer = (event: FocusEvent) => {
      const target = event.target as Node;
      if (
        !mobileNavRef.current?.contains(target) &&
        !mobileNavToggleRef.current?.contains(target)
      ) {
        closeMobileNav(false);
      }
    };
    document.addEventListener("pointerdown", handleOutsidePointer);
    document.addEventListener("focusin", handleFocusTransfer);
    return () => {
      document.removeEventListener("pointerdown", handleOutsidePointer);
      document.removeEventListener("focusin", handleFocusTransfer);
    };
  }, [closeMobileNav, mobileNavOpen]);

  if (authState === "checking") {
    return (
      <div className="state" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <h1>Restoring your session</h1>
      </div>
    );
  }

  if (authState === "signed-out")
    return (
      <Login
        message={loginMessage}
        onSuccess={() => {
          toast("Welcome back", "success");
          setLoginMessage("");
          setAuthState("signed-in");
        }}
      />
    );
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="topbar">
        <NavLink className="brand" to="/">
          <img className="brand-mark" src="/ecdat-logo.svg" alt="" aria-hidden="true" />
          <span>
            ECDAT<small>Assurance</small>
          </span>
        </NavLink>
        <nav
          ref={mobileNavRef}
          aria-label="Main navigation"
          className={"topbar-nav" + (mobileNavOpen ? " open" : "")}
          id="main-nav"
        >
          <NavLink to={`/${scanQuery}`} end>
            Overview
          </NavLink>
          <NavLink to={`/assets${scanQuery}`}>Inventory</NavLink>
          <NavLink to="/scans">Scan history</NavLink>
          {canWrite() && (
            <NavLink to="/scan" className="topbar-scan-link">
              <svg
                className="icon-inline"
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              New scan
            </NavLink>
          )}
          <NavLink to={`/reports${scanQuery}`}>Reports</NavLink>
          <NavLink to={`/cbom${scanQuery}`}>CBOM</NavLink>
        </nav>
        <button
          ref={mobileNavToggleRef}
          className="topbar-mobile-toggle"
          onClick={() => {
            if (mobileNavOpen) closeMobileNav();
            else setMobileNavOpen(true);
          }}
          aria-expanded={mobileNavOpen}
          aria-label="Toggle navigation menu"
          aria-controls="main-nav"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            aria-hidden="true"
          >
            {mobileNavOpen ? (
              <>
                <line x1="18" y1="6" x2="6" y2="18" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </>
            ) : (
              <>
                <line x1="3" y1="6" x2="21" y2="6" />
                <line x1="3" y1="12" x2="21" y2="12" />
                <line x1="3" y1="18" x2="21" y2="18" />
              </>
            )}
          </svg>
        </button>
        <ThemeToggle />
        <div className="topbar-account">
          <button
            className="button topbar-menu-btn"
            onClick={() => setAccountMenu((v: boolean) => !v)}
            aria-expanded={accountMenu}
            aria-haspopup="true"
            aria-label="Account menu"
            aria-controls="account-dropdown"
          >
            <svg
              className="icon-inline"
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <circle cx="12" cy="8" r="4" />
              <path d="M4 20c0-4 4-7 8-7s8 3 8 7" />
            </svg>
            <span className="topbar-menu-label">Account</span>
            <svg
              className="icon-inline"
              width="12"
              height="12"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </button>
          {accountMenu && (
            <>
              <div className="topbar-dropdown-backdrop" onClick={() => setAccountMenu(false)} />
              <div className="topbar-dropdown" role="menu" id="account-dropdown">
                <div className="topbar-dropdown-header">
                  <div className="topbar-avatar" aria-hidden="true">
                    <svg
                      width="18"
                      height="18"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <circle cx="12" cy="8" r="4" />
                      <path d="M4 20c0-4 4-7 8-7s8 3 8 7" />
                    </svg>
                  </div>
                  <div>
                    <div className="topbar-dropdown-name">Operator</div>
                    <div className="topbar-dropdown-role">Authenticated</div>
                  </div>
                </div>
                <div className="topbar-dropdown-divider" />
                <button
                  className="topbar-dropdown-item"
                  onClick={() => {
                    setAccountMenu(false);
                    setConfirmLogout(true);
                  }}
                  role="menuitem"
                >
                  <svg
                    width="15"
                    height="15"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                    <polyline points="16 17 21 12 16 7" />
                    <line x1="21" y1="12" x2="9" y2="12" />
                  </svg>
                  Sign out
                </button>
              </div>
            </>
          )}
        </div>
      </header>
      <main id="main-content" ref={mainRef} tabIndex={-1}>
        <Suspense
          fallback={
            <div className="state">
              <span className="spinner" />
              <h1>Loading ECDAT</h1>
            </div>
          }
        >
          <ErrorBoundary>
            <AnimatePresence mode="wait">
              <Routes location={location} key={location.pathname + location.search}>
                <Route
                  path="/"
                  element={
                    <PageTransition routeKey="dashboard">
                      <Dashboard />
                    </PageTransition>
                  }
                />
                <Route
                  path="/assets"
                  element={
                    <PageTransition routeKey="assets">
                      <AssetsPage />
                    </PageTransition>
                  }
                />
                <Route
                  path="/assets/:id"
                  element={
                    <PageTransition routeKey="asset-detail">
                      <AssetDetail />
                    </PageTransition>
                  }
                />
                <Route
                  path="/scan"
                  element={
                    <PageTransition routeKey="scan">
                      <ScanPage />
                    </PageTransition>
                  }
                />
                <Route
                  path="/reports"
                  element={
                    <PageTransition routeKey="reports">
                      <RiskReportPage />
                    </PageTransition>
                  }
                />
                <Route
                  path="/cbom"
                  element={
                    <PageTransition routeKey="cbom">
                      <CbomPage />
                    </PageTransition>
                  }
                />
                <Route
                  path="/scans"
                  element={
                    <PageTransition routeKey="scan-history">
                      <ScanHistoryPage />
                    </PageTransition>
                  }
                />
                <Route
                  path="/scans/:id"
                  element={
                    <PageTransition routeKey="scan-detail">
                      <ScanDetailPage />
                    </PageTransition>
                  }
                />
                <Route
                  path="/evidence-graph"
                  element={
                    <PageTransition routeKey="evidence-graph">
                      <EvidenceGraphPage />
                    </PageTransition>
                  }
                />
                <Route
                  path="*"
                  element={
                    <PageTransition routeKey="not-found">
                      <NotFound />
                    </PageTransition>
                  }
                />
              </Routes>
            </AnimatePresence>
          </ErrorBoundary>
        </Suspense>
      </main>
      <footer>ECDAT · ImpactX · Evidence-backed cryptographic discovery assurance</footer>
      <ConfirmDialog
        open={confirmLogout}
        title="Sign out?"
        message="Your session will be cleared. You will need to sign in again to access the console."
        confirmLabel="Sign out"
        onConfirm={() => {
          logout();
          setLoginMessage("");
          setAuthState("signed-out");
          setConfirmLogout(false);
        }}
        onCancel={() => setConfirmLogout(false)}
      />
    </div>
  );
}

export default function App() {
  return (
    <MotionConfig reducedMotion="user">
      <ToastProvider>
        <AppInner />
      </ToastProvider>
    </MotionConfig>
  );
}
