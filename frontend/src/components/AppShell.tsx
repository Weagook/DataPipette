import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  BarChart3,
  ChevronDown,
  Database,
  Gauge,
  LayoutDashboard,
  LogOut,
  Menu,
  ShieldCheck,
  Users,
  X,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";

import { logout, type CurrentUser } from "../lib/api";
import { queryKeys } from "../lib/query";
import { Brand } from "./Brand";

interface AppShellProps {
  user: CurrentUser;
  children: ReactNode;
}

export function AppShell({ user, children }: AppShellProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const menuButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const signOut = useMutation({
    mutationFn: logout,
    onSettled: async () => {
      queryClient.clear();
      await navigate("/login", { replace: true });
    },
  });
  const canAuthor = user.role === "admin" || user.role === "analyst";
  const isAdmin = user.role === "admin";
  const pageName = routeName(location.pathname);

  useEffect(() => {
    document.title = `${pageName} · DatePipette`;
  }, [pageName]);

  useEffect(() => {
    if (!mobileOpen) return;
    closeButton.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMobileOpen(false);
        menuButton.current?.focus();
      }
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [mobileOpen]);

  return (
    <div className="app-frame">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <span className="sr-only" aria-live="polite">{pageName} page</span>
      <button
        className="mobile-menu-button"
        onClick={() => setMobileOpen(true)}
        type="button"
        aria-label="Open navigation"
        aria-controls="primary-sidebar"
        aria-expanded={mobileOpen}
        ref={menuButton}
      >
        <Menu size={20} />
      </button>
      {mobileOpen && (
        <div
          className="nav-scrim"
          aria-hidden="true"
          onClick={() => setMobileOpen(false)}
        />
      )}
      <aside id="primary-sidebar" className={`sidebar ${mobileOpen ? "open" : ""}`}>
        <div className="sidebar-head">
          <Brand />
          <button
            className="mobile-close"
            onClick={() => setMobileOpen(false)}
            aria-label="Close navigation"
            type="button"
            ref={closeButton}
          >
            <X size={18} />
          </button>
        </div>
        <nav className="primary-nav" aria-label="Primary navigation">
          <NavLink to="/overview" onClick={() => setMobileOpen(false)}>
            <Gauge size={18} />
            Overview
          </NavLink>
          <NavLink to="/dashboards" onClick={() => setMobileOpen(false)}>
            <LayoutDashboard size={18} />
            Dashboards
          </NavLink>
          {canAuthor && (
            <div className="nav-group" aria-label="Authoring">
              <span className="nav-group-label">Authoring</span>
              <NavLink to="/datasets" onClick={() => setMobileOpen(false)}>
                <Database size={18} />
                Datasets
              </NavLink>
              <NavLink to="/charts" onClick={() => setMobileOpen(false)}>
                <BarChart3 size={18} />
                Charts
              </NavLink>
            </div>
          )}
          {isAdmin && (
            <div className="nav-group" aria-label="Administration">
              <span className="nav-group-label">Administration</span>
              <NavLink to="/connections" onClick={() => setMobileOpen(false)}>
                <Gauge size={18} />
                Connections
              </NavLink>
              <NavLink to="/access" onClick={() => setMobileOpen(false)}>
                <Users size={18} />
                Access
              </NavLink>
              <NavLink to="/governance" onClick={() => setMobileOpen(false)}>
                <ShieldCheck size={18} />
                Governance
              </NavLink>
            </div>
          )}
        </nav>
        <div className="sidebar-foot">
          <span className="environment-dot" />
          <div>
            <strong>Snapshot analytics</strong>
            <span>Source-safe by design</span>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="topbar-context">
            <span>Workspace</span>
            <strong>Analytics</strong>
          </div>
          <div className="profile-wrap">
            <button
              className="profile-button"
              onClick={() => setProfileOpen((open) => !open)}
              type="button"
              aria-expanded={profileOpen}
              aria-controls="profile-menu"
              aria-label={`Open profile menu for ${user.display_name}`}
            >
              <span className="avatar">{initials(user.display_name)}</span>
              <span className="profile-copy">
                <strong>{user.display_name}</strong>
                <small>{humanRole(user.role)}</small>
              </span>
              <ChevronDown size={16} />
            </button>
            {profileOpen && (
              <div className="profile-menu" id="profile-menu">
                <p>{user.email}</p>
                <button
                  type="button"
                  onClick={() => signOut.mutate()}
                  disabled={signOut.isPending}
                >
                  <LogOut size={16} />
                  {signOut.isPending ? "Signing out…" : "Sign out"}
                </button>
              </div>
            )}
          </div>
        </header>
        <main className="content" id="main-content" tabIndex={-1}>{children}</main>
      </div>
    </div>
  );
}

function routeName(pathname: string): string {
  if (pathname.startsWith("/dashboards")) return "Dashboards";
  if (pathname.startsWith("/datasets")) return "Datasets";
  if (pathname.startsWith("/charts")) return "Charts";
  if (pathname.startsWith("/connections")) return "Connections";
  if (pathname.startsWith("/access")) return "Access";
  if (pathname.startsWith("/governance")) return "Governance";
  return "Overview";
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function humanRole(role: string): string {
  return role.charAt(0).toUpperCase() + role.slice(1);
}
