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
