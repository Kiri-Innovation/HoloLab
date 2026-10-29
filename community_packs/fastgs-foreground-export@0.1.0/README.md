# Observed-foreground export

This conservative static-model export retains a Gaussian if its center projects
inside at least one calibrated foreground mask, dilated by two pixels at
resolution 2. It removes centers never supported by any foreground observation;
it does not require every camera to agree, which would damage moving limbs.

It writes a new model, a direct `ply-3dgs` file output, and `cleanup.json` with
before/after counts. Inputs are never modified. This is not a full visual-hull
intersection and cannot remove all Gaussian tails or all off-frame floaters.
Verify rendered foreground quality and wider views after exporting.
