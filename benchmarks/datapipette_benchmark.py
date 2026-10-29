#!/usr/bin/env python3
"""Prepare and exercise the DataPipette reference benchmark through its public API."""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import random
import statistics
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

BENCHMARK_NAMESPACE = UUID("c31b414b-d314-4f32-9ec4-dd7ef2d9166a")
CONNECTION_NAME = "Reference PostgreSQL benchmark"
DATASET_NAME_PREFIX = "Reference 5M x"
TERMINAL_REFRESH_STATES = {"succeeded", "failed", "cancelled"}
TERMINAL_EXPORT_STATES = {"succeeded", "failed", "expired"}

FIELD_NAMES = (
    "event_id",
    "customer_id",
    "occurred_at",
    "event_date",
    "category",
    "status",
    "region",
    "channel",
    "device_type",
    "product_code",
    "campaign_code",
    "country_code",
    "currency_code",
    "amount",
    "tax_amount",
    "discount_amount",
    "unit_price",
    "quantity",
    "score",
    "latency_ms",
    "duration_seconds",
    "retry_count",
    "page_count",
    "item_count",
    "priority",
    "risk_score",
    "metric_01",
    "metric_02",
    "metric_03",
    "metric_04",
    "metric_05",
    "metric_06",
    "metric_07",
    "metric_08",
    "metric_09",
    "metric_10",
    "flag_01",
    "flag_02",
    "flag_03",
    "flag_04",
    "flag_05",
    "attribute_01",
    "attribute_02",
    "attribute_03",
    "attribute_04",
    "attribute_05",
    "attribute_06",
    "attribute_07",
    "attribute_08",
    "description",
)


class BenchmarkError(RuntimeError):
    """A safe, human-readable benchmark failure."""


@dataclass(frozen=True, slots=True)
class ApiResponse:
    status: int
    headers: dict[str, str]
    payload: Any


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Sample:
    scenario: str
    status: int
    elapsed_ms: float
    response_bytes: int
    error: str | None = None

    def _replace_scenario(self, scenario: str) -> Sample:
        return Sample(scenario, self.status, self.elapsed_ms, self.response_bytes, self.error)


class ApiClient:
    """Small cookie-aware JSON client used only for benchmark orchestration."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> ApiResponse:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        request_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=request_headers,
            method=method,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                result = ApiResponse(
                    status=response.status,
                    headers={key.lower(): value for key, value in response.headers.items()},
                    payload=json.loads(raw) if raw else None,
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                problem = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                problem = {"detail": raw.decode(errors="replace")[:500]}
            result = ApiResponse(
                status=exc.code,
                headers={key.lower(): value for key, value in exc.headers.items()},
                payload=problem,
            )
        except OSError as exc:
            raise BenchmarkError(f"API request failed: {type(exc).__name__}") from exc
        if result.status not in expected:
            code = result.payload.get("code") if isinstance(result.payload, dict) else None
            raise BenchmarkError(
                f"{method} {path} returned HTTP {result.status}" + (f" ({code})" if code else "")
            )
        return result

    def login(self, email: str, password: str) -> None:
        self.request(
            "POST",
            "/auth/sessions",
            payload={"email": email, "password": password},
            expected=(201,),
        )

    @property
    def csrf_headers(self) -> dict[str, str]:
        token = next(
            (cookie.value for cookie in self.cookies if cookie.name == "datapipette_csrf"), None
        )
        if token is None:
            raise BenchmarkError("The authenticated session did not receive a CSRF cookie")
        return {"X-CSRF-Token": token}

    @property
    def cookie_header(self) -> str:
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.cookies)


class PersistentJsonClient:
    """One persistent HTTP connection owned by one virtual benchmark client."""

    def __init__(self, base_url: str, cookie_header: str, *, timeout: float) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise BenchmarkError("Benchmark API URL must be an absolute HTTP(S) URL")
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port
        self.base_path = parsed.path.rstrip("/")
        self.cookie_header = cookie_header
        self.timeout = timeout
        self.connection: http.client.HTTPConnection | http.client.HTTPSConnection | None = None

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def post(self, path: str, payload: dict[str, Any] | None) -> tuple[int, int]:
        return self.send("POST", path, payload)

    def get(self, path: str) -> tuple[int, int]:
        return self.send("GET", path, None)

    def send(self, method: str, path: str, payload: dict[str, Any] | None) -> tuple[int, int]:
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        last_error: OSError | http.client.HTTPException | None = None
        for _attempt in range(2):
            try:
                if self.connection is None:
                    connection_type = (
                        http.client.HTTPSConnection
                        if self.scheme == "https"
                        else http.client.HTTPConnection
                    )
                    self.connection = connection_type(self.host, self.port, timeout=self.timeout)
                headers = {"Accept": "application/json", "Cookie": self.cookie_header}
                if method != "GET":
                    headers["Content-Type"] = "application/json"
                    headers["Content-Length"] = str(len(body))
                self.connection.request(
                    method,
                    f"{self.base_path}{path}",
                    body=body if method != "GET" else None,
                    headers=headers,
                )
                response = self.connection.getresponse()
                raw = response.read()
                return response.status, len(raw)
            except (OSError, http.client.HTTPException) as exc:
                last_error = exc
                self.close()
        assert last_error is not None
        raise last_error


def _required_secret(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise BenchmarkError(f"Set {name} in the process environment")
    return value


def _field_id(name: str) -> str:
    return str(uuid5(BENCHMARK_NAMESPACE, f"field:{name}"))


def benchmark_definition(field_count: int = 50) -> dict[str, Any]:
    if not 1 <= field_count <= len(FIELD_NAMES):
        raise ValueError("field_count must be between 1 and 50")
    relation_id = str(uuid5(BENCHMARK_NAMESPACE, "relation:events"))
    fields = []
    for name in FIELD_NAMES[:field_count]:
        display_format = None
        if name == "occurred_at":
            display_format = "datetime_dd_mm_yyyy_hh_mm"
        elif name == "event_date":
            display_format = "date_dd_mm_yyyy"
        fields.append(
            {
                "id": _field_id(name),
                "source": {"relation_id": relation_id, "column": name},
                "name": name,
                "display": {
                    "label": name.replace("_", " ").title(),
                    "format": display_format,
                    "visible": True,
                },
            }
        )
    return {
        "kind": "visual",
        "schema_version": 1,
        "source": {
            "relations": [
                {
                    "id": relation_id,
                    "schema": "datapipette_benchmark",
                    "name": "events",
                    "alias": "events",
                }
            ]
        },
        "fields": fields,
        "joins": [],
    }


def _find_named(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((item for item in items if item.get("name") == name), None)


def _write_result(path: str | None, value: dict[str, Any]) -> None:
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path is None:
        print(rendered, end="")
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(target)
    print(f"Wrote {target}")


def _authenticated_client(args: argparse.Namespace) -> ApiClient:
    client = ApiClient(args.api_url, timeout=args.api_timeout)
    client.login(
        _required_secret("DATAPIPETTE_BENCHMARK_ADMIN_EMAIL"),
        _required_secret("DATAPIPETTE_BENCHMARK_ADMIN_PASSWORD"),
    )
    return client


def setup_benchmark(args: argparse.Namespace) -> None:
    client = _authenticated_client(args)
    started = time.perf_counter()
    dataset_name = f"{DATASET_NAME_PREFIX} {args.field_count}"

    connections = client.request("GET", "/connections?limit=100").payload["data"]
    connection = _find_named(connections, CONNECTION_NAME)
    if connection is None:
        connection = client.request(
            "POST",
            "/connections",
            payload={
                "name": CONNECTION_NAME,
                "host": args.source_host,
                "port": args.source_port,
                "database_name": args.source_database,
                "username": args.source_user,
                "password": _required_secret("DATAPIPETTE_BENCHMARK_SOURCE_PASSWORD"),
                "ssl_mode": args.source_ssl_mode,
            },
            headers=client.csrf_headers,
            expected=(201,),
        ).payload
    connection_id = connection["id"]
    connection_test = client.request(
        "POST",
        f"/connections/{connection_id}/tests",
        headers=client.csrf_headers,
    ).payload
    if not connection_test["succeeded"]:
        raise BenchmarkError(
            f"Source connection test failed ({connection_test.get('error_code') or 'unknown'})"
        )

    datasets = client.request("GET", "/datasets?limit=100").payload["data"]
    dataset = _find_named(datasets, dataset_name)
    if dataset is None:
        created = client.request(
            "POST",
            "/datasets",
            payload={
                "name": dataset_name,
                "connection_id": connection_id,
                "definition": benchmark_definition(args.field_count),
            },
            headers=client.csrf_headers,
            expected=(201,),
        )
        dataset = created.payload
        etag = created.headers["etag"]
    else:
        draft = client.request("GET", f"/datasets/{dataset['id']}/draft")
        etag = draft.headers["etag"]
    dataset_id = dataset["id"]

    validation_started = time.perf_counter()
    validation = client.request(
        "POST",
        f"/datasets/{dataset_id}/draft/validations",
        headers={**client.csrf_headers, "If-Match": etag},
    ).payload
    if validation["state"] == "blocked":
        validation = client.request(
            "POST",
            f"/datasets/{dataset_id}/draft/validations",
            payload={"override_reason": "Approved synthetic reference benchmark workload."},
            headers={**client.csrf_headers, "If-Match": etag},
        ).payload
    validation_seconds = time.perf_counter() - validation_started
    if validation["state"] not in {"valid", "valid_with_warnings"}:
        raise BenchmarkError(f"Dataset validation ended in {validation['state']}")

    impact = client.request(
        "POST",
        f"/datasets/{dataset_id}/draft/impact-analyses",
        headers={**client.csrf_headers, "If-Match": etag},
    ).payload
    if not impact["ready_for_publication"]:
        raise BenchmarkError("Dataset impact analysis did not allow publication")
    preview_started = time.perf_counter()
    preview = client.request(
        "POST",
        f"/datasets/{dataset_id}/draft/previews",
        payload={"limit": 100},
        headers={**client.csrf_headers, "If-Match": etag},
    ).payload
    preview_seconds = time.perf_counter() - preview_started

    refresh = client.request(
        "POST",
        f"/datasets/{dataset_id}/refresh-runs",
        headers={
            **client.csrf_headers,
            "If-Match": etag,
            "Idempotency-Key": f"reference-benchmark-{uuid4()}",
        },
        expected=(202,),
    ).payload
    refresh_id = refresh["id"]
    poll_started = time.monotonic()
    while refresh["state"] not in TERMINAL_REFRESH_STATES:
        if time.monotonic() - poll_started > args.refresh_timeout:
            raise BenchmarkError("Refresh did not finish before the benchmark timeout")
        time.sleep(args.poll_interval)
        refresh = client.request("GET", f"/datasets/{dataset_id}/refresh-runs/{refresh_id}").payload
    if refresh["state"] != "succeeded":
        raise BenchmarkError(f"Refresh ended in {refresh['state']}: {refresh.get('error_code')}")
    exploration = client.request("GET", f"/datasets/{dataset_id}/exploration").payload

    result = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "environment": {
            "api_url": args.api_url,
            "source_host": args.source_host,
            "source_port": args.source_port,
            "source_database": args.source_database,
            "source_ssl_mode": args.source_ssl_mode,
        },
        "dataset": {
            "id": dataset_id,
            "name": dataset_name,
            "field_count": len(exploration["columns"]),
            "snapshot_id": exploration["snapshot_id"],
            "snapshot_row_count": exploration["snapshot_row_count"],
        },
        "connection_test": connection_test,
        "validation": {
            "elapsed_seconds": round(validation_seconds, 3),
            "state": validation["state"],
            "policy_revision": validation["policy_revision"],
            "plan": validation["plan"],
            "findings": validation["findings"],
            "recommendations": validation["recommendations"],
            "overridden": validation["query_safety_override"] is not None,
        },
        "preview": {
            "elapsed_seconds": round(preview_seconds, 3),
            "returned_rows": preview["returned_rows"],
            "truncated": preview["truncated"],
        },
        "refresh": refresh,
        "orchestration_elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    _write_result(args.output, result)


def build_scenarios(exploration: dict[str, Any]) -> list[Scenario]:
    ids = {column["name"]: column["field_id"] for column in exploration["columns"]}
    if "event_id" not in ids:
        raise BenchmarkError("Published benchmark snapshot is missing event_id")
    common_fields = [ids[name] for name in FIELD_NAMES[:12] if name in ids]
    scenarios = [
        Scenario(
            "point_lookup",
            {
                "fields": common_fields,
                "filters": [{"field_id": ids["event_id"], "operator": "eq", "value": 2500000}],
                "sorts": [{"field_id": ids["event_id"], "direction": "asc"}],
                "page_size": 100,
            },
        ),
    ]
    if {"category", "occurred_at"} <= ids.keys():
        scenarios.append(
            Scenario(
                "categorical_filter",
                {
                    "fields": common_fields,
                    "filters": [
                        {
                            "field_id": ids["category"],
                            "operator": "eq",
                            "value": "category-7",
                        }
                    ],
                    "sorts": [{"field_id": ids["occurred_at"], "direction": "desc"}],
                    "page_size": 100,
                },
            )
        )
    if {"event_date", "amount"} <= ids.keys():
        scenarios.append(
            Scenario(
                "date_and_amount_range",
                {
                    "fields": common_fields,
                    "filters": [
                        {
                            "field_id": ids["event_date"],
                            "operator": "between",
                            "values": ["2025-01-01", "2025-03-31"],
                        },
                        {
                            "field_id": ids["amount"],
                            "operator": "gte",
                            "value": "500.00",
                        },
                    ],
                    "sorts": [{"field_id": ids["event_id"], "direction": "asc"}],
                    "page_size": 100,
                },
            )
        )
    if "description" in ids:
        scenarios.append(
            Scenario(
                "text_scan",
                {
                    "fields": common_fields[:8],
                    "filters": [
                        {
                            "field_id": ids["description"],
                            "operator": "contains",
                            "value": "class 42",
                        }
                    ],
                    "sorts": [{"field_id": ids["event_id"], "direction": "asc"}],
                    "page_size": 100,
                },
            )
        )
    scenarios.append(
        Scenario(
            "wide_page",
            {
                "fields": [ids[name] for name in FIELD_NAMES if name in ids],
                "filters": [],
                "sorts": [{"field_id": ids["event_id"], "direction": "desc"}],
                "page_size": 100,
            },
        )
    )
    return scenarios


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def summarize_samples(samples: list[Sample], duration_seconds: float) -> dict[str, Any]:
    successful = [sample for sample in samples if sample.status == 200]
    successful_latencies = [sample.elapsed_ms for sample in successful]
    statuses: dict[str, int] = {}
    scenarios: dict[str, dict[str, Any]] = {}
    for sample in samples:
        status = str(sample.status)
        statuses[status] = statuses.get(status, 0) + 1
    for name in sorted({sample.scenario for sample in samples}):
        values = [
            sample.elapsed_ms
            for sample in samples
            if sample.scenario == name and sample.status == 200
        ]
        attempted = sum(sample.scenario == name for sample in samples)
        scenarios[name] = {
            "attempted": attempted,
            "succeeded": len(values),
            "p50_ms": _rounded(percentile(values, 0.50)),
            "p95_ms": _rounded(percentile(values, 0.95)),
            "p99_ms": _rounded(percentile(values, 0.99)),
        }
    return {
        "attempted": len(samples),
        "succeeded": len(successful),
        "success_rate": round(len(successful) / len(samples), 6) if samples else 0.0,
        "requests_per_second": round(len(successful) / duration_seconds, 3),
        "status_counts": statuses,
        "successful_latency_ms": {
            "min": _rounded(min(successful_latencies) if successful_latencies else None),
            "mean": _rounded(
                statistics.fmean(successful_latencies) if successful_latencies else None
            ),
            "p50": _rounded(percentile(successful_latencies, 0.50)),
            "p95": _rounded(percentile(successful_latencies, 0.95)),
            "p99": _rounded(percentile(successful_latencies, 0.99)),
            "max": _rounded(max(successful_latencies) if successful_latencies else None),
        },
        "scenarios": scenarios,
    }


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def run_load(args: argparse.Namespace) -> None:
    client = _authenticated_client(args)
    exploration = client.request("GET", f"/datasets/{args.dataset_id}/exploration").payload
    scenarios = build_scenarios(exploration)
    samples: list[Sample] = []
    samples_lock = threading.Lock()
    barrier = threading.Barrier(args.concurrency + 1)
    started = time.monotonic()
    warmup_ends = started + args.warmup
    run_ends = warmup_ends + args.duration

    def worker(worker_id: int) -> None:
        connection = PersistentJsonClient(
            args.api_url,
            client.cookie_header,
            timeout=args.request_timeout,
        )
        iteration = 0
        barrier.wait()
        try:
            while time.monotonic() < run_ends:
                scenario = scenarios[(worker_id + iteration) % len(scenarios)]
                request_started = time.perf_counter()
                status = 0
                response_bytes = 0
                error = None
                try:
                    status, response_bytes = connection.post(
                        f"/datasets/{args.dataset_id}/rows:query", scenario.payload
                    )
                except (OSError, http.client.HTTPException) as exc:
                    error = type(exc).__name__
                elapsed_ms = (time.perf_counter() - request_started) * 1000
                if time.monotonic() >= warmup_ends:
                    with samples_lock:
                        samples.append(
                            Sample(
                                scenario=scenario.name,
                                status=status,
                                elapsed_ms=elapsed_ms,
                                response_bytes=response_bytes,
                                error=error,
                            )
                        )
                iteration += 1
        finally:
            connection.close()

    threads = [
        threading.Thread(target=worker, args=(worker_id,), name=f"benchmark-{worker_id}")
        for worker_id in range(args.concurrency)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    result = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "dataset_id": args.dataset_id,
        "snapshot_id": exploration["snapshot_id"],
        "snapshot_row_count": exploration["snapshot_row_count"],
        "concurrency": args.concurrency,
        "warmup_seconds": args.warmup,
        "duration_seconds": args.duration,
        "field_count": len(exploration["columns"]),
        "summary": summarize_samples(samples, args.duration),
        "errors": sorted({sample.error for sample in samples if sample.error}),
    }
    _write_result(args.output, result)


BROWSER_CONNECTIONS_PER_HOST = 6


def dashboard_selection(
    filters: list[dict[str, Any]],
    values: dict[str, list[Any]],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Pick what a viewer might choose: one to two values per list filter, or a month."""

    selections: list[dict[str, Any]] = []
    for item in filters:
        if item["kind"] == "values":
            options = values.get(item["id"], [])
            if options and rng.random() < 0.7:
                chosen = rng.sample(options, k=min(len(options), rng.choice((1, 2))))
                selections.append({"filter_id": item["id"], "values": chosen})
        elif rng.random() < 0.5:
            month = rng.randint(1, 12)
            last_day = 28 if month == 2 else 30 if month in {4, 6, 9, 11} else 31
            year = datetime.now(UTC).year - rng.choice((0, 1))
            selections.append(
                {
                    "filter_id": item["id"],
                    "from": f"{year}-{month:02d}-01",
                    "to": f"{year}-{month:02d}-{last_day}",
                }
            )
    return selections


def items_for_selection(
    item_id: str, filters: list[dict[str, Any]], selections: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Send each item only the choices of filters that target it, as the frontend does."""

    targeted = {
        item["id"]
        for item in filters
        if any(target["item_id"] == item_id for target in item["targets"])
    }
    return [selection for selection in selections if selection["filter_id"] in targeted]


def run_dashboard(args: argparse.Namespace) -> None:
    client = _authenticated_client(args)
    dashboard = client.request("GET", f"/dashboards/{args.dashboard_id}").payload
    items = dashboard["definition"].get("items") or []
    filters = dashboard["definition"].get("filters") or []
    if not items:
        raise BenchmarkError("The dashboard has no charts to load")
    values = {
        item["id"]: client.request(
            "GET", f"/dashboards/{args.dashboard_id}/filters/{item['id']}/values"
        ).payload["values"]
        for item in filters
        if item["kind"] == "values"
    }
    samples: list[Sample] = []
    samples_lock = threading.Lock()
    barrier = threading.Barrier(args.concurrency + 1)
    started = time.monotonic()
    warmup_ends = started + args.warmup
    run_ends = warmup_ends + args.duration

    def viewer(viewer_id: int) -> None:
        rng = random.Random(args.seed + viewer_id)
        local = threading.local()
        connections: list[PersistentJsonClient] = []

        def connection() -> PersistentJsonClient:
            if not hasattr(local, "client"):
                local.client = PersistentJsonClient(
                    args.api_url, client.cookie_header, timeout=args.request_timeout
                )
                connections.append(local.client)
            return local.client

        def timed(scenario: str, call: Any) -> Sample:
            request_started = time.perf_counter()
            status, response_bytes, error = 0, 0, None
            try:
                status, response_bytes = call()
            except (OSError, http.client.HTTPException) as exc:
                error = type(exc).__name__
            return Sample(
                scenario,
                status,
                (time.perf_counter() - request_started) * 1000,
                response_bytes,
                error,
            )

        pool = ThreadPoolExecutor(
            max_workers=min(BROWSER_CONNECTIONS_PER_HOST, len(items)),
            thread_name_prefix=f"viewer-{viewer_id}",
        )
        barrier.wait()
        # Viewers start at different moments, as people do.
        time.sleep(rng.uniform(0, args.think_time))
        try:
            while time.monotonic() < run_ends:
                filtered = bool(filters) and rng.random() < args.filter_share
                selection = dashboard_selection(filters, values, rng) if filtered else []
                view_started = time.perf_counter()
                layout = timed(
                    "layout", lambda: connection().get(f"/dashboards/{args.dashboard_id}")
                )

                def load(
                    item: dict[str, Any], selection: list[dict[str, Any]] = selection
                ) -> Sample:
                    chosen = items_for_selection(item["id"], filters, selection)
                    item_started = time.perf_counter()
                    # Like the frontend, retry capacity and server errors twice, one
                    # second apart (the API's Retry-After), before showing an error.
                    for attempt in range(args.browser_retries + 1):
                        sample = timed(
                            "item_filtered" if chosen else "item",
                            lambda: connection().post(
                                f"/dashboards/{args.dashboard_id}/items/{item['id']}/data:query",
                                {"filters": chosen} if chosen else None,
                            ),
                        )
                        if sample.status < 500 or attempt == args.browser_retries:
                            break
                        with samples_lock:
                            if time.monotonic() >= warmup_ends:
                                samples.append(sample._replace_scenario("retried"))
                        time.sleep(1.0)
                    return Sample(
                        sample.scenario,
                        sample.status,
                        (time.perf_counter() - item_started) * 1000,
                        sample.response_bytes,
                        sample.error,
                    )

                item_samples = list(pool.map(load, items))
                view_ms = (time.perf_counter() - view_started) * 1000
                worst = max(
                    (sample.status for sample in (layout, *item_samples) if sample.status != 200),
                    default=200,
                )
                view = Sample("view_filtered" if selection else "view", worst, view_ms, 0, None)
                if time.monotonic() >= warmup_ends:
                    with samples_lock:
                        samples.extend((layout, *item_samples, view))
                time.sleep(rng.uniform(0.5, 1.5) * args.think_time)
        finally:
            pool.shutdown(wait=True)
            for item in connections:
                item.close()

    threads = [
        threading.Thread(target=viewer, args=(viewer_id,), name=f"viewer-{viewer_id}")
        for viewer_id in range(args.concurrency)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    views = [sample for sample in samples if sample.scenario.startswith("view")]
    requests = [
        sample
        for sample in samples
        if not sample.scenario.startswith("view") and sample.scenario != "retried"
    ]
    result = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "dashboard_id": args.dashboard_id,
        "item_count": len(items),
        "filter_count": len(filters),
        "concurrency": args.concurrency,
        "think_time_seconds": args.think_time,
        "filter_share": args.filter_share,
        "warmup_seconds": args.warmup,
        "duration_seconds": args.duration,
        "browser_retries": args.browser_retries,
        "retried_attempts": sum(sample.scenario == "retried" for sample in samples),
        "dashboard_views": summarize_samples(views, args.duration),
        "requests": summarize_samples(requests, args.duration),
        "errors": sorted({sample.error for sample in samples if sample.error}),
    }
    _write_result(args.output, result)


def run_exports(args: argparse.Namespace) -> None:
    client = _authenticated_client(args)
    exploration = client.request("GET", f"/datasets/{args.dataset_id}/exploration").payload
    ids = {column["name"]: column["field_id"] for column in exploration["columns"]}
    fields = [ids[name] for name in FIELD_NAMES[: args.export_fields]]
    requests = []
    for index in range(args.jobs):
        first = index * args.export_rows + 1
        last = (index + 1) * args.export_rows
        response = client.request(
            "POST",
            f"/datasets/{args.dataset_id}/exports",
            payload={
                "format": "csv",
                "fields": fields,
                "filters": [
                    {
                        "field_id": ids["event_id"],
                        "operator": "between",
                        "values": [first, last],
                    }
                ],
                "sorts": [{"field_id": ids["event_id"], "direction": "asc"}],
            },
            headers={
                **client.csrf_headers,
                "Idempotency-Key": f"reference-export-{uuid4()}",
            },
            expected=(202,),
        ).payload
        requests.append(response)

    started = time.monotonic()
    pending = {item["id"]: item for item in requests}
    while any(item["state"] not in TERMINAL_EXPORT_STATES for item in pending.values()):
        if time.monotonic() - started > args.export_timeout:
            raise BenchmarkError("Exports did not finish before the benchmark timeout")
        time.sleep(args.poll_interval)
        for export_id, item in list(pending.items()):
            if item["state"] not in TERMINAL_EXPORT_STATES:
                pending[export_id] = client.request(
                    "GET", f"/datasets/{args.dataset_id}/exports/{export_id}"
                ).payload
    result = {
        "schema_version": 1,
        "recorded_at": datetime.now(UTC).isoformat(),
        "dataset_id": args.dataset_id,
        "requested_jobs": args.jobs,
        "rows_per_job_filter": args.export_rows,
        "fields_per_job": args.export_fields,
        "wall_seconds": round(time.monotonic() - started, 3),
        "exports": list(pending.values()),
    }
    _write_result(args.output, result)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument(
        "--api-url",
        default=os.environ.get("DATAPIPETTE_BENCHMARK_API_URL", "http://127.0.0.1:8080/api/v1"),
    )
    root.add_argument("--api-timeout", type=float, default=30.0)
    commands = root.add_subparsers(dest="command", required=True)

    setup = commands.add_parser("setup", help="Create, validate, preview, and publish the fixture")
    setup.add_argument("--source-host", default="10.174.80.4")
    setup.add_argument("--source-port", type=int, default=5432)
    setup.add_argument("--source-database", default="datapipette")
    setup.add_argument("--source-user", default="datapipette")
    setup.add_argument("--source-ssl-mode", choices=("disable", "require"), default="disable")
    setup.add_argument("--field-count", type=int, choices=range(1, 51), default=50)
    setup.add_argument("--poll-interval", type=float, default=5.0)
    setup.add_argument("--refresh-timeout", type=float, default=3600.0)
    setup.add_argument("--output")
    setup.set_defaults(func=setup_benchmark)

    load = commands.add_parser("load", help="Run the mixed interactive snapshot workload")
    load.add_argument("--dataset-id", required=True)
    load.add_argument("--concurrency", type=int, choices=range(1, 101), default=20)
    load.add_argument("--warmup", type=float, default=10.0)
    load.add_argument("--duration", type=float, default=60.0)
    load.add_argument("--request-timeout", type=float, default=30.0)
    load.add_argument("--output")
    load.set_defaults(func=run_load)

    dashboard = commands.add_parser(
        "dashboard",
        help="Simulate viewers opening a dashboard, loading every chart, and changing filters",
    )
    dashboard.add_argument("--dashboard-id", required=True)
    dashboard.add_argument("--concurrency", type=int, choices=range(1, 201), default=20)
    dashboard.add_argument("--think-time", type=float, default=3.0)
    dashboard.add_argument("--filter-share", type=float, default=0.5)
    dashboard.add_argument("--warmup", type=float, default=15.0)
    dashboard.add_argument("--duration", type=float, default=120.0)
    dashboard.add_argument("--request-timeout", type=float, default=30.0)
    dashboard.add_argument("--seed", type=int, default=20261029)
    dashboard.add_argument("--browser-retries", type=int, choices=range(0, 6), default=2)
    dashboard.add_argument("--output")
    dashboard.set_defaults(func=run_dashboard)

    exports = commands.add_parser("exports", help="Run bounded concurrent CSV export jobs")
    exports.add_argument("--dataset-id", required=True)
    exports.add_argument("--jobs", type=int, choices=range(1, 9), default=2)
    exports.add_argument("--export-rows", type=int, default=250000)
    exports.add_argument("--export-fields", type=int, choices=range(1, 51), default=12)
    exports.add_argument("--poll-interval", type=float, default=5.0)
    exports.add_argument("--export-timeout", type=float, default=3600.0)
    exports.add_argument("--output")
    exports.set_defaults(func=run_exports)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
    except BenchmarkError as exc:
        print(f"benchmark error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
