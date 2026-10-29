# Working agreements

Conventions this repository expects from automated contributors.

## Publishing and test artifacts

Anything published to the central registry during development or verification
must be removed before the task is reported as done. The registry is public and
shared; a leftover probe pack is indistinguishable from a real contribution.

- Prefer a throwaway identity (`community/<something>-linkcheck@0.0.1`) over a
  real pack and a real version number.
- Delete the published version from the registry (database row plus the COS
  archive object) once the check has served its purpose.
- If a check has to publish under a real name, say so explicitly in the report
  and remove it afterwards.
- When in doubt, verify against a local `file://` registry first and only
  publish remotely for the step that genuinely needs the remote path.

## Local secrets

Credentials live outside the repository (`~/.hololab/publisher.env`,
`/etc/hololab-packs/publisher.env`) with mode `0600`. Never commit them, never
pass them on a command line, and never echo them into logs or reports.

## Registry content

Versions are immutable: publish a new version instead of editing or reuploading
an existing one. Retracting a version means yanking it, not deleting it, unless
it is a verified throwaway probe (see above).

## Removing or renaming packs

A pack's name, version and manifest content are all part of its identity, so
removing a version, renaming a pack or editing its manifest reaches further than
the pack directory. Before doing any of it, search every consumer:

- tests (including parametrised lists and hard-coded version strings),
- workflows (drafts and, where it matters, saved snapshots),
- docs, reports and screenshots that quote the pack,
- the central registry, if the version was published.

Two follow-ups that keep coming up:

- Contract-style tests should discover retained community packs by scanning `community_packs/*/manifest.yaml`
  rather than listing versions, so retaining fewer versions cannot break them.
  Behaviour-style tests, which assert on one specific version, should stay
  explicit and point at a version that is actually retained.
- Editing a published pack's manifest changes its code identity, so the copy in
  the registry becomes stale. Republish it in the same change instead of leaving
  a mismatch behind.
