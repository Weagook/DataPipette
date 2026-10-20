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

import {
  logout,
  problemMessage,
  updateCurrentUserLocale,
  type CurrentUser,
} from "../lib/api";
import { useI18n, type InterfaceLocale } from "../lib/i18n";
import { queryKeys } from "../lib/query";
import { Brand } from "./Brand";

interface AppShellProps {
  user: CurrentUser;
  children: ReactNode;
}

export function AppShell({ user, children }: AppShellProps) {
  const { locale, setLocale, t } = useI18n();
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
  const language = useMutation({
    mutationFn: updateCurrentUserLocale,
    onSuccess: (updatedUser) => {
      queryClient.setQueryData(queryKeys.session, updatedUser);
      setLocale(updatedUser.locale === "ru" ? "ru" : "en");
    },
    onError: () => setLocale(user.locale === "ru" ? "ru" : "en"),
  });
  const canAuthor = user.role === "admin" || user.role === "analyst";
  const isAdmin = user.role === "admin";
  const pageName = routeName(location.pathname, t);

  useEffect(() => {
    document.title = `${pageName} · DataPipette`;
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
      <a className="skip-link" href="#main-content">{t("Skip to content")}</a>
      <span className="sr-only" aria-live="polite">{t("{page} page", { page: pageName })}</span>
      <button
        className="mobile-menu-button"
        onClick={() => setMobileOpen(true)}
        type="button"
        aria-label={t("Open navigation")}
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
            aria-label={t("Close navigation")}
            type="button"
            ref={closeButton}
          >
            <X size={18} />
          </button>
        </div>
        <nav className="primary-nav" aria-label={t("Primary navigation")}>
          <NavLink to="/overview" onClick={() => setMobileOpen(false)}>
            <Gauge size={18} />
            {t("Overview")}
          </NavLink>
          <NavLink to="/dashboards" onClick={() => setMobileOpen(false)}>
            <LayoutDashboard size={18} />
            {t("Dashboards")}
          </NavLink>
          {canAuthor && (
            <div className="nav-group" aria-label={t("Authoring")}>
              <span className="nav-group-label">{t("Authoring")}</span>
              <NavLink to="/datasets" onClick={() => setMobileOpen(false)}>
                <Database size={18} />
                {t("Datasets")}
              </NavLink>
              <NavLink to="/charts" onClick={() => setMobileOpen(false)}>
                <BarChart3 size={18} />
                {t("Charts")}
              </NavLink>
            </div>
          )}
          {isAdmin && (
            <div className="nav-group" aria-label={t("Administration")}>
              <span className="nav-group-label">{t("Administration")}</span>
              <NavLink to="/connections" onClick={() => setMobileOpen(false)}>
                <Gauge size={18} />
                {t("Connections")}
              </NavLink>
              <NavLink to="/access" onClick={() => setMobileOpen(false)}>
                <Users size={18} />
                {t("Access")}
              </NavLink>
              <NavLink to="/governance" onClick={() => setMobileOpen(false)}>
                <ShieldCheck size={18} />
                {t("Governance")}
              </NavLink>
            </div>
          )}
        </nav>
        <div className="sidebar-foot">
          <span className="environment-dot" />
          <div>
            <strong>{t("Snapshot analytics")}</strong>
            <span>{t("Source-safe by design")}</span>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="topbar-context">
            <span>{t("Workspace")}</span>
            <strong>{t("Analytics")}</strong>
          </div>
          <div className="profile-wrap">
            <button
              className="profile-button"
              onClick={() => setProfileOpen((open) => !open)}
              type="button"
              aria-expanded={profileOpen}
              aria-controls="profile-menu"
              aria-label={t("Open profile menu for {name}", { name: user.display_name })}
            >
              <span className="avatar">{initials(user.display_name)}</span>
              <span className="profile-copy">
                <strong>{user.display_name}</strong>
                <small>{humanRole(user.role, t)}</small>
              </span>
              <ChevronDown size={16} />
            </button>
            {profileOpen && (
              <div className="profile-menu" id="profile-menu">
                <p>{user.email}</p>
                <label className="profile-language">
                  <span>{t("Interface language")}</span>
                  <select
                    value={locale}
                    disabled={language.isPending}
                    onChange={(event) => {
                      const nextLocale = event.target.value as InterfaceLocale;
                      setLocale(nextLocale);
                      language.mutate(nextLocale);
                    }}
                  >
                    <option value="en">{t("English")}</option>
                    <option value="ru">{t("Russian")}</option>
                  </select>
                  {language.isPending && <small>{t("Saving language…")}</small>}
                  {language.isError && (
                    <small className="profile-error" role="alert">
                      {problemMessage(language.error)}
                    </small>
                  )}
                </label>
                <button
                  type="button"
                  onClick={() => signOut.mutate()}
                  disabled={signOut.isPending}
                >
                  <LogOut size={16} />
                  {signOut.isPending ? t("Signing out…") : t("Sign out")}
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

function routeName(pathname: string, t: (message: string) => string): string {
  if (pathname.startsWith("/dashboards")) return t("Dashboards");
  if (pathname.startsWith("/datasets")) return t("Datasets");
  if (pathname.startsWith("/charts")) return t("Charts");
  if (pathname.startsWith("/connections")) return t("Connections");
  if (pathname.startsWith("/access")) return t("Access");
  if (pathname.startsWith("/governance")) return t("Governance");
  return t("Overview");
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

function humanRole(role: string, t: (message: string) => string): string {
  if (role === "admin") return t("Administrator");
  if (role === "analyst") return t("Analyst");
  if (role === "viewer") return t("Viewer");
  return role;
}
