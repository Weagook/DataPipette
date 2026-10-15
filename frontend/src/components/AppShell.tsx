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
import { useState, type ReactNode } from "react";
import { NavLink, useNavigate } from "react-router-dom";

import { logout, type CurrentUser } from "../lib/api";
import { queryKeys } from "../lib/query";
import { Brand } from "./Brand";

interface AppShellProps {
  user: CurrentUser;
  children: ReactNode;
}

const futureAnalystAreas = [
  { icon: Database, label: "Datasets" },
  { icon: BarChart3, label: "Charts" },
];

const futureAdminAreas = [
  { icon: Gauge, label: "Connections" },
  { icon: Users, label: "Access" },
  { icon: ShieldCheck, label: "Governance" },
];

export function AppShell({ user, children }: AppShellProps) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const signOut = useMutation({
    mutationFn: logout,
    onSettled: async () => {
      queryClient.clear();
      await navigate("/login", { replace: true });
    },
  });
  const canAuthor = user.role === "admin" || user.role === "analyst";
  const isAdmin = user.role === "admin";

  return (
    <div className="app-frame">
      <button
        className="mobile-menu-button"
        onClick={() => setMobileOpen(true)}
        type="button"
        aria-label="Open navigation"
      >
        <Menu size={20} />
      </button>
      {mobileOpen && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />}
      <aside className={`sidebar ${mobileOpen ? "open" : ""}`}>
        <div className="sidebar-head">
          <Brand />
          <button className="mobile-close" onClick={() => setMobileOpen(false)} aria-label="Close navigation" type="button">
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
            <div className="nav-group" aria-label="Authoring roadmap">
              <span className="nav-group-label">Authoring</span>
              {futureAnalystAreas.map(({ icon: Icon, label }) => (
                <span className="future-nav" key={label} title={`${label} arrives in the next frontend slices`}>
                  <Icon size={18} />
                  {label}
                  <small>Next</small>
                </span>
              ))}
            </div>
          )}
          {isAdmin && (
            <div className="nav-group" aria-label="Administration roadmap">
              <span className="nav-group-label">Administration</span>
              {futureAdminAreas.map(({ icon: Icon, label }) => (
                <span className="future-nav" key={label} title={`${label} arrives in a later frontend slice`}>
                  <Icon size={18} />
                  {label}
                  <small>Planned</small>
                </span>
              ))}
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
            <button className="profile-button" onClick={() => setProfileOpen((open) => !open)} type="button" aria-expanded={profileOpen}>
              <span className="avatar">{initials(user.display_name)}</span>
              <span className="profile-copy">
                <strong>{user.display_name}</strong>
                <small>{humanRole(user.role)}</small>
              </span>
              <ChevronDown size={16} />
            </button>
            {profileOpen && (
              <div className="profile-menu">
                <p>{user.email}</p>
                <button type="button" onClick={() => signOut.mutate()} disabled={signOut.isPending}>
                  <LogOut size={16} />
                  {signOut.isPending ? "Signing out…" : "Sign out"}
                </button>
              </div>
            )}
          </div>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
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
