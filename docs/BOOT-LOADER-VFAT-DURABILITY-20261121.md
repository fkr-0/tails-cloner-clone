# Boot-loader replacement on FAT/vfat — bounded qualification (2026-11-21)

Scope: host regular-file fixtures with injected syscall failures only. No loop
devices, mounting, block-device writes, VM reboot or actual power-cut testing.
The fixtures exercise the current uncommitted `boot_loader.py` implementation;
they do **not** establish behavior of a particular kernel/vfat mount.

> **Status at commit time.** The table below records the findings against the
> earlier `os.link`/unconditional-`fchmod` design. The committed implementation
> addresses findings 1–3: backups are published with `renameat2(RENAME_NOREPLACE)`
> (`_rename_no_replace`), `fchmod`/`fchown` tolerate `EPERM`/`EINVAL`/`EOPNOTSUPP`
> (`_copy_file_metadata`), and a post-rename directory-sync failure raises
> `ReplacementDurabilityError` with `replacement_applied = True`. The crash-
> consistency and power-loss findings remain open and unqualified.

## Findings

| Severity | Evidence | Consequence / recommendation |
| --- | --- | --- |
| **High** | `_create_backup` publishes via `os.link` (around line 466). Linux FAT/vfat does not implement hard links; injected `EOPNOTSUPP` prevents backup publication. | Ordinary vfat boot-loader reordering cannot rely on this backup design. Use an exclusively reserved destination name with an explicit no-clobber publication policy supported by the target filesystem; retest collisions and crash windows before enabling. |
| **High** | `_atomic_replace_bytes` calls `os.replace` and then `os.fsync(parent_fd)`. Injected directory `EINVAL` occurs **after** the config bytes were replaced, although the API raises failure. | Surface `replacement_applied / durability_unknown` distinctly. Do not imply the original remained on disk or that rollback restores power-loss safety. |
| **Medium** | Unconditional `os.fchmod` on temporary and backup descriptors; conditional `os.fchown`. Injected `EPERM` aborts before rename. | FAT permissions/ownership are synthesized from mount options (`uid`, `gid`, `umask`, `fmask`, `dmask`); do not require Unix chmod/chown semantics. Detect filesystem capability or use a deliberately bounded compatible policy. |
| **High** | File `fsync` and directory `fsync` plus `rename` do not establish crash consistency of FAT directory entries, allocation tables and contents. | No power-loss resistance claim. Real evidence requires disposable VM-backed FAT images with crash/reboot replay and filesystem check, plus explicit kernel/mount versions and fault cut points. |
| **Medium** | `os.replace` error injection preserves the original in a regular-file fixture; post-rename `fsync` failure leaves changed bytes visible. | Differentiate pre-rename and post-rename failures in public outcomes. Do not infer on-disk atomicity from host-visible rename atomicity. |

## Qualification matrix / result interpretation

1. `EOPNOTSUPP` on hardlink publication: fails before config mutation; original preserved in fixture.
2. `EPERM` on `fchmod` / forced `fchown`: stage fails; original preserved in fixture.
3. `EINVAL` on directory `fsync` after rename: function raises; replacement is **already visible** in fixture.
4. `EIO` on rename: original preserved in fixture.
5. Simulated mount `umask`/`fmask`/`dmask`: API fault injection covers metadata refusal, **not** real ownership/mode projection or bootability under mount options.
6. Power interruption before/within/after rename, FAT allocation writes, directory entry updates and cache flush: **unqualified**. Host mock tests cannot prove persistence or crash-safe rollback.

## Proposed VM-only follow-up (not executed)

- Create disposable *regular-file* FAT12/16/32 images in a VM and expose them
  through a virtual removable disk attached to that VM (never host loop or real devices).
- Record guest kernel, FAT driver, `mount` option set (`uid/gid/umask/fmask/dmask`,
  `flush`, `sync`), filesystem geometry and image SHA-256.
- Run the existing writer on a disposable guest mount for normal, hardlink,
  chmod/chown and directory-sync behavior; do not assume a mocked result is a
  real vfat result.
- Snapshot at file-stage, backup-publication, rename and sync boundaries;
  simulate abrupt VM power loss, boot a **fresh** guest from copied images and
  inspect both original/new config and recovery copy with independent checks.
- Treat every unverified write ordering and sync guarantee as unknown; report
  recovered states rather than labeling the algorithm power-loss safe.

## Safety and ownership

The implementation file is concurrently owned by other workers. This lane
changes only dedicated tests and this report; no device operations or publish.
No downstream delegation was initiated. If delegated later, the mandatory
recursive delegation contract must be copied unchanged to the child brief.
