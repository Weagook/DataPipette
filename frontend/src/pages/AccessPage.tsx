import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Check,
  Copy,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  Plus,
  Search,
  ShieldCheck,
  Trash2,
  UserPlus,
  UserRound,
  UsersRound,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Navigate, useOutletContext } from "react-router-dom";

import { InlineError } from "../components/Status";
import {
  addGroupMember,
  createApiToken,
  createGroup,
  createServiceAccount,
  createUser,
  listApiTokens,
  listGroupMembers,
  listGroups,
  listServiceAccounts,
  listUsers,
  problemMessage,
  removeGroupMember,
  renameGroup,
  revokeApiToken,
  revokeUserSessions,
  updateServiceAccount,
  updateUser,
  type ApiToken,
  type CurrentUser,
  type Group,
  type IssuedApiToken,
  type ServiceAccount,
  type SystemRole,
  type UserAdmin,
} from "../lib/api";
import { formatDateTime, relativeTime } from "../lib/format";
import { queryKeys } from "../lib/query";

type AccessTab = "people" | "groups" | "integrations";

export function AccessPage() {
  const user = useOutletContext<CurrentUser>();
  if (user.role !== "admin") return <Navigate to="/overview" replace />;
  return <AccessAdmin currentUser={user} />;
}

function AccessAdmin({ currentUser }: { currentUser: CurrentUser }) {
  const [tab, setTab] = useState<AccessTab>("people");
  const users = useQuery({ queryKey: queryKeys.users, queryFn: listUsers });
  const groups = useQuery({ queryKey: queryKeys.groups, queryFn: listGroups });
  const accounts = useQuery({ queryKey: queryKeys.serviceAccounts, queryFn: listServiceAccounts });
  const error = users.error ?? groups.error ?? accounts.error;

  return (
    <div className="page-stack access-page">
      <header className="page-heading">
        <div><span className="eyebrow">Identity control plane</span><h1>Access</h1><p>Keep human access, team membership, and external integrations understandable from one governed workspace.</p></div>
        <span className="trust-pill"><ShieldCheck size={16} /> Authoritative on every request</span>
      </header>
      <section className="access-metrics" aria-label="Access summary">
        <Metric icon={<UserRound size={18} />} label="People" value={users.data?.length} note={`${users.data?.filter((user) => user.status === "active").length ?? 0} active`} />
        <Metric icon={<UsersRound size={18} />} label="Groups" value={groups.data?.length} note="Additive memberships" />
        <Metric icon={<Bot size={18} />} label="Integrations" value={accounts.data?.length} note={`${accounts.data?.filter((account) => account.status === "active").length ?? 0} active`} />
      </section>
      <nav className="access-tabs" aria-label="Access areas">
        <button className={tab === "people" ? "active" : ""} onClick={() => setTab("people")} type="button"><UserRound size={16} /> People</button>
        <button className={tab === "groups" ? "active" : ""} onClick={() => setTab("groups")} type="button"><UsersRound size={16} /> Groups</button>
        <button className={tab === "integrations" ? "active" : ""} onClick={() => setTab("integrations")} type="button"><Bot size={16} /> Integrations</button>
      </nav>
      {error && <InlineError message={problemMessage(error)} onRetry={() => { void users.refetch(); void groups.refetch(); void accounts.refetch(); }} />}
      {tab === "people" && <PeoplePanel currentUser={currentUser} users={users.data ?? []} loading={users.isPending} />}
      {tab === "groups" && <GroupsPanel users={users.data ?? []} groups={groups.data ?? []} loading={groups.isPending} />}
      {tab === "integrations" && <IntegrationsPanel accounts={accounts.data ?? []} loading={accounts.isPending} />}
    </div>
  );
}

function Metric({ icon, label, value, note }: { icon: React.ReactNode; label: string; value?: number; note: string }) {
  return <article><span>{icon}</span><div><small>{label}</small><strong>{value ?? "—"}</strong><p>{note}</p></div></article>;
}

function PeoplePanel({ currentUser, users, loading }: { currentUser: CurrentUser; users: UserAdmin[]; loading: boolean }) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const filtered = useMemo(() => users.filter((user) => `${user.display_name} ${user.email} ${user.role}`.toLocaleLowerCase().includes(search.toLocaleLowerCase())), [search, users]);
  const update = useMutation({
    mutationFn: ({ userId, input }: { userId: string; input: Parameters<typeof updateUser>[1] }) => updateUser(userId, input),
    onSettled: async () => queryClient.invalidateQueries({ queryKey: queryKeys.users }),
  });
  const revoke = useMutation({ mutationFn: revokeUserSessions });

  return <section className="access-surface">
    <header className="access-surface-heading"><div><span className="panel-icon"><UserRound size={18} /></span><span><strong>People</strong><small>Local accounts and coarse capability roles.</small></span></div><button className="button primary small" type="button" onClick={() => setCreating((value) => !value)}>{creating ? <X size={15} /> : <UserPlus size={15} />}{creating ? "Close" : "Add person"}</button></header>
    {creating && <CreateUserPanel onClose={() => setCreating(false)} />}
    <div className="access-toolbar"><label className="search-field"><Search size={16} /><span className="sr-only">Search people</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search people" /></label><span>{filtered.length} accounts</span></div>
    {update.isError && <InlineError message={problemMessage(update.error)} />}
    {revoke.isError && <InlineError message={problemMessage(revoke.error)} />}
    <div className="identity-list" aria-label="People">
      {loading && [0, 1, 2].map((item) => <div className="identity-row skeleton" key={item} />)}
      {filtered.map((user) => <article className="identity-row" key={user.id}>
        <span className="identity-avatar">{initials(user.display_name)}</span>
        <div className="identity-primary"><strong>{user.display_name}{user.id === currentUser.id && <em>You</em>}</strong><small>{user.email}</small></div>
        <label><span>Role</span><select aria-label={`Role for ${user.display_name}`} value={user.role} onChange={(event) => update.mutate({ userId: user.id, input: { role: event.target.value as SystemRole } })} disabled={update.isPending}><option value="admin">Administrator</option><option value="analyst">Analyst</option><option value="viewer">Viewer</option></select></label>
        <label><span>Status</span><select aria-label={`Status for ${user.display_name}`} value={user.status} onChange={(event) => update.mutate({ userId: user.id, input: { status: event.target.value as "active" | "disabled" } })} disabled={update.isPending}><option value="active">Active</option><option value="disabled">Disabled</option></select></label>
        <div className="identity-activity"><span className={`account-state ${user.status}`}>{user.status}</span><small>{user.last_login_at ? `Seen ${relativeTime(user.last_login_at)}` : "Never signed in"}</small></div>
        <button className="icon-button bordered" type="button" aria-label={`Revoke sessions for ${user.display_name}`} title="Revoke browser sessions" onClick={() => revoke.mutate(user.id)} disabled={revoke.isPending}>{revoke.isPending && revoke.variables === user.id ? <LoaderCircle className="spin" size={15} /> : <LogOut size={15} />}</button>
      </article>)}
      {!loading && filtered.length === 0 && <div className="empty-state compact"><UserRound /><strong>No matching people</strong><p>Try another name, email, or role.</p></div>}
    </div>
    {revoke.isSuccess && <div className="access-feedback"><Check size={15} /> Revoked {revoke.data} active browser {revoke.data === 1 ? "session" : "sessions"}.</div>}
  </section>;
}

function CreateUserPanel({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [email, setEmail] = useState(""); const [name, setName] = useState(""); const [password, setPassword] = useState(""); const [role, setRole] = useState<SystemRole>("viewer");
  const create = useMutation({ mutationFn: () => createUser({ email: email.trim(), display_name: name.trim(), password, role }), onSuccess: async () => { setPassword(""); await queryClient.invalidateQueries({ queryKey: queryKeys.users }); onClose(); } });
  const valid = email.includes("@") && name.trim() && password.length >= 12;
  return <form className="quick-create" onSubmit={(event) => { event.preventDefault(); if (valid) create.mutate(); }}>
    <div><strong>Create local account</strong><small>The password is sent once and never returned.</small></div>
    <label><span>Display name</span><input aria-label="Display name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Ada Analyst" /></label>
    <label><span>Email</span><input aria-label="Email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="ada@example.com" /></label>
    <label><span>Initial password</span><input aria-label="Initial password" type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="At least 12 characters" /></label>
    <label><span>Role</span><select aria-label="New user role" value={role} onChange={(event) => setRole(event.target.value as SystemRole)}><option value="viewer">Viewer</option><option value="analyst">Analyst</option><option value="admin">Administrator</option></select></label>
    <button className="button primary" type="submit" disabled={!valid || create.isPending}>{create.isPending ? <LoaderCircle className="spin" size={15} /> : <Plus size={15} />} Create person</button>
    {create.isError && <InlineError message={problemMessage(create.error)} />}
  </form>;
}

function GroupsPanel({ users, groups, loading }: { users: UserAdmin[]; groups: Group[]; loading: boolean }) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const [newName, setNewName] = useState("");
  useEffect(() => { if (!groups.some((group) => group.id === selectedId)) setSelectedId(groups[0]?.id ?? ""); }, [groups, selectedId]);
  const selected = groups.find((group) => group.id === selectedId);
  const create = useMutation({ mutationFn: () => createGroup(newName.trim()), onSuccess: async (group) => { setNewName(""); setSelectedId(group.id); await queryClient.invalidateQueries({ queryKey: queryKeys.groups }); } });

  return <section className="access-split">
    <aside className="group-directory">
      <header><span className="panel-icon"><UsersRound size={18} /></span><div><strong>Groups</strong><small>Reusable access bundles</small></div></header>
      <form onSubmit={(event) => { event.preventDefault(); if (newName.trim()) create.mutate(); }}><input aria-label="New group name" value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="Finance analysts" /><button className="icon-button" type="submit" disabled={!newName.trim() || create.isPending} aria-label="Create group"><Plus size={16} /></button></form>
      {create.isError && <InlineError message={problemMessage(create.error)} />}
      <nav aria-label="Groups">{loading && <LoaderCircle className="spin" size={16} />}{groups.map((group) => <button type="button" className={group.id === selectedId ? "active" : ""} onClick={() => setSelectedId(group.id)} key={group.id}><span>{initials(group.name)}</span><div><strong>{group.name}</strong><small>Updated {relativeTime(group.updated_at)}</small></div></button>)}</nav>
    </aside>
    {selected ? <GroupDetail group={selected} users={users} /> : <div className="access-detail-empty"><UsersRound size={27} /><strong>Create the first group</strong><p>Groups make repeated dashboard, dataset, and connection grants easier to govern.</p></div>}
  </section>;
}

function GroupDetail({ group, users }: { group: Group; users: UserAdmin[] }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(group.name);
  const [userId, setUserId] = useState("");
  useEffect(() => setName(group.name), [group.id, group.name]);
  const members = useQuery({ queryKey: queryKeys.groupMembers(group.id), queryFn: () => listGroupMembers(group.id) });
  const memberCount = members.data?.length ?? 0;
  const memberIds = new Set((members.data ?? []).map((membership) => membership.user_id));
  const eligible = users.filter((user) => user.status === "active" && !memberIds.has(user.id));
  useEffect(() => { if (!eligible.some((user) => user.id === userId)) setUserId(eligible[0]?.id ?? ""); }, [eligible, userId]);
  const rename = useMutation({ mutationFn: () => renameGroup(group.id, name.trim()), onSuccess: async () => queryClient.invalidateQueries({ queryKey: queryKeys.groups }) });
  const add = useMutation({ mutationFn: () => addGroupMember(group.id, userId), onSuccess: async () => queryClient.invalidateQueries({ queryKey: queryKeys.groupMembers(group.id) }) });
  const remove = useMutation({ mutationFn: (id: string) => removeGroupMember(group.id, id), onSuccess: async () => queryClient.invalidateQueries({ queryKey: queryKeys.groupMembers(group.id) }) });
  const error = members.error ?? rename.error ?? add.error ?? remove.error;

  return <div className="group-detail">
    <header><div><span className="eyebrow">Team access</span><h2>{group.name}</h2><p>Membership changes affect the next authorized request.</p></div><span className="member-count">{memberCount} {memberCount === 1 ? "member" : "members"}</span></header>
    <form className="rename-group" onSubmit={(event) => { event.preventDefault(); if (name.trim() && name.trim() !== group.name) rename.mutate(); }}><label><span>Group name</span><input aria-label="Group name" value={name} onChange={(event) => setName(event.target.value)} /></label><button className="button secondary small" type="submit" disabled={!name.trim() || name.trim() === group.name || rename.isPending}>Rename</button></form>
    <form className="add-member" onSubmit={(event) => { event.preventDefault(); if (userId) add.mutate(); }}><div><strong>Add a person</strong><small>Only active local accounts can join.</small></div><select aria-label="Person to add" value={userId} onChange={(event) => setUserId(event.target.value)}><option value="">No eligible people</option>{eligible.map((user) => <option value={user.id} key={user.id}>{user.display_name} · {user.email}</option>)}</select><button className="button primary small" type="submit" disabled={!userId || add.isPending}><UserPlus size={14} /> Add member</button></form>
    {error && <InlineError message={problemMessage(error)} />}
    <div className="member-list">{members.isPending && <LoaderCircle className="spin" />}{members.data?.map((membership) => { const user = users.find((value) => value.id === membership.user_id); return <article key={membership.user_id}><span className="identity-avatar small">{initials(user?.display_name ?? "?")}</span><div><strong>{user?.display_name ?? membership.user_id}</strong><small>{user?.email ?? "Account unavailable"} · Added {formatDateTime(membership.created_at)}</small></div><button className="icon-button" type="button" onClick={() => remove.mutate(membership.user_id)} aria-label={`Remove ${user?.display_name ?? "member"}`}><Trash2 size={14} /></button></article>; })}{!members.isPending && members.data?.length === 0 && <div className="empty-state compact"><UsersRound /><strong>No members yet</strong><p>Add an active person to begin.</p></div>}</div>
  </div>;
}

function IntegrationsPanel({ accounts, loading }: { accounts: ServiceAccount[]; loading: boolean }) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<"viewer" | "analyst">("viewer");
  useEffect(() => { if (!accounts.some((account) => account.id === selectedId)) setSelectedId(accounts[0]?.id ?? ""); }, [accounts, selectedId]);
  const selected = accounts.find((account) => account.id === selectedId);
  const create = useMutation({ mutationFn: () => createServiceAccount(name.trim(), role), onSuccess: async (account) => { setName(""); setSelectedId(account.id); await queryClient.invalidateQueries({ queryKey: queryKeys.serviceAccounts }); } });
  return <section className="integration-layout">
    <div className="integration-directory">
      <header><div><span className="panel-icon"><Bot size={18} /></span><span><strong>Service accounts</strong><small>One identity per external system</small></span></div></header>
      <form onSubmit={(event) => { event.preventDefault(); if (name.trim()) create.mutate(); }}><label><span>Name</span><input aria-label="Service account name" value={name} onChange={(event) => setName(event.target.value)} placeholder="CRM dashboard embed" /></label><label><span>Role</span><select aria-label="Service account role" value={role} onChange={(event) => setRole(event.target.value as "viewer" | "analyst")}><option value="viewer">Viewer</option><option value="analyst">Analyst</option></select></label><button className="button primary" type="submit" disabled={!name.trim() || create.isPending}><Plus size={15} /> Create identity</button></form>
      {create.isError && <InlineError message={problemMessage(create.error)} />}
      <div className="service-account-list">{loading && <LoaderCircle className="spin" />}{accounts.map((account) => <button type="button" key={account.id} className={account.id === selectedId ? "active" : ""} onClick={() => setSelectedId(account.id)}><span><Bot size={16} /></span><div><strong>{account.name}</strong><small>{account.role} · {account.status}</small></div></button>)}</div>
    </div>
    {selected ? <ServiceAccountDetail account={selected} /> : <div className="access-detail-empty"><KeyRound size={27} /><strong>No integration identity</strong><p>Create a service account before issuing its first API token.</p></div>}
  </section>;
}

function ServiceAccountDetail({ account }: { account: ServiceAccount }) {
  const queryClient = useQueryClient();
  const [tokenName, setTokenName] = useState("");
  const [expiry, setExpiry] = useState<"never" | "30" | "90">("90");
  const [issued, setIssued] = useState<IssuedApiToken | null>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => { setIssued(null); setCopied(false); }, [account.id]);
  const tokens = useQuery({ queryKey: queryKeys.apiTokens(account.id), queryFn: () => listApiTokens(account.id) });
  const update = useMutation({ mutationFn: (status: "active" | "disabled") => updateServiceAccount(account.id, { status }), onSettled: async () => queryClient.invalidateQueries({ queryKey: queryKeys.serviceAccounts }) });
  const issue = useMutation({ mutationFn: () => createApiToken(account.id, { name: tokenName.trim(), scopes: ["dashboards:read"], expires_at: expiry === "never" ? null : new Date(Date.now() + Number(expiry) * 86_400_000).toISOString() }), onSuccess: async (token) => { setTokenName(""); setIssued(token); setCopied(false); await queryClient.invalidateQueries({ queryKey: queryKeys.apiTokens(account.id) }); } });
  const revoke = useMutation({ mutationFn: (tokenId: string) => revokeApiToken(account.id, tokenId), onSuccess: async () => queryClient.invalidateQueries({ queryKey: queryKeys.apiTokens(account.id) }) });
  const error = tokens.error ?? update.error ?? issue.error ?? revoke.error;

  async function copyToken() { if (!issued) return; await navigator.clipboard.writeText(issued.token); setCopied(true); }

  return <div className="service-account-detail">
    <header><div><span className="eyebrow">External integration</span><h2>{account.name}</h2><p>Tokens narrow capability; dashboard grants still determine object access.</p></div><button className={`button small ${account.status === "active" ? "secondary" : "primary"}`} type="button" onClick={() => update.mutate(account.status === "active" ? "disabled" : "active")}><LockKeyhole size={14} /> {account.status === "active" ? "Disable identity" : "Enable identity"}</button></header>
    <div className="token-safety"><ShieldCheck size={18} /><div><strong>Rotate without downtime</strong><p>Issue a replacement, update the external secret, verify it, then revoke the old token.</p></div></div>
    {issued && <section className="issued-token" aria-label="New API token"><header><div><span className="one-time-pill">Shown once</span><strong>Copy this token now</strong></div><button className="icon-button" type="button" onClick={() => setIssued(null)} aria-label="Dismiss token"><X size={16} /></button></header><p>After dismissal, DatePipette cannot recover this plaintext. Store it in the external system's secret manager.</p><code>{issued.token}</code><button className="button primary wide" type="button" onClick={() => void copyToken()}>{copied ? <Check size={15} /> : <Copy size={15} />}{copied ? "Copied" : "Copy token"}</button></section>}
    <form className="issue-token" onSubmit={(event) => { event.preventDefault(); if (tokenName.trim() && account.status === "active") issue.mutate(); }}><div><strong>Issue a token</strong><small>{account.status === "active" ? "Current closed scope: dashboards:read" : "Enable this identity before issuing tokens"}</small></div><label><span>Token name</span><input aria-label="Token name" value={tokenName} onChange={(event) => setTokenName(event.target.value)} placeholder="Production · October rotation" disabled={account.status !== "active"} /></label><label><span>Expires</span><select aria-label="Token expiry" value={expiry} onChange={(event) => setExpiry(event.target.value as typeof expiry)} disabled={account.status !== "active"}><option value="30">In 30 days</option><option value="90">In 90 days</option><option value="never">Never</option></select></label><button className="button primary" type="submit" disabled={account.status !== "active" || !tokenName.trim() || issue.isPending}><KeyRound size={15} /> Issue token</button></form>
    {error && <InlineError message={problemMessage(error)} />}
    <div className="token-list"><header><strong>Token history</strong><span>{tokens.data?.filter((token) => !token.revoked_at).length ?? 0} active</span></header>{tokens.isPending && <LoaderCircle className="spin" />}{tokens.data?.map((token) => <TokenRow token={token} onRevoke={() => revoke.mutate(token.id)} revoking={revoke.isPending && revoke.variables === token.id} key={token.id} />)}{!tokens.isPending && tokens.data?.length === 0 && <div className="empty-state compact"><KeyRound /><strong>No tokens issued</strong><p>Issue the first credential for this integration.</p></div>}</div>
  </div>;
}

function TokenRow({ token, onRevoke, revoking }: { token: ApiToken; onRevoke: () => void; revoking: boolean }) {
  const expired = token.expires_at ? new Date(token.expires_at).getTime() <= Date.now() : false;
  const state = token.revoked_at ? "revoked" : expired ? "expired" : "active";
  return <article className="token-row"><span className="token-icon"><KeyRound size={15} /></span><div><strong>{token.name}</strong><small>{token.scopes.join(", ")} · {token.expires_at ? `Expires ${formatDateTime(token.expires_at)}` : "Never expires"}</small></div><div className="token-usage"><span className={`account-state ${state}`}>{state}</span><small>{token.last_used_at ? `Used ${relativeTime(token.last_used_at)}` : "Never used"}</small></div>{!token.revoked_at && <button className="icon-button bordered" type="button" onClick={onRevoke} disabled={revoking} aria-label={`Revoke ${token.name}`}>{revoking ? <LoaderCircle className="spin" size={14} /> : <Trash2 size={14} />}</button>}</article>;
}

function initials(value: string): string { return value.split(/\s+/).slice(0, 2).map((part) => part[0]?.toUpperCase()).join(""); }
