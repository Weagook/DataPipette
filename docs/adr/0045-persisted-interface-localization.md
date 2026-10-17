# ADR-0045: Persisted English and Russian interface localization

- Status: Accepted
- Date: 2026-10-18

## Context

The official frontend must be usable in English and Russian without changing the public machine-readable vocabulary used by API clients. A browser-only preference would be lost across devices and private sessions, while inferring a language from the browser on every visit would make the interface unpredictable. The project currently needs two static locales and does not need the loading, extraction, or translation-service features of a general localization framework.

## Decision

- English is the default interface language for every existing and new local user.
- Store the user's selected `en` or `ru` locale in metadata PostgreSQL and return it in authenticated user projections.
- Expose a CSRF-protected `PATCH /api/v1/auth/me/preferences` operation so the official frontend and other browser clients can persist the current user's locale without administrator involvement.
- Keep localization in a small typed React context and an in-repository Russian dictionary. Switch immediately in the browser, update the session query after success, and restore the persisted value when a session is loaded.
- Translate client-owned labels, guidance, validation messages, empty states, status descriptions, and accessibility text. Keep API error codes, audit action identifiers, UUIDs, enum wire values, SQL, and source schema/table/column names unchanged.

## Consequences

- The preference follows the account across supported browsers and devices and remains independent of browser storage.
- Existing rows migrate safely to English through a non-null server default and a database check constraint rejects unsupported values.
- The frontend gains no localization runtime dependency, but every new user-facing string must be added to the Russian dictionary and covered during UI review.
- API consumers receive an additive `locale` field on user responses and may use the preference without having to reproduce the official frontend implementation.
- Adding more locales or pluralization-heavy copy may justify replacing the small dictionary with a dedicated internationalization library later.

## Alternatives considered

- **Persist only in `localStorage`:** rejected because the preference would not follow the user and would make browser storage part of authenticated profile state.
- **Always follow the browser locale:** rejected because users need an explicit stable choice and shared workstations may not reflect the account owner's preference.
- **Translate API problem codes and wire enums:** rejected because machine-readable identifiers are compatibility contracts, not presentation copy.
- **Add a localization framework now:** rejected because two static locales do not yet justify another production dependency and translation abstraction layer.
