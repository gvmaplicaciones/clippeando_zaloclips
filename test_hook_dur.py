"""Test de duracion del hook enhancement: bug d=300 vs fix d_exacto."""
import sys, math, subprocess
from pathlib import Path
sys.path.insert(0, ".")
from src.vertical import crop_to_vertical
from src.ffmpeg_utils import probe_duration

src = Path("input/OIY8onKQSt8.mp4")
tmp_in  = Path("C:/Temp/hook_test_in.mp4")
tmp_buggy = Path("C:/Temp/hook_test_buggy.mp4")
tmp_fixed = Path("C:/Temp/hook_test_fixed.mp4")
Path("C:/Temp").mkdir(exist_ok=True)

print("=== 1. Creando clip de entrada (6.2s) ===")
crop_to_vertical(src, tmp_in, start=1192.5, duration=6.2)
inp_dur = probe_duration(tmp_in)
expected = 0.4 + inp_dur / 0.82
print(f"INPUT:    {inp_dur:.3f}s")
print(f"EXPECTED: {expected:.3f}s  (0.4s freeze + {inp_dur:.3f}/{0.82:.2f} = {inp_dur/0.82:.3f}s slowmo)")

def run_ffmpeg(args):
    r = subprocess.run(["ffmpeg"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        print("ERROR:", r.stderr[-400:])
        return False
    return True

base_filter = (
    "[0:v]split=2[vin_frz][vin_slow];"
    "[vin_frz]trim=end=0.1,setpts=PTS-STARTPTS[vshort];"
    "[vshort]loop=loop=-1:size=1:start=0[vloop];"
    "[vloop]trim=duration=0.400,setpts=PTS-STARTPTS,fps=30[vfreeze];"
    "{ZOOMPAN};"
    "[vfreeze][vslow]concat=n=2:v=1:a=0[vout];"
    "aevalsrc=0:c=stereo:s=44100:d=0.400[asilence];"
    "[0:a]atempo=0.8200,aresample=44100[aslow];"
    "[asilence][aslow]concat=n=2:v=0:a=1[aout]"
)
common_out = ["-map","[vout]","-map","[aout]","-c:v","libx264","-preset","ultrafast","-crf","28","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k"]

print("\n=== 2. BUGGY: d=300 fijo ===")
filt_buggy = base_filter.replace(
    "{ZOOMPAN}",
    "[vin_slow]setpts=PTS/0.8200,zoompan=z='min(zoom+0.0008,1.08)':d=300:s=1080x1920:fps=30[vslow]"
)
ok = run_ffmpeg(["-i",str(tmp_in),"-filter_complex",filt_buggy] + common_out + ["-y",str(tmp_buggy)])
if ok:
    d = probe_duration(tmp_buggy)
    print(f"BUGGY OUTPUT: {d:.3f}s  (expected {expected:.3f}s, ratio {d/expected:.2f}x)  BUG={'SI' if d/expected > 1.05 else 'NO'}")

print("\n=== 3. FIXED: d calculado + -t limite ===")
d_frames = math.ceil((inp_dur / 0.82) * 30)
total_s = f"{expected:.3f}"
filt_fixed = base_filter.replace(
    "{ZOOMPAN}",
    f"[vin_slow]setpts=PTS/0.8200,zoompan=z='min(zoom+0.0008,1.08)':d={d_frames}:s=1080x1920:fps=30[vslow]"
)
print(f"  d_frames = ceil({inp_dur:.3f} / 0.82 * 30) = {d_frames}")
print(f"  -t {total_s}")
ok = run_ffmpeg(["-i",str(tmp_in),"-filter_complex",filt_fixed] + common_out + ["-t",total_s,"-y",str(tmp_fixed)])
if ok:
    d = probe_duration(tmp_fixed)
    print(f"FIXED OUTPUT: {d:.3f}s  (expected {expected:.3f}s, ratio {d/expected:.2f}x)  BUG={'SI' if d/expected > 1.05 else 'NO'}")

print("\nDone.")
