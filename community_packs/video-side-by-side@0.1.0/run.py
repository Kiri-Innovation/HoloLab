from fractions import Fraction

from _media import encode, main, number, probe, video


def run(i, p, out):
    left, right = video(i["left"]), video(i["right"])
    ls, _ = probe(left)
    rs, _ = probe(right)
    height = int(number(p["height"], "height"))
    if height == 0:
        height = min(ls["height"], rs["height"]) // 2 * 2
    if height < 2 or height % 2:
        raise ValueError("height must be zero (automatic) or a positive even integer")
    fps = Fraction(ls["avg_frame_rate"])
    if fps <= 0:
        raise ValueError("Left video must declare a positive frame rate")
    filt = f"[0:v:0]scale=-2:{height},setsar=1,fps={fps},setpts=PTS-STARTPTS[l];[1:v:0]scale=-2:{height},setsar=1,fps={fps},setpts=PTS-STARTPTS[r];[l][r]hstack=inputs=2:shortest=1[v]"
    encode(["-i", str(left), "-i", str(right), "-filter_complex", filt, "-map", "[v]"], out)


if __name__ == "__main__":
    main(run)
