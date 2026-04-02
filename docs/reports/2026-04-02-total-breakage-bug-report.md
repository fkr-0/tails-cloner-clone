# Strict Fundamentals Bug Report

## Title

We shipped a repo that did not run.

## Severity

Release-blocking. Total breakage.

## What was broken

The prior repository state was not just rough. It was functionally broken:

- the entrypoint imported modules that did not exist (`controller`, `services`, `models`)
- the GUI referenced controller state that could never be constructed
- the release workflow existed without a complete package layout to build
- the archive link problem meant the produced artifact was not practically retrievable by the user

That combination is not “unfinished.” It is a shipped non-product.

## Root cause

We claimed repository completeness before the repository passed the most basic structural checks:

1. import graph must resolve
2. tests must exist and fail first
3. tests must pass after implementation
4. archive output must be linked and downloadable
5. CI config must match the actual repository layout

We violated fundamentals and got the expected outcome: software that looked present but did not actually run.

## Corrective action in this revision

- rebuilt the project around a complete package layout
- added failing tests first, then implemented the missing modules
- replaced the brittle placeholder architecture with a working controller/model/service split
- added async interactive remote version listing
- aligned the AppImage workflow with the actual repository structure
- exported fresh downloadable archives

## Remaining hard truths

- destructive disk writing is still dangerous by nature
- GitHub Actions AppImage release is prepared but still needs one real tagged run on GitHub to count as release-verified
- remote version enumeration still depends on the upstream directory index format

## Opinionated conclusion

The previous state deserved a stop-ship call. This revision fixes the repo-level structural breakage, but it would still be irresponsible to call the release pipeline battle-proven before one real tag produces a real AppImage.
