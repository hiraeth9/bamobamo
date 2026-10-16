# CMU human motion starter clips

The original performer data comes from the CMU Graphics Lab optical motion
capture database, funded by NSF grant EIA-0196217. This is human motion capture,
not a claim that every retargeted pose is natural or that boxing clips make
contact with a real opponent. cgspeed converted the data to BVH; RancidMilk
converted it to FBX with Blender. The gbionics Hugging Face repository mirrors
the converted bones-only files and their labels; it is not the original author.

| File | Mirror metadata label | Actual imported range / fps | Source duration |
| --- | --- | --- | --- |
| 02_01.fbx | walk | 1–86 / 30 fps | 2.833333 s |
| 07_01.fbx | walk | 1–79 / 30 fps | 2.600000 s |
| 02_03.fbx | run/jog | 1–44 / 30 fps | 1.433333 s |
| 79_08.fbx | boxing | 1–111 / 30 fps | 3.666667 s |
| 80_10.fbx | boxing | 1–194 / 30 fps | 6.433333 s |

Each actual FBX import has 43 bones, a hip root and a source-frame-1 T calibration
pose. Start playback at source frame 2 (source time 1/30 second) to exclude the
calibration frame. Source units and retargeting must be handled by the CMU
adapter; a discovered file or label is not a compatibility claim. No finger
performance, paired opponent, contact timing, foot-support intervals, seamless
loop or full recovery has been established merely by this registration.

Original source and permissions: <https://mocap.cs.cmu.edu/>.
BVH conversion: <https://sites.google.com/a/cgspeed.com/cgspeed/motion-capture>.
FBX conversion and redistribution terms:
<https://rancidmilk.itch.io/free-character-animations>.
Mirror / metadata: <https://huggingface.co/datasets/gbionics/cmu-fbx>.

The five direct download paths are
`https://huggingface.co/datasets/gbionics/cmu-fbx/resolve/main/animations/<file>.fbx`
with `<file>` equal to one of 02_01, 07_01, 02_03, 79_08 or 80_10.

Read LICENSE.txt before redistribution. These clips are not CC0: use in
commercial products is permitted, but selling the raw animations, even after
conversion, is prohibited; pass the same conditions to recipients.
