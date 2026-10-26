---
title: Share a Pack others can verify
group: Package site
order: 80
---
# Share a Pack others can verify

You have an algorithm working locally and want someone else to use it. Sending a folder gets the files across, but a month later, which copy did they install? A repository branch can move, and two archives with the same filename can contain different code.

An algorithm **Pack** bundles code with a **manifest**, the configuration that describes the package and how to use it. The package site gives a published Pack a stable reference and the information a recipient needs to check its contents. You can share an exact version instead of asking someone to reconstruct your working directory.

## Find a release, then install it locally

The package site at `https://packs.xenotech.studio/` is a **registry**: a repository of package archives and metadata describing their releases. Publishers upload packages; readers query versions and download the corresponding archive.

It is not an execution platform. Downloading a Pack does not run its algorithm or prepare its runtime dependencies. The local installer verifies and places the files; the environment where you run HoloLab remains responsible for execution.

There is no account system on the site: no registration, login, or token-issuing API. Public reads need no credentials. Publishing requires a Bearer token, a credential supplied separately by the service provider to trusted publishers, with permission for the requested scope.

## Give the next person an exact package identity

A **package identity** names one release as `scope/name@version`. For example, `example/normalize@1.0.0` identifies version `1.0.0` of `normalize` in the `example` namespace. This is an illustrative name, not a claim that the package exists or that the scope is available to you.

Use an exact three-part version. The registry does not select “latest” or resolve version ranges. A workflow records the identity and expected code hash; local source configuration says where to retrieve it. Configure the site root as the source, not `/simple/`. Moving a source address should not require rewriting the package identity.

A published version is **immutable through the publication API**: uploading again cannot replace its contents, even after withdrawal. Changes require a new version number. This does not make the downloadable bytes tamper-proof; recipients still need to verify them.

**Yank** marks a release as unsuitable for new installations without deleting its metadata or archive. A client must reject a new installation of a yanked release. Existing references remain resolvable, and the publisher can undo the mark with `unyank`; neither operation frees the version number for reuse.

## Check the archive and the code separately

A **hash** is a digest computed from content so that a reader can compare it with an expected value. The registry records two SHA-256 hashes with different jobs:

| Field | What it checks | Who verifies it |
| --- | --- | --- |
| `archive_sha256` | The exact compressed `.tgz` bytes, including archive metadata | The server on upload and the client on download |
| `code_sha256` | Code identity within HoloLab’s declared scope, computed from manifest semantics and included file contents under FORMAT=2 | The installer recomputes it after safe extraction and compares it with the release and the workflow’s expected hash |

Repacking unchanged code can change the archive hash. Comparing only that hash cannot replace the code-identity check. The server records the publisher’s `code_sha256` declaration without recomputing it; metadata alone is not independent proof of the code.

To establish that you received the intended code, keep a trusted expected `code_sha256` with the workflow, check the release against it, and independently verify both the downloaded archive and extracted code. Stop on a mismatch; do not silently try another source until something installs. Matching hashes establish content identity within the declared scope, not algorithm safety, pinned external dependencies, or identical numerical results.

## Take one Pack from publication to use

Suppose you are sharing `example/normalize@1.0.0`. The smallest complete path is:

1. **Prepare a release.** If you have not made a Pack yet, start with [Write a Pack](/docs/build/writing-a-pack). Set the manifest’s name and exact version, assemble the `.tgz`, and compute both hashes using the rules in the [API reference](registry-api.md). Obtain publishing permission for your actual scope from the service provider.
2. **Publish it.** Send the metadata and archive to `POST /publish` with your publishing credential. Include an `Idempotency-Key`, a unique operation identifier that lets an identical request be retried without creating another publication. A timeout is not proof of failure: retry the identical request with the same key, following the API contract.
3. **Share the reference.** Give the recipient the exact identity, the expected code hash through a trusted channel, and the public source root for their local configuration. They need no token to read the package.
4. **Retrieve that release.** The recipient queries `GET /simple/example/normalize/`, selects exactly `example/normalize@1.0.0`, checks the expected identity and hash, and rejects a yanked release. They download the path in `Release.archive`, resolved against the source root.
5. **Verify before use.** The installer checks the archive size and hash, extracts safely, validates the manifest, and recomputes the code hash from actual files. Only after verification does it place the package locally and save the source, release metadata, and verification record. Runtime setup and execution are separate steps.

These are protocol steps for a publisher or installer, not a promise of a one-click site workflow. The next article, [Algorithm package registry API reference](registry-api.md), specifies request fields, retry behavior, and the complete installation verification algorithm.

## Know what the current site provides

Reads are public. There are no private scopes or version-deletion endpoints, and a scope is a namespace rather than a privacy boundary. Publishing permission does not make a package private. To withdraw a release from new installations, use yank; to correct its contents, publish a new version.

The site also has no search API or automatic version-range resolution. The package catalog and exact-version metadata provide discovery and retrieval. Local installation, runtime dependencies, and execution remain separate responsibilities. Treat this chapter and the API reference as the current public contract; older contributor design discussions may describe planned capabilities beyond it.
