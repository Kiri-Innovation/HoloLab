# colmap4d HoloLab integration (incubating)

This directory is the first-party incubation home for HoloLab's colmap4d
integration. The pack is kept inside HoloLab while the pack contract and its
distribution story are still evolving.

## Long-term ownership

The intended ownership model is “the project that owns a format also owns its
generic platform integrations.” Once the conditions below are met, the generic
packs in this integration should move as a directory to:

```text
colmap4d/integrations/hololab/
```

A move is appropriate when all of the following are true:

- the HoloLab manifest schema has stable, public documentation;
- an external contributor can run a public manifest validator;
- installation and pack-source registration have stable public instructions;
- public CI can validate the integration without private repository credentials;
- HoloLab API and colmap4d package compatibility have an explicit version policy.

Only generic colmap4d capabilities move upstream: validation, conversion,
inspection, and viewing. Kiri-specific capture and product workflow policy does
not move. In particular, rig capture, materialized temporal grouping, device
clock conversion, and the STG pipeline remain owned by Kiri4DGS/HoloLab even
when their thin adapters call public colmap4d APIs.

## Movable-directory rules

Every pack developed here must remain movable as one directory:

- never embed machine-specific absolute paths;
- keep adapters thin and call only public `colmap4d` Python APIs;
- never reach into the source checkout through paths such as `../../../src`;
- declare and check the minimum supported `colmap4d` package version;
- keep viewer HTML, JavaScript, CSS, and other static assets inside the pack
  directory because HoloLab's file server confines pack assets to that root.

`colmap4d-temporal-grouping@0.1.0` is intentionally classified as Kiri-owned
materialization policy. It lives here to exercise the integration boundary, but
it is not one of the generic nodes that will eventually move upstream.

## Runtime dependency

The HoloLab environment named by `runtime.env` must contain `colmap4d>=1.0.2`.
Installing a checkout for development is fine; the pack itself never imports
through a repository-relative path.
