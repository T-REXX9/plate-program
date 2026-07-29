# Mobile App Readiness Boundary

## Current state

Plate Program is prepared for a future account-service connection, but that
connection is disabled. Existing vehicle registration, expiration checks,
dashboard operation, plate recognition, and gate decisions continue to use the
current `vehicles` table without consulting mobile entitlement data.

## Data stored locally

The dormant schema stores only:

- the mapping between a local vehicle and remote vehicle/household identifiers;
- the latest entitlement status and paid-through/grace dates;
- an opaque synchronization cursor and last-success/error timestamps;
- a minimal audit trail for future synchronization runs.

Homeowner passwords, mobile sessions, payment details, Xendit credentials, and
complete payment history do not belong on the local gate server.

## Safe activation sequence

The integration must not be enabled until all of the following exist:

1. A public HTTPS account service with authenticated homeowner APIs.
2. Verified and idempotent Xendit webhook processing.
3. Per-installation credentials for signed entitlement synchronization.
4. An initial full entitlement synchronization and reconciliation report.
5. Tests covering paid, grace-period, expired, suspended, offline, stale-sync,
   refund, reversal, and administrator-override scenarios.
6. An explicit migration that updates the reader authorization policy.

Merely setting `MOBILE_ACCOUNT_INTEGRATION_ENABLED=1` does not change gate
authorization. The current capability endpoint will report `prepared`, while
`authorization_enforcement` remains `false`.
