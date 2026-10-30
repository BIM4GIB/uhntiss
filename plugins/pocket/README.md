# Pocket plug-ins — the 303, 101 and Kick as VST3 / AU / CLAP

The three Max for Live devices in `m4l/` ([303](../../m4l/pocket303/README.md), [101](../../m4l/pocket101/README.md),
[Kick](../../m4l/pocketkick/README.md)) as plug-ins you can hand to friends who do not run Live Suite. Each
plug-in is the device's C voice mirror (`m4l/<device>/proto/voice.h`, byte-for-byte the same DSP the gen~
codebox runs) wrapped by [DPF](https://github.com/DISTRHO/DPF) (ISC licence, vendored as a git submodule, no
Steinberg SDK needed). No custom GUI yet: hosts show their generic parameter panel, which in Live is the
familiar row of sliders. Presets appear as the host's programs.

```
git submodule update --init plugins/pocket/dpf
make -C plugins/pocket              # native build  -> plugins/pocket/bin/{Pocket303,Pocket101,PocketKick}.{vst3,clap,component}
make -C plugins/pocket universal    # macOS arm64 + x86_64, 10.15+
make -C plugins/pocket params       # after editing an m4l builder: regenerate <plugin>/PocketParams.h, commit it
```

Install on macOS by copying the bundles into `~/Library/Audio/Plug-Ins/VST3`, `.../Components` (AU) and
`.../CLAP`, then rescan in the host (Live: Preferences ▸ Plug-Ins ▸ Rescan). `auval -v aumu Pk03 Uhnt`
(`Pk01`, `PkKk`) validates the AUs.

## How it fits together

| file | role |
|---|---|
| `gen_params.py` | reads each device's `build_device.py` (the same SECTIONS / PARAMS / PRESETS that make the M4L panel) and writes `<plugin>/PocketParams.h`: parameter names, ranges, units, menu items, Sound presets, and the control→gen~ value mapping |
| `common/PocketPlugin.cpp` | the generic DPF plug-in: parameters from the table, programs from the presets, sample-accurate MIDI into the mono note logic, `voice_tick` per sample, stereo out |
| `common/mono_notes.h` | the held-note stack the M4L js and the proto render hosts implement (last-note priority, legato flag, accent at velocity ≥ 100, trig counter) |
| `<plugin>/Plugin.cpp` | includes the device's `voice.h`, its generated table, and the generic plug-in with its label and IDs |
| `<plugin>/DistrhoPluginInfo.h` | DPF's compile-time description: synth, 0 in / 2 out, MIDI in, programs, IDs (`Uhnt` + `Pk03` / `Pk01` / `PkKk`) |
| `Makefile` | builds all three into `bin/`; `.github/workflows/pocket-plugins.yml` does macOS universal, Windows (MinGW) and Linux and attaches zips to a release on a `pocket-plugins-v*` tag |

Parameter ranges are the panel's (0–100 %, Tune in semitones, menus as enums), converted to the gen~ 0..1
values exactly as the M4L patch does, so a plug-in preset and a device preset are the same numbers.

## Status (2026-10-30)

- All nine bundles build as universal (arm64 + x86_64, macOS 10.15+) in ~20 s; the three AUs pass `auval`
  (9 / 25 / 16 parameters, programs present, render tests pass; one "Recommended Property is not supported"
  warning from DPF). Installed into René's `~/Library/Audio/Plug-Ins/{Components,VST3,CLAP}`; a shareable zip
  is `plugins/pocket/dist/pocket-plugins-macos-universal.zip` (ignored by git, 0.5 MB).
- Not yet verified: MIDI → sound inside a host (needs a plug-in rescan in Live and an ear), Windows and
  Linux builds (the workflow does them on push, nothing has run yet), code signing / notarisation (friends
  on macOS will have to allow the unsigned bundles once: right-click ▸ Open, or `xattr -dr com.apple.quarantine`).
- Versions follow the devices: Pocket 303 0.6, Pocket 101 0.1, Pocket Kick 0.3.
