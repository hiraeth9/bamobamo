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
| 69_16.fbx | turn in place | 1–282 / 30 fps | 9.366667 s |
| 16_17.fbx | walk, 90-degree left turn | 1–130 / 30 fps | 4.300000 s |
| 16_33.fbx | slow walk, stop | 1–72 / 30 fps | 2.366667 s |
| 22_14.fbx | alternating squats (2 subjects - subject A) | 1–177 / 30 fps | 5.866667 s |
| 144_05.fbx | Front_Kicking | 1–697 / 30 fps | 23.200000 s |
| 144_07.fbx | Left_Blocks | 1–612 / 30 fps | 20.366667 s |
| 144_26.fbx | Right_Blocks | 1–837 / 30 fps | 27.866667 s |
| 76_11.fbx | quick large steps backwards | 1–129 / 30 fps | 4.266667 s |
| 76_01.fbx | walk backwards then attack with a punch | 1–233 / 30 fps | 7.733333 s |

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

The direct download paths for the fourteen listed files are
`https://huggingface.co/datasets/gbionics/cmu-fbx/resolve/main/animations/<file>.fbx`
with `<file>` equal to a listed file identifier.

Source-frame-2 shoulder-line orientation is measured separately from the actor
root. 16_17, 16_33 and 69_16 initially face roughly −Y; 144_05 is diagonal
toward −Y, while 144_07 and 144_26 are closer to −Y. 22_14, 76_11 and 76_01
initially face roughly −X. Preserve the source turns, retreat and body rotation
when aligning a clip to a requested actor direction. 69_16 contains about 331°
of cumulative turning, not one 90° turn. 22_14 contains two very deep squat cycles, with low points near 1.8 and
4.13 seconds, then returns to its starting hip height. The three 144 clips are long performance sources. Their catalog provides
optional short candidate windows reviewed at three representative poses:
144_05 [4.1, 5.6] seconds, 144_07 [0.6, 2.0], and 144_26 [2.033333, 3.433333].
These do not replace the full calibrated default window, and do not certify
continuous naturalness, stable end support, complete recovery or contact.
The added sources were measured every third motion source frame (six ordinary
sources) or at eight selected times (the three long 144 sources), not subjected
to full-frame ground-contact acceptance. Deep squats show skin penetration
around 5.9 cm; ordinary walk/stop transfer also has ground clearance. Foot
support and hand shape remain later work, separate from body-source sampling.

Read LICENSE.txt before redistribution. These clips are not CC0: use in
commercial products is permitted, but selling the raw animations, even after
conversion, is prohibited; pass the same conditions to recipients.
