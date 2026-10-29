from bench import run

for p, d, e in [
    (1, 2, 8),
    (2, 2, 4),
    (4, 1, 2),
    (8, 1, 1),
    (8, 2, 1),
    (8, 1, 2),
    (4, 2, 4),
    (2, 4, 8),
]:
    run(
        f"fast_p{p}_d{d}_e{e}",
        p=p,
        d=d,
        e=e,
        mode="png",
        where="disk",
        extra=("-compression_level", "1", "-pred", "sub"),
    )
run(
    "fast_paeth_p8_d1_e2",
    p=8,
    d=1,
    e=2,
    mode="png",
    where="disk",
    extra=(
        "-compression_level",
        "1",
    ),
)
