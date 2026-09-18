# Changelog

## 1.2.0 - 2026-09-18

- `ingeteam-sun-storage-3power-c` bumped to version 2: 42250 and 42252
  (grid-forming voltage and frequency droop) moved to `excluded`. The vendor
  map marks both read-only (R, not R/W) and 42252 was observed changing
  between reads (114, 98, 107 within 50 minutes), so they are values reported
  by the inverter, not configuration. Snapshots made with v1 are not
  comparable with v2 snapshots.

## 1.1.0 - 2026-09-18

- Profiles gain an optional `protocol.address_offset` (default 0), added to
  every PDU address after the `addressing` conversion, for firmware that
  serves its documented registers shifted by a constant number of words.
- `jinko-scu-bank` and `jinko-scu-rack` bumped to version 2 with
  `address_offset: 2`. On the SunTera G2 SCU the register documented at
  1-based address A is served at PDU address A+1; the v1 profiles read the
  word pair before each documented value, and the rack profile hashed the
  rack voltage instead of the insulation detection state. Snapshots made
  with the v1 profiles are not comparable with v2 snapshots.

## 1.0.0 - 2026-09-18

- First release.
