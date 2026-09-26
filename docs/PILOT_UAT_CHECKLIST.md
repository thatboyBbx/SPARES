# SPOP Pilot UAT Checklist

Run this on a staging copy of the pilot database. Record the tester, date,
device, and outcome for every item. Never use the pilot credentials in a
production deployment.

## All roles

- [ ] Sign in, refresh the page, and sign out successfully.
- [ ] Disconnect the device, submit one permitted transaction, reconnect, and
      confirm it appears once only with no unresolved sync issue.
- [ ] Confirm the interface is usable at 320 px width and the page has no
      horizontal overflow.

## Owner

- [ ] Review profit and loss, branch performance, approval summary, and
      replenishment suggestions.
- [ ] Confirm a low-stock product appears in stock health and suggestions.

## Store Keeper

- [ ] Receive stock, fulfil a requested transfer, and confirm the movement
      ledger reflects the change.

## Cashier and Shop Manager

- [ ] Record a sale and confirm shop stock reduces exactly once.
- [ ] Request and receive a transfer, then confirm it is no longer in transit.

## Accountant

- [ ] Create and approve an expense; confirm its journal entry is created.
- [ ] Record a supplier payment and confirm its journal entry is created.

## Exit decision

- [ ] All failed items have a documented owner and corrective action.
- [ ] A database backup and restore drill has completed successfully.
- [ ] Owner approves pilot launch.
