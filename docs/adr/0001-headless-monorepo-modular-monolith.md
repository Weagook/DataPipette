# ADR-0001: Headless API-first monorepo and modular monolith

- Status: Accepted
- Date: 2026-10-08

## Context

DataPipette must provide a standalone UI and a backend that other products can use independently. The project needs clear boundaries without the operational cost of early microservices. A portfolio repository should also be straightforward to run and review.

## Decision

DataPipette will use one monorepo with independently buildable backend and frontend applications. The backend is the product core and exposes all official capabilities through a public REST API. Backend domain modules begin as a modular monolith, while API, worker, and scheduler processes may be deployed independently.

## Consequences

- The official frontend cannot rely on hidden backend access or frontend-only business behavior.
- Backend and frontend have independent dependencies, tests, builds, and container images.
- Domain boundaries must remain explicit enough to extract a service later if justified.
- Contributors can run and review the product from one repository.
- CI can target changed applications while still running contract integration checks.

## Alternatives considered

- Separate repositories were rejected initially because they increase coordination and setup cost before independent release cycles are necessary.
- Microservices were rejected because the current product boundaries do not justify distributed transactions, deployment, and observability complexity.
