import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  CheckCircle2,
  CircleDashed,
  Database,
  LoaderCircle,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link, Navigate, useOutletContext } from "react-router-dom";

import { InlineError } from "../components/Status";
import {
  listConnections,
  problemMessage,
  testConnection,
  type Connection,
  type CurrentUser,
} from "../lib/api";
import { formatDateTime } from "../lib/format";
import { queryKeys } from "../lib/query";

export function ConnectionsPage() {
  const user = useOutletContext<CurrentUser>();
  if (user.role !== "admin") return <Navigate to="/overview" replace />;
  return <ConnectionsAdmin />;
}

function ConnectionsAdmin() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [testingId, setTestingId] = useState<string | null>(null);
  const connections = useQuery({
    queryKey: queryKeys.connections,
    queryFn: () => listConnections(),
  });
  const test = useMutation({
    mutationFn: testConnection,
    onMutate: setTestingId,
    onSettled: async () => {
      setTestingId(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.connections });
    },
  });
  const values = useMemo(
    () =>
      (connections.data?.data ?? []).filter((connection) =>
        `${connection.name} ${connection.host} ${connection.database_name}`
          .toLocaleLowerCase()
          .includes(search.toLocaleLowerCase()),
      ),
    [connections.data, search],
  );

  return (
    <div className="page-stack connections-page">
      <header className="page-heading split-heading">
        <div>
          <span className="eyebrow">Source control plane</span>
          <h1>Connections</h1>
          <p>
            Configure PostgreSQL once, then expose only the schemas and tables
            each analyst should discover.
          </p>
        </div>
        <Link className="button primary" to="/connections/new">
          <Plus size={17} /> Add connection
        </Link>
      </header>

      <div className="connection-principles" aria-label="Connection safeguards">
        <div><ShieldCheck size={18} /><span><strong>Encrypted secrets</strong><small>Credentials are write-only</small></span></div>
        <div><RefreshCw size={18} /><span><strong>Live catalog</strong><small>No stale metadata copy</small></span></div>
        <div><Database size={18} /><span><strong>Scoped discovery</strong><small>All, schema, or table</small></span></div>
      </div>

      <div className="collection-toolbar">
        <label className="search-field">
          <Search size={17} />
          <span className="sr-only">Search connections</span>
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search connections" />
        </label>
        <span>{values.length} configured</span>
      </div>

      {connections.isError && (
        <InlineError message={problemMessage(connections.error)} onRetry={() => void connections.refetch()} />
      )}
      {test.isError && <InlineError message={problemMessage(test.error)} />}

      <section className="connection-cards" aria-label="PostgreSQL connections">
        {connections.isPending && [0, 1, 2].map((item) => <div className="connection-card skeleton" key={item} />)}
        {values.map((connection) => (
          <ConnectionCard
            connection={connection}
            key={connection.id}
            testing={testingId === connection.id}
            onTest={() => test.mutate(connection.id)}
          />
        ))}
      </section>

      {!connections.isPending && values.length === 0 && (
        <div className="empty-state">
          <Database />
          <strong>{search ? "No matching connections" : "No PostgreSQL sources yet"}</strong>
          <p>{search ? "Try a host, database, or connection name." : "Add the first source without sharing its credentials with analysts."}</p>
          {!search && <Link className="button primary small" to="/connections/new"><Plus size={15} /> Add connection</Link>}
        </div>
      )}
    </div>
  );
}

function ConnectionCard({ connection, testing, onTest }: { connection: Connection; testing: boolean; onTest: () => void }) {
  const state = connection.last_test_succeeded === true ? "healthy" : connection.last_test_succeeded === false ? "failed" : "untested";
  return (
    <article className="connection-card">
      <header>
        <span className="connection-icon"><Database size={20} /></span>
        <span className={`connection-health ${state}`}>
          {state === "healthy" ? <CheckCircle2 size={13} /> : state === "failed" ? <XCircle size={13} /> : <CircleDashed size={13} />}
          {state === "healthy" ? "Healthy" : state === "failed" ? "Needs attention" : "Not tested"}
        </span>
      </header>
      <div className="connection-card-copy">
        <h2>{connection.name}</h2>
        <p>{connection.host}:{connection.port}</p>
      </div>
      <dl className="connection-facts">
        <div><dt>Database</dt><dd>{connection.database_name}</dd></div>
        <div><dt>Role</dt><dd>{connection.username}</dd></div>
        <div><dt>TLS</dt><dd>{connection.ssl_mode}</dd></div>
      </dl>
      <footer>
        <span>{connection.last_tested_at ? `Tested ${formatDateTime(connection.last_tested_at)}` : "Awaiting first test"}</span>
        <div>
          <button className="button secondary small" type="button" onClick={onTest} disabled={testing}>
            {testing ? <LoaderCircle className="spin" size={14} /> : <RefreshCw size={14} />}
            {testing ? "Testing…" : "Test"}
          </button>
          <Link className="icon-button bordered" to={`/connections/${connection.id}`} aria-label={`Manage ${connection.name}`}><ArrowRight size={17} /></Link>
        </div>
      </footer>
    </article>
  );
}
